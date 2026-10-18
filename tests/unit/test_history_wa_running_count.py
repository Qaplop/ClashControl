"""Running row count of history.war_attacks for the global stats (tracker #0155).

COUNT(*) on history.war_attacks scanned the small idx_wa_player_tag; after that index was dropped
it scans idx_wa_player_tag_date (stats warm-up 51 -> 92 s on PROD, 2026-10-18). The migration now
adds each batch's inserted rows to bot_metadata[HISTORY_WA_COUNT_KEY]; a full recount runs only
when the key is missing or older than HISTORY_WA_RECOUNT_DAYS.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import QBcore
from clashcontrol.db_manager import HISTORY_WA_COUNT_KEY, HISTORY_WA_RECOUNT_DAYS, WarHistoryDB

_COLS = "(id, war_id, clan_tag, date, player_name, player_tag, th_level, stars, attack_order)"


def _rows(n: int, date: str, prefix: str, first_id: int, zero_every: int = 0) -> list:
    # Explicit ids as on PROD: history rows came from main earlier, so their ids are below every id
    # still in main (the migration copies `id`; a collision would be skipped by INSERT OR IGNORE).
    return [
        (first_id + i, f"{prefix}{i}", "#C", date, "P", f"#P{prefix}{i}", 15, 3,
         0 if zero_every and i % zero_every == 0 else 1)
        for i in range(1, n + 1)
    ]


def _insert(path: str, schema_file: str, rows: list) -> None:
    conn = sqlite3.connect(schema_file)
    try:
        conn.executemany(f"INSERT INTO war_attacks {_COLS} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _key(path: str) -> dict | None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM bot_metadata WHERE key = ?", (HISTORY_WA_COUNT_KEY,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _not_migrating():
    QBcore.db_migration_active = False
    yield
    QBcore.db_migration_active = False


@pytest.fixture
async def db(tmp_path):
    d = WarHistoryDB()
    main_path, hist_path = str(tmp_path / "main.db"), str(tmp_path / "hist.db")
    await d.initialize(main_path, hist_path)
    try:
        yield d, main_path, hist_path
    finally:
        await d.close()


def _attacks(d: WarHistoryDB) -> int:
    return d.get_global_db_statistics_sync(force_refresh=True)["attacks_count"]


async def test_first_stats_run_counts_and_stores(db):
    d, main_path, hist_path = db
    _insert(main_path, hist_path, _rows(4, "2025-01-01T10:00", "h", 0, zero_every=4))  # 3 attacks + 1 missed

    assert _attacks(d) == 3
    assert _key(main_path)["count"] == 4


async def test_migration_adds_to_the_count_and_stats_use_it(db):
    d, main_path, hist_path = db
    _insert(main_path, hist_path, _rows(2, "2025-01-01T10:00", "h", 0))
    assert _attacks(d) == 2  # stores count=2

    _insert(main_path, main_path, _rows(5, "2026-01-01T10:00", "m", 1000))
    moved, done = await d._migrate_date_window_batched("war_attacks", "2026-01-01", "2026-01-02", batch_size=2)
    assert (moved, done) == (5, True)
    assert _key(main_path)["count"] == 7

    # A row written to history behind the counter's back is not seen — proof the stats read the
    # running count instead of scanning (the weekly recount picks such rows up).
    _insert(main_path, hist_path, _rows(1, "2025-02-01T10:00", "x", 500))
    assert _attacks(d) == 7


async def test_stale_count_is_recounted(db):
    d, main_path, hist_path = db
    _insert(main_path, hist_path, _rows(3, "2025-01-01T10:00", "h", 0))
    old = (datetime.now(timezone.utc) - timedelta(days=HISTORY_WA_RECOUNT_DAYS + 1)).isoformat()
    conn = sqlite3.connect(main_path)
    conn.execute("INSERT INTO bot_metadata (key, value) VALUES (?, ?)",
                 (HISTORY_WA_COUNT_KEY, json.dumps({"count": 999, "recounted_at_utc": old})))
    conn.commit()
    conn.close()

    assert _attacks(d) == 3
    assert _key(main_path)["count"] == 3


async def test_recount_during_migration_is_not_stored(db):
    d, main_path, hist_path = db
    _insert(main_path, hist_path, _rows(3, "2025-01-01T10:00", "h", 0))
    QBcore.db_migration_active = True

    assert _attacks(d) == 3
    assert _key(main_path) is None


async def test_migration_without_a_stored_count_creates_none(db):
    d, main_path, _ = db
    _insert(main_path, main_path, _rows(3, "2026-01-01T10:00", "m", 1000))
    await d._migrate_date_window_batched("war_attacks", "2026-01-01", "2026-01-02", batch_size=10)
    assert _key(main_path) is None
