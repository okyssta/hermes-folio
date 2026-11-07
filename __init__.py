"""folio — every claim, a page.

Hermes can read a file you point it at, but it has no library: no way to ask
"where does the spec cap retries?", and no way to prove the sentence it quotes
back really exists. Folio indexes a folder of documents once — PDF, DOCX, PPTX,
XLSX, HTML, Markdown, source — and answers with the file, the page and the exact
sentence.

What it adds:

* **``folio_search`` / ``folio_open``** — find a passage, then read the page it
  came from, with the locator a citation needs.
* **``folio_verify``** — prove a quotation appears verbatim before you attribute
  it (EXACT / FUZZY / NOT FOUND, with character offsets).
* **``folio_changes``** — what was added or revised since a date, with a diff, so
  a contract that changed under you is visible.
* **``folio_bibliography``** — citations from the metadata the file actually
  carries.
* **``folio_index`` / ``folio_forget``** — add folders, refresh incrementally,
  and delete what the user asks you to forget.
* **``/folio``** — status, add, search, forget from the command line.
* **A desktop page** — the library, a search box, and the page image with the
  matching sentence boxed.
* **A bundled skill** — when a document outranks memory, and how to cite it.

Everything is local: one SQLite database under the profile's plugin-data folder.
No account, no API key, no embeddings, no network calls; the user's original
files are only ever read.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# A checkout directory is often not a valid Python package name, and pytest
# imports this file as a bare module. Re-home the module under its real name so
# the relative imports below resolve the same way in the host, in pytest and in
# an ad-hoc script.
if __package__ in (None, ""):
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _PARENT = os.path.dirname(_HERE)
    if _PARENT not in sys.path:
        sys.path.append(_PARENT)
    __path__ = [_HERE]  # type: ignore[assignment]
    __package__ = "folio"
    sys.modules.setdefault("folio", sys.modules[__name__])

from . import commands, schemas, tools
from .indexer import AutoScanner, Indexer
from .settings import PLUGIN_ID, SCHEMA_KEYS, Settings
from .store import Store

logger = logging.getLogger(__name__)

_CTX: Any = None
_CACHED: Settings | None = None
_CACHED_AT = 0.0
_CACHE_SECONDS = 2.0
_STORE: Store | None = None
_INDEXER: Indexer | None = None
_SCANNER: AutoScanner | None = None


def _hermes_home() -> Path | None:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:  # pragma: no cover - older hosts
        return None


def data_dir() -> Path:
    """The profile-scoped data directory, resolved on every call."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(PLUGIN_ID)
    except Exception:  # pragma: no cover - standalone/pytest use
        home = _hermes_home() or Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        root = Path(home) / "plugin-data" / PLUGIN_ID
        root.mkdir(parents=True, exist_ok=True)
        return root


def current_settings() -> Settings:
    global _CACHED, _CACHED_AT

    now = time.monotonic()
    if _CACHED is not None and now - _CACHED_AT < _CACHE_SECONDS:
        return _CACHED
    if _CTX is None:
        _CACHED = Settings()
    else:
        _CACHED = Settings.from_ctx(_CTX, _hermes_home())
    _CACHED_AT = now
    return _CACHED


def invalidate_settings() -> None:
    """Drop the memoised settings after a command changes config.yaml."""
    global _CACHED, _CACHED_AT
    _CACHED = None
    _CACHED_AT = 0.0


def current_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store(data_dir() / "folio.db")
    return _STORE


def current_indexer() -> Indexer | None:
    global _INDEXER
    if _INDEXER is None:
        _INDEXER = Indexer(current_store(), current_settings)
    return _INDEXER


# ------------------------------------------------------------------ register

def register(ctx: Any) -> None:
    global _CTX, _SCANNER

    _CTX = ctx
    settings = current_settings()

    handlers = {
        "folio_search": tools.folio_search,
        "folio_open": tools.folio_open,
        "folio_verify": tools.folio_verify,
        "folio_changes": tools.folio_changes,
        "folio_bibliography": tools.folio_bibliography,
        "folio_index": tools.folio_index,
        "folio_forget": tools.folio_forget,
    }
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="folio",
            schema=schema,
            handler=handlers[schema["name"]],
        )

    try:
        ctx.register_command("folio", commands.handle, description="Your document library, with citations")
    except Exception:  # pragma: no cover - optional surface
        logger.debug("folio: could not register /folio", exc_info=True)

    skill_dir = Path(__file__).parent / "skills" / "folio"
    if (skill_dir / "SKILL.md").is_file():
        try:
            ctx.register_skill("folio", str(skill_dir))
        except Exception:  # pragma: no cover - optional surface
            logger.debug("folio: could not register bundled skill", exc_info=True)

    if settings.enabled and settings.auto_scan_minutes:
        _SCANNER = AutoScanner(lambda: _INDEXER, lambda: current_settings().auto_scan_minutes)
        _SCANNER.start()

    logger.info(
        "folio ready: %s folder(s), %s documents indexed",
        len(settings.roots),
        _safe_count(),
    )


def _safe_count() -> str:
    try:
        return f"{current_store().stats()['documents']:,}"
    except Exception:
        return "?"


__all__ = [
    "register",
    "current_settings",
    "current_store",
    "current_indexer",
    "invalidate_settings",
    "data_dir",
    "SCHEMA_KEYS",
]
