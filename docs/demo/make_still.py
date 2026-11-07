"""Render the demo still: a real PDF page with the match boxed, as the pane draws it."""
import base64
import io
import sys
import time

sys.path.insert(0, "/root/.hermes/hermes-agent")
sys.path.insert(0, "/tmp/foliohome/plugins")

import folio  # noqa: E402
from folio.dashboard import plugin_api  # noqa: E402
from folio.settings import Settings  # noqa: E402
from folio.store import Store  # noqa: E402

DB = "/tmp/foliohome/plugin-data/folio/folio.db"
QUERY = sys.argv[1] if len(sys.argv) > 1 else "attempts"
folio._CACHED = Settings(roots=["/root/folio-demo"], auto_scan_minutes=0)  # noqa: SLF001
folio._CACHED_AT = time.monotonic() + 9999  # noqa: SLF001
folio._STORE = Store(DB)  # noqa: SLF001

store = folio.current_store()
row = store.resolve_document("platform-spec")
text = store.locator_text(int(row["id"]), "p. 1", version_id=int(row["current_version"]))
data = plugin_api._render_page(row["path"], "p. 1", text, QUERY)
raw = base64.b64decode(data["image"].split(",", 1)[1])
with open("/root/folio/docs/demo/page.png", "wb") as handle:
    handle.write(raw)

# Draw the same overlay the pane draws: translucent accent fill + hairline border.
from PIL import Image, ImageDraw  # noqa: E402

image = Image.open(io.BytesIO(raw)).convert("RGB")
draw = ImageDraw.Draw(image, "RGBA")
width, height = image.size
for rect in data["rects"]:
    box = [
        rect["left"] * width,
        rect["top"] * height,
        rect["right"] * width,
        rect["bottom"] * height,
    ]
    draw.rectangle(box, fill=(255, 214, 10, 70), outline=(255, 196, 0, 235), width=2)
image.save("/root/folio/docs/demo/page_marked.png")
print("rects", data["rects"], "size", width, height)
