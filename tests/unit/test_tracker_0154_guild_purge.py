"""Tracker #0154: a removed guild's data is kept 30 days, then purged by the nightly maintenance."""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import guild_presence as gp  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402

GONE = "1558904671832244276"
OTHER = "1145641080621109312"
NOW = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


class TestDbAndCache:
    @pytest.mark.asyncio
    async def test_absence_round_trip_keeps_first_timestamp(self, tmp_path, monkeypatch):
        from clashcontrol.db_manager import WarHistoryDB
        db = WarHistoryDB()
        await db.initialize(str(tmp_path / "absences.db"))
        try:
            monkeypatch.setattr(CACHE, "db_manager", db)
            monkeypatch.setattr(CACHE, "guild_absences", {})
            first = await CACHE.mark_guild_absent(GONE, "Server von Qaplop")
            assert first
            assert db.set_guild_absent_sync(GONE, "x", "2099-01-01T00:00:00+00:00")  # INSERT OR IGNORE
            assert db.get_all_guild_absences_sync()[GONE]["absent_since"] == first
            assert await CACHE.mark_guild_absent(GONE, "x") == first
            assert await CACHE.clear_guild_absent(GONE)
            assert db.get_all_guild_absences_sync() == {} and CACHE.guild_absences == {}
            assert not await CACHE.clear_guild_absent(GONE)
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_purge_guild_rows_cascades_and_spares_others(self, tmp_path, monkeypatch):
        from clashcontrol.db_manager import WarHistoryDB
        db = WarHistoryDB()
        await db.initialize(str(tmp_path / "purge.db"))
        try:
            with db._sync_conn() as conn:
                conn.execute("INSERT INTO clans (clan_tag, name) VALUES ('#AAA', 'A')")
                for g in (GONE, OTHER):
                    conn.execute("INSERT INTO guild_config (guild_id) VALUES (?)", (g,))
                    conn.execute("INSERT INTO guild_member_clans (guild_id, clan_tag) VALUES (?, '#AAA')", (g,))
                    conn.execute("INSERT INTO cwl_clan_coordinators (guild_id, clan_tag, discord_user_id) VALUES (?, '#AAA', '1')", (g,))
                    conn.execute("INSERT INTO guild_setup_dismissed (guild_id, step_id) VALUES (?, 'welcome')", (g,))
                    conn.execute("INSERT INTO bot_settings (guild_id, key, value) VALUES (?, 'k', 'v')", (g,))
                conn.execute("INSERT INTO bot_settings (guild_id, key, value) VALUES ('', 'tracker_guild_id', '1')")
                conn.execute("INSERT INTO guild_absences (guild_id, absent_since) VALUES (?, ?)", (GONE, _iso(40)))
                conn.commit()

            counts = db.purge_guild_rows_sync(GONE)
            assert counts is not None and counts["guild_config"] == 1 and counts["guild_absences"] == 1

            with db._sync_conn() as conn:
                def n(sql: str, *a: str) -> int:
                    return conn.execute(sql, a).fetchone()[0]
                for table in ("guild_config", "guild_member_clans", "cwl_clan_coordinators",
                              "guild_setup_dismissed", "bot_settings"):
                    assert n(f"SELECT COUNT(*) FROM {table} WHERE guild_id = ?", GONE) == 0, table
                    assert n(f"SELECT COUNT(*) FROM {table} WHERE guild_id = ?", OTHER) == 1, table
                assert n("SELECT COUNT(*) FROM bot_settings WHERE guild_id = ''") == 1  # bot-wide kept
                assert n("SELECT COUNT(*) FROM clans") == 1                              # clan data kept
            with pytest.raises(ValueError):
                db.purge_guild_rows_sync("")
        finally:
            await db.close()


class TestPurgeExpired:
    @pytest.fixture
    def env(self, monkeypatch):
        monkeypatch.setattr(CACHE, "guild_absences", {})
        monkeypatch.setattr(CACHE, "clear_guild_absent", AsyncMock(return_value=True))
        purge = AsyncMock(return_value={"cwl_events": 0, "subscription_channels": 1, "leaderboard_messages": 2,
                                        "families_deleted": [], "families_kept": [], "rows": {}})
        monkeypatch.setattr(gp, "purge_guild_data", purge)
        monkeypatch.setattr(gp, "expected_absence", lambda g: False)
        return purge

    @pytest.mark.asyncio
    async def test_only_due_and_definitely_absent_guilds_are_purged(self, env, monkeypatch):
        CACHE.guild_absences.update({
            "1": {"guild_name": "due", "absent_since": _iso(31)},
            "2": {"guild_name": "recent", "absent_since": _iso(29)},
            "3": {"guild_name": "back", "absent_since": _iso(40)},
            "4": {"guild_name": "unknown", "absent_since": _iso(40)},
        })
        presence = {"1": False, "2": False, "3": True, "4": None}
        monkeypatch.setattr(gp, "bot_guild_presence", lambda g: presence[g])
        assert await gp.purge_expired_absent_guilds(now=NOW) == ["1"]
        env.assert_awaited_once_with("1")
        CACHE.clear_guild_absent.assert_awaited_once_with("3")

    @pytest.mark.asyncio
    async def test_expected_absence_never_purged(self, env, monkeypatch):
        CACHE.guild_absences["1"] = {"guild_name": "prod guild on dev", "absent_since": _iso(90)}
        monkeypatch.setattr(gp, "bot_guild_presence", lambda g: False)
        monkeypatch.setattr(gp, "expected_absence", lambda g: True)
        assert await gp.purge_expired_absent_guilds(now=NOW) == []
        env.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_is_logged_and_others_continue(self, env, monkeypatch):
        CACHE.guild_absences.update({"1": {"absent_since": _iso(31)}, "2": {"absent_since": _iso(31)}})
        monkeypatch.setattr(gp, "bot_guild_presence", lambda g: False)
        env.side_effect = [RuntimeError("boom"), env.return_value]
        assert await gp.purge_expired_absent_guilds(now=NOW) == ["2"]


class TestPurgeGuildData:
    @pytest.mark.asyncio
    async def test_order_families_and_leaderboards(self, monkeypatch):
        db = MagicMock()
        db.list_cwl_events_sync.return_value = [{"id": 7, "cwl_season": "2026-10"}]
        monkeypatch.setattr(CACHE, "db_manager", db)
        monkeypatch.setattr(CACHE, "server_config", {GONE: {}, OTHER: {"member_families": ["#FAM_SHARED"]}})
        monkeypatch.setattr(CACHE, "subscriptions", {GONE: {"111": [{"clan_tag": "#AAA"}]}, OTHER: {}})
        monkeypatch.setattr(CACHE, "leaderboard_messages", {
            "k1": {"channel_id": "111"}, "k2": {"channel_id": "222"}})
        monkeypatch.setattr(CACHE, "clan_families", {
            "#FAM_OWN": {"owned_by_guild": GONE}, "#FAM_SHARED": {"owned_by_guild": GONE},
            "#FAM_OTHER": {"owned_by_guild": OTHER}})
        for name in ("delete_leaderboard_message", "delete_subscriptions_for_guild", "delete_clan_family"):
            monkeypatch.setattr(CACHE, name, AsyncMock())
        monkeypatch.setattr(CACHE, "purge_guild_rows", AsyncMock(return_value={"guild_config": 1}))
        guard = AsyncMock()
        with patch("clashcontrol.QBdiscocmdshelper_cwl.prune_or_detach_shared_clans_before_deletion", guard):
            res = await gp.purge_guild_data(GONE)

        guard.assert_awaited_once_with(int(GONE), 7, "2026-10")
        db.delete_cwl_event_sync.assert_called_once_with(7)
        CACHE.delete_leaderboard_message.assert_awaited_once_with("k1")
        CACHE.delete_subscriptions_for_guild.assert_awaited_once_with(GONE)
        CACHE.delete_clan_family.assert_awaited_once_with("#FAM_OWN")
        assert res["families_kept"] == ["#FAM_SHARED"]
        CACHE.purge_guild_rows.assert_awaited_once_with(GONE)

    @pytest.mark.asyncio
    async def test_failed_config_delete_raises(self, monkeypatch):
        db = MagicMock()
        db.list_cwl_events_sync.return_value = []
        monkeypatch.setattr(CACHE, "db_manager", db)
        monkeypatch.setattr(CACHE, "subscriptions", {})
        monkeypatch.setattr(CACHE, "leaderboard_messages", {})
        monkeypatch.setattr(CACHE, "clan_families", {})
        monkeypatch.setattr(CACHE, "purge_guild_rows", AsyncMock(return_value=None))
        with pytest.raises(RuntimeError):
            await gp.purge_guild_data(GONE)


class TestClock:
    def test_purge_due_date(self):
        assert gp.purge_due_date("2026-10-11T21:05:18+00:00") == "2026-11-10"

    @pytest.mark.asyncio
    async def test_record_skips_expected_absence(self, monkeypatch):
        mark = AsyncMock(return_value="x")
        monkeypatch.setattr(CACHE, "mark_guild_absent", mark)
        monkeypatch.setattr(gp, "expected_absence", lambda g: True)
        assert await gp.record_guild_removed(GONE, "n") is None
        mark.assert_not_awaited()
        monkeypatch.setattr(gp, "expected_absence", lambda g: False)
        assert await gp.record_guild_removed(GONE, "n") == "x"

    @pytest.mark.asyncio
    async def test_reconcile_starts_and_stops_clock(self, monkeypatch):
        monkeypatch.setattr(CACHE, "guild_absences", {"9": {"absent_since": _iso(3)}})
        monkeypatch.setattr(gp, "absent_configured_guilds", lambda: ["5"])
        monkeypatch.setattr(gp, "expected_absence", lambda g: False)
        monkeypatch.setattr(gp, "bot_guild_presence", lambda g: g == "9")
        mark = AsyncMock(return_value=_iso(0))
        clear = AsyncMock(return_value=True)
        monkeypatch.setattr(CACHE, "mark_guild_absent", mark)
        monkeypatch.setattr(CACHE, "clear_guild_absent", clear)
        await gp.reconcile_guild_absences()
        mark.assert_awaited_once_with("5", None)
        clear.assert_awaited_once_with("9")
