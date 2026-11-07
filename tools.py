"""Tool handlers.

Contract: ``def handler(args: dict, **kwargs) -> str``. A handler never raises —
it returns text the model can act on, including when the library is empty, a
document has moved, or a query is malformed. Every reply that names a passage
also carries the document id, the path and the locator, so the model can chain
search → open → verify without guessing.
"""

from __future__ import annotations

import difflib
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import cite, extract as extract_module, search as search_mod
from .store import SENT_CLOSE, SENT_OPEN


def _settings():
    from . import current_settings

    return current_settings()


def _store():
    from . import current_store

    return current_store()


def _indexer():
    from . import current_indexer

    return current_indexer()


# --------------------------------------------------------------------- helpers

def _mark(text: str) -> str:
    return text.replace(SENT_OPEN, "⟦").replace(SENT_CLOSE, "⟧").replace("\x00", "")


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _when(ts: float) -> str:
    if not ts:
        return "never"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _doc_line(row: Any) -> str:
    get = row.get if isinstance(row, dict) else (
        lambda key, default=None: getattr(row, key, default)
    )
    kind = get("kind") or "file"
    bits = [f"doc {get('id') or get('doc_id')}", kind]
    year = get("year")
    if year:
        bits.append(str(year))
    return f"{get('title')} ({', '.join(bits)})"


def _as_dict(row: Any) -> dict[str, Any]:
    """sqlite3.Row -> plain dict (``dict(row)`` does not work on a Row)."""
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return dict(getattr(row, "__dict__", {}))


def _parse_since(value: str) -> float:
    raw = (value or "").strip().lower()
    now = time.time()
    if not raw or raw in {"default", "7d"}:
        return now - 7 * 86400
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwmy])", raw)
    if match:
        amount = float(match.group(1))
        unit = match.group(2)
        factor = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000, "y": 31536000}[unit]
        return now - amount * factor
    if raw in {"today", "now"}:
        return datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if raw == "yesterday":
        return (datetime.now(tz=timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
    for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return now - 7 * 86400


# ---------------------------------------------------------------------- tools

def folio_search(args: dict[str, Any], **_: Any) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "folio_search needs a query."
    limit = max(1, min(int(args.get("limit") or 8), 25))
    doc_filter = str(args.get("doc") or "").strip()
    include_missing = bool(args.get("include_missing"))

    store = _store()
    match, words = search_mod.build_match(query)
    if not match:
        return f"No usable search terms in {query!r}."

    stats = store.stats()
    if not stats["documents"]:
        indexer = _indexer()
        candidates = indexer.candidates() if indexer else []
        hint = ""
        if candidates:
            listing = "\n".join(
                f"  - {c['path']} ({c['files']:,} files, {c['bytes'] / 1e6:.0f} MB)"
                for c in candidates[:5]
            )
            hint = "\n\nCandidate folders:\n" + listing + "\nAsk the user which to index, then call folio_index."
        return "The library is empty — nothing has been indexed yet." + hint

    try:
        hits = store.search(match, limit=limit * 3, include_missing=include_missing)
    except Exception as exc:
        # A malformed FTS expression: retry with the sanitised word form.
        fallback, words = search_mod.build_match(" ".join(search_mod.tokenize(query)))
        if not fallback or fallback == match:
            return f"That query could not be parsed as a search ({exc})."
        try:
            hits = store.search(fallback, limit=limit * 3, include_missing=include_missing)
        except Exception as exc2:
            return f"That query could not be parsed as a search ({exc2})."

    if doc_filter:
        needle = doc_filter.lower()
        hits = [h for h in hits if needle in h.path.lower() or needle in h.title.lower()]

    ranked = []
    for hit in hits:
        text = store.conn().execute("SELECT text FROM chunks WHERE id=?", (hit.chunk_id,)).fetchone()
        body = text["text"] if text else hit.snippet
        ranked.append((hit.score + search_mod.phrase_bonus(body, words), hit))
    ranked.sort(key=lambda pair: pair[0])

    seen: dict[tuple[int, str], Any] = {}
    for _score, hit in ranked:
        key = (hit.doc_id, hit.locator)
        if key not in seen:
            seen[key] = hit
    chosen = list(seen.values())[:limit]

    if not chosen:
        return (
            f"No passage matched {query!r} in {stats['documents']:,} documents. "
            "Try fewer, more distinctive words, or a quoted phrase."
        )

    lines = [
        f"{len(chosen)} passage(s) · {stats['documents']:,} documents · "
        f"{stats['chunks']:,} passages indexed · query {query!r}",
        "",
    ]
    for position, hit in enumerate(chosen, start=1):
        snippet = _clip(_mark(hit.snippet), 320)
        section = f" · {hit.section}" if hit.section else ""
        lines.append(f"[{position}] {hit.locator}{section} · {_doc_line(hit)}")
        lines.append(f"    {snippet}")
        lines.append(f"    {hit.path}")
        lines.append("")
    lines.append("Open the full passage with folio_open(doc=<id>, locator=<locator>).")
    return "\n".join(lines).strip()


def folio_open(args: dict[str, Any], **_: Any) -> str:
    ref = str(args.get("doc") or "").strip()
    if not ref:
        return "folio_open needs a document (id, path fragment or title)."
    locator = str(args.get("locator") or "").strip()
    want = str(args.get("version") or "current").strip().lower()
    max_chars = max(500, min(int(args.get("max_chars") or 6000), 24000))

    store = _store()
    row = store.resolve_document(ref)
    if row is None:
        return f"No document in the library matches {ref!r}. Use folio_search first."

    db = store.conn()
    versions = db.execute(
        "SELECT id, captured_at, chars, note FROM versions WHERE doc_id=? ORDER BY captured_at DESC, id DESC",
        (row["id"],),
    ).fetchall()
    version_id = int(row["current_version"] or 0)
    if want == "previous" and len(versions) > 1:
        version_id = int(versions[1]["id"])
    captured = next((v for v in versions if int(v["id"]) == version_id), None)

    if locator:
        text = store.locator_text(int(row["id"]), locator, version_id=version_id)
        if not text:
            available = [
                r["locator"]
                for r in db.execute(
                    "SELECT DISTINCT locator FROM chunks WHERE doc_id=? AND version_id=? ORDER BY ord LIMIT 20",
                    (int(row["id"]), version_id),
                )
            ]
            hint = ", ".join(available[:12]) if available else "none"
            return (
                f"{row['title']}: no passage at {locator!r} in this version. "
                f"Locators available: {hint}."
            )
    else:
        text = store.sample_text(int(row["id"]), max_chars)
        locators = [
            r["locator"]
            for r in db.execute(
                "SELECT DISTINCT locator FROM chunks WHERE doc_id=? AND version_id=? ORDER BY ord LIMIT 12",
                (int(row["id"]), version_id),
            )
        ]
        locator = locators[0] if locators else ""
        if locators:
            text = f"(passage starts at {locator}; locators: {', '.join(locators)})\n\n" + text

    if not text:
        return f"{row['title']} has no stored text (it may have been redacted to nothing)."

    body = _clip(text, max_chars)
    head_version = ""
    if len(versions) > 1:
        position = next(
            (index + 1 for index, row_ in enumerate(versions) if int(row_["id"]) == version_id),
            len(versions),
        )
        head_version = f" · revision {position} of {len(versions)}"
        if position < len(versions):
            head_version += " (before the current one)"
    header = [
        f"{row['title']} · {row['kind']} · {locator or 'start'}",
        row["path"],
        f"captured {_when(captured['captured_at'] if captured else row['last_indexed'])}"
        + head_version,
        "",
    ]
    citation = cite.format_citation(_as_dict(row), "plain", locator)
    return "\n".join(header) + body + f"\n\nCite as: {citation}"


def folio_verify(args: dict[str, Any], **_: Any) -> str:
    quote = str(args.get("quote") or "").strip()
    ref = str(args.get("doc") or "").strip()
    locator = str(args.get("locator") or "").strip()
    if not quote:
        return "folio_verify needs the quote you want to check."
    if not ref:
        return "folio_verify needs a document (id, path fragment or title)."

    store = _store()
    row = store.resolve_document(ref)
    if row is None:
        return f"No document matches {ref!r}."

    db = store.conn()
    version_id = int(row["current_version"] or 0)
    if locator:
        pages = [(locator, store.locator_text(int(row["id"]), locator, version_id))]
    else:
        pages = [
            (r["locator"], store.locator_text(int(row["id"]), r["locator"], version_id))
            for r in db.execute(
                "SELECT DISTINCT locator FROM chunks WHERE doc_id=? AND version_id=? ORDER BY ord",
                (int(row["id"]), version_id),
            )
        ]

    best = None
    for page_locator, text in pages:
        if not text:
            continue
        found = search_mod.find_quote(text, quote)
        if found.found and (best is None or found.ratio > best[1].ratio):
            best = (page_locator, found)
        if found.exact:
            break

    if best is None:
        where = f" in {locator}" if locator else ""
        return f"NOT FOUND — that quotation does not appear{where} in {row['title']}."

    page_locator, match = best
    if not match.exact and match.ratio < 0.55:
        return (
            f"NOT FOUND — no passage in {row['title']} matches that quotation "
            f"(closest is {page_locator}, {match.ratio:.0%} similar).\n"
            f"Closest text: …{_clip(match.context, 320)}…\n"
            "Do not present this as a quotation. Find the sentence with folio_search first."
        )
    label = "EXACT" if match.exact else "FUZZY"

    ratio = "" if match.exact else f" · similarity {match.ratio:.0%}"
    context = _clip(match.context, 420)
    tail = "" if match.exact else (
        "\nThis is not a verbatim quotation. Quote the highlighted span exactly, or "
        "describe it as a paraphrase."
    )
    return (
        f"{label} · {page_locator} · characters {match.start}-{match.end}{ratio}\n"
        f"{row['title']} (doc {row['id']})\n{row['path']}\n\n"
        f"…{context}…{tail}"
    )


def folio_changes(args: dict[str, Any], **_: Any) -> str:
    since_raw = str(args.get("since") or "7d")
    limit = max(1, min(int(args.get("limit") or 20), 60))
    since = _parse_since(since_raw)

    store = _store()
    rows = store.changes_since(since, limit=limit)
    if not rows:
        return f"Nothing was added or changed since {_when(since)}."

    db = store.conn()
    lines = [f"{len(rows)} document(s) changed since {_when(since)}", ""]
    for entry in rows:
        tag = "NEW" if entry["version_count"] <= 1 else "REV"
        age = _when(entry["latest_at"] or entry["last_indexed"])
        missing = " · file missing" if entry["missing"] else ""
        lines.append(
            f"{tag}  {entry['title']} (doc {entry['doc_id']}) · "
            f"revision {entry['version_count']} · {age}{missing}"
        )
        lines.append(f"     {entry['path']}")
        if entry["previous_version"] and entry["latest_version"]:
            old = store.version_text(entry["doc_id"], entry["previous_version"])
            new = store.version_text(entry["doc_id"], entry["latest_version"])
            diff = _diff_sample(old, new)
            if diff:
                lines.append("     " + diff.replace("\n", "\n     "))
        lines.append("")
    lines.append("Read an older revision with folio_open(doc=<id>, version='previous').")
    return "\n".join(lines).strip()


def _diff_sample(old: str, new: str, max_lines: int = 8) -> str:
    old_lines = [line.strip() for line in old.splitlines() if line.strip()]
    new_lines = [line.strip() for line in new.splitlines() if line.strip()]
    diff = list(
        difflib.unified_diff(old_lines, new_lines, lineterm="", n=0)
    )[2:]
    added = sum(1 for line in diff if line.startswith("+"))
    removed = sum(1 for line in diff if line.startswith("-"))
    if not added and not removed:
        return ""
    shown = [line[:160] for line in diff if line[:1] in {"+", "-"}][:max_lines]
    head = f"+{added} / -{removed} lines"
    return head + ("\n" + "\n".join(shown) if shown else "")


def folio_bibliography(args: dict[str, Any], **_: Any) -> str:
    refs = args.get("docs") or []
    if isinstance(refs, str):
        refs = [refs]
    refs = [str(r) for r in refs if str(r).strip()]
    if not refs:
        return "folio_bibliography needs at least one document."
    style = str(args.get("style") or "apa").lower()
    if style not in cite.STYLES:
        return f"Unknown style {style!r}. Use one of: {', '.join(cite.STYLES)}."
    locator = str(args.get("locator") or "").strip()

    store = _store()
    found = []
    missing = []
    for ref in refs:
        row = store.resolve_document(ref)
        if row is None:
            missing.append(ref)
        else:
            found.append(_as_dict(row))

    if not found:
        return "None of those documents are in the library: " + ", ".join(missing)

    body = cite.format_citations(found, style, locator)
    if missing:
        body += "\n\nNot in the library: " + ", ".join(missing)
    return body


def folio_index(args: dict[str, Any], **_: Any) -> str:
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    paths = [str(p) for p in paths if str(p).strip()]
    indexer = _indexer()
    if indexer is None:
        return "Folio is still loading; try again in a moment."

    started = time.perf_counter()
    result = indexer.index(paths or None)
    elapsed = time.perf_counter() - started

    if not result.get("ok"):
        candidates = result.get("candidates") or []
        lines = [f"Not indexed: {result.get('error', 'unknown error')}"]
        if candidates:
            lines.append("")
            lines.append("Candidate folders on this machine:")
            for candidate in candidates[:6]:
                lines.append(
                    f"  - {candidate['path']} ({candidate['files']:,} files, "
                    f"{candidate['bytes'] / 1e6:.0f} MB)"
                )
            lines.append("Ask the user which to index, then call folio_index(paths=[...]).")
        return "\n".join(lines)

    if result.get("busy"):
        state = result.get("state", {})
        return (
            f"A scan is already running ({state.get('done', 0)}/{state.get('total', '?')} files). "
            "Try again when it finishes."
        )

    stats = _store().stats()
    lines = [
        f"Indexed: {result.get('indexed', 0)} new, {result.get('updated', 0)} revised, "
        f"{result.get('unchanged', 0)} unchanged, {result.get('skipped', 0)} skipped, "
        f"{result.get('failed', 0)} failed · {elapsed:.1f}s",
        f"Library: {stats['documents']:,} documents · {stats['chunks']:,} passages · "
        f"{stats['chars'] / 1e6:.1f}M characters",
        f"Readers: {extract_module.reader_status()}",
    ]
    recent = (result.get("state") or {}).get("recent") or []
    if recent:
        lines.append("")
        for entry in recent[:6]:
            lines.append(
                f"  {entry['version']:<8} {entry['title'][:60]} · {entry['passages']} passages "
                f"· {entry['ms']:.0f} ms"
            )
    skips = (result.get("state") or {}).get("skips") or []
    if skips and result.get("skipped"):
        lines.append("")
        lines.append("Skipped:")
        for reason, count in skips[:4]:
            lines.append(f"  {count:>4}  {reason}")
    return "\n".join(lines)



def folio_forget(args: dict[str, Any], **_: Any) -> str:
    refs = args.get("docs") or []
    if isinstance(refs, str):
        refs = [refs]
    refs = [str(r) for r in refs if str(r).strip()]
    drop_all = bool(args.get("all"))
    if drop_all and refs:
        return "Pass either docs or all=true, not both."

    store = _store()
    remove_root = str(args.get("remove_root") or "").strip()
    if remove_root:
        store.remove_root(str(Path(remove_root).expanduser()))

    if drop_all:
        removed = store.forget(None)
        store.set_meta("last_forget", time.time())
        return f"Forgot the entire library: {removed} document(s), all revisions and passages deleted."

    if not refs:
        return "Nothing to forget: pass docs=[...] or all=true."

    ids: list[int] = []
    names: list[str] = []
    unknown: list[str] = []
    for ref in refs:
        row = store.resolve_document(ref)
        if row is None:
            unknown.append(ref)
        else:
            ids.append(int(row["id"]))
            names.append(row["title"])
    removed = store.forget(ids) if ids else 0
    message = f"Forgot {removed} document(s): " + ", ".join(names[:8]) if ids else "Nothing removed."
    if unknown:
        message += "\nNot in the library: " + ", ".join(unknown[:8])
    return message
