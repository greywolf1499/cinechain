---
tags: [cinechain, roadmap, platform-architecture, homelab]
aliases: [CineChain Platform Evolution & UX Roadmap Final]
date_created: 2026-09-20
---
# 🛠️ CineChain

This document outlines the architectural, UX, and administrative upgrades required to evolve CineChain from a single-game tracker into a multi-tenant, multi-game platform.

## 1. UI/UX Architecture & Tools Hub

### The "Tools & Utilities" Hub
* **Concept:** As new game modes (Roulette, Bracket, TSP Router) are added, a global "Bridge Solver" navbar link loses context.
* **Execution:** Create a `/tools` portal. 
  * **Flow:** Select Tool (Bridge, Map, Bingo Gen) → Select Context (Freeplay vs. Active Run). 
  * **Game-Awareness:** If a user selects a run where the tool is mathematically incompatible (e.g., Bridge Solver on a Random Roulette run), the UI disables the option with a tooltip.

### Settings & Admin Redesign
* **Concept:** The settings page must scale to handle multiple integrations.
* **Execution:** Migrate to a vertical sidebar layout (`react-router` nested routes):
  * *General* (Users, UI Theme)
  * *Engine* (Timeouts, JSON Rulesets)
  * *Integrations* (TMDB, OMDb, Jellyfin, Letterboxd)
  * *Tasks & Logs* (See Section 4)

### Metadata Completeness (Overviews)
* **Concept:** Movies must feel rich and descriptive.
* **Execution:** Ensure the JIT TMDB fetcher explicitly extracts and caches the `overview` and `tagline` strings into the `movie_cache` SQLite table. Display these prominently on hover states and detail modals.

---

## 2. Advanced Graph UX (Bridge Solver 2.0)

### Dynamic Path Highlights (Tagging)
* **Concept:** Instead of rigid categories ("Path 1: Shortest"), paths should be dynamically analyzed and tagged.
* **Execution:** Once the BFS returns a path, run it through analyzer functions to append UI highlight chips:
  * `[ 👥 Alternative Cast Link ]`
  * `[ 🏆 3 Canon Films ]`
  * `[ 🌍 Multi-Country Journey ]`
  * `[ ⏱️ Epic Runtimes ]`

### Node & Route Swapping (Edge Multiplicity)
* **Concept:** Allow users to tweak a generated path without recalculating everything from scratch.
* **Execution:** Two distinct mathematical swap options on the UI:
  1. **The Same-Actor Swap:** `Intersection(Actor_A_Movies, Actor_B_Movies)`. Finds a different movie starring the *exact same two actors* (e.g., swapping *Casino* for *Goodfellas*).
  2. **The Broad Route Swap:** `Intersection(Movie_A_AllActors_Movies, Movie_C_AllActors_Movies)`. A massive 2-hop recalculation that finds an entirely different pair of actors to bridge the gap between A and C.

### "Search Deeper" (Cache Building)
* **Concept:** If the solver returns paths, but the user wants weirder, deeper connections.
* **Execution:** A `[ 🔍 Search Deeper ]` button resumes the BFS from its current depth limit (e.g., Depth 2 → Depth 3). This intentionally builds the SQLite cache while searching for obscure routes.

---

## 3. Challenge Engine V2 (States & JSON Rulesets)

### Perpetual Runs & Opt-In Win/Loss States
* **Concept:** By default, CineChain runs are *perpetual* (no finish line, no fail state for burning wildcards). Win/Loss conditions are opt-in modifiers.
* **Execution:** 
  * Add `status` enum: `Active`, `Completed`, `Forfeited`, `Failed`. 
  * Highly customizable conditions (e.g., "Win after 3 decades visited", "Fail if 5 movies share the same actor link").

### The "Super-Unlock" JSON Rule Builder
* **Concept:** A raw JSON editor for admins to build limitless, deeply nested conditional logic that the UI cannot express.
* **Execution:** JSON is injected into the Strategy Pattern engine's `validate_step` pipeline. 
* **Compatibility Check:** The engine evaluates if the current game mode supports JSON overrides (e.g., Graph modes support it; rigid Trackers ignore it).

### Backwards Compatibility & Migrations
* **Rule:** Database changes must never break existing active runs. 
* **Execution:** Use Alembic/SQLModel migrations. Legacy runs are tagged with `engine_version = 1`. New rules strictly apply to `engine_version >= 2`, or migration scripts inject default fallback states into old rows.

---

## 4. Data, Tasks, & Caching Operations

### Threaded Admin Tasks & Activity Monitor
* **Concept:** Long-running admin actions (syncing Letterboxd lists, scanning Jellyfin libraries) must not block the main FastAPI thread or require heavy daemons.
* **Execution:** 
  * Use FastAPI `BackgroundTasks` via `asyncio`.
  * Track state in a lightweight SQLite table `SystemTask`.
  * Admin UI features a "Task History" page with SSE/polling to show live progress bars and statuses (Running/Completed/Failed).

### TTL Caching Strategy
* **Concept:** Dynamic data gets stale; static data doesn't.
* **Execution:** 
  * Add a `last_updated` timestamp to cache tables. 
  * *Jellyfin / OMDb Ratings:* TTL of 7–14 days. JIT refresh on read if stale.
  * *Manual Refresh:* Admin button `[ ⚡ Flush & Refresh Stale Cache ]`.

### Letterboxd Data Boundaries & Caching
* **Concept:** Maximize data extraction without risking Cloudflare IP bans.
* **Execution:** 
  * *Allowed & Required:* Scraping Letterboxd for User Diaries, Curated Lists, Watchlists, Community Ratings, and **TMDB IDs**. Extracting the `data-tmdb-id` from Letterboxd HTML is the essential bridge key that links Letterboxd lists to our local TMDB cache.
  * *Strictly Banned:* Scraping Letterboxd for heavy universal metadata (Plot Overviews, Backdrops, Full Cast Lists, Runtimes). Once we extract the TMDB ID from Letterboxd, we use the official TMDB API to fetch the heavy metadata safely.

---

## 5. Meta-Progression & Global Profiles

### True Multi-Account Sessions
* **Concept:** CineChain is a household app. Partners need to be able to log in securely from their own separate phones simultaneously.
* **Execution:** Implement lightweight JWT/Cookie-based authentication. Users log into their distinct accounts but collaborate on shared `ChallengeRun` instances via role-based access.

### The Global Passport
* **Concept:** A master view of a user's entire cinematic life, aggregating all `watched_at` steps across all game modes.
* **Execution:** 
  * Master Map & Stats board.
  * *Letterboxd Diary Import:* Allow users to import their Letterboxd Diary export (CSV or RSS) to retroactively populate the Global Passport (countries, decades, directors) without needing to re-watch everything inside CineChain.

---
## 0. Developer Primer (Constraints)
* **Compute:** Async tasks must use native Python `asyncio`. No Celery, Redis, or heavy message brokers.
* **Database:** All task state, JSON rulesets, and user profiles live in the single `/config/cinechain.db` SQLite file.
* **UI Updates:** Task monitoring relies on TanStack Query polling or FastAPI Server-Sent Events (SSE) to keep memory footprint near zero.