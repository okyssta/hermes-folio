---
name: folio
description: "Answer from the user's own documents, with page-level citations."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [documents, citations, evidence, pdf, research, provenance]
---

# Citing the user's own documents

Folio is a local index of the documents on this machine. It exists so that an
answer about a document comes from the document — with the page — instead of from
the model's memory of something similar.

## When to use it

Reach for Folio before you reach for the web, and before you ask the user to
paste text, whenever the question is about **material the user owns**:

- "Where does the spec say retries are capped?" → `folio_search`
- "What did the contract say about termination?" → `folio_search`
- "Summarise chapter 4" / "read page 12" → `folio_open`
- "Which of my documents mention the migration?" → `folio_search`
- "What changed in my documents this week?" → `folio_changes`
- "Is this quotation real?" → `folio_verify`

Do **not** use it for general knowledge, current events, or anything the user has
not put on this machine. An empty result is information: say the library does not
cover it rather than answering from memory as if it did.

## The loop

1. **Search with distinctive words.** Two or three content words beat a full
   question. Run several queries instead of one long one; preferred synonyms
   separately. Quoted phrases, `AND`, `OR`, `NEAR` and a trailing `*` work.
2. **Open the page** you found (`folio_open` with the hit's `locator`) when the
   snippet is not enough. The reply carries the file path, the locator and a
   ready-made citation line.
3. **Verify every direct quotation** with `folio_verify` before you attribute it.
   `EXACT` means the sentence is there verbatim; `FUZZY` means it is not, and the
   reply tells you how close it was. Never present a `FUZZY` or `NOT FOUND` result
   as a quotation.

## Citing

A citation is only useful if the reader can find the passage:

- Name the document and the locator: *"Retries are capped at three attempts
  (`platform-spec.pdf`, p. 4)."*
- Use `folio_bibliography` for a reference list. It emits only the fields the
  file actually carries — BibTeX entries are `@misc` because a PDF does not know
  its venue. Do not invent a journal, a DOI or a year.
- Pass the real locator into the bibliography call (`locator="p. 4"`) when every
  entry shares a page, or append it per claim in prose.

## Revisions

Every document is content-hashed. When a file changes, the old text is kept, so:

- `folio_changes(since="30d")` lists what was added or revised, with a diff
  summary for revisions.
- `folio_open(doc=<id>, version="previous")` reads the revision before the
  current one — the honest way to answer "what did this say before it changed?".

If a document is marked *file missing*, the index still has its text but the
original has moved or been deleted. Say so rather than implying the path works.

## Etiquette

- Never index a folder the user did not name. `folio_index` with no arguments
  returns candidate folders; ask which one.
- `folio_forget` deletes stored text and revisions. The user's files are never
  touched — say that plainly when you use it.
- The library is local: no network calls, no embeddings. If a search misses, the
  fix is a better query, not a smarter index.
