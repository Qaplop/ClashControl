"""Tracker #0114 — the "Ersatzbank"/Bench CWL sign-up status.

A guild switches sign-up mode (guild_config.cwl_signup_mode: 'standard' | 'extended'), but WHO may
be offered the Bench status is player-based: a player on any extended-sign-up server keeps the
option wherever their single per-season sign-up DM happens to come from. Statuses themselves are
never rewritten per guild — every screen shows what the player really answered.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest


def _guilds(monkeypatch, modes: Dict[str, str], members: Optional[Dict[str, List[int]]] = None):
    """CACHE.server_config with the given per-guild sign-up modes, plus a fake bot whose guilds
    hold the given member ids."""
    import sys
    from qapbot.cache_manager import CACHE

    CACHE.server_config.clear()
    for guild_id, mode in modes.items():
        CACHE.server_config[guild_id] = {"cwl_signup_mode": mode}

    members = members or {}

    def _get_guild(gid: int):
        if str(gid) not in modes:
            return None
        guild = MagicMock()
        guild.id = gid
        guild.get_member = MagicMock(
            side_effect=lambda uid: MagicMock() if uid in members.get(str(gid), []) else None
        )
        return guild

    fake_bot = MagicMock()
    fake_bot.get_guild = MagicMock(side_effect=_get_guild)
    monkeypatch.setitem(sys.modules, "QBcore", MagicMock(bot=fake_bot))


# ---------------------------------------------------------------------------
# cwl_bench_enabled_for / is_cwl_extended_signup
# ---------------------------------------------------------------------------

def test_extended_guild_offers_bench_to_everyone(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import cwl_bench_enabled_for, is_cwl_extended_signup

    _guilds(monkeypatch, {"1": "extended"})
    assert is_cwl_extended_signup(1) is True
    assert cwl_bench_enabled_for("777", 1) is True
    assert cwl_bench_enabled_for(None, 1) is True  # an unlinked account on an extended board


def test_standard_guild_offers_bench_to_a_member_of_an_extended_guild(monkeypatch):
    """The Q4 decision: the option follows the player. Their one enrollment DM may be sent by the
    standard guild, and it must still carry the button."""
    from qapbot.QBdiscocmdshelper_cwl import cwl_bench_enabled_for

    _guilds(monkeypatch, {"1": "standard", "2": "extended"}, members={"2": [777]})
    assert cwl_bench_enabled_for("777", 1) is True
    assert cwl_bench_enabled_for("888", 1) is False  # not a member of the extended guild


def test_no_extended_guild_anywhere_means_no_bench(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import cwl_bench_enabled_for, is_cwl_extended_signup

    _guilds(monkeypatch, {"1": "standard", "2": "standard"}, members={"2": [777]})
    assert is_cwl_extended_signup(1) is False
    assert cwl_bench_enabled_for("777", 1) is False
    assert cwl_bench_enabled_for(None, 1) is False


def test_unparsable_discord_id_is_not_bench_enabled(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import cwl_bench_enabled_for

    _guilds(monkeypatch, {"1": "standard", "2": "extended"}, members={"2": [777]})
    assert cwl_bench_enabled_for("not-an-id", 1) is False


# ---------------------------------------------------------------------------
# Seeding precedence and the settled set
# ---------------------------------------------------------------------------

def test_always_bench_preference_seeds_auto_passive():
    from qapbot.QBdiscocmdshelper_cwl import resolve_seeded_cwl_signup_status

    assert resolve_seeded_cwl_signup_status(None, False, False, True) == ("auto_passive", "auto_bench")


def test_seed_precedence_optout_beats_bench_beats_optin():
    from qapbot.QBdiscocmdshelper_cwl import resolve_seeded_cwl_signup_status

    assert resolve_seeded_cwl_signup_status(None, True, True, True)[0] == "declined"
    assert resolve_seeded_cwl_signup_status(None, False, True, True)[0] == "auto_passive"
    assert resolve_seeded_cwl_signup_status(None, False, True, False)[0] == "auto_confirmed"
    assert resolve_seeded_cwl_signup_status(None, False, False, False)[0] == "pending"


def test_a_real_answer_still_beats_the_bench_preference():
    from qapbot.QBdiscocmdshelper_cwl import resolve_seeded_cwl_signup_status

    status, _ = resolve_seeded_cwl_signup_status({"status": "declined"}, False, False, True)
    assert status == "declined"


def test_bench_statuses_count_as_answered():
    """Otherwise "Notify New Pool Members" would invite a bench player all over again."""
    from qapbot.QBdiscocmdshelper_cwl import CWL_SETTLED_STATUSES

    assert {"passive", "auto_passive"} <= CWL_SETTLED_STATUSES
    assert "pending" not in CWL_SETTLED_STATUSES


# ---------------------------------------------------------------------------
# DM views
# ---------------------------------------------------------------------------

def test_signup_view_has_two_buttons_without_bench_and_three_with():
    from qapbot.ui_cwl_roster import build_cwl_signup_response_view

    assert len(build_cwl_signup_response_view(1, "#P1", 5).children) == 2
    view = build_cwl_signup_response_view(1, "#P1", 5, bench=True)
    assert [c.custom_id.split(":")[2] for c in view.children] == ["confirm", "passive", "optout"]


def test_reminder_view_keeps_three_buttons_per_account_on_one_row():
    from qapbot.ui_cwl_roster import build_cwl_reminder_response_view

    accounts = [{"player_tag": f"#P{i}", "player_name": f"P{i}"} for i in range(5)]
    view = build_cwl_reminder_response_view(1, accounts, 5, bench=True)

    assert len(view.children) == 15
    for row in range(5):
        assert len([c for c in view.children if c.row == row]) == 3  # Discord's cap is 5


def test_custom_id_template_parses_the_passive_action():
    import re
    from qapbot.ui_cwl_roster import CWL_REMINDER_RESPONSE_TEMPLATE, CWL_SIGNUP_RESPONSE_TEMPLATE

    for template, prefix in (
        (CWL_SIGNUP_RESPONSE_TEMPLATE, "cwl:signup"), (CWL_REMINDER_RESPONSE_TEMPLATE, "cwl:remind"),
    ):
        match = re.match(template, f"{prefix}:passive:42:#ABC123")
        assert match is not None and match["action"] == "passive"
        # every previously-sent DM keeps working
        assert re.match(template, f"{prefix}:confirm:42:#ABC123") is not None


@pytest.mark.asyncio
async def test_clicking_bench_stores_passive_globally(monkeypatch):
    from qapbot.cache_manager import CACHE
    import qapbot.QBdiscocmdshelper_cwl as helper
    import qapbot.web_bridge as wb
    from qapbot.ui_cwl_roster import _apply_cwl_signup_response

    written: Dict[str, Any] = {}
    db = MagicMock()
    db.get_cwl_event_by_id_sync = MagicMock(
        return_value={"id": 7, "guild_id": "900", "cwl_season": "2026-10", "status": "signup_open"}
    )
    db.get_cwl_signup_sync = MagicMock(return_value={
        "player_tag": "#P1", "player_name": "Alpha", "dmed_discord_id": "55",
        "preferred_league_rank": None, "status": "pending",
    })
    db.get_player_links_sync = MagicMock(return_value={"#P1": {"discord_id": "55"}})
    db.upsert_cwl_signup_sync = MagicMock(
        side_effect=lambda *a, **k: written.update({"status": a[6], "source": a[5]})
    )
    monkeypatch.setattr(CACHE, "db_manager", db)
    propagate = AsyncMock(return_value=[])
    monkeypatch.setattr(helper, "propagate_cwl_player_response", propagate)
    monkeypatch.setattr(wb, "bump_enrollment_version", AsyncMock())

    result = await _apply_cwl_signup_response(7, "#P1", "passive", "55")

    assert result["code"] == "ok"
    assert written == {"status": "passive", "source": "template_passive"}
    # The global row (and therefore every other guild's mirror) gets the same real status.
    assert propagate.await_args.args[2] == "passive"


# ---------------------------------------------------------------------------
# Settings screen + roster announcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwl_settings_embed_shows_the_signup_mode(monkeypatch):
    from qapbot.cache_manager import CACHE
    from qapbot.QBdiscocmdshelper_cwl import format_clan_management_cwl_settings

    guild = MagicMock()
    guild.id = 9840
    guild.name = "The QCrew"
    guild.get_role = MagicMock(return_value=None)
    guild.get_channel = MagicMock(return_value=None)

    CACHE.server_config["9840"] = {"cwl_signup_mode": "extended"}
    embed, _, _, _ = await format_clan_management_cwl_settings(guild)
    blocks = "\n".join(f.value or "" for f in embed.fields)
    assert "Extended" in blocks

    CACHE.server_config["9840"] = {}
    embed, _, _, _ = await format_clan_management_cwl_settings(guild)
    blocks = "\n".join(f.value or "" for f in embed.fields)
    assert "Standard" in blocks


def test_settings_screen_offers_the_signup_mode_toggle():
    from qapbot.cache_manager import CACHE
    from qapbot.ui_cwl_roster import add_cwl_settings_components

    CACHE.server_config["9841"] = {}
    guild = MagicMock()
    guild.id = 9841
    view = MagicMock()
    view.guild = guild
    added: List[Any] = []
    view.add_item = MagicMock(side_effect=added.append)

    add_cwl_settings_components(view, 9841)

    labels = [getattr(c, "label", "") for c in added]
    assert any("Extended Sign-up" in (label or "") for label in labels)


def test_roster_announcement_marks_a_bench_player(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import _build_cwl_roster_account_lines

    account = {
        "player_name": "Alpha", "clan_name": "StayCalm", "clan_tag": "#CLAN1",
        "cwl_start_at": None, "in_clan": True, "current_clan_name": None, "current_clan_tag": None,
    }
    plain = _build_cwl_roster_account_lines([account], "55", 1)[0]
    bench = _build_cwl_roster_account_lines([{**account, "signup_status": "passive"}], "55", 1)[0]

    assert "🪑" not in plain
    assert bench.startswith(plain) and "🪑" in bench
