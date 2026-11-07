"""Scanning and indexing.

The scan is deliberately boring: walk the roots, skip what is obviously noise,
stat every file, and only re-read the ones whose mtime or size moved. Content is
hashed to decide whether a changed file is really a *new revision* (which gets a
version row) or just a touched timestamp.

One scan runs at a time, in the caller's thread; the background scanner calls the
same code, so the desktop refresh button and the timer can never trample each
other.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import extract as extract_mod
from .chunk import chunk_segment
from .extract import extract, sha256_file
from .redact import redact
from .settings import Settings, expand
from .store import Store

logger = logging.getLogger(__name__)

CANDIDATE_FOLDERS = (
    "~/Documents", "~/Desktop", "~/Downloads", "~/Papers", "~/Projects",
    "~/Dropbox", "~/OneDrive", "~/Google Drive",
)


@dataclass
class ScanState:
    running: bool = False
    root: str = ""
    total: int = 0
    done: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    note: str = ""
    recent: list[dict[str, Any]] = field(default_factory=list)
    skips: dict[str, int] = field(default_factory=dict)

    def note_skip(self, reason: str) -> None:
        reason = (reason or "unsupported file")[:160]
        self.skips[reason] = self.skips.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "root": self.root,
            "total": self.total,
            "done": self.done,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "failed": self.failed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "note": self.note,
            "recent": self.recent[:12],
            "skips": sorted(self.skips.items(), key=lambda kv: -kv[1])[:6],
        }



class Indexer:
    def __init__(self, store: Store, settings_getter: Callable[[], Settings]) -> None:
        self.store = store
        self.settings_getter = settings_getter
        self.state = ScanState()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ paths
    def resolve_roots(self) -> list[Path]:
        """Configured roots plus any folder added at runtime with folio_index."""
        settings = self.settings_getter()
        entries: list[str] = list(settings.roots)
        try:
            entries += [row["path"] for row in self.store.roots()]
        except Exception:
            pass
        roots: list[Path] = []
        for entry in entries:
            path = expand(entry)
            if path.is_dir() and path not in roots:
                roots.append(path)
        return roots

    def candidates(self) -> list[dict[str, Any]]:
        """Folders that look like a personal library, with a rough size."""
        out = []
        seen: set[str] = set()
        for raw in CANDIDATE_FOLDERS:
            path = expand(raw)
            if not path.is_dir() or str(path) in seen:
                continue
            seen.add(str(path))
            count, total = _quick_measure(path, self.settings_getter())
            out.append({"path": str(path), "files": count, "bytes": total})
        return out

    # ------------------------------------------------------------------ index
    def index(self, paths: Iterable[str] | None = None) -> dict[str, Any]:
        settings = self.settings_getter()
        if not settings.enabled:
            return {"ok": False, "error": "Folio is disabled in config (folio.enabled)."}

        targets: list[Path] = []
        if paths:
            for raw in paths:
                path = expand(str(raw))
                if path.is_file():
                    targets.append(path)
                elif path.is_dir():
                    self.store.add_root(str(path))
                    targets.append(path)
                else:
                    logger.debug("folio: skipping missing path %s", path)
        if not targets:
            targets = self.resolve_roots()

        if not targets:
            return {
                "ok": False,
                "error": "No library folders configured.",
                "candidates": self.candidates(),
                "hint": "Call folio_index with a folder (or run /folio add <folder>).",
            }

        if not self._lock.acquire(blocking=False):
            return {"ok": True, "busy": True, "state": self.state.as_dict()}

        try:
            self.state = ScanState(running=True, started_at=time.time())
            totals = {"indexed": 0, "updated": 0, "unchanged": 0, "skipped": 0,
                      "failed": 0, "removed": 0, "chars": 0}
            for target in targets:
                if target.is_file():
                    result = self._index_file(target, root="", settings=settings)
                    totals[_bucket(result)] = totals.get(_bucket(result), 0) + 1
                    self.state.total = max(self.state.total, 1)
                    self.state.done += 1
                else:
                    self._scan_root(target, settings, totals)
            self.state.finished_at = time.time()
            self.state.running = False
            totals["elapsed_s"] = round(self.state.finished_at - self.state.started_at, 2)
            totals["state"] = self.state.as_dict()
            return {"ok": True, **totals}
        finally:
            self.state.running = False
            self._lock.release()

    def _scan_root(self, root: Path, settings: Settings, totals: dict[str, Any]) -> None:
        known = self.store.known_paths(str(root))
        seen: set[str] = set()
        files = list(_walk(root, settings))
        self.state.root = str(root)
        self.state.total += len(files)

        for path in files:
            seen.add(str(path))
            result = self._index_file(path, root=str(root), settings=settings, known=known)
            bucket = _bucket(result)
            totals[bucket] = totals.get(bucket, 0) + 1
            if bucket in {"indexed", "updated"}:
                totals["chars"] += int(result.get("chars", 0))
            self.state.done += 1
            if bucket == "failed":
                self.state.failed += 1
            elif bucket == "skipped":
                self.state.skipped += 1
            elif bucket in {"indexed", "updated"}:
                self.state.indexed += 1

        for path, (doc_id, _sha, _mtime, missing) in known.items():
            if path not in seen and not missing:
                self.store.mark_missing(doc_id)
                totals["removed"] += 1

        self.store.touch_root(str(root), len(seen), self.state.note)
        self.state.root = ""

    def _index_file(
        self,
        path: Path,
        *,
        root: str,
        settings: Settings,
        known: dict[str, tuple[int, str, float, int]] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            stat = path.stat()
        except OSError as exc:
            return {"status": "failed", "path": str(path), "error": str(exc)}

        if stat.st_size > settings.max_file_mb * 1024 * 1024:
            reason = f"larger than {settings.max_file_mb:g} MB"
            self.state.note_skip(reason)
            return {"status": "skipped", "path": str(path), "reason": reason}

        if known is not None:
            prior = known.get(str(path))
            if prior and prior[3] == 0 and abs(prior[2] - stat.st_mtime) < 1e-6:
                return {"status": "unchanged", "path": str(path)}

        try:
            digest = sha256_file(path)
        except OSError as exc:
            return {"status": "failed", "path": str(path), "error": str(exc)}

        existing = self.store.find_document(str(path))
        if existing is not None and existing["sha256"] == digest and not existing["missing"]:
            self.store.upsert_document(
                path=str(path), root=root or existing["root"], ext=path.suffix.lower(),
                kind=existing["kind"], title=existing["title"], author=existing["author"],
                year=existing["year"], sha256=digest, size=stat.st_size, mtime=stat.st_mtime,
                units_total=existing["units_total"], units_label=existing["units_label"],
                chars=existing["chars"], note=existing["note"],
            )
            return {"status": "unchanged", "path": str(path)}

        try:
            extracted = extract(path, settings.max_text_chars)
        except Exception as exc:  # extract() already guards, this is belt and braces
            return {"status": "failed", "path": str(path), "error": str(exc)}

        if not extracted.segments:
            reason = extracted.note or "no extractable text"
            self.state.note_skip(reason)
            return {"status": "skipped", "path": str(path), "reason": reason}

        rows: list[dict[str, Any]] = []
        ordinal = 0
        for segment in extracted.segments:
            text = redact(segment.text) if settings.redact else segment.text
            if not text.strip():
                continue
            segment.text = text
            for piece in chunk_segment(segment, settings.chunk_chars):
                rows.append(
                    {
                        "ord": ordinal,
                        "locator": segment.locator,
                        "section": segment.section,
                        "page": segment.page,
                        "start": piece.start,
                        "length": piece.length,
                        "text": piece.text,
                    }
                )
                ordinal += 1

        if not rows:
            self.state.note_skip("no indexable text")
            return {"status": "skipped", "path": str(path), "reason": "no indexable text"}

        doc_id, changed, is_new = self.store.upsert_document(
            path=str(path),
            root=root or (existing["root"] if existing else ""),
            ext=path.suffix.lower(),
            kind=extracted.kind,
            title=extracted.title,
            author=extracted.author,
            year=extracted.year,
            sha256=digest,
            size=stat.st_size,
            mtime=stat.st_mtime,
            units_total=extracted.pages_total,
            units_label=extracted.units_label,
            chars=extracted.chars,
            note=extracted.note,
        )
        if not changed:
            return {"status": "unchanged", "path": str(path)}

        version_id = self.store.begin_version(
            doc_id, digest, extracted.chars, extracted.pages_total, extracted.note
        )
        kept = self.store.replace_chunks(doc_id, version_id, rows)
        if settings.keep_versions:
            self.store.prune_versions(doc_id, settings.max_versions)
        else:
            self.store.prune_versions(doc_id, 1)

        elapsed = (time.perf_counter() - started) * 1000
        entry = {
            "path": str(path),
            "title": extracted.title,
            "kind": extracted.kind,
            "locator_kind": extracted.units_label,
            "passages": kept,
            "chars": extracted.chars,
            "ms": round(elapsed, 1),
            "version": "new" if is_new else "revision",
        }
        self.state.recent.insert(0, entry)
        del self.state.recent[12:]
        return {"status": "indexed" if is_new else "updated", **entry}


def _bucket(result: dict[str, Any]) -> str:
    status = result.get("status", "failed")
    return {"indexed": "indexed", "updated": "updated", "unchanged": "unchanged",
            "skipped": "skipped"}.get(status, "failed")


def _walk(root: Path, settings: Settings) -> Iterable[Path]:
    exclude = {name.lower() for name in settings.exclude_dirs}
    allowed = {ext.lower() for ext in settings.include_extensions}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name for name in dirnames
            if name.lower() not in exclude and not name.startswith(".")
        ]
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() not in allowed:
                continue
            if path.is_symlink():
                continue
            yield path


def _quick_measure(root: Path, settings: Settings, limit: int = 4000) -> tuple[int, int]:
    count = 0
    total = 0
    for path in _walk(root, settings):
        try:
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
        if count >= limit:
            break
    return count, total


class AutoScanner:
    """Re-scans the library on a timer so citations stay current."""

    def __init__(self, indexer_getter: Callable[[], Indexer | None], minutes_getter: Callable[[], int]) -> None:
        self._indexer_getter = indexer_getter
        self._minutes_getter = minutes_getter
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="folio-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(30.0):
            minutes = max(1, int(self._minutes_getter() or 0))
            indexer = self._indexer_getter()
            if indexer is None:
                continue
            last = indexer.store.get_meta("last_scan_at", 0) or 0
            if time.time() - float(last) < minutes * 60:
                continue
            try:
                result = indexer.index(None)
                indexer.store.set_meta("last_scan_at", time.time())
                logger.debug("folio background scan: %s", {k: v for k, v in result.items() if k != "state"})
            except Exception:
                logger.debug("folio background scan failed", exc_info=True)
