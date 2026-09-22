# CWL guest clans: persistent guild status, member-role rights, removal UI

Origin: STAY family report (2026-09-20) — "Akatsumi" (actually `! ! ＡＫＡＴＳＵＫＩ! !`, #2CGGVVVJG)
re-added as a CWL guest clan, "Notify New Pool Members" reached none of its players.

## Diagnosis (not the Master-3 theory)
The clan's members WERE in the DB: `ensure_cwl_clan_membership_tracked()` had fetched all 25 on
2026-09-17. 23/25 are in the UNASSIGNED pool (no Discord link) and the other 2 belong to one STAY
admin account holder who had already answered for the season. The bot can only DM a player whose
CoC account is linked to a Discord user, so nobody was reachable. The fix for that is getting the
guest players to link — which is exactly what member-role rights for guest clans enable.

Real gap found alongside: `ensure_cwl_clan_membership_tracked()` only fetched a clan with ZERO
known members, so a re-invited guest clan kept a member list of any age.

## Changes
1. New table `guild_guest_clans (guild_id, clan_tag, first_invited_season, last_invited_season)`,
   UNIQUE(guild_id, clan_tag) — many-to-many "which guild invited which clan". One-time backfill
   (bot_metadata-gated) from existing non-cancelled cwl_events. Registered in
   CLAN_TAG_REFERENCING_TABLES so the orphan purge never drops a guest clan's `clans` row.
2. `CACHE.guild_guest_clans` (guild_id -> clan_tag -> row), loaded in `load_all()`, write-through
   helpers `register_guild_guest_clans()` / `remove_guild_guest_clan()`.
3. Guest clans count as tracked in `update_all_clan_subscription_statuses()` /
   `_calculate_subscription_status()` → the regular poll keeps their member list current.
4. Member-role eligibility (`is_player_in_member_clans()`, role-sync member-role block) includes
   guest clans. CoC in-game Leader/Co-Leader roles deliberately NOT — those gate CWL admin rights.
5. `handle_post_clan_config` registers every guest clan of the saved event and runs
   `ensure_cwl_clan_membership_tracked()` for newly added ones, now with a 24h freshness rule.
6. `/clan management` → Families: guest clan list + "Remove Guest Clan" (select → confirm).
   Blocked while the clan is on any non-cancelled event whose season is upcoming or still running
   (season end = latest cwl_start_at + 9 days).
