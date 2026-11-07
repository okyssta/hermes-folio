# Folio

**Every claim, a page.** Hermes gets a library of the documents you own — and
answers from them with the file, the page, and the exact sentence, instead of a
paraphrase.

```
you   › where does the spec cap retries?

hermes › Retries are capped at three attempts per request
         (`platform-spec-v4.pdf`, p. 1), with exponential backoff starting at
         250 ms. The retry budget resets every sixty seconds per client and route
         (p. 1).
```

Folio is a local index of PDF, Word, PowerPoint, Excel, HTML, Markdown and source
files. Seven tools let the agent search it, open a page, **prove a quotation**,
see **what changed** since a date, and format citations. A desktop page shows the
actual page image with the matching sentence boxed.

No account, no API key, no embeddings, no network calls. One SQLite file, under the
profile's `plugin-data` folder.

---

## Why not just grep — or a vector database

| | `grep` over a document folder | embeddings | **Folio** |
|---|---|---|---|
| PDF page numbers | ✗ (text only) | ✗ (chunk ids) | ✓ `p. 14` |
| Quote can be verified | ✗ | ✗ | ✓ `folio_verify` |
| Deterministic result | ✓ | ✗ | ✓ |
| Cold start on 5,000 files | minutes, every query | hours of embedding | 6.7 s per 1,600 pages, once |
| Runs offline, no model | ✓ | ✗ (needs an encoder) | ✓ |
| Revised document keeps its old text | ✗ | ✗ | ✓ content-hashed revisions |

The honest trade: Folio is lexical, not semantic. There is no vector search, so
"throttling" will not match "rate limit" on its own. That is deliberate — the
model *is* the query planner. It rewrites the question, runs two or three short
queries, and every hit it gets back is text that demonstrably exists. A retrieval
layer that guesses is worse than one that returns evidence and says when it has
none.

## What it measures

On a 2-vCPU ARM VM (no GPU), indexing 60 real arXiv papers:

```
60 documents · 1,630 pages · 4,649 passages · 5.3M characters
indexed in 12.2 s   (≈ 130 pages/s)
query latency        0.6 – 1.8 ms
index size           10.2 MB
```

The number that matters is not the speed. It is that every result points at a
page you can turn to.

## Tools

| Tool | What it answers |
|---|---|
| `folio_search` | *"Where do my documents mention the retry budget?"* → ranked passages with document, locator and snippet |
| `folio_open` | *"Read page 14."* → the full passage, the path, the capture time, a ready-made citation |
| `folio_verify` | *"Is this quotation real?"* → `EXACT` / `FUZZY` / `NOT FOUND`, with character offsets |
| `folio_changes` | *"What changed in my documents this month?"* → added and revised documents, with a diff |
| `folio_bibliography` | *"Cite these five."* → APA, MLA, Chicago, BibTeX or plain |
| `folio_index` | Add folders, refresh incrementally |
| `folio_forget` | Delete stored text and revisions — the originals are never touched |

`/folio` gives a person the same thing from the command line: status, `add`,
`search`, `changes`, `forget`.

## The desktop page

A right-hand pane: library health, a search box, results with their locators, and
a reader that renders the page and boxes the sentence it came from.

Highlights are real geometry: Folio reads the character boxes out of the PDF and
scales them onto the rendered page, so the box lands on the sentence rather than
near it.

![the reader, with the match boxed on the page](docs/demo/page_marked.png)

## Demo

[`docs/demo/folio-demo.mp4`](docs/demo/folio-demo.mp4) — 62 seconds, silent.

It is cut from a real session: index 68 documents, answer with the page, verify
the quotation, catch a revision, refuse a fabricated quote. The full transcript
is in [`docs/demo/transcript.md`](docs/demo/transcript.md), and the video's text
is taken from it verbatim.

```
you   › where does the platform spec cap retries?
hermes › Retries are capped at three attempts per request … (p. 1)
         Quote verified EXACT at p. 1 (chars 97-216).
```

## Revisions

Every document is content-hashed. When a file changes, the old text is kept (up
to `max_versions`), so:

```
you   › what did the agreement say before legal edited it?

hermes › It said liability was limited to the fees paid in the prior twelve
         months (rev 1, 2026-02-11). The current revision (2026-03-04) adds
         "and excludes the indemnity obligations", shown here as a diff:
         + and excludes the indemnity obligations
```

`folio_changes` lists what moved; `folio_open(version="previous")` reads the
revision before the current one.

## Install

```bash
hermes plugins install okyssta/hermes-folio   # or `... folio` once listed
hermes plugins enable folio
```

or, from a checkout:

```bash
git clone https://github.com/okyssta/hermes-folio
cp -r hermes-folio ~/.hermes/plugins/folio
hermes plugins enable folio
```

Then, in the desktop app: **Settings → Plugins → Folio** (the pane is opt-in), and
⌘K → **Reload desktop plugins**.

Add a folder — nothing is indexed until you name one:

```
/folio add ~/Documents
```

or just ask: *"index my Documents folder and tell me what you can answer from it."*

## Configuration

| Key | Default | Notes |
|---|---|---|
| `roots` | `[]` | Nothing is indexed until you add a folder |
| `include_extensions` | 29 types | PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, code |
| `exclude_dirs` | `.git`, `node_modules`, `venv`, … | Never descended into |
| `max_file_mb` | `80` | Larger files are skipped and reported |
| `max_text_chars` | `400000` | Per document |
| `chunk_chars` | `1200` | Passage size |
| `keep_versions` | `true` | Keep the previous text of a revised file |
| `max_versions` | `20` | Per document |
| `redact` | `true` | Strip keys, tokens, private-key blocks and cards before storing |
| `auto_scan_minutes` | `15` | Background refresh; `0` disables |

## Privacy

- **Local only.** No network calls anywhere in the plugin. `tests/test_manifest.py`
  asserts the disclosure line; the code has no HTTP client.
- **Read-only on your files.** Folio reads documents and writes one SQLite file
  plus its WAL. It never modifies, moves or deletes an original.
- **Redaction at the storage boundary**, before text is indexed: PEM private-key
  blocks, AWS/GitHub/OpenAI/Stripe/Slack/Google/HuggingFace token shapes, JWT
  triples, `Authorization:` headers, `scheme://user:pass@host`, and card numbers
  that pass a Luhn check.
- **`forget` means forget.** `folio_forget` removes the passages, their FTS
  entries, every revision and the document row. A probe in the test suite checks
  search returns nothing afterwards.

## What it does not do

- **No OCR.** A scanned PDF has no text layer; Folio reports it as a scan instead
  of inventing text. OCR the file first (the `peripheral` plugin or `ocrmypdf`).
- **No semantic search** — see above. The agent supplies the vocabulary.
- **No page geometry for non-PDF formats.** DOCX, PPTX and XLSX have no pages, so
  they cite by paragraph, slide or sheet range and the reader shows text.
- **No OCR'd figures, no table cell geometry** — a cell is cited as a row range.

## Development

```bash
uv venv /tmp/fv --system-site-packages
uv pip install --python /tmp/fv/bin/python pytest pytest-timeout pyyaml fastapi httpx pypdf pypdfium2 openpyxl pillow
/tmp/fv/bin/python -m pytest -q          # 39 tests, no network
hermes plugins validate . --install-deps  # the same checks catalog CI runs
```

The PDF fixtures are written by `tests/conftest.py` — a hundred lines of raw PDF
so the suite carries no binary blobs.

## License

MIT.
