"""Bot permission checks for automations (tracker #0150 follow-up, clashcontrol/bot_permissions.py).

Two PROD guilds configured automations the bot could not carry out (channels it could not see /
post in) and got normal success replies. These tests pin: what each feature requires, the role
checks (Manage Roles, hierarchy, managed roles), the guild audit's enabled/disabled scoping, the
user-facing warning, and that every config entry point calls the check.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import inspect
import os
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import bot_permissions as bp  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402

ALL_POST = dict(view_channel=True, send_messages=True, embed_links=True,
                read_message_history=True, send_messages_in_threads=True)


class FakeRole:
    def __init__(self, rid: int, position: int, *, managed: bool = False, default: bool = False):
        self.id, self.position, self.managed, self._default = rid, position, managed, default
        self.name = f"role{rid}"

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def _channel(cid: int, **perms: bool) -> MagicMock:
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = cid
    ch.permissions_for = MagicMock(return_value=discord.Permissions(**perms))
    return ch


def _guild(channels: Dict[int, MagicMock], *, roles: Optional[Dict[int, FakeRole]] = None,
           manage_roles: bool = True, administrator: bool = False, top: int = 10,
           system_channel: Optional[MagicMock] = None, gid: int = 4242) -> SimpleNamespace:
    me = SimpleNamespace(
        guild_permissions=discord.Permissions(manage_roles=manage_roles, administrator=administrator),
        top_role=FakeRole(1, top),
    )
    roles = roles or {}
    return SimpleNamespace(
        id=gid, name="G", owner_id=7, me=me, system_channel=system_channel,
        get_channel_or_thread=lambda cid: channels.get(cid),
        get_channel=lambda cid: channels.get(cid),
        get_role=lambda rid: roles.get(rid),
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})


class TestPrimitives:
    def test_view_missing_reports_only_view(self):
        ch = _channel(1)  # nothing granted
        assert bp.missing_channel_permissions(ch, None, bp.CHANNEL_FEATURES["cwl_management"]) == ("view_channel",)

    def test_reports_exact_missing(self):
        ch = _channel(1, view_channel=True, send_messages=True)
        assert bp.missing_channel_permissions(ch, None, bp.CHANNEL_FEATURES["cwl_management"]) == (
            "embed_links", "read_message_history",
        )

    def test_thread_needs_send_in_threads(self):
        th = MagicMock(spec=discord.Thread)
        th.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True, send_messages=True))
        assert bp.missing_channel_permissions(th, None, ("view_channel", "send_messages")) == ("send_messages_in_threads",)

    def test_leaderboard_does_not_require_embeds(self):
        ch = _channel(1, view_channel=True, send_messages=True, read_message_history=True)
        assert bp.check_channel_feature(_guild({1: ch}), "leaderboard", 1) == []

    def test_channel_not_found(self):
        issues = bp.check_channel_feature(_guild({}), "registration", "99")
        assert issues == [bp.PermissionIssue("registration", bp.CHANNEL_NOT_FOUND, 99)]

    def test_role_checks(self):
        roles = {5: FakeRole(5, 3), 6: FakeRole(6, 12), 7: FakeRole(7, 2, managed=True)}
        g = _guild({}, roles=roles, manage_roles=False)
        kinds = {(i.kind, i.target_id) for i in bp.check_role_feature(g, "role_system", [5, 6, 7, 8])}
        assert kinds == {
            (bp.MISSING_MANAGE_ROLES, None), (bp.ROLE_ABOVE_BOT, 6),
            (bp.ROLE_MANAGED, 7), (bp.ROLE_NOT_FOUND, 8),
        }

    def test_role_ok(self):
        g = _guild({}, roles={5: FakeRole(5, 3)})
        assert bp.check_role_feature(g, "role_system", ["5"]) == []


class TestAudit:
    def test_enabled_only_by_default_and_include_disabled(self):
        g = _guild({1: _channel(1)})
        CACHE.server_config["4242"] = {"registration_channel_id": "1", "registration_message_enabled": False}
        assert bp.audit_guild(g) == []
        assert [i.kind for i in bp.audit_guild(g, include_disabled=True)] == [bp.MISSING_CHANNEL_PERMS]

    def test_raid_falls_back_to_war_channel(self):
        g = _guild({1: _channel(1)})
        CACHE.server_config["4242"] = {"war_notification_channel_id": "1", "channel_raid_notifications_enabled": True}
        issues = bp.audit_guild(g, features={"raid_notifications"})
        assert issues and issues[0].target_id == 1

    def test_leaderboard_subscription_channels(self):
        g = _guild({1547508198246981713: _channel(1547508198246981713, view_channel=True)})
        CACHE.subscriptions["4242"] = {"1547508198246981713": [{"clan_tag": "#2JQUJPVQU"}]}
        issues = bp.audit_guild(g, features={"leaderboard"})
        assert issues[0].perms == ("send_messages", "read_message_history")

    def test_welcome_needs_system_channel(self):
        CACHE.server_config["4242"] = {"welcome_message_enabled": True}
        assert bp.audit_guild(_guild({}), features={"welcome"})[0].kind == bp.NO_SYSTEM_CHANNEL

    def test_coc_roles_without_ids_still_need_manage_roles(self):
        CACHE.server_config["4242"] = {"coc_role_enabled": True}
        issues = bp.audit_guild(_guild({}, manage_roles=False), features={"coc_roles"})
        assert [i.kind for i in issues] == [bp.MISSING_MANAGE_ROLES]

    def test_all_good(self):
        g = _guild({1: _channel(1, **ALL_POST)}, roles={5: FakeRole(5, 2)})
        CACHE.server_config["4242"] = {
            "registration_channel_id": "1", "registration_message_enabled": True,
            "cwl_management_channel_id": "1", "cwl_management_message_enabled": True,
            "newbie_role_id": "5", "member_role_id": "5", "role_system_enabled": True,
        }
        assert bp.audit_guild(g) == []


class TestFormatting:
    def test_english_message_names_feature_channel_and_perms(self):
        msg = bp.format_issues_message(
            [bp.PermissionIssue("leaderboard", bp.MISSING_CHANNEL_PERMS, 123, ("send_messages",))], guild_id=None,
        )
        assert "Leaderboard" in msg and "<#123>" in msg and "Send Messages" in msg

    def test_guild_language(self):
        CACHE.server_config["4242"] = {"language": "de"}
        msg = bp.format_issues_message(
            [bp.PermissionIssue("war_notifications", bp.MISSING_CHANNEL_PERMS, 1, ("embed_links",))], guild_id=4242,
        )
        assert "Kriegserinnerungen" in msg and "Links einbetten" in msg

    def test_truncates_long_lists(self):
        issues = [bp.PermissionIssue("leaderboard", bp.MISSING_CHANNEL_PERMS, 10**17 + i, ("send_messages",)) for i in range(200)]
        assert len(bp.format_issues_message(issues, guild_id=None)) <= 2000


class TestWarn:
    def _interaction(self, guild, *, done: bool = True):
        it = MagicMock()
        it.guild = guild
        it.user.id = 99
        it.response.is_done = MagicMock(return_value=done)
        it.followup.send = AsyncMock()
        it.response.send_message = AsyncMock()
        return it

    @pytest.mark.asyncio
    async def test_sends_ephemeral_when_missing(self):
        g = _guild({1: _channel(1)})
        CACHE.server_config["4242"] = {"registration_channel_id": "1"}
        it = self._interaction(g)
        issues = await bp.warn_missing_permissions(it, features={"registration"})
        assert issues
        assert it.followup.send.await_args.kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_silent_when_fine(self):
        g = _guild({1: _channel(1, **ALL_POST)})
        CACHE.server_config["4242"] = {"registration_channel_id": "1"}
        it = self._interaction(g, done=False)
        assert await bp.warn_missing_permissions(it, features={"registration"}) == []
        it.response.send_message.assert_not_awaited()
        it.followup.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_channel_filter(self):
        g = _guild({1: _channel(1), 2: _channel(2)})
        CACHE.subscriptions["4242"] = {"1": [], "2": []}
        it = self._interaction(g)
        issues = await bp.warn_missing_permissions(it, features={"leaderboard"}, only_channel_id=2)
        assert [i.target_id for i in issues] == [2]

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(bp, "audit_guild", MagicMock(side_effect=RuntimeError("boom")))
        assert await bp.warn_missing_permissions(self._interaction(_guild({})), features={"registration"}) == []


class TestAuditReport:
    def test_report_flags_missing_administrator(self):
        g = _guild({1: _channel(1)}, administrator=False)
        CACHE.server_config["4242"] = {"registration_channel_id": "1", "registration_message_enabled": True}
        report, with_issues, total = bp.build_audit_report([g])
        assert with_issues == 1 and total == 1
        assert "Administrator: NO" in report and "Registration message" in report


class TestEntryPointsCallTheCheck:
    """Structural: every place a user enables an automation or sets its channel/role calls
    warn_missing_permissions (or the direct channel check) — adding a new entry point means
    adding it here."""

    @pytest.mark.parametrize("module, qualname", [
        ("clashcontrol.ui_clan_management", "ChannelConfigurationView._on_apply"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._toggle_channel_reminders"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._on_toggle_registration"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._on_toggle_welcome_message"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._on_toggle_role_system"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._on_toggle_coc_role_feature"),
        ("clashcontrol.ui_clan_management", "ClanManagementView._on_toggle_clan_role_feature"),
        ("clashcontrol.ui_clan_management", "RoleConfigurationView._on_apply"),
        ("clashcontrol.ui_cwl_roster", "_make_cwl_settings_toggle_callback"),
        ("clashcontrol.ui_cwl_roster", "_make_cwl_settings_toggle_player_hub_callback"),
        ("QBdiscordcmds", "subscribe"),
    ])
    def test_calls_permission_check(self, module, qualname):
        import importlib
        obj = importlib.import_module(module)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        obj = getattr(obj, "callback", obj)  # app_commands.Command -> its function
        assert "warn_missing_permissions" in inspect.getsource(obj)
