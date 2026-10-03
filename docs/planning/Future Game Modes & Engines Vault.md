---
tags: [cinechain, roadmap, game-modes, movie-challenges, homelab]
aliases: [CineChain Future Mechanics, Idea Vault Final]
date_created: 2026-09-20
---

# 🍿 CineChain

**Core Philosophy:** CineChain is a living-room companion, not an arcade game. Future modes must facilitate collaboration, eliminate decision paralysis, and celebrate unrestricted world cinema.

---

## 0. Architecture & Implementation Primer (For the AI Agent)
When implementing these modes, strictly adhere to the existing stack and the following homelab boundaries:
* **Base Stack:** Python 3.12, FastAPI, SQLite (WAL mode), SQLModel, React 19, TypeScript, Tailwind v4, Vite.
* **Resource Ceiling (The "Goldilocks" Budget):** The base app idles at ~40MB RAM. For advanced graph/ML features, we allow a strict maximum of **~150MB RAM** and **~1GB Storage**. Zero heavy vector databases (no Neo4j, Redis, or Postgres).
* **The "Cinephile Neighborhood" Cache:** To stay under 1GB storage, do not download the 2GB+ TMDB daily export. Instead, build a background sync that caches the dense network of: Your Jellyfin Server + Curated Canons + exactly 1 actor-hop outward. This enables instant, API-free graph routing.
* **Data Science / ML:** For "Big Algorithm" modes, use lightweight Python C-extensions (`scipy`, `networkx`, `Pillow`) and tiny quantized ONNX models (e.g., the 80MB `all-MiniLM` for text embeddings). Load them JIT (Just-In-Time) or keep them within the 150MB RAM limit.
* **Engine Integration:** New modes must inherit from the existing `BaseChallengeEngine` (Strategy Pattern) so they plug directly into the current SSE (`/api/engine/bridge/stream`) and step validation workflows without breaking the UI.

---

## 1. Co-op & Relationship Dynamics (The "Who Picks?" Solvers)

### Meet in the Middle (The Two-Way Tunnel)
* **Concept:** Partner A picks a beloved movie at one end. Partner B picks a totally different movie at the other end. You alternate movie nights building inward until the two chains collide.
* **Tech:** Reuses Bidirectional BFS logic but allows manual state progression from two distinct `tail_node` pointers.

### The Blind Fork (Offer & Veto)
* **Concept:** The player whose turn it is selects 3 valid connecting movies. The other partner vetoes 1, and chooses the final winner from the remaining 2.
* **Tech:** UI-level workflow extension of the "Pick Next" hub. Stored in React state until the final step is committed.

### Tug of War (Decade / Geography)
* **Concept:** A single chain where players have opposing victory conditions (e.g., Partner A tries to pull toward pre-1970s films; Partner B tries to pull toward post-2010s).
* **Tech:** Engine tracks "Tug of War Points" per user based on `release_year` or `origin_country` metadata on each logged step.

---

## 2. Graph Traversal Variations (Bacon Engine Mutators)

### The Auteur Relay (Actor ↔ Director Zig-Zag)
* **Concept:** You cannot connect actor-to-actor. The chain must alternate: Movie → Director → Movie → Actor → Movie → Director.
* **Tech:** A modification of the BFS valid-link checker to strictly enforce `job == 'Director'` alternating with `job == 'Acting'`.

### The Crew & Craft Trail
* **Concept:** Allows bridging through behind-the-camera legends (Composers like Hans Zimmer, DPs like Roger Deakins). 
* **Tech:** Expand TMDB credits ingestion to cache specific crew roles, creating a multi-modal bipartite graph.

### The Genre Pendulum
* **Concept:** Adjacent movies must share at least one genre, but you must shift the main genre flavor every 2 steps according to a target cycle (e.g., *Horror → Thriller → Crime → Comedy*).
* **Tech:** Valid-link checker ensures genre overlap, while maintaining a state machine for the target cycle.

### Chrono Climb & World Cinema Passport
* **Concept:** 
  * *Chrono Climb:* Every hop must move strictly forward in time (e.g., by decade).
  * *Passport:* Every movie must have a different primary country/language than the previous film.
* **Tech:** Real-time frontier constraints that auto-filter candidate pools based on the tail movie's metadata.

### Character Name Hopping & Golden Reunions
* **Concept:** Hop using *Character Names* (e.g., James Bond to James Bond) or *Golden Reunions* (Director and Lead Actor from Movie A reunite in Movie B).
* **Tech:** UI surfacing of `character_name` overlaps and joint crew-cast intersections in the candidate pool.

---

## 3. List Expeditions & Trackers (Non-Graph Modes)

### The Decade Sieve & Regional Deep Dives
* **Concept:** Generate an expedition by slicing an ingested canon list (e.g., *Sight & Sound 2022*) by a specific era (1970s) or region (Japanese Cinema).
* **Tech:** A tracker-style Engine preset. Requires zero graph traversal; simply queries the `CanonMovieBadge` table with year/country filters.

### The Auteur Marathon (Complete Works Tracker)
* **Concept:** Pick a director (e.g., Christopher Nolan, Satyajit Ray). The app pulls their full feature filmography into a chronological track to watch from debut to finale.
* **Tech:** Dedicated list generation using a TMDB person query, filtered by `job == 'Director'`.

### Criterion / Canon-Only Island
* **Concept:** A purist graph challenge where every candidate movie presented **must** belong to a specific curated catalog (like the TSPDT 1000). The engine blocks non-canon films.
* **Tech:** Run rules enforce `allowed_curated_list = "id"`.

---

## 4. Hybrid Modes & Progression Gauntlets

### Connect the Canon (Stepping Stones)
* **Concept:** Pick 3 milestone movies you want to watch this month. Use actor links (and the Bridge Solver) to chart the path between Milestone 1 → 2 → 3.
* **Tech:** Multi-target BFS waypoint routing leveraging the Cinephile Neighborhood cache for instant solving.

### Canon Infiltration
* **Concept:** Start with a guilty-pleasure popcorn flick and try to bridge to a prestigious masterpiece in under 4 hops.
* **Tech:** Standard challenge UI, but with a fixed target end-state goal.

### The Runtime Staircase
* **Concept:** A 6-movie gauntlet where each consecutive movie must be longer than the last (e.g., <85 min, 85-100 min, 100-120 min... up to >180 min).
* **Tech:** Real-time filter injecting `runtime > previous_step.runtime` into the Pick Next pool.

### The Rotten Tomatoes Split (Critics vs. Audience)
* **Concept:** The app serves movies where Critics and Audience scores diverge by at least 25%. Partner A sides with Critics, Partner B with Audience. Whoever's guess matches your household enjoyment wins.
* **Tech:** SQL query filtering where `ABS(rt_critic - rt_audience) > 25`.

### Watchlist March Madness
* **Concept:** Takes 16 unplayed movies from your Jellyfin/Letterboxd watchlist and seeds them into a single-elimination tournament bracket.
* **Tech:** State machine tracking a bracket JSON object. Movie nights are "matchups" where the winner advances.

### Movie Night Roulette & The Blind Draft
* **Concept:** 
  * *Roulette:* Set sliders (Jellyfin only, <105 mins, >7.5 IMDb). App spins and drops one movie.
  * *Blind Draft:* 3 random valid movies are presented with posters/titles hidden. Vote on the vibe/logline, then reveal.
* **Tech:** Pure SQLite query randomization against cached JIT metadata; UI masking (`filter: blur()`).

---

## 5. "Big Algorithm" Engines (Advanced Math & Local ML)
*Note: These utilize the 150MB RAM allowance to keep advanced math/ML models resident without bloating the homelab.*

### The Semantic "Trope Web"
* **Concept:** Links movies by *what they are about* (Keywords/Tropes) rather than *who is in them* (*Inception* → \[Heist\] → *Ocean's 11*).
* **Tech:** An 80MB ONNX quantized embedding model (`all-MiniLM`) loaded in Python, running cosine similarity on plot overviews.

### Perfect Marathon Router (Traveling Cinephile Problem)
* **Concept:** Input 15 watchlist movies. The engine calculates the mathematically smoothest tonal transition order for a month-long marathon.
* **Tech:** Simulated Annealing or A* Optimization against a weighted "Whiplash Score" (calculating delta between genres, years, and ratings).

### Cinema Turf War (Graph Coloring)
* **Concept:** Partner A claims a starting node (e.g., Scorsese), Partner B claims another (e.g., Studio Ghibli). Watching movies claims territory on the global graph.
* **Tech:** Real-time Voronoi / Network Flow calculations computing territory borders on the SQLite cache.

### The Visual / Aesthetic Gradient
* **Concept:** Link movies to create a smooth color gradient marathon (Red movies → Orange movies → Yellow movies).
* **Tech:** Perceptual Image Hashing / K-Means clustering via `Pillow`. When downloading TMDB posters, extract the dominant hex color and store it in SQLite. Pathfinder calculates Euclidean distance.

### Historical Time-Travel Engine
* **Concept:** Chain movies chronologically based on *when they are set* (e.g., 180 AD *Gladiator* → 1945 *Oppenheimer* → 2049 *Blade Runner 2049*).
* **Tech:** Entity Extraction on TMDB keywords (identifying century/year tags).

### The Vibe Controller (PID / Fatigue State Machine)
* **Concept:** An emotional safety net. If you watch three 3-hour depressing dramas in a row, the engine locks you out of heavy movies and forces a short comedy "Palate Cleanser."
* **Tech:** Hidden Markov Model or basic PID controller tracking a rolling "Heaviness" moving average.

---

## 6. Micro-Mechanics & Meta-Games

### The Bounty Board (Wildcard Quests)
* **Concept:** Start runs with 0 wildcards. Earn them by completing bounties (e.g., "Watch a film <90 mins", "Watch a film pre-1960", "Watch a film with <5k votes").
* **Tech:** Rule-check hooks evaluated after a step is saved, incrementing the run's `wildcard_budget`.

### Tagline Roulette & The Aesthetic Blur
* **Concept:** Replaces posters with TMDB Taglines or blurred CSS gradients in the Pick Next hub. You pick based entirely on marketing copy or color vibe.
* **Tech:** UI toggle. Zero backend changes.

### The Chaser (Automated Double Features)
* **Concept:** After logging a heavy/long film, one click generates a strict "Under 95 min + Comedy/Animation" candidate pool for an immediate palette cleanser.
* **Tech:** A predefined quick-filter payload injected into the Pick Next candidate fetcher.

### The Chaos Button
* **Concept:** A `🎲` button that applies one random, extreme handicap for the current step only (e.g., "Must be pre-1970", "Must have < 6.0 rating").
* **Tech:** Frontend randomized filter injection.

### The Underdog / B-Side Flip
* **Concept:** Reverses default sorting from Popularity/Ratings to *Lowest Vote Count > 0*. Forces discovery of obscure indie films and foreign co-productions.
* **Tech:** Simple SQLite/Frontend sort inversion.

### The Veto Token
* **Concept:** Each user gets exactly 1 "Golden Veto" per month to block their partner's terrible pick. 
* **Tech:** Simple integer column `veto_tokens` on the `User` model, reset via simple TTL/timestamp check.

### The Node Swap (Edge Multiplicity)
* **Concept:** When using the Bridge Solver, you aren't stuck with the exact movie it suggests for a waypoint. You can actively "reroll" a specific step in the path.
* **The Gameplay:** You get two reroll options:
  1. *The Recast (Same Actors):* Swap the movie for another one that stars the exact same two connecting actors (e.g., swapping *Casino* for *Goodfellas*).
  2. *The Detour (New Actors):* Swap the movie by finding a completely different pair of actors that still successfully bridge the gap between the previous and next movies.
* **Tech:** UI executes instant, localized set intersections (`Intersection(ActorA, ActorB)` or `Intersection(MovieA_Cast, MovieC_Cast)`) without recalculating the entire BFS graph.

---

## 7. Single-Player Modes (Solo Gauntlets & Puzzles)

While co-op modes focus on compromise, Single-Player modes are designed for personal cinematic progression, deep-dives, and rogue-like survival mechanics.

### The Rabbit Hole (Rogue-like Survival Mode)
* **Concept:** An endless chain mode where the engine acts as a Dungeon Master. You start at any movie. To step forward, you must use valid actor links, but **the engine applies progressively harder constraints at each depth tier.**
  * *Depth 1-5:* No constraints.
  * *Depth 6-10:* Must be released before 2000.
  * *Depth 11-15:* Must NOT be in the English language.
  * *Depth 16-20:* Must be under 100 minutes.
  * *Depth 21+:* Must have an IMDb score below 6.0!
* **The Vibe:** How deep into the cinematic rabbit hole can you survive before you get stuck in a dead-end with no valid hops? You get exactly 3 "Reroll/Wildcard" lives for the entire run.
* **Tech:** A state-machine in the `ChallengeEngine` that calculates current depth and injects strict filters into the candidate pool generation.

### The Daily Bridge (The "Cine-Wordle" Puzzle)
* **Concept:** A daily global puzzle for solo players. Every day at midnight, the engine uses a seeded randomizer to pick a **Starting Movie** (e.g., *Tropic Thunder*) and a **Target Movie** (e.g., *Amélie*). 
* **The Vibe:** You must chart the path between them in the fewest possible hops. It tests your raw knowledge of actors and filmographies. When you finish, it generates a spoiler-free emoji block to share (e.g., 🎬 🟩 🟩 🟨 🟩 🎯).
* **The Mechanics:**
  * **Anti-Cheat:** The app's Bridge Solver is strictly locked for the daily movie pair until you finish or forfeit the puzzle.
  * **Forfeit & Convert:** If you get stuck, you can hit "Give Up" to reveal the optimal mathematical path. Whether you win or forfeit, you can click `[ Queue as Challenge Run ]` to instantly convert the puzzle's sequence into your next actual movie marathon!
* **Tech:** Daily seeded randomizer using the SQLite cache. Local state blocking for the solver endpoint. A 1-click mutation that converts a bridge array directly into a new `ChallengeRun` in SQLite.

### Blind Spot Bingo (The Watchlist Grid)
* **Concept:** The app generates a 5x5 Bingo board. Instead of numbers, each square is a specific cinematic challenge based on your synced Letterboxd watchlist and Jellyfin library.
  * Examples: *Watch a 1970s Sci-Fi*, *Watch a film directed by a woman*, *Watch a Palme d'Or winner*, *Watch a movie under 85 minutes*.
* **The Vibe:** Gamifies tackling your backlog. You try to get 5 in a row over the course of a month, or go for the ultimate "Blackout" (all 25 movies).
* **Tech:** A randomized grid generator that maps SQL queries to grid squares. When you log a movie that fits a square's parameters, it stamps the board.

### The Method Actor Marathon (The Deep Dive)
* **Concept:** You select a single legendary actor (e.g., Nicolas Cage, Tilda Swinton, Philip Seymour Hoffman, or Song Kang-ho). 
* **The Vibe:** The app curates a chronological "Evolution Track." You watch them grow from an uncredited extra in their debut, to their breakout supporting role, to their Oscar-winning peak, to their late-career indie resurgence. 
* **Tech:** Simple TMDB person-credit fetch, filtered chronologically, with visual milestones added to the UI timeline.

### The Map Clearer (Global Cinema Scratch-Off)
* **Concept:** A long-term background tracker. The goal is to watch a movie from **50 different countries of origin**. 
* **The Vibe:** Every time you log a film from a new country (e.g., your first Iranian film, your first Senegalese film), a literal interactive 3D globe (or a vintage scratch-off map) on your Passport page fills in with color. It pushes you entirely out of your Hollywood/domestic comfort zone.
* **Tech:** Tracks the `origin_country` metadata from TMDB across all your completed steps. Visualized using a lightweight frontend SVG map projection (like `d3-geo` or a simple SVG path filler).

### The Global Passport (The Lifetime Tracker)
* **Concept:** The ultimate meta-game. It transcends individual runs and aggregates every single movie you have ever logged across all co-op and solo modes into one master profile.
* **The Vibe:** It acts as your lifetime cinematic footprint. It tracks your total decades spanned, your most-watched directors, and fills in a master scratch-off globe. 
* **Tech:** Aggregates `watched_at` steps across all `ChallengeRun` instances tied to your authenticated `user_id`. Can be retroactively populated by importing a Letterboxd Diary CSV so you don't start from zero.