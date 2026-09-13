# CineChain v1.1 Development Instructions

## Project Overview

CineChain is a self-hosted movie challenge companion tool built for couples/households.

- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB with zero arbitrary popularity filters.
- Homelab: Batched read-only Jellyfin integration ("On Server" badge), dynamic admin settings, memory budget ~60 MiB idle RSS.

## Current State (Phase 13 Complete)

- 75/75 backend tests passing across `backend/tests/test_*.py`.
- Full SPA with Cockpit layout: Transit spine timeline, sticky right rail, mobile viewport fixes, MovieDetailModal with notes/watched-date editing, and a unified "Pick Next" Discovery Hub (pooled cast aggregation, AND/OR co-star filtering, actor chips, genre/decade/search/sort filters).

## Active Roadmap: v1.1 Enhancements

- **Phase 14 (ACTIVE)**: OMDb integration for IMDb/RT ratings, Admin OMDb key manager, and rating/year sorting.
- **Phase 15**: Mid-run rule mutation with wildcard budget guards and run-scoped Bridge Solver synergy.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
