# CineChain v1.1 Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming.
- Host: Arch Linux laptop running 17 Docker containers. Strict idle RAM budget < 60 MiB RSS. Zero persistent background daemons.

## Current State (Phase 15 Complete)
- 104/104 backend tests passing across `backend/tests/test_*.py`.
- Run-scoped Bridge Solver with cycle exclusion, active run banner, and multi-bridge candidate paths.
- Unreleased/cancelled reality filter active (no phantom movies, zero indie/international bias).
- Mid-run rule mutation (`PATCH /api/runs/{id}/rules`) with safety guards and Delete Run confirmation.

## Active Roadmap: v1.1 Enhancements
- **Phase 15.5 (ACTIVE)**: Complete UX Polish, Solver Pacing, Jellyfin Diagnostics & Curated Canon Engine:
  1. In-Dialog Breadcrumb Navigation: Eliminate modal stacking; allow drill-down and back navigation inside a single modal shell.
  2. Bridge Solver Pacing & Timeout: Server-level search timeout setting, adaptive TMDB rate-limiting during BFS.
  3. Jellyfin Match Diagnostics: Settings inspector tool and robust title/year/TMDB matching for local files.
  4. Curated Canon & Letterboxd Engine: Port `letterboxd_poc.py` into native on-demand service, store caches in `/config/cache_letterboxd`, add Settings UI for Canons/Watchlists, wire laurel badges (`🏆 SS22`), and activate "The Cinephile Route" in Bridge Solver.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
