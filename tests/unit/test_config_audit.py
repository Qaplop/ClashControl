"""/setup — server configuration audit, Phase 1 (tracker #0151, plans/tracker-0151-setup-config-audit.md).

Pins: the install permission set (must match the Developer Portal), step 1's classification
(Administrator / missing permissions / feature problems / bot not ready), the registry and its
prerequisite blocking, the re-authorize URL, rendering in every language without raw keys or
Discord limit overruns, the view's overview ⇄ detail navigation, and the /setup hint in the
config-time permission warning.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import QBcore  # noqa: E402
from clashcontrol import bot_permissions as bp  # noqa: E402
from clashcontrol import config_audit as ca  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402
from clashcontrol.config_audit import AuditStatus  # noqa: E402

GID = 515151
LANGS = ("en", "de", "es", "zh", "la")


class FakeRole:
    def __init__(self, rid: int, position: int):
        self.id, self.position, self.managed = rid, position, False
        self.name = f"role{rid}"
        self.mention = f"<@&{rid}>"

    def is_default(self) -> bool:
        return False

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def _channel(cid: int, **perms: bool) -> MagicMock:
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = cid
    ch.name = f"ch{cid}"
    ch.permissions_for = MagicMock(return_value=discord.Permissions(**perms))
    return ch


def _guild(perms: Optional[discord.Permissions], channels: Optional[Dict[int, MagicMock]] = None) -> SimpleNamespace:
    me = None if perms is None else SimpleNamespace(guild_permissions=perms, top_role=FakeRole(1, 10))
    channels = channels or {}
    return SimpleNamespace(
        id=GID, name="Test Server", owner_id=7, me=me, system_channel=None,
        get_channel_or_thread=lambda cid: channels.get(cid),
        get_channel=lambda cid: channels.get(cid),
        get_role=lambda rid: None,
    )


def _all_but(*missing: str) -> discord.Permissions:
    return discord.Permissions(**{p: True for p in bp.INSTALL_PERMISSIONS if p != "administrator" and p not in missing})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(QBcore, "bot", SimpleNamespace(application_id=1234))


class TestInstallPermissions:
    def test_matches_the_developer_portal_set(self):
        # 2026-10-11 (qaplop): screenshot set minus Manage Messages and Use Slash Commands.
        assert set(bp.INSTALL_PERMISSIONS) == {
            "administrator", "view_channel", "send_messages", "send_messages_in_threads", "embed_links",
            "attach_files", "read_message_history", "use_external_emojis", "manage_roles",
            "create_public_threads", "manage_threads", "create_instant_invite",
        }

    def test_every_name_is_a_real_permission(self):
        for name in bp.INSTALL_PERMISSIONS:
            assert name in discord.Permissions.VALID_FLAGS, name
        assert bp.install_permissions().administrator

    def test_missing_server_level(self):
        assert bp.missing_install_permissions(_guild(None)) is None
        assert bp.missing_install_permissions(_guild(discord.Permissions(administrator=True))) == ()
        assert bp.missing_install_permissions(_guild(_all_but("embed_links", "manage_roles"))) == (
            "embed_links", "manage_roles",
        )

    def test_reauthorize_url(self):
        from clashcontrol.constants import bot_install_url
        assert bot_install_url(99) == "https://discord.com/oauth2/authorize?client_id=99"
        url = bot_install_url(99, GID, bp.install_permissions().value)
        assert f"guild_id={GID}" in url and "disable_guild_select=true" in url
        assert f"permissions={bp.install_permissions().value}" in url and "scope=bot" in url


class TestStepBotPermissions:
    def test_administrator_is_ok(self):
        r = ca.check_bot_permissions(_guild(discord.Permissions(administrator=True)))
        assert r.status == AuditStatus.OK

    def test_all_permissions_without_administrator_is_incomplete(self):
        r = ca.check_bot_permissions(_guild(_all_but()))
        assert r.status == AuditStatus.INCOMPLETE
        assert [f.code for f in r.findings] == ["no_administrator"]

    def test_missing_permissions_are_broken(self):
        r = ca.check_bot_permissions(_guild(_all_but("send_messages", "manage_threads")))
        assert r.status == AuditStatus.BROKEN
        assert r.summary_params == {"count": 2}
        assert [dict(f.params)["perm"] for f in r.findings if f.code == "missing_permission"] == [
            "send_messages", "manage_threads",
        ]

    def test_feature_problems_are_broken_even_with_administrator(self):
        # Administrator, but the configured registration channel is gone.
        CACHE.server_config[str(GID)] = {"registration_message_enabled": True, "registration_channel_id": "555"}
        r = ca.check_bot_permissions(_guild(discord.Permissions(administrator=True)))
        assert r.status == AuditStatus.BROKEN
        assert [f.code for f in r.findings] == ["feature_issue"]

    def test_bot_not_cached_yet_is_info(self):
        assert ca.check_bot_permissions(_guild(None)).status == AuditStatus.INFO


class TestRegistry:
    def test_thirteen_steps_in_order(self):
        assert [s.order for s in ca.STEPS] == list(range(1, 14))
        assert ca.STEPS[0].id == "bot_permissions"
        for s in ca.STEPS:
            assert all(r in ca.STEPS_BY_ID for r in s.requires)

    def test_unimplemented_steps_are_pending(self):
        results = ca.run_audit(_guild(discord.Permissions(administrator=True)))
        assert list(results) == [s.id for s in ca.STEPS]
        assert all(results[s.id].status == AuditStatus.PENDING for s in ca.STEPS[1:])

    def test_prerequisite_not_set_up_blocks(self, monkeypatch):
        def incomplete(g):
            return ca.StepResult(AuditStatus.INCOMPLETE, "x")

        def ok(g):
            return ca.StepResult(AuditStatus.OK, "x")

        steps = (ca.AuditStep("a", 1, "admin", check=incomplete), ca.AuditStep("b", 2, "admin", ("a",), check=ok),
                 ca.AuditStep("c", 3, "admin", ("a",)))
        monkeypatch.setattr(ca, "STEPS", steps)
        monkeypatch.setattr(ca, "STEPS_BY_ID", {s.id: s for s in steps})
        results = ca.run_audit(_guild(None))
        assert results["b"].status == AuditStatus.BLOCKED and results["b"].summary_params == {"steps": "1"}
        assert results["c"].status == AuditStatus.PENDING  # not checked → not "blocked" either

    def test_failing_check_never_breaks_the_list(self):
        def boom(g):
            raise RuntimeError("x")
        r = ca.run_step(ca.AuditStep("z", 1, "admin", check=boom), _guild(None))
        assert r.status == AuditStatus.INFO and r.summary_key == "config_audit.summary_check_failed"


class TestRendering:
    @pytest.mark.parametrize("lang", LANGS)
    @pytest.mark.parametrize("perms", [
        discord.Permissions(administrator=True), _all_but(), _all_but(*bp.INSTALL_PERMISSIONS), None,
    ], ids=["admin", "no_admin", "nothing", "not_ready"])
    def test_every_language_renders_without_raw_keys(self, lang, perms):
        from clashcontrol.ui_config_audit import build_overview_embed, build_step_embed
        CACHE.server_config[str(GID)] = {"language": lang}
        guild = _guild(perms)
        results = ca.run_audit(guild)
        embeds = [build_overview_embed(guild, "1", results)] + [
            build_step_embed(guild, "1", s.id, results[s.id]) for s in ca.STEPS
        ]
        for e in embeds:
            texts = [e.title or "", e.description or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any("config_audit." in x or "permissions." in x for x in texts), texts
            assert len(e.description or "") <= 4096
            assert all(len(f.value) <= 1024 for f in e.fields)

    def test_detail_lists_each_missing_permission_and_offers_reauthorize(self):
        guild = _guild(_all_but("attach_files", "create_instant_invite"))
        result = ca.check_bot_permissions(guild)
        detail = ca.detail_bot_permissions(guild, result, GID, "1")
        body = "\n".join(b for _, b in detail.sections)
        assert "Attach Files" in body and "Create Invite" in body
        assert len(detail.links) == 1 and f"guild_id={GID}" in detail.links[0][1]

    def test_no_reauthorize_link_with_administrator(self):
        guild = _guild(discord.Permissions(administrator=True))
        detail = ca.detail_bot_permissions(guild, ca.check_bot_permissions(guild), GID, "1")
        assert detail.links == []


class TestSetupView:
    @pytest.mark.asyncio
    async def test_overview_detail_back_and_recheck(self, monkeypatch):
        from clashcontrol import ui_config_audit as ui
        guild = _guild(_all_but("embed_links"))
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        assert view.current_embed().title.startswith("🛠️")

        def click(values=None):
            it = MagicMock()
            it.data = {"values": values} if values else {}
            it.response = MagicMock()
            it.response.edit_message = AsyncMock()
            return it

        it = click(["bot_permissions"])
        await view._on_select(it)
        embed = it.response.edit_message.call_args.kwargs["embed"]
        assert embed.title.startswith("❌ 1.")
        assert any(getattr(c, "url", None) for c in view.children)  # re-authorize link button

        calls = []
        monkeypatch.setattr(ui, "run_audit", lambda g: calls.append(g) or ca.run_audit(g))
        it = click()
        await view._on_recheck(it)
        assert calls == [guild] and view.selected == "bot_permissions"

        it = click()
        await view._on_back(it)
        assert view.selected is None
        assert it.response.edit_message.call_args.kwargs["embed"].title.startswith("🛠️")
        view.stop()


class TestWarningPointsToSetup:
    def test_footer_mentions_setup(self):
        issue = bp.PermissionIssue("registration", bp.MISSING_CHANNEL_PERMS, 1, ("send_messages",))
        text = bp.format_issues_message([issue], guild_id=None)
        assert "/setup" in text
