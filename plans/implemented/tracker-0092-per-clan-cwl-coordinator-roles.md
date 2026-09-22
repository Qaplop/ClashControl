# Tracker #0092 — per-clan CWL Coordinator roles

Builds on #0086 (plans/implemented/tracker-0085-0086-0087-cwl-coordinator-role.md), which links ONE
existing guild role to "is a CWL coordinator of any clan".

## Spec (project owner, 2026-09-22)

- A per-guild switch: **one coordinator role** for all clans (today's behaviour, default) or
  **one role per clan**.
- In per-clan mode the role is **selectable per clan** (linked existing roles, same as #0086 —
  the bot never creates or deletes them).
- Roles are auto-assigned / auto-removed when someone is added / removed as a coordinator.
- One user can be coordinator of several clans and then holds every one of those clans' roles.

The ticket's use case: `#cwl-staymad` is only visible to the "CWL Koordinator StayMad" role, so a
coordinator only sees the channel of the clan they coordinate.

## Storage

- `guild_config.cwl_coordinator_role_mode TEXT NOT NULL DEFAULT 'single'` (`'single'`|`'per_clan'`),
  migrated via `_add_column_if_missing`, included in load/save.
- New table `cwl_clan_coordinator_roles (guild_id, clan_tag, role_id, UNIQUE(guild_id, clan_tag))`,
  FK guild_config ON DELETE CASCADE — same "standing per-clan config" shape as `guild_clan_roles`.
  Loaded into config as `cwl_clan_coordinator_roles: Dict[clan_tag, role_id]`. Written with a
  replace-all `save_cwl_clan_coordinator_roles(guild_id, mapping)`.
- Both the single role id and the per-clan mapping are kept when the mode flips, so switching back
  restores the previous links.

## Sync

`sync_cwl_coordinator_role(guild)` becomes mode-aware by reducing both modes to one shape:
`role_id -> set(target user ids)`.

- single: `{single_role: union of every clan's coordinators}` (unchanged behaviour).
- per_clan: for each linked clan, `role -> that clan's coordinators`; two clans mapped to the same
  role union their coordinators (so a shared role can never strip someone still covered by the
  other clan).

Each role is reconciled independently (add missing, remove non-targets from `role.members`).
Roles not linked in the active mode are left untouched — unlinking/switching means "stop
syncing", not "revoke", consistent with #0086's `cleared` semantics.

Call sites unchanged: coordinator Save and role-config Save.

## UI (CwlCoordinatorRoleConfigurationView, reworked)

Working copy + one Save that persists mode, single role and all per-clan links together.

- Row 0: mode select (single / per clan).
- single: row 1 RoleSelect, row 2 Clear + Save.
- per_clan: row 1 clan select, row 2 RoleSelect for that clan (default_values = current link),
  row 3 Clear (this clan) + Save.
- Content: explanation + current links (per-clan: a "clan → role" list), with an unsaved-changes
  marker when the working copy differs from what's persisted.
- `asyncio.Lock` around every handler (same fix as CwlCoordinatorConfigurationView, Pitfall 49).

CWL Settings embed readout shows the mode, and in per-clan mode the number of linked clans.

## Files

db_manager.py, guild_role_manager.py, ui_cwl_roster.py, QBdiscocmdshelper_cwl.py, en/de.json,
tests, docs (CWL_ROSTER_PLANNING_PLAN.md, DATABASE_ARCHITECTURE.md, CODE_STRUCTURE.md),
changelog, BOT_BUILD.
