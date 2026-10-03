# CineChain v1.1 Development Instructions

## Current State (Phase 16 Complete)
- 194/194 backend tests passing. Radarr/Seerr integrations active.

## Active Roadmap: v1.1 Enhancements
- **Phase 16.5 (ACTIVE)**: Live QA Bug-Squash & UX Polish:
  1. Jellyfin UI & 401s: Make "On Server" highly visible on Discovery grid cards (green tint/badge). Fix the 401 Unauthorized error specifically on the "Lookup Inspector" tool in Settings.
  2. Bridge Timeout Ceiling: Remove the arbitrary max limit on `bridge_max_duration_seconds` (allow up to 600s).
  3. TMDB Rate-Limit Fix: Enforce a strict async concurrency limit in the TMDB client to prevent 429 budget exhaustion during deep Bridge searches.
  4. Missing Overviews: Implement frontend JIT fetch for movie details if descriptions are missing on Bridge/Discovery cards.
  5. Sync UX & Watchlist Bug: Make "Last Synced" status a prominent, color-coded UI badge. Debug and fix the failing Letterboxd Watchlist Sync endpoint.
  6. Request Modal Clarity: Explicitly label "Routing via Seerr" vs "Direct to Radarr" and rename server dropdowns.
  7. HQ UI Clutter & Navigation Stack: Hide the 900 discovered HQs behind a "Manage Curators" modal using an in-dialog navigation stack.

# CineChain Copilot Directives
1. **Context First:** Before writing any code or proposing solutions, you MUST read `docs/STATE.md` to understand the current objective and `docs/LESSONS.md` to avoid known pitfalls.
2. **State Management:** When you complete a task or resolve a significant bug, you MUST remind the human to update `docs/STATE.md` and append any new learnings to `docs/LESSONS.md`.
3. **Constraint Enforcement:** Never suggest Redis, Celery, cron jobs, or heavy vector databases. All background tasks must be ephemeral. All ML/Graph models must be loaded JIT and garbage collected.
4. **Documentation Sync:** If a task involves game mechanics or UI evolution, ask the human if you should read specific files in `docs/planning/` before proceeding.
5. **Git Commits:** Upon successfully completing a phase and verifying the code compiles/tests pass, you MUST automatically stage and commit your changes using a descriptive conventional commit message.
