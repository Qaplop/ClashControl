"""Persisted CWL guest clans (plans/cwl-guest-clan-persistence.md, 2026-09-22): the
guild_guest_clans table + one-time backfill, CACHE write-through, subscription tracking,
member-role eligibility, the season-still-running removal guard, and the 24h member-list
freshness rule in ensure_cwl_clan_membership_tracked().
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from qapbot.db_manager import WarHistoryDB

GUILD = "111"
FAMILY_CLAN = "#FAM1"
FAMILY_FAMILY_CLAN = "#FAMF1"
GUEST = "#GUEST1"
CANCELLED_GUEST = "#GUEST2"


@pytest.fixture
async def db(tmp_path):
    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "qapbot_test.db"))
    try:
        yield manager
    finally:
        await manager.close()


async def _seed(db: WarHistoryDB) -> Dict[str, int]:
    """A guild with one direct member clan, one family (one clan) and a guest clan on two seasons,
    plus a guest clan only on a cancelled season. Returns season -> event_id."""
    conn = db._conn
    await conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (GUILD,))
    for tag in (FAMILY_CLAN, FAMILY_FAMILY_CLAN, GUEST, CANCELLED_GUEST):
        await conn.execute("INSERT OR IGNORE INTO clans (clan_tag, name) VALUES (?, ?)", (tag, f"Clan {tag}"))
    await conn.execute("INSERT INTO guild_member_clans (guild_id, clan_tag) VALUES (?, ?)", (GUILD, FAMILY_CLAN))
    await conn.execute("INSERT INTO clan_families (family_tag, name, owned_by_guild) VALUES ('FAM', 'Fam', ?)", (GUILD,))
    await conn.execute("INSERT INTO clan_family_members (family_tag, clan_tag) VALUES ('FAM', ?)", (FAMILY_FAMILY_CLAN,))
    await conn.execute("INSERT INTO guild_member_families (guild_id, family_tag) VALUES (?, 'FAM')", (GUILD,))
    await conn.commit()

    event_ids: Dict[str, int] = {}
    for season, clans, status in (
        ("2026-08", [FAMILY_CLAN, FAMILY_FAMILY_CLAN, GUEST], "war"),
        ("2026-09", [FAMILY_CLAN, GUEST], "war"),
        ("2026-10", [CANCELLED_GUEST], "cancelled"),
    ):
        event_id = db.create_cwl_event_sync(GUILD, season, "1")
        assert event_id is not None
        db.set_cwl_event_clans_sync(event_id, [
            {"clan_tag": tag, "roster_size": 15, "tier_order": 0,
             "cwl_start_at": f"{season}-02T08:00Z", "participating": True}
            for tag in clans
        ])
        await conn.execute("UPDATE cwl_events SET status = ? WHERE id = ?", (status, event_id))
        await conn.commit()
        event_ids[season] = event_id
    return event_ids


async def _rerun_backfill(db: WarHistoryDB, reset_marker: bool) -> None:
    if reset_marker:
        await db._conn.execute("DELETE FROM bot_metadata WHERE key = ?", (db.GUILD_GUEST_CLANS_BACKFILL_KEY,))
        await db._conn.commit()
    await db._create_guild_guest_clans_schema()


# ── DB layer ─────────────────────────────────────────────────────────────────

async def test_backfill_seeds_only_real_guests_from_non_cancelled_seasons(db):
    await _seed(db)
    await _rerun_backfill(db, reset_marker=True)

    rows = db.get_all_guild_guest_clans_sync()
    assert rows == {GUILD: {GUEST: {"first_invited_season": "2026-08", "last_invited_season": "2026-09"}}}


async def test_backfill_runs_once_so_removed_guests_are_not_resurrected(db):
    await _seed(db)
    await _rerun_backfill(db, reset_marker=True)
    assert db.delete_guild_guest_clan_sync(GUILD, GUEST) is True

    await _rerun_backfill(db, reset_marker=False)

    assert db.get_all_guild_guest_clans_sync() == {}


async def test_upsert_only_widens_the_season_window(db):
    await _seed(db)
    assert db.upsert_guild_guest_clans_sync(GUILD, {GUEST: "2026-09"})
    assert db.upsert_guild_guest_clans_sync(GUILD, {GUEST: "2026-07"})
    assert db.upsert_guild_guest_clans_sync(GUILD, {GUEST: "2026-11"})
    assert db.upsert_guild_guest_clans_sync(GUILD, {GUEST: "2026-10"})

    assert db.get_all_guild_guest_clans_sync()[GUILD][GUEST] == {
        "first_invited_season": "2026-07", "last_invited_season": "2026-11",
    }


async def test_guest_clan_row_protects_clan_from_orphan_purge(db):
    await db._conn.execute("INSERT INTO clans (clan_tag, name) VALUES (?, 'Lonely')", (GUEST,))
    await db._conn.commit()
    assert await db.is_clan_tag_referenced(GUEST) is False

    db.upsert_guild_guest_clans_sync(GUILD, {GUEST: "2026-10"})

    assert await db.is_clan_tag_referenced(GUEST) is True


async def test_events_containing_clan_carry_every_start_time_of_that_event(db):
    await _seed(db)
    events = db.get_cwl_events_containing_clan_sync(GUILD, GUEST)

    assert [e["cwl_season"] for e in events] == ["2026-09", "2026-08"]
    assert sorted(events[1]["clan_cwl_start_ats"]) == ["2026-08-02T08:00Z"] * 3


# ── Season-still-running guard ───────────────────────────────────────────────

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"cwl_season": "2026-10", "status": "draft"}, True),            # upcoming
        ({"cwl_season": "2026-08", "status": "war"}, False),             # past month
        ({"cwl_season": "2026-10", "status": "cancelled"}, False),       # cancelled never blocks
        # current month: default start (1st 08:00) + 9 days is long over on the 22nd
        ({"cwl_season": "2026-09", "status": "war"}, False),
        # current month with a late start time: still inside the 9-day window
        ({"cwl_season": "2026-09", "status": "war", "clan_cwl_start_ats": ["2026-09-01T08:00Z", "2026-09-15T08:00Z"]}, True),
        ({"cwl_season": "2026-09", "status": "war", "clan_cwl_start_ats": ["not-a-date"]}, False),
    ],
)
def test_is_cwl_event_active_or_upcoming(event: Dict[str, Any], expected: bool):
    from qapbot.QBdiscocmdshelper_cwl import is_cwl_event_active_or_upcoming

    assert is_cwl_event_active_or_upcoming(event, now=NOW) is expected


# ── CACHE write-through, tracking, removal ───────────────────────────────────

@pytest.fixture
def cache(monkeypatch, db):
    from qapbot.cache_manager import CACHE

    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(CACHE, "server_config", {GUILD: {"member_clans": [FAMILY_CLAN], "member_families": ["FAM"]}})
    monkeypatch.setattr(CACHE, "clan_families", {"FAM": {"name": "Fam", "clans": [FAMILY_FAMILY_CLAN], "owned_by_guild": GUILD}})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(CACHE, "guild_guest_clans", {})
    monkeypatch.setattr(CACHE, "clan_name_cache", {
        tag: {"name": f"Clan {tag}", "has_active_subscriptions": False, "track_war_updates": False}
        for tag in (FAMILY_CLAN, FAMILY_FAMILY_CLAN, GUEST, CANCELLED_GUEST)
    })
    return CACHE


async def test_register_persists_guests_and_makes_them_tracked(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import register_cwl_guest_clans_for_event

    event_ids = await _seed(db)
    newly_added = await register_cwl_guest_clans_for_event(int(GUILD), event_ids["2026-09"], "2026-09")

    assert newly_added == [GUEST]
    assert cache.get_guild_guest_clan_tags(GUILD) == {GUEST}
    assert db.get_all_guild_guest_clans_sync()[GUILD][GUEST]["last_invited_season"] == "2026-09"
    assert cache.clan_name_cache[GUEST]["has_active_subscriptions"] is True
    assert cache._calculate_subscription_status(GUEST) is True
    # Re-saving the same season is a no-op
    assert await register_cwl_guest_clans_for_event(int(GUILD), event_ids["2026-09"], "2026-09") == []


async def test_guest_clan_stays_out_of_the_cwl_family(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags

    await cache.register_guild_guest_clans(GUILD, {GUEST: "2026-09"})

    assert GUEST not in resolve_guild_member_clan_tags(int(GUILD))


async def test_removal_blocked_while_a_season_is_upcoming(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import remove_guild_guest_clan_checked

    await _seed(db)
    upcoming = db.create_cwl_event_sync(GUILD, "2099-01", "1")
    db.set_cwl_event_clans_sync(upcoming, [{"clan_tag": GUEST, "roster_size": 15, "tier_order": 0, "participating": False}])
    await cache.register_guild_guest_clans(GUILD, {GUEST: "2099-01"})

    result = await remove_guild_guest_clan_checked(int(GUILD), GUEST)

    assert result == {"ok": False, "error": "season_active", "seasons": ["2099-01"]}
    assert cache.get_guild_guest_clan_tags(GUILD) == {GUEST}


async def test_removal_of_past_guest_clears_db_cache_and_tracking(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import remove_guild_guest_clan_checked

    await _seed(db)  # GUEST only on 2026-08/2026-09 — both over by the time this test runs
    await cache.register_guild_guest_clans(GUILD, {GUEST: "2026-09"})
    assert cache.clan_name_cache[GUEST]["has_active_subscriptions"] is True

    result = await remove_guild_guest_clan_checked(int(GUILD), GUEST)

    assert result["ok"] is True
    assert cache.get_guild_guest_clan_tags(GUILD) == set()
    assert db.get_all_guild_guest_clans_sync() == {}
    assert cache.clan_name_cache[GUEST]["has_active_subscriptions"] is False


async def test_removal_of_unknown_clan_is_refused(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import remove_guild_guest_clan_checked

    assert (await remove_guild_guest_clan_checked(int(GUILD), GUEST))["error"] == "not_a_guest"


async def test_overview_lists_guests_with_lock_state(db, cache):
    from qapbot.QBdiscocmdshelper_cwl import get_guild_guest_clans_overview_sync

    await _seed(db)
    await cache.register_guild_guest_clans(GUILD, {GUEST: "2026-09", CANCELLED_GUEST: "2026-10"})

    rows = {r["clan_tag"]: r for r in get_guild_guest_clans_overview_sync(int(GUILD))}

    assert rows[GUEST]["clan_name"] == f"Clan {GUEST}"
    assert rows[GUEST]["blocking_seasons"] == []
    assert rows[CANCELLED_GUEST]["blocking_seasons"] == []  # only on a cancelled season


# ── Member-role eligibility ──────────────────────────────────────────────────

def test_guest_clan_member_is_eligible_for_member_role(cache):
    from qapbot.QBdiscocmdshelper import is_player_in_member_clans

    assert is_player_in_member_clans(GUEST, int(GUILD)) is False
    cache.guild_guest_clans[GUILD] = {GUEST: {"first_invited_season": "2026-09", "last_invited_season": "2026-09"}}
    assert is_player_in_member_clans(GUEST, int(GUILD)) is True
    # ...but only on the guild that invited it
    assert is_player_in_member_clans(GUEST, 999) is False


async def test_role_sync_grants_member_role_but_not_coc_leader_role_to_guest(monkeypatch, cache):
    import discord

    from qapbot import guild_role_manager

    cache.guild_guest_clans[GUILD] = {GUEST: {"first_invited_season": "2026-09", "last_invited_season": "2026-09"}}
    cache.server_config[GUILD].update({
        "role_system_enabled": True, "member_role_id": "500",
        "coc_role_enabled": True, "coc_role_leader_id": "600",
    })
    monkeypatch.setattr(cache, "user_accounts", {
        "42": {"players": [{"player_tag": "#P1", "current_clan_tag": GUEST, "coc_role": "leader", "verified": True}]},
    })

    member_role = MagicMock(spec=discord.Role, id=500)
    leader_role = MagicMock(spec=discord.Role, id=600)
    guild = MagicMock(spec=discord.Guild)
    guild.id = int(GUILD)
    guild.get_role = MagicMock(side_effect=lambda rid: {500: member_role, 600: leader_role}.get(rid))
    member = MagicMock(spec=discord.Member)
    member.roles = []
    assigned: list = []
    monkeypatch.setattr(guild_role_manager, "assign_role_to_member", AsyncMock(side_effect=lambda m, r, reason="": assigned.append(r)))
    monkeypatch.setattr(guild_role_manager, "remove_role_from_member", AsyncMock())

    await guild_role_manager.sync_roles_for_user(guild, GUILD, 42, member=member)

    assert member_role in assigned
    assert leader_role not in assigned


# ── 24h member-list freshness ────────────────────────────────────────────────

def test_member_list_freshness(monkeypatch, cache):
    from qapbot.QBdiscocmdshelper_cwl import _cwl_clan_member_list_is_fresh

    fake_coc = MagicMock()
    fake_coc.members_refreshed_at = {GUEST: NOW - timedelta(hours=2)}
    monkeypatch.setattr(cache, "coc_clan_cache", fake_coc)
    assert _cwl_clan_member_list_is_fresh(GUEST, now=NOW) is True

    fake_coc.members_refreshed_at = {GUEST: NOW - timedelta(hours=30)}
    assert _cwl_clan_member_list_is_fresh(GUEST, now=NOW) is False

    # Restart-safe fallback: a tracked clan fetched recently had its members updated by that fetch
    cache.clan_name_cache[GUEST].update({"has_active_subscriptions": True, "last_checked_via_api": (NOW - timedelta(hours=5)).isoformat()})
    assert _cwl_clan_member_list_is_fresh(GUEST, now=NOW) is True
    cache.clan_name_cache[GUEST]["has_active_subscriptions"] = False
    assert _cwl_clan_member_list_is_fresh(GUEST, now=NOW) is False


async def test_ensure_refetches_a_guest_clan_whose_member_list_is_stale(monkeypatch, db, cache):
    from qapbot.QBdiscocmdshelper_cwl import ensure_cwl_clan_membership_tracked

    await db._conn.execute("INSERT OR IGNORE INTO clans (clan_tag, name) VALUES (?, 'G')", (GUEST,))
    await db._conn.execute("INSERT OR IGNORE INTO users (discord_id, display_name) VALUES ('UNASSIGNED', 'UNASSIGNED')")
    await db._conn.execute(
        "INSERT INTO user_players (discord_id, player_tag, player_name, current_clan_tag) VALUES ('UNASSIGNED', '#P1', 'P', ?)",
        (GUEST,),
    )
    await db._conn.commit()

    fake_coc = MagicMock()
    fake_coc.get_clan = AsyncMock(return_value=MagicMock())
    fake_coc.update_player_info_in_user_accounts = AsyncMock()
    fake_coc.members_refreshed_at = {GUEST: datetime.now(timezone.utc) - timedelta(days=3)}
    monkeypatch.setattr(cache, "coc_clan_cache", fake_coc)
    monkeypatch.setattr(cache, "coc_client", MagicMock())

    await ensure_cwl_clan_membership_tracked([GUEST])
    fake_coc.get_clan.assert_awaited_once_with(GUEST)

    fake_coc.get_clan.reset_mock()
    fake_coc.members_refreshed_at = {GUEST: datetime.now(timezone.utc) - timedelta(hours=1)}
    await ensure_cwl_clan_membership_tracked([GUEST])
    fake_coc.get_clan.assert_not_awaited()
