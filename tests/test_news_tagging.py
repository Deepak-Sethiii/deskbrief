"""Tests for the alias tagger. It is substring matching -- these pin down exactly
how much it does and does not do, including the false positives we accept."""

from __future__ import annotations

import pytest

from src.config import load_watchlist
from src.ingest.news import build_alias_patterns, clean_title, tag_ticker


@pytest.fixture(scope="module")
def patterns():
    return build_alias_patterns(load_watchlist())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Reliance Industries posts higher Q1 profit", "RELIANCE.NS"),
        ("TCS wins large deal in Europe", "TCS.NS"),
        ("Infosys raises FY guidance", "INFY.NS"),
        ("L&T bags order worth Rs 5,000 crore", "LT.NS"),
        ("Larsen & Toubro bags order", "LT.NS"),
        ("ITC Q1 preview: cigarette volumes", "ITC.NS"),
        ("Bharti Airtel adds subscribers", "BHARTIARTL.NS"),
    ],
)
def test_obvious_names_are_tagged(patterns, text, expected):
    assert tag_ticker(text, patterns) == expected


def test_longest_alias_wins(patterns):
    # both "HDFC Bank" and "HDFC" are aliases; the specific one must win
    assert tag_ticker("HDFC Bank raises deposit rates", patterns) == "HDFCBANK.NS"


def test_word_boundaries_prevent_substring_hits(patterns):
    # "ITC" must not fire inside SWITCH / STITCHED
    assert tag_ticker("Razorpay launches UPI SWITCH product", patterns) != "ITC.NS"
    assert tag_ticker("A stitched-together rally", patterns) is None


def test_no_match_returns_none(patterns):
    assert tag_ticker("Mastercard profit beats estimates", patterns) is None
    assert tag_ticker("", patterns) is None


def test_case_insensitive(patterns):
    assert tag_ticker("infosys gains after results", patterns) == "INFY.NS"


def test_known_false_positive_is_documented_behaviour(patterns):
    """A different legal entity that shares a brand prefix WILL be mis-tagged.

    ICICI Prudential Life is not ICICI Bank. Substring matching cannot tell them
    apart, and we do not pretend otherwise -- see the module docstring.
    """
    assert tag_ticker("ICICI Prudential Life Q4 profit falls", patterns) == "ICICIBANK.NS"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Razorpay announces #39;UPI Switch#39;", "Razorpay announces 'UPI Switch'"),
        ("M&amp;M, Coal India among gainers", "M&M, Coal India among gainers"),
        # \s+ also normalises exotic feed whitespace (here U+3000) to a plain space
        ("  spaced   out　title ", "spaced out title"),
        ("", ""),
    ],
)
def test_clean_title_undoes_feed_encoding(raw, expected):
    assert clean_title(raw) == expected
