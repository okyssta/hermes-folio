"""Document extraction.

Every format is reduced to a list of :class:`Segment` objects. A segment is the
unit a citation points at — a PDF page, a slide, a sheet range, or a run of
lines — so a hit can always be named precisely ("p. 14", "slide 3",
"Sheet1!rows 12-48") and its text mapped back to an exact offset for
highlighting. Nothing in this module raises on malformed input: a corrupt file
yields whatever could be read plus a ``note`` explaining what was lost.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

MAX_NOTE = 240


@dataclass
class Segment:
    locator: str
    text: str
    section: str = ""
    page: int | None = None


@dataclass
class Extracted:
    kind: str
    segments: list[Segment] = field(default_factory=list)
    title: str = ""
    author: str = ""
    year: str = ""
    note: str = ""
    pages_total: int = 0
    units_label: str = "page"

    @property
    def chars(self) -> int:
        return sum(len(s.text) for s in self.segments)


def sha256_file(path: Path, limit_mb: float = 512.0) -> str:
    h = hashlib.sha256()
    read = 0
    cap = int(limit_mb * 1024 * 1024)
    with path.open("rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            read += len(block)
            if read > cap:
                h.update(f"size:{read}".encode())
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------- helpers

_WS = re.compile(r"[ \t\u00a0]+")
_PARA_SPLIT = re.compile(r"\n\s*\n+")


def normalize_space(text: str) -> str:
    return _WS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", " ")
    # pdfium marks a hyphenated line break with U+FFFE: the word continues.
    text = text.replace("\ufffe", "")
    # Invisible characters that break tokenisation but carry no meaning.
    text = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff\ufffd]", "", text)
    # Join words hyphenated across a line break: "reten-\ntion" -> "retention".
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # De-hyphenate a line-wrapped mid-word only when the next line is lowercase.
    text = re.sub(r"\n(?=[a-z])", " ", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()



def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_NOTE else text[: MAX_NOTE - 1] + "…"


def _line_ranges(text: str, target_lines: int = 60) -> list[tuple[int, int, str]]:
    """Split plain text into (first_line, last_line, chunk_text) runs."""
    lines = text.split("\n")
    out: list[tuple[int, int, str]] = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + target_lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            out.append((start + 1, end, body))
        start = end
    return out


# ------------------------------------------------------------------------ PDF

def _pdf_pages_pdfium(path: Path, max_chars: int) -> tuple[list[Segment], int, str]:
    """Page text via PDFium: fastest, and the reader the desktop page uses."""
    import pypdfium2 as pdfium

    segments: list[Segment] = []
    doc = pdfium.PdfDocument(str(path))
    try:
        total = len(doc)
        used = 0
        blank = 0
        for index in range(total):
            if used >= max_chars:
                return segments, total, f"stopped at the {max_chars:,}-character cap"
            try:
                textpage = doc[index].get_textpage()
                raw = textpage.get_text_range(0, textpage.count_chars())
            except Exception:
                raw = ""
            text = _clean(raw or "")
            if not text:
                blank += 1
                continue
            if used + len(text) > max_chars:
                text = text[: max(0, max_chars - used)]
            segments.append(Segment(locator=f"p. {index + 1}", text=text, page=index + 1))
            used += len(text)
        note = ""
        if blank and blank == total:
            note = "no text layer — this looks like a scan; OCR it first"
        elif blank:
            note = f"{blank} page(s) had no extractable text (scans or figures)"
        return segments, total, note
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _pdf_pages_pypdf(path: Path, max_chars: int) -> tuple[list[Segment], int, str]:
    """Fallback page text via pypdf, for hosts where the PDFium binary will not load."""
    from pypdf import PdfReader

    reader = PdfReader(str(path), strict=False)
    segments: list[Segment] = []
    used = 0
    blank = 0
    pages = len(reader.pages)
    for index in range(pages):
        if used >= max_chars:
            return segments, pages, f"stopped at the {max_chars:,}-character cap"
        try:
            raw = reader.pages[index].extract_text() or ""
        except Exception:
            raw = ""
        text = _clean(raw)
        if not text:
            blank += 1
            continue
        if used + len(text) > max_chars:
            text = text[: max(0, max_chars - used)]
        segments.append(Segment(locator=f"p. {index + 1}", text=text, page=index + 1))
        used += len(text)
    note = ""
    if blank and blank == pages:
        note = "no text layer — this looks like a scan; OCR it first"
    elif blank:
        note = f"{blank} page(s) had no extractable text (scans or figures)"
    return segments, pages, note


def extract_pdf(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="pdf", units_label="page")
    notes: list[str] = []

    for reader, label in ((_pdf_pages_pdfium, "PDFium"), (_pdf_pages_pypdf, "pypdf")):
        try:
            segments, pages, note = reader(path, max_chars)
        except Exception as exc:
            notes.append(f"{label} unavailable ({_short(exc)})")
            continue
        if segments:
            out.segments = segments
            out.pages_total = pages
            out.note = note or ("; ".join(notes) if notes else "")
            _pdf_meta(path, out)
            return out
        notes.append(f"{label} read no text")
        out.pages_total = out.pages_total or pages

    out.note = "; ".join(dict.fromkeys(notes))[:MAX_NOTE]
    _pdf_meta(path, out)
    return out



def _pdf_meta(path: Path, out: Extracted) -> None:
    try:
        import logging

        from pypdf import PdfReader

        # pypdf's repair warnings on producer-mangled PDFs are noise the user
        # cannot act on; keep the library log clean while metadata is read.
        pdf_logger = logging.getLogger("pypdf")
        previous = pdf_logger.level
        pdf_logger.setLevel(logging.ERROR)
        try:
            reader = PdfReader(str(path), strict=False)
            meta = reader.metadata or {}
            out.title = normalize_space(str(meta.get("/Title") or ""))[:300]
            author = meta.get("/Author") or ""
            if isinstance(author, list):
                author = ", ".join(str(a) for a in author)
            out.author = normalize_space(str(author))[:300]
            raw = str(meta.get("/CreationDate") or "")
            found = re.search(r"(19|20)\d{2}", raw)
            out.year = found.group(0) if found else ""
        finally:
            pdf_logger.setLevel(previous)
    except Exception:
        pass

    if not out.title:
        out.title = path.stem.replace("_", " ").replace("-", " ").strip()[:300]


# ----------------------------------------------------------------------- DOCX

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_paragraphs(path: Path) -> list[tuple[str, str]]:
    """(style, text) for every paragraph, body order, tables flattened."""
    rows: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    body = root.find(f"{_W}body")
    if body is None:
        return rows

    def walk(node, inherited: str = "") -> None:
        for child in node:
            tag = child.tag
            if tag == f"{_W}p":
                style = inherited
                ppr = child.find(f"{_W}pPr")
                if ppr is not None:
                    st = ppr.find(f"{_W}pStyle")
                    if st is not None:
                        style = st.get(f"{_W}val", "") or style
                text = "".join(t.text or "" for t in child.iter(f"{_W}t"))
                text = text.strip()
                if text:
                    rows.append((style, text))
            elif tag == f"{_W}tbl":
                for tr in child.iter(f"{_W}tr"):
                    cells = []
                    for tc in tr.iter(f"{_W}tc"):
                        cell = " ".join(
                            (t.text or "") for t in tc.iter(f"{_W}t")
                        ).strip()
                        if cell:
                            cells.append(cell)
                    if cells:
                        rows.append(("Table", " | ".join(cells)))

    walk(body)
    return rows


def extract_docx(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="docx", units_label="paragraph")
    try:
        paragraphs = _docx_paragraphs(path)
    except Exception as exc:
        out.note = f"unreadable DOCX ({_short(exc)})"
        return out

    out.title = path.stem.replace("_", " ").strip()
    section = ""
    buffer: list[str] = []
    start = 0
    total = 0

    def flush(end: int) -> None:
        nonlocal buffer, total
        body = "\n\n".join(buffer).strip()
        if body:
            out.segments.append(
                Segment(locator=f"¶{start}-{end}", text=body, section=section)
            )
            total += len(body)
        buffer = []

    index = 0
    for style, text in paragraphs:
        index += 1
        is_heading = style.lower().startswith("heading") or style.lower() == "title"
        if is_heading:
            flush(index - 1)
            section = text[:120]
            start = index
            if total >= max_chars:
                break
            continue
        buffer.append(text)
        if sum(len(b) for b in buffer) > 800:
            flush(index)
            start = index + 1
        if total >= max_chars:
            break
    flush(index)
    out.pages_total = index
    return out


# ----------------------------------------------------------------------- PPTX

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def extract_pptx(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="pptx", units_label="slide")
    out.title = path.stem.replace("_", " ").strip()
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
            import xml.etree.ElementTree as ET

            total = 0
            for number, name in enumerate(names, start=1):
                root = ET.fromstring(archive.read(name))
                parts = []
                for para in root.iter(f"{_A}p"):
                    text = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                    if text:
                        parts.append(text)
                body = "\n".join(parts).strip()
                if not body:
                    continue
                if total + len(body) > max_chars:
                    body = body[: max(0, max_chars - total)]
                out.segments.append(Segment(locator=f"slide {number}", text=body, page=number))
                total += len(body)
            out.pages_total = len(names)
    except Exception as exc:
        out.note = f"unreadable PPTX ({_short(exc)})"
    return out


# ----------------------------------------------------------------------- XLSX

def extract_xlsx(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="xlsx", units_label="sheet")
    out.title = path.stem.replace("_", " ").strip()
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # pragma: no cover - core ships it
        out.note = f"spreadsheet support unavailable ({_short(exc)})"
        return out

    total = 0
    try:
        book = load_workbook(filename=str(path), read_only=True, data_only=True)
    except Exception as exc:
        out.note = f"unreadable workbook ({_short(exc)})"
        return out

    try:
        for sheet in book.worksheets:
            rows: list[str] = []
            first = last = 0
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if not cells:
                    continue
                if not first:
                    first = row_index
                last = row_index
                rows.append(f"{row_index}\t" + "\t".join(cells))
                if len(rows) >= 60 or sum(len(r) for r in rows) > 1400:
                    body = "\n".join(rows)
                    out.segments.append(
                        Segment(locator=f"{sheet.title}!rows {first}-{last}", text=body,
                                section=str(sheet.title))
                    )
                    total += len(body)
                    rows, first = [], 0
                if total >= max_chars:
                    break
            if rows:
                body = "\n".join(rows)
                out.segments.append(
                    Segment(locator=f"{sheet.title}!rows {first}-{last}", text=body,
                            section=str(sheet.title))
                )
                total += len(body)
            if total >= max_chars:
                out.note = f"stopped at the {max_chars:,}-character cap"
                break
        out.pages_total = len(book.worksheets)
    finally:
        try:
            book.close()
        except Exception:
            pass
    return out


# ------------------------------------------------------------------------ CSV

def extract_csv(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="csv", units_label="row")
    out.title = path.stem.replace("_", " ").strip()
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            rows: list[str] = []
            first = last = 0
            total = 0
            header_line = " | ".join(header or [])
            for index, row in enumerate(reader, start=2):
                cells = [c.strip() for c in row if c and c.strip()]
                if not cells:
                    continue
                if not first:
                    first = index
                last = index
                rows.append(f"{index}\t" + "\t".join(cells))
                if len(rows) >= 60:
                    body = ("columns: " + header_line + "\n" if header_line else "") + "\n".join(rows)
                    out.segments.append(Segment(locator=f"rows {first}-{last}", text=body))
                    total += len(body)
                    rows, first = [], 0
                if total >= max_chars:
                    break
            if rows:
                body = "\n".join(rows)
                out.segments.append(Segment(locator=f"rows {first}-{last}", text=body))
            out.pages_total = last
    except Exception as exc:
        out.note = f"unreadable CSV ({_short(exc)})"
    return out


# --------------------------------------------------------------- text / html

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def extract_text(path: Path, max_chars: int) -> Extracted:
    suffix = path.suffix.lower()
    kind = {".md": "markdown", ".markdown": "markdown", ".rst": "rst"}.get(suffix, "text")
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
                  ".h", ".cpp", ".rb", ".sh", ".sql", ".yaml", ".yml", ".json", ".toml"}:
        kind = "code"
    out = Extracted(kind=kind, units_label="lines")
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        out.note = f"unreadable text file ({_short(exc)})"
        return out

    text = _clean(raw)
    if len(text) > max_chars:
        text = text[:max_chars]
        out.note = f"stopped at the {max_chars:,}-character cap"

    lines = text.split("\n")
    out.pages_total = len(lines)

    if kind == "markdown":
        marks: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            found = _HEADING.match(line)
            if found:
                marks.append((index, len(found.group(1)), found.group(2).strip()[:120]))

        spans: list[tuple[int, int, list[str]]] = []
        stack: list[tuple[int, str]] = []
        if not marks or marks[0][0] > 0:
            spans.append((0, marks[0][0] if marks else len(lines), []))
        for position, (start, level, title) in enumerate(marks):
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
            spans.append((start, end, [name for _level, name in stack]))

        for start, end, path in spans:
            for chunk_start in range(start, end, 80):
                chunk_end = min(end, chunk_start + 80)
                piece = "\n".join(lines[chunk_start:chunk_end]).strip()
                if piece:
                    out.segments.append(
                        Segment(locator=f"lines {chunk_start + 1}-{chunk_end}", text=piece,
                                section=" › ".join(path[-3:]))
                    )
        return out


    for start, end, body in _line_ranges(text, 80):
        out.segments.append(Segment(locator=f"lines {start}-{end}", text=body))
    return out


class _TextHTMLParser:
    """Minimal HTML to text: drops script/style, keeps block boundaries."""

    def __init__(self) -> None:
        from html.parser import HTMLParser

        self._Parser = HTMLParser
        self.parts: list[str] = []
        self._skip = 0
        self._parser = HTMLParser(convert_charrefs=True)
        self._parser.handle_starttag = self._start  # type: ignore[assignment]
        self._parser.handle_endtag = self._end  # type: ignore[assignment]
        self._parser.handle_data = self._data  # type: ignore[assignment]

    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def _start(self, tag, attrs):  # noqa: ANN001
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def _end(self, tag):  # noqa: ANN001
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def _data(self, data):  # noqa: ANN001
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        self._parser.feed("".join(self.parts))
        return _clean("".join(self.parts))


def extract_html(path: Path, max_chars: int) -> Extracted:
    out = Extracted(kind="html", units_label="lines")
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        out.note = f"unreadable HTML ({_short(exc)})"
        return out
    try:
        text = _TextHTMLParser().text()
    except Exception:
        text = _clean(re.sub(r"<[^>]+>", " ", raw))
    if len(text) > max_chars:
        text = text[:max_chars]
        out.note = f"stopped at the {max_chars:,}-character cap"
    out.pages_total = len(text.split("\n"))
    for start, end, body in _line_ranges(text, 60):
        out.segments.append(Segment(locator=f"lines {start}-{end}", text=body))
    return out


# ------------------------------------------------------------------- dispatch

def reader_status() -> str:
    """Which optional readers this process can actually import.

    Folio ships no Python dependencies; it borrows PDF and spreadsheet readers
    from the host. When one is missing, extraction degrades to text and this line
    says so instead of a bare "0 documents indexed".
    """
    available = []
    for module, label in (("pypdfium2", "pdfium"), ("pypdf", "pypdf"), ("openpyxl", "openpyxl")):
        try:
            __import__(module)
            available.append(label)
        except Exception:
            continue
    return " + ".join(available) or "text only (no PDF or spreadsheet reader)"


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".pptx": extract_pptx,
    ".xlsx": extract_xlsx,
    ".csv": extract_csv,
    ".html": extract_html,
    ".htm": extract_html,
}


def _title_from_text(out: Extracted, path: Path) -> None:
    """Trust the document over its metadata when they disagree.

    Exporters put junk in ``/Title`` ("Microsoft Word - Document1", a LaTeX
    class name, a template name). A real title nearly always appears in the
    document's own opening lines, so when the metadata title cannot be found
    there, the first line of page one is a better answer than the boilerplate.
    """
    if not out.segments:
        return
    head = normalize_space(out.segments[0].text[:4000]).lower()
    current = normalize_space(out.title).lower()
    probe = current[:30]
    if probe and probe in head:
        return
    first_line = ""
    best_score = -99
    labels = ("abstract", "keyword", "keywords", "introduction", "contents", "figure",
              "table", "chapter", "section", "appendix", "references")
    candidates = [ln for ln in out.segments[0].text.split("\n") if normalize_space(ln)][:8]
    for position, line in enumerate(candidates):
        candidate = normalize_space(line)
        if not 6 <= len(candidate) <= 220:
            continue
        words = candidate.split()
        score = 0
        score += 2 if 3 <= len(words) <= 20 else -2
        score += 2 if not candidate.isupper() else -2
        score += 1 if any(ch.islower() for ch in candidate) else 0
        score -= 3 if candidate.lower().startswith(labels) else 0
        score -= 2 if candidate.endswith((":", ".")) else 0
        score -= 2 if re.match(r"^(\d+[.)]|[ivx]+\.)\s", candidate, re.I) else 0
        # Author lines carry affiliation superscripts ("Zhao1"), footnote marks,
        # initials, or start with punctuation; a title almost never does.
        score -= 3 if re.search(r"[A-Za-z]\d", candidate) else 0
        score -= 3 if re.search(r"[*†‡]", candidate) else 0
        score -= 3 if candidate.lstrip().startswith((",", ";", ":", "and ", "&", "-", "—")) else 0
        score -= 3 if re.search(r"\b[A-Z]\.(\s|$)", candidate) else 0
        score -= 6 if "@" in candidate else 0
        score -= position * 0.1

        if score > best_score:
            best_score, first_line = score, candidate
    if first_line and best_score >= 2:
        out.title = first_line[:300]
        return


    slug = path.stem.replace("_", " ").replace("-", " ").strip()
    if slug and not re.fullmatch(r"[0-9.]+v?[0-9]*", slug):
        out.title = slug[:300]


def extract(path: Path, max_chars: int = 400_000) -> Extracted:
    """Extract *path*, never raising. Unknown types are read as text."""
    suffix = path.suffix.lower()
    extractor = EXTRACTORS.get(suffix)
    try:
        if extractor is not None:
            out = extractor(path, max_chars)
        else:
            out = extract_text(path, max_chars)
    except Exception as exc:  # last-resort guard: one bad file must not stop a scan
        out = Extracted(kind=suffix.lstrip(".") or "file", note=f"extraction failed ({_short(exc)})")
    if out.kind == "pdf":
        _title_from_text(out, path)
    if not out.title:
        out.title = path.stem.replace("_", " ").replace("-", " ").strip()[:300]

    if not out.author:
        out.author = ""
    if not out.year:
        try:
            out.year = str(datetime.fromtimestamp(path.stat().st_mtime).year)
        except Exception:
            out.year = ""
    return out


def metadata_json(out: Extracted) -> str:
    return json.dumps(
        {
            "kind": out.kind,
            "title": out.title,
            "author": out.author,
            "year": out.year,
            "pages_total": out.pages_total,
            "units_label": out.units_label,
            "note": out.note,
        },
        ensure_ascii=False,
    )
