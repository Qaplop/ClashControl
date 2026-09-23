"""Double-click guards on confirm/apply buttons (Cardinal Rule 7, 2026-09-23).

qapbot.ui_common's claim_action / release_action / lock_buttons / unlock_buttons are the shared
mechanism every side-effecting confirm step uses: the first click claims the view before any
await, later clicks are acknowledged silently and do nothing, and the buttons are shown greyed out
as the click's own response. CWL-specific dialogs are covered in test_ui_cwl_roster.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _click() -> AsyncMock:
    """A fresh mocked button click (its own response slot, like a real second interaction)."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = AsyncMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 987654321
    interaction.guild_id = 987654321
    interaction.user = MagicMock()
    interaction.user.id = 123456789
    return interaction


def _two_button_view() -> discord.ui.View:
    view = discord.ui.View(timeout=60)
    view.add_item(discord.ui.Button(label="Yes", custom_id="yes"))
    view.add_item(discord.ui.Button(label="No", custom_id="no", disabled=True))
    return view


@pytest.mark.discord
@pytest.mark.asyncio
async def test_claim_action_rejects_second_click_silently():
    from qapbot.ui_common import action_in_flight, claim_action

    view = _two_button_view()
    first, second = _click(), _click()

    assert await claim_action(view, first) is True
    assert action_in_flight(view) is True
    assert await claim_action(view, second) is False

    first.response.defer.assert_not_awaited()  # the first click keeps its response slot
    second.response.defer.assert_awaited_once()  # ack'd, so no "interaction failed" toast


@pytest.mark.discord
@pytest.mark.asyncio
async def test_release_action_allows_a_retry():
    from qapbot.ui_common import claim_action, release_action

    view = _two_button_view()
    assert await claim_action(view, _click()) is True
    release_action(view)
    assert await claim_action(view, _click()) is True


@pytest.mark.discord
@pytest.mark.asyncio
async def test_lock_buttons_disables_everything_as_the_first_response():
    from qapbot.ui_common import lock_buttons

    view = _two_button_view()
    click = _click()

    await lock_buttons(view, click, content="working…")

    assert all(getattr(c, "disabled") for c in view.children)
    click.response.edit_message.assert_awaited_once_with(view=view, content="working…")
    click.edit_original_response.assert_not_awaited()


@pytest.mark.discord
@pytest.mark.asyncio
async def test_lock_buttons_edits_original_response_when_already_answered():
    from qapbot.ui_common import lock_buttons

    view = _two_button_view()
    click = _click()
    click.response.is_done = MagicMock(return_value=True)

    await lock_buttons(view, click)

    click.response.edit_message.assert_not_awaited()
    click.edit_original_response.assert_awaited_once_with(view=view)


@pytest.mark.discord
@pytest.mark.asyncio
async def test_unlock_buttons_restores_previous_disabled_state_and_releases():
    from qapbot.ui_common import action_in_flight, claim_action, lock_buttons, unlock_buttons

    view = _two_button_view()  # "No" starts disabled on purpose
    click = _click()
    await claim_action(view, click)
    await lock_buttons(view, click)

    await unlock_buttons(view, click)

    yes, no = view.children
    assert getattr(yes, "disabled") is False
    assert getattr(no, "disabled") is True  # stays as it was before the lock
    assert action_in_flight(view) is False
    click.edit_original_response.assert_awaited_once_with(view=view)


# ---------------------------------------------------------------------------
# One representative per module: the second click must not run the action again
# ---------------------------------------------------------------------------

@pytest.mark.discord
@pytest.mark.asyncio
async def test_import_data_confirm_applies_once_on_double_click(monkeypatch):
    import qapbot.import_clashperk_userlist as importer
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_clan_management import ImportDataConfirmView

    apply_mock = MagicMock(return_value=(None, 1, 0, 0, []))
    monkeypatch.setattr(importer, "apply_import_changes", apply_mock)
    monkeypatch.setattr(helper, "send_and_track", AsyncMock())

    view = ImportDataConfirmView(
        original_interaction=_click(), user_accounts={}, results={}, clan_name="Alpha",
        clan_tag="#CLAN1", to_add_count=1, to_upgrade_count=0, to_skip_count=0,
    )
    first, second = _click(), _click()

    await view._confirm_callback(first)
    await view._confirm_callback(second)

    apply_mock.assert_called_once()
    first.response.edit_message.assert_awaited_once()  # buttons greyed out immediately
    assert all(getattr(c, "disabled") for c in view.children)


@pytest.mark.discord
@pytest.mark.asyncio
async def test_import_data_cancel_is_ignored_while_confirm_runs(monkeypatch):
    from qapbot.ui_clan_management import ImportDataConfirmView
    from qapbot.ui_common import claim_action

    view = ImportDataConfirmView(
        original_interaction=_click(), user_accounts={}, results={}, clan_name="Alpha",
        clan_tag="#CLAN1", to_add_count=1, to_upgrade_count=0, to_skip_count=0,
    )
    await claim_action(view, _click())  # the confirm is running

    cancel = _click()
    await view._cancel_callback(cancel)

    cancel.response.send_message.assert_not_awaited()  # no "import cancelled" over a running import


@pytest.mark.discord
@pytest.mark.asyncio
async def test_unlink_all_confirm_unlinks_once_on_double_click(monkeypatch):
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_registration import UnlinkAllConfirmView

    unlink_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(helper, "unlink_all_players", unlink_mock)
    view = UnlinkAllConfirmView(user_id="123456789", guild_id=987654321, account_count=2, parent_view=MagicMock())
    first, second = _click(), _click()
    first.guild = None
    second.guild = None

    await view._on_confirm(first)
    await view._on_confirm(second)

    unlink_mock.assert_awaited_once()
    second.edit_original_response.assert_not_awaited()


@pytest.mark.discord
@pytest.mark.asyncio
async def test_notification_settings_apply_reenables_buttons_afterwards(monkeypatch):
    """The settings dialog stays open after Apply — its buttons come back once the run ends,
    even on the early-return path (user scope with nobody selected)."""
    from qapbot.ui_common import action_in_flight
    from qapbot.ui_notifications import NotificationSettingsView

    view = NotificationSettingsView.__new__(NotificationSettingsView)
    discord.ui.View.__init__(view, timeout=60)
    view.add_item(discord.ui.Button(label="Apply", custom_id="apply"))
    view.scope = "user"
    view.selected_user_id = None
    click = _click()

    await view._on_apply(click)

    click.response.edit_message.assert_awaited_once()  # locked as the first response
    assert all(not getattr(c, "disabled") for c in view.children)  # ...and unlocked again
    assert action_in_flight(view) is False


# ---------------------------------------------------------------------------
# Structural: every guarded confirm/apply handler claims its view before its first await
# ---------------------------------------------------------------------------

_GUARDED_HANDLERS = {
    "qapbot/ui_cwl_roster.py": {
        "CwlDeleteSeasonConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlStartEnrollmentConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlNotifyNewMembersConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlSendRosterUpdatesConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlAnnounceRostersConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlRemindPendingConfirmView": ["_on_confirm", "_on_cancel"],
        "CwlCarryOverPromptView": ["_finish"],
    },
    "qapbot/ui_clan_management.py": {
        "RoleDeleteConfirmationView": ["_on_confirm", "_on_cancel"],
        "ConfirmDeleteClanRolesView": ["_on_delete", "_on_keep"],
        "CwlLineupRemovalConfirmView": ["_on_confirm", "_on_cancel"],
        "ClanManagementUnlinkPlayerConfirmView": ["_on_confirm", "_on_cancel"],
        "ClanManagementAdminOverrideView": ["_confirm_callback", "_cancel_callback"],
        "AdminOverrideConfirmView": ["_confirm_callback", "_cancel_callback"],
        "ImportDataConfirmView": ["_confirm_callback", "_cancel_callback"],
        "SwitchViewContinueView": ["_continue_callback", "_cancel_callback"],
        "ChannelConfigurationView": ["_on_apply"],
        "CustodianConfigurationView": ["_on_apply"],
        "MemberClansConfigurationView": ["_on_apply"],
        "RoleConfigurationView": ["_on_apply"],
        "EditFamilyView": ["_on_save"],
        "WelcomeMessageConfigView": ["_on_save", "_on_cancel"],
    },
    "qapbot/ui_registration.py": {
        "UnlinkConfirmView": ["_on_confirm", "_on_cancel"],
        "UnlinkAllConfirmView": ["_on_confirm", "_on_cancel"],
    },
    "qapbot/ui_tracker.py": {"BotSetupView": ["_on_save"]},
    "qapbot/ui_notifications.py": {
        "NotificationSettingsView": ["_on_apply"],
        "WarNotificationPromptView": ["activate_button", "skip_button"],
    },
}


def test_confirm_handlers_claim_before_first_await():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    problems = []
    for rel_path, classes in _GUARDED_HANDLERS.items():
        tree = ast.parse((root / rel_path).read_text(encoding="utf-8"))
        found = {}
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name in classes):
            for fn in cls.body:
                if isinstance(fn, ast.AsyncFunctionDef) and fn.name in classes[cls.name]:
                    found[(cls.name, fn.name)] = fn
        for cls_name, methods in classes.items():
            for method in methods:
                fn = found.get((cls_name, method))
                if fn is None:
                    problems.append(f"{rel_path}: {cls_name}.{method} not found")
                    continue
                first_await = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Await))
                claims = [
                    n.lineno for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "claim_action"
                ]
                if not claims or min(claims) != first_await:
                    problems.append(f"{rel_path}: {cls_name}.{method} awaits before claim_action()")
    assert not problems, "\n".join(problems)
