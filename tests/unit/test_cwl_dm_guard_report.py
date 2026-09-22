"""DEV-mode per-clan report of players the CWL DM guard held back after Start Enrollment
(2026-09-22): _send_cwl_enrollment_dm_batch records who was skipped, and
format_cwl_dm_guard_skipped_report groups them by current clan and splits at Discord's limit.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")


@pytest.fixture
def cache(monkeypatch):
    from qapbot.cache_manager import CACHE

    db = MagicMock()
    db.get_current_clan_tags_for_players_sync = MagicMock(return_value={
        "#P1": "#ZULU", "#P2": "#ALPHA", "#P3": "#ALPHA",
    })
    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(CACHE, "clan_name_cache", {"#ZULU": {"name": "Zulu"}, "#ALPHA": {"name": "Alpha"}})
    return CACHE


def test_report_groups_by_current_clan_sorted_with_no_clan_last(cache):
    from qapbot.QBdiscocmdshelper_cwl import format_cwl_dm_guard_skipped_report

    skipped = [
        {"player_tag": "#P1", "player_name": "zed"},
        {"player_tag": "#P3", "player_name": "bob"},
        {"player_tag": "#P2", "player_name": "Anna"},
        {"player_tag": "#P4", "player_name": None},  # no current clan, no name
    ]
    messages = format_cwl_dm_guard_skipped_report(skipped, 1, "42")

    assert len(messages) == 1
    lines = messages[0].split("\n")
    assert "(4)" in lines[0]
    body = [line for line in lines[1:] if line]
    assert body == [
        "**Alpha (#ALPHA)** — 2", "• Anna (#P2)", "• bob (#P3)",
        "**Zulu (#ZULU)** — 1", "• zed (#P1)",
        "**No current clan** — 1", "• #P4 (#P4)",
    ]


def test_report_is_empty_when_nobody_was_skipped(cache):
    from qapbot.QBdiscocmdshelper_cwl import format_cwl_dm_guard_skipped_report

    assert format_cwl_dm_guard_skipped_report([], 1, "42") == []


def test_report_splits_under_discord_limit_on_line_boundaries(cache):
    from qapbot.QBdiscocmdshelper_cwl import DISCORD_MESSAGE_LIMIT, format_cwl_dm_guard_skipped_report

    skipped = [{"player_tag": f"#T{i:04d}", "player_name": "x" * 40} for i in range(200)]
    messages = format_cwl_dm_guard_skipped_report(skipped, 1, "42")

    assert len(messages) > 1
    assert all(len(m) <= DISCORD_MESSAGE_LIMIT for m in messages)
    joined = "\n".join(messages)
    assert all(f"(#T{i:04d})" in joined for i in range(200))


async def test_batch_records_players_skipped_by_dm_guard(monkeypatch):
    from qapbot import QBdiscocmdshelper_cwl as cwl
    from qapbot.cache_manager import CACHE

    db = MagicMock()
    db.get_cwl_player_season_dm_status_bulk_sync = MagicMock(return_value={})
    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(cwl, "_dm_guard_blocks", lambda discord_id: True)

    result = await cwl._send_cwl_enrollment_dm_batch(1, 1, "2026-10", [
        {"player_tag": "#P1", "player_name": "One", "discord_id": "10"},
        {"player_tag": "#P2", "player_name": "Two", "discord_id": "20"},
    ])

    assert result["skipped_dm_guard"] == 2
    assert result["dm_guard_skipped"] == [
        {"player_tag": "#P1", "player_name": "One"}, {"player_tag": "#P2", "player_name": "Two"},
    ]
    assert result["contacted"] == 0
