"""Housekeeping for stored CWL sign-up DM references (2026-09-23).

cwl_player_season_status keeps the message/channel id of each player's sign-up DM so it can later
be re-rendered, upgraded with the bench button or retracted. Players who delete the DM instead of
answering leave references to messages that no longer exist. Two mechanisms clean them up:

- self-healing: a fetch that returns 404 clears that reference immediately;
- nightly: references older than CWL_DM_REF_MAX_AGE_DAYS from a finished season are dropped.

Both clear ONLY the two reference columns — dm_sent and the player's answer stay, so nobody is
ever invited twice because of this.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clashcontrol.db_manager import CWL_DM_REF_MAX_AGE_DAYS, WarHistoryDB


@pytest.fixture
async def db(tmp_path):
    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "refs.db"))
    try:
        yield manager
    finally:
        await manager.close()


def _stamp(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%MZ")


def _season(months_ago: int) -> str:
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month - months_ago
    while month < 1:
        year, month = year - 1, month + 12
    return f"{year:04d}-{month:02d}"


async def _row(db: WarHistoryDB, tag: str, season: str, sent_at: str, message_id: str,
               status: str = "pending") -> None:
    await db._conn.execute(
        """INSERT INTO cwl_player_season_status
           (player_tag, cwl_season, player_name, dmed_discord_id, dm_sent, dm_sent_at,
            dm_sent_via_message_id, dm_sent_via_channel_id, status)
           VALUES (?, ?, ?, '55', 1, ?, ?, '99', ?)""",
        (tag, season, tag, sent_at, message_id, status),
    )
    await db._conn.commit()


async def _get(db: WarHistoryDB, tag: str, season: str) -> dict:
    cursor = await db._conn.execute(
        "SELECT dm_sent, dm_sent_at, dm_sent_via_message_id, dm_sent_via_channel_id, status "
        "FROM cwl_player_season_status WHERE player_tag = ? AND cwl_season = ?",
        (tag, season),
    )
    return dict(await cursor.fetchone())


# ---------------------------------------------------------------------------
# 404 self-healing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clearing_a_dead_message_keeps_dm_sent_and_the_answer(db):
    season = _season(0)
    await _row(db, "#A", season, _stamp(1), "1551144802626576526", status="confirmed")

    assert db.clear_cwl_dm_message_ref_sync("1551144802626576526") == 1

    row = await _get(db, "#A", season)
    assert row["dm_sent_via_message_id"] is None and row["dm_sent_via_channel_id"] is None
    # Still counts as invited (no second DM from "Notify New Pool Members"), answer untouched.
    assert row["dm_sent"] == 1 and row["dm_sent_at"] is not None and row["status"] == "confirmed"


@pytest.mark.asyncio
async def test_one_dead_reminder_dm_clears_every_account_it_covered(db):
    """One reminder DM carries up to five accounts; they all lost the same message."""
    season = _season(0)
    for tag in ("#A", "#B", "#C"):
        await _row(db, tag, season, _stamp(1), "1551477818863525961")
    await _row(db, "#D", season, _stamp(1), "1551477822998978611")

    assert db.clear_cwl_dm_message_ref_sync("1551477818863525961") == 3
    assert (await _get(db, "#D", season))["dm_sent_via_message_id"] == "1551477822998978611"


# ---------------------------------------------------------------------------
# Nightly age-based purge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_old_reference_from_a_finished_season_is_dropped(db):
    old_season = _season(2)
    await _row(db, "#OLD", old_season, _stamp(CWL_DM_REF_MAX_AGE_DAYS + 5), "1551144805944401960")

    assert db.purge_stale_cwl_dm_refs_sync() == 1
    row = await _get(db, "#OLD", old_season)
    assert row["dm_sent_via_message_id"] is None
    assert row["dm_sent"] == 1 and row["status"] == "pending"


@pytest.mark.asyncio
async def test_recent_reference_is_kept(db):
    await _row(db, "#NEW", _season(1), _stamp(CWL_DM_REF_MAX_AGE_DAYS - 5), "1551144808016248905")

    assert db.purge_stale_cwl_dm_refs_sync() == 0
    assert (await _get(db, "#NEW", _season(1)))["dm_sent_via_message_id"] == "1551144808016248905"


@pytest.mark.asyncio
async def test_a_running_season_is_never_touched_however_old_its_dm(db):
    """Enrollment can open weeks before the CWL month; while that month is current (or still
    ahead) the reference may yet be needed for a re-render, the bench upgrade or a retraction."""
    for months_ahead, tag in ((0, "#CUR"), (-1, "#NEXT")):
        await _row(db, tag, _season(months_ahead), _stamp(CWL_DM_REF_MAX_AGE_DAYS + 20), f"15511448100504863{abs(months_ahead)}")

    assert db.purge_stale_cwl_dm_refs_sync() == 0


@pytest.mark.asyncio
async def test_rows_without_a_reference_are_left_alone(db):
    old_season = _season(3)
    await _row(db, "#GONE", old_season, _stamp(90), "1551144812533514282")
    db.clear_cwl_dm_message_ref_sync("1551144812533514282")

    assert db.purge_stale_cwl_dm_refs_sync() == 0


# ---------------------------------------------------------------------------
# The bench upgrade heals what it finds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bench_upgrade_clears_a_deleted_dm_instead_of_retrying_it_forever(monkeypatch):
    """The 2026-09-23 DEV run fetched 43 dead references one per second — and would have
    done so again on every switch to extended sign-up."""
    import sys
    from unittest.mock import AsyncMock, MagicMock

    import discord

    import clashcontrol.QBdiscocmdshelper_cwl as helper
    from clashcontrol.cache_manager import CACHE

    CACHE.server_config.clear()
    CACHE.server_config["1"] = {"cwl_signup_mode": "extended"}
    db_mock = MagicMock()
    db_mock.get_cwl_pending_dm_rows_for_season_sync = MagicMock(return_value=[{
        "player_tag": "#P1", "player_name": "P1", "dmed_discord_id": "777", "status": "pending",
        "dm_sent_via_message_id": "1551144802626576526", "dm_sent_via_channel_id": "10",
        "dm_sent_via_event_id": 7, "dm_sent_via_guild_id": "1",
    }])
    db_mock.clear_cwl_dm_message_ref_sync = MagicMock(return_value=1)
    monkeypatch.setattr(CACHE, "db_manager", db_mock)
    monkeypatch.setattr(helper, "get_current_cwl_event_sync",
                        lambda guild_id: {"id": 7, "cwl_season": "2026-10", "status": "signup_open"})
    monkeypatch.setattr(helper.asyncio, "sleep", AsyncMock())

    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "Unknown Message"))
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    fake_bot = MagicMock(get_channel=MagicMock(return_value=channel), get_guild=MagicMock(return_value=guild))
    monkeypatch.setitem(sys.modules, "QBcore", MagicMock(bot=fake_bot))

    assert await helper.upgrade_pending_cwl_dms_for_bench(1) == 0
    db_mock.clear_cwl_dm_message_ref_sync.assert_called_once_with("1551144802626576526")
