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
    ch.permissions_for = MagicMock(side_effect=lambda who: discord.Permissions(**by_role.get(who, bot)))
    return ch


def _all_but(*missing: str) -> discord.Permissions:
    return discord.Permissions(**{p: True for p in bp.INSTALL_PERMISSIONS if p != "administrator" and p not in missing})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(CACHE, "clan_name_cache", {})
    monkeypatch.setattr(CACHE, "clan_families", {})
    monkeypatch.setattr(CACHE, "guild_guest_clans", {})
    monkeypatch.setattr(CACHE, "user_accounts", {})
    monkeypatch.setattr(CACHE, "db_manager", FakeCwlDb())
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
    def test_thirteen_steps_in_order(self):
        assert [s.order for s in ca.STEPS] == list(range(1, 14))
        assert ca.STEPS[0].id == "bot_permissions"
        for s in ca.STEPS:
            assert all(r in ca.STEPS_BY_ID for r in s.requires)

    def test_unimplemented_steps_are_pending(self):
        results = ca.run_audit(_guild(discord.Permissions(administrator=True)))
        assert list(results) == [s.id for s in ca.STEPS]
        assert [s.id for s in ca.STEPS if results[s.id].status == AuditStatus.PENDING] == [
            s.id for s in ca.STEPS if s.check is None
        ]
        assert all(s.check is not None and s.detail is not None for s in ca.STEPS)  # Phase 4: all 13

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


ADMIN = discord.Permissions(administrator=True)
ALL_PERMS = dict(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)


def _cfg(**kw) -> Dict:
    CACHE.server_config[str(GID)] = kw
    return kw


def _body(detail: ca.StepDetail) -> str:
    return "\n".join(b for _, b in detail.sections)


class TestStepBasics:
    def test_defaults_are_ok_with_hints(self):
        r = ca.check_basics(_guild(ADMIN))
        assert r.status == AuditStatus.OK
        assert {f.code for f in r.findings} == {"default_language", "default_timezone"}
        body = _body(ca.detail_basics(_guild(ADMIN), r, GID, "1"))
        assert "UTC" in body and "/clan management" in body

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

    def test_ok_with_coverage_only_when_someone_is_linked(self):
        _cfg(member_clans=["#A"], registration_message_enabled=True, registration_channel_id="30")
        ch = _channel_by_role(30, {EVERYONE: VIEW_READ}, ALL_PERMS)
        guild = _guild(ADMIN, {30: ch})
        assert ca.check_registration(guild).summary_key.endswith("summary_ok")          # no data: no number
        _accounts(UNASSIGNED=[{"player_tag": "#1", "current_clan_tag": "#A"}])
        r = ca.check_registration(guild)                                                   # Q-6: nobody linked
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_nobody_linked")
        _accounts(u1=[{"player_tag": "#3", "current_clan_tag": "#A"}])
        r = ca.check_registration(guild)
        assert r.status == AuditStatus.OK and r.summary_params == {"channel": "#ch30", "linked": 1, "total": 2}

    def test_hidden_from_newcomers_unless_the_newbie_role_sees_it(self):
        newbie = FakeRole(77, 2)
        ch = _channel_by_role(30, {EVERYONE: {}, newbie: VIEW_READ}, ALL_PERMS)
        guild = _guild(ADMIN, {30: ch}, roles={77: newbie})
        _cfg(registration_message_enabled=True, registration_channel_id="30")
        r = ca.check_registration(guild)
        assert r.status == AuditStatus.INCOMPLETE and r.summary_key.endswith("summary_hidden_from_newcomers")
        body = _body(ca.detail_registration(guild, r, GID, "1"))
        assert "“View Channel”" in body and "“Read Message History”" in body
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
        assert r.status == AuditStatus.OK and r.summary_params == {"systems": "CoC Roles"}


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
        assert r.status == AuditStatus.OK and "#ch40" in r.summary_params["parts"]

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
            assert results[sid].status not in (AuditStatus.PENDING, AuditStatus.INFO), (sid, results[sid])
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
    def test_always_info(self):
        assert ca.check_cwl_player_hub(_guild(ADMIN)).summary_key.endswith("summary_command")
        _cfg(cwl_player_hub_message_enabled=True, cwl_player_hub_channel_id="40")
        r = ca.check_cwl_player_hub(_guild(ADMIN, {40: _channel(40)}))
        assert r.status == AuditStatus.INFO and r.summary_params == {"channel": "#ch40"}


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
        assert r.status == AuditStatus.OK and r.summary_params["phase"] == "Setup"

    def test_outside_the_window(self, monkeypatch):
        _cfg(cwl_management_message_enabled=True)
        assert ca.check_cwl_season(_guild(ADMIN), self.BEFORE).status == AuditStatus.INFO
        self._db(monkeypatch, [{"id": 2, "cwl_season": "2026-10", "status": "war"}],
                 {2: [{"clan_tag": "#A", "participating": 1, "cwl_start_at": "2026-10-01T08:00", "locked_at": "x"}]})
        r = ca.check_cwl_season(_guild(ADMIN), self.BEFORE)
        assert r.status == AuditStatus.OK and r.summary_params["season"] == "2026-10"


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
