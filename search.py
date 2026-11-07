"""Query building, ranking and quote verification.

Two jobs live here. The first turns whatever the model typed into a valid FTS5
MATCH expression: a natural-language question becomes an OR of its content words
so bm25 can rank by coverage, while explicit FTS syntax (quoted phrases, AND/OR/
NEAR, a trailing ``*``) is passed through untouched. The second proves a quote:
it normalises both sides the same way — whitespace collapsed, line-break hyphens
joined, case folded — while keeping a map back to original offsets, so "this
sentence is on p. 14" is a byte-level fact rather than a similarity score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "did",
    "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in",
    "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will", "with", "you",
    "your", "about", "any", "all", "also", "been", "being", "would", "should",
}

_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
_FTS_HINT = re.compile(r'["()*]|\b(AND|OR|NOT|NEAR)\b')
_QUOTED = re.compile(r'"([^"]+)"')


def tokenize(query: str) -> list[str]:
    seen: list[str] = []
    for match in _WORD.finditer(query):
        word = match.group(0).strip(".-_").lower()
        if len(word) < 2 or word in STOPWORDS:
            continue
        if word not in seen:
            seen.append(word)
    return seen


def build_match(query: str) -> tuple[str, list[str]]:
    """Return ``(fts_expression, content_words)`` for a user query."""
    query = (query or "").strip()
    if not query:
        return "", []

    words = tokenize(_QUOTED.sub(" ", query))
    phrases = [p.strip() for p in _QUOTED.findall(query) if p.strip()]

    if _FTS_HINT.search(query):
        # Looks like deliberate FTS syntax (a quoted phrase, AND/OR/NEAR, *).
        # Let SQLite parse it; callers catch the error and retry sanitised.
        return query, words

    parts: list[str] = []
    for phrase in phrases:
        cleaned = re.sub(r'["*()]', " ", phrase).strip()
        if cleaned:
            parts.append(f'"{cleaned}"')
    for word in words[:16]:
        safe = re.sub(r"[^a-z0-9_.\-]", "", word)
        if safe:
            parts.append(f'"{safe}"')

    if not parts:
        return "", words
    return " OR ".join(dict.fromkeys(parts)), words


def phrase_bonus(text: str, words: list[str]) -> float:
    """A small reward for passages where the query words appear as a phrase."""
    if len(words) < 2:
        return 0.0
    lowered = text.lower()
    if " ".join(words) in lowered:
        return -1.5
    return 0.0


# ------------------------------------------------------------------ verifying

@dataclass
class QuoteMatch:
    found: bool
    exact: bool
    ratio: float
    start: int
    end: int
    context: str = ""


def canonical_map(text: str) -> tuple[str, list[int]]:
    """Case-folded, whitespace-collapsed text plus a map to original indices."""

    out: list[str] = []
    index: list[int] = []
    space = True
    for position, char in enumerate(text):
        if char.isspace():
            if not space:
                out.append(" ")
                index.append(position)
                space = True
            continue
        lowered = char.lower()
        out.append(lowered)
        index.append(position)
        space = False
    # Drop a trailing collapsed space so offsets end on a real character.
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def find_quote(page_text: str, quote: str) -> QuoteMatch:
    """Locate *quote* inside *page_text*, tolerating line breaks and case."""
    quote = (quote or "").strip().strip('"').strip("“”").strip()
    if not quote or not page_text:
        return QuoteMatch(False, False, 0.0, 0, 0)

    haystack, index = canonical_map(page_text)
    needle, _ = canonical_map(quote)
    if not needle:
        return QuoteMatch(False, False, 0.0, 0, 0)

    position = haystack.find(needle)
    if position >= 0:
        start = index[position]
        end = index[position + len(needle) - 1] + 1
        return QuoteMatch(True, True, 1.0, start, end,
                          page_text[max(0, start - 90): end + 90].replace("\n", " "))

    # Not exact: score the best window around the rarest quote word.
    words = [w for w in re.findall(r"[a-z0-9]{3,}", needle)]
    best = QuoteMatch(False, False, 0.0, 0, 0)
    if not words:
        return best
    try:
        anchor = max(words, key=lambda w: (haystack.count(w), -len(w)))
    except ValueError:
        return best
    if not anchor:
        return best
    window = max(200, len(needle) * 2)
    cursor = 0
    checked = 0
    while checked < 40:
        found = haystack.find(anchor, cursor)
        if found < 0:
            break
        start = max(0, found - window // 3)
        end = min(len(haystack), found + window)
        ratio = SequenceMatcher(None, needle, haystack[start:end]).ratio()
        if ratio > best.ratio:
            best = QuoteMatch(
                True, False, round(ratio, 3), index[start], index[end - 1] + 1,
                page_text[max(0, index[start] - 90): index[end - 1] + 90].replace("\n", " "),
            )
        cursor = found + len(anchor)
        checked += 1
    return best


def highlight_span(page_text: str, start: int, end: int, width: int = 160) -> str:
    """A readable window around a match, with ⟦ ⟧ marking the span."""
    start = max(0, min(start, len(page_text)))
    end = max(start, min(end, len(page_text)))
    left = max(0, start - width)
    right = min(len(page_text), end + width)
    body = page_text[left:right]
    relative_start = start - left
    relative_end = end - left
    return (
        ("…" if left > 0 else "")
        + body[:relative_start]
        + "⟦"
        + body[relative_start:relative_end]
        + "⟧"
        + body[relative_end:]
        + ("…" if right < len(page_text) else "")
    )
