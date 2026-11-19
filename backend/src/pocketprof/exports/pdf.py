"""PDF rendering (M3, FR-9) with PyMuPDF's Story, which flows HTML across as many A4 pages as it needs.

Everything that came from course files or a model is untrusted: it is HTML-escaped first, and only then
is the small Markdown subset the app uses (headings, lists, bold, italic, code, [S1] markers) turned into
markup. This is the PDF equivalent of the web app never using innerHTML.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field

import pymupdf

PAGE = pymupdf.paper_rect("a4")
MARGIN = (54, 54, -54, -64)          # left, top, right, bottom (room for the footer)
FOOTER = "PocketProf · AI-generated only from this class's material · page {n} of {total}"

CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.45; color: #1d2a26; }
h1 { font-size: 18pt; margin: 0 0 4pt 0; color: #1f4d40; }
h2 { font-size: 13pt; margin: 14pt 0 4pt 0; color: #1f4d40; }
h3 { font-size: 11pt; margin: 10pt 0 3pt 0; }
p { margin: 0 0 6pt 0; }
ul, ol { margin: 0 0 6pt 0; }
li { margin: 0 0 2pt 0; }
code { font-family: monospace; font-size: 9.5pt; }
pre { font-family: monospace; font-size: 9pt; background-color: #f5f2ea; padding: 4pt; }
sup { font-size: 7.5pt; color: #2e6b59; }
.sub { color: #4b5a55; font-size: 9.5pt; margin: 0 0 2pt 0; }
.label { color: #8a5a12; font-size: 8.5pt; margin: 0 0 10pt 0; }
.who { font-weight: bold; color: #1f4d40; margin: 10pt 0 2pt 0; }
.notice { color: #8a5a12; font-style: italic; }
.src { font-size: 9pt; color: #4b5a55; }
.answer { color: #1f4d40; }
"""


@dataclass
class Document:
    """What every export builds: a title, a subtitle line, body HTML (already escaped) and a numbered
    Sources list that matches the [n] markers in the body."""
    title: str
    subtitle: str
    body_html: str
    sources: list[dict] = field(default_factory=list)
    filename: str = "pocketprof.pdf"


_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[S\d+\]|\*[^*\s][^*]*\*)")


def _inline(escaped: str) -> str:
    def repl(m: re.Match) -> str:
        part = m.group(0)
        if part.startswith("**"):
            return f"<b>{part[2:-2]}</b>"
        if part.startswith("`"):
            return f"<code>{part[1:-1]}</code>"
        if part.startswith("[S"):
            return f"<sup>[{part[2:-1]}]</sup>"
        return f"<i>{part[1:-1]}</i>"
    return _INLINE.sub(repl, escaped)


def markdown_to_html(text: str) -> str:
    """The Markdown subset of components/Markdown.tsx, as escaped HTML."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag: str | None = None
    code: list[str] | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br/>".join(_inline(html.escape(p)) for p in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"<{list_tag}>" + "".join(f"<li>{_inline(html.escape(i))}</li>" for i in items)
                       + f"</{list_tag}>")
            items.clear()
            list_tag = None

    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            if code is not None:
                out.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
                code = None
            else:
                flush_para()
                flush_list()
                code = []
            continue
        if code is not None:
            code.append(raw)
            continue
        if not line.strip():
            flush_para()
            flush_list()
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        if heading:
            flush_para()
            flush_list()
            level = min(len(heading.group(1)) + 1, 3)
            out.append(f"<h{level}>{_inline(html.escape(heading.group(2)))}</h{level}>")
            continue
        bullet = re.match(r"^\s*[-*•]\s+(.*)", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        if bullet or numbered:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                flush_list()
                list_tag = tag
            items.append((bullet or numbered).group(1))  # type: ignore[union-attr]
            continue
        flush_list()
        para.append(line)
    if code is not None:
        out.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
    flush_para()
    flush_list()
    return "".join(out)


def sources_html(sources: list[dict]) -> str:
    if not sources:
        return ""
    rows = []
    for s in sources:
        bits = [s.get("file") or s.get("post_title") or "Class material"]
        if s.get("location"):
            bits.append(s["location"])
        where = ", ".join(html.escape(str(b)) for b in bits)
        week = f" (week {int(s['week'])})" if s.get("week") else ""
        rows.append(f"<li class='src'>[{int(s['n'])}] {where}{week}</li>")
    return "<h2>Sources</h2><ul style='list-style-type: none; padding-left: 0'>" + "".join(rows) + "</ul>"


def render(doc: Document) -> bytes:
    body = (f"<h1>{html.escape(doc.title)}</h1><p class='sub'>{html.escape(doc.subtitle)}</p>"
            "<p class='label'>AI-generated by PocketProf from this class's own material. Check anything important "
            "against the cited source.</p>" + doc.body_html + sources_html(doc.sources))
    story = pymupdf.Story(html=body, user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    where = PAGE + MARGIN
    more = 1
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

    pdf = pymupdf.open("pdf", buf.getvalue())
    total = pdf.page_count
    for i, page in enumerate(pdf, start=1):
        page.insert_text((PAGE.x0 + 54, PAGE.y1 - 32), FOOTER.format(n=i, total=total), fontsize=7.5,
                         color=(0.42, 0.47, 0.45))
    pdf.set_metadata({"title": doc.title, "creator": "PocketProf", "producer": "PocketProf"})
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return data
