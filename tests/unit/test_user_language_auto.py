"""The "auto" (unlocked) user language follows the Discord locale for every bot language.

Live PROD finding 2026-09-25: update_user_metadata() mapped every locale except German to "en",
so a Spanish Discord user was stored — and then messaged — in English."""
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from qapbot import i18n
from qapbot.i18n import language_from_discord_locale


@pytest.fixture(autouse=True)
def _all_bot_languages(monkeypatch):
    """Other tests can leave a reduced translation catalog behind; pin the real language set."""
    monkeypatch.setattr(i18n, "get_available_languages", lambda: ["de", "en", "es", "la", "zh"])


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("de", "de"), ("es-ES", "es"), ("es-419", "es"), ("zh-CN", "zh"), ("zh-TW", "zh"),
     ("en-US", "en"), ("en-GB", "en"), ("fr", None), ("", None), (None, None)],
)
def test_language_from_discord_locale(locale, expected):
    assert language_from_discord_locale(locale) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("locale", "expected"), [("es-ES", "es"), ("zh-CN", "zh"), ("de", "de"), ("fr", "en")])
async def test_update_user_metadata_auto_language_uses_every_bot_language(monkeypatch, locale, expected):
    from qapbot.cache_manager import CACHE

    monkeypatch.setattr(CACHE, "users_loaded", True)
    monkeypatch.setattr(CACHE, "user_accounts", {"42": {"display_name": "x", "user_language": "en", "players": []}})
    monkeypatch.setattr(CACHE, "persist_user", AsyncMock())
    interaction = SimpleNamespace(locale=locale, user=SimpleNamespace(display_name="x"))

    await CACHE.update_user_metadata("42", interaction=cast(Any, interaction))

    assert CACHE.user_accounts["42"]["user_language"] == expected


@pytest.mark.asyncio
async def test_update_user_metadata_keeps_a_locked_language(monkeypatch):
    from qapbot.cache_manager import CACHE

    monkeypatch.setattr(CACHE, "users_loaded", True)
    monkeypatch.setattr(
        CACHE, "user_accounts",
        {"42": {"display_name": "x", "user_language": "la", "user_language_locked": True, "players": []}},
    )
    monkeypatch.setattr(CACHE, "persist_user", AsyncMock())
    interaction = SimpleNamespace(locale="es-ES", user=SimpleNamespace(display_name="x"))

    await CACHE.update_user_metadata("42", interaction=cast(Any, interaction))

    assert CACHE.user_accounts["42"]["user_language"] == "la"
