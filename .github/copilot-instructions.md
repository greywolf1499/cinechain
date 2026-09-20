# CineChain v1.1 Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB.
- Philosophy: Unrestricted world cinema. ZERO popularity, vote-count, language, or decade filters. Supports Hollywood, Bollywood, vintage international, and indie films equally.

## Current State (Phase 13.5 & 14 Complete)
- All backend tests passing across `backend/tests/test_*.py`.
- OMDb ratings cached and displayed (IMDb / Rotten Tomatoes / Metacritic).
- Frontier link guard & accidental wildcard burn protection in movie modals.
- Cycle/duplicate movie prevention rule (`allow_movie_repeats`).

## Active Roadmap: v1.1 Enhancements
- **Phase 15 (ACTIVE)**: Mid-Run Rules Recalibration, Run-Scoped Bridge Synergy, Multi-Bridge Paths & Reality Filters:
  1. Reality Filter: Exclude unreleased/cancelled/in-production movies from graph and candidate pools without penalizing indie/international/low-vote films.
  2. Run-Scoped Bridge Solver: Inherit run constraints (`run_id`), exclude already-watched run movies, and show active run banner.
  3. Multi-Path Bridge Output: Return up to 2-3 distinct path options at the collision layer (e.g. Shortest, Alternative, Underdog/Indie).
  4. Mid-Run Rule Mutation: `PATCH /api/runs/{id}/rules` with wildcard safety guards + full rules summary card on Cockpit with "Edit Rules" modal.
  5. Delete Run: `DELETE /api/runs/{id}` endpoint and UI confirmation dialog.
  6. Polish: Bridge movie detail modal support, bidirectional BFS depth label clarity, and card flex-alignment fixes.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
