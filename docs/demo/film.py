"""Render the Folio demo film.

Every visual comes from the working product: thumbnails of the indexed PDFs, a
screenshot of the running Desktop app, the reader's own page render, and text
captured from a live session. Frames are drawn with Pillow and piped to ffmpeg.
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1600, 900
FPS = 30
DURATION = 80.0

BG = (10, 11, 13)
INK = (244, 245, 247)
DIM = (140, 146, 153)
FAINT = (86, 91, 98)
GOLD = (227, 176, 75)
GOLD_DIM = (150, 116, 50)
RULE = (34, 36, 41)
PANEL = (15, 16, 19)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
SANS = FONT_DIR / "DejaVuSans.ttf"
SANS_B = FONT_DIR / "DejaVuSans-Bold.ttf"
MONO = FONT_DIR / "DejaVuSansMono.ttf"
MONO_B = FONT_DIR / "DejaVuSansMono-Bold.ttf"

SHEET = Image.open("/tmp/sheet.png").convert("RGB")
APP = Image.open("/tmp/appshot.png").convert("RGB")
PAGE = Image.open("/root/folio/docs/demo/page_marked.png").convert("RGB")

_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    key = (str(path), size)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(str(path), size)
    return _cache[key]


def ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 3 * x**2 - 2 * x**3


def ramp(t: float, start: float, dur: float) -> float:
    return max(0.0, min(1.0, (t - start) / dur)) if dur else 1.0


def tracked(draw: ImageDraw.ImageDraw, xy, text, f, fill, tracking=0.0, anchor_left=True):
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=f, fill=fill)
        x += draw.textlength(char, font=f) + tracking
    return x


def tracked_width(draw: ImageDraw.ImageDraw, text, f, tracking=0.0) -> float:
    return sum(draw.textlength(c, font=f) for c in text) + tracking * max(0, len(text) - 1)


def wrap(draw, text, f, width) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=f) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def with_alpha(layer: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    return out


def rounded_card(image: Image.Image, radius: int = 16) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def drop_shadow(card: Image.Image, blur: int = 34, spread: int = 18, alpha: int = 170) -> Image.Image:
    pad = blur * 2
    shadow = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2), (0, 0, 0, 0))
    plate = Image.new("RGBA", card.size, (0, 0, 0, alpha))
    plate.putalpha(card.getchannel("A"))
    shadow.alpha_composite(plate, (pad + spread // 2, pad + spread))
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def paste_scaled(base: Image.Image, card: Image.Image, center, scale=1.0, alpha=1.0, shadow=True):
    w = max(1, int(card.width * scale))
    h = max(1, int(card.height * scale))
    scaled = card.resize((w, h), Image.LANCZOS)
    x = int(center[0] - w / 2)
    y = int(center[1] - h / 2)
    if shadow:
        sh = drop_shadow(scaled)
        base.alpha_composite(with_alpha(sh, alpha), (x - sh.width // 2 + w // 2, y - sh.height // 2 + h // 2))
    base.alpha_composite(with_alpha(scaled, alpha), (x, y))


# ---------------------------------------------------------------- backgrounds

def build_backsheet() -> Image.Image:
    """The page wall, kept readable so the product's raw material is visible."""
    pad_x, pad_y = 200, 140
    cover = SHEET.resize((W + pad_x, int(SHEET.height * (W + pad_x) / SHEET.width)), Image.LANCZOS)
    if cover.height < H + pad_y:
        cover = cover.resize((int(cover.width * (H + pad_y) / cover.height), H + pad_y), Image.LANCZOS)
    image = cover.filter(ImageFilter.GaussianBlur(4))
    image = image.point(lambda v: int(v * 0.72))
    return image.convert("RGBA")


SHEET_BIG = build_backsheet()
VIGNETTE = Image.new("L", (W, H), 0)
_vd = ImageDraw.Draw(VIGNETTE)
for _y in range(H):
    _depth = abs(_y - H / 2) / (H / 2)
    _vd.line((0, _y, W, _y), fill=int(72 + 170 * _depth**1.6))


def backdrop(t: float = 0.0, drift: float = 1.0) -> Image.Image:
    dx = (SHEET_BIG.width - W) // 2 + int(18 * math.sin(t * 0.16))
    dy = (SHEET_BIG.height - H) // 2 + int(drift * min(70.0, 9.0 * t / 7.0))
    crop = SHEET_BIG.crop((dx, dy, dx + W, dy + H))
    dark = Image.new("RGBA", (W, H), (8, 9, 11, 255))
    dark.putalpha(VIGNETTE)
    return Image.alpha_composite(crop, dark)


FLAT = Image.new("RGBA", (W, H), BG + (255,))


def glow(center, radius=520, color=(227, 176, 75), strength=26) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 26
    for i in range(steps, 0, -1):
        r = radius * i / steps
        a = int(strength * (1 - i / steps) ** 1.6)
        d.ellipse((center[0] - r, center[1] - r * 0.72, center[0] + r, center[1] + r * 0.72),
                  fill=color + (a,))
    return layer.filter(ImageFilter.GaussianBlur(40))


# ------------------------------------------------------------------- scenes

@dataclass
class Scene:
    start: float
    end: float
    draw: callable
    fade_in: float = 0.55
    fade_out: float = 0.55


def title_scene(t: float) -> Image.Image:
    frame = backdrop(t, drift=0.0)
    d = ImageDraw.Draw(frame)

    appear = ease_out(ramp(t, 0.1, 1.1))
    lift = int((1 - appear) * 18)
    cy = 470 + lift

    caps_f = font(SANS_B, 15)
    label = "A LOCAL DOCUMENT LIBRARY FOR HERMES AGENT"
    lw = tracked_width(d, label, caps_f, 5.6)
    tracked(d, ((W - lw) / 2, cy - 150), label, caps_f, GOLD + (int(215 * appear),), 5.6)

    mark_f = font(SANS_B, 128)
    word = "FOLIO"
    ww = tracked_width(d, word, mark_f, 16)
    tracked(d, ((W - ww) / 2, cy - 108), word, mark_f, INK + (int(255 * appear),), 16)

    rule_w = ease_out(ramp(t, 0.85, 0.9)) * 210
    d.line(((W - rule_w) / 2, cy + 66, (W + rule_w) / 2, cy + 66), fill=GOLD + (255,), width=3)

    sub_f = font(SANS, 33)
    sub = "every claim, a page"
    sw = d.textlength(sub, font=sub_f)
    d.text(((W - sw) / 2, cy + 104), sub, font=sub_f, fill=DIM + (int(255 * ease_out(ramp(t, 1.25, 0.9))),))
    return frame


def stats_scene(t: float) -> Image.Image:
    frame = backdrop(7.0 + t, drift=1.0)
    frame.alpha_composite(Image.new("RGBA", (W, H), (6, 7, 9, 120)))
    d = ImageDraw.Draw(frame)

    stats = [("68", "documents"), ("4,666", "passages"), ("10.2 MB", "on disk")]
    num_f = font(SANS_B, 92)
    lab_f = font(SANS_B, 15)
    xs = [W / 2 - 430, W / 2, W / 2 + 430]

    for i, ((value, label), x) in enumerate(zip(stats, xs)):
        a = ease_out(ramp(t, 0.25 + i * 0.22, 0.8))
        y = 380 - int((1 - a) * 14)
        vw = d.textlength(value, font=num_f)
        d.text((x - vw / 2, y), value, font=num_f, fill=GOLD + (int(255 * a),))
        lw = tracked_width(d, label.upper(), lab_f, 4.2)
        tracked(d, (x - lw / 2, y + 120), label.upper(), lab_f, DIM + (int(230 * a),), 4.2)

    a = ease_out(ramp(t, 1.1, 0.9))
    line = "indexed once  ·  no network  ·  no API key  ·  no embeddings"
    f = font(SANS, 24)
    lw = d.textlength(line, font=f)
    d.text(((W - lw) / 2, 640), line, font=f, fill=FAINT + (int(255 * a),))
    return frame


APP_CARD = rounded_card(APP, 14)


def app_scene(t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), BG + (255,))
    frame.alpha_composite(glow((W // 2, 330), 600, strength=28))

    enter = ease_out(ramp(t, 0.0, 0.9))
    base = 1180 / APP_CARD.width
    scale = base * (0.972 + 0.028 * enter) * (1 + 0.026 * ease_in_out(ramp(t, 1.0, 18.0)))
    paste_scaled(frame, APP_CARD, (W // 2, int(340 + (1 - enter) * 18)), scale, enter)

    d = ImageDraw.Draw(frame)
    caps_f = font(SANS_B, 14)
    body_f = font(SANS, 27)
    left = 210

    a1 = ease_out(ramp(t, 1.4, 0.8))
    tracked(d, (left, 690), "ONE QUESTION, ASKED IN THE APP", caps_f, GOLD + (int(230 * a1),), 4.6)
    d.text((left, 714), "“where does the platform spec cap retries?”", font=body_f, fill=INK + (int(255 * a1),))

    a2 = ease_out(ramp(t, 8.6, 0.8))
    tracked(d, (left, 776), "USED 5 TOOLS", caps_f, DIM + (int(210 * a2),), 4.6)
    d.text((left + 220, 770), "verified verbatim · EXACT · p. 1 · characters 97–215",
           font=font(SANS, 22), fill=GOLD + (int(240 * a2),))
    return frame


PAGE_CARD = rounded_card(PAGE, 10)


def page_scene(t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), BG + (255,))
    frame.alpha_composite(glow((430, 450), 520, strength=22))

    enter = ease_out(ramp(t, 0.0, 0.9))
    paste_scaled(frame, PAGE_CARD, (392, 450), 0.50 * (0.985 + 0.015 * enter), enter)
    d = ImageDraw.Draw(frame)

    # The box, drawn from the plugin's own character-box rectangle.
    pw, ph = PAGE.width * 0.50, PAGE.height * 0.50
    left = 392 - pw / 2
    top = 450 - ph / 2
    rect = (0.31501, 0.1498, 0.4645, 0.16195)
    x0, y0 = left + rect[0] * pw, top + rect[1] * ph
    x1, y1 = left + rect[2] * pw, top + rect[3] * ph
    grow = ease_in_out(ramp(t, 1.2, 0.9))
    if grow > 0:
        pad = 7
        box = (x0 - pad, y0 - pad, x0 - pad + (x1 - x0 + pad * 2) * grow, y1 + pad)
        d.rounded_rectangle(box, 3, outline=GOLD + (255,), width=3)

    a = ease_out(ramp(t, 2.0, 0.9))
    x = 760
    caps_f = font(SANS_B, 14)
    tracked(d, (x, 250), "THE PAGE, NOT A PARAPHRASE", caps_f, GOLD + (int(230 * a),), 4.6)

    head_f = font(SANS_B, 44)
    for i, line in enumerate(wrap(d, "The highlight is the PDF's own character geometry.", head_f, 700)):
        d.text((x, 296 + i * 56), line, font=head_f, fill=INK + (int(255 * a),))

    body_f = font(SANS, 23)
    body = ("folio_open renders the page from the file and draws the sentence "
            "from the same coordinates folio_verify checked. Nothing is guessed "
            "from a text match, so the picture and the citation cannot disagree.")
    b = ease_out(ramp(t, 2.6, 1.0))
    for i, line in enumerate(wrap(d, body, body_f, 690)):
        d.text((x, 440 + i * 36), line, font=body_f, fill=DIM + (int(240 * b),))

    c = ease_out(ramp(t, 3.4, 0.9))
    d.text((x, 640), "p. 1  ·  the verified line  ·  characters 97–215",
           font=font(MONO, 19), fill=GOLD_DIM + (int(255 * c),))
    return frame


def terminal_panel(size=(1180, 430), title="hermes — folio") -> Image.Image:
    panel = Image.new("RGBA", size, PANEL + (255,))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), 12, fill=PANEL + (255,), outline=RULE + (255,), width=1)
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((22 + i * 22, 18, 32 + i * 22, 28), fill=color + (200,))
    d.text((size[0] / 2 - 60, 12), title, font=font(MONO, 14), fill=FAINT + (255,))
    d.line((0, 44, size[0], 44), fill=RULE + (255,))
    return rounded_card(panel, 12)


def draw_terminal(frame, t, lines, size=(1180, 430), cps=64.0, start=0.0, center=(W // 2, 452)):
    panel = terminal_panel(size)
    d0 = ImageDraw.Draw(panel)

    f = font(MONO, 19)
    y = 70
    consumed = 0
    budget = max(0, int((t - start) * cps))
    for segments in lines:
        line_len = sum(len(s[0]) for s in segments)
        if line_len == 0:
            y += 34
            continue
        if consumed > budget:
            break
        x = 34
        for text, color in segments:
            visible = text[: max(0, min(len(text), budget - consumed))]
            if visible:
                d0.text((x, y), visible, font=f, fill=color + (255,))
                x += d0.textlength(visible, font=f)
            consumed += len(visible)
            if len(visible) < len(text):
                if int(t * 3) % 2 == 0:
                    d0.rectangle((x + 1, y - 2, x + 9, y + 22), fill=GOLD + (210,))
                break
        y += 34

    paste_scaled(frame, panel, center, 1.0, ease_out(ramp(t, 0.0, 0.5)))
    return frame


def revision_scene(t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), BG + (255,))
    frame.alpha_composite(glow((W // 2, 440), 480, strength=16))
    lines = [
        [("$ ", GOLD), ("hermes chat -q 'what changed in the retry clause?'", INK)],
        [],
        [("platform-spec.pdf", DIM), ("   revised 2026-10-14", FAINT)],
        [("- Retries are capped at ", DIM), ("three", (232, 96, 92)), (" attempts per request.", DIM)],
        [("+ Retries are capped at ", DIM), ("five", (120, 200, 140)), (" attempts per request.", DIM)],
        [],
        [("the cap moved from three to five; the backoff paragraph", INK)],
        [("is unchanged.", INK)],
    ]
    draw_terminal(frame, t, lines, size=(1180, 400), cps=66, start=0.35)

    d = ImageDraw.Draw(frame)
    a = ease_out(ramp(t, 6.0, 0.9))
    tracked(d, (W // 2 - 330, 700), "WHAT DID IT SAY BEFORE?", font(SANS_B, 14), GOLD + (int(230 * a),), 4.6)
    d.text((W // 2 - 330, 726), "Every revision is kept, so the answer can be a diff.",
           font=font(SANS, 24), fill=DIM + (int(250 * a),))
    return frame


def refusal_scene(t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), BG + (255,))
    frame.alpha_composite(glow((W // 2, 430), 460, (180, 90, 80), 18))
    lines = [
        [("$ ", GOLD), ("hermes chat -q 'quote the regional failover clause'", INK)],
        [],
        [("folio_verify  ", DIM), ("\"the platform shall fail over to the nearest region\"", INK)],
        [("  NOT FOUND", (232, 96, 92)), ("   no such sentence in any indexed document", FAINT)],
        [],
        [("There is no regional failover clause. The closest sentence is", INK)],
        [("on p. 3, about single-region deployment.", INK)],
    ]
    draw_terminal(frame, t, lines, size=(1180, 360), cps=72, start=0.35)

    d = ImageDraw.Draw(frame)
    a = ease_out(ramp(t, 5.4, 0.9))
    tracked(d, (W // 2 - 330, 660), "THE HARD PART", font(SANS_B, 14), GOLD + (int(230 * a),), 4.6)
    d.text((W // 2 - 330, 686), "A quote the document does not contain is refused, not smoothed over.",
           font=font(SANS, 24), fill=DIM + (int(250 * a),))
    return frame


def closing_scene(t: float) -> Image.Image:
    frame = backdrop(4.0, drift=0.0)
    frame.alpha_composite(Image.new("RGBA", (W, H), (6, 7, 9, 140)))
    d = ImageDraw.Draw(frame)

    a = ease_out(ramp(t, 0.1, 0.9))
    mark_f = font(SANS_B, 44)
    ww = tracked_width(d, "FOLIO", mark_f, 8)
    tracked(d, ((W - ww) / 2, 282), "FOLIO", mark_f, INK + (int(255 * a),), 8)

    b = ease_out(ramp(t, 0.6, 0.9))
    cmd_f = font(MONO, 27)
    cmd = "hermes plugins install okyssta/hermes-folio"
    cw = d.textlength(cmd, font=cmd_f)
    box = (W / 2 - cw / 2 - 30, 372, W / 2 + cw / 2 + 30, 428)
    d.rounded_rectangle(box, 8, fill=PANEL + (255,), outline=(58, 61, 67, 255), width=1)
    d.text((W / 2 - cw / 2, 386), cmd, font=cmd_f, fill=GOLD + (int(255 * b),))

    c = ease_out(ramp(t, 1.2, 0.9))
    d.text((W / 2 - cw / 2, 452), "github.com/okyssta/hermes-folio", font=font(SANS, 22),
           fill=DIM + (int(240 * c),))

    e = ease_out(ramp(t, 1.8, 1.0))
    claims = "local only   ·   no keys   ·   no embeddings   ·   MIT"
    f = font(SANS_B, 15)
    lw = tracked_width(d, claims.upper(), f, 3.6)
    tracked(d, ((W - lw) / 2, 592), claims.upper(), f, FAINT + (int(255 * e),), 3.6)
    return frame


SCENES = [
    Scene(0.0, 7.2, title_scene),
    Scene(7.2, 12.6, stats_scene, fade_in=0.7, fade_out=0.6),
    Scene(12.6, 32.0, app_scene, fade_in=0.6, fade_out=0.6),
    Scene(32.0, 45.0, page_scene, fade_in=0.6, fade_out=0.6),
    Scene(45.0, 58.0, revision_scene, fade_in=0.5, fade_out=0.5),
    Scene(58.0, 68.0, refusal_scene, fade_in=0.5, fade_out=0.5),
    Scene(68.0, DURATION, closing_scene, fade_in=0.8, fade_out=0.0),
]


def render_frame(t: float) -> Image.Image:
    active = [s for s in SCENES if s.start <= t < s.end] or [SCENES[-1]]
    scene = active[0]
    frame = scene.draw(t - scene.start)

    local = t - scene.start
    alpha = 1.0
    if scene.start > 0 and local < scene.fade_in:
        alpha = ease_out(local / scene.fade_in)
    elif scene.end < DURATION and scene.end - t < scene.fade_out:
        alpha = ease_out((scene.end - t) / scene.fade_out)

    if alpha >= 0.999:
        return frame
    under = FLAT.copy()
    idx = SCENES.index(scene)
    if idx > 0 and local < scene.fade_in:
        prev = SCENES[idx - 1]
        under = prev.draw(min(prev.end - prev.start - 0.001, t - prev.start))
    else:
        under = Image.new("RGBA", (W, H), BG + (255,))
    return Image.blend(under.convert("RGB"), frame.convert("RGB"), alpha).convert("RGBA")


def main() -> None:
    ffmpeg = subprocess.run(
        ["bash", "-lc", "ls /root/.hermes/tools/ffmpeg-*/bin/ffmpeg | head -1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    out = Path("/root/folio/docs/demo/folio-demo.mp4")
    process = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "19",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )
    total = int(DURATION * FPS)
    for index in range(total):
        frame = render_frame(index / FPS)
        process.stdin.write(frame.convert("RGB").tobytes())
        if index % 150 == 0:
            print(f"{index}/{total}", flush=True)
    process.stdin.close()
    process.wait()
    print("written", out, out.stat().st_size)


if __name__ == "__main__":
    main()
