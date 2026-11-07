"""Citation formatting.

Folio knows a title, an author, a year and a file — not a venue or a DOI — so the
formats it can honestly produce are the ones that need exactly those fields.
BibTeX entries are emitted as ``@misc`` for the same reason: inventing
``@article`` metadata a PDF does not carry would make the bibliography lie.
"""

from __future__ import annotations

import re
from typing import Any

STYLES = ("apa", "mla", "chicago", "bibtex", "plain")


def _surname(author: str) -> str:
    author = (author or "").strip()
    if not author:
        return ""
    first = re.split(r"[;,]", author)[0].strip()
    if not first:
        return ""
    if "," in author:
        return first
    parts = first.split()
    return parts[-1] if parts else first


def _bare(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip()).rstrip(".")


def _key(doc: dict[str, Any]) -> str:
    surname = re.sub(r"[^A-Za-z]", "", _surname(doc.get("author", ""))) or "doc"
    year = re.sub(r"[^0-9]", "", str(doc.get("year", "")))[:4] or "nd"
    word = ""
    for candidate in re.findall(r"[A-Za-z]{3,}", doc.get("title", "")):
        if candidate.lower() not in {"the", "and", "for", "with", "from", "into"}:
            word = candidate.lower()
            break
    return f"{surname.lower()}{year}{word}"


def format_citation(doc: dict[str, Any], style: str = "apa", locator: str = "") -> str:
    style = (style or "apa").lower()
    title = _bare(doc.get("title", "")) or "(untitled)"
    author = (doc.get("author") or "").strip()
    year = (doc.get("year") or "").strip() or "n.d."
    path = doc.get("path", "")
    where = f", {locator}" if locator else ""

    if style == "bibtex":
        fields = [f"  title = {{{title}}}"]
        if author:
            fields.append(f"  author = {{{author}}}")
        if doc.get("year"):
            fields.append(f"  year = {{{doc['year']}}}")
        fields.append(f"  file = {{{path}}}")
        fields.append("  note = {Local document indexed by Hermes Folio}")
        return "@misc{" + _key(doc) + ",\n" + ",\n".join(fields) + "\n}"

    if style == "mla":
        lead = f"{author}. " if author else ""
        return f'{lead}"{title}." {year}{where}. Local file: {path}'

    if style == "chicago":
        lead = f"{author}. " if author else ""
        return f'{lead}"{title}." {year}{where}. Local file: {path}'

    if style == "plain":
        lead = f"{author} ({year}). " if author else f"{year}. "
        return f"{lead}{title}{where}. {path}"

    # APA
    if author:
        lead = f"{author} ({year}). "
    else:
        lead = f"{title} ({year}). "
    body = title if author else ""
    tail = f" {body}." if body else ""
    return f"{lead}{title}.{where} Local file: {path}".replace("..", ".")


def format_citations(docs: list[dict[str, Any]], style: str, locator: str = "") -> str:
    return "\n\n".join(format_citation(doc, style, locator) for doc in docs)
