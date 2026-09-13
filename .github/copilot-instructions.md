# CineChain v1.1 Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB with zero arbitrary popularity filters.
- Homelab: Batched read-only Jellyfin integration ("On Server" badge), dynamic admin settings, memory budget ~60 MiB idle RSS.

## Current State (Phase 12 Complete)
- 72/72 backend tests passing across `backend/tests/test_*.py`.
- Full SPA with Cockpit layout: Transit spine timeline, sticky right rail, mobile viewport fixes, and MovieDetailModal with notes/watched-date editing.

## Active Roadmap: v1.1 Enhancements
- **Phase 13 (ACTIVE)**: Unified "Pick Next" Discovery Hub with multi-actor pooled filmographies, AND/OR co-star filters, actor pill selection, genre/decade filtering, and instant search.
- **Phase 14**: OMDb integration for IMDb/RT ratings, Admin OMDb key manager, and rating/year sorting.
- **Phase 15**: Mid-run rule mutation with wildcard budget guards and run-scoped Bridge Solver synergy.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
