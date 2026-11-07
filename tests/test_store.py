"""Index lifecycle: revisions, deletions, forget, and search over the real store."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from folio.indexer import Indexer
from folio.settings import Settings
from folio.store import Store
from tests.conftest import make_pdf


@pytest.fixture()
def library(tmp_path: Path, corpus: Path):
    settings = Settings(roots=[str(corpus)], auto_scan_minutes=0)
    store = Store(tmp_path / "folio.db")
    indexer = Indexer(store, lambda: settings)
    result = indexer.index([str(corpus)])
    assert result["ok"]
    return store, indexer, settings, corpus


def test_indexes_every_format(library) -> None:
    store, _indexer, _settings, _corpus = library
    stats = store.stats()
    assert stats["documents"] == 7
    assert stats["chunks"] > 5
    kinds = set(stats["kinds"])
    assert {"pdf", "docx", "pptx", "xlsx", "markdown", "code"} <= kinds


def test_search_finds_a_page_and_locator(library) -> None:
    store, _indexer, _settings, _corpus = library
    hits = store.search('"retry"')
    assert hits
    pdf_hits = [hit for hit in hits if hit.locator.startswith("p. ")]
    assert pdf_hits, "a PDF page should be among the hits"
    body = store.locator_text(pdf_hits[0].doc_id, pdf_hits[0].locator)
    assert "retry" in body.lower()


def test_search_ranks_the_specific_page_first(library) -> None:
    store, _indexer, _settings, _corpus = library
    hits = store.search('"retry" OR "budget"')
    titles = [hit.title for hit in hits]
    assert any("spec" in title.lower() or "Service Level" in title for title in titles)


def test_revision_creates_a_version_and_changes_report(library) -> None:
    store, indexer, _settings, corpus = library
    path = corpus / "spec.pdf"
    before = store.find_document(str(path))
    assert int(before["version_count"]) == 1

    make_pdf(
        path,
        [
            [
                "Service Level Specification",
                "Retries are capped at five attempts per request.",
            ]
        ],
    )
    result = indexer.index([str(corpus)])
    assert result["updated"] == 1

    after = store.find_document(str(path))
    assert int(after["version_count"]) == 2

    changes = store.changes_since(time.time() - 120)
    entry = next(item for item in changes if item["path"] == str(path))
    assert entry["previous_version"] and entry["latest_version"]

    latest = store.version_text(entry["doc_id"], entry["latest_version"])
    previous = store.version_text(entry["doc_id"], entry["previous_version"])
    assert "five attempts" in latest
    assert "three attempts" in previous

    hits = store.search('"five"')
    assert any(hit.doc_id == entry["doc_id"] for hit in hits)


def test_unchanged_files_are_not_reread(library) -> None:
    _store, indexer, _settings, corpus = library
    result = indexer.index([str(corpus)])
    assert result["unchanged"] == 7
    assert result["updated"] == 0


def test_forced_rescan_refreshes_metadata_without_a_new_revision(library) -> None:
    store, indexer, _settings, corpus = library
    before = store.find_document(str(corpus / "spec.pdf"))
    result = indexer.index([str(corpus)], force=True)
    assert result["unchanged"] == 0
    assert result["updated"] == 7
    after = store.find_document(str(corpus / "spec.pdf"))
    assert int(after["version_count"]) == int(before["version_count"])
    assert after["title"] == before["title"]


def test_deleted_file_is_marked_missing_and_leaves_search(library) -> None:
    store, indexer, _settings, corpus = library
    (corpus / "notes.pdf").unlink()
    result = indexer.index([str(corpus)])
    assert result["removed"] == 1

    stats = store.stats()
    assert stats["missing"] == 1
    assert all("notes.pdf" not in hit.path for hit in store.search('"retry"'))


def test_forget_removes_rows_and_search_entries(library) -> None:
    store, _indexer, _settings, corpus = library
    row = store.find_document(str(corpus / "spec.pdf"))
    removed = store.forget([int(row["id"])])
    assert removed == 1
    assert store.find_document(str(corpus / "spec.pdf")) is None
    assert all(hit.doc_id != int(row["id"]) for hit in store.search('"retry"'))


def test_forget_everything_empties_the_index(library) -> None:
    store, _indexer, _settings, _corpus = library
    assert store.forget(None) == 7
    stats = store.stats()
    assert stats["documents"] == 0 and stats["chunks"] == 0
    assert store.search('"retry"') == []


def test_redaction_runs_before_storage(tmp_path: Path) -> None:
    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "secrets.md").write_text(
        "Use key sk-abcdef0123456789abcdef for the API.\n", encoding="utf-8"
    )
    settings = Settings(roots=[str(corpus)], redact=True)
    store = Store(tmp_path / "redact.db")
    Indexer(store, lambda: settings).index([str(corpus)])

    hits = store.search('"key"')
    assert hits
    body = store.locator_text(hits[0].doc_id, hits[0].locator)
    assert "sk-abcdef" not in body
    assert "[redacted]" in body
