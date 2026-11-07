"""Tool schemas.

Written for a model that has never heard of this plugin: each description says
what the library *is*, when reaching for it beats guessing or asking the user to
paste text, and what the returned coordinates mean. The search/open/verify trio
is the loop the model is expected to run — find, read, then prove.
"""

from __future__ import annotations

SEARCH = {
    "name": "folio_search",
    "description": (
        "Search the user's own document library — PDF, Word, PowerPoint, Excel, HTML, "
        "Markdown and source files indexed on this machine. Use it whenever an answer "
        "should come from a document the user owns rather than from memory or the web: "
        "'where does the spec say retries are capped?', 'what did the contract say about "
        "termination?', 'which paper defines this metric?'. Returns ranked passages, each "
        "with the document, the exact locator (page, slide, sheet rows or line range) and "
        "a verbatim snippet. Pass short, distinctive queries; run several instead of one "
        "long question. Follow up with folio_open to read the full page."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words likely to appear in the document. Quoted phrases and FTS operators (AND, OR, NEAR, trailing *) are honoured.",
            },
            "limit": {"type": "integer", "description": "Passages to return (default 8, max 25)."},
            "doc": {
                "type": "string",
                "description": "Optional: restrict to one document, by path fragment or title.",
            },
            "include_missing": {
                "type": "boolean",
                "description": "Also search documents whose file has since been moved or deleted (default false).",
            },
        },
        "required": ["query"],
    },
}

OPEN = {
    "name": "folio_open",
    "description": (
        "Read one document's page, slide, sheet range or line range in full, exactly as "
        "it was indexed. Use it after folio_search when the snippet is not enough, or to "
        "read a specific citation the user gave you. Returns the text plus the file path, "
        "the version capture time, and a ready-made citation line. Documents indexed in "
        "several revisions can be opened at an older capture (version='previous')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc": {"type": "string", "description": "Document id, path fragment, or title."},
            "locator": {
                "type": "string",
                "description": "Exact locator from a search hit, e.g. 'p. 14', 'slide 3', 'Sheet1!rows 12-48', 'lines 90-140'. Empty reads the start of the document.",
            },
            "version": {
                "type": "string",
                "description": "'current' (default) or 'previous' — the revision before the latest one.",
            },
            "max_chars": {"type": "integer", "description": "Cap on returned characters (default 6000, max 24000)."},
        },
        "required": ["doc"],
    },
}

VERIFY = {
    "name": "folio_verify",
    "description": (
        "Prove that a quotation really appears in a document before you attribute it. "
        "Give the sentence you intend to cite; Folio reports whether it is present "
        "verbatim (EXACT, with the locator and character offsets), close to a passage "
        "(FUZZY, with a similarity ratio), or not there at all (NOT FOUND). Use this for "
        "every direct quotation in a report, and whenever the user challenges a citation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "quote": {"type": "string", "description": "The sentence or phrase as you intend to cite it."},
            "doc": {"type": "string", "description": "Document id, path fragment, or title."},
            "locator": {
                "type": "string",
                "description": "Optional locator to check first, e.g. 'p. 14'. Empty searches the whole document.",
            },
        },
        "required": ["quote", "doc"],
    },
}

CHANGES = {
    "name": "folio_changes",
    "description": (
        "List documents added or revised since a point in time, with a short diff for "
        "revisions. Use it for 'what changed in my documents lately?', to catch a "
        "contract or spec that was updated under the user, and to answer 'what did this "
        "say last month' by locating the previous revision before opening it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": "'7d' (default), '24h', 'today', '2026-03-01', or an ISO date.",
            },
            "limit": {"type": "integer", "description": "Documents to list (default 20, max 60)."},
        },
        "required": [],
    },
}

BIBLIOGRAPHY = {
    "name": "folio_bibliography",
    "description": (
        "Format citations for documents in the library from the metadata Folio extracted "
        "(title, author, year, path). Use it to build a reference list for a report "
        "without inventing publication details: Folio emits only fields the file actually "
        "carries, and BibTeX entries are @misc for that reason."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "docs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Document ids, path fragments or titles. Empty uses the documents most recently cited? No — empty is an error.",
            },
            "style": {
                "type": "string",
                "description": "One of: apa (default), mla, chicago, bibtex, plain.",
            },
            "locator": {"type": "string", "description": "Optional page hint shared by every entry, e.g. 'p. 14'."},
        },
        "required": ["docs"],
    },
}

INDEX = {
    "name": "folio_index",
    "description": (
        "Add folders or files to the library and refresh what changed. Incremental: "
        "unchanged files are stat-ed and skipped, changed ones are re-read and kept as a "
        "new revision. With no paths it refreshes the configured library folders; if none "
        "are configured it returns candidate folders with file counts so you can ask the "
        "user which to index. Indexing is local and makes no network calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Folders or files to add. Empty refreshes the configured roots.",
            },
            "force": {
                "type": "boolean",
                "description": "Re-read every file even when its mtime is unchanged (default false).",
            },
        },
        "required": [],
    },
}

FORGET = {
    "name": "folio_forget",
    "description": (
        "Delete documents from the index — the only tool here that destroys data. Removes "
        "the stored text, every revision and the search entries; the user's original files "
        "are never touched. Use it when the user asks to forget a document or remove a "
        "folder from the library. Confirm the scope you passed in the reply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "docs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Document ids, path fragments or titles to delete.",
            },
            "all": {
                "type": "boolean",
                "description": "Delete the entire index (every document, every version). Requires docs to be empty.",
            },
            "remove_root": {
                "type": "string",
                "description": "Also stop indexing this folder path.",
            },
        },
        "required": [],
    },
}

ALL = [SEARCH, OPEN, VERIFY, CHANGES, BIBLIOGRAPHY, INDEX, FORGET]
