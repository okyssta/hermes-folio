"""HTTP surface for the Folio desktop page.

The pane must read exactly the index the tools write, so this module imports the
plugin package instead of opening its own store. Endpoints are plain ``def``:
they run on the threadpool, which keeps the synchronous SQLite and PDFium work
off the event loop.
"""

from __future__ import annotations

import base64
import importlib
import io
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()

_MAX_IMAGE = 1_600_000


def _plugin() -> Any:
    """Find the already-imported Folio package (never import it twice)."""
    root = Path(__file__).resolve().parent.parent
    target = (root / "__init__.py").resolve()
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).resolve() == target:
            return module
    for name in ("folio", f"plugins.{root.name}"):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    raise RuntimeError("folio package is not importable from the dashboard process")


def _store():
    return _plugin().current_store()


def _doc_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _submodule(name: str) -> Any:
    """Import a submodule of the *already loaded* package (never a second copy)."""
    package = _plugin()
    return importlib.import_module(f"{package.__name__}.{name}")


@router.get("/status")
def status() -> dict[str, Any]:
    plugin = _plugin()
    store = _store()
    stats = store.stats()
    indexer = plugin.current_indexer()
    scan = indexer.state.as_dict() if indexer else {}
    settings = plugin.current_settings()
    return {
        "ok": True,
        "stats": stats,
        "scan": scan,
        "enabled": settings.enabled,
        "auto_scan_minutes": settings.auto_scan_minutes,
        "roots": [root["path"] for root in stats["roots"]],
        "candidates": indexer.candidates()[:6] if indexer and not stats["documents"] else [],
    }


@router.get("/search")
def search(
    q: str = Query("", min_length=0, max_length=400),
    limit: int = Query(12, ge=1, le=40),
) -> dict[str, Any]:
    search_mod = _submodule("search")

    store = _store()
    query = (q or "").strip()
    if not query:
        return {"ok": True, "query": "", "results": [], "total_documents": store.stats()["documents"]}

    match, words = search_mod.build_match(query)
    if not match:
        return {"ok": True, "query": query, "results": [], "error": "no searchable words"}

    try:
        hits = store.search(match, limit=limit * 3)
    except Exception:
        fallback, words = search_mod.build_match(" ".join(search_mod.tokenize(query)))
        hits = store.search(fallback, limit=limit * 3) if fallback else []

    db = store.conn()
    results = []
    seen: set[tuple[int, str]] = set()
    for hit in hits:
        key = (hit.doc_id, hit.locator)
        if key in seen:
            continue
        seen.add(key)
        row = db.execute("SELECT text FROM chunks WHERE id=?", (hit.chunk_id,)).fetchone()
        body = row["text"] if row else hit.snippet
        results.append(
            {
                "doc_id": hit.doc_id,
                "path": hit.path,
                "title": hit.title,
                "kind": hit.kind,
                "locator": hit.locator,
                "section": hit.section,
                "snippet": hit.snippet.replace("\x01", "").replace("\x02", ""),
                "highlight": _highlight_terms(hit.snippet, words),
                "score": round(hit.score, 4),
            }
        )
        if len(results) >= limit:
            break

    results.sort(key=lambda item: item["score"])
    return {"ok": True, "query": query, "results": results, "total_documents": store.stats()["documents"]}


def _highlight_terms(snippet: str, words: list[str]) -> list[str]:
    found = []
    lowered = snippet.lower()
    for word in words[:8]:
        if word and word in lowered and word not in found:
            found.append(word)
    return found


@router.get("/page")
def page(
    doc_id: int = Query(..., ge=1),
    locator: str = Query(""),
    q: str = Query(""),
) -> dict[str, Any]:
    store = _store()
    row = store.get_document(doc_id)
    if row is None:
        return {"ok": False, "error": "no such document"}

    text = store.locator_text(doc_id, locator) if locator else store.sample_text(doc_id, 6000)
    payload: dict[str, Any] = {
        "ok": True,
        "doc": _doc_dict(row),
        "locator": locator,
        "text": text,
        "image": None,
        "rects": [],
        "width": 0,
        "height": 0,
    }
    if (row["kind"] or "").lower() == "pdf" and locator.startswith("p. "):
        try:
            payload.update(_render_page(row["path"], locator, text, q))
        except Exception as exc:  # a render failure must not break the reader
            payload["render_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _render_page(path: str, locator: str, text: str, query: str, scale: float = 1.7) -> dict[str, Any]:
    import pypdfium2 as pdfium

    number = int(re.sub(r"[^0-9]", "", locator) or "1")
    document = pdfium.PdfDocument(path)
    try:
        page = document[number - 1]
        width_pt, height_pt = page.get_size()
        textpage = page.get_textpage()
        total = textpage.count_chars()
        raw = textpage.get_text_range(0, total) if total else ""

        start, length = _locate(raw, text, query)
        rects = _rects(textpage, start, length, width_pt, height_pt) if start >= 0 else []

        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
    finally:
        try:
            document.close()
        except Exception:
            pass

    encoded = ""
    if len(data) <= _MAX_IMAGE:
        encoded = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return {
        "image": encoded or None,
        "rects": rects,
        "width": round(width_pt * scale),
        "height": round(height_pt * scale),
        "scale": scale,
    }


def _locate(raw: str, text: str, query: str) -> tuple[int, int]:
    """Find where this passage sits in the page's raw text, and the span to mark.

    The stored text is normalised (hyphens joined, whitespace collapsed) while the
    page's raw text is not, so both sides go through the same canonicalisation and
    the match is mapped back to a raw character index.
    """
    if not raw or not text:
        return -1, 0
    search_mod = _submodule("search")
    haystack, index = search_mod.canonical_map(raw)
    probe, _ = search_mod.canonical_map(text)
    if not probe:
        return -1, 0

    start = length = -1
    for size in (240, 180, 130, 90, 60, 36, 20):
        position = haystack.find(probe[:size])
        if position >= 0:
            start = index[position]
            end_at = min(len(probe), size) - 1
            length = index[min(position + end_at, len(index) - 1)] - start + 1
            break
    if start < 0:
        return -1, 0

    # Tighten the mark onto the query itself when it is close by.
    for word in [w for w in re.findall(r"[A-Za-z0-9]{4,}", query or "")][:6]:
        found = raw.lower().find(word.lower(), max(0, start - 200))
        if found >= 0 and abs(found - start) < 900:
            return found, min(len(word) + 10, 240)
    return start, length


def _rects(textpage: Any, start: int, length: int, page_width: float,
           page_height: float) -> list[dict[str, float]]:
    """Merge character boxes into one rectangle per text line, as 0..1 fractions."""
    lines: list[dict[str, float]] = []
    for index in range(start, min(start + length, textpage.count_chars())):
        try:
            left, bottom, right, top = textpage.get_charbox(index)
        except Exception:
            continue
        if right - left <= 0 or top - bottom <= 0:
            continue
        if lines and _same_line(lines[-1], top, bottom):
            line = lines[-1]
            line["left"] = min(line["left"], left)
            line["right"] = max(line["right"], right)
            line["top"] = max(line["top"], top)
            line["bottom"] = min(line["bottom"], bottom)
        else:
            lines.append({"left": left, "right": right, "top": top, "bottom": bottom})
        if len(lines) >= 14:
            break
    return [
        {
            "left": round(max(0.0, line["left"] / page_width), 5),
            "right": round(min(1.0, line["right"] / page_width), 5),
            "top": round(max(0.0, (page_height - line["top"]) / page_height), 5),
            "bottom": round(min(1.0, (page_height - line["bottom"]) / page_height), 5),
        }
        for line in lines
    ]



def _same_line(line: dict[str, float], top: float, bottom: float) -> bool:
    overlap = min(line["top"], top) - max(line["bottom"], bottom)
    height = min(line["top"] - line["bottom"], top - bottom) or 1.0
    return overlap > height * 0.4


@router.get("/changes")
def changes(since: str = Query("7d"), limit: int = Query(20, ge=1, le=60)) -> dict[str, Any]:
    tools = _submodule("tools")

    since_ts = tools._parse_since(since)
    store = _store()
    rows = store.changes_since(since_ts, limit=limit)
    return {"ok": True, "since": since_ts, "changes": rows}


@router.post("/index")
def index(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    paths = body.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    indexer = _plugin().current_indexer()
    if indexer is None:
        return {"ok": False, "error": "indexer unavailable"}
    result = indexer.index([str(p) for p in paths] or None, force=bool(body.get("force")))
    if isinstance(result, dict):
        result.pop("state", None)
    return {"ok": bool(result.get("ok", True)), **result}


@router.post("/forget")
def forget(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    store = _store()
    refs = body.get("docs") or []
    if body.get("all"):
        removed = store.forget(None)
        return {"ok": True, "removed": removed}
    ids = []
    for ref in refs:
        row = store.resolve_document(str(ref))
        if row is not None:
            ids.append(int(row["id"]))
    removed = store.forget(ids) if ids else 0
    return {"ok": True, "removed": removed}


@router.get("/document")
def document(doc_id: int = Query(..., ge=1)) -> dict[str, Any]:
    store = _store()
    row = store.get_document(doc_id)
    if row is None:
        return {"ok": False, "error": "no such document"}
    db = store.conn()
    locators = [
        r["locator"]
        for r in db.execute(
            "SELECT DISTINCT locator FROM chunks WHERE doc_id=? AND version_id=? ORDER BY ord",
            (doc_id, int(row["current_version"] or 0)),
        )
    ]
    versions = [
        {"id": int(v["id"]), "captured_at": float(v["captured_at"]), "chars": int(v["chars"])}
        for v in db.execute(
            "SELECT id, captured_at, chars FROM versions WHERE doc_id=? ORDER BY captured_at DESC",
            (doc_id,),
        )
    ]
    return {"ok": True, "doc": _doc_dict(row), "locators": locators[:400], "versions": versions}
