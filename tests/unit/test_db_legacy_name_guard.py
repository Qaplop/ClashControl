"""Startup guard for the QapBot -> ClashControl DB file rename (2026-09-26): a folder that still
holds qapbot*.db but no clashcontrol*.db must stop the bot, not start it on an empty new DB."""

from clashcontrol.db_manager import find_unrenamed_legacy_db


def _paths(tmp_path):
    return str(tmp_path / "clashcontrol.db"), str(tmp_path / "clashcontrol_history.db")


def test_unrenamed_hot_db_is_reported(tmp_path):
    (tmp_path / "qapbot.db").write_bytes(b"x")
    assert find_unrenamed_legacy_db(*_paths(tmp_path)) == str(tmp_path / "qapbot.db")


def test_unrenamed_history_db_is_reported(tmp_path):
    (tmp_path / "clashcontrol.db").write_bytes(b"x")
    (tmp_path / "qapbot_history.db").write_bytes(b"x")
    assert find_unrenamed_legacy_db(*_paths(tmp_path)) == str(tmp_path / "qapbot_history.db")


def test_renamed_files_pass_even_if_an_old_copy_is_left_over(tmp_path):
    for name in ("clashcontrol.db", "clashcontrol_history.db", "qapbot.db", "qapbot_history.db"):
        (tmp_path / name).write_bytes(b"x")
    assert find_unrenamed_legacy_db(*_paths(tmp_path)) is None


def test_fresh_install_without_any_db_passes(tmp_path):
    assert find_unrenamed_legacy_db(*_paths(tmp_path)) is None


def test_custom_db_names_are_not_checked(tmp_path):
    (tmp_path / "qapbot.db").write_bytes(b"x")
    assert find_unrenamed_legacy_db(str(tmp_path / "other.db"), str(tmp_path / "other_history.db")) is None
