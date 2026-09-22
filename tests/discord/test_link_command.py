"""Tests for the /link command group (/link clan, /link player): resolve a clan or player by tag
or name substring and post ``**Name**: <CoC in-game deep link>`` publicly. Errors and the
player disambiguation dropdown go out ephemerally after deleting the public placeholder.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import QBdiscordcmds  # noqa: E402


CLAN_URL = "https://link.clashofclans.com/en?action=OpenClanProfile&tag=%23L2J0C0PY"
PLAYER_URL = "https://link.clashofclans.com/en?action=OpenPlayerProfile&tag=%23P2Y0V8LQ"


def _public_text(interaction) -> str:
    """The content of the one public followup (no ephemeral flag)."""
    call = interaction.followup.send.await_args
    assert not call.kwargs.get("ephemeral"), "result must be posted publicly"
    return call.args[0]


def test_link_commands_are_dm_invokable():
    assert QBdiscordcmds.link_clan.guild_only is False
    assert QBdiscordcmds.link_player.guild_only is False


def test_link_group_registered_with_both_subcommands():
    names = {c.name for c in QBdiscordcmds.link_group.commands}
    assert names == {"clan", "player"}


class TestLinkClan:
    @pytest.mark.asyncio
    async def test_tracked_clan_by_name_posts_link(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(QBdiscordcmds.CACHE, "clan_name_cache", {"#L2J0C0PY": {"name": "The QCrew"}})

        await QBdiscordcmds.link_clan.callback(mock_interaction, clan="qcrew")  # type: ignore[arg-type]

        mock_interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=False)
        assert _public_text(mock_interaction) == f"**The QCrew**: <{CLAN_URL}>"

    @pytest.mark.asyncio
    async def test_untracked_tag_is_looked_up_not_tracked(self, mock_interaction, monkeypatch):
        """An untracked tag is fetched for its name only — never added to tracking."""
        monkeypatch.setattr(QBdiscordcmds.CACHE, "clan_name_cache", {})
        clan_obj = MagicMock()
        clan_obj.name = "Some_Clan"
        get_clan = AsyncMock(return_value=clan_obj)
        monkeypatch.setattr(QBdiscordcmds.CACHE.coc_clan_cache, "get_clan", get_clan)
        track = AsyncMock()
        monkeypatch.setattr(QBdiscordcmds, "validate_and_add_clan_to_cache", track)

        await QBdiscordcmds.link_clan.callback(mock_interaction, clan="#l2joc0py")  # type: ignore[arg-type]

        get_clan.assert_awaited_once_with("#L2J0C0PY")
        track.assert_not_called()
        # Markdown in the name is escaped so it renders literally.
        assert _public_text(mock_interaction) == f"**Some\\_Clan**: <{CLAN_URL}>"

    @pytest.mark.asyncio
    async def test_unknown_tag_sends_ephemeral_error(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(QBdiscordcmds.CACHE, "clan_name_cache", {})
        monkeypatch.setattr(QBdiscordcmds.CACHE.coc_clan_cache, "get_clan", AsyncMock(side_effect=Exception("404")))

        await QBdiscordcmds.link_clan.callback(mock_interaction, clan="#L2J0C0PY")  # type: ignore[arg-type]

        mock_interaction.delete_original_response.assert_awaited_once()
        call = mock_interaction.followup.send.await_args
        assert call.kwargs["ephemeral"] is True
        assert "#L2J0C0PY" in call.args[0]

    @pytest.mark.asyncio
    async def test_garbage_input_sends_invalid_error(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(QBdiscordcmds.CACHE, "clan_name_cache", {})

        await QBdiscordcmds.link_clan.callback(mock_interaction, clan="no such clan!!")  # type: ignore[arg-type]

        assert mock_interaction.followup.send.await_args.kwargs["ephemeral"] is True


class TestLinkPlayer:
    @pytest.mark.asyncio
    async def test_explicit_tag_posts_link_with_api_name(self, mock_interaction, monkeypatch):
        player_obj = MagicMock()
        player_obj.name = "Qaplop"
        get_player = AsyncMock(return_value=player_obj)
        monkeypatch.setattr(QBdiscordcmds.CACHE, "get_player", get_player)

        await QBdiscordcmds.link_player.callback(mock_interaction, player="#p2yov8lq")  # type: ignore[arg-type]

        get_player.assert_awaited_once_with("#P2Y0V8LQ")
        assert _public_text(mock_interaction) == f"**Qaplop**: <{PLAYER_URL}>"

    @pytest.mark.asyncio
    async def test_explicit_tag_not_found_sends_ephemeral_error(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(QBdiscordcmds.CACHE, "get_player", AsyncMock(return_value=None))

        await QBdiscordcmds.link_player.callback(mock_interaction, player="#P2Y0V8LQ")  # type: ignore[arg-type]

        mock_interaction.delete_original_response.assert_awaited_once()
        assert mock_interaction.followup.send.await_args.kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_single_name_match_posts_link(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(
            QBdiscordcmds, "_search_player_name_matches",
            AsyncMock(return_value=([{"player_tag": "#P2Y0V8LQ", "player_name": "Qaplop"}], 1)),
        )

        await QBdiscordcmds.link_player.callback(mock_interaction, player="qap")  # type: ignore[arg-type]

        assert _public_text(mock_interaction) == f"**Qaplop**: <{PLAYER_URL}>"

    @pytest.mark.asyncio
    async def test_no_name_match_sends_ephemeral_error(self, mock_interaction, monkeypatch):
        monkeypatch.setattr(QBdiscordcmds, "_search_player_name_matches", AsyncMock(return_value=([], 0)))

        await QBdiscordcmds.link_player.callback(mock_interaction, player="nobody")  # type: ignore[arg-type]

        call = mock_interaction.followup.send.await_args
        assert call.kwargs["ephemeral"] is True
        assert "nobody" in call.args[0]

    @pytest.mark.asyncio
    async def test_multiple_matches_show_ephemeral_dropdown_then_select_posts_publicly(
        self, mock_interaction, monkeypatch
    ):
        matches = [
            {"player_tag": "#P2Y0V8LQ", "player_name": "Qaplop"},
            {"player_tag": "#9ABC", "player_name": "Qaplop2"},
        ]
        monkeypatch.setattr(QBdiscordcmds, "_search_player_name_matches", AsyncMock(return_value=(matches, 2)))

        await QBdiscordcmds.link_player.callback(mock_interaction, player="qaplop")  # type: ignore[arg-type]

        call = mock_interaction.followup.send.await_args
        assert call.kwargs["ephemeral"] is True
        view = call.kwargs["view"]
        assert [o.value for o in view.select.options] == ["#P2Y0V8LQ", "#9ABC"]

        # Picking an option posts the link publicly and removes the dropdown.
        dropdown_msg = AsyncMock()
        view.message = dropdown_msg
        select_interaction = AsyncMock()
        select_interaction.user.id = 1
        select_interaction.guild_id = None
        await view.callback_fn(select_interaction, "#P2Y0V8LQ", **view.callback_kwargs)

        dropdown_msg.delete.assert_awaited_once()
        select_interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=False)
        assert _public_text(select_interaction) == f"**Qaplop**: <{PLAYER_URL}>"


class TestSharedPlayerSearch:
    @pytest.mark.asyncio
    async def test_guild_matches_first_global_deduped_and_capped(self, mock_interaction, monkeypatch):
        """_search_player_name_matches (shared by /whois and /link player): guild matches first,
        global matches minus duplicates after, capped at 25 with the true total returned."""
        monkeypatch.setattr(
            QBdiscordcmds, "_build_guild_player_name_matches",
            lambda guild_id, needle: [{"player_tag": "#G1", "player_name": "Guildie"}],
        )
        global_rows = [{"player_tag": "#G1", "player_name": "Guildie"}] + [
            {"player_tag": f"#X{i}", "player_name": f"Other{i}"} for i in range(30)
        ]
        db = MagicMock()
        db.search_player_names_full_sync = MagicMock(return_value=global_rows)
        monkeypatch.setattr(QBdiscordcmds.CACHE, "db_manager", db)

        matches, total = await QBdiscordcmds._search_player_name_matches(1, "abc")

        assert total == 31
        assert len(matches) == 25
        assert matches[0]["player_tag"] == "#G1"
        assert [m["player_tag"] for m in matches].count("#G1") == 1
