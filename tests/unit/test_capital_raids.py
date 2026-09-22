"""Clan Capital raid weekend tracking, leaderboards and reminders (tracker #0115).

Design: plans/tracker-0115-capital-raid-leaderboards.md. Game rules:
qapbot/docs/COC_GAME_MECHANICS.md § Clan Capital Raid Weekends.
"""
from __future__ import annotations

import ast
import inspect
import os
import textwrap
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from qapbot.cache_manager import CACHE
from qapbot.constants import coc_timestamp_to_iso, current_raid_season_bounds, is_capital_raid_window
from qapbot.db_manager import WarHistoryDB
from qapbot.formatting import MODE_REGISTRY, apply_cwl_mode_suffix, render_leaderboard
import qapbot.war_notifications as wn
import QBhelperfunctions as qh

S1, E1 = "2026-09-11T07:00:00Z", "2026-09-14T07:00:00Z"   # last weekend
S2, E2 = "2026-09-18T07:00:00Z", "2026-09-21T07:00:00Z"   # this weekend


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _member(tag: str, name: str, attacks: int, loot: int, bonus: int = 0) -> Dict[str, Any]:
    """One API members[] entry (shape verified against a live capitalraidseasons response)."""
    return {"tag": tag, "name": name, "attacks": attacks, "attackLimit": 5,
            "bonusAttackLimit": bonus, "capitalResourcesLooted": loot}


def _api_item(start_iso: str, end_iso: str, state: str, members: List[Dict[str, Any]], **totals: int) -> Dict[str, Any]:
    fmt = lambda iso: _utc(iso.rstrip("Z")).strftime("%Y%m%dT%H%M%S.000Z")  # noqa: E731
    item = {"state": state, "startTime": fmt(start_iso), "endTime": fmt(end_iso), "members": members,
            "capitalTotalLoot": sum(m["capitalResourcesLooted"] for m in members),
            "totalAttacks": sum(m["attacks"] for m in members),
            "raidsCompleted": 0, "enemyDistrictsDestroyed": 0, "offensiveReward": 0, "defensiveReward": 0}
    item.update(totals)
    return item


@pytest.fixture
async def db(tmp_path):
    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "raid_test.db"))
    try:
        yield manager
    finally:
        await manager.close()


@pytest.fixture
def cache(monkeypatch, db):
    """Point CACHE at the temp DB and give it deterministic, empty side state."""
    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(CACHE, "clan_families", {})
    monkeypatch.setattr(CACHE, "notification_state", {})
    monkeypatch.setattr(CACHE, "user_accounts", {})
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "get_clan_name", lambda tag, default=None: default)
    return CACHE


def _set_roster(monkeypatch, roster: Dict[str, str]) -> None:
    clan = SimpleNamespace(members=[SimpleNamespace(tag=t, name=n) for t, n in roster.items()])
    monkeypatch.setattr(CACHE, "coc_clan_cache", SimpleNamespace(get_clan=AsyncMock(return_value=clan)))


def _set_api(monkeypatch, item: Optional[Dict[str, Any]]) -> AsyncMock:
    mock = AsyncMock(return_value={"items": [item] if item else [], "paging": {"cursors": {}}})
    monkeypatch.setattr(CACHE, "get_capital_raid_seasons_from_api", mock)
    return mock


# ── clock helpers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now, bounds, window", [
    ("2026-09-17T23:59", (S1, E1), False),   # Thursday night
    ("2026-09-18T06:59", (S1, E1), False),   # Friday before start
    ("2026-09-18T07:00", (S2, E2), True),    # start
    ("2026-09-20T12:00", (S2, E2), True),    # Sunday
    ("2026-09-21T06:59", (S2, E2), True),    # last minute
    ("2026-09-21T07:00", (S2, E2), False),   # end (exclusive)
    ("2026-09-22T10:00", (S2, E2), False),   # Tuesday
])
def test_raid_clock(now, bounds, window):
    assert current_raid_season_bounds(_utc(now)) == bounds
    assert is_capital_raid_window(_utc(now)) is window


def test_coc_timestamp_to_iso():
    assert coc_timestamp_to_iso("20260918T070000.000Z") == S2
    assert coc_timestamp_to_iso("garbage") == ""


# ── DB: snapshot + upsert (eligibility, leavers, freezing) ────────────────────

async def test_snapshot_fixes_eligibility_and_upsert_rules(db):
    assert await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "a", "#B": "b", "#L": "leaver"}, 5)
    # Second call is a no-op: a later joiner (#NEW) never becomes eligible.
    assert not await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "a", "#NEW": "new"}, 5)

    finalized = await db.upsert_capital_raid_season(
        "#C", S2, E2, "ongoing", {"total_attacks": 3},
        [_member("#A", "a", 3, 100, bonus=1)], {"#A": "a", "#B": "b", "#NEW": "new"},
    )
    assert finalized is False
    rows = {r["player_tag"]: r for r in db.get_capital_raid_rows_sync(clan_tags=["#C"], season_starts=[S2])}
    assert set(rows) == {"#A", "#B"}            # leaver's zero row deleted, #NEW never added
    assert rows["#A"]["attacks"] == 3 and rows["#A"]["bonus_attack_limit"] == 1
    assert rows["#B"]["attacks"] == 0 and rows["#B"]["attack_limit"] == 5

    assert await db.upsert_capital_raid_season("#C", S2, E2, "ended", {}, [], {"#A": "a", "#B": "b"}) is True
    # Frozen: nothing changes any more.
    assert await db.upsert_capital_raid_season("#C", S2, E2, "ongoing", {}, [_member("#B", "b", 5, 1)], {}) is False
    rows = {r["player_tag"]: r for r in db.get_capital_raid_rows_sync(clan_tags=["#C"], season_starts=[S2])}
    assert rows["#B"]["attacks"] == 0
    assert db.get_unfinalized_capital_raid_seasons_sync("#C") == []


async def test_attacker_who_left_is_kept(db):
    await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "a"}, 5)
    await db.upsert_capital_raid_season("#C", S2, E2, "ongoing", {}, [_member("#A", "a", 2, 10)], {})
    assert [r["player_tag"] for r in db.get_capital_raid_rows_sync(clan_tags=["#C"], season_starts=[S2])] == ["#A"]


async def test_roster_none_keeps_zero_rows(db):
    await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "a", "#B": "b"}, 5)
    await db.upsert_capital_raid_season("#C", S2, E2, "ongoing", {}, [], None)  # roster fetch failed
    assert len(db.get_capital_raid_rows_sync(clan_tags=["#C"], season_starts=[S2])) == 2


# ── update_capital_raid_for_clan ─────────────────────────────────────────────

async def test_update_snapshots_then_stores_ongoing_then_finalizes(cache, monkeypatch):
    _set_roster(monkeypatch, {"#A": "a", "#B": "b"})
    # Friday 07:10, first run ever, clan hasn't started yet: API still shows last week, which the
    # one-time backfill imports; this weekend is snapshotted and stays pending.
    _set_api(monkeypatch, _api_item(S1, E1, "ended", [_member("#A", "a", 6, 50)]))
    counts = await qh.update_capital_raid_for_clan("#C", _utc("2026-09-18T07:10"))
    assert counts["snapshot"] == 1 and counts["backfilled"] == 1 and counts["finalized"] == 1
    assert [s["state"] for s in cache.db_manager.get_unfinalized_capital_raid_seasons_sync("#C")] == ["pending"]

    _set_api(monkeypatch, _api_item(S2, E2, "ongoing", [_member("#A", "a", 2, 20)]))
    counts = await qh.update_capital_raid_for_clan("#C", _utc("2026-09-19T10:00"))
    assert counts == {"snapshot": 0, "fetched": 1, "written": 1, "finalized": 0, "backfilled": 0}

    cache.notification_state[qh.raid_notification_key("#C", S2)] = {"notified_players": {"B": {}}}
    _set_api(monkeypatch, _api_item(S2, E2, "ended", [_member("#A", "a", 6, 60)], offensiveReward=209, defensiveReward=211))
    counts = await qh.update_capital_raid_for_clan("#C", _utc("2026-09-21T07:05"))
    assert counts["finalized"] == 1
    assert qh.raid_notification_key("#C", S2) not in cache.notification_state  # dedup state dropped


async def test_friday_catchup_finalizes_last_week(cache, monkeypatch):
    """Monday outage: last week stays unfinalized. On Friday the new snapshot must not hide it
    while the API still serves last week's members[] (plan §3 "Friday catch-up")."""
    db = cache.db_manager
    await db.snapshot_capital_raid_roster("#C", S1, E1, {"#A": "a", "#B": "b"}, 5)
    await db.upsert_capital_raid_season("#C", S1, E1, "ongoing", {}, [_member("#A", "a", 2, 20)], {"#A": "a", "#B": "b"})
    _set_roster(monkeypatch, {"#A": "a", "#B": "b"})
    _set_api(monkeypatch, _api_item(S1, E1, "ended", [_member("#A", "a", 6, 70)]))

    counts = await qh.update_capital_raid_for_clan("#C", _utc("2026-09-18T07:05"))
    assert counts["snapshot"] == 1 and counts["finalized"] == 1
    open_seasons = db.get_unfinalized_capital_raid_seasons_sync("#C")
    assert [(s["season_start"], s["state"]) for s in open_seasons] == [(S2, "pending")]
    last = db.get_capital_raid_season_rows_sync(["#C"], [S1])[0]
    assert last["state"] == "ended"


async def test_season_never_started_is_closed_as_no_result(cache, monkeypatch):
    db = cache.db_manager
    await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "a"}, 5)
    _set_roster(monkeypatch, {"#A": "a"})
    _set_api(monkeypatch, _api_item(S1, E1, "ended", []))  # API never shows this weekend
    await qh.update_capital_raid_for_clan("#C", _utc("2026-09-21T07:05"))
    assert db.get_capital_raid_season_rows_sync(["#C"], [S2])[0]["state"] == "no_result"
    text = qh.generate_leaderboard_text("#C", month=9, year=2026, mode="raidmissed", style="terminal")
    assert "#A" not in text and "a " not in text.split("\n", 2)[-1]


async def test_member_clans_gate_polls_a_new_clan_exactly_once_between_seasons(cache, monkeypatch):
    """A clan that never raids gets ONE out-of-weekend poll (first-run backfill), then none until
    Friday — the project owner's gate (plan §3.2)."""
    monkeypatch.setattr(cache, "server_config", {"1": {"member_clans": ["#C"], "member_families": []}})
    api = _set_api(monkeypatch, None)     # clan has never raided
    _set_roster(monkeypatch, {"#A": "a"})
    totals = await qh.update_capital_raids_for_member_clans(_utc("2026-09-22T10:00"))
    assert totals["clans"] == 1 and totals["backfilled"] == 1 and api.await_count == 1
    for day in ("2026-09-22T10:05", "2026-09-23T10:00", "2026-09-24T22:00"):
        await qh.update_capital_raids_for_member_clans(_utc(day))
    assert api.await_count == 1           # never polled again between seasons
    # The marker is a 'no_result' season: invisible to every leaderboard.
    assert cache.db_manager.get_capital_raid_season_rows_sync(["#C"], [S2])[0]["state"] == "no_result"
    assert "No raid weekends recorded yet" in qh.generate_leaderboard_text(
        "#C", month=None, year=None, mode="raidmissed", style="terminal")


async def test_unfinalized_season_is_caught_up_on_tuesday(cache, monkeypatch):
    monkeypatch.setattr(cache, "server_config", {"1": {"member_clans": ["#C"], "member_families": []}})
    api = _set_api(monkeypatch, None)
    _set_roster(monkeypatch, {})
    await cache.db_manager.snapshot_capital_raid_roster("#C", S2, E2, {}, 5)
    totals = await qh.update_capital_raids_for_member_clans(_utc("2026-09-22T10:00"))
    assert totals["clans"] == 1 and totals["backfilled"] == 0 and api.await_count == 1


async def test_first_run_backfill_imports_last_weekend_flagged_late(cache, monkeypatch):
    _set_roster(monkeypatch, {"#A": "Alice", "#B": "Bob"})
    _set_api(monkeypatch, _api_item(S2, E2, "ended", [_member("#A", "Alice", 6, 500, bonus=1)],
                                    offensiveReward=209, defensiveReward=211))
    counts = await qh.update_capital_raid_for_clan("#C", _utc("2026-09-22T10:00"))
    assert counts["backfilled"] == 1 and counts["finalized"] == 1
    stats = qh.calculate_raid_leaderboard("#C", season_start=S2)
    assert stats["#A"]["Loot"] == 500 and stats["#A"]["Medals"] == 209 * 6 + 211   # attackers exact
    missed = qh.generate_leaderboard_text("#C", month=None, year=None, mode="raidmissed", style="terminal")
    assert "Bob" in missed and "after raid start" in missed                         # approximate -> flagged
    # Once imported, the clan is not polled again between seasons.
    api = _set_api(monkeypatch, None)
    await qh.update_capital_raids_for_member_clans(_utc("2026-09-23T10:00"))
    assert api.await_count == 0


async def test_first_run_during_started_weekend_marks_last_week_no_result(cache, monkeypatch):
    """First run on Saturday after the clan started this weekend's raid: last week's members[] are
    gone from the API, so last week gets the marker and this weekend is tracked normally."""
    _set_roster(monkeypatch, {"#A": "a"})
    _set_api(monkeypatch, _api_item(S2, E2, "ongoing", [_member("#A", "a", 2, 20)]))
    await qh.update_capital_raid_for_clan("#C", _utc("2026-09-19T10:00"))
    rows = {r["season_start"]: r["state"] for r in cache.db_manager.get_capital_raid_season_rows_sync(["#C"], [S1, S2])}
    assert rows == {S1: "no_result", S2: "ongoing"}


# ── Leaderboards ─────────────────────────────────────────────────────────────

async def _seed_two_weekends(db) -> None:
    roster = {"#A": "Alice", "#B": "Bob", "#Z": "Zed"}
    await db.snapshot_capital_raid_roster("#C", S1, E1, roster, 5)
    await db.upsert_capital_raid_season("#C", S1, E1, "ended", {"offensive_reward": 209, "defensive_reward": 211},
                                        [_member("#A", "Alice", 6, 500, bonus=1)], roster)
    await db.snapshot_capital_raid_roster("#C", S2, E2, roster, 5)
    await db.upsert_capital_raid_season("#C", S2, E2, "ongoing", {"total_attacks": 3},
                                        [_member("#B", "Bob", 3, 300)], roster)


async def test_calculate_raid_leaderboard_semantics(cache):
    await _seed_two_weekends(cache.db_manager)
    stats = qh.calculate_raid_leaderboard("#C", periods=[(9, 2026)])
    assert stats["#A"]["Medals"] == 209 * 6 + 211 and stats["#A"]["Medals_final"]  # confirmed in-game formula
    assert stats["#B"]["Loot"] == 300 and not stats["#B"]["Medals_final"]           # ongoing: not final
    assert stats["#B"]["Missed"] == 1 and stats["#Z"]["Missed"] == 1                 # ended weekend only
    assert stats["#A"]["Missed"] == 0 and stats["#A"]["Pending"] is True             # ongoing != missed


async def test_raidmissed_sums_and_sorts(cache):
    db = cache.db_manager
    await _seed_two_weekends(db)
    third_s, third_e = "2026-09-04T07:00:00Z", "2026-09-07T07:00:00Z"
    await db.snapshot_capital_raid_roster("#C", third_s, third_e, {"#Z": "Zed", "#A": "Alice"}, 5)
    await db.upsert_capital_raid_season("#C", third_s, third_e, "ended", {}, [_member("#A", "Alice", 5, 1)], None)
    text = qh.generate_leaderboard_text("#C", month=9, year=2026, mode="raidmissed", style="terminal")
    rows = [line for line in text.splitlines() if line.startswith(("Zed", "Bob", "Alice"))]
    assert rows[0].startswith("Zed") and rows[0].split()[-2:] == ["2", "2"]   # Missed 2 of 2 eligible
    assert rows[1].startswith("Bob") and not any(r.startswith("Alice") for r in rows)


async def test_currentraid_footer_only_while_ongoing(cache):
    await _seed_two_weekends(cache.db_manager)
    text = qh.generate_leaderboard_text("#C", month=None, year=None, mode="currentraid", style="terminal")
    assert "raid weekend 18-21 Sep 2026" in text and "Not attacked yet (2): Alice, Zed" in text.replace("‎", "")
    await cache.db_manager.upsert_capital_raid_season("#C", S2, E2, "ended", {}, [], None)
    text = qh.generate_leaderboard_text("#C", month=None, year=None, mode="currentraid", style="terminal")
    assert "Not attacked yet" not in text and "State: ended" in text


async def test_currentraid_skips_pending_season(cache):
    db = cache.db_manager
    await _seed_two_weekends(db)
    await db.upsert_capital_raid_season("#C", S2, E2, "ended", {}, [], None)
    await db.snapshot_capital_raid_roster("#C", "2026-09-25T07:00:00Z", "2026-09-28T07:00:00Z", {"#A": "Alice"}, 5)
    text = qh.generate_leaderboard_text("#C", month=None, year=None, mode="currentraid", style="terminal")
    assert "18-21 Sep 2026" in text  # the not-yet-started weekend is skipped


async def test_weekend_counts_toward_month_of_its_friday(cache):
    db = cache.db_manager
    s, e = "2026-10-30T07:00:00Z", "2026-11-02T07:00:00Z"
    await db.snapshot_capital_raid_roster("#C", s, e, {"#A": "Alice"}, 5)
    await db.upsert_capital_raid_season("#C", s, e, "ended", {}, [_member("#A", "Alice", 5, 9)], None)
    assert "#A" in qh.calculate_raid_leaderboard("#C", periods=[(10, 2026)])
    assert qh.calculate_raid_leaderboard("#C", periods=[(11, 2026)]) == {}


async def test_family_and_scope_all(cache, monkeypatch):
    db = cache.db_manager
    await db.snapshot_capital_raid_roster("#C1", S1, E1, {"#A": "Alice"}, 5)
    await db.upsert_capital_raid_season("#C1", S1, E1, "ended", {}, [_member("#A", "Alice", 5, 100)], None)
    await db.snapshot_capital_raid_roster("#C2", S2, E2, {"#A": "Alice"}, 5)
    await db.upsert_capital_raid_season("#C2", S2, E2, "ended", {}, [_member("#A", "Alice", 4, 50)], None)
    monkeypatch.setattr(cache, "clan_families", {"FAM": {"name": "Fam", "clans": ["#C1", "#C2"]}})
    assert qh.calculate_raid_leaderboard("FAM", periods=[(9, 2026)])["#A"]["Loot"] == 150
    assert qh.calculate_raid_leaderboard("#C1", periods=[(9, 2026)], scope="all",
                                         member_player_tags={"#A"})["#A"]["Loot"] == 150


async def test_late_snapshot_note(cache):
    db = cache.db_manager
    await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "Alice"}, 5)
    await db._conn.execute("UPDATE capital_raid_seasons SET roster_snapshot_at = ?", ("2026-09-18T07:10:00Z",))
    await db._conn.commit()
    await db.upsert_capital_raid_season("#C", S2, E2, "ended", {}, [], None)
    assert "roster snapshot" not in qh.generate_leaderboard_text("#C", month=None, year=None, mode="raidmissed")
    await db._conn.execute("UPDATE capital_raid_seasons SET roster_snapshot_at = ?", ("2026-09-18T12:00:00Z",))
    await db._conn.commit()
    assert "5.0 h after raid start" in qh.generate_leaderboard_text("#C", month=None, year=None, mode="raidmissed")


def test_render_cells_mode_alignment_and_highlight():
    stats = {"#A": {"PlayerID": "#A", "Player": "Alice", "Loot": 1234, "Attacks": 6, "Medals": 1465,
                    "Medals_final": True, "Weekends": 1}}
    text = render_leaderboard("#C", "Clan", " for 09/2026", "", stats, "raid", style="discord",
                              highlight_player_ids={"#A"})
    header = next(l for l in text.splitlines() if l.startswith("Player"))
    row = next(l for l in text.splitlines() if "Alice" in l)
    assert "1465" in row and "206" in row  # 1234 / 6
    assert "\x1b" in row or "[1;33m" in row  # highlighted
    assert len(header) == sum(w for _, w in MODE_REGISTRY["raid"]["columns"]) + len(MODE_REGISTRY["raid"]["columns"]) - 1


@pytest.mark.parametrize("mode", ["raid", "raidmissed"])
async def test_long_raid_table_splits_on_its_header(monkeypatch, caplog, mode):
    """Regression (2026-09-22 DEV log): the splitter only recognized a header containing
    'Stars'/'Attacks', so a >2000-char raid board fell back to a blind half-split with a WARNING."""
    stats = {f"#P{i}": {"PlayerID": f"#P{i}", "Player": f"Player{i:02d}", "Loot": 20000 + i, "Attacks": 6,
                        "Medals": 1465, "Medals_final": True, "Weekends": 1, "Missed": 1} for i in range(80)}
    text = render_leaderboard("#C", "The Marines", " for 09/2026", "", stats, mode, style="discord")
    sent: List[str] = []

    class _Channel:
        async def send(self, content: str) -> Any:
            sent.append(content)
            return SimpleNamespace(id=len(sent))

    async def _retry(op: Any, _name: str) -> Any:
        return await op()

    monkeypatch.setattr(qh, "discord_retry", _retry)
    caplog.set_level("WARNING")
    await qh._split_and_post_leaderboard_helper(_Channel(), text)  # type: ignore[arg-type]
    assert "Could not find player table header" not in caplog.text
    assert len(sent) >= 2 and all(len(m) <= 2000 for m in sent)
    first_lines = sent[0].split("\n")
    assert any(l.startswith("Player ") for l in first_lines) and any(set(l.strip()) <= {"-", " "} and l.strip() for l in first_lines)
    import re
    posted = re.findall(r"Player\d{2}\b", "\n".join(sent))
    assert sorted(posted) == sorted(p["Player"] for p in stats.values())   # every row exactly once


def test_raid_modes_have_no_cwl_variant():
    for mode in ("raid", "currentraid", "raidmissed"):
        assert apply_cwl_mode_suffix(mode, True) == mode


# ── Reminders ────────────────────────────────────────────────────────────────

def _raid_user(discord_id: str, tag: str, *, hours: int = 24, scope: str = "not_attacked", war: bool = False) -> Dict[str, Any]:
    return {discord_id: {"display_name": "U", "players": [{"player_tag": tag}], "watched_players": [],
                         "notification_settings": {"war_reminders": war, "raid_reminders": True,
                                                   "raid_hours_before_end": hours, "raid_reminder_scope": scope,
                                                   "notification_mode": "repeated", "hours_before_end": 4}}}


async def _seed_ongoing(db) -> None:
    await db.snapshot_capital_raid_roster("#C", S2, E2, {"#A": "Alice", "#B": "Bob", "#F": "Full"}, 5)
    await db.upsert_capital_raid_season("#C", S2, E2, "ongoing", {},
                                        [_member("#B", "Bob", 3, 30), _member("#F", "Full", 6, 60, bonus=1)], None)


async def test_get_active_raids_shape(cache):
    await _seed_ongoing(cache.db_manager)
    assert wn._get_active_raids(_utc("2026-09-19T12:00")) == []          # > 24 h before end
    raids = wn._get_active_raids(_utc("2026-09-20T08:00"))
    (clan_tag, key, data), = raids
    assert key == qh.raid_notification_key("#C", S2) and data["kind"] == "raid"
    members = {m["tag"]: m for m in data["clan"]["members"]}
    assert set(members) == {"#A", "#B"}                                  # #F used all 6 (earned bonus)
    assert members["#A"]["attacks_per_member"] == 5 and len(members["#B"]["attacks"]) == 3


@pytest.mark.parametrize("scope, expected", [("not_attacked", {"A"}), ("open_attacks", {"A", "B"})])
async def test_reminder_scope_and_once_per_season(cache, monkeypatch, scope, expected):
    await _seed_ongoing(cache.db_manager)
    users = {**_raid_user("1", "#A", scope=scope), **_raid_user("2", "#B", scope=scope)}
    monkeypatch.setattr(cache, "user_accounts", users)
    monkeypatch.setattr(wn, "_raid_notification_player_index", {"A": "1", "B": "2"})
    monkeypatch.setattr(wn, "_notification_player_index", {})
    (_, key, data), = wn._get_active_raids(_utc("2026-09-20T08:00"))
    picked = wn._get_players_needing_reminders("#C", key, data)
    assert {p["player_tag"].lstrip("#") for p in picked} == expected
    assert all(p["kind"] == "raid" for p in picked)
    if "B" in expected:
        assert next(p for p in picked if p["player_tag"] == "#B")["attacks_remaining"] == 2
    # Once per season even though the users' war mode is "repeated"
    for p in picked:
        cache.notification_state.setdefault(key, {"notified_players": {}})["notified_players"][p["player_tag"].lstrip("#")] = {
            "notification_time": "2000-01-01 00:00:00"}
    assert wn._get_players_needing_reminders("#C", key, data) == []


async def test_reminder_threshold_and_war_only_users(cache, monkeypatch):
    await _seed_ongoing(cache.db_manager)
    monkeypatch.setattr(cache, "user_accounts", _raid_user("1", "#A", hours=12))
    monkeypatch.setattr(wn, "_raid_notification_player_index", {"A": "1"})
    (_, key, data), = wn._get_active_raids(_utc("2026-09-20T08:00"))   # 23 h left
    assert wn._get_players_needing_reminders("#C", key, data) == []   # 12 h threshold not reached
    (_, key, data), = wn._get_active_raids(_utc("2026-09-20T20:00"))  # 11 h left
    assert len(wn._get_players_needing_reminders("#C", key, data)) == 1
    # A user with only war reminders on is not in the raid index -> no raid DM
    monkeypatch.setattr(wn, "_raid_notification_player_index", {})
    monkeypatch.setattr(wn, "_notification_player_index", {"A": "1"})
    assert wn._get_players_needing_reminders("#C", key, data) == []


def test_notification_channel_fallback_and_scope_helper():
    assert wn._notification_channel_id({"war_notification_channel_id": "W"}, "raid") == "W"
    assert wn._notification_channel_id({"war_notification_channel_id": "W", "raid_notification_channel_id": "R"}, "raid") == "R"
    assert wn._notification_channel_id({"raid_notification_channel_id": "R"}, "war") is None
    assert wn._notification_channel_id({}, "raid") is None
    assert wn._in_scope("not_attacked", 0) and not wn._in_scope("not_attacked", 2)
    assert wn._in_scope("open_attacks", 2) and wn._in_scope(None, 2)


async def test_raid_channel_goes_to_member_clan_guild_raid_channel(cache, monkeypatch):
    await _seed_ongoing(cache.db_manager)
    sent: List[Any] = []
    channel = SimpleNamespace(send=AsyncMock(side_effect=lambda **kw: sent.append(kw)))
    import discord
    monkeypatch.setattr(discord, "TextChannel", SimpleNamespace)
    bot = SimpleNamespace(get_channel=lambda cid: channel if cid == 555 else None, fetch_channel=AsyncMock(return_value=None))
    monkeypatch.setattr("QBcore.bot", bot, raising=False)
    monkeypatch.setattr(cache, "subscriptions", {})
    monkeypatch.setattr(cache, "server_config", {
        "1": {"member_clans": ["#C"], "member_families": [], "channel_raid_notifications_enabled": True,
              "raid_notification_threshold_hours": 24, "war_notification_channel_id": "444",
              "raid_notification_channel_id": "555"},
        "2": {"member_clans": [], "member_families": [], "channel_raid_notifications_enabled": True,
              "war_notification_channel_id": "666"},
    })
    monkeypatch.setattr(wn, "CONFIG", SimpleNamespace(is_dev_mode=False, discord_guild_id=0))
    monkeypatch.setattr(cache, "persist_channel_notification", AsyncMock(), raising=False)
    (_, key, data), = wn._get_active_raids(_utc("2026-09-20T08:00"))
    data["hours_remaining"] = 20.0
    count = await wn._send_channel_war_notification("#C", data, wn._get_players_with_attacks_remaining(data))
    assert count == 1 and len(sent) == 1
    field_text = sent[0]["embed"].fields[0].value
    assert "Alice" in field_text and "Bob" not in field_text   # default scope: no attack yet
    assert wn._is_channel_notification_sent(key, "1")


# ── Settings round-trip ──────────────────────────────────────────────────────

async def test_raid_settings_round_trip(db):
    await db.save_user("42", {"display_name": "U", "players": [], "notification_settings": {
        "war_reminders": True, "raid_reminders": True, "raid_hours_before_end": 12, "raid_reminder_scope": "open_attacks"}})
    loaded = await db.get_user("42")
    ns = loaded["notification_settings"]
    assert (ns["raid_reminders"], ns["raid_hours_before_end"], ns["raid_reminder_scope"]) == (True, 12, "open_attacks")

    await db.save_guild_config("7", {"channel_raid_notifications_enabled": True, "raid_notification_threshold_hours": 12,
                                     "raid_notification_scope": "open_attacks", "raid_notification_channel_id": "99"})
    cfg = await db.get_guild_config("7")
    assert (cfg["channel_raid_notifications_enabled"], cfg["raid_notification_threshold_hours"],
            cfg["raid_notification_scope"], cfg["raid_notification_channel_id"]) == (True, 12, "open_attacks", "99")


async def test_existing_users_default_raid_reminders_off(db):
    await db._conn.execute("INSERT INTO users (discord_id, display_name) VALUES ('5', 'x')")
    await db._conn.commit()
    loaded = await db.get_user("5")
    assert loaded["notification_settings"]["raid_reminders"] is False
    assert loaded["notification_settings"]["raid_reminder_scope"] == "not_attacked"


# ── Rule 14 structural guard ─────────────────────────────────────────────────

@pytest.mark.parametrize("fn", [
    WarHistoryDB.get_unfinalized_capital_raid_seasons_sync, WarHistoryDB.get_capital_raid_season_rows_sync,
    WarHistoryDB.get_latest_capital_raid_season_sync, WarHistoryDB.get_capital_raid_rows_sync,
    WarHistoryDB.get_ongoing_capital_raid_candidates_sync, WarHistoryDB._upsert_capital_raid_season_impl,
    WarHistoryDB._snapshot_capital_raid_roster_impl,
])
def test_no_positional_row_access(fn):
    """Cardinal Rule 14: rows must be read by column name, never row[N]."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in ("row", "r", "existing"):
            assert not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)), ast.dump(node)
