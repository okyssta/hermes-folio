"""SQLite storage for the library.

Three tables carry the whole product: ``documents`` (one row per file),
``versions`` (one row per distinct content hash, so a revised file keeps its old
text) and ``chunks`` (the passages, each with the locator it came from and the
offset it occupies inside that page). ``chunks_fts`` is an FTS5 index over the
passage text; its rowid mirrors ``chunks.id`` so a hit is a join, not a lookup.

Connections are thread-local: tools run on the tool threadpool while the desktop
backend may read on another, and SQLite in WAL mode handles that cleanly as long
as no connection is shared across threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SENT_OPEN = "\x01"
SENT_CLOSE = "\x02"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS roots (
    path TEXT PRIMARY KEY,
    added_at REAL NOT NULL,
    last_scan REAL,
    doc_count INTEGER DEFAULT 0,
    last_note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    root TEXT NOT NULL DEFAULT '',
    ext TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    units_total INTEGER NOT NULL DEFAULT 0,
    units_label TEXT NOT NULL DEFAULT 'page',
    chars INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL DEFAULT 0,
    last_indexed REAL NOT NULL DEFAULT 0,
    version_count INTEGER NOT NULL DEFAULT 1,
    current_version INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    captured_at REAL NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0,
    units_total INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(doc_id, sha256)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL,
    ord INTEGER NOT NULL DEFAULT 0,
    locator TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    page INTEGER,
    start INTEGER NOT NULL DEFAULT 0,
    length INTEGER NOT NULL DEFAULT 0,
    current INTEGER NOT NULL DEFAULT 1,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, current);
CREATE INDEX IF NOT EXISTS idx_chunks_version ON chunks(version_id);
CREATE INDEX IF NOT EXISTS idx_versions_doc ON versions(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    tokenize='porter unicode61 remove_diacritics 2',
    prefix='2 3'
);
"""


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    path: str
    title: str
    locator: str
    section: str
    start: int
    length: int
    snippet: str
    score: float
    kind: str
    author: str
    year: str
    captured_at: float
    version_id: int


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._ready = False

    # ------------------------------------------------------------- connection
    def conn(self) -> sqlite3.Connection:
        db = getattr(self._local, "conn", None)
        if db is None:
            db = sqlite3.connect(str(self.path), timeout=20.0, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=20000")
            db.execute("PRAGMA foreign_keys=ON")
            self._local.conn = db
        if not self._ready:
            with self._init_lock:
                if not self._ready:
                    db.executescript(_SCHEMA)
                    self._ready = True
        return db

    def close(self) -> None:
        db = getattr(self._local, "conn", None)
        if db is not None:
            try:
                db.close()
            finally:
                self._local.conn = None

    # ------------------------------------------------------------------- meta
    def set_meta(self, key: str, value: Any) -> None:
        self.conn().execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    # ------------------------------------------------------------------ roots
    def add_root(self, path: str) -> None:
        self.conn().execute(
            "INSERT INTO roots(path, added_at) VALUES(?,?) ON CONFLICT(path) DO NOTHING",
            (path, time.time()),
        )

    def remove_root(self, path: str) -> None:
        self.conn().execute("DELETE FROM roots WHERE path=?", (path,))

    def roots(self) -> list[sqlite3.Row]:
        return list(self.conn().execute("SELECT * FROM roots ORDER BY path"))

    def touch_root(self, path: str, doc_count: int, note: str = "") -> None:
        self.conn().execute(
            "UPDATE roots SET last_scan=?, doc_count=?, last_note=? WHERE path=?",
            (time.time(), doc_count, note[:200], path),
        )

    # -------------------------------------------------------------- documents
    def find_document(self, path: str) -> sqlite3.Row | None:
        return self.conn().execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()

    def get_document(self, doc_id: int) -> sqlite3.Row | None:
        return self.conn().execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def resolve_document(self, ref: str | int) -> sqlite3.Row | None:
        """Find a document by id, absolute path, relative suffix, or title."""
        db = self.conn()
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            row = self.get_document(int(ref))
            if row is not None:
                return row
        ref = str(ref)
        row = db.execute("SELECT * FROM documents WHERE path=?", (ref,)).fetchone()
        if row is not None:
            return row
        row = db.execute(
            "SELECT * FROM documents WHERE path LIKE ? ORDER BY length(path) LIMIT 1",
            (f"%{ref}%",),
        ).fetchone()
        if row is not None:
            return row
        return db.execute(
            "SELECT * FROM documents WHERE title LIKE ? ORDER BY length(title) LIMIT 1",
            (f"%{ref}%",),
        ).fetchone()

    def upsert_document(
        self,
        *,
        path: str,
        root: str,
        ext: str,
        kind: str,
        title: str,
        author: str,
        year: str,
        sha256: str,
        size: int,
        mtime: float,
        units_total: int,
        units_label: str,
        chars: int,
        note: str,
    ) -> tuple[int, bool, bool]:
        """Insert or update one document.

        Returns ``(doc_id, changed, is_new)``. ``changed`` is True when the content
        hash differs from the last indexed revision, which is what triggers a new
        version row; an unchanged file only refreshes its mtime and scan stamp.
        """
        db = self.conn()
        now = time.time()
        row = db.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()
        if row is None:
            cursor = db.execute(
                """INSERT INTO documents(path, root, ext, kind, title, author, year, sha256, size,
                        mtime, units_total, units_label, chars, note, first_seen, last_indexed,
                        version_count, current_version, missing)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,0)""",
                (path, root, ext, kind, title, author, year, sha256, size, mtime, units_total,
                 units_label, chars, note, now, now),
            )
            doc_id = int(cursor.lastrowid)
            return doc_id, True, True

        doc_id = int(row["id"])
        if row["sha256"] == sha256 and not row["missing"]:
            db.execute(
                "UPDATE documents SET size=?, mtime=?, last_indexed=? WHERE id=?",
                (size, mtime, now, doc_id),
            )
            return doc_id, False, False

        db.execute(
            """UPDATE documents SET root=?, ext=?, kind=?, title=?, author=?, year=?, sha256=?,
                   size=?, mtime=?, units_total=?, units_label=?, chars=?, note=?, last_indexed=?,
                   missing=0 WHERE id=?""",
            (root, ext, kind, title, author, year, sha256, size, mtime, units_total, units_label,
             chars, note, now, doc_id),
        )
        return doc_id, True, False

    def begin_version(self, doc_id: int, sha256: str, chars: int, units_total: int, note: str) -> int:
        """Create (or reuse) the version row for this content hash; supersede older chunks."""
        db = self.conn()
        row = db.execute(
            "SELECT id FROM versions WHERE doc_id=? AND sha256=?", (doc_id, sha256)
        ).fetchone()
        if row is not None:
            version_id = int(row["id"])
            db.execute(
                "UPDATE versions SET captured_at=?, chars=?, units_total=?, note=? WHERE id=?",
                (time.time(), chars, units_total, note, version_id),
            )
        else:
            cursor = db.execute(
                "INSERT INTO versions(doc_id, sha256, captured_at, chars, units_total, note) VALUES(?,?,?,?,?,?)",
                (doc_id, sha256, time.time(), chars, units_total, note),
            )
            version_id = int(cursor.lastrowid)

        db.execute("UPDATE chunks SET current=0 WHERE doc_id=? AND version_id<>?", (doc_id, version_id))
        db.execute(
            "UPDATE documents SET current_version=?, version_count=(SELECT COUNT(*) FROM versions WHERE doc_id=?) WHERE id=?",
            (version_id, doc_id, doc_id),
        )
        return version_id

    def mark_missing(self, doc_id: int) -> None:
        self.conn().execute("UPDATE documents SET missing=1 WHERE id=?", (doc_id,))

    def update_metadata(self, doc_id: int, *, title: str, author: str, year: str,
                        kind: str, units_total: int, units_label: str, chars: int,
                        note: str) -> None:
        """Refresh extracted metadata for unchanged content (a forced re-scan)."""
        self.conn().execute(
            """UPDATE documents SET title=?, author=?, year=?, kind=?, units_total=?,
                   units_label=?, chars=?, note=? WHERE id=?""",
            (title, author, year, kind, units_total, units_label, chars, note, doc_id),
        )

    def known_paths(self, root: str) -> dict[str, tuple[int, str, float, int]]:
        rows = self.conn().execute(
            "SELECT id, path, sha256, mtime, size, missing FROM documents WHERE root=?", (root,)
        )
        return {r["path"]: (int(r["id"]), r["sha256"], r["mtime"], int(r["missing"])) for r in rows}

    # ----------------------------------------------------------------- chunks
    def replace_chunks(self, doc_id: int, version_id: int, chunks: list[dict[str, Any]]) -> int:
        db = self.conn()
        db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE version_id=?)",
                   (version_id,))
        db.execute("DELETE FROM chunks WHERE version_id=?", (version_id,))
        kept = 0
        for chunk in chunks:
            cursor = db.execute(
                """INSERT INTO chunks(doc_id, version_id, ord, locator, section, page, start, length,
                        current, text)
                   VALUES(?,?,?,?,?,?,?,?,1,?)""",
                (doc_id, version_id, chunk.get("ord", 0), chunk.get("locator", ""),
                 chunk.get("section", ""), chunk.get("page"), chunk.get("start", 0),
                 chunk.get("length", len(chunk["text"])), chunk["text"]),
            )
            db.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)",
                       (int(cursor.lastrowid), chunk["text"]))
            kept += 1
        return kept

    def prune_versions(self, doc_id: int, keep: int) -> int:
        if keep < 1:
            keep = 1
        db = self.conn()
        rows = db.execute(
            "SELECT id FROM versions WHERE doc_id=? ORDER BY captured_at DESC, id DESC", (doc_id,)
        ).fetchall()
        removed = 0
        for row in rows[keep:]:
            version_id = int(row["id"])
            db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE version_id=?)",
                       (version_id,))
            db.execute("DELETE FROM chunks WHERE version_id=?", (version_id,))
            db.execute("DELETE FROM versions WHERE id=?", (version_id,))
            removed += 1
        if removed:
            db.execute(
                "UPDATE documents SET version_count=(SELECT COUNT(*) FROM versions WHERE doc_id=?) WHERE id=?",
                (doc_id, doc_id),
            )
        return removed

    # ----------------------------------------------------------------- search
    def search(self, match: str, limit: int = 8, include_missing: bool = False) -> list[Hit]:
        sql = """
        SELECT c.id AS chunk_id, c.doc_id, c.locator, c.section, c.start, c.length,
               c.version_id, d.path, d.title, d.kind, d.author, d.year,
               snippet(chunks_fts, 0, char(1), char(2), ' … ', 16) AS snip,
               bm25(chunks_fts) AS score
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN documents d ON d.id = c.doc_id
        WHERE chunks_fts MATCH ? AND c.current = 1
        """
        if not include_missing:
            sql += " AND d.missing = 0"
        sql += " ORDER BY score LIMIT ?"
        rows = self.conn().execute(sql, (match, limit)).fetchall()
        hits: list[Hit] = []
        for row in rows:
            captured = self.conn().execute(
                "SELECT captured_at FROM versions WHERE id=?", (row["version_id"],)
            ).fetchone()
            hits.append(
                Hit(
                    chunk_id=int(row["chunk_id"]),
                    doc_id=int(row["doc_id"]),
                    path=row["path"],
                    title=row["title"],
                    locator=row["locator"],
                    section=row["section"],
                    start=int(row["start"]),
                    length=int(row["length"]),
                    snippet=row["snip"] or "",
                    score=float(row["score"]),
                    kind=row["kind"],
                    author=row["author"],
                    year=row["year"],
                    captured_at=float(captured["captured_at"]) if captured else 0.0,
                    version_id=int(row["version_id"]),
                )
            )
        return hits

    def locator_text(self, doc_id: int, locator: str, version_id: int | None = None) -> str:
        db = self.conn()
        if version_id is None:
            row = db.execute("SELECT current_version FROM documents WHERE id=?", (doc_id,)).fetchone()
            version_id = int(row["current_version"]) if row and row["current_version"] else None
        rows = db.execute(
            "SELECT text FROM chunks WHERE doc_id=? AND locator=? AND version_id=? ORDER BY start",
            (doc_id, locator, version_id),
        ).fetchall()
        return "\n\n".join(r["text"] for r in rows)

    def version_text(self, doc_id: int, version_id: int) -> str:
        rows = self.conn().execute(
            "SELECT text FROM chunks WHERE doc_id=? AND version_id=? ORDER BY page, ord, start",
            (doc_id, version_id),
        ).fetchall()
        return "\n\n".join(r["text"] for r in rows)

    def sample_text(self, doc_id: int, limit_chars: int = 6000) -> str:
        row = self.conn().execute("SELECT current_version FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row or not row["current_version"]:
            return ""
        parts = []
        total = 0
        for chunk in self.conn().execute(
            "SELECT text FROM chunks WHERE doc_id=? AND version_id=? ORDER BY page, ord, start",
            (doc_id, int(row["current_version"])),
        ):
            parts.append(chunk["text"])
            total += len(chunk["text"])
            if total >= limit_chars:
                break
        return "\n\n".join(parts)[:limit_chars]

    # ---------------------------------------------------------------- reports
    def stats(self) -> dict[str, Any]:
        db = self.conn()
        docs = db.execute("SELECT COUNT(*) c FROM documents WHERE missing=0").fetchone()["c"]
        missing = db.execute("SELECT COUNT(*) c FROM documents WHERE missing=1").fetchone()["c"]
        chunks = db.execute("SELECT COUNT(*) c FROM chunks WHERE current=1").fetchone()["c"]
        chars = db.execute("SELECT COALESCE(SUM(chars),0) c FROM documents WHERE missing=0").fetchone()["c"]
        versions = db.execute("SELECT COUNT(*) c FROM versions").fetchone()["c"]
        kinds = {
            row["kind"]: row["c"]
            for row in db.execute(
                "SELECT kind, COUNT(*) c FROM documents WHERE missing=0 GROUP BY kind ORDER BY c DESC"
            )
        }
        last = db.execute("SELECT MAX(last_indexed) m FROM documents").fetchone()["m"]
        return {
            "documents": docs,
            "missing": missing,
            "chunks": chunks,
            "chars": chars,
            "versions": versions,
            "kinds": kinds,
            "last_indexed": last or 0.0,
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "roots": [dict(r) for r in self.roots()],
        }

    def changes_since(self, since: float, limit: int = 40) -> list[dict[str, Any]]:
        db = self.conn()
        rows = db.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM versions v2 WHERE v2.doc_id=d.id AND v2.captured_at>=?) AS new_versions
               FROM documents d
               WHERE d.last_indexed>=? OR EXISTS (SELECT 1 FROM versions v WHERE v.doc_id=d.id AND v.captured_at>=?)
               ORDER BY d.last_indexed DESC LIMIT ?""",
            (since, since, since, limit),
        ).fetchall()
        out = []
        for row in rows:
            versions = db.execute(
                "SELECT id, captured_at, chars FROM versions WHERE doc_id=? ORDER BY captured_at DESC LIMIT 2",
                (row["id"],),
            ).fetchall()
            out.append(
                {
                    "doc_id": int(row["id"]),
                    "path": row["path"],
                    "title": row["title"],
                    "missing": int(row["missing"]),
                    "last_indexed": float(row["last_indexed"] or 0),
                    "version_count": int(row["version_count"] or 0),
                    "new_versions": int(row["new_versions"] or 0),
                    "latest_version": int(versions[0]["id"]) if versions else 0,
                    "previous_version": int(versions[1]["id"]) if len(versions) > 1 else 0,
                    "latest_at": float(versions[0]["captured_at"]) if versions else 0.0,
                    "previous_at": float(versions[1]["captured_at"]) if len(versions) > 1 else 0.0,
                }
            )
        return out

    def list_documents(self, limit: int = 50, order: str = "recent") -> list[sqlite3.Row]:
        clause = {
            "recent": "ORDER BY last_indexed DESC",
            "title": "ORDER BY lower(title) ASC",
            "size": "ORDER BY chars DESC",
        }.get(order, "ORDER BY last_indexed DESC")
        return list(
            self.conn().execute(
                f"SELECT * FROM documents WHERE missing=0 {clause} LIMIT ?", (limit,)
            )
        )

    # ----------------------------------------------------------------- delete
    def forget(self, doc_ids: Iterable[int] | None = None) -> int:
        db = self.conn()
        if doc_ids is None:
            db.execute("DELETE FROM chunks_fts")
            db.execute("DELETE FROM chunks")
            db.execute("DELETE FROM versions")
            count = db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            db.execute("DELETE FROM documents")
            db.execute("UPDATE roots SET doc_count=0, last_scan=NULL")
            return int(count)

        ids = [int(i) for i in doc_ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        db.execute(f"DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id IN ({marks}))", ids)
        db.execute(f"DELETE FROM chunks WHERE doc_id IN ({marks})", ids)
        db.execute(f"DELETE FROM versions WHERE doc_id IN ({marks})", ids)
        db.execute(f"DELETE FROM documents WHERE id IN ({marks})", ids)
        return len(ids)
