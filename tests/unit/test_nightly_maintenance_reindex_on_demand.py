"""Tracker #0155: nightly maintenance without the routine REINDEX.

* REINDEX took ~1,900 s every night (2026-10-12..17) whatever had changed; now it runs only when
  SQLite reported corruption (QBcore.db_reindex_requested, set by the log watcher).
* idx_wa_player_tag / idx_ws_clan_tag are prefixes of composite indexes; maintenance drops them
  once (measured on a PROD copy: migration 1,264 -> 3,688 rows/s) and startup no longer builds them.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
import sqlite3

import pytest

import QBcore
from clashcontrol.db_manager import SUPERSEDED_INDEXES, WarHistoryDB, install_db_corruption_watcher


def _index_names(path: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _no_pending_request():
    QBcore.db_reindex_requested = None
    yield
    QBcore.db_reindex_requested = None


@pytest.fixture
async def db(tmp_path):
    d = WarHistoryDB()
    paths = (str(tmp_path / "main.db"), str(tmp_path / "hist.db"))
    # 16 KB pages as on PROD: a fresh 4 KB file takes maintenance's one-time page-size VACUUM
    # path instead, which never REINDEXes.
    pre = sqlite3.connect(paths[0])
    pre.execute("PRAGMA page_size=16384")
    pre.execute("VACUUM")
    pre.close()
    await d.initialize(*paths)
    try:
        yield d, paths
    finally:
        await d.close()


class TestSupersededIndexes:
    async def test_startup_does_not_build_them(self, db):
        _, (main_path, hist_path) = db
        for path in (main_path, hist_path):
            names = _index_names(path)
            for old, new in SUPERSEDED_INDEXES:
                assert old not in names
                assert new in names

    async def test_maintenance_drops_them_where_the_composite_exists(self, db):
        d, (main_path, hist_path) = db
        # As on PROD before this change.
        for schema in ("main", "history"):
            await d._conn.execute(f"CREATE INDEX {schema}.idx_wa_player_tag ON war_attacks(player_tag)")
            await d._conn.execute(f"CREATE INDEX {schema}.idx_ws_clan_tag ON war_summary(clan_tag)")
        await d._conn.commit()

        result = await d.nightly_db_maintenance()

        for path in (main_path, hist_path):
            names = _index_names(path)
            assert "idx_wa_player_tag" not in names and "idx_ws_clan_tag" not in names
            assert "idx_wa_player_tag_date" in names and "idx_ws_clan_date" in names
        assert "dropped main.idx_wa_player_tag" in result
        assert "dropped history.idx_ws_clan_tag" in result

    async def test_kept_when_the_composite_is_missing(self, db):
        d, (main_path, _) = db
        await d._conn.execute("DROP INDEX main.idx_wa_player_tag_date")
        await d._conn.execute("CREATE INDEX main.idx_wa_player_tag ON war_attacks(player_tag)")
        await d._conn.commit()

        await d.nightly_db_maintenance()

        assert "idx_wa_player_tag" in _index_names(main_path)


class TestReindexOnDemand:
    async def test_no_request_no_reindex(self, db):
        d, _ = db
        result = await d.nightly_db_maintenance()
        assert "VACUUM" in result and "VACUUM pending" not in result  # the non-VACUUM path ran
        assert "REINDEX skipped," in result

    async def test_request_runs_reindex_and_clears_it(self, db):
        d, _ = db
        QBcore.db_reindex_requested = "test: database disk image is malformed"
        result = await d.nightly_db_maintenance()
        assert "REINDEX (requested)" in result
        assert QBcore.db_reindex_requested is None


class TestCorruptionWatcher:
    @pytest.fixture(autouse=True)
    def _watcher(self):
        install_db_corruption_watcher()
        install_db_corruption_watcher()  # idempotent
        root = logging.getLogger()
        watchers = [h for h in root.handlers if type(h).__name__ == "_DbCorruptionLogWatcher"]
        assert len(watchers) == 1
        yield
        for h in watchers:
            root.removeHandler(h)

    def test_error_with_corruption_text_requests_reindex(self):
        logging.error("[DB-QUERY-SYNC] something failed: database disk image is malformed")
        assert "malformed" in (QBcore.db_reindex_requested or "")

    def test_exception_text_counts_too(self):
        try:
            raise sqlite3.DatabaseError("database disk image is malformed")
        except sqlite3.DatabaseError:
            logging.error("[DB] query failed", exc_info=True)
        assert QBcore.db_reindex_requested

    def test_other_errors_and_warnings_do_not(self):
        logging.error("[DB] database is locked")
        logging.warning("database disk image is malformed")
        assert QBcore.db_reindex_requested is None

    def test_first_reason_is_kept(self):
        logging.error("first: database disk image is malformed")
        logging.error("second: database disk image is malformed")
        assert QBcore.db_reindex_requested.startswith("first")
