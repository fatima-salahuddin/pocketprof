"""Chunker (SDS 4.2). Deterministic: the same segments and settings always give the same chunks, which
together with deterministic chunk IDs makes re-ingestion idempotent (NFR-22).

Token counts are estimated with a word/punctuation split, which tracks WordPiece counts for English
lecture text closely enough for packing and is stable across tokenizer versions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..arms import Chunking
from .parsers import Segment

_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def n_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


@dataclass
class Chunk:
    seq: int
    text: str
    location: str
    page: int | None = None
    slide: int | None = None
    heading: str | None = None
    ocr: bool = False


def _span_label(first: Segment, last: Segment) -> str:
    if first is last:
        return first.location
    if first.slide and last.slide:
        return f"slides {first.slide}-{last.slide}"
    if first.page and last.page:
        return f"pages {first.page}-{last.page}"
    return first.location


def _split_units(text: str, limit: int) -> list[str]:
    """Paragraphs, with any paragraph longer than `limit` broken into sentences (and very long sentences
    into word windows), so no unit exceeds the chunk size."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if n_tokens(para) <= limit:
            units.append(para)
            continue
        for sent in _SENT.split(para):
            if n_tokens(sent) <= limit:
                units.append(sent)
            else:
                words = sent.split()
                step = max(1, int(limit * 0.75))
                units.extend(" ".join(words[i:i + step]) for i in range(0, len(words), step))
    return units


def _pack(units: list[str], size: int, overlap: float) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    carry_budget = int(size * overlap)
    for u in units:
        t = n_tokens(u)
        if cur and cur_tokens + t > size:
            chunks.append("\n\n".join(cur))
            # carry the tail of the closed chunk across the boundary
            carry, carried = [], 0
            for prev in reversed(cur):
                pt = n_tokens(prev)
                if carried + pt > carry_budget:
                    break
                carry.insert(0, prev)
                carried += pt
            cur, cur_tokens = carry, carried
        cur.append(u)
        cur_tokens += t
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _structure_aware(segments: list[Segment], cfg: Chunking) -> list[Chunk]:
    out: list[Chunk] = []
    is_deck = bool(segments) and all(s.slide is not None for s in segments)

    if is_deck:
        group: list[Segment] = []
        tokens = 0

        def close():
            nonlocal group, tokens
            if group:
                out.append(Chunk(len(out), "\n\n".join(s.text for s in group), _span_label(group[0], group[-1]),
                                 slide=group[0].slide, heading=group[0].heading, ocr=any(s.ocr for s in group)))
            group, tokens = [], 0

        for seg in segments:
            t = n_tokens(seg.text)
            if t > cfg.size:  # one very dense slide: split it like prose
                close()
                for piece in _pack(_split_units(seg.text, cfg.size), cfg.size, cfg.overlap):
                    out.append(Chunk(len(out), piece, seg.location, slide=seg.slide, heading=seg.heading, ocr=seg.ocr))
                continue
            if group and tokens + t > cfg.size:
                close()
            group.append(seg)
            tokens += t
            if tokens >= cfg.min:
                close()
        close()
        return out

    # Prose: pack within a segment; merge consecutive short segments (e.g. sparse PDF pages).
    pending: list[Segment] = []

    def emit(segs: list[Segment]):
        text = "\n\n".join(s.text for s in segs)
        label = _span_label(segs[0], segs[-1])
        for piece in _pack(_split_units(text, cfg.size), cfg.size, cfg.overlap):
            out.append(Chunk(len(out), piece, label, page=segs[0].page, heading=segs[0].heading,
                             ocr=any(s.ocr for s in segs)))

    for seg in segments:
        if seg.heading and pending:  # a heading starts a new chunk
            emit(pending)
            pending = []
        pending.append(seg)
        if sum(n_tokens(s.text) for s in pending) >= cfg.min:
            emit(pending)
            pending = []
    if pending:
        emit(pending)
    return out


def _fixed(segments: list[Segment], cfg: Chunking) -> list[Chunk]:
    words = " ".join(s.text for s in segments).split()
    if not words:
        return []
    # convert the token budget to words using the document's own ratio
    ratio = max(1.0, n_tokens(" ".join(words)) / len(words))
    step_words = max(1, int(cfg.size / ratio))
    stride = max(1, int(step_words * (1 - cfg.overlap)))
    first = segments[0]
    return [Chunk(i, " ".join(words[s:s + step_words]), first.location if len(segments) == 1 else "document",
                  page=first.page, slide=first.slide)
            for i, s in enumerate(range(0, len(words), stride)) if words[s:s + step_words]]


def chunk_segments(segments: list[Segment], cfg: Chunking) -> list[Chunk]:
    segments = [s for s in segments if s.text.strip()]
    if not segments:
        return []
    return _fixed(segments, cfg) if cfg.splitter == "fixed" else _structure_aware(segments, cfg)
