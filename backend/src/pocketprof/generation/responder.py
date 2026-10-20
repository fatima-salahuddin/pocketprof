"""ChatResponder (M2 + M7; FR-4 to FR-7, FR-14, FR-16; SDS 8.1, Figure 12).

    scope (QueryScoper) -> retrieve (course collection only) -> abstain?  -> refusal, no model call
                                                             -> generate (stream) -> citation check
                                                                -> answered | refused | fallback

Fail closed (SDS 2.2): if generation fails, or the model answers without citing a source, the student
sees a notice and the retrieved passages, never an unsupported answer.

The responder yields events that the API streams to the client as Server-Sent Events:
    meta    {scope, sources}          once, before generation
    delta   {text}                    answer text as it arrives
    done    {message_id, outcome, answer, citations, notice, ...}
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..arms import RunConfig, load_arm
from ..core.security import now_utc, student_ref
from ..db.models import Conversation, Course, Message, QueryLog, User
from ..ingestion import calendar
from ..retrieval import scoper
from ..retrieval.retriever import Hit, RetrievalResult, retrieve
from . import citations, prompts
from .llm import ChatTurn, GenerationRefused, get_client
from .openai_compat import OpenAICompatClient

log = logging.getLogger("pocketprof.generation.responder")

NOT_COVERED = ("I couldn't find this in the class material on Google Classroom. It may not have been posted "
               "yet, so please check with your teacher.")
SMALL_TALK = re.compile(r"^\s*(hi+|hello+|hey+|salam|assalam[- ]?o?[- ]?alaikum|good (morning|afternoon|evening)|"
                        r"thanks?( you)?|thank u|ok(ay)?|bye|how are you)[\s!.?]*$", re.I)


@dataclass
class Event:
    type: str
    data: dict


def source_list(hits: list[Hit], cited: list[int] | None = None) -> list[dict]:
    cited = cited or []
    out = []
    for i, h in enumerate(hits, start=1):
        p = h.payload
        out.append({"n": i, "cited": i in cited, "kind": p.get("kind"), "post_title": p.get("post_title"),
                    "topic": p.get("topic"), "file": p.get("file"), "location": p.get("location"),
                    "week": p.get("week"), "date": p.get("date"), "url": p.get("url") or p.get("post_url"),
                    "post_url": p.get("post_url"), "snippet": p.get("text", "")[:600],
                    "score": round(h.dense_score, 3), "ocr": bool(p.get("ocr_flag"))})
    return out


def _retrieval_query(question: str, history: list[ChatTurn]) -> str:
    """Short follow-ups ("explain that again", "and the second one?") retrieve better with the previous
    question attached."""
    if len(question.split()) >= 6:
        return question
    prev = [t.content for t in history if t.role == "user"][-1:]
    return " ".join(prev + [question])


class ChatResponder:
    def __init__(self, db: Session, course: Course, user: User, *, arm: RunConfig | None = None):
        self.db, self.course, self.user = db, course, user
        self.arm = arm or load_arm(course.active_arm)

    # ----- threads (FR-16) -----

    def _thread(self, thread_id: int | None, question: str) -> Conversation:
        if thread_id is not None:
            conv = self.db.get(Conversation, thread_id)
            if conv is None or conv.user_id != self.user.id or conv.course_id != self.course.id:
                from ..core.errors import not_found
                raise not_found("Thread")
            return conv
        title = question.strip().replace("\n", " ")
        conv = Conversation(user_id=self.user.id, course_id=self.course.id,
                            title=title[:80] + ("..." if len(title) > 80 else ""))
        self.db.add(conv)
        self.db.flush()
        return conv

    def _history(self, conv: Conversation) -> list[ChatTurn]:
        rows = self.db.scalars(select(Message).where(Message.conversation_id == conv.id)
                               .order_by(Message.id.desc()).limit(8)).all()
        turns = [ChatTurn(m.role, m.content) for m in reversed(rows)
                 if m.outcome in (None, "answered", "smalltalk")]
        return turns[-4:]

    # ----- the main scenario -----

    def respond(self, question: str, *, thread_id: int | None = None, mode: str | None = None,
                stop: threading.Event | None = None) -> Iterator[Event]:
        stop = stop or threading.Event()
        t0 = time.perf_counter()
        mode = mode or self.arm.response.default_mode
        conv = self._thread(thread_id, question)
        history = self._history(conv)
        self.db.add(Message(conversation_id=conv.id, role="user", content=question, mode=mode))
        self.db.flush()

        if SMALL_TALK.match(question):
            text = (f"Hi! I'm PocketProf for {self.course.code}. Ask me anything about this class's material: "
                    "a concept from the slides, an assignment's requirements, or what was covered in a given week.")
            yield Event("meta", {"thread_id": conv.id, "scope": None, "sources": []})
            yield from self._finish(conv, question, text, "smalltalk", [], [], None, mode, t0, top=None)
            return

        cal = calendar.load(self.db, self.course)
        scope = scoper.resolve(question, cal)
        result = retrieve(self.course.id, _retrieval_query(question, history), self.arm, scope)
        scope = result.scope
        yield Event("meta", {"thread_id": conv.id, "scope": scope.as_dict() if scope.active else None,
                             "scope_notes": scope.notes, "sources": source_list(result.hits)})

        if result.abstain or not result.hits:
            text = self._refusal_text(result)
            yield from self._finish(conv, question, text, "refused", result.hits, [], scope, mode, t0,
                                    top=result.top_score)
            return

        client = get_client(self.arm.generation)
        if client is None:
            yield from self._finish(conv, question, "", "fallback", result.hits, [], scope, mode, t0,
                                    top=result.top_score, notice="No language model is configured, so here are "
                                    "the passages from the class material that best match your question.")
            return

        ctx = prompts.CourseContext(self.course.code, self.course.title, self.course.teacher.name)
        brief = isinstance(client, OpenAICompatClient) and client.local
        system, messages = prompts.build(ctx, mode, history, question, result.hits,
                                         scope.label if scope.active else None, brief=brief)
        text, buffered, released = "", "", False
        try:
            for piece in client.stream(system, messages, max_tokens=self.arm.generation.max_tokens, stop=stop):
                text += piece
                if released:
                    yield Event("delta", {"text": piece})
                    continue
                # Hold back the opening while it could still be the refusal sentinel, so a refusal
                # never flashes on screen as an answer.
                buffered += piece
                head = buffered.lstrip().lstrip("*_`\"'").upper()
                sentinel = prompts.REFUSAL_SENTINEL
                if head.startswith(sentinel) or sentinel.startswith(head):
                    continue
                released = True
                yield Event("delta", {"text": buffered})
            if stop.is_set():
                return
        except GenerationRefused:
            yield from self._finish(conv, question, "", "fallback", result.hits, [], scope, mode, t0,
                                    top=result.top_score, notice="The language model declined this request, so "
                                    "here are the closest passages from the class material instead.")
            return
        except Exception as e:
            if stop.is_set():
                return
            log.warning("generation failed", extra={"course_id": self.course.id, "error": type(e).__name__})
            reason = "it is busy right now" if "RateLimit" in type(e).__name__ else "it could not be reached"
            yield from self._finish(conv, question, "", "fallback", result.hits, [], scope, mode, t0,
                                    top=result.top_score, notice=f"The language model could not answer ({reason}), "
                                    "so here are the closest passages from the class material instead.")
            return

        text = citations.normalise_tags(text).strip()
        if not text:
            yield from self._finish(conv, question, "", "fallback", result.hits, [], scope, mode, t0,
                                    top=result.top_score, notice="The language model returned an empty answer, "
                                    "so here are the closest passages from the class material instead.")
            return
        if citations.is_refusal(text):
            # A refusal cites nothing: drop any tags the model attached while saying "not covered".
            clean = NOT_COVERED if prompts.REFUSAL_SENTINEL in text else re.sub(r"\s*\[S\d+\]", "", text).strip()
            yield from self._finish(conv, question, clean, "refused", result.hits, [], scope, mode, t0,
                                    top=result.top_score)
            return
        text = citations.strip_invalid_tags(text, len(result.hits))
        cited = citations.cited_numbers(text, len(result.hits))
        if not cited:
            # FR-5 in code: an answer that cites nothing is treated as unsupported.
            yield from self._finish(conv, question, "", "fallback", result.hits, [], scope, mode, t0,
                                    top=result.top_score, notice="The answer could not be tied to a source in the "
                                    "class material, so it is not shown. Here are the closest passages instead.",
                                    discarded=text)
            return
        yield from self._finish(conv, question, text, "answered", result.hits, cited, scope, mode, t0,
                                top=result.top_score, model=client.name)

    def _refusal_text(self, result: RetrievalResult) -> str:
        if result.abstain == "out_of_scope" and result.scope.active:
            return (f"Nothing in the class material was posted for {result.scope.label}. If you meant a different "
                    "week or date, try asking with it, or check with your teacher.")
        if result.abstain == "no_material":
            return "This class has no material indexed yet. Your teacher may still be connecting it."
        return NOT_COVERED

    def _finish(self, conv: Conversation, question: str, text: str, outcome: str, hits: list[Hit],
                cited: list[int], scope, mode: str, t0: float, *, top: float | None, notice: str | None = None,
                model: str | None = None, discarded: str | None = None) -> Iterator[Event]:
        latency = int((time.perf_counter() - t0) * 1000)
        records = citations.citation_records(hits, cited)
        msg = Message(conversation_id=conv.id, role="assistant", content=text or (notice or ""), mode=mode,
                      scope=scope.as_dict() if scope is not None and scope.active else None,
                      citations=records, outcome=outcome, latency_ms=latency, model=model)
        self.db.add(msg)
        conv.updated_at = now_utc()
        if self.user.role == "student":  # teachers previewing their own course do not skew the signal
            # Week and topic come from the material that answered; a refused question has none (the nearest
            # passage is, by definition, not about it).
            grounded = outcome == "answered" and hits
            first_week = hits[0].payload.get("week") if grounded else None
            topic = hits[0].payload.get("topic") if grounded else None
            self.db.add(QueryLog(course_id=self.course.id, student_ref=student_ref(self.user.id, self.course.id),
                                 question=question, week=first_week, topic=topic, outcome=outcome,
                                 top_score=top, latency_ms=latency))
        self.db.flush()
        if discarded:
            log.info("uncited answer discarded", extra={"course_id": self.course.id, "message_id": msg.id})
        yield Event("done", {"message_id": msg.id, "thread_id": conv.id, "outcome": outcome, "answer": text,
                             "notice": notice, "citations": records, "sources": source_list(hits, cited),
                             "show_passages": outcome == "fallback", "latency_ms": latency})
