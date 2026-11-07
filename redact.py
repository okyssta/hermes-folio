"""Redaction applied at the storage boundary.

Folio keeps document text in a local index, so secrets that happen to live in a
document should not be duplicated into it in the clear. Patterns are deliberately
narrow (a token shape, not a word that looks like one) so normal prose, version
numbers and hashes survive intact.
"""

from __future__ import annotations

import re

_MARK = "[redacted]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Private key blocks (PEM) — replace the body, keep the markers.
    (
        re.compile(
            r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)(?:.|\n)*?(-----END [A-Z0-9 ]*PRIVATE KEY-----)"
        ),
        r"\1 " + _MARK + r" \2",
    ),
    # Well-known token shapes.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _MARK),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), _MARK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _MARK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), _MARK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), _MARK),
    (re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{10,}\b"), _MARK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), _MARK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), _MARK),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), _MARK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), _MARK),
    # Authorization headers.
    (
        re.compile(r"(?i)\b(authorization|bearer|token|api[_-]?key|secret|password|passwd|pwd)"
                   r"(\s*[:=]\s*)(?:bearer\s+)?[\"']?([^\s\"',;]{6,})[\"']?"),
        lambda m: f"{m.group(1)}{m.group(2)}{_MARK}",
    ),
    # Credentials embedded in a URL: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+:)([^@\s/]{3,})(@)"), r"\1" + _MARK + r"\3"),
    # Card-like digit runs (13-19 digits, optional separators).
    (
        re.compile(r"\b(?:\d[ -]?){13,18}\d\b"),
        lambda m: _MARK if _luhn_ok(m.group(0)) else m.group(0),
    ),
)


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def redact(text: str) -> str:
    """Return *text* with recognisable secrets replaced by ``[redacted]``."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        try:
            out = pattern.sub(replacement, out)
        except Exception:
            continue
    return out
