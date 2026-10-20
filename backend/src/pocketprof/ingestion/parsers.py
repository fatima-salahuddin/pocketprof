"""DocumentParser (SDS 4.2, Table 3): bytes + MIME type -> located text segments.

PDFs are read with PyMuPDF directly (SDS Table 3 names PyMuPDF4LLM, a wrapper over it; the wrapper's
current PyPI release ships no code, so it is not used).

A segment is the unit of structure a citation can point at: a PDF page, a slide, a DOCX section.
Normalisation (repeated headers/footers, page numbers, whitespace) is applied here so every parser's
output looks the same to the chunker.
"""
from __future__ import annotations

import io
import logging
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field

log = logging.getLogger("pocketprof.ingestion.parsers")

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
GDOC = "application/vnd.google-apps.document"
GSLIDES = "application/vnd.google-apps.presentation"
GSHEET = "application/vnd.google-apps.spreadsheet"
# Google-native files have no bytes of their own; Drive exports them to these types first.
EXPORT_AS = {GDOC: DOCX, GSLIDES: PPTX, GSHEET: "text/csv"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "text/x-python", "text/x-java", "text/x-c",
              "application/json"}
EXTENSION_MIME = {".pdf": PDF, ".docx": DOCX, ".pptx": PPTX, ".txt": "text/plain", ".md": "text/markdown",
                  ".csv": "text/csv", ".py": "text/x-python", ".java": "text/x-java", ".c": "text/x-c",
                  ".cpp": "text/x-c", ".json": "application/json"}

MIN_PAGE_CHARS = 50   # below this a PDF page is treated as image-only and sent to OCR (Table 3)
PARSER_VERSION = "p3"  # bump when parser output changes, so the parse cache is invalidated


class UnsupportedType(Exception):
    pass


@dataclass
class Segment:
    location: str                 # human label used in citations: "slide 7", "page 3", "section: Heaps"
    text: str
    page: int | None = None
    slide: int | None = None
    heading: str | None = None
    ocr: bool = False


@dataclass
class ParseResult:
    segments: list[Segment] = field(default_factory=list)
    ocr_pages: int = 0
    needs_ocr_pages: int = 0       # image-only pages left unread because no OCR engine is installed

    def to_json(self) -> list[dict]:
        return [s.__dict__ for s in self.segments]

    @classmethod
    def from_json(cls, rows: list[dict], ocr_pages: int = 0) -> ParseResult:
        return cls([Segment(**r) for r in rows], ocr_pages)


def is_supported(mime: str) -> bool:
    return mime in (PDF, DOCX, PPTX, GDOC, GSLIDES, GSHEET) or mime in TEXT_TYPES or mime.startswith("text/")


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


# ---------- normalisation ----------

_PAGE_NO = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)


def clean(text: str) -> str:
    text = text.replace("\x00", " ").replace(" ", " ")
    out, in_code = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            out.append(line.rstrip())
            continue
        out.append(line.rstrip() if in_code else re.sub(r"[ \t]+", " ", line).strip())
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_repeated_lines(pages: list[str]) -> list[str]:
    """Remove running headers and footers: short lines that recur on at least half of the pages,
    and bare page numbers."""
    if len(pages) < 3:
        return ["\n".join(ln for ln in p.splitlines() if not _PAGE_NO.match(ln)) for p in pages]
    counts: Counter[str] = Counter()
    for p in pages:
        counts.update({ln.strip() for ln in p.splitlines() if 0 < len(ln.strip()) <= 80})
    repeated = {ln for ln, n in counts.items() if n >= max(3, len(pages) // 2)}
    return ["\n".join(ln for ln in p.splitlines() if ln.strip() not in repeated and not _PAGE_NO.match(ln))
            for p in pages]


# ---------- per-format parsers ----------

def parse_pdf(data: bytes) -> ParseResult:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        # sort=True reads blocks top-to-bottom, left-to-right, which keeps two-column slides in order.
        texts = strip_repeated_lines([page.get_text("text", sort=True) for page in doc])
        result = ParseResult()
        can_ocr = ocr_available()
        for i, text in enumerate(texts, start=1):
            text = clean(text)
            ocr = False
            # Short text on a page that also holds images is likely a scan with a caption: OCR it. Short text
            # on a page without images is simply a short page and is kept.
            if len(text) < MIN_PAGE_CHARS and doc[i - 1].get_images():
                if not can_ocr:
                    result.needs_ocr_pages += 1
                    if len(text) >= 20:
                        result.segments.append(Segment(f"page {i}", text, page=i))
                    continue
                try:
                    tp = doc[i - 1].get_textpage_ocr(full=True, dpi=200)
                    text, ocr = clean(doc[i - 1].get_text(textpage=tp)), True
                    result.ocr_pages += 1
                except Exception as e:  # OCR failure on one page must not fail the file
                    log.warning("OCR failed", extra={"page": i, "error": type(e).__name__})
                    continue
            if len(text) >= 20:
                result.segments.append(Segment(f"page {i}", text, page=i, ocr=ocr))
        return result
    finally:
        doc.close()


def parse_pptx(data: bytes) -> ParseResult:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    result = ParseResult()
    for i, slide in enumerate(prs.slides, start=1):
        title, parts = None, []
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = clean(slide.shapes.title.text_frame.text) or None
        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append("Speaker notes: " + notes)
        body = clean("\n".join(parts))
        text = clean(f"{title}\n{body}" if title else body)
        if text:  # empty slides are dropped (Table 3)
            result.segments.append(Segment(f"slide {i}", text, slide=i, heading=title))
    return result


def parse_docx(data: bytes) -> ParseResult:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    result, heading, buf = ParseResult(), None, []

    def flush():
        text = clean("\n".join(buf))
        if text:
            result.segments.append(Segment(f"section: {heading}" if heading else "introduction", text,
                                           heading=heading))

    # Walk the body in document order so tables stay inside the section they belong to.
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            style = (para.style.name if para.style is not None else "").lower()
            if (style.startswith("heading") or style == "title") and para.text.strip():
                flush()
                heading, buf = para.text.strip()[:80], [para.text.strip()]
            else:
                buf.append(para.text)
        elif tag == "tbl":
            table = Table(child, doc)
            for row in table.rows:  # tables are flattened row by row (Table 3)
                cells = []
                for c in row.cells:
                    t = c.text.strip()
                    if not cells or cells[-1] != t:  # merged cells repeat their text
                        cells.append(t)
                buf.append(" | ".join(cells))
    flush()
    return result


def parse_text(data: bytes, mime: str) -> ParseResult:
    text = clean(data.decode("utf-8", errors="replace"))
    if not text:
        return ParseResult()
    if mime == "text/markdown":
        sections = re.split(r"(?m)^(?=#{1,3} )", text)
        segs = []
        for s in sections:
            s = s.strip()
            if not s:
                continue
            head = s.splitlines()[0].lstrip("# ").strip() if s.startswith("#") else None
            segs.append(Segment(f"section: {head}" if head else "text", s, heading=head))
        return ParseResult(segs)
    # Plain text is one segment; the chunker splits it.
    return ParseResult([Segment("text", text)])


def parse(data: bytes, mime: str) -> ParseResult:
    if mime == PDF:
        return parse_pdf(data)
    if mime == PPTX:
        return parse_pptx(data)
    if mime == DOCX:
        return parse_docx(data)
    if mime in TEXT_TYPES or mime.startswith("text/"):
        return parse_text(data, mime)
    raise UnsupportedType(f"file type not supported ({mime})")


def sniff_mime(filename: str, data: bytes) -> str:
    """Content-signature check for uploads (SDS Table 9): the bytes must match the extension."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    mime = EXTENSION_MIME.get(ext)
    if mime is None:
        raise UnsupportedType(f"'{ext or filename}' files are not supported; use PDF, PPTX, DOCX or text.")
    if mime == PDF and not data.startswith(b"%PDF"):
        raise UnsupportedType("the file is named .pdf but is not a PDF")
    if mime in (DOCX, PPTX):
        if not data.startswith(b"PK"):
            raise UnsupportedType(f"the file is named {ext} but is not an Office document")
        if b"vbaProject.bin" in data:
            raise UnsupportedType("documents with macros are not accepted")
    if mime.startswith("text/") or mime == "application/json":
        if b"\x00" in data[:4096]:
            raise UnsupportedType("the file looks binary, not text")
    return mime
