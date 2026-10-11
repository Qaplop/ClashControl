"""/tutorial — the member tutorial (tracker #0121): storybook registry, stored progress, the
"run this command" reaction, the step pages, and the shared member flows they open.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import tutorial as tut  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402

UID = "4711"
GID = 909090
LANGS = ("en", "de", "es", "zh", "la")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # A fresh manager from the real translation files: other tests swap the shared one's
    # translations for fakes, so in a full run rendered texts must not depend on test order
    # (same as test_simple_roles_texts.py).
    from clashcontrol import i18n
    fresh = i18n.TranslationManager()
    fresh.load_translations()
    monkeypatch.setattr(i18n, "_translation_manager", fresh)
    monkeypatch.setattr(CACHE, "tutorial_progress", {})
    monkeypatch.setattr(CACHE, "user_accounts", {})
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "pending_registration_dm_guild", {})

    async def fake_set(discord_id, track, step_id, done):
        CACHE.tutorial_progress.setdefault(discord_id, {})[track] = {
            "step": step_id, "done": set(done), "updated_at": datetime.now(timezone.utc).isoformat()}
        return True
    monkeypatch.setattr(CACHE, "set_tutorial_progress", fake_set)


def _click(guild=True):
    it = MagicMock()
    it.user.id = int(UID)
    it.guild = SimpleNamespace(id=GID) if guild else None
    it.response.edit_message = AsyncMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.is_done = MagicMock(return_value=False)
    it.followup.send = AsyncMock()
    return it


class TestStorybook:
    def test_order_and_commands_exist_in_help(self):
        import QBdiscordcmds
        assert tut.STEP_IDS == ["welcome", "help", "link_account", "my_accounts", "war_reminders",
                                "whois", "leaderboard", "cwl_preferences", "done"]
        help_names = set(QBdiscordcmds._get_help_command_names())
        for step in tut.STEPS:
            if step.command:
                assert step.command in help_names, step.command
        assert "tutorial" in help_names

    def test_flows_exist_in_the_shared_module(self):
        from clashcontrol import tutorial_flows
        for step in tut.STEPS:
            for _key, flow in step.flows:
                assert callable(getattr(tutorial_flows, flow)), flow


class TestProgressStorage:
    @pytest.mark.asyncio
    async def test_db_round_trip_and_user_deletion(self, tmp_path, monkeypatch):
        from clashcontrol.cache_manager import CacheManager
        from clashcontrol.db_manager import WarHistoryDB
        db = WarHistoryDB()
        await db.initialize(str(tmp_path / "tutorial.db"))
        try:
            monkeypatch.setattr(CACHE, "db_manager", db)
            monkeypatch.setattr(CACHE, "set_tutorial_progress",
                                CacheManager.set_tutorial_progress.__get__(CACHE))  # the real one
            assert await CACHE.set_tutorial_progress(UID, "user", "whois", {"help", "welcome"})
            loaded = db.get_all_tutorial_progress_sync()
            assert loaded[UID]["user"]["step"] == "whois" and loaded[UID]["user"]["done"] == {"help", "welcome"}
            assert CACHE.tutorial_progress[UID]["user"]["step"] == "whois"
            await db.delete_user(UID)                       # no FK: removed explicitly
            assert db.get_all_tutorial_progress_sync() == {}
        finally:
            await db.close()


class TestCommandReaction:
    @pytest.mark.asyncio
    async def test_running_the_asked_command_completes_the_step(self, monkeypatch):
        await tut.save_progress(UID, "help", {"welcome"})
        it = _click()
        await tut.on_command_completed(it, SimpleNamespace(qualified_name="help", name="help"))
        progress = tut.get_progress(UID)
        assert progress["step"] == "link_account" and progress["done"] == {"welcome", "help"}
        it.followup.send.assert_awaited_once()
        assert it.followup.send.await_args.kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_other_commands_old_or_finished_tutorials_do_nothing(self):
        it = _click()
        await tut.on_command_completed(it, SimpleNamespace(qualified_name="help", name="help"))  # never started
        await tut.save_progress(UID, "whois", set())
        await tut.on_command_completed(it, SimpleNamespace(qualified_name="help", name="help"))  # not this step
        assert tut.get_progress(UID)["step"] == "whois"
        CACHE.tutorial_progress[UID]["user"]["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await tut.on_command_completed(it, SimpleNamespace(qualified_name="whois", name="whois"))  # stale
        assert tut.get_progress(UID)["step"] == "whois"
        await tut.save_progress(UID, "done", set())
        await tut.on_command_completed(it, SimpleNamespace(qualified_name="whois", name="whois"))  # finished
        it.followup.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subcommand_by_qualified_name(self):
        await tut.save_progress(UID, "cwl_preferences", set())
        await tut.on_command_completed(_click(), SimpleNamespace(qualified_name="cwl preferences", name="preferences"))
        assert tut.get_progress(UID)["step"] == "done"


class TestTutorialView:
    @pytest.mark.asyncio
    async def test_new_user_starts_at_welcome_and_returning_user_resumes(self):
        from clashcontrol import ui_tutorial as ui
        it = _click()
        await ui.send_tutorial(it)
        assert tut.get_progress(UID)["step"] == "welcome"
        await tut.save_progress(UID, "whois", {"welcome"})
        it = _click()
        await ui.send_tutorial(it)
        embed = it.response.send_message.await_args.kwargs["embed"]
        assert "6" in embed.description.splitlines()[0]         # "Welcome back — you're at step 6."

    @pytest.mark.asyncio
    async def test_next_marks_done_only_when_the_step_really_is(self):
        from clashcontrol import ui_tutorial as ui
        view = ui.TutorialView(UID, GID, "welcome")
        await view._on_next(_click())
        assert view.step_id == "help" and "welcome" in tut.get_progress(UID)["done"]
        view.step_id = "link_account"
        await view._on_next(_click())                                  # no account linked
        assert "link_account" not in tut.get_progress(UID)["done"]
        CACHE.user_accounts[UID] = {"players": [{"player_tag": "#1"}]}
        view.step_id = "link_account"
        await view._on_next(_click())
        assert "link_account" in tut.get_progress(UID)["done"]
        view.step_id = "whois"
        await view._on_next(_click())                                  # command step: only via the command
        assert "whois" not in tut.get_progress(UID)["done"]
        view.stop()

    @pytest.mark.asyncio
    async def test_back_and_start_over(self):
        from clashcontrol import ui_tutorial as ui
        await tut.save_progress(UID, "whois", {"welcome", "help"})
        view = ui.TutorialView(UID, GID, "whois")
        await view._on_back(_click())
        assert view.step_id == "war_reminders"
        await view._on_restart(_click())
        assert view.step_id == "welcome" and tut.get_progress(UID)["done"] == set()
        assert view.children[0].disabled                               # no Back on step 1
        view.stop()

    @pytest.mark.asyncio
    async def test_flow_buttons_open_the_shared_flows(self, monkeypatch):
        from clashcontrol import tutorial_flows, ui_tutorial as ui
        opened = AsyncMock()
        monkeypatch.setattr(tutorial_flows, "open_link_account", opened)
        view = ui.TutorialView(UID, GID, "link_account")
        assert [c.custom_id for c in view.children if c.custom_id.startswith("tutorial_flow_")] == ["tutorial_flow_0"]
        it = _click()
        await view._make_flow_callback(0)(it)
        opened.assert_awaited_once_with(it, GID)
        view.stop()

    @pytest.mark.asyncio
    async def test_flow_in_a_dm_without_a_known_server_asks_for_registration(self, monkeypatch):
        from clashcontrol import QBdiscocmdshelper, tutorial_flows, ui_tutorial as ui
        monkeypatch.setattr(QBdiscocmdshelper, "get_dm_registration_guild_ids", lambda client, uid: [1, 2])
        opened = AsyncMock()
        monkeypatch.setattr(tutorial_flows, "open_link_account", opened)
        view = ui.TutorialView(UID, None, "link_account")
        it = _click(guild=False)
        await view._make_flow_callback(0)(it)
        opened.assert_not_awaited()
        assert it.response.send_message.await_args.kwargs["ephemeral"] is True
        CACHE.pending_registration_dm_guild[UID] = 5                   # picked via /registration
        await view._make_flow_callback(0)(_click(guild=False))
        assert opened.await_args.args[1] == 5
        view.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang", LANGS)
    async def test_every_step_renders_in_every_language(self, lang):
        from clashcontrol import ui_tutorial as ui
        CACHE.server_config[str(GID)] = {"language": lang}
        for sid in tut.STEP_IDS:
            for welcome_back in (False, True):
                e = ui.build_tutorial_embed(sid, UID, GID, welcome_back)
                texts = [e.title or "", e.description or "", e.author.name or ""] + [f.name for f in e.fields] + [f.value for f in e.fields]
                assert not any("tutorial." in x or "{" in x for x in texts), (sid, texts)
                assert all(len(f.value) <= 1024 for f in e.fields)
            view = ui.TutorialView(UID, GID, sid)
            assert not any("tutorial." in (getattr(c, "label", "") or "") for c in view.children)
            view.stop()
        it = _click()
        it.guild = SimpleNamespace(id=GID)
        assert "tutorial." not in ui.step_done_text(tut.STEPS_BY_ID["help"], it)


class TestSetupHint:
    @pytest.mark.asyncio
    async def test_guided_setup_suggests_the_tutorial_until_it_is_finished(self):
        from test_config_audit import ADMIN, _guild  # tests/unit is on sys.path (no packages)
        from clashcontrol import config_audit as ca, ui_config_audit as ui
        CACHE.guild_setup_dismissed = {}
        guild = _guild(ADMIN)
        guild.id = GID
        view = ui.SetupView(guild, UID, ca.run_audit(guild))
        it = _click()
        await view._on_start_guided(it)
        fields = [f.value for f in view.current_embed().fields]
        assert any("/tutorial" in v for v in fields)
        await tut.save_progress(UID, "done", set())
        await view._on_start_guided(_click())
        assert not any("/tutorial" in f.value for f in view.current_embed().fields)
        view.stop()



class TestOneMessageFlow:
    """qaplop 2026-10-11: /tutorial left every page and "continue" prompt behind."""

    @pytest.mark.asyncio
    async def test_next_message_removes_the_previous_one(self):
        from clashcontrol import ui_tutorial
        first, second = AsyncMock(), AsyncMock()
        await ui_tutorial.replace_open_message("u9", first)
        await ui_tutorial.replace_open_message("u9", second)
        first.assert_awaited_once()
        second.assert_not_awaited()
        assert ui_tutorial._OPEN_MESSAGE["u9"] is second
        await ui_tutorial.replace_open_message("u9", None)          # nothing new: just remove
        second.assert_awaited_once()
        assert "u9" not in ui_tutorial._OPEN_MESSAGE

    @pytest.mark.asyncio
    async def test_a_failed_delete_never_breaks_the_flow(self):
        from clashcontrol import ui_tutorial
        broken = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=401), "token expired"))
        await ui_tutorial.replace_open_message("u8", broken)
        await ui_tutorial.replace_open_message("u8", AsyncMock())    # no raise

    @pytest.mark.asyncio
    async def test_continue_turns_its_prompt_into_the_page(self, monkeypatch):
        from clashcontrol import ui_tutorial
        monkeypatch.setattr(ui_tutorial, "get_progress", lambda uid: {"step": "link_account", "done": set()})
        # The open message is the "continue" prompt itself (on_command_completed registered it):
        # it becomes the page and must NOT be deleted (qaplop 2026-10-11, Build 170 deleted it).
        prompt = AsyncMock()
        ui_tutorial._OPEN_MESSAGE["77"] = prompt
        it = MagicMock()
        it.user.id = 77
        it.guild = None
        it.response.edit_message = AsyncMock()
        it.response.send_message = AsyncMock()
        it.delete_original_response = AsyncMock()
        await ui_tutorial.send_tutorial(it, in_place=True)
        it.response.edit_message.assert_awaited_once()
        it.response.send_message.assert_not_awaited()
        prompt.assert_not_awaited()
        it.delete_original_response.assert_not_awaited()
        assert ui_tutorial._OPEN_MESSAGE["77"] is it.delete_original_response
        ui_tutorial._OPEN_MESSAGE.pop("77", None)


class TestFlowCompletion:
    """qaplop 2026-10-11: linking an account in step 3 didn't move the tutorial on — the link
    flow is buttons, not an app command."""

    @pytest.mark.asyncio
    async def test_linking_completes_the_link_step(self, monkeypatch):
        from clashcontrol import ui_tutorial
        now = datetime.now(timezone.utc).isoformat()
        monkeypatch.setattr(tut, "get_progress", lambda uid, track=tut.TRACK_USER: {
            "step": "link_account", "done": {"welcome", "help"}, "updated_at": now})
        saved = []

        async def _save(uid, step_id, done=None, track=tut.TRACK_USER):
            saved.append((step_id, set(done or ())))
        monkeypatch.setattr(tut, "save_progress", _save)
        step = tut.STEPS_BY_ID["link_account"]
        monkeypatch.setattr(tut, "STEPS_BY_ID", {**tut.STEPS_BY_ID, "link_account": tut.TutorialStep(
            step.id, step.emoji, flows=step.flows, done_check=lambda uid: True)})
        it = MagicMock()
        it.user.id = 31
        it.guild = None
        message = MagicMock()
        message.delete = AsyncMock()
        it.followup.send = AsyncMock(return_value=message)
        page = AsyncMock()
        ui_tutorial._OPEN_MESSAGE["31"] = page
        await tut.on_flow_completed(it)
        assert saved == [("my_accounts", {"welcome", "help", "link_account"})]
        assert isinstance(it.followup.send.await_args.kwargs["view"], ui_tutorial.ContinueTutorialView)
        page.assert_awaited_once()                       # the step-3 page makes room for the prompt
        ui_tutorial._OPEN_MESSAGE.pop("31", None)

    @pytest.mark.asyncio
    async def test_nothing_happens_while_the_task_is_not_done(self, monkeypatch):
        now = datetime.now(timezone.utc).isoformat()
        monkeypatch.setattr(tut, "get_progress", lambda uid, track=tut.TRACK_USER: {
            "step": "link_account", "done": set(), "updated_at": now})
        step = tut.STEPS_BY_ID["link_account"]
        monkeypatch.setattr(tut, "STEPS_BY_ID", {**tut.STEPS_BY_ID, "link_account": tut.TutorialStep(
            step.id, step.emoji, flows=step.flows, done_check=lambda uid: False)})
        it = MagicMock()
        it.user.id = 32
        it.followup.send = AsyncMock()
        await tut.on_flow_completed(it)
        it.followup.send.assert_not_awaited()

