"""Every literal t('...') key must exist in en.json.

2026-09-22: two Hub count lines shipped with keys nobody had added (tracker #0114's
cwl.management.signup_status_passive / _auto_passive), so the season overview printed the raw key
path at the reader. The en/de/es/zh/la parity check cannot catch that — the key was missing from
ALL of them equally, which is perfect parity.

Dynamic keys (f-strings such as f'cwl.phase.step_{key}') can't be resolved statically and are not
covered here; the modules that build them own their own tests.
"""
from __future__ import annotations

import glob
import json
import re

# Deliberately EMPTY. It held the 17 keys this test first uncovered (2026-09-22); 13 were added
# in all five languages, and the other four belonged to two _translate_inputs() helpers that
# nothing ever called — those were deleted and their modals now translate inline through the
# ui_components.modals.* keys every other modal already uses. Keep it empty: an entry here is a
# string the user sees as a raw key path, so it is a bug being tolerated, never an exception.
KNOWN_MISSING_KEYS: set[str] = set()

_T_CALL = re.compile(r"""\bt\(\s*(['"])([A-Za-z0-9_.]+)\1""")


def _static_keys() -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for path in glob.glob("clashcontrol/**/*.py", recursive=True) + glob.glob("*.py"):
        if "/scripts/" in path.replace("\\", "/"):
            continue  # translation tooling talks ABOUT keys, it doesn't render them
        for match in _T_CALL.finditer(open(path, encoding="utf-8").read()):
            keys.setdefault(match.group(2), set()).add(path)
    return keys


def _exists(translations: dict, key: str) -> bool:
    node = translations
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def test_every_literal_translation_key_exists_in_english():
    en = json.load(open("clashcontrol/translations/en.json", encoding="utf-8"))
    keys = _static_keys()
    assert len(keys) > 500, "the t() scan found suspiciously few keys — did the call shape change?"

    missing = {
        key: sorted(files) for key, files in keys.items()
        if not _exists(en, key) and key not in KNOWN_MISSING_KEYS
    }
    assert not missing, (
        "translation keys used in code but absent from en.json (they render as the raw key path "
        f"to the user): {missing}"
    )


def test_known_missing_list_has_no_stale_entries():
    """Once one of those pre-existing gaps is fixed, it has to leave the list — otherwise the list
    slowly turns into a place where real bugs hide."""
    en = json.load(open("clashcontrol/translations/en.json", encoding="utf-8"))
    fixed = sorted(key for key in KNOWN_MISSING_KEYS if _exists(en, key))
    assert not fixed, f"these keys now exist — remove them from KNOWN_MISSING_KEYS: {fixed}"


def test_the_cwl_signup_count_labels_resolve():
    """The concrete 2026-09-22 regression: the season overview's own count lines."""
    en = json.load(open("clashcontrol/translations/en.json", encoding="utf-8"))
    for status in ("pending", "confirmed", "auto_confirmed", "declined", "passive", "auto_passive"):
        assert _exists(en, f"cwl.management.signup_status_{status}"), status
