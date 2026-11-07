"""Extraction: every format yields locators that a citation can point at."""

from __future__ import annotations

from pathlib import Path

import pytest

from folio.extract import extract
from tests.conftest import make_docx, make_pdf, make_pptx


def test_pdf_pages_become_locators(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "report.pdf", [["First page text"], ["Second page text"]])
    out = extract(path)
    assert out.kind == "pdf"
    assert [segment.locator for segment in out.segments] == ["p. 1", "p. 2"]
    assert "Second page text" in out.segments[1].text
    assert out.pages_total == 2
    assert out.units_label == "page"


def test_pdf_title_falls_back_to_the_page_when_metadata_is_empty(tmp_path: Path) -> None:
    path = make_pdf(
        tmp_path / "spec.pdf",
        [["Quarterly Platform Report", "The retry budget is reviewed quarterly."]],
    )
    out = extract(path)
    assert out.title.startswith("Quarterly Platform Report")


def test_docx_headings_become_sections(tmp_path: Path) -> None:
    path = make_docx(
        tmp_path / "contract.docx",
        [("Heading1", "Payment"), ("", "Invoices are due within thirty days.")],
    )
    out = extract(path)
    assert out.kind == "docx"
    assert out.segments
    assert out.segments[0].section == "Payment"
    assert "thirty days" in out.segments[0].text
    assert out.segments[0].locator.startswith("¶")


def test_pptx_slides_are_numbered(tmp_path: Path) -> None:
    path = make_pptx(tmp_path / "deck.pptx", [["Roadmap"], ["Retry work moves to Q3"]])
    out = extract(path)
    assert out.kind == "pptx"
    assert [segment.locator for segment in out.segments] == ["slide 1", "slide 2"]
    assert "Q3" in out.segments[1].text


def test_markdown_keeps_a_heading_path(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# Deploying\n\nRun the script.\n\n## Rollback\n\nRestore within ten minutes.\n")
    out = extract(path)
    assert out.kind == "markdown"
    sections = [segment.section for segment in out.segments]
    assert any("Deploying" in section for section in sections)
    assert any("Rollback" in section for section in sections)


def test_xlsx_rows_become_sheet_ranges(corpus: Path) -> None:
    out = extract(corpus / "budget.xlsx")
    assert out.kind == "xlsx"
    joined = " ".join(segment.text for segment in out.segments)
    assert "Retry budget" in joined
    assert all(segment.locator.startswith("Budget!rows") for segment in out.segments)


def test_broken_file_reports_a_note_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a pdf")
    out = extract(path)
    assert out.segments == []
    assert out.note


def test_unknown_extension_is_read_as_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.log"
    path.write_text("retry budget exhausted\n")
    out = extract(path)
    assert out.kind == "text"
    assert "retry budget" in out.segments[0].text
