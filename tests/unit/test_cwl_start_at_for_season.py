"""Per-clan CWL start times must lie inside their own season (tracker #0145/#0147/#0148).

The season carry-over copied last season's whole cwl_start_at, so the 2026-10 event held
"2026-09-01T16:15Z" for every clan whose time had been set by hand: the roster DM told players to
switch "before 1 September (a month ago)" and the switch alarms fired straight away. Covers the
helper, the startup repair of already-stored rows, and the "Start CWL" refusal.
"""
from __future__ import annotations

import pytest

from clashcontrol.constants import cwl_start_at_for_season, cwl_start_at_in_season
from clashcontrol.db_manager import WarHistoryDB


class TestCwlStartAtForSeason:
    def test_previous_season_value_keeps_its_time_of_day(self):
        assert cwl_start_at_for_season("2026-09-01T16:15Z", "2026-10") == "2026-10-01T16:15Z"

    def test_value_inside_the_window_is_unchanged(self):
        assert cwl_start_at_for_season("2026-10-02T20:00Z", "2026-10") == "2026-10-02T20:00Z"

    def test_offset_beyond_the_window_is_clamped(self):
        assert cwl_start_at_for_season("2026-09-05T08:00Z", "2026-10") == "2026-10-03T08:00Z"

    def test_year_boundary(self):
        assert cwl_start_at_for_season("2026-12-01T16:15Z", "2027-01") == "2027-01-01T16:15Z"

    @pytest.mark.parametrize("value", [None, "", "garbage"])
    def test_missing_or_unparseable_becomes_official_start(self, value):
        assert cwl_start_at_for_season(value, "2026-10") == "2026-10-01T08:00Z"

    def test_in_season(self):
        assert cwl_start_at_in_season("2026-10-01T16:15Z", "2026-10")
        assert not cwl_start_at_in_season("2026-09-01T16:15Z", "2026-10")
        assert not cwl_start_at_in_season(None, "2026-10")


@pytest.fixture
async def db(tmp_path):
    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "clashcontrol_test.db"))
    try:
        yield manager
    finally:
        await manager.close()


async def _seed_event(db: WarHistoryDB, clans: dict) -> int:
    await db._conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES ('945')")
    for clan_tag in clans:
        await db._conn.execute("INSERT OR IGNORE INTO clans (clan_tag, name) VALUES (?, 'C')", (clan_tag,))
    await db._conn.commit()
    event_id = db.create_cwl_event_sync("945", "2026-10", "admin1")
    db.set_cwl_event_clans_sync(event_id, [
        {"clan_tag": tag, "participating": True, "cwl_start_at": start} for tag, start in clans.items()
    ])
    return event_id


def _starts(db: WarHistoryDB, event_id: int) -> dict:
    return {c["clan_tag"]: c["cwl_start_at"] for c in db.get_cwl_event_clans_sync(event_id)}


class TestRepairStartTimesOutsideSeason:
    @pytest.mark.asyncio
    async def test_moves_stale_value_and_rearms_alarms(self, db):
        event_id = await _seed_event(db, {"#STALE": "2026-09-01T16:15Z", "#GOOD": "2026-10-01T08:00Z"})
        db.upsert_cwl_assignment_sync(event_id, "#P1", "#STALE")
        db.upsert_cwl_assignment_sync(event_id, "#P2", "#GOOD")
        db.bump_cwl_alarm_stage_sync(event_id, "#P1", 2)
        db.bump_cwl_alarm_stage_sync(event_id, "#P2", 1)
        db.mark_cwl_coordinator_reminder_sent_sync(event_id, "#STALE")

        await db._repair_cwl_start_times_outside_season()

        assert _starts(db, event_id) == {"#STALE": "2026-10-01T16:15Z", "#GOOD": "2026-10-01T08:00Z"}
        stages = {a["player_tag"]: a["alarm_stage_sent"] for a in db.get_cwl_assignments_sync(event_id)}
        assert stages == {"#P1": 0, "#P2": 1}  # only the repaired clan's alarms are re-armed
        stale = next(c for c in db.get_cwl_event_clans_sync(event_id) if c["clan_tag"] == "#STALE")
        assert stale["coordinator_reminder_sent_at"] is None

    @pytest.mark.asyncio
    async def test_is_idempotent(self, db):
        event_id = await _seed_event(db, {"#STALE": "2026-09-01T16:15Z"})
        db.upsert_cwl_assignment_sync(event_id, "#P1", "#STALE")
        await db._repair_cwl_start_times_outside_season()
        db.bump_cwl_alarm_stage_sync(event_id, "#P1", 1)  # a real alarm after the repair

        await db._repair_cwl_start_times_outside_season()

        assert _starts(db, event_id) == {"#STALE": "2026-10-01T16:15Z"}
        assert db.get_cwl_assignments_sync(event_id)[0]["alarm_stage_sent"] == 1

    @pytest.mark.asyncio
    async def test_skips_clans_that_already_started(self, db):
        event_id = await _seed_event(db, {"#LOCKED": "2026-09-01T16:15Z"})
        db.mark_cwl_event_clan_locked_sync("#LOCKED", "2026-10")

        await db._repair_cwl_start_times_outside_season()

        assert _starts(db, event_id) == {"#LOCKED": "2026-09-01T16:15Z"}
