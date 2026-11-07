"""Exercise the /folio command surface against the installed profile's index."""
import sys
import time

sys.path.insert(0, "/root/.hermes/hermes-agent")
sys.path.insert(0, "/root/.hermes/profiles/main/plugins")

import folio  # noqa: E402
from folio import commands  # noqa: E402
from folio.indexer import Indexer  # noqa: E402
from folio.settings import Settings  # noqa: E402
from folio.store import Store  # noqa: E402

folio._CACHED = Settings(roots=["/root/folio-demo"], auto_scan_minutes=15)  # noqa: SLF001
folio._CACHED_AT = time.monotonic() + 9999  # noqa: SLF001
folio._STORE = Store(str(folio.data_dir() / "folio.db"))  # noqa: SLF001
folio._INDEXER = Indexer(folio._STORE, folio.current_settings)  # noqa: SLF001

for argument in ("", "help", "status", "list", "search retry budget", "changes 30d", "nonsense words"):
    text = commands.handle(argument)
    print(f"--- /folio {argument!r} -> {len(text)} chars")
    print(text[:420].strip())
    print()
