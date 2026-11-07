"""/folio — the human-facing surface.

A command answers in sentences, not JSON: it runs in the gateway process, returns
text instead of printing, and only ever changes this plugin's own index or the
user's library folders.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from . import tools
from .settings import Settings

USAGE = """folio — your document library, with citations

  /folio                      status: what is indexed, how fresh, how big
  /folio add <folder>         add a folder to the library and index it
  /folio reindex              refresh what changed
  /folio search <words>       find a passage and the page it is on
  /folio changes [since]      what was added or revised (default 7d)
  /folio forget <doc|all>     delete from the index (originals untouched)
  /folio help                 this text"""


def _settings() -> Settings:
    from . import current_settings

    return current_settings()


def _store():
    from . import current_store

    return current_store()


def _age(ts: float) -> str:
    if not ts:
        return "never"
    seconds = max(0.0, time.time() - ts)
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds / 3600)}h ago"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _status() -> str:
    settings = _settings()
    stats = _store().stats()
    lines = [
        f"Folio · {stats['documents']:,} documents · {stats['chunks']:,} passages · "
        f"{stats['chars'] / 1e6:.1f}M characters"
        + (f" · {stats['versions'] - stats['documents']:,} older revisions"
           if stats["versions"] > stats["documents"] else ""),
    ]
    if stats["kinds"]:
        kinds = ", ".join(f"{kind} {count}" for kind, count in list(stats["kinds"].items())[:6])
        lines.append(f"  types: {kinds}")
    lines.append(f"  index: {stats['db_bytes'] / 1e6:.1f} MB · refreshed {_age(stats['last_indexed'])}")
    roots = stats["roots"]
    if roots:
        lines.append("  folders:")
        for root in roots:
            last = _age(root["last_scan"]) if root["last_scan"] else "never"
            lines.append(f"    {root['path']} · {root['doc_count']:,} files · scanned {last}")
    else:
        lines.append("  folders: none yet — /folio add <folder>")
    if settings.auto_scan_minutes:
        lines.append(f"  refresh: every {settings.auto_scan_minutes} min")
    else:
        lines.append("  refresh: manual (/folio reindex)")
    if stats["missing"]:
        lines.append(f"  {stats['missing']:,} indexed file(s) have moved or been deleted")
    return "\n".join(lines)


def _add(target: str) -> str:
    from pathlib import Path

    if not target:
        candidates = _store() and None
        indexer = __import__("folio", fromlist=["current_indexer"]).current_indexer()
        candidates = indexer.candidates() if indexer else []
        if not candidates:
            return "Usage: /folio add <folder>"
        lines = ["Which folder? /folio add <path>", ""]
        for candidate in candidates[:8]:
            lines.append(f"  {candidate['path']} · {candidate['files']:,} files, {candidate['bytes'] / 1e6:.0f} MB")
        return "\n".join(lines)
    path = Path(target).expanduser()
    if not path.exists():
        return f"{path} does not exist."
    from . import current_indexer

    result = current_indexer().index([str(path)])
    if not result.get("ok"):
        return f"Not indexed: {result.get('error')}"
    return (
        f"Added {path} · {result.get('indexed', 0)} new, {result.get('updated', 0)} revised, "
        f"{result.get('unchanged', 0)} unchanged in "
        f"{result.get('elapsed_s', 0):.1f}s.\n\n" + _status()
    )


def _search(query: str) -> str:
    if not query:
        return "Usage: /folio search <words>"
    return tools.folio_search({"query": query, "limit": 6})


def _changes(since: str) -> str:
    return tools.folio_changes({"since": since or "7d", "limit": 12})


def _forget(target: str) -> str:
    if not target:
        return "Usage: /folio forget <document|all>"
    if target.strip().lower() in {"all", "*", "everything"}:
        return tools.folio_forget({"all": True})
    return tools.folio_forget({"docs": [target]})


def handle(raw_args: str = "", **kwargs: Any) -> str:
    text = (raw_args or "").strip()
    lowered = text.lower()
    if lowered in {"", "status", "stats", "info"}:
        return _status()
    if lowered in {"help", "?", "-h", "--help"}:
        return USAGE

    verb, _, rest = text.partition(" ")
    verb = verb.lower()
    rest = rest.strip()

    try:
        if verb in {"add", "watch"}:
            return _add(rest)
        if verb in {"reindex", "index", "scan", "refresh"}:
            result = tools.folio_index({})
            return result
        if verb in {"search", "find", "s"}:
            return _search(rest)
        if verb in {"changes", "changed", "diff"}:
            return _changes(rest)
        if verb in {"forget", "remove", "delete"}:
            return _forget(rest)
        if verb in {"list", "ls"}:
            rows = _store().list_documents(limit=20)
            if not rows:
                return "The library is empty. /folio add <folder>"
            lines = [f"{len(rows)} document(s):"]
            for row in rows:
                lines.append(f"  doc {row['id']:<5} {row['title'][:64]} · {row['units_total']} {row['units_label']}(s)")
            return "\n".join(lines)
    except Exception as exc:  # a command must answer, never explode the CLI
        return f"folio: {type(exc).__name__}: {exc}"

    # Anything else is a bare query — the most likely intent.
    return _search(text)
