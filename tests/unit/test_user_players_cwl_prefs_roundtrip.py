"""CWL preferences survive the CACHE save round-trip (2026-09-22, found while planning #0114).

save_user() rewrites all of a Discord user's user_players rows (DELETE + re-INSERT). Before this
fix it only carried cwl_permanent_optout and the league rank, so "always in"
(cwl_permanent_optin) and "send DM anyway" were reset to 0 by every save — and because
set_cwl_preferences_sync() writes the DB without touching CACHE, even opt-out could be overwritten
by a stale CACHE value.
"""
from __future__ import annotations

import sqlite3

import pytest

from qapbot.db_manager import USER_PLAYER_CWL_PREF_COLUMNS, WarHistoryDB


@pytest.fixture
async def db(tmp_path):
    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "qapbot_test.db"))
    try:
        yield manager
    finally:
        await manager.close()


def _user(players):
    return {"display_name": "Tester", "notification_settings": {}, "players": players, "user_language": "en"}


@pytest.mark.asyncio
async def test_every_cwl_column_of_user_players_is_in_the_preserved_list(db):
    """Structural guard: a new cwl_* preference column that isn't in USER_PLAYER_CWL_PREF_COLUMNS
    would silently be reset by every save_user() — exactly the bug this file exists for."""
    conn = sqlite3.connect(db.db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_players)")}
    finally:
        conn.close()
    assert {c for c in cols if c.startswith("cwl_")} == set(USER_PLAYER_CWL_PREF_COLUMNS)


@pytest.mark.asyncio
async def test_optin_and_dm_anyway_survive_a_save_with_a_stale_cache_dict(db):
    await db.save_user("111", _user([{"player_tag": "#A1", "player_name": "A"}, {"player_tag": "#B1", "player_name": "B"}]))
    db.set_cwl_preferences_sync("111", "#A1", mode="optin")
    db.set_cwl_preferences_sync("111", "#B1", mode="optout", send_dm_anyway=True)

    # The CACHE dict still holds the pre-preference state (no cwl_* keys at all).
    await db.save_user("111", _user([{"player_tag": "#A1", "player_name": "A"}, {"player_tag": "#B1", "player_name": "B"}]))

    prefs = db.get_user_player_cwl_prefs_sync("111")
    assert prefs["#A1"]["cwl_permanent_optin"] is True
    assert prefs["#B1"]["cwl_permanent_optout"] is True
    assert prefs["#B1"]["cwl_optout_send_dm_anyway"] is True


@pytest.mark.asyncio
async def test_get_user_loads_every_preference_column(db):
    await db.save_user("112", _user([{"player_tag": "#C1", "player_name": "C"}]))
    db.set_cwl_preferences_sync("112", "#C1", mode="optin")

    user = await db.get_user("112")
    assert user is not None
    player = user["players"][0]
    for col in USER_PLAYER_CWL_PREF_COLUMNS:
        assert col in player
    assert player["cwl_permanent_optin"] is True


@pytest.mark.asyncio
async def test_account_moved_to_another_discord_id_keeps_its_cache_preferences(db):
    """A tag new under this discord_id has no existing row to preserve, so the incoming dict's
    values are used — that is how an unlinked/relinked account carries its preferences."""
    await db.save_user("113", _user([{
        "player_tag": "#D1", "player_name": "D", "cwl_permanent_optin": True,
        "cwl_default_preferred_league_rank": "Master League I",
    }]))
    prefs = db.get_user_player_cwl_prefs_sync("113")
    assert prefs["#D1"]["cwl_permanent_optin"] is True
    assert prefs["#D1"]["cwl_default_preferred_league_rank"] == "Master League I"
