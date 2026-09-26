"""Maintenance mode (/admin Maintenance Start) closes the database on purpose so data/ can be
copied safely (2026-09-26): nothing may reopen it, and the web bridge (Activity + tracker MCP)
answers with one clear 503 instead of crashing into a plain-text 500."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
import pytest
from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import QBcore  # noqa: E402
from clashcontrol.db_manager import DatabaseMaintenanceError, WarHistoryDB  # noqa: E402


@pytest.mark.asyncio
async def test_sync_fallback_refuses_to_reopen_db_during_maintenance(tmp_path, monkeypatch):
    db = WarHistoryDB()
    await db.initialize(str(tmp_path / "maint.db"))
    await db.close()  # what do_maintenance_shutdown() does — drops the sync pool too
    assert db._pool is None

    monkeypatch.setattr(QBcore, "maintenance_mode", True)
    with pytest.raises(DatabaseMaintenanceError):
        with db._sync_conn():
            pass

    # Outside maintenance the fallback still works (tests / pre-initialize usage).
    monkeypatch.setattr(QBcore, "maintenance_mode", False)
    with db._sync_conn() as conn:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


@pytest.mark.asyncio
async def test_bridge_answers_503_json_during_maintenance(monkeypatch):
    from clashcontrol.web_bridge import create_app

    monkeypatch.setattr(QBcore, "maintenance_mode", True)
    async with TestClient(TestServer(create_app())) as client:
        resp = await client.get("/api/tracker/items/120")
        assert resp.status == 503
        assert (await resp.json())["error"] == "maintenance"

        health = await client.get("/api/health")
        assert health.status == 200


@pytest.mark.asyncio
async def test_bridge_turns_a_mid_request_maintenance_error_into_503(monkeypatch, caplog):
    """Maintenance started after the middleware's flag check: the handler's DB call raises
    DatabaseMaintenanceError — the answer is the same 503, logged as one INFO line, no traceback."""
    from clashcontrol.web_bridge import create_app

    async def _handler(request):
        raise DatabaseMaintenanceError("[DB-MAINT] Database closed for maintenance — aborting auto-reconnect")

    monkeypatch.setattr(QBcore, "maintenance_mode", False)
    app = create_app()
    app.router.add_get("/api/_test_maint_race", _handler)
    caplog.set_level("INFO")
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/_test_maint_race")
        assert resp.status == 503
        assert (await resp.json())["error"] == "maintenance"
    refused = [r for r in caplog.records if "refused" in r.getMessage()]
    assert refused and refused[0].levelname == "INFO" and refused[0].exc_info is None
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.asyncio
async def test_tracker_client_reports_maintenance_and_plain_text_errors():
    from clashcontrol.mcp.tracker_bridge_client import _read_body

    class _Resp:
        def __init__(self, status: int, json_value, text: str = "") -> None:
            self.status = status
            self._json, self._text = json_value, text

        async def json(self, content_type=None):
            if isinstance(self._json, Exception):
                raise self._json
            return self._json

        async def text(self):
            return self._text

    body = await _read_body(_Resp(503, {"error": "maintenance"}))  # type: ignore[arg-type]
    assert "maintenance mode" in body["error"]

    body = await _read_body(_Resp(500, ValueError("not json"), "500 Internal Server Error"))  # type: ignore[arg-type]
    assert body["error"] == "500 Internal Server Error"


@pytest.mark.asyncio
async def test_cwl_purge_with_nothing_to_delete_leaves_no_open_write_transaction(tmp_path):
    """Nightly Step 0.6 used to commit only when it deleted something — a 0-row DELETE still
    opens a write transaction, which then blocked Step 0.7/0.8's sync writes ("database is
    locked" after the 5 s busy_timeout, PROD 2026-09-26 17:17)."""
    db = WarHistoryDB()
    await db.initialize(str(tmp_path / "purge.db"))
    try:
        result = await db.purge_expired_cwl_events()
        assert not any(result.values())
        assert db._conn.in_transaction is False
        assert db.purge_stale_cwl_dm_refs_sync() == 0  # returned 0 via the "locked" error path before
        assert db.backfill_clan_created_at_from_first_war_sync() == 0
        with db._sync_conn() as conn:  # a real sync write must not hit the lock
            conn.execute("INSERT INTO clans (clan_tag, name) VALUES ('#LOCK', 'x')")
            conn.commit()
    finally:
        await db.close()
