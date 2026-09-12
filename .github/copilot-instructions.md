# CineChain Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith where Node/Vite compiles the frontend into static assets, and Python 3.12-slim (FastAPI) serves the API and the SPA on port 8787.
- Database: SQLite with SQLModel, located at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB with ZERO popularity or language filters.
- Homelab: Batched read-only Jellyfin integration ("On Server" badge via `/Items`), Radarr/Seerr stubs.

## Current State (Committed at Git HEAD: 94141c4)
- Phases 0 through 9.1 are fully implemented and verified.
- Backend: 61/61 tests passing across `backend/tests/test_*.py`.
- Frontend: React 19 / Vite / Tailwind CSS v4. RunsPage, RunDetailPage, ChainTimeline, and ForkInTheRoadModal are functional.
- Rule Engine & Planning: Supports presets (Standard, Purist, Casual, Custom), no-repeats, no-consecutive-actors, retroactive backfill via `watched_at`, and `status = "watched" | "planned"`.
- Existing Page Shells: `BridgePage.tsx`, `PassportPage.tsx`, and `SettingsPage.tsx` exist in `frontend/src/pages/` as placeholders awaiting full implementation.

## Active Milestone: Phase 10 (Implement Bridge, Passport & Settings)
1. `frontend/src/pages/BridgePage.tsx` & `src/components/BridgePathView.tsx`:
   - Replace placeholder with live SSE streaming pathfinder (`/api/engine/bridge/stream`).
   - Starting movie selector (defaults to tail movie of active run, or search any movie) + Target movie autocomplete.
   - Max depth slider (2 to 5 hops, default 4).
   - Live streaming indicator (showing depth, time, films examined) + Cancel button to abort connection.
   - Handle `exhausted` events with an explicit "Retry with Higher Depth" affordance.
   - Bridge path display with connecting actor badges and batched Jellyfin `OnServerBadge`.
   - "Queue Bridge to Active Run" button: Appends solved bridge movies as "planned" steps to the active run.
2. `frontend/src/pages/PassportPage.tsx`:
   - Replace placeholder with run stats fetched from `GET /api/runs/{id}/stats`:
   - Origin countries visited (clean CSS badges), decade distribution, keystone connecting actors, and run score/medals.
   - Keep lightweight: CSS/Tailwind layouts only; NO external charting or graph libraries.
3. `frontend/src/pages/SettingsPage.tsx`:
   - Integrations card: Status check for Jellyfin connectivity and Radarr/Seerr stubs.
   - Cache stats card: Displays total cached movies, actors, cast edges, and SQLite DB size on disk.
   - User Management card: View users and register new participant accounts (for admins).

## Strict Guardrails
- Keep idle RAM < 120MB. Never introduce Celery, Redis, or Neo4j.
- Never add heavy canvas/graph libraries (e.g. D3, Cytoscape, React Flow) to the frontend.
