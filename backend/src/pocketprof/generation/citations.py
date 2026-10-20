"""Citation mapping (SDS 4.4, FR-5): [Sn] tags -> the retrieved chunks they name.

An answer with no valid tag is unsupported and is not shown as an answer; an answer that is the refusal
sentinel (or opens by saying the material does not cover the question) is a refusal. Both checks live in
code, not only in the prompt.
"""
from __future__ import annotations

import re

from ..retrieval.retriever import Hit
from .prompts import REFUSAL_SENTINEL

_TAG_GROUP = re.compile(r"\[\s*(S\s*\d+(?:\s*[,;]\s*S?\s*\d+)*)\s*\]", re.I)
_NUM = re.compile(r"\d+")

# Fallback for models that ignore the sentinel and say it in words. Only the opening is checked, so a
# partial answer ("X is ... [S1]. The material does not cover Y.") still counts as an answer.
_NOT_COVERED = re.compile(
    r"not covered|not (in|part of) the (class|course) material|no information|no mention|nothing about"
    r"|(could ?n[o']t|cannot|can't|unable to) find|(does|do|did) ?n[o']t (state|contain|mention|include|cover|say"
    r"|provide|specify|have)|(is|are) ?n[o']t (mentioned|included|stated|specified|covered)", re.I)


def normalise_tags(text: str) -> str:
    """'[S1, S3]' and '[s 2]' -> '[S1][S3]', '[S2]', so the client renders every tag the same way."""
    def repl(m: re.Match) -> str:
        return "".join(f"[S{n}]" for n in _NUM.findall(m.group(1)))
    return _TAG_GROUP.sub(repl, text)


def cited_numbers(text: str, n_hits: int) -> list[int]:
    seen: list[int] = []
    for m in _TAG_GROUP.finditer(text):
        for n in map(int, _NUM.findall(m.group(1))):
            if 1 <= n <= n_hits and n not in seen:
                seen.append(n)
    return seen


def strip_invalid_tags(text: str, n_hits: int) -> str:
    return re.sub(r"\[S(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_hits else "", text)


def is_refusal(text: str) -> bool:
    t = text.strip().strip("*_`\"'. ")
    if t.upper().startswith(REFUSAL_SENTINEL) or (REFUSAL_SENTINEL in text and len(t) < 80):
        return True
    plain = re.sub(r"[*_`]", "", text).strip()
    opening = re.split(r"(?<=[.!?])\s", plain, maxsplit=1)[0][:240]  # the first sentence only
    return bool(_NOT_COVERED.search(opening))


def citation_records(hits: list[Hit], numbers: list[int]) -> list[dict]:
    out = []
    for n in numbers:
        p = hits[n - 1].payload
        out.append({"n": n, "chunk_id": hits[n - 1].chunk_id, "file": p.get("file"), "location": p.get("location"),
                    "week": p.get("week"), "post_title": p.get("post_title"), "url": p.get("url") or p.get("post_url")})
    return out
