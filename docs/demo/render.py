"""Render the 90-second Folio demo from the real session transcripts.

Frames are drawn with Pillow and assembled by ffmpeg's concat/rawvideo pipe.
Every quoted line comes from docs/demo/transcript.md, which is the captured
output of a live Hermes session — nothing here is invented for the video.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
FPS = 30
BG = (11, 12, 14)
PANEL = (17, 19, 24)
STROKE = (35, 38, 46)
TEXT = (230, 232, 235)
DIM = (138, 143, 152)
FAINT = (90, 95, 104)
ACCENT = (255, 214, 10)
GREEN = (74, 222, 128)
RED = (248, 113, 113)
BLUE = (125, 180, 255)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

HERE = Path(__file__).resolve().parent
OUT = HERE / "folio-demo.mp4"
FRAMES = HERE / "frames"


@dataclass
class Line:
    text: str = ""
    color: tuple = TEXT
    indent: int = 0
    size: int = 23
    gap: int = 0
    bold: bool = False


@dataclass
class Scene:
    kind: str
    caption: str
    lines: list[Line] = field(default_factory=list)
    seconds: float = 0.0
    per_line: float = 0.55
    image: str = ""


def wrap(text: str, width: int, indent: int = 0, color=TEXT, size=23) -> list[Line]:
    return [
        Line(piece, color, indent, size)
        for piece in textwrap.wrap(text, width=width, initial_indent="", subsequent_indent="")
    ]


def bullet(prefix: str, text: str, width: int = 84, color=TEXT) -> list[Line]:
    out = []
    for index, piece in enumerate(textwrap.wrap(text, width=width)):
        out.append(Line((prefix if index == 0 else " " * len(prefix)) + piece, color))
    return out


def scenes() -> list[Scene]:
    s: list[Scene] = []

    s.append(Scene(
        "title",
        "",
        seconds=6.0,
    ))

    body: list[Line] = []
    body += [Line("you  › ", ACCENT, bold=True), Line("       where does the platform spec cap retries?", TEXT)]
    body.append(Line(""))
    body += [Line("⚡ folio_search  retries capped", FAINT)]
    body += [Line("⚡ folio_open", FAINT)]
    body.append(Line(""))
    body += wrap("Retries are capped at three attempts per request, with exponential backoff "
                 "starting at 250 ms and jitter of 20 percent. The retry budget is reset every "
                 "sixty seconds per client and per route.", 82)
    body += [Line("")]
    body += [Line("Platform Service Specification · §1 Request handling · p. 1", DIM)]
    body.append(Line(""))
    body += [Line("⚡ folio_verify  “Retries are capped at three attempts…”", FAINT)]
    body += [Line("EXACT · p. 1 · characters 97–216", GREEN, bold=True)]
    s.append(Scene("terminal", "the agent cites the page — and checks its own quotation", body, per_line=0.62))

    body = []
    body += wrap("Worth flagging a conflict: meeting-notes.md records that retries move to the edge "
                 "and “the origin stops retrying entirely”, and q3-roadmap slide 2 lists “Move the "
                 "retry budget to the edge” as Q3 work. The spec’s capped-at-origin rule is the "
                 "current written spec, but the meeting notes describe it as being replaced.", 82, color=TEXT)
    s.append(Scene("terminal", "it read the whole library, not just the page it was asked about", body, per_line=0.5))

    body = []
    body += [Line("you  › ", ACCENT, bold=True), Line("       what changed in the spec — and what did it say before?", TEXT)]
    body.append(Line(""))
    body += [Line("⚡ folio_index   ·   1 revised", FAINT)]
    body += [Line("⚡ folio_changes  ·   revision 2 of 2", FAINT)]
    body.append(Line(""))
    body += [Line("Four lines changed; three are substantive:", TEXT)]
    body.append(Line(""))
    body += [Line("  Retries       was  “capped at three attempts per request”", DIM)]
    body += [Line("                now  “capped at five attempts per request”", TEXT)]
    body += [Line("  p99 latency   was  400 ms edge / 620 ms origin", DIM)]
    body += [Line("                now  250 ms edge / 400 ms origin", TEXT)]
    body += [Line("  Audit logs    was  retained 400 days", DIM)]
    body += [Line("                now  retained 180 days", TEXT)]
    s.append(Scene("terminal", "documents are content-hashed — the previous text is still answerable", body, per_line=0.55))

    body = []
    body += [Line("you  › ", ACCENT, bold=True), Line("       is this quotation real: “Liability is unlimited for indirect damages.”", TEXT)]
    body.append(Line(""))
    body += [Line("⚡ folio_verify", FAINT)]
    body.append(Line(""))
    body += wrap("That quotation is not in the master services agreement. Verbatim, section 9 (p. 2) "
                 "says the opposite:", 82, color=RED)
    body.append(Line(""))
    body += wrap("“Liability is limited to the fees paid in the prior twelve months. Neither party is "
                 "liable for indirect or consequential damages.”", 78, color=TEXT)
    body += [Line("")]
    body += wrap("So “Liability is unlimited for indirect damages” is not merely unsupported — it "
                 "inverts both clauses. Don’t cite it.", 82, color=TEXT)
    s.append(Scene("terminal", "a wrong quotation is caught instead of polished", body, per_line=0.55))

    s.append(Scene("page", "the reader draws the match from the PDF's own character boxes", seconds=11.0, image="page_marked.png"))
    s.append(Scene("end", "", seconds=7.0))
    return s


def font(path: str, size: int):
    return ImageFont.truetype(path, size)


def draw_chrome(draw: ImageDraw.ImageDraw, title: str) -> None:
    draw.rounded_rectangle([40, 34, W - 40, H - 78], radius=14, fill=PANEL, outline=STROKE, width=1)
    draw.rectangle([40, 34, W - 40, 74], fill=(22, 24, 30))
    for index, colour in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([62 + index * 22, 48, 74 + index * 22, 60], fill=colour)
    draw.text((150, 47), title, font=font(MONO, 15), fill=DIM)


def render_terminal(scene: Scene, frame_index: int, total_frames: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_chrome(draw, "hermes — folio")
    mono = font(MONO, 23)
    mono_b = font(MONO_B, 23)

    visible = min(len(scene.lines), int(frame_index / (scene.per_line * FPS)) + 1)
    y = 104
    for line in scene.lines[:visible]:
        if line.gap:
            y += line.gap
        if line.text:
            chosen = mono_b if line.bold else mono
            draw.text((84 + line.indent, y), line.text, font=chosen, fill=line.color)
        y += 34
        if y > H - 110:
            break

    return image


def render_title(frame_index: int, total_frames: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    # A thin gold rule and the wordmark.
    fade = min(1.0, frame_index / (0.6 * FPS))
    gold = tuple(int(c * fade) for c in ACCENT)
    draw.rectangle([90, 250, 90 + int(120 * fade), 256], fill=gold)
    draw.text((90, 285), "FOLIO", font=font(SANS, 92), fill=tuple(int(c * fade) for c in TEXT))
    draw.text((94, 400), "every claim, a page", font=font(SANS_R, 34), fill=tuple(int(c * fade) for c in DIM))

    stats = "68 documents  ·  4,666 passages  ·  5.3M characters  ·  one local index"
    draw.text((94, 480), stats, font=font(MONO, 20), fill=FAINT)

    if frame_index > 2.2 * FPS:
        draw.text((94, 540), "no account   no API key   no embeddings   no network calls",
                  font=font(MONO, 19), fill=(70, 74, 82))
    return image


def add_caption(image: Image.Image, text: str) -> Image.Image:
    draw = ImageDraw.Draw(image)
    draw.text((84, H - 52), text, font=font(SANS_R, 18), fill=(120, 125, 134))
    return image


def render_page(scene: Scene) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_chrome(draw, "hermes — folio · reader")
    page = Image.open(HERE / scene.image).convert("RGB")
    # Show the top of the page at a readable size, right of a small caption panel.
    target_w = 470
    ratio = target_w / page.width
    page = page.crop((0, 0, page.width, int(page.height * 0.42)))
    page = page.resize((target_w, int(page.height * ratio)), Image.LANCZOS)
    image.paste(page, (86, 106))

    draw.text((600, 120), "folio_open", font=font(MONO, 22), fill=ACCENT)
    draw.text((600, 160), "p. 1  ·  current revision", font=font(MONO, 19), fill=DIM)
    body = (
        "The box is not a screenshot annotation:\n"
        "it is the PDF's own character geometry,\n"
        "scaled onto the rendered page."
    )
    y = 220
    for line in body.split("\n"):
        draw.text((600, y), line, font=font(SANS_R, 21), fill=TEXT)
        y += 34

    draw.text((600, 360), "the same box the desktop pane draws", font=font(MONO, 17), fill=FAINT)
    return image


def render_end(frame_index: int, total_frames: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([90, 210, 210, 216], fill=ACCENT)
    draw.text((90, 240), "Folio", font=font(SANS, 64), fill=TEXT)
    draw.text((90, 330), "hermes plugins install folio", font=font(MONO, 26), fill=ACCENT)
    draw.text((90, 390), "github.com/okyssta/hermes-folio", font=font(MONO, 20), fill=DIM)
    lines = [
        "seven tools · a desktop page · a bundled skill",
        "PDF · DOCX · PPTX · XLSX · HTML · Markdown · code",
        "39 tests · validate clean · local only",
    ]
    y = 470
    for line in lines:
        draw.text((90, y), line, font=font(SANS_R, 20), fill=(140, 145, 154))
        y += 32
    return image


def build() -> Path:
    FRAMES.mkdir(exist_ok=True)
    for old in FRAMES.glob("*.png"):
        old.unlink()

    plan = scenes()
    index = 0
    manifest = []
    for scene in plan:
        if scene.kind == "title":
            frames = int(scene.seconds * FPS)
        elif scene.kind == "end":
            frames = int(scene.seconds * FPS)
        elif scene.kind == "page":
            frames = int(scene.seconds * FPS)
        else:
            reveal = len(scene.lines) * scene.per_line
            frames = int(min(24.0, reveal + 3.2) * FPS)
        manifest.append((scene.kind, frames))
        for local in range(frames):
            if scene.kind == "title":
                image = render_title(local, frames)
            elif scene.kind == "end":
                image = render_end(local, frames)
            elif scene.kind == "page":
                image = render_page(scene)
            else:
                image = render_terminal(scene, local, frames)
                image = add_caption(image, scene.caption)
            image.save(FRAMES / f"f{index:05d}.png")
            index += 1

    total = index / FPS
    print(f"{index} frames · {total:.1f}s")

    list_file = HERE / "frames.txt"
    with list_file.open("w") as handle:
        handle.write(f"ffconcat version 1.0\nfile '{FRAMES}/f00000.png'\nduration {total}\n")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(FRAMES / "f%05d.png"),
        "-vf", "scale=1280:720,format=yuv420p",
        "-fps_mode", "cfr", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart", str(OUT),
    ]
    subprocess.run(cmd, check=True)
    print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")
    return OUT


if __name__ == "__main__":
    build()
