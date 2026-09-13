# CineChain v1.1 Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith where Node/Vite compiles the frontend into static assets, and Python 3.12-slim (FastAPI) serves the API and the SPA on port 8787.
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB and OMDb with ZERO arbitrary popularity filters.
- Homelab: Batched read-only Jellyfin integration ("On Server" badge), Radarr/Seerr stubs, dynamic Admin settings.

## Current State (v1.0.0 Tagged)
- 70/70 backend tests passing across `backend/tests/test_*.py`.
- Full SPA complete: RunsPage, RunDetailPage, BridgePage, PassportPage, SettingsPage.
- In-app settings configured for TMDB and Jellyfin with secret masking.
- Memory budget verified at ~60 MiB idle RSS.

## Active Roadmap: v1.1 Enhancements
- **Phase 12 (ACTIVE)**: Desktop Cockpit Layout (kills empty space), Mobile Viewport Fixes (kills horizontal scroll/navbar overflow), Vertical Transit Spine Timeline (with 1->N vs N->1 order toggle), and Movie Detail Modal (editable watched_at dates and notes).
- **Phase 13**: Unified "Pick Next" Hub with multi-actor pooled filmographies, AND/OR filters, and modal search bars.
- **Phase 14**: OMDb integration for IMDb/RT ratings, Admin OMDb key manager, and rating/year sorting.
- **Phase 15**: Mid-run rule mutation with wildcard budget guards and run-scoped Bridge Solver synergy.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
