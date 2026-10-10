"""Simple Roles notices name the exact buttons and modes (2026-10-10).

The old texts sent every missing setting to "Basic Server Configuration" (the roles are set in
Manage Server Roles → Configure Roles), named a "Manage Member Clans & Families" button that
doesn't exist, and inserted an untranslated English reason ("da newbie role gelöscht wurde").
The labels now come from the real button/mode keys via simple_roles_labels().
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
from clashcontrol import i18n  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402
from clashcontrol.ui_clan_management import simple_roles_labels  # noqa: E402

LANGS = ["en", "de", "es", "la", "zh"]
GID = 4711


@pytest.fixture(autouse=True)
def _guild(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {str(GID): {}})
    # A fresh manager from the real translation files: other tests swap the shared one's
    # translations for fakes, and in a full run this file must not depend on test order.
    fresh = i18n.TranslationManager()
    fresh.load_translations()
    assert "en" in fresh.translations
    monkeypatch.setattr(i18n, "_translation_manager", fresh)


def _render(lang: str, key: str, **kw) -> str:
    CACHE.server_config[str(GID)]["language"] = lang
    return i18n.t(key, guild_id=GID, **kw)


@pytest.mark.parametrize("lang", LANGS)
def test_notices_render_with_real_labels(lang):
    CACHE.server_config[str(GID)]["language"] = lang
    labels = simple_roles_labels(GID)
    reason = _render(lang, "ui_components.messages.role_system_reason_newbie_removed")
    texts = {
        "auto": _render(lang, "ui_components.messages.role_system_auto_disabled", reason=reason, **labels),
        "clans": _render(lang, "ui_components.errors.member_role_system_disabled", **labels),
        "incomplete": _render(lang, "ui_components.errors.role_system_config_incomplete",
                              missing_items="X", **labels),
    }
    for name, text in texts.items():
        assert not re.search(r"\{[a-z_]+\}", text), (lang, name, text)
        assert labels["enable_button"] in text, (lang, name)
    assert reason in texts["auto"] and labels["configure_roles"] in texts["auto"]
    assert labels["member_clans"] in texts["clans"] and labels["mode_config"] in texts["clans"]
    assert reason != "role_system_reason_newbie_removed"  # a real translation, not the key


def test_english_wording_is_specific():
    labels = simple_roles_labels(GID)
    assert labels["enable_button"] == "🟢 Enable Simple Roles"
    auto = _render("en", "ui_components.messages.role_system_auto_disabled",
                   reason=_render("en", "ui_components.messages.role_system_reason_both_removed"), **labels)
    assert auto.startswith("⚠️ **Simple Roles switched off automatically**")
    assert "You removed the newbie role and the member role." in auto
    assert "🎭 Manage Server Roles → **⚙️ Configure Roles**" in auto
    header = _render("en", "permissions.warning_header")
    assert "missing permissions" not in header  # also used for role-position issues
