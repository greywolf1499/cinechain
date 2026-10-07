# CineChain v4 Implementation Blueprint

**Source of findings:** [`V4_AUDIT.md`](./V4_AUDIT.md) (finding IDs `V<step>-<nn>`, root causes R1–R6).
**Baseline:** `master` @ `c6e3584` (v3.0.0). Alembic head at baseline: `a2b3c4d5e6f7`.
**Binding rules:** [`AGENT_PLAYBOOK.md`](./AGENT_PLAYBOOK.md) and `.github/copilot-instructions.md`.
Every phase obeys them: read `STATE.md`/`LESSONS.md` first; server-owned keys go in
`SERVER_OWNED_METADATA` (`api/routes_runs.py`) / `SERVER_OWNED_RULES` (`services/blind_fork.py`) in
the same commit with a forge test; no Redis/Celery/cron/brokers/vector databases; **no new ML
models or ML dependencies** (Arctic ONNX + local Qwen stay JIT-loaded and garbage collected);
searches stay time-boxed; nothing touches `config/cinechain.db`.

**Phase naming.** Phases are `F0`–`F11` ("F" for *four*) so they never collide with finding IDs.
**Prioritisation principle (inherited from v3):** remove dead ends and contradictions first, then
make every film inspectable and every background job visible, then build the shared Facet Engine,
then add planes, fog and new modes. Each phase lists its dependencies and can ship and commit on
its own. Locate code **by symbol**; line numbers drift.

**Tracks.**

| Track | Phases | Theme |
|---|---|---|
| A. Core UX & Data Spa | F0, F1, F2, F3, F4, F6 | No dead ends, one queue/detail loop, visible tasks, resilient sync, cache maintenance |
| B. Universal Facet Engine | F5 (F5a, F5b), F7 | Typed, indexed, versioned film facets feeding every rule system |
| C. Mode depth | F8, F9 | Rabbit Hole Fog of War; Tug Plane Registry + traversal policies |
| D. Narrative AI & new modes | F10, F11 | Structured AI, Vibe Controller, Grid Crawler, Connect the Canon, Canon Infiltration |

---

## 0. Global conventions for every phase

| Item | Rule |
|---|---|
| Validation gates | `cd backend && uv run pytest -q` · `uv run ruff check .` · migrations: `mkdir -p /tmp/cinechain-dev && CONFIG_DIR=/tmp/cinechain-dev uv run alembic upgrade head && uv run alembic heads` (exactly one head) · `cd frontend && npm run build` |
| Property tests | No `hypothesis`. Seeded `random.Random` or exhaustive enumeration over small spaces. No new test dependencies. |
| Metadata growth | New `/engines` fields go into `EngineMeta` (`api/routes_engine.py`) **and** `EngineMeta` in `frontend/src/types/api.ts`. Frontend mirrors of backend constants are deleted when a phase touches them (V2-08, V5-03, V6-06). |
| Server-owned keys | Each phase lists its new keys. Forge-attempt test in the phase's test file. |
| Legacy runs | Scoring/tier/plane changes are versioned (`tug_rules_version`, `rh_rules_version`, …). Old folds stay untouched and keep their tests green. |
| Facet versioning | Every facet evaluator has a `version`; bumping it marks rows stale in `movie_facet_status` and they are recomputed lazily or by the Data Spa. Never rewrite a finished run's stamped evidence. |
| Missing data | Three-valued everywhere: unknown is never false. Partial success is success, with per-item outcomes recorded and retryable (R4). Never persist a transient failure (S2). |
| Hidden information | Concealment is enforced server-side through `public_rules` (F8) or omitted fields, never only by CSS. |
| AI | All new generation goes through `llm.generate_structured` (F10) with a deterministic fallback; the feature must work with the LLM `off` (the default). |
| Browser acceptance | Scratch `CONFIG_DIR`, provider-isolated transports, mobile widths 390/375, stop servers and remove scratch files afterwards (LESSONS). |
| Bookkeeping | Conventional commit with the Co-authored-by trailer; update `docs/STATE.md`; append `## Phase F<n>: …` to `docs/LESSONS.md`; tick §13. |

---

## Phase F0: Correctness hotfixes (no new architecture)

**Goal:** remove the two P0s and the contradictions players see today, without design changes.
**Depends on:** nothing. **Findings:** V3-01, V4-01, V4-02, V4-04, V5-04, V1-07.

| # | File · symbol | Change |
|---|---|---|
| 1 | `frontend/src/components/{CareerTrack,AuteurTrack,ExpeditionBoard}.tsx` | Replace the terminal `✓ Queued` label with two actions: **Log watched** (`PATCH /runs/{id}/steps/{step_id}/mark-watched` via the existing mutation, with Table Mode `actingFields()`) and **Unqueue** (existing step delete). Keep the sky tint. |
| 2 | `backend/app/api/routes_curated.py` · `_queue_canon_hydration.work` | Partial success: count per-film outcomes (`indexed`, `no_country`, `undated`, `not_found`) and return them in `progress_data.result`; raise only when **no** film indexed or on a systemic error (missing key/auth). Slices already exclude films they can't place. |
| 3 | `backend/app/services/letterboxd.py` · `enrich_entry`; `routes_curated._persist_sync_result` | Entries with `tmdb_type == "tv"` are never persisted as `CanonMovieBadge.movie_id`; they are counted in the sync result as `tv_titles`. |
| 4 | `routes_curated._persist_sync_result` | One transaction: insert/update the new badges, delete the badges no longer present, then commit once. A failed scrape never reaches this function, so the previous snapshot survives. |
| 5 | `backend/app/api/routes_runs.py` · `discover_next_movies`; `engines/rabbit_hole.py` · `discover_candidates` | Move the Rabbit Hole verdict (`tier_compliant`, `constraint_unverified`) into a new `RabbitHoleEngine.annotate_candidates(candidates, rules, history)` called by the route **after** `pool_options.shape_pool` and the `candidate.runtime` read. `discover_candidates` keeps filtering (drop `False` unless off-tier) but no longer writes verdict fields. Base `annotate_candidates` is a no-op. |
| 6 | `engines/tug_of_war.py` · `tug_config`; `frontend/src/lib/tugOfWar.ts`; `pages/RunDetailPage.tsx`; `run-creator/mode-config/TugConfig.tsx` | One default: `DEFAULT_TARGET_LEAD = 7` (the `RuleField` default). Frontend reads the default from `/engines` `rule_fields` instead of constants. Legacy runs store `target_lead` explicitly (prepared on create), so their games don't change; add a regression test asserting that. |

**Tests**
- `tests/test_curated_api.py`: 98/100 indexable films → task `completed` with outcome counts; 0/100 → `failed`; a `tv` entry is never a badge; an exception after scrape keeps the previous badges.
- `tests/test_rabbit_hole.py`: a candidate hydrated by `filter_by_modifiers` or `shape_pool` in the same request is annotated compliant; a candidate with a cached runtime is never `constraint_unverified`.
- `tests/test_tug_v3.py`: raw-JSON Tug run without `target_lead` gets 7; stored legacy targets unchanged.

**Acceptance:** queue then log a film from a Career Track, Auteur Track and Expedition board; re-sync a list containing one undated film and see a completed task with "1 film not indexable"; Rabbit Hole Tier 2 shows no card with both a runtime and "Rule unverified".
**Commit:** `fix: board queue dead end, partial canon indexing, tv entries, atomic list sync and late rabbit hole verdicts`

---

## Phase F1: The universal two-stage loop ("Up Next")

**Goal:** every film-logging mode has the same Stage 1 (queue) → Stage 2 (log/rate/score) lifecycle.
**Depends on:** F0. **Findings:** V3-02, V3-06, V3-07, V3-08 (V3-05 completes in F2).

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/base.py` · `BaseChallengeEngine` | `queue_policy: ClassVar[Literal["frontier", "slot", "none"]]`: `frontier` for graph modes (today), `slot` for trackers/boards/RT Split/Roulette/Decade Sieve, `none` for March Madness. Publish in `EngineMeta`. |
| 2 | `backend/app/schemas/runs.py` · `MarkWatchedRequest`, `RunStepUpdate` | Add `household_score: int \| None` (1–100) and `no_contest: bool = False`. |
| 3 | `backend/app/api/routes_runs.py` · `create_step`, `mark_step_watched`, step `PATCH` | Extract `_settle_on_watch(session, run, step, payload)` from the `split` branch of `create_step` (OMDb warm-up, `settle`, `split_no_contest`). `create_step` accepts `status="planned"` for RT Split (no score required); the planned → watched transition requires `household_score` or `no_contest` and runs the same settlement. All settlement keys stay server-owned (already registered); add a forge test on the transition path. |
| 4 | `routes_runs.discover_next_movies`, engine folds | For `queue_policy == "slot"`, planned steps never act as the frontier and never enter scoring folds until watched (Tug v3's "watch order is game order" timestamp rule is reused for any fold that orders by watch time). |
| 5 | `frontend/src/lib/useLogFilm.ts` **(new)** | One hook: `queue`, `logWatched`, `markWatched(step, extras)`, `unqueue(step)`, pre-flight `/runs/{id}/validate`, overlay-skip confirmation, Table Mode `actingFields()`, the shared log notifications. |
| 6 | `frontend/src/components/UpNextShelf.tsx` **(new)** | Compact strip above every board/timeline listing planned steps (poster, title, **Log watched**, **Unqueue**). For RT Split, Log watched opens `HouseholdRatingModal`. Rendered by `RunDetailPage` for every mode except `queue_policy == "none"`. |
| 7 | `CareerTrack`, `AuteurTrack`, `ExpeditionBoard`, `SplitBoard`, `PickNextHub`, `MovieSearchAutocomplete`, `RouletteSpinner`, `BlindDraft` | Call `useLogFilm`; delete per-board `log()` copies. `SplitBoard` pool cards gain **Queue**. |
| 8 | Copy | One verb pair: **Queue** / **Log watched**; chips **Up next** / **Watched**. "Plan for Later" → "Queue"; "Queue as Challenge Run" → "Start a run from this". |

**Tests**
- `tests/test_bounty_split.py`: planned split step accepted; mark-watched without score → 422; with score → settled point; with `no_contest` → no point; forged settlement keys on mark-watched stripped.
- `tests/test_runs_api.py`: `queue_policy` published for every registered engine; a planned step on a `slot` engine is not the discovery frontier.

**Acceptance:** queue two films in RT Split, rate one later from the Up Next shelf; queue on a graph mode still sets the frontier; shelf fits 390/375.
**Commit:** `feat(ux): universal up-next queue with settlement on watch`

---

## Phase F2: Ubiquitous detail & bite-sized rules

**Goal:** any poster opens the full detail sheet (unless a mechanic conceals it); rules teach in context without jargon.
**Depends on:** F1 (actions slot uses `useLogFilm`). **Findings:** V3-03, V3-04, V3-05, V3-09, V3-10.

| # | File · symbol | Change |
|---|---|---|
| 1 | `frontend/src/components/MovieDetailSheet.tsx` **(new)** | Merge `MovieDetailModal` and `MoviePreviewModal`: keyed by `movieId`; optional `step` adds notes / watched date / mark watched / delete; optional `actions` slot. Owns plot, tagline, cast strip, ratings, canon badges, `AcquisitionControl`, Jellyfin. `PickNextHub`'s movie screen renders the same body inline. Delete the two old modals. |
| 2 | `frontend/src/store/movieDetailStore.ts` **(new)** | zustand store + `useMovieDetail().open(movieId, {step?, actions?})`; one sheet mounted in `AppLayout`. |
| 3 | `frontend/src/components/MoviePoster.tsx` | New props `movieId?: number`, `concealed?: boolean`. With `movieId` and not concealed it renders a `button` that opens the sheet (keyboard + `aria-label="Details for {title}"`). |
| 4 | All 26 `MoviePoster` call sites | Pass `movieId`, or `concealed` for Blind Draft, Roulette pre-reveal, Tagline Roulette (later: Rabbit Hole fog, Grid Crawler fog). Boards pass their step so the step section appears (V3-05). |
| 5 | `backend/app/engines/rulebook.py` · `GLOSSARY` → `glossary(rules)` | Variant-selected glossary per engine/rules version; v3 Tug runs never see v2 text. |
| 6 | `backend/tests/test_rulebook.py` | Guard: card goal ≤ 14 words; drawer turn bullets ≤ 3 × 16 words; no bullet > 25 words; banned terms (`v1`, `v2`, `v3`, `legacy`, `server`, `metadata`, `predicate`, `overlay`, `modifier`, `soft violation`, `fold`) absent from rendered player copy for every engine × preset; syllable-heuristic grade ≤ 8 for card/drawer. |
| 7 | `backend/app/api/routes_runs.py` · `GET /runs/{id}/coach` **(new)**; `engines/base.py` · `coach_line(run, steps)` | Cache-only "Right now" line (Tug streak/raid, Rabbit Hole next tier, a bounty the frontier pool could complete). Default `None`. |
| 8 | `frontend/src/components/HowToPlay.tsx` | Show the coach line first; per-mechanic just-in-time tips (seen keys per mechanic in `localStorage`, extending S3's per-mode keys); long dynamic strings split into bullets with chip emojis. |

**Tests:** a call-site guard without adding a frontend test runner: `backend/tests/test_poster_callsites.py` that greps `frontend/src` for `<MoviePoster` without `movieId=` or `concealed` and fails (no frontend test runner is added). Rulebook guard above. `test_runs_api.py`: `/coach` is participant-guarded and cache-only.
**Acceptance:** every board, tunnel, fork panel, bracket, router, bingo and daily page poster opens the sheet with Radarr/Jellyfin; Blind Draft stays masked; Tug drawer shows "Right now: Ben's streak is ×3 — a Raid breaks it."
**Commit:** `feat(ux): universal movie detail sheet and contextual how-to-play`

---

## Phase F3: Global background tasks

**Goal:** background work is visible, resumable and cancellable from anywhere.
**Depends on:** nothing. **Findings:** V4-05, V4-06.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/models/system.py` · `SystemTask` + migration **(new)** | Add `cancel_requested: bool = False` and `link: str \| None`. Single head. |
| 2 | `backend/app/services/task_runner.py` | `submit_task(…, link=None)`; `TaskContext.cancelled()` (cheap row read, throttled like progress); a cancelled task ends `failed` with `progress_data.error.code = "cancelled"` at the next checkpoint. No thread killing. |
| 3 | `backend/app/api/routes_tasks.py` | `POST /tasks/{id}/cancel` (owner or admin; 409 when finished). |
| 4 | Long jobs (`letterboxd` scrapes, `canon_hydrate`, passport imports, director backfill, LLM download) | Check `ctx.cancelled()` between items. |
| 5 | `frontend/src/lib/tasks.ts` → `TaskStreamProvider` **(new)** | One app-level `EventSource('/api/tasks/stream')` + 15 s poll fallback feeding `TASKS_KEY`; `useLiveTasks`/`useTrackedTask` read it. `TASK_TITLES` covers every task name. |
| 6 | `frontend/src/components/TaskIndicator.tsx` **(new)**, `layouts/AppLayout.tsx` | Top-bar indicator (hidden when idle), popover with `TaskProgressBar`, elapsed time, View (`link`) and Cancel; completion/failure toasts for transitions seen this session. |
| 7 | `CuratedCanonsCard`, `CuratedListCard` | Replace `runTask/waitForTask` local polling with `useTrackedTask` resume-by-`dedupe_key`. |

**Tests:** `tests/test_task_runner.py` (cancel flag stops at a checkpoint; finished task cancel → 409; `link` stored); `tests/test_tasks_api.py` (cancel permissions); `backend/tests/test_task_titles.py` (every `submit_task` name literal appears in `TASK_TITLES`).
**Acceptance:** start a list sync, navigate to a run page, watch the indicator progress, get a completion toast; cancel an import mid-way.
**Commit:** `feat(tasks): global task indicator, resume and cancellation`

---

## Phase F4: Resilient Letterboxd matching

**Goal:** list syncs match what can be matched, record what can't, and let an admin fix it.
**Depends on:** F0 (atomic persistence); F3 recommended (toasts). **Findings:** V4-03.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/models/curated.py` · `CuratedListEntry` **(new)** + migration | `curated_list_entries(list_id, position, slug, title, year, imdb_id, tmdb_id NULL, match_tier, status ∈ matched/unmatched/tv_title/ambiguous, reason, attempted_at)`; unique `(list_id, position)`. Single head. |
| 2 | `backend/app/services/tmdb_resolver.py` | Tiered resolver: (0) inline id respecting type; (1) exact title+year; (2) `normalize_title()` (NFKD fold, punctuation, `&`↔`and`, leading article, Roman↔Arabic) ± 1 year; (3) title-only then slug query, director tie-break; (4) `/find/{imdb_id}?external_source=imdb_id`. Returns `(tmdb_id, tier, status, reason)`. |
| 3 | `backend/app/services/tmdb.py` · `find_by_imdb_id` **(new)** | The `/find` call through the shared pacing/backoff. |
| 4 | `backend/app/services/letterboxd.py` · `enrich_entry`, `resolve_tmdb_multipass` | The sync path calls the shared resolver via a sync adapter; the IMDb id is fetched from the film page lazily, only for entries that failed tiers 1–3. `resolve_tmdb_multipass` becomes a thin wrapper (ranking helpers stay shared, so diary import and sync can't drift). |
| 5 | `routes_curated._persist_sync_result` | Writes entries and badges in the F0 single transaction; result `{matched, unmatched, tv_titles, ambiguous}`. |
| 6 | `GET /curated/lists/{id}/entries?status=` and `PATCH /curated/lists/{id}/entries/{position}` (admin) **(new)** | Review and manual match (`match_tier="manual"`); a manual match survives re-sync when the slug is unchanged. |
| 7 | Frontend `CuratedListCard`, `components/ListEntryReview.tsx` **(new)** | "247 matched · 2 unmatched · 1 TV title — Review" and a search-to-match picker. |

**Tests:** `tests/test_tmdb_resolver.py` (each tier incl. diacritics, `&`, articles, numerals, IMDb `/find`, TV exclusion, ambiguity); `tests/test_curated_api.py` (entries persisted, manual match survives re-sync, review endpoints admin-only).
**Acceptance:** sync a fixture list with an accented title, a "&" title, a TV entry and an IMDb-only match; review and manually match the remaining one.
**Commit:** `feat(curated): tiered tmdb matching with reviewable list entries`

---

## Phase F5: The Universal Facet Engine

### F5a. Storage, tier-0 facets, compiler and catalogue (behaviour-neutral)

**Goal:** one typed, indexed, versioned home for film properties; existing predicates become aliases with identical results.
**Depends on:** nothing. **Findings:** V2-02, V2-03, V2-04, V2-05, V2-06.

| # | File · symbol | Change |
|---|---|---|
| 1 | Migration **(new)** | `movie_facets` (`facet_id, value_text, value_num, movie_id, source, confidence`, PK `(facet_id, value_text, value_num, movie_id)` `WITHOUT ROWID`, index `(movie_id, facet_id)`), `movie_facet_status` (`movie_id, family, version, status, computed_at`, PK `(movie_id, family)` `WITHOUT ROWID`); `cached_movies.budget/revenue/collection_id`, `cached_actors.deathday`, `cached_directors.deathday`. Plain JSON1 only (no JSONB). Single head. |
| 2 | `backend/app/services/tmdb.py` · `_normalize_movie_detail`, person helpers | Keep `budget`, `revenue`, `belongs_to_collection.id`, person `deathday`. `cache_repo.upsert_movie` / person upserts persist them (NULL unknown, TMDB `0` stored as `0` and treated as unknown by facets). |
| 3 | `backend/app/facets/` **(new package)**: `registry.py` (`Facet`), `genres.py` (single TMDB genre map; `bounties.GENRE_IDS`, `genre_pendulum.TMDB_GENRE_IDS` re-export it), `regions.py` (single region taxonomy), `lexical.py`, `production.py`, `reception.py`, `micro_eras.py` | Tier-0/1 evaluators from Step 2 §2.5: lexical (first/last letter, length, word count, one-word, title numbers, palindrome, subtitle/sequel markers), production (year, decade, micro-era, runtime band, countries, region, language, genre, setting year/era, collection, director debut/index, posthumous), reception (parsed IMDb/RT/Metacritic, `rating`, critic–audience gap, cult classic, critic darling, bomb, sleeper hit). Relative facets (popularity percentile) are query-time only. |
| 4 | `backend/app/facets/store.py` **(new)**; `cache_repo.upsert_movie`, `upsert_ratings` | `refresh(session, movie_ids, families)` writes rows + status inline for tier-0 families in the same transaction; input changes clear dependent family status (mirrors embedding invalidation). |
| 5 | `backend/app/facets/query.py` **(new)** | `FacetQuery` Pydantic AST (`all`/`any`/`not`/leaf), validated against the catalogue; Kleene `evaluate(facts)`; `compile(query, universe_sql) -> (sql, params)` using one `EXISTS` per leaf; `count(session, query, universe) -> {matches, unknown, pass_rate}`. |
| 6 | `backend/app/engines/predicates.py` | `FacetPredicate` implements the `Predicate` protocol; the 10 legacy ids resolve to facet aliases. `predicate()` keeps its signature. |
| 7 | `backend/app/services/feasibility.py` | `pass_rate`/`cache_pass_rate` use compiled counts when every leaf is a stored facet; the in-memory `Evidence` path remains for relative facets and as a fallback (removed in F5b once parity holds). |
| 8 | `backend/app/services/cache_flush.py` | Delete facet rows/status with their movie (explicit delete; FK enforcement is per connection). |
| 9 | `backend/app/api/routes_facets.py` **(new)** | `GET /api/facets` (catalogue + cache coverage), `POST /api/facets/count` (`{query, run_id?}` → counts + ≤ 6 samples; cache-only; 2 s deadline). |
| 10 | Backfill | A one-off `facets_backfill` task (task_runner, batch 2,000, resumable by `movie_id` cursor) started from the F6 Data Spa or, before F6 lands, from `POST /api/system/facets/backfill` (admin). The migration itself does no provider work. |

**Tests:** `tests/test_facets.py`: every evaluator's boundaries and unknowns (TMDB `0` budget → unknown; palindrome folding; one-word with hyphenation; micro-era membership; cult classic inputs missing → unknown); compiler ↔ Python `evaluate` parity on a seeded random fixture (500 films × 200 random queries); `WITHOUT ROWID` index used (`EXPLAIN QUERY PLAN` contains `USING PRIMARY KEY`); the legacy predicate suite (`test_bounty_feasibility.py`, `test_chaos_chaser.py`, `test_rabbit_hole.py`) passes unchanged; migration upgrade/downgrade/upgrade.
**Commit:** `feat(facets): typed facet store, catalogue, query compiler and predicate aliases`

### F5b. Consumers, bidirectionality and named concepts

**Goal:** every rule system draws from the catalogue; every ordered facet works in both directions.
**Depends on:** F5a. **Findings:** V2-01, V2-07, V2-08 (and completes V2-02/V2-04).

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/modifier_registry.py` · `SequenceModifier` **(new)** | Generic `(facet, direction ∈ {asc, desc}, strict, mode ∈ {increasing, count})`. `alphabet_run`, `ascending_numbers`, `runtime_staircase`, `chrono_direction` become aliases (stored keys unchanged). New registered sequences: `count_down`, `title_length`, `rating_climb`, `into_obscurity`, `last_letter_chain` (pair scope). History folds read stored facet values instead of re-tokenising titles. |
| 2 | `engines/chaos.py` | Handicaps are named facet queries; add inverses (Modern only, Crowd-pleaser, English only) and facet combos (One-word titles, Cult classics). S8b fairness gate unchanged. |
| 3 | `engines/rabbit_hole.py` | `rh_rules_version = 3`: the deck builder draws 1–3-leaf facet compositions inside per-tier pass-rate bands (Tier 2 ≈ 30% … deepest ≈ 3%) on cache **and** reachable pool; v1/v2 decks read stored ids through aliases. |
| 4 | `services/bounties.py` · `normalize_rule`, `custom_bounty`, prompt | Accept facet leaves (≤ 3) whitelisted by the catalogue; legacy condition types become aliases; the prompt lists the 25 best-covered facets for the run universe. Static catalogue bounties use named variants (one "short"). |
| 5 | `backend/app/api/routes_tools.py` · `GET /tools/bingo/squares` **(new)**; `frontend/src/lib/bingo.ts`, `pages/BingoPage.tsx` | Squares are server facet queries; the client predicate table is deleted; stamps validated server-side for the tool. |
| 6 | `facets/registry.py` · `named_variants`; `pool_options`, `lib/chaser.ts` | "short", "epic", "classic", "recent", "hidden gem", "Asian cinema"… defined once; Chaser and Bingo thresholds read them (frontend reads `/api/facets`). |
| 7 | Measured difficulty | `difficulty = clamp(1 + round(-log2(pass_rate)), 1, 6)` from compiled counts, cached per request; decks/Chaos/bounties sort by it. |
| 8 | `engines/base.py` · `FilterSpec` | New `source="facet"` so engines can declare facet filter chips in Pick Next. |
| 9 | `feasibility.Evidence` | Removed once compiled counts cover every registered predicate. |

**Tests:** `tests/test_sequence_modifiers.py` (both directions for every ordered facet; count-down completion; legacy alias parity with the S9 suite unchanged); Chaos/Rabbit Hole/bounty feasibility suites extended with inverse predicates; `tests/test_bingo_tools.py` server squares; difficulty monotone in pass-rate.
**Acceptance:** create a run with Count Down from 10 and Z→A; Chaos can roll "Modern only"; a Rabbit Hole v3 deck shows a composition tier ("Cult classic + one-word title").
**Commit:** `feat(facets): bidirectional sequence modifiers and facet-driven chaos, decks, bounties and bingo`

---

## Phase F6: The Data Spa & discovery fallback

**Goal:** proactive, visible, rate-limited cache repair, and Pick Next that widens instead of going silent.
**Depends on:** F3 (indicator, cancel), F5a (facet status). **Findings:** V4-07 (seed JIT), V4-08, V4-09, V4-10.

| # | File · symbol | Change |
|---|---|---|
| 1 | Migration **(new)** · `provider_budgets(provider, day, used)` | Daily budgets (OMDb default 900). Single head. |
| 2 | `backend/app/services/data_spa.py` **(new)** | Treatments `spa_details`, `spa_people`, `spa_ratings`, `spa_embeddings`, `spa_facets` (and `spa_tropes` in F7), each a `task_runner` job with `dedupe_key`, batch cap, cursor, priority (active runs → canon → watchlists → popularity) and per-item `movie_facet_status` outcomes (`unavailable` not retried for 30 days; `error` retried). Embedding batches load the ONNX session once and unload after. "Fix all" chains treatments in one task. |
| 3 | `backend/app/api/routes_system.py` | `GET /system/cache/health` (SQL coverage counts), `POST /system/spa/{treatment}` (admin), `POST /runs/{id}/prepare` (participant; same treatments scoped to the run's current pool, capped). |
| 4 | `engines/base.py` · `widen_pool(frontier, rules, history, rung)`; `discover_with_modifiers` | Dry-pool ladder when `len(pool) < DRY_POOL_MIN = 8`, inside a shared 12 s deadline and the existing hydrate budgets: engine widen hook (Semantic: next related page, then `/discover/movie` by top genres + keywords; graph modes: `cast_limit + 10` and the next uncached filmographies; Passport/Chrono: more sampled countries/decades), then a facet SQL query for the engine's rule, then stop. |
| 5 | `routes_runs.discover_next_movies` | `?envelope=1` → `{candidates, diagnostics: {engine_pool, after_modifiers, after_filters, widened, reason}}`; bare list remains the default for one release. |
| 6 | `engines/algorithms.py` · `SemanticTropeEngine.validate_candidate`; `create_run` | Seed preparation for Semantic runs: embed the seed (and extract tropes when the LLM is on) synchronously for that one film; if the embedding model is unavailable, return the actionable setup error "Download the embedding model in Settings > AI & Embeddings". |
| 7 | Frontend `pages/settings/DataSpaPage.tsx` **(new)**, `SettingsLayout`; `PickNextHub` | Health bars per family, treatment buttons, budget readout; Pick Next distinguishes "filters hid N films" from "the engine found nothing" (shows `diagnostics.reason`, "Search wider", "Prepare this run"). |

**Tests:** `tests/test_data_spa.py` (budget stops cleanly; transient vs unavailable; cursor resume after a simulated restart; priority order; cancel); `tests/test_algorithm_sandbox.py` (empty Semantic pool widens via discover; envelope diagnostics; seed setup error when the model is unavailable); OMDb/TMDB mocked with `respx`.
**Acceptance:** with the LLM off and a cold cache, a Semantic Trope run seeded with *The Fabelmans* either shows candidates after widening or a precise reason; the Spa fills runtimes for a run's pool and the indicator shows progress.
**Commit:** `feat(spa): data spa treatments, provider budgets and dry-pool discovery widening`

---

## Phase F7: Semantic facets & the TVTropes hybrid scraper

**Goal:** trustworthy trope and vibe evidence without new models.
**Depends on:** F5a, F6. **Findings:** V4-07 (trope source), Step 2 semantic facets (prerequisite for F10).

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/facets/anchors.py` **(new)**, `facets/semantic.py` **(new)** | Versioned culture-neutral anchor texts (tragedy, levity/spectacle, valence ±, arousal ±); centroids embedded once per embedding fingerprint into `config_dir/facet_anchors/<fingerprint>.npy`; facets `vibe_valence`, `vibe_arousal`, `vibe_quadrant`, `heaviness_raw` (household percentile computed at query time). Unknown when the film has no vector for the active fingerprint. |
| 2 | `backend/app/facets/tropes.py` **(new)** | Curated trope taxonomy: slug, aliases (incl. TVTropes CamelCase names), our own one-line definition, S10c genre requirements. |
| 3 | `backend/app/services/tvtropes.py` **(new)** | Opt-in (`tvtropes_enabled`, default off; Settings note quoting the site's robots/content signals and an attribution link). Runtime robots.txt check; identifying UA; ≥ 8 s spacing, 1 concurrent, ≤ 150 pages per batch; ETag revalidation; 90-day cache of extracted *link lists only* (raw HTML discarded); Cloudflare challenge/403/429 stops the batch. Resolve work page (title/year), extract `Main/<Trope>` links, map via aliases. **Verify licence/terms before enabling; if disallowed, ship with LLM + manual sources only.** |
| 4 | Validation | Accept a scraped trope when cosine(film overview, trope definition) ≥ the S10c 0.85 normalised floor, or the Qwen yes/no judge (when on) agrees; genre gate always applies. Rows land as `facet_id="trope"`, `source="tvtropes"`, `confidence`. Unmapped slugs stored with `confidence=None` and unused in gameplay. |
| 5 | `services/data_spa.py` · `spa_tropes` | TVTropes (if enabled) then Qwen (if on) per film; manual tags (`source="manual"`) from the existing player confirmation path. |
| 6 | `engines/algorithms.py` · `SemanticTropeEngine._shared_trope`, pools; `services/movie_features.ensure_tropes` | Read the `trope` facet union instead of `extracted_tropes` directly (`extracted_tropes` stays Qwen's raw cache). |
| 7 | Frontend `TropeChips` | Source badge ("via TV Tropes" link, "AI", "You"). |

**Tests:** `tests/test_tvtropes.py` (fixture HTML only, never live: work-page resolution, link extraction, CamelCase→slug, robots disallow honoured, challenge stops batch, cache revalidation); `tests/test_semantic_facets.py` (anchor centroid cache per fingerprint; quadrant thresholds; unknown without vector); trope union and genre gate.
**Acceptance:** with TVTropes enabled against a provider-isolated fixture server, a film gains validated tropes with attribution; with it disabled nothing is fetched.
**Commit:** `feat(tropes): vibe facets and opt-in tvtropes hybrid trope evidence`

---

## Phase F8: Rabbit Hole Fog of War & render-time rules

**Goal:** an opt-in hidden descent, and rule chips that resolve the moment facts are known.
**Depends on:** F0 (annotate-last). F5b optional (`rule_query` upgrades from `predicate_data` to `FacetQuery`). **Findings:** V5-01, V5-02, V5-03, V5-05, V5-06, V5-07.

| # | File · symbol | Change |
|---|---|---|
| 1 | `engines/base.py` · `public_rules(rules, run) -> dict` | Default identity. Applied by `routes_runs._to_run_detail`, `GET /runs/{id}/constraint`, rulebook values and discover annotations. |
| 2 | `engines/rabbit_hole.py` | Creation-only `fog ∈ {off, fog, abyss}` (default `off`); `public_rules` replaces unrevealed deck entries with `{number, start_depth, hidden, emoji, difficulty}`; `upcoming_tier_warning`, `next_tier_name`, `next_tier_rule` use redacted text; full deck when the run is finished. Periscope: `periscope_charges` (1 in fog, 0 in abyss), relic kind `periscope` in the deterministic boundary relics, `revealed_depths`. |
| 3 | `routes_runs` · `POST /runs/{id}/rabbit-hole/periscope {depth}` **(new)** | Pre-checked (active run, future unrevealed boundary, charge available, no pending fork) before spending. |
| 4 | Server-owned | `fog` (frozen after creation), `periscope_charges`, `revealed_depths` → `SERVER_OWNED_RULES`; relic award/undo through S11's `rh_resources_before`. |
| 5 | `POST /engine/rabbit-hole/preview {rules}` **(new)** | Representative deck via `draw_deck` with a throwaway seed, cache-only; refused (409) when `fog != off`. |
| 6 | `schemas/engine.py` · `ConstraintInfo.rule_query`; `schemas/discovery.py` · `DiscoveryCandidate.original_language`, `rating` | Evaluable rule + the facts any registered predicate needs (`rating` via `rating_of`). |
| 7 | `routes_runs` · `POST /runs/{id}/verify-candidates {movie_ids ≤ 24}` **(new)** | Hydrates only those films within the shared budget/deadline and returns facts + server verdicts. |
| 8 | Frontend `lib/ruleEval.ts` **(new)**, `PickNextHub`, `RabbitHoleHud`, `RabbitHoleGameOver`, `lib/gameModes.ts`, `lib/rabbitHole.ts` | Client three-valued evaluator over candidate facts merged with cached movie detail; verify the visible page's unknowns and `setQueryData` the results; HUD silhouettes/periscope button; Game Over "What lay below"; mode card depth gauge; creator preview when fog is off; `RABBIT_TIERS`/`WARNING_WINDOW` read from `/engines`. |

**Tests:** `tests/test_rabbit_hole.py` (redaction per fog level and at Game Over; legality unchanged by redaction; periscope pre-check, spend, relic award/undo; forged `fog`/`periscope_charges`/`revealed_depths` stripped; preview refused under fog; verify-candidates budget); a JSON parity fixture shared by Python predicates and `ruleEval.ts` expectations (checked by a backend test that loads the fixture, and by `tsc`-typed fixture import on the frontend).
**Acceptance:** a fog run shows "? ●●○" for Tier 3 until one hop away; spending the periscope reveals it; opening a candidate's detail resolves its "?" chip to ✓ without refetching the pool.
**Commit:** `feat(rabbit-hole): fog of war, periscope and render-time rule resolution`

---

## Phase F9: Tug Plane Registry & traversal policies

**Goal:** replayable, balanced Tug boards with per-plane link rules; a shared link-policy layer.
**Depends on:** F5a (poles are facet queries). **Findings:** V1-01 … V1-06, V1-08, V1-09, V1-10.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/traversal.py` **(new)** | `LinkPolicy` protocol + `shared_cast` (wraps `CineChainEngine.validate_primary`/pool), `shared_director` (Auteur Relay director path), `shared_any_person` (Crew & Craft), `attribute` (`genre_overlap`, `decade_adjacent`, `language`, `shared_trope`), `draft` (seeded deal via `RouletteEngine.draw`-style distinct draws). Each declares `graph: bool` and its link metadata. Behaviour-neutral for existing engines (they keep their current link code in this phase; Meet in the Middle/Canon Island adoption is backlog). |
| 2 | `backend/app/engines/tug_planes.py` **(new)** | `PoleSpec`, `TraversalRule`, `TugPlane`, `BalanceSpec`; catalogue: `bipolar_decades`, `country_pair`, `language_pair`, `genre_clusters`, `runtime_poles`, `setting_eras`, `critic_audience`, plus frozen legacy `era_classic` / `geo_west_rest` (not offered for new runs). |
| 3 | Balance check | Pole pass-rates ≥ 5%, ratio 0.5–2.0, neutral/contested band 10–50% (compiled facet counts); for graph traversals, cache-only bridge density ≥ 2% within a 1.5 s deadline, else downgrade to the plane's next allowed non-graph traversal with an explanation. 🎲 Random plane draws only passing parameterisations. |
| 4 | `engines/tug_of_war.py` | `tug_rules_version = 4`: inputs `tug_plane {id, params}`, `tug_traversal`; `prepare_run` freezes `tug_plane_snapshot` (poles, overlap policy, traversal, balance), `tug_seed`, `tug_portals`; `validate_primary`/`discover_candidates` delegate to the policy; `draft` maintains `tug_deal` (≥ 1 home, ≥ 1 opponent, ≥ 1 neutral when they exist), regenerated on pull log/delete. Portals: one unlinked hop **into the neutral band only**, offered when lookahead shows zero scoring replies, pre-checked before spending. Capabilities derived from the policy (`solve_bridge` only when `graph`; new `tug_draft`). |
| 5 | Stamps & folds | `_log_step` stamps `tug_territory`, `tug_territory_evidence`, `tug_link` or `tug_portal`; `tally_v3` takes a `territory_of(step)` callback: v4 reads stamps, v1–v3 keep `_territory` through the legacy planes. |
| 6 | `routes_runs._cached_tug_lookahead` | Asks `plane.unknown(row)` / `plane.territory(facts)` and `policy.graph`; no dimension literals. Non-graph policies return pole counts of the deal/pool. |
| 7 | Server-owned | Rules: `tug_plane_snapshot`, `tug_seed`, `tug_deal`, `tug_portals`. Metadata: `tug_territory`, `tug_territory_evidence`, `tug_link`, `tug_portal`. |
| 8 | `routes_engine.EngineMeta` · `tug_planes` | Catalogue (id, label, blurb, params JSON schema, allowed/default traversal). `POST /engine/tug/balance {plane, params, traversal}` returns the readout for the creator. |
| 9 | Frontend `TugConfig.tsx`, `TugOfWarMeter.tsx`, `lib/tugOfWar.ts`, `types/api.ts` | Plane card grid, metadata-driven params (S9 controls), traversal selector, balance readout, Random plane; meter labels from the snapshot; `TUG_DIMENSIONS` and `TugDimension` deleted (legacy runs render via the snapshot synthesised for v1–v3 by `public_rules`). |

**Tests:** `tests/test_tug_planes.py` (each plane's poles/overlap/neutral; balance thresholds and downgrade; draft deal composition and determinism; portal pre-check and neutral-only; v1–v3 fold parity unchanged; forged snapshot/deal/portals/territory stripped; seeded mirror-play win rate 45–55% on `genre_clusters` and a `draft` plane, 2,000 games, as in S6).
**Acceptance:** create France vs Italy (shared cast) and India vs US (auto-downgraded to Genre Overlap with the note), play three rounds each on one device, see stamped territories survive a cache refresh.
**Commit:** `feat(tug): plane registry, balance checks and per-plane traversal policies`

---

## Phase F10: Narrative AI — structured generation, Tale of the Tape, Vibe Controller

**Goal:** grounded, schema-checked narrative features that work with the LLM off; culture-neutral fatigue control.
**Depends on:** F5b (facets), F7 (vibe facets). **Findings:** V6-01 … V6-06.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/services/llm.py` · `generate_structured(config, system, facts, schema, retries=1, fallback)` **(new)** | Facts-only prompt, Pydantic schema parse (tolerating fences/`<think>`), validator hook, one retry, deterministic fallback; never caches a failure. Pitch/critic/teaser/bounties/tropes migrate behaviour-neutrally. |
| 2 | `backend/app/services/tale_of_the_tape.py` **(new)** | `TapeCard` from facets; axis scoring (Tone, Era, Reception, Scale, Theme, Origin, Pedigree); pick 3 contrasting + 1 common-ground axis seeded by `matchup_id`; LLM render with validator (axis names, digits present in facts, only the two titles as names, length caps) or template fallback. |
| 3 | `api/routes_bracket.py` | Generate when a matchup becomes ready (advance/vote request `BackgroundTasks` via `task_runner`) or on first open; store `rules_config["bracket_tape"][matchup_id] = {axes, headline, source, version}` (server-owned, first writer wins); `bracket_commentary` stays readable. |
| 4 | `backend/app/services/vibe_controller.py` **(new, pure)** | `load = 0.8·heaviness + 0.2·length_load` (language-relative runtime from cache medians; unknown → None); PID (`0.6P + 0.3I + 0.1D`, decay 0.7, anti-windup ±2) + hysteresis states `steady/fatigued/recovering`; comfort setpoints Gentle 0.45 / Balanced 0.55 / Brave 0.7. |
| 5 | `engines/modifier_registry.py` · `vibe_control` overlay | `soft` (re-rank + banner) or `strict` (block `load > setpoint` while fatigued; unknown allowed); film scope; any pool engine. `vibe_state` cached in rules (server-owned), always recomputed from steps. |
| 6 | `services/pool_options.py` · `needs_chaser`, `is_chaser` | Chaser = vibe-based (`load ≤ setpoint − 0.15` and below-median length for its language); genre/runtime constants removed; `lib/chaser.ts` reads `/engines`. |
| 7 | Frontend `BracketView` (`MatchupCard` tape layout), `VibeMeter.tsx` **(new)**, `PickNextHub` load chips, `ChaserPrompt` | Tape renders without a button; Table Mode votes per axis; meter shows rolling load and reason. |

**Tests:** `tests/test_tale_of_the_tape.py` (axis selection determinism; validator rejects invented numbers/names; template fallback with LLM off; caching/first-writer; forged `bracket_tape` stripped); `tests/test_vibe_controller.py` (PID/hysteresis transitions; anti-windup; unknown loads ignored; strict overlay blocks only while fatigued; **bias guard**: on a fixture cache, Drama-tagged films with neutral overviews across `hi`/`ko`/`en` get mean heaviness within 0.15, and a 160-min Hindi film is not "long" against a Hindi median of 150); forged `vibe_state` stripped.
**Acceptance:** with the LLM off a matchup shows a three-axis tape; three heavy films trigger "fatigued" and Pick Next re-ranks; a Bollywood musical is not flagged heavy for its runtime.
**Commit:** `feat(narrative): structured generation, grounded tale of the tape and vibe controller`

---

## Phase F11: New game modes — Grid Crawler, Connect the Canon, Canon Infiltration

**Goal:** three new modes built on facets, link policies, fog and a shared goal graph.
**Depends on:** F5b, F8 (`public_rules`), F9 (`LinkPolicy`). **Findings:** V6-07, V6-08.

### F11a. Goal graph

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/services/goal_graph.py` **(new)** | Multi-source/multi-target bounded BFS over the cached movie↔person graph with a `LinkPolicy` for edges; explicit `max_depth`, `max_seconds`; backward frontier seeded with a target **set**; returns `distance`, one path verified hop by hop with `find_links`, `par`. |
| 2 | `services/daily_puzzle.py`, `engines/meet_in_middle.py` · `distance` | Move onto `goal_graph` behaviour-neutrally (existing tests unchanged). |

### F11b. Grid Crawler

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/grid_crawler.py` **(new)** | `TrackerEngine` + optional `LinkPolicy`. Inputs: `size` 4–6, `layout` (gradient/siege), `victory` (`bingo`/`crossing`/`blackout`), `universe` (cache/watchlist/canon list), `fog`, `link`. Board generation seeded by `grid_seed`; cells are facet queries with pass-rate 2–60%; ≥ 1 winning line coverable by distinct films (S9 bipartite matcher); bounded redraws → actionable 422. |
| 2 | Rules | First film claims a start-edge cell; later films claim an unclaimed orthogonally adjacent cell (or adjacent to the last claim in Crawler variant) and satisfy its query; `grid_cell` chosen by the player when several fit (validated); claims folded from steps; Jump = wildcard to any unclaimed cell; Table Mode alternating claims. |
| 3 | Server-owned | Rules `grid`, `grid_seed`; metadata `grid_cell`. `public_rules` hides fogged cell queries. |
| 4 | Pick Next | Pool = union of claimable cells' compiled queries (+ link policy pool when on); candidates carry `grid_cells`. |
| 5 | Frontend `components/GridBoard.tsx` **(new)**, mode card, setup config | 5×5 board, claim highlights, fog silhouettes, line/crossing celebration; Watchlist Bingo tool reuses the generator (`mode=tool`). |

### F11c. Connect the Canon

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/connect_canon.py` **(new)** | `CineChainEngine` + `LinkPolicy`. Inputs: 3 waypoints (picked, or 🎲 from a canon list with S8 feasibility), `order` (`ordered`/`best_order`), `assist`. `prepare_run` computes per-leg `par` with `goal_graph` (6 s per leg, previous legs' intermediates excluded; "unknown par" allowed). |
| 2 | Rules & scoring | Logging the current target stamps `waypoint_reached` and advances `current_leg`; completion at the last waypoint; score = Σ(hops − par). Hints reuse Meet in the Middle's pre-checked spending. Bridge Solver locked for the active leg unless `assist` (Daily Bridge 403 `anti_cheat_locked`). |
| 3 | Server-owned | Rules `waypoints`, `legs`, `current_leg`; metadata `waypoint_reached`. |

### F11d. Canon Infiltration

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/canon_infiltration.py` **(new)** | Inputs: seed (picked or 🎲 from a B-movie facet query: `rating ≤ 5.5` with `vote_count ≥ 50`, or `box_office_bomb`), `target_list_id` (default the Sight & Sound preset), `hop_limit` 3–6 (default 4). Creation requires `goal_graph.distance(seed, canon_set) ∈ [2, hop_limit]` (cache; else live solver within budget). |
| 2 | Rules | Win when a logged film is on the target list within `hop_limit` ("Infiltrated *Tokyo Story* in 3 hops"); fail via a new `max_hops` fail type in `engines/conditions.py`. Pick Next shows "~N hops from the canon" from one backward multi-target BFS per request (cache-only, 1.5 s); fog can hide it. |
| 3 | Server-owned | Rules `infiltration_par` (and `target_list_id`, `hop_limit` frozen after creation); metadata `infiltrated`. |

**Tests:** `tests/test_goal_graph.py` (multi-target distance; deadline; Daily/Meet in the Middle parity); `tests/test_grid_crawler.py` (feasible boards incl. distinct-film coverage; adjacency; multi-cell choice; fold on delete; each victory; jump; fog redaction; forged board/cell stripped; seeded determinism); `tests/test_connect_canon.py` (par per leg; disjoint legs; waypoint advance/undo; anti-cheat lock; forged legs stripped); `tests/test_canon_infiltration.py` (seed feasibility; win within limit; `max_hops` fail; distance chips; forged state stripped). Rulebook guard (F2) covers the three new engines.
**Acceptance:** play a 5×5 Grid Crawler to a bingo on one device; finish a three-waypoint Connect the Canon under par; infiltrate a canon list from a B-movie in ≤ 4 hops; all at 390/375.
**Commit (per sub-phase):** `feat(graph): shared goal graph` · `feat(modes): grid crawler` · `feat(modes): connect the canon` · `feat(modes): canon infiltration`

---

## 12. Phase order & dependency graph

```
F0 ─► F1 ─► F2                         F3 (independent) ─┐
F0 ─► F4 (F3 recommended)                                 ├─► F6 ─► F7 ─► F10
F5a ─► F5b ───────────────────────────────────────────────┘          │
F5a ─► F6                                                             │
F0 ─► F8 (F5b upgrades rule_query)                                    │
F5a ─► F9 ───────────────────────────────────────────────► F11 ◄──────┘
F5b ─► F11 ◄── F8
```

Recommended order (anti-paralysis first): **F0 → F1 → F2 → F3 → F4 → F5a → F5b → F6 → F7 → F8 →
F9 → F10 → F11**. F3 and F5a are independent and can be pulled forward. Migrations land in F3,
F4, F5a and F6 only; each must leave exactly one Alembic head.

---

## 13. Traceability (finding → phase)

| Finding | Phase | Finding | Phase | Finding | Phase |
|---|---|---|---|---|---|
| V1-01 | F9 | V2-08 | F5b | V4-07 | F6 (seed JIT), F7 (sources) |
| V1-02 | F9 | V3-01 | F0 | V4-08 | F6 |
| V1-03 | F9 | V3-02 | F1 | V4-09 | F6 |
| V1-04 | F9 | V3-03 | F2 | V4-10 | F6 |
| V1-05 | F9 | V3-04 | F2 | V5-01 | F8 |
| V1-06 | F9 | V3-05 | F2 | V5-02 | F8 |
| V1-07 | F0 | V3-06 | F1 | V5-03 | F8 |
| V1-08 | F9 | V3-07 | F1 | V5-04 | F0 |
| V1-09 | F9 (Tug); other engines → Backlog | V3-08 | F1 | V5-05 | F8 |
| V1-10 | F9 | V3-09 | F2 | V5-06 | F8 |
| V2-01 | F5b | V3-10 | F2 | V5-07 | F8 |
| V2-02 | F5a, F5b | V4-01 | F0 | V6-01 | F10 |
| V2-03 | F5a | V4-02 | F0 | V6-02 | F10 |
| V2-04 | F5a, F5b | V4-03 | F4 | V6-03 | F10 |
| V2-05 | F5a | V4-04 | F0 | V6-04 | F10 |
| V2-06 | F5a | V4-05 | F3 | V6-05 | F10 |
| V2-07 | F5b | V4-06 | F3 | V6-06 | F10 |
| | | | | V6-07 | F11b |
| | | | | V6-08 | F11a, F11c, F11d |

**Backlog (not scheduled):** adopting `LinkPolicy` in Meet in the Middle, Canon Island and Rabbit
Hole (V1-09 remainder); studio/production-company facets (needs TMDB companies storage);
materialised popularity percentiles; cross-device (non-Table-Mode) multiplayer Grid Crawler;
a Vibe Controller "strict" default for specific modes; Tale of the Tape for non-bracket head-to-heads
(Blind Fork offers, Tug draft deals).

---

## 14. Definition of done (whole programme)

- No board or mode has a queued state without a Log watched / Unqueue action (F0/F1 live check across all registered modes).
- Every non-concealed poster opens the detail sheet (F2 call-site guard test).
- Every background task is visible from any route and cancellable (F3).
- A list sync never fails because some films lack details; every unmatched entry is reviewable (F0/F4).
- Every registered predicate, Chaos handicap, bounty, Rabbit Hole tier, Tug pole and Grid cell is a facet query with measured difficulty; every ordered facet supports both directions (F5).
- Pick Next never shows an unexplained empty pool, and no card shows a known fact next to "unverified" (F0/F6/F8).
- Tug planes pass balance checks and mirror play stays 45–55% (F9 property test).
- Every narrative AI feature has a non-AI fallback and a schema validator (F10).
- New modes ship with rulebooks that pass the F2 guard, forge tests for all server-owned keys and mobile acceptance at 390/375 (F11).
- No new ML models/dependencies, brokers, cron or vector databases were added; `STATE.md`/`LESSONS.md` updated after each phase.
