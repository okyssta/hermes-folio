"""Passage chunking.

A chunk is a slice of one segment's text: ``(start, length, text)``. Because a
chunk never spans two segments, every result carries exactly one locator
("p. 14", "slide 3", "lines 90-140"), and its ``start`` offset maps back into
the page text for exact highlighting.

Splitting prefers paragraph boundaries, then sentence boundaries, and only
hard-cuts a pathological run of text with no punctuation at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Segment

MIN_CHUNK = 160
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[¡¿«\"'])")
_PARAGRAPH = re.compile(r"\n\s*\n+")


@dataclass
class Chunk:
    start: int
    length: int
    text: str


def _paragraphs(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _PARAGRAPH.finditer(text):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    out = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            out.append((start, end))
    return out


def _split_long(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Split an oversized paragraph at sentence boundaries, then hard."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > target * 1.5:
        window_end = min(end, cursor + target)
        window = text[cursor:window_end]
        cut = -1
        for match in _SENTENCE.finditer(window):
            if match.start() > target * 0.45:
                cut = match.start()
        if cut <= 0:
            space = window.rfind(" ")
            cut = space if space > target * 0.45 else target
        pieces.append((cursor, cursor + cut))
        cursor = cursor + cut
        while cursor < end and text[cursor].isspace():
            cursor += 1
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def chunk_segment(segment: Segment, target: int = 1200) -> list[Chunk]:
    text = segment.text
    if not text:
        return []
    if len(text) <= target * 1.3:
        return [Chunk(0, len(text), text)]

    spans: list[tuple[int, int]] = []
    for start, end in _paragraphs(text):
        if end - start > target * 1.5:
            spans.extend(_split_long(text, start, end, target))
        else:
            spans.append((start, end))

    chunks: list[Chunk] = []
    buf_start = buf_end = 0
    for start, end in spans:
        if not buf_start and not buf_end:
            buf_start, buf_end = start, end
            continue
        if end - buf_start > target:
            body = text[buf_start:buf_end]
            if len(body) >= MIN_CHUNK or not chunks:
                chunks.append(Chunk(buf_start, buf_end - buf_start, body))
            buf_start, buf_end = start, end
        else:
            buf_end = end
    if buf_start or buf_end:
        body = text[buf_start:buf_end]
        if body.strip():
            if chunks and len(body) < MIN_CHUNK:
                # Fold a thin tail into the previous chunk instead of emitting a stub.
                prev = chunks[-1]
                merged = text[prev.start:buf_end]
                chunks[-1] = Chunk(prev.start, buf_end - prev.start, merged)
            else:
                chunks.append(Chunk(buf_start, buf_end - buf_start, body))
    return chunks or [Chunk(0, len(text), text)]
