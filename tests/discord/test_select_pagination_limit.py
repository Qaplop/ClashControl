"""Paged player/user selects never exceed Discord's 25 options.

2026-10-18 (PROD): ClanManagementLinkAccountView showed 24 players on every page, plus "Back to
first page" and "Load more…" from page 2 on — 26 options, and Discord refused the edit (50035,
"Must be between 1 and 25 in length") when a server with more than 48 unlinked players clicked
"Load more…". The Unlink view and the notification-settings user select had the same layout.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from clashcontrol.ui_common import SELECT_MAX_OPTIONS, select_page_bounds

NAV = {"__back_to_first__", "__load_more__"}


@pytest.mark.parametrize("total", [0, 1, 24, 25, 47, 48, 49, 60, 100])
def test_page_bounds_cover_every_entry_once_within_the_limit(total):
    seen, offset = [], 0
    while True:
        start, end, has_more = select_page_bounds(offset, total)
        options = (1 if offset > 0 else 0) + (end - start) + (1 if has_more else 0)
        assert options <= SELECT_MAX_OPTIONS
        seen.extend(range(start, end))
        if not has_more:
            break
        offset = end
    assert seen == list(range(total))


def _message() -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.guild = MagicMock(id=1)
    return msg


async def _walk(view, select_getter, handler) -> list:
    """Click "Load more…" until the last page; return every non-navigation value shown."""
    seen = []
    while True:
        sel = select_getter(view)
        assert len(sel.options) <= SELECT_MAX_OPTIONS
        values = [o.value for o in sel.options]
        seen.extend(v for v in values if v not in NAV)
        if "__load_more__" not in values:
            return seen
        inter = MagicMock()
        inter.data = {"values": ["__load_more__"]}
        inter.response.edit_message = AsyncMock()
        inter.response.defer = AsyncMock()
        inter.edit_original_response = AsyncMock()
        await handler(view, inter)


def _player_select(view) -> discord.ui.Select:
    return next(c for c in view.children if isinstance(c, discord.ui.Select) and "player_select" in (c.custom_id or ""))


@pytest.mark.asyncio
async def test_link_account_view_pages_stay_within_25():
    from clashcontrol.ui_clan_management import ClanManagementLinkAccountView

    players = [{"tag": f"#P{i:03}", "name": f"Player{i:03}", "th_level": 15, "activity": 0} for i in range(60)]
    view = ClanManagementLinkAccountView("#C", players, _message(), ["#C"])

    seen = await _walk(view, _player_select, ClanManagementLinkAccountView._on_player_select)

    assert sorted(seen) == sorted(p["tag"] for p in players)


@pytest.mark.asyncio
async def test_unlink_player_view_pages_stay_within_25():
    from clashcontrol.ui_clan_management import ClanManagementUnlinkPlayerView

    players = [{"tag": f"#P{i:03}", "name": f"Player{i:03}", "discord_user_id": "1", "discord_name": "U",
                "verified": True} for i in range(60)]
    view = ClanManagementUnlinkPlayerView("#C", players, _message(), ["#C"])

    seen = await _walk(view, _player_select, ClanManagementUnlinkPlayerView._on_player_select)

    assert len(seen) == 60 and len(set(seen)) == 60


@pytest.mark.asyncio
async def test_notification_user_select_pages_stay_within_25(monkeypatch):
    import clashcontrol.i18n as i18n_module
    import clashcontrol.ui_notifications as ui

    monkeypatch.setattr(i18n_module, "t", lambda key, **_k: key)
    users = {str(1000 + i): {"name": f"U{i}", "players": []} for i in range(60)}
    view = ui.NotificationSettingsView(
        scope="user", clan_tag="#C1", sent_message=SimpleNamespace(edit=AsyncMock()),  # type: ignore[arg-type]
        guild_clans=["#C1"], users_in_clan=users, guild=MagicMock(id=1),
    )

    def _user_select(v) -> discord.ui.Select:
        return next(c for c in v.children if isinstance(c, discord.ui.Select)
                    and any(o.value in users or o.value in NAV for o in c.options))

    seen = await _walk(view, _user_select, ui.NotificationSettingsView._on_user_select)

    assert sorted(seen) == sorted(users)
