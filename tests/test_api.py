"""The desktop backend, exercised over real HTTP with a throwaway library."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from folio.indexer import Indexer
from folio.settings import Settings
from folio.store import Store


@pytest.fixture()
def client(tmp_path: Path, corpus: Path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import folio
    from folio.dashboard import plugin_api  # noqa: F401  (module import is the point)

    settings = Settings(roots=[str(corpus)], auto_scan_minutes=0)
    store = Store(tmp_path / "api.db")
    indexer = Indexer(store, lambda: settings)
    indexer.index([str(corpus)])

    monkeypatch.setattr(folio, "_CACHED", settings, raising=False)
    monkeypatch.setattr(folio, "_CACHED_AT", time.monotonic() + 10_000, raising=False)
    monkeypatch.setattr(folio, "_STORE", store, raising=False)
    monkeypatch.setattr(folio, "_INDEXER", indexer, raising=False)

    app = fastapi.FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/folio")
    return TestClient(app), store


def test_status_reports_the_library(client) -> None:
    http, _store = client
    payload = http.get("/api/plugins/folio/status").json()
    assert payload["ok"]
    assert payload["stats"]["documents"] == 7
    assert payload["enabled"] is True


def test_search_returns_locators_and_snippets(client) -> None:
    http, _store = client
    payload = http.get("/api/plugins/folio/search", params={"q": "retry budget"}).json()
    assert payload["ok"]
    assert payload["results"]
    first = payload["results"][0]
    assert first["locator"]
    assert first["title"]
    assert first["highlight"]


def test_empty_query_is_not_an_error(client) -> None:
    http, _store = client
    payload = http.get("/api/plugins/folio/search", params={"q": ""}).json()
    assert payload["ok"] and payload["results"] == []


def test_page_returns_a_rendered_pdf_with_highlight_boxes(client) -> None:
    http, _store = client
    results = http.get("/api/plugins/folio/search", params={"q": "retry"}).json()["results"]
    hit = next(item for item in results if item["kind"] == "pdf")
    payload = http.get(
        "/api/plugins/folio/page",
        params={"doc_id": hit["doc_id"], "locator": hit["locator"], "q": "retry"},
    ).json()
    assert payload["ok"]
    assert payload["text"]
    assert payload["image"].startswith("data:image/png;base64,")
    assert payload["rects"], "the matching sentence should be boxed on the page"
    assert 0 <= payload["rects"][0]["left"] <= 1


def test_changes_and_forget_round_trip(client) -> None:
    http, store = client
    changes = http.get("/api/plugins/folio/changes", params={"since": "1d"}).json()
    assert changes["ok"] and changes["changes"]

    doc_id = changes["changes"][0]["doc_id"]
    removed = http.post("/api/plugins/folio/forget", json={"docs": [str(doc_id)]}).json()
    assert removed["ok"] and removed["removed"] == 1
    assert store.get_document(doc_id) is None


def test_index_endpoint_reports_a_result(client) -> None:
    http, _store = client
    payload = http.post("/api/plugins/folio/index", json={}).json()
    assert payload["ok"]
    assert payload.get("unchanged", 0) >= 6
