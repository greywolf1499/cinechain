# CineChain v1.1 Development Instructions

## Project Overview

CineChain is a self-hosted movie challenge companion tool built for couples/households.

- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB.
- Philosophy: Unrestricted world cinema. ZERO popularity, vote-count, language, or decade filters. Supports Hollywood, Bollywood, vintage international, and indie films equally.

## Current State (Phase 15 Complete)

- All backend tests passing across `backend/tests/test_*.py`.
- OMDb ratings cached and displayed (IMDb / Rotten Tomatoes / Metacritic).
- Frontier link guard & accidental wildcard burn protection in movie modals.
- Cycle/duplicate movie prevention rule (`allow_movie_repeats`).
- Reality filter excludes unreleased/cancelled/in-production movies from the
  bridge graph and candidate pools (release-date/status only - never
  popularity/vote-count/rating).
- Bridge solver is run-scoped (`run_id`): excludes already-watched movies,
  inherits the run's cast-depth/min-runtime rules, and the Bridge Solver page
  shows a "solving within context of" banner + duplicate-target warning.
- Bridge solver returns up to 3 distinct collision-layer paths (Shortest /
  Alternative Cast Link / Underdog-International), presented as switchable
  tabs; every bridge movie card opens a read-only preview modal.
- Mid-run rule mutation (`PATCH /api/runs/{id}/rules`) with a 409 guard
  against setting the wildcard budget below wildcards already consumed, plus
  an "Edit Rules" modal and expanded Rules Summary card on the Cockpit.
- "Delete Run" is available end-to-end (backend already had cascading
  delete; Phase 15 added the confirmation-gated UI button).

## Active Roadmap: v1.1 Enhancements

- **Phase 16 (ACTIVE)**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
