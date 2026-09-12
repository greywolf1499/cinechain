# Technical Specification & Architecture: CineChain

**Project Name:** CineChain
**Type:** Self-Hosted Cinephile Challenge Companion & Discovery Engine
**Deployment:** Single Docker Container (Monolith: FastAPI + Static SPA)
**Host Target:** Arch Linux (Laptop Server running Docker, *Arr stack, Jellyfin)

---

## 1. Executive Summary & Core Concept

**CineChain** is a self-hosted movie challenge companion and exploration tool designed for couples and cinephile households. Rather than an arcade-style game, it acts as an interactive companion for film watching, discovery, and tracking.

The foundational challenge mode is inspired by *Six Degrees of Kevin Bacon*:
1. **The Chain Rule:** Every subsequent movie logged must share at least one credited actor with the previous movie.
2. **Discovery & Recommendation:** After watching a movie, the app surfaces the cast's filmographies, highlights hidden overlaps (e.g., "Actors A & B also starred in Film Z"), and suggests viable next movies.
3. **The Bridge Solver:** When users want to reach an unrelated movie with zero shared actors (e.g., transitioning from Bollywood to Korean cinema or classic Japanese cinema), the engine calculates the shortest path of connecting films.
4. **Unrestricted World Cinema:** No arbitrary filters on popularity, vote counts, release decade, or language. Any film cataloged on TMDB is fully supported.
5. **Extensibility:** Built using a modular Strategy Pattern to easily plug in other movie challenge types in the future (e.g., Director Chains, Decade Climbs, Genre Roulette).

---

## 2. System Architecture & Resource Constraints

### 2.1 Host Context & Resource Budget
* **Hardware & OS:** Arch Linux laptop acting as a daily driver and media server.
* **Coexisting Services:** Jellyfin (GPU transcode), Radarr, Sonarr, ROMM, Gluetun, etc.
* **Resource Target:**
  * Idle RAM consumption: **< 120 MB**.
  * Zero persistent background CPU polling when idle.
  * No heavy external database servers (Neo4j, MariaDB, or Redis).

### 2.2 Container Topology (Monolith Pattern)
* **Single Container Deployment:** A multi-stage Docker build:
  * *Build Stage:* Node.js builds the React/Vite/Tailwind frontend into optimized static HTML/JS/CSS assets.
  * *Runtime Stage:* Lightweight `python:3.12-slim` runs a FastAPI server that serves both the JSON API and the static frontend assets on port **8787**.
* **Persistence:** Single local SQLite database stored in `/config/cinechain.db`.

---

## 3. Data Architecture: Just-In-Time (JIT) Dynamic Graph

Because the platform supports obscure, international, and vintage cinema without popularity restrictions, pre-indexing the entire TMDB catalog is neither necessary nor efficient.

### 3.1 JIT Set-Intersection Pathfinding
1. **1-Hop Validation:** Checks for common cast members between Movie A and Movie B via cached or live TMDB credits.
2. **2-Hop Bridging:** Almost all disparate global films connect within 2 or 3 hops via prolific international or character actors:
   $$\text{Cast}(Movie_A) \to \text{Filmographies} \cap \text{Filmographies} \leftarrow \text{Cast}(Movie_B)$$
   The engine queries TMDB API on-demand for the top 12–15 cast members of both endpoints, fetches their credits, and performs an in-memory set intersection.
3. **Local Adjacency Cache:** Every API call permanently caches movie, actor, and credit tuples into the local SQLite database. Repeat traversals and familiar actors resolve in sub-millisecond local queries.

---

## 4. Modular Challenge Engine (Strategy Pattern)

The core application platform handles runs, logging, Letterboxd sync, and local media discovery. Game mechanics are decoupled via an engine interface:

```
                  ┌─────────────────────────────────────────┐
                  │          BaseChallengeEngine            │
                  ├─────────────────────────────────────────┤
                  │ + validate_next_step(current, next)     │
                  │ + get_suggestions(current, filters)     │
                  │ + solve_bridge(current, target)         │
                  │ + compute_stats(run_history)            │
                  └────────────────────┬────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────┐                             ┌───────────────────────┐
│    CineChainEngine    │                             │  Future Engines...    │
│ (Shared Actor Rule &  │                             │  - Director Ladder    │
│  Bidirectional Bridge)│                             │  - Decade Climber     │
└───────────────────────┘                             └───────────────────────┘
```

---

## 5. Database Schema (SQLite)

```sql
-- Active and past challenge runs
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    game_type TEXT NOT NULL DEFAULT 'cinechain',
    status TEXT CHECK(status IN ('active', 'completed', 'abandoned')) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Sequence of logged films in a run
CREATE TABLE run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    movie_id INTEGER NOT NULL,            -- TMDB Movie ID
    movie_title TEXT NOT NULL,
    movie_poster_path TEXT,
    movie_release_year INTEGER,
    movie_origin_country TEXT,
    transition_metadata JSON,             -- e.g., {"actor_id": 123, "actor_name": "Irrfan Khan"}
    user_notes TEXT,                       -- Personal thoughts/memories
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

-- JIT Adjacency Cache
CREATE TABLE cached_movies (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    release_date TEXT,
    poster_path TEXT,
    overview TEXT,
    origin_country TEXT                   -- JSON array of ISO country codes
);

CREATE TABLE cached_actors (
    tmdb_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    profile_path TEXT
);

CREATE TABLE cached_movie_cast (
    movie_id INTEGER,
    actor_id INTEGER,
    cast_order INTEGER,                   -- Billing position (top 15)
    character_name TEXT,
    PRIMARY KEY (movie_id, actor_id)
);
```

---

## 6. Homelab & Media Integrations

* **Letterboxd (Public Feeds & CSV):**
  * *Ingest:* Reads public user RSS (`https://letterboxd.com/<user>/rss/`) to detect recently watched films and suggest them as the next step.
  * *Export:* Generates standard Letterboxd-compliant CSV format (`Title,Year,tmdb_id`) to export challenge runs directly to Letterboxd lists.
* **Jellyfin Integration (`http://jellyfin:8096`):**
  * Queries local Jellyfin library using user API key.
  * Displays an **"On Server"** badge on suggested films or bridge paths if already downloaded and playable.
* **Radarr / Seerr Integration:**
  * One-click "Request / Queue" action to post missing bridge movies directly to Radarr (`/api/v3/movie`) or Seerr (`/api/v1/request`).

---

## 7. UX & Frontend Capabilities

* **Run Dashboard:** Interactive visual timeline/filmstrip showing posters linked by connecting actors.
* **Discovery Modal ("Fork in the Road"):** Clicking on cast members displays filterable filmographies (Genre, Decade, Country, Jellyfin availability).
* **Bridge Solver UI:** Input any target movie in world cinema; visualizes the 2–3 step connection chain with direct links to play on Jellyfin or queue via Radarr.
* **Passport / Run Statistics:**
  * Cultural breadth (countries visited).
  * Decades traversed (e.g., 1954 $\to$ 2024).
  * Keystone actors (the most frequent or cross-cultural connecting actors).
* **Run Management:** Seamlessly start, pause, resume, or archive multiple runs.

---

## 8. Development & Docker Conventions

* **Port:** `8787` (prevents conflict with host and common homelab ports).
* **Environment Configuration (`.env`):**
  * `PUID=1000`, `PGID=1000`, `TZ=Etc/UTC`
  * `TMDB_API_KEY=<key>`
  * `JELLYFIN_URL`, `JELLYFIN_API_KEY` (Optional)
  * `RADARR_URL`, `RADARR_API_KEY` (Optional)
* **Volumes:**
  * `/config` mapped to `${DATA_ROOT}/configs/cinechain`
