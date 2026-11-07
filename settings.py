"""Settings resolution for Folio.

Every key mirrors a ``config_schema`` entry in ``plugin.yaml`` one-for-one; a
test asserts the two sets stay identical. Values are coerced defensively — a
typo in ``config.yaml`` must never stop a query from being answered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLUGIN_ID = "folio"

SCHEMA_KEYS = (
    "enabled",
    "roots",
    "include_extensions",
    "exclude_dirs",
    "max_file_mb",
    "max_text_chars",
    "chunk_chars",
    "keep_versions",
    "max_versions",
    "redact",
    "auto_scan_minutes",
)

DEFAULT_EXTENSIONS = tuple(
    [
        ".pdf", ".docx", ".pptx", ".xlsx", ".md", ".markdown", ".txt", ".rst",
        ".html", ".htm", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx", ".go",
        ".rs", ".java", ".c", ".h", ".cpp", ".rb", ".sh", ".sql", ".yaml",
        ".yml", ".json", ".toml",
    ]
)

DEFAULT_EXCLUDES = (
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".cache",
    "dist", "build", ".next", "target", "Library", ".Trash",
)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off", ""}:
            return False
    return default


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any, default: tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        items = [p.strip() for p in value.replace(",", "\n").splitlines()]
        return [p for p in items if p] or list(default)
    if isinstance(value, (list, tuple, set)):
        items = [str(p).strip() for p in value if str(p).strip()]
        return items or list(default)
    return list(default)


def expand(path: str) -> Path:
    """``~``-expanding absolute path, resolved without requiring existence."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).absolute()


@dataclass
class Settings:
    enabled: bool = True
    roots: list[str] = field(default_factory=list)
    include_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    exclude_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_mb: float = 80.0
    max_text_chars: int = 400_000
    chunk_chars: int = 1200
    keep_versions: bool = True
    max_versions: int = 20
    redact: bool = True
    auto_scan_minutes: int = 15

    @classmethod
    def from_ctx(cls, ctx: Any, hermes_home: Path | None = None) -> "Settings":
        try:
            raw = ctx.get_config() or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        exts = []
        for ext in _as_list(raw.get("include_extensions"), DEFAULT_EXTENSIONS):
            ext = ext.strip().lower()
            if not ext.startswith("."):
                ext = "." + ext
            exts.append(ext)

        roots = []
        for entry in _as_list(raw.get("roots"), ()):
            roots.append(str(expand(entry)))

        return cls(
            enabled=_as_bool(raw.get("enabled"), True),
            roots=roots,
            include_extensions=exts or list(DEFAULT_EXTENSIONS),
            exclude_dirs=_as_list(raw.get("exclude_dirs"), DEFAULT_EXCLUDES),
            max_file_mb=_as_float(raw.get("max_file_mb"), 80.0, 0.1, 4096.0),
            max_text_chars=_as_int(raw.get("max_text_chars"), 400_000, 20_000, 4_000_000),
            chunk_chars=_as_int(raw.get("chunk_chars"), 1200, 400, 4000),
            keep_versions=_as_bool(raw.get("keep_versions"), True),
            max_versions=_as_int(raw.get("max_versions"), 20, 1, 200),
            redact=_as_bool(raw.get("redact"), True),
            auto_scan_minutes=_as_int(raw.get("auto_scan_minutes"), 15, 0, 2880),
        )

    def allows(self, path: Path) -> bool:
        return path.suffix.lower() in set(self.include_extensions)
