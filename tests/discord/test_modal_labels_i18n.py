# pyright: reportArgumentType=false
"""Modal field labels and titles follow the guild language (2026-09-23 backlog item).

discord.py 2.7 deprecates TextInput.label, so the 2026-09-22 i18n sweep only translated the
placeholders and left every modal's labels English. These modals now wrap each TextInput in a
discord.ui.Label (CODE_STRUCTURE.md § Modal Pattern) and translate the label text, the title and
the placeholder per instance.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import discord
import pytest

GUILD_ID = 987654321
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def german_guild(monkeypatch):
    from qapbot.cache_manager import CACHE

    monkeypatch.setattr(CACHE, "server_config", {str(GUILD_ID): {"language": "de"}})
    monkeypatch.setattr(CACHE, "user_accounts", {})  # no per-user override — guild language applies


def _guild_view(**extra: Any) -> SimpleNamespace:
    """A parent view the way the management views look: a discord.Guild on .guild, no guild_id."""
    return SimpleNamespace(guild=SimpleNamespace(id=GUILD_ID), **extra)


def _build_modals() -> dict[str, discord.ui.Modal]:
    from qapbot.ui_clan_management import (
        AddClanFamilyModal,
        AddClanModal,
        CreateFamilyModal,
        ManualPlayerTagModal,
        ManualUserIDModal,
        RenameFamilyModal,
    )
    from qapbot.ui_notifications import LinkBuddyModal
    from qapbot.ui_registration import (
        ApiTokenEntryModal,
        ApiTokenOwnershipModal,
        PlayerSubstringModal,
        VerifyAccountModal,
    )

    return {
        "VerifyAccountModal": VerifyAccountModal({"player_tag": "#P1", "player_name": "Alice"}, guild_id=GUILD_ID),
        "ApiTokenOwnershipModal": ApiTokenOwnershipModal("#P1", 1, "Bob", "2", False, guild_id=GUILD_ID),
        "ApiTokenEntryModal": ApiTokenEntryModal("#P1", "Alice", guild_id=GUILD_ID),
        "PlayerSubstringModal": PlayerSubstringModal([], user_id="1", guild_id=GUILD_ID),
        "ManualPlayerTagModal": ManualPlayerTagModal(link_view=_guild_view()),
        "ManualUserIDModal": ManualUserIDModal(link_view=_guild_view()),
        "AddClanFamilyModal": AddClanFamilyModal(_guild_view()),
        "CreateFamilyModal": CreateFamilyModal(SimpleNamespace(sent_message=SimpleNamespace(guild=SimpleNamespace(id=GUILD_ID)))),
        "RenameFamilyModal": RenameFamilyModal(_guild_view(), "Old Name"),
        "AddClanModal": AddClanModal(_guild_view()),
        "LinkBuddyModal": LinkBuddyModal(
            user_id="1", parent_view=MagicMock(), original_interaction=SimpleNamespace(guild_id=GUILD_ID)
        ),
    }


# modal -> (title, [label texts in order])
GERMAN = {
    "VerifyAccountModal": (None, ["CoC-API-Token"]),  # title names the player; checked separately
    "ApiTokenOwnershipModal": ("Kontobesitz nachweisen", ["CoC-API-Token"]),
    "ApiTokenEntryModal": ("API-Token eingeben", ["CoC-API-Token"]),
    "PlayerSubstringModal": ("Spielerdaten eingeben", ["Spielername oder Tag", "CoC-API-Token (optional)"]),
    "ManualPlayerTagModal": ("Spieler-Tag eingeben", ["Spieler-Tag"]),
    "ManualUserIDModal": ("Discord-Benutzer-ID eingeben", ["Discord-Benutzer-ID"]),
    "AddClanFamilyModal": ("Clan oder Familie hinzufügen", ["Clan-/Familien-Tag oder Name"]),
    "CreateFamilyModal": ("Clan-Familie erstellen", ["Familienname"]),
    "RenameFamilyModal": ("Clan-Familie umbenennen", ["Neuer Familienname"]),
    "AddClanModal": ("Clan zur Familie hinzufügen", ["Clan-Name (Teil) oder Tag (vollständig)"]),
    "LinkBuddyModal": ("Buddy-Konto verbinden", ["ℹ️  Buddy speichern", "Spielername oder Tag"]),
}


def _labels(modal: discord.ui.Modal) -> list[discord.ui.Label[Any]]:
    return [c for c in modal.children if isinstance(c, discord.ui.Label)]


@pytest.mark.discord
def test_every_modal_shows_title_labels_and_placeholders_in_the_guild_language(german_guild):
    en = json.load(open(ROOT / "qapbot/translations/en.json", encoding="utf-8"))
    english_placeholders = set(en["ui_components"]["modals"].values()) | {
        en["warnotifications"]["buddy_modal_placeholder"],
        en["playerregistration"]["modal_placeholder_player_name"],
    }

    for name, modal in _build_modals().items():
        title, label_texts = GERMAN[name]
        if title is not None:
            assert modal.title == title, name
        assert [label.text for label in _labels(modal)] == label_texts, name
        for label in _labels(modal):
            placeholder = cast(discord.ui.TextInput, label.component).placeholder
            assert placeholder not in english_placeholders, (name, placeholder)


@pytest.mark.discord
def test_modals_send_labels_on_the_label_component_not_the_deprecated_text_input_field(german_guild):
    for name, modal in _build_modals().items():
        components = modal.to_dict()["components"]
        assert components and all(c["type"] == 18 for c in components), name  # 18 = Label
        for component in components:
            assert component["component"]["type"] == 4, name  # 4 = TextInput
            assert component["component"].get("label") is None, name
            assert len(component["label"]) <= 45, name


@pytest.mark.discord
def test_translating_one_instance_never_leaks_into_the_class_or_the_next_modal(german_guild, monkeypatch):
    from qapbot.cache_manager import CACHE
    from qapbot.ui_registration import PlayerSubstringModal

    german = PlayerSubstringModal([], user_id="1", guild_id=GUILD_ID)
    monkeypatch.setattr(CACHE, "server_config", {})
    english = PlayerSubstringModal([], user_id="1", guild_id=GUILD_ID)

    assert german.substring.text == "Spielername oder Tag"
    assert english.substring.text == "Player Name or Tag"
    assert english.title == "Enter Player Data"
    assert PlayerSubstringModal.substring.text == "Player Name or Tag"


@pytest.mark.discord
def test_rename_family_keeps_the_current_name_prefilled(german_guild):
    from qapbot.ui_clan_management import RenameFamilyModal

    modal = RenameFamilyModal(_guild_view(), "The QCrew")
    assert cast(discord.ui.TextInput, modal.family_name.component).default == "The QCrew"


@pytest.mark.discord
def test_view_guild_id_reads_guild_when_the_view_has_no_guild_id():
    """The family/clan modals once read getattr(view, 'guild_id', None) off views that only have
    .guild, so they were English on every server."""
    from qapbot.ui_clan_management import _view_guild_id

    assert _view_guild_id(_guild_view()) == GUILD_ID
    assert _view_guild_id(SimpleNamespace(guild_id=5, guild=SimpleNamespace(id=6))) == 5
    assert _view_guild_id(SimpleNamespace()) is None


def test_modal_titles_and_labels_fit_discords_45_char_limit_in_every_language():
    """A Label.text or modal title over 45 characters 400s the whole modal (Pitfall 66)."""
    for path in sorted((ROOT / "qapbot/translations").glob("*.json")):
        data = json.load(open(path, encoding="utf-8"))
        texts = {k: v for k, v in data["ui_components"]["modals"].items() if k.startswith(("label_", "title_"))}
        texts["buddy_modal_title"] = data["warnotifications"]["buddy_modal_title"]
        texts["buddy_modal_label"] = data["warnotifications"]["buddy_modal_label"]
        texts["buddy_modal_info_label"] = data["warnotifications"]["buddy_modal_info_label"]
        for key, text in texts.items():
            assert len(text) <= 45, f"{path.name}: {key} is {len(text)} chars"


def test_no_modal_sets_the_deprecated_text_input_label():
    """discord.py 2.7 deprecates TextInput(label=...) — wrap the field in discord.ui.Label."""
    problems = []
    for path in sorted((ROOT / "qapbot").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))  # some modules carry a BOM
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "TextInput"
                and any(kw.arg == "label" for kw in node.keywords)
            ):
                problems.append(f"{path.name}:{node.lineno}")
    assert not problems, "TextInput(label=...) found: " + ", ".join(problems)
