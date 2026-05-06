from __future__ import annotations

from deep_daily.backup.retention import ARCHIVE_NAME_RE, select_prune_candidates


def test_retention_edge_cases():
    assert select_prune_candidates([], 30) == []
    assert select_prune_candidates(["m4-deep-daily-20260505-030000Z.tar.gz"], 1) == []
    assert select_prune_candidates(["m4-deep-daily-20260505-030000Z.tar.gz"], 0) == [
        "m4-deep-daily-20260505-030000Z.tar.gz"
    ]
    files = [
        "m4-deep-daily-20260505-030000Z.tar.gz",
        "m4-deep-daily-20260504-030000Z.tar.gz",
        "m4-deep-daily-20260503-030000Z.tar.gz",
    ]
    assert select_prune_candidates(files, 3) == []
    assert select_prune_candidates(files, 2) == ["m4-deep-daily-20260503-030000Z.tar.gz"]


def test_retention_regex_guards_prefix_and_lex_sort_matches_chronological():
    files = [
        "m4-deep-daily-20260503-030000Z.tar.gz",
        "m4-deep-daily-20260505-030000Z.tar.gz",
        "m4-deep-daily-20260504-030000Z.tar.gz",
        "other-20260501.tar.gz",
        "m4-deep-daily-20260505-030000Z.tar.gz.part",
    ]
    assert ARCHIVE_NAME_RE.match("m4-deep-daily-20260505-030000Z.tar.gz")
    assert not ARCHIVE_NAME_RE.match("other-20260505-030000Z.tar.gz")
    assert not ARCHIVE_NAME_RE.match("m4-deep-daily-20260505-030000Z.tar.gz.part")
    assert select_prune_candidates(files, 2) == ["m4-deep-daily-20260503-030000Z.tar.gz"]
