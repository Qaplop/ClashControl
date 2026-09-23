# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""/leaderboard scope handling in the command itself (2026-09-23).

scope is now all (default: current + past members) / members (current members only) / own.
The command resolves current rosters only for the roster scopes and only when a requested mode
reads history, and says so under the board when a roster couldn't be loaded.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

import QBdiscordcmds


def _setup(monkeypatch, mock_interaction, *, get_clan: AsyncMock) -> List[Dict[str, Any]]:
    """DM invocation for #CLAN1; returns the list of generate_leaderboard_text kwargs + sent texts."""
    mock_interaction.guild = None
    mock_interaction.guild_id = None
    fake_cache = SimpleNamespace(
        user_accounts={}, clan_families={}, subscriptions={},
        get_all_subscriptions_flat=lambda: {},
        coc_clan_cache=SimpleNamespace(get_clan=get_clan),
        get_clan_name=lambda tag, default=None: "Clan One",
    )
    monkeypatch.setattr(QBdiscordcmds, "CACHE", fake_cache)
    monkeypatch.setattr(QBdiscordcmds, "_get_clan_tag", lambda clan: (1, "#CLAN1"))
    monkeypatch.setattr(QBdiscordcmds, "update_clan_war_info_and_stats", AsyncMock(return_value=True))
    calls: List[Dict[str, Any]] = []

    def fake_generate(*args, **kwargs):
        calls.append(kwargs)
        return "PLAYER TABLE"

    monkeypatch.setattr(QBdiscordcmds, "generate_leaderboard_text", fake_generate)

    async def fake_send_and_track(interaction, content=None, command_name=None, embed=None, ephemeral=False):
        calls.append({"sent": content})

    monkeypatch.setattr(QBdiscordcmds, "send_and_track", fake_send_and_track)
    return calls


def _roster(*tags: str) -> SimpleNamespace:
    return SimpleNamespace(members=[SimpleNamespace(tag=t) for t in tags])


@pytest.mark.discord
@pytest.mark.asyncio
async def test_default_scope_is_all_with_the_current_roster(mock_interaction, monkeypatch):
    get_clan = AsyncMock(return_value=_roster("#P1", "#P2"))
    calls = _setup(monkeypatch, mock_interaction, get_clan=get_clan)

    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1")  # type: ignore[arg-type]

    assert calls[0]["scope"] == "all"
    assert calls[0]["member_player_tags"] == {"#P1", "#P2"}
    assert "⚠️" not in calls[1]["sent"]


@pytest.mark.discord
@pytest.mark.asyncio
async def test_members_scope_is_passed_through_and_unknown_values_fall_back_to_all(mock_interaction, monkeypatch):
    calls = _setup(monkeypatch, mock_interaction, get_clan=AsyncMock(return_value=_roster("#P1")))
    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", scope="members")  # type: ignore[arg-type]
    assert calls[0]["scope"] == "members"

    calls = _setup(monkeypatch, mock_interaction, get_clan=AsyncMock(return_value=_roster("#P1")))
    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", scope="bogus")  # type: ignore[arg-type]
    assert calls[0]["scope"] == "all"


@pytest.mark.discord
@pytest.mark.asyncio
async def test_failed_roster_is_noted_under_the_board(mock_interaction, monkeypatch):
    """Without the roster "members" silently became "own" (past members listed, the opposite of
    what was asked) — the board now says so."""
    calls = _setup(monkeypatch, mock_interaction, get_clan=AsyncMock(side_effect=RuntimeError("API down")))

    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", scope="members")  # type: ignore[arg-type]

    sent = calls[1]["sent"]
    assert "Current roster of Clan One could not be loaded" in sent and "shown as scope OWN" in sent


@pytest.mark.discord
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["currentwar", "currentraid"])
async def test_modes_that_ignore_scope_skip_the_roster_lookup(mock_interaction, monkeypatch, mode):
    get_clan = AsyncMock(side_effect=RuntimeError("must not be called"))
    calls = _setup(monkeypatch, mock_interaction, get_clan=get_clan)
    monkeypatch.setattr(QBdiscordcmds, "update_capital_raid_for_clan", AsyncMock())

    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#CLAN1", mode=mode)  # type: ignore[arg-type]

    get_clan.assert_not_awaited()
    assert "⚠️" not in calls[-1]["sent"]


@pytest.mark.discord
def test_scope_choices_offer_all_members_own():
    param = next(p for p in QBdiscordcmds.leaderboard.parameters if p.name == "scope")
    assert [c.value for c in param.choices] == ["all", "members", "own"]
    assert all(len(c.name) <= 100 for c in param.choices) and len(param.description) <= 100
