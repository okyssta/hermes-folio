"""Query building, ranking and quote verification."""

from __future__ import annotations

from folio.search import build_match, find_quote, phrase_bonus, tokenize


def test_natural_language_becomes_an_or_of_content_words() -> None:
    match, words = build_match("what did the spec say about the retry budget?")
    assert words == ["spec", "say", "retry", "budget"]
    assert match == '"spec" OR "say" OR "retry" OR "budget"'


def test_stopwords_are_dropped() -> None:
    assert tokenize("the and of to in it") == []


def test_explicit_fts_syntax_is_passed_through() -> None:
    match, _ = build_match('"retry budget" AND notice')
    assert match == '"retry budget" AND notice'


def test_punctuation_cannot_break_the_expression() -> None:
    match, _ = build_match("retry:budget; drop table!")
    assert ":" not in match and ";" not in match


def test_phrase_bonus_prefers_the_exact_phrase() -> None:
    assert phrase_bonus("the retry budget is capped", ["retry", "budget"]) < 0
    assert phrase_bonus("budget was raised, then retry logic changed", ["retry", "budget"]) >= 0


def test_exact_quote_across_a_line_break() -> None:
    page = "Retries are\ncapped at three attempts per request."
    match = find_quote(page, "Retries are capped at three attempts")
    assert match.found and match.exact
    assert page[match.start:match.end].startswith("Retries")


def test_exact_quote_is_case_and_space_insensitive() -> None:
    page = "The  RETRY   budget is reset."
    assert find_quote(page, "the retry budget is reset").exact


def test_absent_quote_is_not_claimed_as_exact() -> None:
    page = "Either party may terminate this agreement with 30 days notice."
    match = find_quote(page, "the agreement may be terminated at any time without notice")
    assert not match.exact
    assert match.ratio < 0.95


def test_empty_quote_is_not_found() -> None:
    assert not find_quote("some text", "   ").found
