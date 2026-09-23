# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""/leaderboard scope handling (2026-09-23).

scope is all (default: current + past members) / members (current members only) / own. The
command itself resolves no rosters any more: generate_leaderboard_text() reads them from the DB
(get_leaderboard_roster), the same way the scheduled subscription posts do — so no CoC API call,
and a manual board is byte-identical to the scheduled one it replaces.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

import QBdiscordcmds


def _setup(monkeypatch, mock_interaction) -> tuple[List[Dict[str, Any]], AsyncMock]:
    """DM invocation for #CLAN1; returns (generate_leaderboard_text kwargs + sent texts, get_clan)."""
    mock_interaction.guild = None
    mock_interaction.guild_id = None
    get_clan = AsyncMock(side_effect=AssertionError("/leaderboard must not fetch rosters from the CoC API"))
    fake_cache = SimpleNamespace(
        user_accounts={}, clan_families={}, subscriptions={},
        get_all_subscriptions_flat=lambda: {},
        coc_clan_cache=SimpleNamespace(get_clan=get_clan),
        get_clan_name=lambda tag, default=None: "Clan One",
    )
    monkeypatch.setattr(QBdiscordcmds, "CACHE", fake_cache)
    monkeypatch.setattr(QBdiscordcmds, "_get_clan_tag", lambda clan: (1, "#CLAN1"))
    monkeypatch.setattr(QBdiscordcmds, "update_clan_war_info_and_stats", AsyncMock(return_value=True))
    monkeypatch.setattr(QBdiscordcmds, "update_capital_raid_for_clan", AsyncMock())
    calls: List[Dict[str, Any]] = []

    def fake_generate(*args, **kwargs):
        calls.append(kwargs)
        return "PLAYER TABLE"

    monkeypatch.setattr(QBdiscordcmds, "generate_leaderboard_text", fake_generate)

    async def fake_send_and_track(interaction, content=None, command_name=None, embed=None, ephemeral=False):
        calls.append({"sent": content})

    monkeypatch.setattr(QBdiscordcmds, "send_and_track", fake_send_and_track)
    return calls, get_clan


@pytest.mark.discord
@pytest.mark.asyncio
async def test_default_scope_is_all_and_no_roster_is_fetched_from_the_api(mock_interaction, monkeypatch):
    calls, get_clan = _setup(monkeypatch, mock_interaction)

    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1")  # type: ignore[arg-type]

    assert calls[0]["scope"] == "all"
    assert "member_player_tags" not in calls[0]  # resolved from the DB by generate_leaderboard_text
    get_clan.assert_not_awaited()
    assert calls[1]["sent"] == "```ansi\nPLAYER TABLE```"


@pytest.mark.discord
@pytest.mark.asyncio
async def test_members_scope_is_passed_through_and_unknown_values_fall_back_to_all(mock_interaction, monkeypatch):
    calls, _ = _setup(monkeypatch, mock_interaction)
    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", scope="members")  # type: ignore[arg-type]
    assert calls[0]["scope"] == "members"

    calls, _ = _setup(monkeypatch, mock_interaction)
    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", scope="bogus")  # type: ignore[arg-type]
    assert calls[0]["scope"] == "all"


@pytest.mark.discord
def test_scope_choices_offer_all_members_own():
    param = next(p for p in QBdiscordcmds.leaderboard.parameters if p.name == "scope")
    assert [c.value for c in param.choices] == ["all", "members", "own"]
    assert all(len(c.name) <= 100 for c in param.choices) and len(param.description) <= 100


# ---------------------------------------------------------------------------
# generate_leaderboard_text: DB roster, default scope, one history load per month
# ---------------------------------------------------------------------------

def _db_with_roster(roster_by_clan: Dict[str, Dict[str, str]]) -> MagicMock:
    db = MagicMock()
    db.get_player_owners_for_clan_sync = MagicMock(side_effect=lambda tag: roster_by_clan.get(tag, {}))
    db.get_player_attack_history_sync = MagicMock(return_value=[])
    return db


def test_get_leaderboard_roster_reads_every_family_clan_from_the_db(monkeypatch):
    import QBhelperfunctions as qh

    db = _db_with_roster({"#C1": {"#A": "UNASSIGNED", "#B": "55"}, "#C2": {"#C": "UNASSIGNED"}})
    monkeypatch.setattr(qh.CACHE, "db_manager", db)
    monkeypatch.setattr(qh.CACHE, "clan_families", {"FAM": {"name": "Fam", "clans": ["#C1", "#C2"]}})

    assert qh.get_leaderboard_roster("FAM") == {"#A", "#B", "#C"}
    assert qh.get_leaderboard_roster("#C1") == {"#A", "#B"}


def test_scheduled_posts_default_to_scope_all_with_the_db_roster(monkeypatch):
    """The scheduled subscription loop calls generate_leaderboard_text() without a scope — that
    must now mean "all" (past members kept), with the roster from the DB, and each month's
    history loaded once (it used to be loaded twice, doubling the cross-clan query)."""
    import QBhelperfunctions as qh

    db = _db_with_roster({"#C1": {"#A": "UNASSIGNED"}})
    monkeypatch.setattr(qh.CACHE, "db_manager", db)
    monkeypatch.setattr(qh.CACHE, "clan_families", {})
    monkeypatch.setattr(qh.CACHE, "get_temp_war_stats", lambda tag: {})
    monkeypatch.setattr(qh.CACHE, "get_clan_name", lambda tag, default=None: "Clan One")
    loads: List[tuple[str, Any]] = []

    def fake_rows(clan_tag, month, year, cwl_season, *, scope="own", member_player_tags=None):
        loads.append((scope, member_player_tags))
        return [{"WarID": "W1", "PlayerID": "#GONE", "Player": "Past Member", "Stars": 3, "Attacks": 1,
                 "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2,
                 "Date": "2026-09-05T10:00", "TH_lvl": 16}]

    monkeypatch.setattr(qh, "_load_history_rows", fake_rows)

    text = qh.generate_leaderboard_text("#C1", month=9, year=2026, mode="attack")

    assert loads == [("all", {"#A"})]  # scope all, DB roster, ONE load for the one month
    assert "Past Member" in text
