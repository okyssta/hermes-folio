"""Fixtures: real files in the formats Folio reads, built from scratch.

The PDF writer is deliberately tiny — a handful of objects and an xref table —
so the test suite carries no binary fixtures and the bytes are readable in this
file. If a fixture ever stops parsing, the bug is in ten lines, not in a blob.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[list[str]], font_size: int = 11) -> Path:
    """Write a valid one-font PDF with the given lines on each page."""
    objects: dict[int, str] = {}
    page_ids: list[int] = []
    content_ids: list[int] = []
    next_id = 4
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2

    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>"
    objects[3] = ("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                  "/Encoding /WinAnsiEncoding >>")

    for page_id, content_id, lines in zip(page_ids, content_ids, pages):
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )
        body = "BT /F1 %d Tf 72 720 Td 15 TL\n" % font_size
        body += "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
        body += "\nET"
        objects[content_id] = f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n{objects[object_id]}\nendobj\n".encode("latin-1")

    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for object_id in range(1, size):
        out += f"{offsets[object_id]:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(bytes(out))
    return path


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path: Path, paragraphs: list[tuple[str, str]]) -> Path:
    """Write a DOCX with (style, text) paragraphs; style '' means body text."""
    body = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(
            f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{_xml(text)}</w:t></w:r></w:p>'
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>' + "".join(body) + "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
    return path


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def make_pptx(path: Path, slides: list[list[str]]) -> Path:
    names = []
    with zipfile.ZipFile(path, "w") as archive:
        for index, lines in enumerate(slides, start=1):
            name = f"ppt/slides/slide{index}.xml"
            names.append(name)
            paragraphs = "".join(
                f"<a:p><a:r><a:t>{_xml(line)}</a:t></a:r></a:p>" for line in lines
            )
            archive.writestr(
                name,
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<p:cSld><p:spTree><p:sp><p:txBody>{paragraphs}</p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>",
            )
    return path


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """A small library: two PDFs, a DOCX, a PPTX, an XLSX, Markdown and code."""
    root = tmp_path / "Documents"
    root.mkdir()

    make_pdf(
        root / "spec.pdf",
        [
            [
                "Service Level Specification",
                "Version 4, platform engineering",
                "Retries are capped at three attempts per request.",
                "The retry budget is reset every sixty seconds.",
            ],
            [
                "Termination",
                "Either party may terminate this agreement with 30 days notice.",
                "The retry budget is reviewed quarterly by the platform team.",
            ],
        ],
    )
    make_pdf(root / "notes.pdf", [["Meeting notes", "Nobody mentioned the retry budget at all."]])

    make_docx(
        root / "contract.docx",
        [
            ("Heading1", "Payment"),
            ("", "Invoices are due within thirty days of receipt."),
            ("", "The vendor may suspend service after a missed payment."),
            ("Heading1", "Liability"),
            ("", "Liability is limited to the fees paid in the prior twelve months."),
        ],
    )

    make_pptx(root / "deck.pptx", [["Roadmap"], ["Retry work moves to Q3"]])

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Budget"
    sheet.append(["Item", "Owner", "Cost"])
    sheet.append(["Retry budget", "Platform", 1200])
    book.save(root / "budget.xlsx")

    (root / "guide.md").write_text(
        "# Deploying the service\n\nRun the deploy script before the retry budget resets.\n\n"
        "## Rollback\n\nRestore the previous release within ten minutes.\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "def handle(request):\n    \"\"\"Retry handling for inbound requests.\"\"\"\n"
        "    return request\n",
        encoding="utf-8",
    )
    return root
