# CineChain Project State
**Current Phase:** Mechanics & Synergy programme (post-v1.3.0). Audit complete; implementation not started.
**Current Objective:** Execute `docs/SYNERGY_IMPLEMENTATION.md`, starting with **Phase S0 (correctness hotfixes)**.
**Status:** The post-playtest Mechanics & Synergy Audit is recorded in `docs/SYNERGY_AUDIT.md`: 59 tabled findings (8 P0) across seed/data resilience, engine dynamics, bounties/modifiers/rulesets, and onboarding/shared-device play, traced to five root causes. The phased blueprint (S0–S11, with exact files, functions, tests and a finding → phase traceability table) is `docs/SYNERGY_IMPLEMENTATION.md`. Two Tug of War defects were reproduced directly against `tug_of_war.tally`/`preview_pull`: the raid preview over-states the swing when the victim is at 0, and v2 momentum never exceeds 1 under enforced alternation.
**Recommended execution order:** S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8 → S9 → S10 → S11 (anti-paralysis first). Dependencies are in §12 of the blueprint. S3, S4, S5 and S7 are independent of the rest.
**Validation per phase:** `cd backend && uv run pytest -q` · `uv run ruff check .` · a single Alembic head when migrations change (S2 adds `cached_movies.imdb_id`) · `cd frontend && npm run build`. No `hypothesis`: property tests use seeded `random.Random`.
**Next Steps:** Implement Phase S0 per the blueprint, then follow `docs/AGENT_PLAYBOOK.md` (commit per phase; update this file and `docs/LESSONS.md`).
**Previous milestone:** v1.3.0 (UX & Game-Systems overhaul, Phases 0–4 and 2a–2d) shipped and tagged.
