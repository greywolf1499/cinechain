# CineChain Synergy Implementation Blueprint

**Source of findings:** [`SYNERGY_AUDIT.md`](./SYNERGY_AUDIT.md) (finding IDs `S<step>-<nn>`).
**Baseline:** `master` @ `9381a85` (v1.3.0).
**Binding rules:** [`AGENT_PLAYBOOK.md`](./AGENT_PLAYBOOK.md) and `.github/copilot-instructions.md`.
Every phase below obeys them: read `STATE.md`/`LESSONS.md` first, add server-owned keys in the same
commit with a forge test, no Redis/Celery/cron/brokers/vector DBs, no new ML models or ML
dependencies, searches stay time-boxed, and nothing touches `config/cinechain.db`.

**Prioritisation principle:** remove **decision paralysis and dead ends first**. The order is:
the app stops offering things it will reject → the player understands the game → choices
narrow to a few good ones → richer mechanics. Each phase is **decoupled**: it lists its own
dependencies and can ship and commit on its own. Locate code **by symbol**, because line numbers
drift.

---

## 0. Global conventions for every phase

| Item | Rule |
|---|---|
| Validation gates | `cd backend && uv run pytest -q` · `uv run ruff check .` · migrations: `CONFIG_DIR=/tmp/cinechain-dev uv run alembic upgrade head && uv run alembic heads` (exactly one head; create the scratch dir first, per the LESSONS note) · `cd frontend && npm run build` |
| Property tests | No `hypothesis` in the project. Use seeded `random.Random` or exhaustive enumeration over small strategy spaces. **Don't add test dependencies.** |
| Metadata growth | New fields on `/engines` go into `EngineMeta` (`backend/app/api/routes_engine.py`) **and** the single `EngineMeta` interface in `frontend/src/types/api.ts`. That file currently declares `EngineMeta` **twice** (merged by TypeScript): delete the duplicate the first time a phase touches it. |
| Server-owned keys | New computed `rules_config` keys go in `blind_fork.SERVER_OWNED_RULES`, and step metadata in `routes_runs.SERVER_OWNED_METADATA`, each with a forge-attempt test in the phase's test file. |
| Legacy runs | Any rule change to scoring/tiers is **versioned** (`*_rules_version`) and leaves the old fold path untouched (Phase 2a precedent). |
| Bookkeeping | After each phase: conventional commit (with the Co-authored-by trailer), update `docs/STATE.md`, append `## Phase S<n>: …` to `docs/LESSONS.md`, and tick the traceability table (§13). |

---

## Phase S0: Correctness hotfixes (no new architecture)

**Goal:** fix the bugs that show wrong numbers or crash, without design changes.
**Depends on:** nothing. **Findings:** S1-07, S1-10, S1-11, S1-15, S1-18, S1-19, S4-01.

| # | File · symbol | Change |
|---|---|---|
| 1 | `frontend/src/components/run-creator/HeroSeedPreview.tsx` | Replace the `details.origin_country` interpolation with `parseOriginCountries(details?.origin_country)` and render each code as `isoToFlagEmoji(code) countryName(code, code)`, joined by ` / `. |
| 2 | `backend/app/engines/cinechain.py` · `compute_run_stats` | Replace `json.loads(step.movie_origin_country)` with `bridge_paths.parse_countries(...)` (guarded; legacy rows can't 500). |
| 3 | `backend/app/api/routes_router.py` · `_rating_of` | Use the TMDB fallback only when `movie.vote_count >= 10` (extract `movie_filters.verified_tmdb_rating(row)` from `rating_of` and call it from both places). |
| 4 | `backend/app/integrations/omdb.py` | Add `OMDbLookup` (`ratings: OMDbRatings \| None`, `transient: bool`). `lookup_by_title()` returns `transient=True` on `httpx.HTTPError`, a non-2xx response or `Error: "Request limit reached!"`, and `transient=False` on a real "Movie not found!". Keep `get_ratings_by_title` as a thin wrapper (its "never raises" contract is unchanged). |
| 5 | `backend/app/services/cache_repo.py` · `get_movie_ratings` | (a) For a transient lookup, **don't persist** anything; return the existing row or `None`. (b) Treat a cached all-null row as stale once `fetched_at` is older than `RATINGS_NEGATIVE_TTL = timedelta(hours=24)` and re-query. (c) Add a `force: bool = False` parameter that bypasses the cache (used in S2). |
| 6 | `backend/app/main.py` | Register exception handlers: `TMDBRateLimitError` → 429 `{"detail": "TMDB is rate-limiting us - try again in a moment"}`, `TMDBError` → 503 `{"detail": "TMDB is unreachable right now - try again shortly"}`. Routes that already catch these keep their behaviour. |
| 7 | `backend/app/engines/tug_of_war.py` · `preview_pull`, `tally` | A raid's realised steal is `min(scores[opponent], multiplier)`. `preview_pull` returns `multiplier + steal`, and `Pull.points` records the realised net delta. Scores are unchanged (v2 runs keep their totals). |
| 8 | `frontend/src/components/MoviePoster.tsx` | Add `onError` → render the existing placeholder (local `failed` state). |

**Tests**
- `tests/test_tug_momentum.py`: `test_preview_steal_matches_realised_delta_at_zero` (0–0 raid previews 1, not 2); `test_preview_steal_full_when_opponent_has_points`; the existing v1/v2 totals stay green.
- `tests/test_omdb_client.py`: transient vs not-found classification (rate-limit body, 500, timeout, "Movie not found!").
- `tests/test_cache_repo.py`: a transient failure isn't persisted; an all-null row older than 24 h is re-fetched; a fresh all-null row isn't.
- `tests/test_cinechain_engine.py`: stats survive a legacy `"US, GB"` country string.
- `tests/test_marathon_router.py`: a 3-vote 9.4 film has no rating.
- `tests/test_movies_api.py`: a TMDB outage on a cold `GET /movies/{id}` returns 503 with a friendly detail.

**Acceptance:** the hero seed shows `🇮🇳 India`; the Tug Pick Next card at 0–0 says the raid is worth 1.
**Commit:** `fix: correct seed country render, tug steal preview, OMDb negative caching and TMDB outage errors`

---

## Phase S1: Setup without dead ends (seed policy, slices, early validation)

**Goal:** the creator can't produce a configuration the server will reject, and every random
button respects the chosen mode.
**Depends on:** nothing (S0 recommended first). **Findings:** S1-01…S1-06 (exclusion only), S1-14, S1-20, S1-21.

### S1a. Engine seed policy

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/base.py` · `BaseChallengeEngine` | Add `seed_policy: ClassVar[Literal["none", "free", "derived", "pair"]] = "free"` and `async def seed_candidates(self, rules: dict) -> list[int] \| None` (default `None` = unrestricted). **It must not call TMDB**: cached rows only. |
| 2 | `engines/march_madness.py`, `rt_split.py`, `trackers.py` (`RouletteEngine`), `method_actor.py`, `auteur_marathon.py` | `seed_policy = "none"`. On track modes the start is chosen on the board (the first film must be within `max_skip + 1` of the track start, already enforced by `_check`). |
| 3 | `engines/meet_in_middle.py` | `seed_policy = "pair"`. |
| 4 | `engines/regional_deep_dive.py` | `seed_policy = "derived"`. Extract the slicing loop in `prepare_run` into a pure `_slice(rows, country, decade)` and a sync `_slice_ids(curated_id, country, decade)` over the **already-cached** badge × `CachedMovie` join. `seed_candidates` returns `_slice_ids(...)`. `prepare_run` reuses `_slice` (same predicate, so they can't drift). |
| 5 | `engines/canon_island.py` | `seed_policy = "derived"`; `seed_candidates` = the list's badge `movie_id`s. |
| 6 | `engines/trackers.py` · `DecadeSieveEngine` | `seed_policy = "derived"`; `seed_candidates` = cached ids whose `release_date` falls in `target_decade` (SQL `LIKE '197%'`-style range on `release_date`). |
| 7 | `backend/app/api/routes_engine.py` · `EngineMeta`, `list_engines` | Add `seed_policy`. |

### S1b. Rules-aware dice

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/services/seed_suggestions.py` · `suggest_seed` | New keyword arguments `allowed_ids: set[int] \| None` and `min_runtime: int \| None`. When `allowed_ids` is set, both pools are intersected with it, plus a third pool, `allowed_ids` itself (labelled e.g. "On Sight & Sound #12"), so a niche slice never comes back empty. Respect `min_runtime` when the runtime is known. |
| 2 | `backend/app/api/routes_movies.py` | Add `POST /movies/seed-suggestion` with body `{game_type, rules_config, exclude: list[int]}`. It runs `strip_server_rules`, `engine.validate_rules_config` (422 on problems) and `seed_candidates` (no `prepare_run`), then `suggest_seed`. The new response is `{suggestion: film \| null, reason: string \| null}` (a literal JSON null cannot carry a reason); `none` returns a null suggestion and board-start reason without preparing its board. Keep the GET's existing film/null shape; optional JSON `rules_config` lets derived-mode legacy callers supply their bounds. Add cache-only `POST /movies/seed-options` returning `{seed_policy, allowed_ids, reason}` so manual candidates can be rejected before detail hydration. |
| 3 | `backend/app/api/routes_runs.py` · `create_run` | (a) `seed_policy == "none"` and a seed sent → 422 "`<mode>` doesn't use a seed film". (b) When `seed_candidates(rules)` is not `None`, validate seed membership **before** `prepare_run` (fail fast, without the 90 s hydration). (c) The existing post-prepare `validate_candidate` stays as the authoritative check. |
| 4 | `frontend/src/components/SeedMoviePicker.tsx` | New props: `rules: RulesConfig` and `excludeIds?: number[]`. Use the POST endpoint and reset `seen` when `JSON.stringify(rules-subset)` changes. Show the server's `reason`. |
| 5 | `frontend/src/components/run-creator/Step2RunSetup.tsx`, `HeroSeedPreview.tsx`, `useRunDraft.ts` | Render the seed column from `mode.seed_policy`: `none` → a one-line note ("This mode starts on its board"); `derived` → the label "Seed (from your slice)"; `pair` → both pickers share `excludeIds`. `useRunDraft` omits `seed_movie_id` when the policy is `none`. Replace `TRACKER_MODES`-based seed decisions with the policy. |

### S1c. Regional slices that can't be empty

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/api/routes_curated.py` | Add `GET /curated-lists/{id}/slices` → `{hydrated: int, total: int, countries: {code: count}, decades: {decade: count}, pairs: {"JP:1970": count}, indexing: bool, indexing_error: string \| null}`, computed from cached rows via `RegionalDeepDiveEngine._slice`'s predicate. The indexing fields stop polling when ephemeral work completes/fails and expose retryable failures. The existing `/curated/lists/{id}/slices` path is an alias. |
| 2 | `backend/app/api/routes_curated.py` (`sync_curated_list`, and `update_curated_list` when `is_enabled` flips to true) | After a list is enabled or synced, `task_runner.submit_task("canon_hydrate", …)` hydrates missing details in batches (same `fetch_with_backoff` path as `_hydrate`), so Create rarely needs the 90 s inline hydration. Reuses the existing `SystemTask` machinery; no new infrastructure. |
| 3 | `frontend/src/components/run-creator/mode-config/DiveConfig.tsx` | Fetch the slices (new `useCuratedSlices` in `lib/queries.ts`). Options show counts ("🇯🇵 Japan · 14"), zero-count options are disabled, the decade list narrows to the chosen country (and vice versa), "Indexing N/M films…" shows while hydration runs, and **"🎲 Surprise me"** picks a random pair, weighted towards 5–25 films. |

**Tests**: `tests/test_seed_policy.py` (new)
- every engine in `ENGINE_REGISTRY` declares a valid `seed_policy`;
- Regional `JP` slice: 30 rolls return only JP checklist films; the AU slice never returns IN/US;
- Decade Sieve 1970 returns only 1970s films; Canon Island only list films;
- `POST /runs` with a seed on March Madness/RT Split/Roulette/Method Actor/Auteur → 422 with the mode name;
- a bad Regional seed → 422 **without** `prepare_run` being awaited (monkeypatch it to raise if called);
- Meet in the Middle: `exclude` from both pickers is honoured.

`tests/test_curated_api.py`: slice counts match `prepare_run`'s output for the same list, and the hydrate task enqueues once per enable.

**Acceptance (live, scratch CONFIG_DIR):** a Regional Deep Dive on a synced list with
"Australia" set: 10 dice rolls, all Australian; an empty slice can't be selected.
**Commit:** `feat(setup): engine seed policies, rules-aware seed dice and non-empty regional slices`

---

## Phase S2: Ratings & data resilience (no mid-run dead ends)

**Goal:** a missing or failed third-party fact never stalls a game.
**Depends on:** S0 (the `force` parameter, transient classification). **Findings:** S1-08, S1-09, S1-12, S1-13, S1-17.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/models/cache.py` · `CachedMovie` | Add `imdb_id: str \| None = None`. **Migration** `<rev>_cached_movie_imdb_id` (nullable column, no backfill). |
| 2 | `backend/app/services/tmdb.py` (`TMDBMovie`, the detail normaliser), `cache_repo.CacheRepo.upsert_movie` | Carry `imdb_id` from `/movie/{id}`; never overwrite a known id with `None`. |
| 3 | `backend/app/integrations/omdb.py` | `lookup_by_imdb_id(imdb_id)` (`?i=`), with the same transient classification as S0. |
| 4 | `cache_repo.get_movie_ratings` | Prefer `imdb_id` (re-fetch the detail with `require_detail` if it's missing and the row is a stub); fall back to title+year. |
| 5 | `backend/app/api/routes_split.py` | `POST /runs/{id}/split/ratings/{movie_id}/retry` → `get_movie_ratings(..., force=True)`, returning `SplitCandidate \| {qualifies: false, reason}`. |
| 6 | `backend/app/schemas/runs.py` · `RunStepCreate`; `routes_runs._log_step`; `engines/rt_split.py` | Add `no_contest: bool = False`, valid only on `rt_split`. A no-contest step skips `validate_candidate`'s *scores* checks, requires no `household_score`, is stamped `transition_metadata["split_no_contest"] = True` and **never** gets `point_to`. Add `split_no_contest` to `SERVER_OWNED_METADATA`. |
| 7 | `frontend/src/components/SplitBoard.tsx` | Add a "We watched something else" search (`MovieSearchAutocomplete` in a picker-only mode). If the film qualifies, it opens `HouseholdRatingModal`. Otherwise it shows the reason plus **"Retry ratings"** and **"Log as no-contest"**. Pool cards gain a ↻ retry for films whose scores later fail. |
| 8 | `backend/app/api/routes_movies.py` · `get_movie` | Add the query param `refresh_ratings: bool`. `frontend/src/components/RatingBadges.tsx`: when the ratings object exists but is all-null, show a muted "No critic scores · Retry" chip instead of nothing. |
| 9 | `backend/app/utils/countries.py` (**new**) | Move `passport.parse_country_codes` here as the canonical parser. `bridge_paths.parse_countries`, `modifiers.step_country` and `passport` delegate to it. |
| 10 | `schemas/movies.py` `MovieSummary`, `schemas/discovery.py` `DiscoveryCandidate`, `schemas/engine.py` (both country fields), `schemas/runs.py` step out | Add a computed `origin_countries: list[str]` (and `movie_origin_countries` on steps). Keep the legacy string fields for one release. |
| 11 | `frontend/src/components/CountryFlags.tsx` (**new**) | `<CountryFlags codes max={2} />`: flag + `countryName`, with a "+N" overflow. Use it in `HeroSeedPreview`, `PickNextHub`, `ChainTimeline`, `MovieDetailModal`, `MoviePreviewModal`, `ChainLink`, `RunStatsSection` and `ExpeditionBoard`, reading `origin_countries` (falling back to `parseOriginCountries`). |

**Tests**
- `tests/test_omdb_client.py`: lookup by id.
- `tests/test_cache_repo.py`: `imdb_id` is preferred, kept on re-upsert, and title fallback works.
- `tests/test_bounty_split.py`:
  - a no-contest step scores nothing and is excluded from `compute_scores`;
  - `no_contest` on a non-split run → 422;
  - forging `split_no_contest`/`point_to` is stripped;
  - the retry endpoint re-queries (spy) and returns the qualification.
- `tests/test_movies_api.py`: `origin_countries` is a list on summary, detail, discovery and steps; a legacy `"US, GB"` row → `["US", "GB"]`.
- **Migration:** a single Alembic head.

**Acceptance:** with OMDb unreachable, an RT Split run can still log a watched film (as no-contest) and continue.
**Commit:** `feat(data): IMDb-id ratings lookup, split no-contest escape hatch and list-typed origin countries`

---

## Phase S3: Rulebooks & "How to Play"

**Goal:** every mode and overlay explains its goal, turn, scoring and strategy in place.
**Depends on:** nothing. Overlay fragments for future modifiers arrive with S9 (the guard test enforces them).
**Findings:** S4-06 (copy), S4-11, S4-12, S4-13.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/rulebook.py` (**new**) | `RuleSection(goal, turn, scoring, lose, tips, glossary)`, `GLOSSARY` (wildcard, life, seed, tier, bounty, raid, build, bank/anchor, streak, sudden death, veto, fork, checklist, track, no-contest), and `render(section, values) -> RuleSection` (`str.format_map` with a strict mapping; an unresolved key raises in tests). |
| 2 | every engine class in `ENGINE_REGISTRY` | `rulebook: ClassVar[RuleSection]` with placeholders (`{target_lead}`, `{max_lives}`, `{target_points}`, `{max_skip}`…) and `rulebook_values(rules) -> dict`. **Tips must state the strategic levers** (e.g. Tug: "Your pick sets your opponent's options"). |
| 3 | overlays: `services/bounties.py`, `engines/chaos.py`, `engines/modifiers.py` (per key), `services/blind_fork.py`, `services/veto.py` | A module-level `RULEBOOK: RuleSection` fragment each. |
| 4 | `backend/app/api/routes_engine.py` | `EngineMeta.rulebook` (rendered with engine defaults), `tagline`, `tags` (moved from the frontend, S4-12). Add `GET /runs/{id}/rulebook` → the engine rulebook rendered with the run's rules plus a fragment for every active overlay. |
| 5 | `frontend/src/components/HowToPlay.tsx` (**new**) | `HowToPlayCard` (Goal · Your turn · How to win, three lines), `HowToPlayDrawer` (full rulebook plus a "This run" tab listing the active settings, which replaces `RulesSummaryCard` for trackers), and `GlossaryChip` (a `Popover` with a glossary entry). |
| 6 | `frontend/src/components/GameModePicker.tsx` | A "?" **sibling** button on each card (not nested inside the card's button, per the Phase 3 lesson) → `HowToPlayCard` popover. |
| 7 | `frontend/src/pages/RunDetailPage.tsx` | "📖 How to play" in the header → drawer. On first visit for each `game_type` (`localStorage["cinechain.rulebook.seen.<type>"]`), auto-show the collapsed card. |
| 8 | `frontend/src/lib/tugOfWar.ts`, `PickNextHub.tsx`, `TugOfWarMeter.tsx` | Effect labels become `GlossaryChip`s with verb copy: "⚔️ Raid (+n you · −n them)", "⚓ Bank (next pull ×2)", "🔥 Build". |
| 9 | `frontend/src/lib/gameModes.ts` | `GAME_MODE_STYLES` keeps visuals only. Text comes from `EngineMeta`, with the old strings as a fallback until removed. |

**Tests**: `tests/test_rulebook.py` (new): every engine and overlay has non-empty `goal`, `turn`
and `scoring`; rendering with defaults *and* non-default rules leaves no `{…}`; every glossary key
referenced exists; `GET /runs/{id}/rulebook` includes the Bounty Board fragment only when the
board is on.

**Acceptance:** each of the 20 mode cards shows a "?" with three lines; each run page has a drawer that quotes the run's real numbers.
**Commit:** `feat(onboarding): server-sourced rulebooks, How to Play drawer and glossary chips`

---

## Phase S4: Pick Next decision aids & mode filters

**Goal:** turn a 1,000-card pool into a few clear choices, with filters that fit the mode.
**Depends on:** nothing (S3's glossary chips are optional). **Findings:** S1-16, S2-14…S2-18.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/schemas/engine.py` | `FilterSpec(key, kind: Literal["select", "toggle", "range"], label, source: Literal[...], default, server_param, help)`. `source` is an allow-listed enum (`origin_country`, `release_year`, `narrative_year`, `runtime`, `genre_ids`, `tug_effect`, `tier_compliant`, `new_country`), **never an expression**; `server_param` allows only `include_off_tier` or null. |
| 2 | engines | `discovery_filters: ClassVar[list[FilterSpec]]`: World Passport (`origin_country` select, `new_country` toggle, default on once ≥ 3 stamps), Chrono (`release_year`) / Time-Travel (`narrative_year`) ranges bounded by their respective frontier year, Genre Pendulum (`genre_ids` toggle "Target genre only", default on), Tug (`tug_effect` select: Build/Raid/Bank, Build by default), Rabbit Hole (`include_off_tier` toggle → `server_param`). Base default: `[]`. |
| 3 | `backend/app/api/routes_runs.py` · `discover_next_movies`; `engines/rabbit_hole.py` | `include_off_tier: bool = False`. When true, skip the tier pre-filter, mark candidates with `tier_compliant`, and let the client label off-tier cards "−1 ❤️". |
| 4 | `backend/app/api/routes_engine.py` | `EngineMeta.discovery_filters`. |
| 5 | `frontend/src/components/pick-next/ModeFilterBar.tsx` (**new**) | Renders the specs. Select options are built from the **current pool** with counts; `new_country` derives visited countries from `run.steps` (`origin_countries`); locked cooldown countries are disabled with "Cooling down (n more)". Unknown data is shown with a `?` chip, never hidden (S1-16, S2-18). |
| 6 | `frontend/src/components/pick-next/DirectorsPicks.tsx` (**new**) | A strip of three pinned cards above the grid: *Best match*, *Best for the mode* (tug points / tier-compliant / new stamp / biggest semantic score), *Underdog*. Also **"🎲 Pick for me"** over the *filtered* list, and an optional `slot` prop that S6 reuses for the Tug triad. |
| 7 | `frontend/src/components/PickNextHub.tsx` · `DiscoveryGrid` | Mount both components. Remove the `craftMode` and `tugMode` filter branches that the specs replace (trope chips stay). The count line reads "Showing 48 of N best matches · narrow with a filter". |
| 8 | `frontend/src/lib/queries.ts`; `PickNextHub` | Wire `GET /runs/{id}/suggestions` as **"Search further in 🇯🇵 Japan / 1970s"** when a country/decade filter empties the pool (S2-17). Standalone modes search one TMDB page with no fabricated cast link; `Suggestion` adds discovery fields and nullable connector fields. Re-check up to 20 suggestions with full run history and current rules under a 20-second request deadline. Preserve active filters and show provider/timeout errors explicitly. |

**Implementation corrections:** Historical Time-Travel uses story-setting years, not release years; `narrative_year` is therefore an additional safe source. Standalone `MutatorEngine.get_suggestions` previously returned `[]`, so wiring the endpoint alone would not implement the intended search. Suggestions now carry real discovery metadata and nullable connector fields for standalone films, while cast-linked results retain their connectors. Crew/person and trope controls still reflect actual capabilities; only the bespoke Tug default decade/filter branch is replaced by engine specs. Paging remains explicit (48 grid cards plus up to three pinned picks), with no automatic endless-scroll expansion. Keep the discovery grid mounted while drilling into random/recommended films so Back preserves the filtered choice context.

**Tests**
- `tests/test_game_modes.py`: every `discovery_filters` source is in the allow-list.
- `tests/test_rabbit_hole.py`: `include_off_tier` returns off-tier films flagged `tier_compliant=False` and still costs a life when logged.
- `tests/test_graph_mutators.py`: `/suggestions?country=JP` on World Passport returns JP films.
- Frontend: `npm run build`. Live check: in a World Passport run, the Country filter lists only pool countries with counts, "New stamps only" hides visited countries, and "Pick for me" respects filters.

**Commit:** `feat(pick-next): engine-declared mode filters, director's picks and pick-for-me`

---

## Phase S5: Rule schemas, mode presets & marathon length

**Goal:** a one-tap, mode-specific difficulty choice everywhere, and marathons of the length the player wants.
**Depends on:** nothing. (S9 later adds modifier add-ons to the same picker.) **Findings:** S2-01…S2-03, S3-12, S3-13.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/schemas/engine.py` | `RuleField(key, kind: int\|bool\|enum\|segmented, label, help, min, max, options, default, group)`, `Preset(id, label, emoji, blurb, values)`. |
| 2 | `backend/app/engines/base.py` | `rule_fields: ClassVar[list[RuleField]]`, `presets: ClassVar[list[Preset]]`, `default_preset: ClassVar[str]`. `validate_rules_config` first validates the keys present against `rule_fields` (type/min/max/options), then the engine's custom checks. Graph modes inherit the cast-chain fields and Standard/Purist/Casual (moved from `RulesetFields.RULE_PRESETS`). |
| 3 | `engines/rabbit_hole.py` | Presets *Tourist* (`max_lives` 5), *Spelunker* (3, default), *Ironman* (1, re-rolls off via a new input `allow_reroll: false`, checked in `reroll_rabbit_hole_tier`). |
| 4 | `engines/tug_of_war.py` | Presets *Friendly* (`target_lead` 5), *Rivalry* (7 + sudden death), *Blood Feud* (9, no sudden death). `sudden_death_after` is validated to 4–50 today, so add an explicit boolean input `sudden_death_enabled` (default true) rather than overloading 0. |
| 5 | `engines/method_actor.py`, `auteur_marathon.py` | New inputs `track_length: Literal["milestones", "short", "feature", "full", "endless"]` (default `"feature"`) and `order: Literal["strict", "relaxed", "free"]` (maps to `max_skip` 0 / 2 / `None`). `build_career_track(..., length=)` slices `chosen` (`milestones` = milestone films only, min 3; `short` 6; `feature` 12; `full` 25; `endless` ≤ 60). `MIN_TRACK_FILMS = 3`. `_check` treats `max_skip is None` as free order (still on-track). Presets *Taster* (milestones, free), *Biopic* (feature, relaxed), *Completist* (full, strict). Unify the default `max_skip` to 2 for both and show it in the UI. |
| 6 | `backend/app/api/routes_runs.py` | `POST /runs/{id}/wrap`, only for track modes with `track_length == "endless"`: completes the run with "Marathon wrapped: N of M films (P%)". |
| 7 | `backend/app/api/routes_engine.py` | `EngineMeta.rule_fields`, `presets`, `default_preset`. |
| 8 | `frontend/src/components/RulesetFields.tsx` | A generic renderer: preset pills from the engine (the default is pre-selected) and fields grouped as core/advanced. Shown for **all** modes, trackers included. Delete `RULE_PRESETS`. |
| 9 | `frontend/src/components/run-creator/Step2RunSetup.tsx`, `mode-config/ActorConfig.tsx` | Drop the `!isTracker` gate around `RulesetFields`. The actor/director pickers stay in the mode config. |

**Tests**
- `tests/test_rule_schemas.py` (new): every preset validates against its engine; an out-of-range field → 422; unknown keys still pass through (the JSON-rules path is preserved).
- `tests/test_method_actor.py`: each `track_length` size, `milestones` ≥ 3, free order accepts going backwards on-track, `endless` + wrap completes, wrap is refused on non-endless runs.
- `tests/test_rabbit_hole.py`: Ironman refuses a re-roll.
- `tests/test_tug_momentum.py`: Blood Feud (`sudden_death_enabled=false`) never shrinks the target.

**Commit:** `feat(rules): engine rule schemas, mode-flavoured presets and flexible marathon length`

**S5 implementation clarifications:** Auteur Marathon had no milestone/ranking data. Its length
selection reuses the deterministic career milestones with director credits treated as leading
work, after the existing director/runtime eligibility checks. When milestone tags collapse onto
fewer than three films, pad with ranked eligible features (never fabricate milestones); genuinely
short filmographies remain usable. Free-order tracks complete only when all track films are
watched, not when the last chronological entry is picked first. Endless tracks never auto-complete
on their last entry; wrapping counts distinct watched on-track films, excluding queued entries.
Track length and starting lives are creation-only in the edit form/API, avoiding filmography
rebuilding or survival-budget resets mid-run. Legacy stored numeric skip limits remain authoritative
unless a player explicitly chooses the new order control.

---

## Phase S6: Tug of War v3 & strategic legibility

**Goal:** make the versus maths match the story, with skill (not parity) deciding games.
**Depends on:** S0 (preview fix). Uses S4's `DirectorsPicks` slot if present. **Findings:** S4-02…S4-05.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/tug_of_war.py` | `TUG_RULES_VERSION_KEY = 3` for **new** runs (`prepare_rules_config`). A new `tally_v3` with a rope model (`rope += delta`, no clamp; `scores` derived as cumulative gains for display). **Build** (territory = puller): `+own_streak` (cap `momentum_cap`). **Raid**: `+2`, and resets the defender's streak. **Bank** (neutral): 0 now, ×2 on your next pull, resets your own streak. Per-team `streaks: {team_a, team_b}`. **Sudden Death:** the target shrinks only after Team B's pull (round boundary), and from the first sudden-death round the **trailing** team pulls first (`next_team`). `preview_pull_v3` matches. `compute_scores`/`winner` dispatch by version; v1/v2 are untouched. **Caution:** `compute_scores` currently tests `rules.get(TUG_RULES_VERSION_KEY) != 2` → v1, so a v3 run would silently fall into the v1 fold. Rewrite it as an explicit `{None/1: v1, 2: v2, 3: v3}` dispatch and test all three. |
| 2 | `schemas/discovery.py`; `TugOfWarEngine.discover_candidates` | Add `tug_breaks_streak: bool` on candidates. |
| 3 | `backend/app/api/routes_runs.py` | `GET /runs/{id}/tug/lookahead?movie_ids=…` (≤ 10 ids): for each candidate, counts of scoring vs neutral films reachable from it via **cached** cast/credits only. Time-boxed at 1.5 s total, so a partial result is labelled partial. |
| 4 | server-owned | `tug_momentum` already server-owned. If a `tug_rope` cache is added, register it. |
| 5 | `frontend/src/components/TugOfWarMeter.tsx`, `lib/tugOfWar.ts` | Show both teams' 🔥 streaks and the round counter, plus "Trailing team pulls first" in Sudden Death. |
| 6 | `frontend/src/components/PickNextHub.tsx` | **Decision triad** in the picks slot: *Best Build*, *Best Raid (breaks 🔥n)*, *Bank*, each with its realised delta. Show "Leaves them: x scoring · y neutral" on hover (lookahead). |

**Tests**: `tests/test_tug_v3.py` (new)
- per-team streaks reach the cap under alternation;
- a raid resets the defender's streak;
- bank doubling;
- the rope has no clamp;
- **parity property:** over 2,000 seeded random mirror-strategy games (both teams draw from the same distribution of Build/Raid/Bank), Team A's win rate is within 45–55%. Under v2 the same harness shows the bias; keep that as a documented `xfail` for v2;
- the termination bound holds for every simulated game;
- v1 and v2 runs keep their exact historical totals;
- lookahead respects the time budget (monkeypatched slow loader).

**Commit:** `feat(tug): v3 per-team momentum, raids break streaks, fair sudden death and decision triad`

**S6 implementation clarifications:** "After Team B" only identifies the boundary while A
pulls first; trailing-first Sudden Death can put B first. A v3 round therefore completes after
both teams pull, and victory/target shrinkage settle only then (solo runs settle each pull).
Tied Sudden Death rounds alternate initiative. Banks remain zero-point pulls as specified,
including Sudden Death; perpetual banking can draw, so the termination assertion covers each
seeded simulated game, not every possible strategy. The 2,000-game uniform harness produces
48.3% A wins in v3 and 51.75% in v2: uniform draws alone do not demonstrate the old bias.
The additional identical bank-heavy distribution (Build/Raid/Bank weights 1/1/30) yields
47.95% in v3 versus 62.1% in v2, retained as a strict v2 `xfail`; every simulated game
terminates within 500 pulls. Lookahead is a bounded cache-only reachability estimate, not a
provider search or a promise of legal logging; missing cache/metadata and deadline exhaustion
are explicitly partial. Queued v3 pulls enter fold chronology when watched, not when queued.

---

## Phase S7: Table Mode (one device, many players)

**Goal:** every multiplayer mechanic works when one logged-in device is passed around.
**Depends on:** nothing (S6 makes the Tug turn validation stricter). **Findings:** S4-07…S4-10.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/api/routes_runs.py` · `create_run` | Accept a boolean `rules_config["table_mode"]` (a player input, so **not** server-owned; validated as a real bool). |
| 2 | `routes_runs.py` (new helper `_acting_user(session, run, current_user, acting_participant_id) -> User`) | Returns `current_user` when `acting_participant_id` is absent. Otherwise it requires `table_mode`, the caller being a participant and the acting id being a participant; else 403. |
| 3 | `schemas/runs.py` (`RunStepCreate`, `ForkOffer`, `ForkVeto`, `ForkAccept`, `GoldenVeto`, `MarkWatchedRequest`, `RunStepUpdate`), `routes_bracket.py` (`advance_bracket`/`vote_in_bracket` payloads) | An optional `acting_participant_id` field each; fork withdrawal accepts it as a query parameter. Watching a queued film requires its original acting participant and preserves that identity. |
| 4 | `_log_step`, `offer_fork`, `veto_fork_movie`, `accept_fork_movie`, `withdraw_fork`, `_require_partner_of_offer`, `golden_veto`, `routes_bracket.advance_bracket`/`vote_in_bracket` | Use `_acting_user(...)` for **game identity** (offerer/partner checks, team lookup, `consume_veto_token(acting_user)`, bracket votes). `logged_by_user_id` stays `current_user.id` (Rule 9). Stamp `transition_metadata["acting_participant_id"]`. |
| 5 | Tug | In Table Mode the server derives direct-pull `tug_team` from the acting participant's team and returns 409 when it isn't `next_team`, so turn validation becomes real. Blind Fork preserves the existing offerer-owned pull: the accepting partner is stamped as actor, while an internal-only override scores the offerer's team. |
| 6 | `SERVER_OWNED_METADATA` | Add `acting_participant_id`. |
| 7 | `frontend/src/lib/tableMode.ts` (**new**) | The seat state per run (`sessionStorage`) and an `actingFields()` helper merged into every mutation payload in `lib/queries.ts`. |
| 8 | `frontend/src/components/TableSeat.tsx` (**new**), `HandoverInterstitial.tsx` (**new**) | A "🎮 Ana's turn" pill in the run header and a full-screen "Pass to Ben → I'm Ben" interstitial, which **hides a pending Blind Fork offer** until it's tapped. The seat auto-advances for Tug (`next_team`) and Blind Fork (offer → partner → back). |
| 9 | `ParticipantPicker.tsx` | A "📱 One device, many players" toggle when ≥ 2 participants. **Implementation clarification:** the current user model has no household relationship; keep an explicit, default-off opt-in rather than infer one or add an unplanned migration. Automatic household defaults require that future relationship. |
| 10 | `Toast` usage in `PickNextHub`, `MovieSearchAutocomplete` | "Logged for **Ana** (Team A) · +1 rope" with a 10 s **Undo** (existing delete), delivered by shared mutation notifications to `TableSeat`. For a Tug fork pick, distinguish chooser and scored player ("Picked by Ben for Ana (Team A)"). |
| 11 | `BracketView.tsx` | In Table Mode, a per-participant vote row ("Ana: A · Ben: B") that submits acting votes. |
| 12 | `schemas/auth.py`, `api/routes_users.py` | Add the veto balance to user summaries and refresh it lazily so each seat sees its own available token, not the authenticated device owner's balance. |

**Tests**: `tests/test_table_mode.py` (new)
- `acting_participant_id` without Table Mode → 403; a non-participant acting id → 403;
- a full Blind Fork offer/veto/pick flow on **one** account;
- a Golden Veto of the partner's step on one account consumes the *partner's* token;
- bracket majority reached by acting votes from one account;
- a forged `acting_participant_id` in client metadata is stripped;
- `logged_by_user_id` is always the authenticated account (Passport unchanged);
- an out-of-turn Tug acting pull → 409.

**Completed validation:** 1159 backend tests passed, 1 intentional legacy v2-bias xfail;
Ruff and TypeScript/Vite build passed. Deterministic scratch-browser checks verified
setup opt-in, seat persistence, handover privacy across reload, one-login fork/veto flows,
actor/logger separation, Tug fork attribution, bracket ties/majorities, 10-second Undo
and mobile bounds. No live provider or configured-service calls were made.

**Commit:** `feat(table-mode): hot-seat acting participant for steps, forks, vetoes and bracket votes`

---

## Phase S8: Predicate registry, feasibility & fair bounties/chaos

**Goal:** no random draw (bounty, chaos, re-roll) can be impossible, trivial, or pay in a currency the mode can't spend.
**Depends on:** nothing. S10 and S11 build on it. **Findings:** S2-08, S2-11 (shared guard), S3-01…S3-07.

### S8a. Behaviour-neutral extraction

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/predicates.py` (**new**) | `Predicate(id, label, emoji, check(movie, facts) -> bool \| None, needs: frozenset[str], difficulty: int, params)`. Port: `year_lt`, `runtime_lt`, `runtime_gt`, `runtime_le`, `runtime_ge`, `non_english`, `non_us_non_english`, `rating_lt` (via `rating_of`), `popularity_lt`, `female_director`. |
| 2 | `services/bounties.py`, `engines/chaos.py`, `engines/rabbit_hole.py` · `compliance` | Re-express the catalogues as predicate ids + params. **All existing tests stay green unchanged.** |

### S8b. Feasibility and fair draws

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/services/feasibility.py` (**new**) | `pass_rate(session, predicate, ids)`, `exists(...)`, and `cache_pass_rate(session, predicate)` (memoised per request). Unknown data counts as passable. |
| 2 | `engines/base.py` | `bounty_reward: ClassVar[Literal["wildcard", "life", "hint", "star"]] = "wildcard"`; `def bounty_feasible(self, rules, history, bounty) -> Feasibility`. Regional, Method Actor and Auteur: exact over the remaining checklist/track. Decade Sieve: open pool gated by its decade bounds. Graph modes: cache pass-rate gated by engine bounds (chrono direction × frontier year, the Rabbit Hole tier). |
| 3 | rewards | Regional / Decade Sieve / RT Split / Roulette: `bounty_reward = "star"` → `rules_config["bounty_stars"]` (server-owned), added to the victory text. Roulette also has no spendable soft-violation wildcard. Meet in the Middle: `"hint"` → `tunnel_hints_remaining += 1`. Rabbit Hole stays `"life"`. Implement in `award_bounty` and the matching revoke path; preserve unmarked legacy-step undo semantics. |
| 4 | `services/bounties.py` · `prepare_board`, `draw_replacement` | Take a `feasible: Callable[[str], bool]`. Exclude infeasible draws and known-trivial ones (pass-rate > 0.8). Keep a smaller board if none qualify: the no-trivial-draw goal takes precedence over filling all three slots. Unknown facts count as possible, but cannot prove triviality. |
| 5 | `routes_runs._log_step` (after award) | Re-check active bounties. An impossible one is retired (`transition_metadata["bounty_expired"] = [ids]`, server-owned) and replaced; deleting the step reverses it (the same pattern as `completed_bounty`). |
| 6 | `POST /runs/{id}/bounties/{bounty_id}/discard` | One free discard per run (`bounty_discards_left`, server-owned, default 1). |
| 7 | `bounties.generate_custom_bounty` | Add the mode context line (mode, bounds, tier, checklist summary) to the prompt. Accept only if drawable; retry once, then fall back to a static feasible, nontrivial bounty with an explicit UI note. If no fallback qualifies, return 503 without mutating the board. |
| 8 | `engines/chaos.py` · `roll`; `routes_runs.roll_chaos` | Roll from feasible, nontrivial handicaps against the server-recomputed current pool (the existing discover path and hydration budget). Skipped handicaps are noted in the response. Before a frontier exists, respect engine seed-candidate constraints. No fair handicap means 409 without mutation. Paid Rabbit rerolls check the widened reachable pool before spending a life and return 409 without spending if no alternative qualifies. |
| 9 | Frontend `BountyBoardPanel.tsx`, `lib/bounties.ts` | Reward chip per `bounty_reward` (🎟️ / ❤️ / 💡 / ⭐), an "Expired" toast with the reason, and a Discard button. Expiry appears above Table Mode attribution/Undo, and Undo clears stale completion/expiry feedback. `ChaosBanner` shows the skipped-handicap note. |

**Tests**: `tests/test_bounty_feasibility.py` (new)
- Decade Sieve 2010: Time Capsule is never dealt (200 seeded draws);
- Regional JP×1970s never deals Time Capsule and never deals Foreign Horizon (trivial);
- a Chrono Climb past 1960 expires Time Capsule, and undo restores it;
- star / hint / life rewards apply, and undo reverses them;
- discard once, then 409;
- forged `bounty_stars`, `bounty_discards_left` and `bounty_expired` are stripped;
- an AI bounty failing feasibility is rejected (stubbed LLM).

`tests/test_chaos_chaser.py`: a Rabbit Hole Tier 4 frontier never rolls The Long Haul.

**Commits:** `refactor(engines): shared predicate registry for bounties, chaos and tiers`, then `feat(bounties): feasibility-checked draws, mode-fit rewards, expiry and discard`

---

## Phase S9: Composable modifier registry + A–Z / Number in Title / Ascending Numbers

**Goal:** new challenges become overlays on any compatible game rather than new modes.
**Depends on:** S8a (predicates) is recommended; S3 (the rulebook guard requires fragments). **Findings:** S3-08…S3-11, S3-14.

### S9a. Behaviour-neutral port

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/modifier_registry.py` (**new**) | The `ModifierSpec` protocol (audit §3.4): `key`, `label`, `emoji`, `blurb`, `scope`, `needs`, `params` (pydantic), `check`, `progress`, `outcome`, `compatible`, `rulebook`. |
| 2 | port | `chrono_direction`, `runtime_staircase`, `country_cooldown` and `require_cast_link` become registry entries. `modifiers.merge_modifiers` reads both the legacy flat keys and the new `rules_config["modifiers"] = [{key, params}]`. |
| 3 | `engines/base.py` | `modifier_violation`, `_modifiers_need_detail`, `filter_by_modifiers` and `describe_run_constraint` iterate the registry. `supports_modifiers: bool` becomes `modifier_scopes: frozenset[str]` (graph: all scopes; checklist trackers: `{"film", "sequence"}`; track trackers and RT Split: `{"film"}`; March Madness/Roulette: none). `modifiers_requested` checks scope **and** `compatible()`. |
| 4 | `test_composable_modifiers.py` | Stays green **unchanged**. |

### S9b. New overlays

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/utils/title_tokens.py` (**new**) | `first_letter(title, ignore_articles)` (NFKD, accents stripped, leading digit → `#`), `title_numbers(title, allow_years) -> list[int]` (digit runs, number words, ordinals, standalone uppercase Romans II–XX). |
| 2 | registry | `alphabet_run` (sequence; params `direction`, `ignore_articles`, `wild_letters`, `strict`, `seed_sets_start`; `outcome` = Z/A reached), `number_in_title` (film; `allow_years`), `ascending_numbers` (sequence; `mode: increasing \| count_up`, `start`, `target`, `allow_years`; `outcome` = target reached). Progress is **folded from history** every request (nothing stored). |
| 3 | `engines/base.py` · `evaluate_run_outcome` | Compose overlay outcomes (the first to fire wins; the engine's own outcome takes precedence). |
| 4 | creation-time feasibility | Checklist trackers: exact (e.g. strict A–Z on a 12-film slice is rejected with the reason). Graph modes: cache coverage of each letter/number via `feasibility.cache_pass_rate`. |
| 5 | Pick Next | Candidates gain `overlay_ok: dict[str, bool \| None]`. When no pool film satisfies an overlay, the hub shows "No *Q* films within reach: spend a wildcard to skip Q" (pre-checked before spending). |
| 6 | `/engines` | `EngineMeta.modifiers = [{key, label, blurb, emoji, scope, params_schema, compatible, incompatible_reason}]`. |
| 7 | Frontend `ModeOptions.tsx` | A generic renderer from the metadata (incompatible rows disabled with the reason). Delete `lib/modifiers.ts` constants `availableModifiers`, `DEFAULT_COOLDOWN` and `MAX_COUNTRY_COOLDOWN`. `ModifierChips.tsx` gets overlay progress chips ("🔤 Next: F · 5/26", "🔢 Next: 4"). `RulesetFields` presets gain add-on toggles ("+ 🔤 A–Z") (S3-14). |

**Tests**
- `tests/test_title_tokens.py` (new, table-driven):
  - *The Seventh Seal* → 7; *Ocean's Eleven* → 11; *Rocky II* → 2; *10 Things I Hate About You* → 10;
  - *1917* → none unless `allow_years`; *I, Robot* → no Roman; *Mix* → no Roman; *Se7en* → 7;
  - *The Matrix* → M with `ignore_articles`; *Élite Squad* → E; *2001: A Space Odyssey* → `#`.
- `tests/test_modifier_overlays.py` (new):
  - A–Z sequence enforcement, wild letters, seed start, Z victory;
  - `strict=false` non-decreasing;
  - incompatible with Meet in the Middle / Method Actor → 422 with the reason;
  - strict A–Z on a small checklist is rejected at creation;
  - `number_in_title` filters the RT Split pool;
  - `count_up` to 5 wins;
  - legacy flat keys still work;
  - forged progress keys are ignored.
- `tests/test_rulebook.py`: every new modifier has a fragment.

**Commits:** `refactor(modifiers): registry-driven composable modifiers`, then `feat(modifiers): A-Z, number-in-title and ascending-number overlays`

**S9 implementation clarifications**
- Finite checklist coverage uses distinct-film matching after film filters; one title cannot prove coverage of several required letters/numbers. Graph coverage uses bounded cached evidence through `feasibility.pass_rate`, not exhaustive online reachability. An empty open cache is unknown, not proof of impossibility.
- A skip is explicitly confirmed on the next **watched substitute**, costs one wildcard per unreachable sequence requirement, and is rechecked against the current server-side pool before spending. Reachable requirements, Number in Title and other run rules cannot be bypassed. Queueing never spends; server-owned skip metadata advances folded progress, and deleting the substitute restores progress and refunds actual finite spending.
- Number in Title can filter a finite career checklist at creation; changing that filter afterward requires a new run because discarded track entries are no longer present.
- Modifier JSON schemas expose actual Pydantic defaults, including factory-generated Q/X/Z wild letters. Preset switches preserve selected add-ons.

---

## Phase S10: Career context milestones & semantic tagging quality

**Goal:** a career told through verifiable milestones, with no hallucinated facts, and
semantic game tags grounded in genres and measurable confidence.
**Depends on:** S5 (track length) recommended for career work; the tagging guard is independent.
**Findings:** S2-04…S2-07, S2-19.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/method_actor.py` | Pure functions over the credits already fetched: `flag_first_lead` (`order == 0`), `flag_genre_pivots` (Jaccard ≥ 0.75 vs the union of the previous 4 films' genres, *and* the next 2 films closer to the new set), `flag_comebacks` (gap ≥ 4 years), `flag_language_crossover` (first credit whose `original_language` differs from the career mode). Every flag writes `film["evidence"][milestone] = "<fact string>"`. |
| 2 | `backend/app/services/tmdb.py` | `get_movie_release_dates(tmdb_id)`. In `prepare_run`, query only the first ≤ 3 films (10 s budget; failure = skip) to set `first_theatrical` when the debut's earliest type is 1, 4 or 6. |
| 3 | `against_type` post-pass | Embed the track films' **credit overviews** (already in the raw credits) through the existing `services/embeddings.py` JIT path. Flag films whose distance from the trailing-5 centroid is ≥ μ + 2σ of the career's own distances. If embeddings are unavailable, skip silently. **No new model.** |
| 4 | eras | `rules_config["filmography"]` entries gain `era_index`. Eras split at `genre_pivot`/`against_type`/`comeback` and are named from the dominant genre ("Romance era"). Player-tagged eras live in `rules_config["career_eras"]` (user input, editable via `PATCH /rules`; **not** server-owned). |
| 5 | villain *suggestions* (optional S10b) | TMDB keywords (`get_movie_keywords`) ∩ {villain, supervillain, antagonist, serial killer, psychopath}, **and** the character name appears in the overview → a `suggestions: ["villain"]` chip the player confirms. Never auto-badged. |
| 6 | `auteur_marathon.py` | The same flags where meaningful (genre pivot, comeback, language crossover). |
| 7 | Frontend `CareerTrack.tsx`, `AuteurTrack.tsx`, `lib/careerTrack.ts` | New milestone badges (🎬 First theatrical, ⭐ First lead, 🔀 Genre pivot, 🎭 Against type, 🔁 Comeback, 🌐 Crossover), evidence in a `Popover`, era grouping (decade fallback), "No prestige peak on record (too few ratings)" lines, and a "🏷️ Tag this era" action. |

**Tests**: `tests/test_method_actor.py`
- synthetic careers: a romance run followed by a drama/social film with stubbed vectors flags `against_type` (the *Swades* pattern) while genres alone don't flag it;
- a one-off genre outlier is not flagged as a pivot (it needs 2 confirming films);
- comeback at a 4-year gap, but not at 3;
- language crossover;
- `first_theatrical` with stubbed release dates, and a skip on TMDB failure;
- every milestone carries evidence;
- `career_eras` survives `PATCH /rules` while `filmography` remains unforgeable.

**Commit:** `feat(method-actor): evidence-backed career milestones and eras`

### S10c. Semantic trope Genre Gate & Confidence Threshold

- In `backend/app/services/movie_features.py`, share one filtering path between
  `extract_and_store_tropes` and `ensure_tropes`; both currently call the existing Qwen
  `llm.extract_tropes` pipeline. Route explicit extraction and Semantic Trope Web preparation
  through the guard so no caller can store or link with unfiltered suggestions.
- **Genre Gate:** cross-reference proposed normalized tropes with `CachedMovie.genre_ids`
  using explicit, reviewed concept-to-TMDB-genre compatibility rules. Reject conceptual
  conflicts (for example, `cyberpunk` without Science Fiction on a Romance/Comedy-only film).
  Do not treat every trope as genre-exclusive; unknown/missing genres are not invented.
- **Confidence Threshold:** embed each candidate trope's concise concept description and the
  film overview using the existing JIT `embeddings.embed_batch` provider, in the same batch/model
  fingerprint. Use the existing provider-aware similarity normalization (Arctic raw cosine
  baselines are high); accept only at or above a named strict threshold calibrated with fixture
  positives and negatives, not arbitrary raw-cosine guesses. Do not compare stale/different-model
  vectors. No new model, ML dependency, daemon or vector database.
- Discard rejected tags silently (no UI warning for normal filtering); keep unavailable
  models/provider errors observable through the established logging/error contracts. Apply
  the guard to cached tags before game matching as well, without a blanket cache deletion.
- Add deterministic regression tests in the existing movie-feature/algorithm tests: a pure
  rom-com cannot retain `cyberpunk`; compatible high-confidence tags survive; low-confidence
  tags do not; equality at the threshold is covered; missing genres, mismatched fingerprints
  and unavailable providers cannot admit unverified tags; both extraction callers behave alike.

---

## Phase S11: Procedural Rabbit Hole

**Goal:** every descent feels different and is always survivable by skill.
**Depends on:** S8 (predicates + feasibility). **Findings:** S2-09, S2-10, S2-12, S2-13.

| # | File · symbol | Change |
|---|---|---|
| 1 | `backend/app/engines/rabbit_hole.py` | New runs get `rh_rules_version = 2`, `rh_seed` (server-generated int) and `tier_deck` (Tier 1 Freefall, then 4–6 predicates drawn by `random.Random(rh_seed)`, ordered by `difficulty`, with jittered params: Retro 1980/1990/2000, Micro-Clock 90/100/110, …). Each draw must pass `feasibility.cache_pass_rate ≥ 0.03`. `tier_for_depth`, `compliance` and `violation_reason` read the deck; **v1 runs keep `TIERS`**. |
| 2 | curses (input `curses: true`, "Hard mode") | From Tier 4, the previous tier's predicate persists. The combined pass-rate must be ≥ 0.01, else the curse is dropped for that tier. |
| 3 | relics | At each tier boundary the server grants one of +1 life (capped), a free re-roll token or "skip one curse" (`relics`, server-owned). Stamp `transition_metadata["relic_awarded"]` (server-owned) so undo reverses it. |
| 4 | `routes_runs.reroll_rabbit_hole_tier` | Deterministic `random.Random(f"{rh_seed}:{depth}")`. Exclude predicates that are infeasible for the current frontier's pool. Spend a free re-roll token before a life. |
| 5 | Daily Dive (input `daily: true`) | `rh_seed = int(sha256(date.isoformat()))`, the same for every household that day. |
| 6 | server-owned | `rh_seed`, `tier_deck`, `relics`, `rh_rules_version`, `reroll_tokens` → `SERVER_OWNED_RULES`; `relic_awarded` → `SERVER_OWNED_METADATA`. |
| 7 | Frontend `RabbitHoleHud.tsx`, `lib/rabbitHole.ts`, `RabbitHoleGameOver.tsx` | Render the deck's tiers (names and params), curse chips, the relic inventory and a "Daily Dive" badge. The Pick Next tier badge is unchanged. |

**Tests**: `tests/test_rabbit_hole.py`
- the same seed gives the same deck;
- every dealt tier has a pass-rate ≥ 3% on a fixture cache;
- curses stack and are dropped when infeasible;
- relics are awarded and reversed on undo;
- re-rolls are deterministic and avoid infeasible tiers;
- the Daily seed is the same for two runs on the same date;
- v1 runs are unchanged;
- forged deck/seed/relics are stripped.

**Commit:** `feat(rabbit-hole): procedural tier decks, curses, relics and daily dives`

---

## 12. Phase order & dependency graph

```
S0 ─┬─► S1 ─► (S2 uses S0)        S3 (independent)      S4 (independent)
    │                              S5 (independent) ─► S9b presets add-ons
    └─► S6 (Tug v3; uses S4 slot if present)            S7 (independent)
S8a ─► S8b ─► S11
S8a ─► S9a ─► S9b      S5 ─► S10
```

Recommended order (anti-paralysis first): **S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8 → S9 → S10 → S11**.

---

## 13. Traceability (finding → phase)

| Finding | Phase | Finding | Phase | Finding | Phase |
|---|---|---|---|---|---|
| S1-01 | S1b | S2-01 | S5 | S3-01 | S8b |
| S1-02 | S1b | S2-02 | S5 | S3-02 | S8b |
| S1-03 | S1a | S2-03 | S5 | S3-03 | S8b |
| S1-04 | S1a | S2-04 | S10 | S3-04 | S8b |
| S1-05 | S1a | S2-05 | S10 | S3-05 | S8b |
| S1-06 | S1b (exclusion); distance preference → Backlog | S2-06 | S10 | S3-06 | S8b |
| S1-07 | S0 | S2-07 | S10 | S3-07 | S8b |
| S1-08 | S2 | S2-08 | S8a | S3-08 | S9a |
| S1-09 | S2 | S2-09 | S11 | S3-09 | S9b |
| S1-10 | S0 | S2-10 | S11 | S3-10 | S9b |
| S1-11 | S0 | S2-11 | S8b (shared guard), S11 | S3-11 | S9b |
| S1-12 | S2 | S2-12 | S11 | S3-12 | S5 |
| S1-13 | S2 | S2-13 | S11 | S3-13 | S5 |
| S1-14 | S1a | S2-14 | S4 | S3-14 | S9b |
| S1-15 | S0 | S2-15 | S4 | S4-01 | S0 |
| S1-16 | S4 | S2-16 | S4 | S4-02 | S6 |
| S1-17 | S2 | S2-17 | S4 | S4-03 | S6 |
| S1-18 | S0 | S2-18 | S4 | S4-04 | S6 |
| S1-19 | S0 | | | S4-05 | S6 |
| S1-20 | S1c | | | S4-06 | S3 |
| S1-21 | S1c | | | S4-07 … S4-10 | S7 |
| | | S2-19 | S10c | S4-11 … S4-13 | S3 |

**Backlog (not scheduled):** Meet in the Middle seed pairs with BFS distance ≥ 3 (S1-06
remainder; needs a time-boxed BFS per roll); Blind Fork "offer a trap" coaching and Golden Veto
rule-in-place copy (§4.2 P2 rows: fold them into S3 copy if cheap).

**Future architecture:** custom **TVTropes Scraper Hybrid Pipeline**, deferred to a later release.
Assess source permissions/attribution, bounded respectful fetching, source provenance, genre and
confidence guards, and merging scraped evidence with the existing local Arctic/Qwen pipeline.
This is a backlog design note, not authorization to add scraper infrastructure in S0–S11.

---

## 14. Definition of done (whole programme)

- No creator path can produce a 422 that the creator could have predicted (S1 live check across all 20 modes).
- No mode can stall on missing third-party data (S2 live check with OMDb unreachable).
- Every mode card and run page has a How to Play (S3 guard test).
- Pick Next opens with ≤ 3 recommended picks plus a "Pick for me" in every pool mode (S4).
- Tug of War mirror-strategy win rate is 45–55% (S6 property test).
- Every multiplayer mechanic works on one device (S7 tests).
- No bounty, chaos roll or tier is ever impossible (S8/S11 property tests).
- `STATE.md`/`LESSONS.md` are updated after each phase.
