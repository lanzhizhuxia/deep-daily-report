"""Tests for _citation_label helper (Oracle citation bug fix, 2026-05-05).

Protects the invariant: Step 3 / Step 4 citation labels are always scannable,
regardless of whether the upstream material's `title` field contains a proper
title or a 2500+ char tweet body.

Background: upstream collectors (especially Twitter) sometimes populate
`material["title"]` with the full tweet preview. Before this fix, pipeline.py
copied that raw string straight into HTML citations, producing multi-paragraph
reference entries (Oracle session ses_2099a0e7..., 2026-05-05).
"""

from deep_daily.pipeline import _citation_label, _CITATION_LABEL_MAX


def test_short_title_unchanged():
    assert _citation_label("Ondo launches USDY") == "Ondo launches USDY"


def test_empty_returns_empty():
    assert _citation_label("") == ""
    assert _citation_label(None) == ""


def test_whitespace_only_returns_empty():
    assert _citation_label("   \n  \t  ") == ""


def test_whitespace_collapsed():
    assert _citation_label("Ondo\n\n launches    USDY\t\tV2") == "Ondo launches USDY V2"


def test_tweet_body_truncated_with_ellipsis():
    tweet_body = (
        "tl;dr OP Mainnet added privacy infrastructure for enterprise, "
        "absorbed the largest TVL migration in its history, launched an "
        "institutional investment product, and became home to regulated "
        "precious metals from a Mitsui subsidiary. Here's what those four "
        "announcements add up to. Institutions moving onchain need proven "
        "infrastructure, compliance-compatible architecture, and counterparties."
    )
    result = _citation_label(tweet_body)
    assert len(result) <= _CITATION_LABEL_MAX + 1  # +1 for ellipsis char
    assert result.endswith("…")
    assert result.startswith("tl;dr OP Mainnet")


def test_exactly_at_boundary_no_ellipsis():
    s = "x" * _CITATION_LABEL_MAX
    result = _citation_label(s)
    assert result == s
    assert not result.endswith("…")


def test_one_over_boundary_truncated():
    s = "x" * (_CITATION_LABEL_MAX + 1)
    result = _citation_label(s)
    assert result.endswith("…")
    assert len(result) == _CITATION_LABEL_MAX + 1


def test_custom_max_chars():
    assert _citation_label("hello world", max_chars=5) == "hello…"


def test_leading_trailing_whitespace_stripped():
    assert _citation_label("   short title   ") == "short title"


def test_realworld_coingecko_tweet():
    """Real example from step-3-alt-rwas-rss.json — 500+ char 'title'."""
    raw = (
        "CoinGecko Releases RWA Report 2026: Tokenized RWA Market Cap Hits "
        '$19.3B According to CoinGecko\'s "RWA Report 2026," the total tokenized '
        "RWA market cap grew by 256.7% over 15 months, reaching $19.32 billion "
        "by the end of Q1 2026, representing 6.4% of the stablecoin market. "
        "Tokenized Treasuries remain the largest class, surpassing the $10 "
        "billion mark in February with a 67.2% market share."
    )
    result = _citation_label(raw)
    assert len(result) <= _CITATION_LABEL_MAX + 1
    assert result.endswith("…")
    assert "CoinGecko Releases RWA Report 2026" in result
