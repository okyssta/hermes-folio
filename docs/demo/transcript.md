# Demo transcript

Captured from a live Hermes session running this plugin, on 2026-11-07, against
the 68-document test library in `/root/folio-demo` (60 real arXiv papers plus a
worked example folder: a platform spec PDF, a services agreement PDF, a deck, a
workbook, a DOCX and notes). The model is `deepseek-v4.1-flash`.

The video's on-screen text is taken from these two runs. Nothing was rewritten
for the video; only CLI chrome (spinners, session footer, ANSI colour) was
stripped.

## Run 1 — index, then cite

Prompt:

    Index /root/folio-demo with folio_index and report the counts and readers.
    Then answer from the library: where does the platform spec cap retries, and
    what exactly does it say? Cite the page.

☤ Hermes
I'll start by loading the folio tools and indexing the directory.
────────────────────────────────────────

📖 Reading tool details · 5 tools
⚡ Folio_index  28.3s

────────────────────────────────────────
Now search for the platform spec retries cap.
∴ Thought for 0s
────────────────────────────────────────

⚡ Folio_search retries capped
⚡ Folio_search retry maximum attempts platform spec
⚡ Folio_open

────────────────────────────────────────
Verify the quote.
∴ Thought for 0s
────────────────────────────────────────

⚡ Folio_verify

────────────────────────────────────────
☤ Hermes
Indexing complete.

  Indexed: 68 new, 0 revised, 0 unchanged, 0 skipped, 0 failed (28.3s)
  Library: 68 documents · 4,666 passages · 5.3M characters
  Readers: pdfium + pypdf + openpyxl

Where the retries are capped — Platform Service Specification, v4.0 (Platform Engineering, Internal), section "1. Request handling", page 1:

  "Retries are capped at three attempts per request, with exponential backoff starting at 250 ms and jitter of 20 percent.
  The retry budget is reset every sixty seconds per client and per route."

Quote verified EXACT at p. 1 (chars 97-216) of /root/folio-demo/work/platform-spec-v4.pdf.Citation: 2026. Platform Service Specification, p. 1.

Worth flagging a conflict: /root/folio-demo/work/meeting-notes.md ("Platform weekly › Decisions") records that retries move to the edge and "the origin stops retrying entirely," and q3-roadmap slide 2 lists "Move the retry budget to the edge" as Priya's Q3 work. So the spec's capped-at-origin rule is the current written spec, but the meeting notes describe it as being replaced.
────────────────────────────────────────

## Run 2 — a revision, and a quotation that does not exist

Between the runs the spec file was replaced with a revised version (retries
three → five, p99 400/620 ms → 250/400 ms, audit retention 400 → 180 days), so
the library held two revisions of the same document. The file name was left
unchanged on purpose — real documents are like that.

Prompt:

    Two questions about my library. (1) What changed in the platform spec, and
    what exactly did it say about retries before the change? (2) Is this
    quotation real: 'Liability is unlimited for indirect damages.' Check it
    against the master services agreement.

☤ Hermes
(1) The platform spec was revised today (captured 15:33 UTC, replacing the 15:30 capture). Four lines changed; three are substantive:  Retries      was: "capped at three attempts per request, with exponential backoff
                    starting at 250 ms and jitter of 20 percent"
               now: "capped at five attempts per request, with the same backoff
                    and jitter"
  p99 latency  was: 400 ms edge / 620 ms origin               now: 250 ms edge / 400 ms origin  Audit logs   was: retained 400 days
               now: retained 180 days  Version      was: 4.0               now: 5.0

So the answer to "what did it say about retries before the change": exactly "Retries are capped at three attempts per request, with exponential backoff starting at 250 ms and jitter of 20 percent." (p. 1). The unchanged companion line is "The retry budget is reset every sixty seconds per client and per route."One thing to be aware of: the file is still named platform-spec-v4.pdf even though its contents now say Version 5.0, and both captures report as "revision 2 of 2" — so don't trust the filename or the revision counter alone as evidence of which version you're reading.

(2) That quotation is not in the master services agreement. Verbatim, section 9 (p. 2) says the opposite:  "Liability is limited to the fees paid in the prior twelve months. Neither party is liable for indirect or consequential damages."

So "Liability is unlimited for indirect damages" is not merely unsupported — it inverts both clauses. Don't cite it.
────────────────────────────────────────
