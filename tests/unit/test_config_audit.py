"""/setup — server configuration audit, Phase 1 (tracker #0151, plans/implemented/tracker-0151-setup-config-audit.md).

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


EVERYONE = FakeRole(GID, 0)


class FakeCwlDb:
    """The two sync reads step 12 does (cwl_events / cwl_event_clans)."""

    def __init__(self, events=None, clans=None):
        self.events = events or []          # newest season first, like list_cwl_events_sync
        self.clans = clans or {}            # event id -> clan rows

    def list_cwl_events_sync(self, guild_id, status=None):
        return list(self.events)

    def get_cwl_event_clans_sync(self, event_id):
        return list(self.clans.get(event_id, []))


def _guild(perms: Optional[discord.Permissions], channels: Optional[Dict[int, MagicMock]] = None,
           system_channel: Optional[MagicMock] = None, roles: Optional[Dict[int, FakeRole]] = None) -> SimpleNamespace:
    me = None if perms is None else SimpleNamespace(guild_permissions=perms, top_role=FakeRole(1, 10))
    channels = channels or {}
    roles = roles or {}
    return SimpleNamespace(
        id=GID, name="Test Server", owner_id=7, me=me, system_channel=system_channel,
        default_role=EVERYONE,
        get_channel_or_thread=lambda cid: channels.get(cid),
        get_channel=lambda cid: channels.get(cid),
        get_role=lambda rid: roles.get(rid),
    )


def _channel_by_role(cid: int, by_role: Dict[object, Dict[str, bool]], bot: Dict[str, bool]) -> MagicMock:
    """A channel whose permissions differ per role: ``by_role`` maps a role to its permissions
    there; anything else (the bot's own member) gets ``bot``."""
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = cid
    ch.name = f"ch{cid}"
    # Identity lookup: the bot's own fake member (a SimpleNamespace) isn't hashable.
    pairs = list(by_role.items())
    ch.permissions_for = MagicMock(side_effect=lambda who: discord.Permissions(
        **next((perms for role, perms in pairs if role is who), bot)))
    return ch


def _all_but(*missing: str) -> discord.Permissions:
    return discord.Permissions(**{p: True for p in bp.INSTALL_PERMISSIONS if p != "administrator" and p not in missing})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Fresh translations: other tests swap the shared manager's for fakes (see test_tutorial.py).
    from clashcontrol import i18n
    fresh = i18n.TranslationManager()
    fresh.load_translations()
    monkeypatch.setattr(i18n, "_translation_manager", fresh)
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(CACHE, "clan_name_cache", {})
    monkeypatch.setattr(CACHE, "clan_families", {})
    monkeypatch.setattr(CACHE, "guild_guest_clans", {})
    monkeypatch.setattr(CACHE, "user_accounts", {})
    monkeypatch.setattr(CACHE, "db_manager", FakeCwlDb())
    monkeypatch.setattr(CACHE, "guild_setup_dismissed", {})
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

    def test_feature_problems_show_under_their_own_step(self):
        # Administrator, but the configured registration channel is gone: step 5 reports it, step 1
        # does not (every audited feature belongs to a step since Phase 3).
        CACHE.server_config[str(GID)] = {"registration_message_enabled": True, "registration_channel_id": "555"}
        guild = _guild(discord.Permissions(administrator=True))
        assert ca.check_bot_permissions(guild).status == AuditStatus.OK
        assert ca.check_registration(guild).status == AuditStatus.BROKEN
        owned = {f for st in ca.STEPS if st.check is not None for f in st.permission_features}
        assert set(bp.CHANNEL_FEATURES) - {"clan_management"} <= owned
        assert set(bp.ROLE_FEATURES) <= owned

    def test_bot_not_cached_yet_is_info(self):
        assert ca.check_bot_permissions(_guild(None)).status == AuditStatus.INFO


class TestRegistry:
    def test_fourteen_steps_in_order(self):
        # 14 since 2026-10-11: linked accounts split out of step 5 and moved to the end (qaplop).
        assert [s.order for s in ca.STEPS] == list(range(1, 15))
        assert ca.STEPS[-1].id == "linked_accounts"
        assert ca.STEPS[0].id == "bot_permissions"
        for s in ca.STEPS:
            assert all(r in ca.STEPS_BY_ID for r in s.requires)

    def test_every_step_is_checked(self):
        results = ca.run_audit(_guild(discord.Permissions(administrator=True)))
        assert list(results) == [s.id for s in ca.STEPS]
        assert not hasattr(AuditStatus, "PENDING")  # "not checked yet" retired once all phases shipped

    def test_switched_off_feature_is_not_blocked(self):
        # No member clans (step 3 ⚠️): raid reminders off → ⏸️, not 🔒; switched on → 🔒.
        guild = _guild(discord.Permissions(administrator=True))
        assert ca.run_audit(guild)["raid_reminders"].status == AuditStatus.OFF
        CACHE.server_config[str(GID)] = {"channel_raid_notifications_enabled": True}
        assert ca.run_audit(guild)["raid_reminders"].status == AuditStatus.BLOCKED

    def test_prerequisite_not_set_up_blocks(self, monkeypatch):
        def incomplete(g):
            return ca.StepResult(AuditStatus.INCOMPLETE, "x")

        def ok(g):
            return ca.StepResult(AuditStatus.OK, "x")

        steps = (ca.AuditStep("a", 1, "admin", check=incomplete, detail=None),
                 ca.AuditStep("b", 2, "admin", ("a",), check=ok, detail=None))
        monkeypatch.setattr(ca, "STEPS", steps)
        monkeypatch.setattr(ca, "STEPS_BY_ID", {s.id: s for s in steps})
        results = ca.run_audit(_guild(None))
        assert results["b"].status == AuditStatus.BLOCKED and results["b"].summary_params == {"steps": "1"}

    def test_failing_check_never_breaks_the_list(self):
        def boom(g):
            raise RuntimeError("x")
        r = ca.run_step(ca.AuditStep("z", 1, "admin", check=boom, detail=None), _guild(None))
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
        heading, body = detail.problem                  # the missing permissions lead the page
        assert "(2)" in heading
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


ADMIN = discord.Permissions(administrator=True)
ALL_PERMS = dict(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)


def _cfg(**kw) -> Dict:
    CACHE.server_config[str(GID)] = kw
    return kw


def _body(detail: ca.StepDetail) -> str:
    """All text of a detail page: the problem block (if any) and the information sections."""
    parts = [b for _, b in detail.sections]
    if detail.problem is not None:
        parts = [detail.problem[0], detail.problem[1]] + parts
    return "\n".join(parts)


class TestStepBasics:
    def test_defaults_are_ok_with_hints(self):
        r = ca.check_basics(_guild(ADMIN))
        assert r.status == AuditStatus.OK
        assert {f.code for f in r.findings} == {"default_language", "default_timezone"}
        body = _body(ca.detail_basics(_guild(ADMIN), r, GID, "1"))
        assert "UTC" in body and "Open settings" in body

    def test_explicit_settings_have_no_hints(self):
        _cfg(language="de", timezone_name="Europe/Berlin")
        r = ca.check_basics(_guild(ADMIN))
        assert r.findings == [] and r.summary_params["timezone"] == "Europe/Berlin"
        assert r.summary_params["lang_name"] == "Deutsch"


class TestStepClans:
    def test_none_is_incomplete_and_blocks_enabled_dependents(self):
        _cfg(channel_war_notifications_enabled=True, war_notification_channel_id="1")
        results = ca.run_audit(_guild(ADMIN))
        assert results["clans"].status == AuditStatus.INCOMPLETE
        assert results["channel_war_notifications"].status == AuditStatus.BLOCKED

    def test_states_and_family_expansion(self):
        CACHE.clan_families["F1"] = {"name": "Fam", "clans": ["#B", "#C"]}
        _cfg(member_clans=["#A"], member_families=["F1"])
        CACHE.clan_name_cache.update({
            "#A": {"name": "Alpha", "warlog_is_public": True},
            "#B": {"name": "Beta", "warlog_is_public": False},
            "#C": {"name": "Gamma", "is_deleted": True},
        })
        r = ca.check_clans(_guild(ADMIN))
        assert r.status == AuditStatus.BROKEN and r.summary_params == {"clans": 3, "families": 1, "count": 2}
        assert dict(r.data["clans"]) == {"#A": "ok", "#B": "warlog_private", "#C": "deleted"}
        body = _body(ca.detail_clans(_guild(ADMIN), r, GID, "1"))
        assert "Alpha" in body and "Fam" in body and "Edit Clan" in body

    def test_unknown_clan_is_not_a_problem(self):
        _cfg(member_clans=["#X"])
        r = ca.check_clans(_guild(ADMIN))
        assert r.status == AuditStatus.OK and r.data["clans"] == [("#X", "no_data")]


class TestStepWelcome:
    def test_off(self):
        assert ca.check_welcome(_guild(ADMIN)).status == AuditStatus.OFF

    def test_no_system_channel_is_broken(self):
        _cfg(welcome_message_enabled=True)
        assert ca.check_welcome(_guild(ADMIN)).status == AuditStatus.BROKEN

    def test_apply_mode(self):
        sys_ch = _channel(10, view_channel=True, send_messages=True)
        _cfg(welcome_message_enabled=True, welcome_message_mode="apply_channel")
        assert ca.check_welcome(_guild(ADMIN, {10: sys_ch}, sys_ch)).status == AuditStatus.INCOMPLETE
        _cfg(welcome_message_enabled=True, welcome_message_mode="apply_channel", welcome_apply_channel_id="99")
        assert ca.check_welcome(_guild(ADMIN, {10: sys_ch}, sys_ch)).status == AuditStatus.BROKEN
        r = ca.check_welcome(_guild(ADMIN, {10: sys_ch, 99: _channel(99)}, sys_ch))
        assert r.status == AuditStatus.OK and r.summary_params == {"channel": "#ch10"}


class TestChannelReminders:
    def test_channel_war_states_and_step1_does_not_double_count(self):
        assert ca.check_channel_war_notifications(_guild(ADMIN)).status == AuditStatus.OFF
        _cfg(channel_war_notifications_enabled=True)
        assert ca.check_channel_war_notifications(_guild(ADMIN)).status == AuditStatus.INCOMPLETE
        _cfg(channel_war_notifications_enabled=True, war_notification_channel_id="20",
             war_notification_threshold_hours=1.0)
        no_embed = _channel(20, view_channel=True, send_messages=True)
        guild = _guild(_all_but(), {20: no_embed})
        assert ca.check_channel_war_notifications(guild).status == AuditStatus.BROKEN
        assert not [f for f in ca.check_bot_permissions(guild).findings if f.code == "feature_issue"]
        r = ca.check_channel_war_notifications(_guild(ADMIN, {20: _channel(20, **ALL_PERMS)}))
        assert r.status == AuditStatus.OK and r.summary_params["channel"] == "#ch20"

    def test_raid_falls_back_to_war_channel(self):
        _cfg(channel_raid_notifications_enabled=True, war_notification_channel_id="20")
        guild = _guild(ADMIN, {20: _channel(20, **ALL_PERMS)})
        r = ca.check_raid_reminders(guild)
        assert r.status == AuditStatus.OK and r.data["channel_id"] == "20"
        assert "war reminder channel" in _body(ca.detail_raid_reminders(guild, r, GID, "1"))


class TestStepLeaderboards:
    def test_registration_tracker_is_not_a_leaderboard(self):
        CACHE.subscriptions[str(GID)] = {"5": [{"clan_tag": "#A", "subscription_type": "playerregistration"}]}
        assert ca.check_leaderboards(_guild(ADMIN)).status == AuditStatus.OFF

    def test_deleted_clan_and_ok(self):
        CACHE.subscriptions[str(GID)] = {"5": [{"clan_tag": "#A", "mode": "attack"}]}
        guild = _guild(ADMIN, {5: _channel(5, **ALL_PERMS)})
        r = ca.check_leaderboards(guild)
        assert r.status == AuditStatus.OK and r.summary_params == {"count": 1, "channels": 1}
        CACHE.clan_name_cache["#A"] = {"name": "Alpha", "is_deleted": True}
        r = ca.check_leaderboards(guild)
        assert r.status == AuditStatus.BROKEN and r.data["deleted"] == ["#A"]


class TestRenderingConfigured:
    @pytest.mark.parametrize("lang", LANGS)
    @pytest.mark.parametrize("problems", [False, True], ids=["ok", "problems"])
    def test_every_step_renders_in_every_language(self, lang, problems):
        from clashcontrol.ui_config_audit import build_overview_embed, build_step_embed
        CACHE.clan_families["F1"] = {"name": "Fam", "clans": ["#B"]}
        CACHE.clan_name_cache.update({"#A": {"name": "Alpha"}, "#B": {"name": "Beta", "warlog_is_public": not problems}})
        CACHE.guild_guest_clans[str(GID)] = {"#G": "2026-10"}
        CACHE.subscriptions[str(GID)] = {"5": [{"clan_tag": "#A", "mode": "attack"}]}
        _cfg(language=lang, member_clans=["#A"], member_families=["F1"],
             welcome_message_enabled=True, welcome_clan_tags=["#A"],
             channel_war_notifications_enabled=True, war_notification_channel_id="20",
             channel_raid_notifications_enabled=True, raid_notification_scope="open_attacks")
        perms = dict(view_channel=True) if problems else dict(ALL_PERMS)
        chans = {i: _channel(i, **perms) for i in (5, 10, 20)}
        guild = _guild(_all_but("attach_files") if problems else ADMIN, chans, chans[10])
        results = ca.run_audit(guild)
        assert all(results[s.id].summary_key != "config_audit.summary_check_failed" for s in ca.STEPS), results
        embeds = [build_overview_embed(guild, "1", results)] + [
            build_step_embed(guild, "1", s.id, results[s.id]) for s in ca.STEPS
        ]
        for e in embeds:
            texts = [e.title or "", e.description or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any("config_audit." in x or "permissions." in x or "ui_components." in x for x in texts), texts
            assert len(e.description or "") <= 4096 and all(len(f.value) <= 1024 for f in e.fields)


class TestPermissionNames:
    """qaplop 2026-10-11: permission names in text are quoted and use Discord's own client wording;
    Latin uses the English labels (Discord has no Latin client)."""

    @pytest.mark.parametrize("lang, channel, server", [
        ("en", "“Send Messages”", "“Send Messages and Create Posts”"),
        ("de", "„Nachrichten senden“", "„Nachrichten versenden und Posts erstellen“"),
        ("es", "«Enviar mensajes»", "«Enviar mensajes y crear publicaciones»"),
        ("zh", "“发送消息”", "“发送消息与创建帖子”"),
        ("la", "“Send Messages”", "“Send Messages and Create Posts”"),
    ])
    def test_quoted_in_every_language(self, lang, channel, server):
        # Channel overrides (text channel) keep the short name; the role settings say "…Posts".
        CACHE.server_config[str(GID)] = {"language": lang}
        assert bp.perm_label("send_messages", guild_id=GID) == channel
        assert bp.perm_label("send_messages", guild_id=GID, scope="server") == server

    def test_view_channel_differs_by_scope(self):
        CACHE.server_config[str(GID)] = {"language": "de"}
        assert bp.perm_label("view_channel", guild_id=GID) == "„Kanal anzeigen“"
        assert bp.perm_label("view_channel", guild_id=GID, scope="server") == "„Kanäle ansehen“"

    def test_latin_uses_the_english_discord_labels(self):
        import json
        def perms(lang):
            d = json.load(open(f"clashcontrol/translations/{lang}.json", encoding="utf-8"))
            return d["permissions"]["perm"], d["permissions"]["perm_server"]
        assert perms("la") == perms("en")

    def test_manage_roles_issue_uses_the_role_label(self):
        CACHE.server_config[str(GID)] = {"language": "de"}
        issue = bp.PermissionIssue("coc_roles", bp.MISSING_MANAGE_ROLES, perms=("manage_roles",))
        assert "„Rollen verwalten“" in bp.format_issue(issue, guild_id=GID)

    def test_issue_line_quotes_the_permission(self):
        CACHE.server_config[str(GID)] = {"language": "de"}
        issue = bp.PermissionIssue("leaderboard", bp.MISSING_CHANNEL_PERMS, 5, ("send_messages",))
        assert "„Nachrichten senden“" in bp.format_issue(issue, guild_id=GID)

    @pytest.mark.parametrize("lang", LANGS)
    def test_no_unquoted_permission_name_in_any_text(self, lang):
        import json
        d = json.load(open(f"clashcontrol/translations/{lang}.json", encoding="utf-8"))
        names = {v for k, v in {**d["permissions"]["perm"], **d["permissions"]["perm_server"]}.items()
                 if k != "administrator"}  # "Administrator" is also an ordinary word (a person)
        opening = d["permissions"]["quoted"].split("{name}")[0]

        def strings(o, path=""):
            for k, v in o.items():
                p = f"{path}.{k}" if path else k
                if isinstance(v, dict):
                    if p not in ("permissions.perm", "permissions.perm_server"):
                        yield from strings(v, p)
                elif isinstance(v, str):
                    yield p, v

        # Ordinary words that happen to equal a permission name (not a permission reference).
        allowed = {
            ("de", "warnotifications.description"),               # "…als persönliche Nachrichten senden"
            ("de", "commands.help.clan management.detailed"),     # menu label "Server-Rollen verwalten"
            ("de", "ui_components.clan_management.mode_roles_label"),
            ("zh", "ui_components.tracker.grant_access_invite_failed"),     # verb "无法创建邀请"; permission quoted later
            ("zh", "ui_components.tracker.grant_access_invite_dm_failed"),  # verb "已创建邀请"
            # 添加附件 ("add attachments") = the tracker's own upload button, not the permission
            ("zh", "commands.help.bug.detailed"),
            ("zh", "commands.help.feature.detailed"),
            ("zh", "ui_components.tracker.button_add_attachments"),
            ("zh", "ui_components.tracker.upload_window_expired"),
        }
        bad = []
        for path, text in strings(d):
            if (lang, path) in allowed:
                continue
            for name in names:
                start = 0
                while (i := text.find(name, start)) != -1:
                    if not text[:i].endswith(opening):
                        bad.append((path, name))
                    start = i + len(name)
        assert not bad, bad


VIEW_READ = dict(view_channel=True, read_message_history=True)


def _accounts(**players_by_user):
    CACHE.user_accounts.update({u: {"players": ps} for u, ps in players_by_user.items()})


class TestStepRegistration:
    def test_link_coverage_counts_accounts_in_member_clans(self):
        _cfg(member_clans=["#A"])
        _accounts(
            UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}, {"player_tag": "#2", "current_clan_tag": "#A"}],
            u1=[{"player_tag": "#3", "current_clan_tag": "#A", "verified": True}],
            u2=[{"player_tag": "#4", "current_clan_tag": "#OTHER"}],
        )
        assert ca.link_coverage(_guild(ADMIN)) == (1, 3, 1)

    def test_hub_off_is_incomplete(self):
        r = ca.check_registration(_guild(ADMIN))
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_hub_off")

    def test_registration_is_about_the_message_only(self):
        _cfg(member_clans=["#A"], registration_message_enabled=True, registration_channel_id="30")
        ch = _channel_by_role(30, {EVERYONE: VIEW_READ}, ALL_PERMS)
        guild = _guild(ADMIN, {30: ch})
        _accounts(UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}])   # nobody linked: not step 5's job
        r = ca.check_registration(guild)
        assert r.status == AuditStatus.OK and r.summary_params == {"channel": "#ch30"}

    def test_linked_accounts_step(self):
        _cfg(member_clans=["#A"])
        guild = _guild(ADMIN)
        assert ca.check_linked_accounts(guild).status == AuditStatus.INFO                   # no member data
        _accounts(UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}])
        r = ca.check_linked_accounts(guild)                                                  # Q-6: nobody linked
        assert r.status == AuditStatus.INCOMPLETE and r.summary_params == {"total": 1}
        problem = ca.detail_linked_accounts(guild, r, GID, "1").problem
        assert problem is not None and "/registration" in problem[1]
        _accounts(u1=[{"player_tag": "#3", "current_clan_tag": "#A"}])
        r = ca.check_linked_accounts(guild)
        assert r.status == AuditStatus.OK and r.summary_params == {"linked": 1, "total": 2}
        assert ca.detail_linked_accounts(guild, r, GID, "1").problem is None

    def test_hidden_from_newcomers_unless_the_newbie_role_sees_it(self):
        newbie = FakeRole(77, 2)
        ch = _channel_by_role(30, {EVERYONE: {}, newbie: VIEW_READ}, ALL_PERMS)
        guild = _guild(ADMIN, {30: ch}, roles={77: newbie})
        _cfg(registration_message_enabled=True, registration_channel_id="30")
        r = ca.check_registration(guild)
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_hidden_from_newcomers")
        heading, body = ca.detail_registration(guild, r, GID, "1").problem
        assert "can't see" in heading                                   # the problem comes first
        assert "“View Channel”" in body and "“Read Message History”" in body
        assert "Open settings" in body and "Select Channels" in body    # how and where to fix
        _cfg(registration_message_enabled=True, registration_channel_id="30",
             role_system_enabled=True, newbie_role_id="77")
        assert ca.check_registration(guild).status == AuditStatus.OK


class TestStepRoles:
    def test_off(self):
        assert ca.check_roles(_guild(ADMIN)).status == AuditStatus.OFF

    def test_simple_roles_without_roles_is_incomplete(self):
        _cfg(role_system_enabled=True)
        r = ca.check_roles(_guild(ADMIN))
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_simple_roles_unset")

    def test_member_clan_without_clan_role(self):
        _cfg(member_clans=["#A", "#B"], clan_role_enabled=True, clan_roles={"#A": "5"})
        r = ca.check_roles(_guild(_all_but(), roles={5: FakeRole(5, 3)}))  # incl. Manage Roles
        assert r.status == AuditStatus.INCOMPLETE and r.summary_params == {"count": 1}
        assert r.data["missing_clan_roles"] == ["#B"]

    def test_missing_manage_roles_is_broken_and_ok_lists_systems(self):
        _cfg(coc_role_enabled=True)
        assert ca.check_roles(_guild(_all_but("manage_roles"))).status == AuditStatus.BROKEN
        r = ca.check_roles(_guild(_all_but()))
        assert r.status == AuditStatus.OK and ca.summary_text(r, GID, None).endswith("CoC Roles")


class TestStepCwlSettings:
    def test_off(self):
        assert ca.check_cwl_settings(_guild(ADMIN)).status == AuditStatus.OFF

    def test_player_hub_visibility_and_activities(self):
        _cfg(cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40")
        hidden = _channel_by_role(40, {EVERYONE: {}}, ALL_PERMS)
        r = ca.check_cwl_settings(_guild(ADMIN, {40: hidden}))
        assert r.summary_key.endswith("summary_player_hub_hidden")
        no_act = _channel_by_role(40, {EVERYONE: VIEW_READ}, ALL_PERMS)
        r = ca.check_cwl_settings(_guild(ADMIN, {40: no_act}))
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_no_activities")
        ok = _channel_by_role(40, {EVERYONE: dict(VIEW_READ, use_embedded_activities=True)}, ALL_PERMS)
        r = ca.check_cwl_settings(_guild(ADMIN, {40: ok}))
        assert r.status == AuditStatus.OK and "#ch40" in ca.summary_text(r, GID, None)

    def test_hub_without_channel_is_incomplete(self):
        _cfg(cwl_management_message_enabled=True)
        r = ca.check_cwl_settings(_guild(ADMIN))
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_mgmt_no_channel")


class TestRenderingPhase3:
    @pytest.mark.parametrize("lang", LANGS)
    def test_steps_5_9_10_render_in_every_language(self, lang):
        from clashcontrol.ui_config_audit import build_step_embed
        newbie, member = FakeRole(77, 2), FakeRole(78, 3)
        ch = _channel_by_role(30, {EVERYONE: {}}, ALL_PERMS)                  # hidden + no activities
        guild = _guild(ADMIN, {30: ch}, roles={77: newbie, 78: member})
        _cfg(language=lang, member_clans=["#A", "#B"], registration_message_enabled=True,
             registration_channel_id="30", role_system_enabled=True, newbie_role_id="77",
             member_role_id="78", coc_role_enabled=True, clan_role_enabled=True, clan_roles={"#A": "78"},
             cwl_management_message_enabled=True, cwl_player_hub_message_enabled=True,
             cwl_player_hub_channel_id="30", cwl_retention_months=6, cwl_signup_mode="extended")
        _accounts(UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}])
        results = ca.run_audit(guild)
        for sid in ("registration", "roles", "cwl_settings"):
            assert results[sid].status != AuditStatus.INFO, (sid, results[sid])
            e = build_step_embed(guild, "1", sid, results[sid])
            texts = [e.title or "", e.description or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any(k in x for x in texts for k in ("config_audit.", "permissions.", "ui_components.", "cwl.")), texts
            assert all(len(f.value) <= 1024 for f in e.fields)


class TestStepWarNotifications:
    def test_info_without_linked_members(self):
        _cfg(member_clans=["#A"])
        assert ca.check_war_notifications(_guild(ADMIN)).status == AuditStatus.INFO

    def test_counts_and_none_on(self):
        _cfg(member_clans=["#A"], clan_custodians={"#A": ["11"], "#B": []})
        _accounts(u1=[{"player_tag": "#1", "current_clan_tag": "#A"}],
                  u2=[{"player_tag": "#2", "current_clan_tag": "#A"}],
                  UNASSIGNED=[{"player_tag": "#3", "current_clan_tag": "#A"}])
        r = ca.check_war_notifications(_guild(ADMIN))
        assert r.status == AuditStatus.INCOMPLETE
        CACHE.user_accounts["u1"]["notification_settings"] = {"war_reminders": True, "raid_reminders": True}
        r = ca.check_war_notifications(_guild(ADMIN))
        assert r.status == AuditStatus.OK and r.summary_params == {"war_on": 1, "members": 2}
        assert r.data["raid_on"] == 1 and r.data["custodians"] == {"#A": ["11"]}
        assert "<@11>" in _body(ca.detail_war_notifications(_guild(ADMIN), r, GID, "1"))


class TestStepCwlPlayerHub:
    def test_green_when_the_hub_works_info_otherwise(self):
        # qaplop 2026-10-11 (STAY): a working player hub is ✅, not ℹ️.
        r = ca.check_cwl_player_hub(_guild(ADMIN))
        assert r.status == AuditStatus.INFO and r.summary_key.endswith("summary_command")
        _cfg(cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40")
        members_ok = dict(view_channel=True, read_message_history=True, use_embedded_activities=True)
        hub = _channel_by_role(40, {EVERYONE: members_ok}, ALL_PERMS)
        r = ca.check_cwl_player_hub(_guild(ADMIN, {40: hub}))
        assert r.status == AuditStatus.OK and r.summary_params == {"channel": "#ch40"}

    def test_a_broken_hub_points_to_step_10(self):
        _cfg(cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40")
        hidden = _channel_by_role(40, {EVERYONE: {}}, ALL_PERMS)
        r = ca.check_cwl_player_hub(_guild(ADMIN, {40: hidden}))
        assert r.status == AuditStatus.INFO and r.summary_key.endswith("summary_hub_problem")
        assert r.summary_params == {"step": 10}
        assert ca.check_cwl_player_hub(_guild(ADMIN)).summary_key.endswith("summary_hub_problem")  # channel gone


class TestStepCwlSeason:
    from datetime import datetime as _dt, timezone as _tz
    IN_WINDOW = _dt(2026, 10, 25, 12, tzinfo=_tz.utc)    # next season 2026-11
    BEFORE = _dt(2026, 10, 5, 12, tzinfo=_tz.utc)

    def _db(self, monkeypatch, events=None, clans=None):
        monkeypatch.setattr(CACHE, "db_manager", FakeCwlDb(events, clans))

    def test_off_when_cwl_never_used(self):
        assert ca.check_cwl_season(_guild(ADMIN), self.IN_WINDOW).status == AuditStatus.OFF

    def test_sign_up_window(self, monkeypatch):
        _cfg(cwl_management_message_enabled=True)
        r = ca.check_cwl_season(_guild(ADMIN), self.IN_WINDOW)
        assert r.status == AuditStatus.INCOMPLETE and r.summary_params == {"season": "2026-11"}
        event = {"id": 1, "cwl_season": "2026-11", "status": "draft"}
        self._db(monkeypatch, [event])
        assert ca.check_cwl_season(_guild(ADMIN), self.IN_WINDOW).summary_key.endswith("summary_no_clans")
        self._db(monkeypatch, [event], {1: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": None}]})
        r = ca.check_cwl_season(_guild(ADMIN), self.IN_WINDOW)
        assert r.summary_key.endswith("summary_no_start") and r.summary_params["count"] == 1
        self._db(monkeypatch, [event], {1: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": "2026-11-01T08:00"}]})
        r = ca.check_cwl_season(_guild(ADMIN), self.IN_WINDOW)
        assert r.status == AuditStatus.OK and "Setup" in ca.summary_text(r, GID, None)

    def test_outside_the_window(self, monkeypatch):
        _cfg(cwl_management_message_enabled=True)
        assert ca.check_cwl_season(_guild(ADMIN), self.BEFORE).status == AuditStatus.INFO
        self._db(monkeypatch, [{"id": 2, "cwl_season": "2026-10", "status": "war"}],
                 {2: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": "2026-10-01T08:00", "locked_at": "x"}]})
        r = ca.check_cwl_season(_guild(ADMIN), self.BEFORE)
        assert r.status == AuditStatus.OK and r.summary_params["season"] == "2026-10"

    def test_window_opens_when_this_months_cwl_is_over(self, monkeypatch):
        # qaplop 2026-10-11: next month's season can start as soon as this month's CWL is over for
        # all clans (the 11th at the latest) — not only from the 20th.
        _cfg(cwl_management_message_enabled=True)
        from datetime import datetime, timezone
        october = [{"id": 2, "cwl_season": "2026-10", "status": "war"}]
        clans = {2: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": "2026-10-01T08:00", "locked_at": "x"},
                     {"clan_tag": "#B", "participating": 1, "cwl_start_at": "2026-10-02T08:00", "locked_at": "x"}]}
        self._db(monkeypatch, october, clans)
        still_running = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)   # #B's CWL ends 10-11 08:00
        assert ca.check_cwl_season(_guild(ADMIN), still_running).status == AuditStatus.OK
        over = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)
        r = ca.check_cwl_season(_guild(ADMIN), over)                      # ℹ️ "can be started now"
        assert r.status == AuditStatus.INFO and r.summary_key.endswith("summary_can_start")
        assert r.summary_params == {"season": "2026-11"}
        due = datetime(2026, 10, 20, 0, tzinfo=timezone.utc)              # ⚠️ from the 20th
        r = ca.check_cwl_season(_guild(ADMIN), due)
        assert r.status == AuditStatus.INCOMPLETE and r.summary_params == {"season": "2026-11"}
        clans[2].pop()                                                    # only #A: over on the 10th
        assert ca.check_cwl_season(_guild(ADMIN), still_running).summary_key.endswith("summary_can_start")
        self._db(monkeypatch, [], {})                                     # no season this month
        assert ca.check_cwl_season(_guild(ADMIN), over).summary_key.endswith("summary_can_start")
        assert ca.check_cwl_season(_guild(ADMIN), still_running).summary_key.endswith("summary_between")


class TestRenderingPhase4:
    @pytest.mark.parametrize("lang", LANGS)
    def test_steps_6_11_12_render_in_every_language(self, lang, monkeypatch):
        from clashcontrol.ui_config_audit import build_step_embed
        monkeypatch.setattr(CACHE, "db_manager", FakeCwlDb(
            [{"id": 1, "cwl_season": "2026-11", "status": "draft"}],
            {1: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": None}]}))
        _cfg(language=lang, member_clans=["#A"], clan_custodians={"#A": ["11"]},
             cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40",
             cwl_clan_coordinators={"#A": ["12"]})
        _accounts(u1=[{"player_tag": "#1", "current_clan_tag": "#A"}])
        guild = _guild(ADMIN, {40: _channel(40)})
        for sid in ("war_notifications", "cwl_player_hub", "cwl_season"):
            r = ca.run_step(ca.STEPS_BY_ID[sid], guild)
            assert r.summary_key != "config_audit.summary_check_failed", (sid, r)
            e = build_step_embed(guild, "1", sid, r)
            texts = [e.title or "", e.description or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any(k in x for x in texts for k in ("config_audit.", "permissions.", "ui_components.", "cwl.", "playerregistration.")), texts
            assert all(len(f.value) <= 1024 for f in e.fields)


class TestOpenSettings:
    """Phase 5: "⚙️ Open settings" opens the /clan management panel in the step's mode."""

    def test_modes_per_step(self):
        expected = {"bot_permissions": None, "basics": "config", "clans": "config", "welcome": "config",
                    "war_notifications": "notifications", "channel_war_notifications": "config",
                    "raid_reminders": "config", "roles": "roles", "cwl_settings": "cwl_settings",
                    "cwl_player_hub": "cwl_settings", "cwl_season": "cwl_management", "leaderboards": None,
                    "registration": "config", "linked_accounts": "registrations"}
        dummy = ca.StepResult(AuditStatus.OK, "x")
        for sid, mode in expected.items():
            step = ca.STEPS_BY_ID[sid]
            assert (step.fix_mode(dummy) if step.fix_mode else None) == mode, sid

    @pytest.mark.asyncio
    async def test_button_only_where_a_screen_exists(self):
        from clashcontrol import ui_config_audit as ui
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        ids = lambda: {getattr(c, "custom_id", None) for c in view.children}   # noqa: E731
        assert "setup_open_settings" not in ids()                       # overview
        for sid, shown in (("basics", True), ("bot_permissions", False), ("leaderboards", False),
                           ("roles", True)):
            view.selected = sid
            view._rebuild()
            assert ("setup_open_settings" in ids()) is shown, sid
        _cfg(channel_raid_notifications_enabled=True)                   # no clans → step 8 blocked
        view.results = ca.run_audit(guild)
        view.selected = "raid_reminders"
        view._rebuild()
        assert "setup_open_settings" not in ids()
        view.stop()

    @pytest.mark.asyncio
    async def test_panel_is_sent_ephemerally_and_closed_later(self, monkeypatch):
        from clashcontrol import ui_clan_management, ui_config_audit as ui
        panel = SimpleNamespace(sent_message=None)
        build = AsyncMock(return_value=([discord.Embed(title="x")], panel))
        monkeypatch.setattr(ui_clan_management, "build_clan_management_panel", build)
        spawned = []
        monkeypatch.setattr(QBcore, "spawn_tracked", lambda name, coro: spawned.append((name, coro)))
        message = SimpleNamespace(edit=AsyncMock())
        it = MagicMock()
        it.user.id = 1
        it.response.defer = AsyncMock()
        it.followup.send = AsyncMock(return_value=message)
        await ui.open_settings_panel(it, _guild(ADMIN), "roles")
        build.assert_awaited_once()
        assert build.await_args.kwargs["mode"] == "roles"
        kw = it.followup.send.await_args.kwargs
        assert kw["ephemeral"] is True and kw["view"] is panel and kw["wait"] is True
        assert panel.sent_message is message
        assert len(spawned) == 1
        spawned[0][1].close()                                          # don't sleep 14 minutes

    @pytest.mark.asyncio
    async def test_panel_message_knows_its_guild(self, monkeypatch):
        """Tracker #0153: a follow-up message has no guild in discord.py; the panel's Refresh
        rebuilt itself from it and lost every CWL button (and its language)."""
        from clashcontrol import ui_clan_management, ui_config_audit as ui
        panel = SimpleNamespace(sent_message=None)
        monkeypatch.setattr(ui_clan_management, "build_clan_management_panel",
                            AsyncMock(return_value=([discord.Embed(title="x")], panel)))
        spawned = []
        monkeypatch.setattr(QBcore, "spawn_tracked", lambda name, coro: spawned.append(coro))
        state = MagicMock()
        state._get_guild.return_value = None
        channel = discord.PartialMessageable(state=state, id=5, guild_id=None)  # what a follow-up gets
        message = discord.WebhookMessage(state=state, channel=channel, data={
            "id": "9", "channel_id": "5", "type": 0, "content": "", "attachments": [], "embeds": [],
            "mentions": [], "mention_roles": [], "pinned": False, "mention_everyone": False, "tts": False,
            "timestamp": "2026-10-11T17:22:00+00:00", "edited_timestamp": None,
            "author": {"id": "1", "username": "cc", "discriminator": "0", "avatar": None}})
        assert message.guild is None
        it = MagicMock()
        it.user.id = 1
        it.response.defer = AsyncMock()
        it.followup.send = AsyncMock(return_value=message)
        guild = _guild(ADMIN)
        await ui.open_settings_panel(it, guild, "cwl_management")
        assert panel.sent_message is message and message.guild is guild
        spawned[0].close()


class TestBuildClanManagementPanel:
    @pytest.mark.asyncio
    async def test_works_without_clans_in_config_mode(self, monkeypatch):
        from clashcontrol import QBdiscocmdshelper, ui_clan_management
        fmt = AsyncMock(return_value=(discord.Embed(title="cfg"), None, [], []))
        monkeypatch.setattr(QBdiscocmdshelper, "format_clan_management_message", fmt)
        guild = _guild(ADMIN)
        embeds, view = await ui_clan_management.build_clan_management_panel(guild, mode="config", timeout=60)
        assert [e.title for e in embeds] == ["cfg"] and view.mode == "config"
        assert view.sent_message.guild is guild and view.clan_tag is None
        assert fmt.await_args.args[0] is None
        view.stop()


# ---------------------------------------------------------------------------------------------
# Phase 6: "we don't use this" + guided setup + member flows
# ---------------------------------------------------------------------------------------------

class TestDismissal:
    def test_dismissed_off_or_incomplete_shows_off(self):
        CACHE.guild_setup_dismissed[str(GID)] = {"welcome", "leaderboards", "registration"}
        _cfg(member_clans=["#A"])
        results = ca.run_audit(_guild(ADMIN))
        for sid in ("welcome", "leaderboards", "registration"):     # OFF, OFF, INCOMPLETE before
            assert results[sid].status == AuditStatus.OFF
            assert results[sid].summary_key == "config_audit.summary_dismissed"
            assert results[sid].data["dismissed"] is True

    def test_dismissed_but_broken_shows_the_real_status(self):
        CACHE.guild_setup_dismissed[str(GID)] = {"welcome"}
        _cfg(welcome_message_enabled=True)                            # no system channel -> broken
        r = ca.run_audit(_guild(ADMIN))["welcome"]
        assert r.status == AuditStatus.BROKEN and r.data["dismissed"] is True

    def test_what_can_be_dismissed(self):
        incomplete = ca.StepResult(AuditStatus.INCOMPLETE, "x")
        assert not ca.can_dismiss(ca.STEPS_BY_ID["clans"], incomplete)          # basics: never
        assert not ca.can_dismiss(ca.STEPS_BY_ID["bot_permissions"], incomplete)
        assert ca.can_dismiss(ca.STEPS_BY_ID["welcome"], incomplete)
        assert not ca.can_dismiss(ca.STEPS_BY_ID["welcome"], ca.StepResult(AuditStatus.BROKEN, "x"))
        assert not ca.can_dismiss(ca.STEPS_BY_ID["welcome"],
                                  ca.StepResult(AuditStatus.OFF, "x", data={"dismissed": True}))

    @pytest.mark.asyncio
    async def test_db_round_trip_and_cache_write_through(self, tmp_path, monkeypatch):
        from clashcontrol.db_manager import WarHistoryDB
        db = WarHistoryDB()
        await db.initialize(str(tmp_path / "setup_dismissed.db"))
        try:
            monkeypatch.setattr(CACHE, "db_manager", db)
            assert await CACHE.set_setup_step_dismissed(GID, "welcome", True)
            assert await CACHE.set_setup_step_dismissed(GID, "welcome", True)     # idempotent
            assert db.get_all_guild_setup_dismissed_sync() == {str(GID): {"welcome"}}
            assert CACHE.guild_setup_dismissed == {str(GID): {"welcome"}}
            assert await CACHE.set_setup_step_dismissed(GID, "welcome", False)
            assert db.get_all_guild_setup_dismissed_sync() == {}
            assert CACHE.guild_setup_dismissed[str(GID)] == set()
        finally:
            await db.close()


def _click_it():
    it = MagicMock()
    it.data = {}
    it.user.id = 1
    it.response.edit_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.is_done = MagicMock(return_value=False)
    return it


def _ids(view):
    return {getattr(c, "custom_id", None) for c in view.children}


class TestGuidedSetup:
    @pytest.mark.asyncio
    async def test_starts_at_first_step_needing_attention_and_walks_to_done(self):
        from clashcontrol import ui_config_audit as ui
        guild = _guild(ADMIN)                 # no clans: step 3 is the first to need attention
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        assert "setup_guided_start" in _ids(view)
        await view._on_start_guided(_click_it())
        assert view.guided and view.selected == "clans"
        assert "🧭" in (view.current_embed().author.name or "")
        await view._on_previous(_click_it())
        assert view.selected == "basics"
        view.selected = "linked_accounts"
        await view._on_next(_click_it())
        assert view.selected == ui._DONE
        assert "setup_guided_next" not in _ids(view)
        await view._on_back(_click_it())
        assert view.selected is None and not view.guided
        view.stop()

    @pytest.mark.asyncio
    async def test_tour_from_step_1_when_nothing_needs_attention(self):
        from clashcontrol import ui_config_audit as ui
        CACHE.guild_setup_dismissed[str(GID)] = {s.id for s in ca.STEPS if s.dismissable}
        _cfg(member_clans=["#A"])
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        await view._on_start_guided(_click_it())
        assert view.selected == "bot_permissions"
        assert view.children[0].disabled                                    # no "Previous" on step 1
        view.stop()

    @pytest.mark.asyncio
    async def test_member_flow_buttons_on_steps_5_6_11(self, monkeypatch):
        from clashcontrol import tutorial_flows, ui_config_audit as ui
        _cfg(member_clans=["#A"])
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        view.guided = True
        flows = {}
        for sid in ("registration", "war_notifications", "cwl_player_hub", "roles"):
            view.selected = sid
            view._rebuild()
            flows[sid] = sorted(i for i in _ids(view) if str(i).startswith("setup_flow_"))
        assert flows == {"registration": ["setup_flow_0", "setup_flow_1"], "war_notifications": ["setup_flow_0"],
                         "cwl_player_hub": ["setup_flow_0"], "roles": []}
        opened = AsyncMock()
        monkeypatch.setattr(tutorial_flows, "open_my_accounts", opened)
        view.selected = "registration"
        view._rebuild()
        it = _click_it()
        await view._make_flow_callback(1)(it)
        opened.assert_awaited_once_with(it, GID)
        view.stop()

    @pytest.mark.asyncio
    async def test_dismiss_and_undo_from_the_page(self, monkeypatch):
        from clashcontrol import ui_config_audit as ui

        async def fake_set(guild_id, step_id, dismissed):
            steps = CACHE.guild_setup_dismissed.setdefault(str(guild_id), set())
            (steps.add if dismissed else steps.discard)(step_id)
            return True
        monkeypatch.setattr(CACHE, "set_setup_step_dismissed", fake_set)
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild), selected="welcome")
        assert "setup_dismiss" in _ids(view)
        await view._on_dismiss(_click_it())
        assert view.results["welcome"].summary_key == "config_audit.summary_dismissed"
        assert "setup_undismiss" in _ids(view) and "setup_dismiss" not in _ids(view)
        await view._on_undismiss(_click_it())
        assert view.results["welcome"].status == AuditStatus.OFF and "setup_dismiss" in _ids(view)
        view.selected = "clans"
        view._rebuild()
        assert "setup_dismiss" not in _ids(view)                               # never for the basics
        view.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang", LANGS)
    async def test_guided_pages_render_in_every_language(self, lang):
        from clashcontrol import ui_config_audit as ui
        _cfg(language=lang, member_clans=["#A"])
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        view.guided = True
        for sid in [s.id for s in ca.STEPS] + [ui._DONE]:
            view.selected = sid
            view._rebuild()
            e = view.current_embed()
            texts = [e.title or "", e.description or "", (e.author.name or "")] + \
                [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any("config_audit." in x for x in texts), (sid, texts)
            labels = [getattr(c, "label", "") or "" for c in view.children]
            assert not any("config_audit." in x for x in labels), labels
        view.stop()


class TestTutorialFlows:
    @pytest.mark.asyncio
    async def test_registration_buttons_run_behind_the_hub_gate(self, monkeypatch):
        from clashcontrol import tutorial_flows, ui_registration
        calls = []

        class FakeHub:
            def __init__(self, guild_id):
                calls.append(("init", guild_id))
                self.link_account_button = SimpleNamespace(
                    callback=AsyncMock(side_effect=lambda i: calls.append("link")))

            async def interaction_check(self, interaction):
                calls.append("check")
                return True

            def stop(self):
                calls.append("stop")

        monkeypatch.setattr(ui_registration, "RegistrationView", FakeHub)
        await tutorial_flows.open_link_account(_click_it(), GID)
        assert calls == [("init", GID), "check", "link", "stop"]

    @pytest.mark.asyncio
    async def test_cwl_preferences_launch_the_real_activity(self, monkeypatch):
        from clashcontrol import tutorial_flows, ui_cwl_roster
        launch = AsyncMock()
        monkeypatch.setattr(ui_cwl_roster, "_launch_cwl_activity", launch)
        it = _click_it()
        await tutorial_flows.open_cwl_preferences(it, GID)
        launch.assert_awaited_once_with(it, GID, "player_prefs")

    def test_has_linked_accounts(self):
        from clashcontrol.tutorial_flows import user_has_linked_accounts
        assert not user_has_linked_accounts("u1")
        _accounts(u1=[{"player_tag": "#1"}])
        assert user_has_linked_accounts("u1")



class TestProblemFirstLayout:
    """qaplop 2026-10-11: a step with a problem opens with ONE dominant block — what's wrong and
    the numbered fix — and the information comes below it."""

    def test_problem_block_leads_the_page(self):
        from clashcontrol.ui_config_audit import build_step_embed
        _cfg(member_clans=["#A"], channel_raid_notifications_enabled=True)          # on, no channel
        guild = _guild(ADMIN)
        r = ca.run_audit(guild)["raid_reminders"]
        e = build_step_embed(guild, "1", "raid_reminders", r)
        assert e.description.startswith("## ⚠️")
        assert "1." in e.description and "Check again" in e.description
        assert e.fields[0].value.startswith("**ℹ️")                                  # "About this step"

    def test_ok_page_has_no_problem_block(self):
        from clashcontrol.ui_config_audit import build_step_embed
        guild = _guild(ADMIN)
        r = ca.run_audit(guild)["basics"]
        e = build_step_embed(guild, "1", "basics", r)
        assert not e.description.startswith("##")

    @pytest.mark.parametrize("lang", LANGS)
    def test_every_problem_block_renders_in_every_language(self, lang, monkeypatch):
        """Every step in a problem state, every language: no raw keys, within Discord limits."""
        from clashcontrol.ui_config_audit import build_step_embed
        monkeypatch.setattr(CACHE, "db_manager", FakeCwlDb([], {}))
        CACHE.clan_name_cache.update({"#A": {"name": "Alpha", "warlog_is_public": False}})
        _cfg(language=lang, member_clans=["#A"], welcome_message_enabled=True, welcome_message_mode="apply_channel",
             registration_message_enabled=True, registration_channel_id="30",
             channel_war_notifications_enabled=True, channel_raid_notifications_enabled=True,
             role_system_enabled=True, clan_role_enabled=True,
             cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="30", cwl_management_message_enabled=True)
        _accounts(UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}])
        ch = _channel_by_role(30, {EVERYONE: {}}, ALL_PERMS)
        guild = _guild(_all_but("attach_files"), {30: ch}, _channel(10, **ALL_PERMS))
        results = ca.run_audit(guild)
        with_problem = 0
        for step in ca.STEPS:
            e = build_step_embed(guild, "1", step.id, results[step.id])
            with_problem += (e.description or "").startswith("## ")
            texts = [e.title or "", e.description or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
            assert not any(k in x for x in texts for k in ("config_audit.", "permissions.", "ui_components.", "cwl.")), (step.id, texts)
            assert len(e.description or "") <= 4096 and all(len(f.value) <= 1024 for f in e.fields)
        assert with_problem >= 8



class TestMemberAccessGroups:
    """qaplop 2026-10-11 (STAY server): members reach the player hub if ONE group fully works —
    @everyone, the Simple Roles member role, ALL four CoC roles, or EVERY clan role."""

    COC = {"coc_role_member_id": "51", "coc_role_elder_id": "52", "coc_role_coleader_id": "53", "coc_role_leader_id": "54"}
    OK = dict(view_channel=True, read_message_history=True, use_embedded_activities=True)

    def _guild_with(self, channel_perms):
        roles = {i: FakeRole(i, 2) for i in (51, 52, 53, 54, 61, 62, 70)}
        # Every role is listed: one without channel permissions gets none (not the bot's).
        ch = _channel_by_role(40, {r: channel_perms.get(i, {}) for i, r in roles.items()} | {EVERYONE: {}}, ALL_PERMS)
        return _guild(ADMIN, {40: ch}, roles=roles)

    def _status(self, guild, **cfg):
        _cfg(member_clans=["#A", "#B"], cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40", **cfg)
        return ca.check_cwl_settings(guild)

    def test_all_four_coc_roles_cover_members(self):
        guild = self._guild_with({51: self.OK, 52: self.OK, 53: self.OK, 54: self.OK})
        assert self._status(guild, coc_role_enabled=True, **self.COC).status == AuditStatus.OK

    def test_three_of_four_coc_roles_are_not_enough(self):
        guild = self._guild_with({51: self.OK, 52: self.OK, 53: self.OK})
        r = self._status(guild, coc_role_enabled=True, **self.COC)
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_player_hub_hidden")

    def test_coc_roles_only_count_while_coc_roles_are_on(self):
        guild = self._guild_with({51: self.OK, 52: self.OK, 53: self.OK, 54: self.OK})
        assert self._status(guild, coc_role_enabled=False, **self.COC).status == AuditStatus.INCOMPLETE

    def test_every_clan_role_or_the_member_role(self):
        guild = self._guild_with({61: self.OK, 62: self.OK})
        assert self._status(guild, clan_role_enabled=True, clan_roles={"#A": "61", "#B": "62"}).status == AuditStatus.OK
        guild = self._guild_with({61: self.OK})                       # #B's clan role can't
        assert self._status(guild, clan_role_enabled=True, clan_roles={"#A": "61", "#B": "62"}).status == AuditStatus.INCOMPLETE
        guild = self._guild_with({70: self.OK})
        assert self._status(guild, role_system_enabled=True, member_role_id="70").status == AuditStatus.OK

    def test_activities_checked_per_group_too(self):
        no_act = dict(view_channel=True, read_message_history=True)
        guild = self._guild_with({51: no_act, 52: no_act, 53: no_act, 54: no_act})
        r = self._status(guild, coc_role_enabled=True, **self.COC)
        assert r.summary_key.endswith("summary_no_activities")


class TestRealTextChannelActivities:
    """discord.py's TextChannel.permissions_for() clears every 'voice' permission, and it still
    counts "Use Activities" as one — on a real text channel the check always failed (STAY,
    2026-10-11). _channel_permissions() resolves it as Discord does."""

    def test_use_activities_survives_on_a_real_text_channel(self):
        state = MagicMock()
        state.self_id = 999
        guild = MagicMock()
        guild.id, guild.owner_id = 1000, 1

        def role(rid, perms=0):
            return discord.Role(guild=guild, state=state, data={
                "id": str(rid), "name": f"r{rid}", "permissions": str(perms), "position": 1, "color": 0,
                "hoist": False, "managed": False, "mentionable": False, "flags": 0})

        guild.default_role = role(1000, discord.Permissions(view_channel=True).value)
        leader = role(5)
        allow = discord.Permissions(view_channel=True, read_message_history=True, use_embedded_activities=True).value
        channel = discord.TextChannel(state=state, guild=guild, data={
            "id": "30", "type": 0, "name": "cwl", "position": 0,
            "permission_overwrites": [{"id": "5", "type": 0, "allow": str(allow), "deny": "0"}]})
        assert channel.permissions_for(leader).use_embedded_activities is False   # discord.py's strip
        assert ca._channel_permissions(channel, leader).use_embedded_activities is True
        assert ca._channel_permissions(channel, leader).view_channel is True


class TestSummaryInReadersLanguage:
    """Tracker #0152 (qaplop): a German server, an English admin — step 7 read "2 Stunden before
    the war ends". A check never translates; the summary is rendered in the reader's language."""

    LAZY_STEPS = ("channel_war_notifications", "raid_reminders", "roles", "cwl_settings")

    def test_no_server_language_text_in_an_english_summary(self, monkeypatch):
        _cfg(language="de", member_clans=["#A"], channel_war_notifications_enabled=True,
             war_notification_channel_id="40", war_notification_threshold_hours=2.0,
             channel_raid_notifications_enabled=True, raid_notification_channel_id="40",
             coc_role_enabled=True, cwl_management_message_enabled=True, cwl_management_channel_id="40")
        monkeypatch.setattr(ca, "_permission_issues", lambda guild, features: [])
        guild = _guild(ADMIN, {40: _channel(40)})
        import clashcontrol.i18n as i18n
        monkeypatch.setattr(i18n, "get_user_language", lambda uid: "en" if uid == "en_admin" else None)
        monkeypatch.setattr(i18n, "get_guild_language", lambda gid: "de")
        r = ca.check_channel_war_notifications(guild)
        assert r.status == AuditStatus.OK
        assert ca.summary_text(r, GID, "en_admin").endswith("2 hours before war end")
        assert "2 Stunden" in ca.summary_text(r, GID, "de_admin")
        for sid in self.LAZY_STEPS:
            r = ca.run_step(ca.STEPS_BY_ID[sid], guild)
            assert r.summary_key != "config_audit.summary_check_failed", sid
            assert all(not isinstance(v, str) or v.startswith("#") or v.isdigit()
                       for v in r.summary_params.values()), (sid, r.summary_params)


class TestToggleStatusLine:
    """qaplop 2026-10-11: an on/off step reads "Status: 🔴 Disabled" like /clan management, not
    "off" + "Off. When it is on …"."""

    def test_switch_steps(self):
        assert {s.id for s in ca.STEPS if s.toggle_key} == {
            "welcome", "registration", "channel_war_notifications", "raid_reminders"}

    @pytest.mark.parametrize("lang", LANGS)
    def test_disabled_and_enabled_pages(self, lang):
        from clashcontrol.ui_config_audit import build_step_embed
        _cfg(language=lang)
        guild = _guild(ADMIN)
        r = ca.run_step(ca.STEPS_BY_ID["welcome"], guild)
        e = build_step_embed(guild, "1", "welcome", r)
        assert e.description.count("🔴") == 1 and "🟢" not in e.description
        assert "\n" not in e.description                       # no second "off" line
        _cfg(language=lang, welcome_message_enabled=True)
        r = ca.run_step(ca.STEPS_BY_ID["welcome"], guild)
        e = build_step_embed(guild, "1", "welcome", r)
        if r.status == AuditStatus.OK:
            assert e.description.startswith(e.description.split("🟢")[0]) and "🟢" in e.description



class TestRegistrationWhyAndTipOnTop:
    """qaplop 2026-10-11: step 5 says why registration matters; the /tutorial tip stays last."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_step_5_explains_why(self, lang):
        _cfg(language=lang)
        guild = _guild(ADMIN)
        detail = ca.detail_registration(guild, ca.check_registration(guild), GID, "1")
        body = ca.what_text("registration", GID, "1")
        assert "{" not in body and len(body) <= 1024 and detail.sections

    @pytest.mark.asyncio
    async def test_tutorial_tip_is_at_the_end(self):
        from clashcontrol import ui_config_audit as ui
        guild = _guild(ADMIN)
        view = ui.SetupView(guild, "1", ca.run_audit(guild))
        await view._on_start_guided(_click_it())
        embed = view.current_embed()
        assert view._tutorial_hint and not embed.description.startswith("🎓")
        assert embed.fields[-1].value.startswith("🎓")
        view.stop()


class TestRoleSystemsExplained:
    """qaplop 2026-10-11: step 9 explains each role system (what, how, what for), on or off."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_every_system_has_explanation_and_status(self, lang):
        _cfg(language=lang)
        guild = _guild(ADMIN)
        detail = ca.detail_roles(guild, ca.check_roles(guild), GID, "1")
        bodies = [b for h, b in detail.sections if h.startswith("⏸️")]
        assert len(bodies) == 3
        for body in bodies:
            assert "🔴" in body and len(body) > 60 and "{" not in body and len(body) <= 1024



class TestClansExplained:
    """qaplop 2026-10-11: step 3 explains member clans and families also when they're set up."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_explanation_first_when_clans_exist(self, lang):
        _cfg(language=lang, member_clans=["#A"])
        guild = _guild(ADMIN)
        result = ca.StepResult(AuditStatus.OK, "x", data={"clans": [("#A", "ok")], "families": []})
        body = ca.what_text("clans", GID, "1")
        assert "{" not in body and len(body) <= 1024 and ca.detail_clans(guild, result, GID, "1").sections



class TestEveryStepExplainsItself:
    """qaplop 2026-10-11: every step page opens its information with "💡 What this is" — in every
    state, not only when something is missing."""

    def test_registry_covers_every_step(self):
        assert set(ca.WHAT_KEYS) == {s.id for s in ca.STEPS}

    @pytest.mark.parametrize("lang", LANGS)
    def test_every_page_shows_it(self, lang):
        from clashcontrol.ui_config_audit import build_step_embed
        _cfg(language=lang)
        guild = _guild(ADMIN)
        title = ca._translator(GID, None)("config_audit.what_title")
        for step in ca.STEPS:
            r = ca.run_step(step, guild)
            if r.status == AuditStatus.BLOCKED:
                continue
            e = build_step_embed(guild, "1", step.id, r)
            names = [f.name for f in e.fields]
            assert title in names, (step.id, names)
            value = e.fields[names.index(title)].value
            assert value and "{" not in value and "config_audit." not in value, (step.id, value)

