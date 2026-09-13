# CineChain v1.1 Development Instructions

## Project Overview
CineChain is a self-hosted movie challenge companion tool built for couples/households.
- Architecture: Single-container monolith (Vite SPA -> FastAPI serving static assets & API on port 8787).
- Database: SQLite with SQLModel at `/config/cinechain.db` using WAL mode.
- Pathfinding: Bipartite graph (Movie <-> Actor), iterative bidirectional BFS with live SSE streaming. JIT caching from TMDB with zero arbitrary popularity filters.
- Homelab: Batched read-only Jellyfin integration ("On Server" badge), dynamic admin settings, memory budget ~60 MiB idle RSS.

## Current State (Phase 13 Complete)
- 75/75 backend tests passing across `backend/tests/test_*.py`.
- Full SPA Cockpit: Transit Spine Timeline, sticky right rail, mobile viewport fixes, `MovieDetailModal` with 1-click watched action.
- Unified "Pick Next" Hub (`PickNextHub.tsx`): multi-actor pooled filmographies, AND/OR co-star filters, actor chip selector, genre/decade filters, instant search, and planned/watched step creation.
- Concurrency Hardening: SQLite SAVEPOINT pattern in `cache_repo.py` prevents race conditions during parallel JIT caching.

## Active Roadmap: v1.1 Enhancements
- **Phase 13.5 & 14 (ACTIVE)**: 
  1. Frontier Link Guard & Cycle Protection:
     - Enforce `allow_movie_repeats: bool = False` in `RunRules` / backend `validate_step` (blocks cycles/duplicate movies unless rule allows).
     - Guard accidental wildcard burn in movie/actor modals when looking at non-frontier movies: show explicit amber warning ("Breaks connection with frontier; will burn 1 of X wildcards"), require confirmation, and provide a "Bridge to Here" shortcut button.
  2. OMDb Ratings Integration:
     - OMDb API client for IMDb, Rotten Tomatoes, and Metacritic scores (JIT cached in SQLite).
     - In-app Admin OMDb API key management in Settings.
     - Rating display pills on cards and sorting in discovery hub (by IMDb / RT score).
- **Phase 15**: Mid-run rule mutation with wildcard budget guards and run-scoped Bridge Solver synergy.
- **Phase 16**: Real Radarr & Seerr write-integrations (quality profiles, root folders, one-click send).
