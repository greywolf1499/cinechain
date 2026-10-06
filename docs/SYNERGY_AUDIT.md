# CineChain Mechanics & Synergy Audit (post-v1.3.0 playtest)

**Baseline:** `master` @ `9381a85` (v1.3.0, UX Phases 0–4 + 2a–2d shipped).
**Method:** each playtest observation is traced through three lenses:
**Point** (the reported bug), **Vertical** (every other use of the same component or feature),
**Horizontal** (the same *class* of problem anywhere in the app).
**North star:** replayability, fun, and **zero decision/analysis paralysis**. A player should
never reach a dead end that the app could have predicted, and should never face a blank choice
without a sensible default.

Finding IDs: `S<step>-<nn>`. Severity: **P0** blocks play / corrupts state, **P1** misleads or
stalls the player, **P2** polish/consistency. Code references are by symbol (line numbers drift).

---

## Step 1 — Data Resilience & the Seed Picker

### 1.1 Point: the "🎲 Recommend Seed Movie" roll ignores Regional Deep Dive filters

**Trace.** `SeedMoviePicker` (`frontend/src/components/SeedMoviePicker.tsx`) calls
`GET /movies/seed-suggestion?game_type=…&exclude=…`. The route `get_seed_suggestion`
(`backend/app/api/routes_movies.py`) forwards only `game_type` and the exclusion set to
`seed_suggestions.suggest_seed` (`backend/app/services/seed_suggestions.py`).
`suggest_seed` builds a *global* pool (any canon badge, or IMDb ≥ 7.5, else the top-150 popular
cached films) and applies only `MODE_REQUIREMENTS`, a table of *data presence* checks
(`release_date`, `origin_country`, `overview`, `poster_path`) for five graph modes. It never sees
`rules_config`, so `target_country`, `target_decade`, `curated_list_id`, `actor_id` and
`director_id` aren't available to it. Picking Australia therefore can't affect the roll.

**What the player experiences.** The wizard happily shows a non-Australian hero seed. Nothing
complains until **Create**. `create_run` (`routes_runs.py`) then runs
`RegionalDeepDiveEngine.prepare_run`, which can spend up to `HYDRATE_SECONDS = 90` fetching canon
details, then calls `validate_candidate(seed)` and returns
`422 "Seed movie rejected: Off the expedition: … isn't on the … checklist"`. The roll is worse
than useless here: it actively produces a failing configuration after a long wait.

| ID | Sev | Finding |
|---|---|---|
| S1-01 | **P0** | `suggest_seed` has no access to the draft rules, so every rules-scoped mode can be dealt an illegal seed. The error only appears after a slow Create. |
| S1-02 | **P1** | `create_run` validates the seed **after** `prepare_run`. A doomed seed still pays the full setup cost (90 s hydration for Regional Deep Dive, 60 s for Auteur Marathon filmography detail reads). |

### 1.2 Vertical: does the dice respect *any* mode filter?

The seed slot is rendered by `Step2RunSetup` → `HeroSeedPreview` → `SeedMoviePicker` for
**every** mode except Meet in the Middle (which gets two). `useRunDraft` always sends
`seed_movie_id`. Mode by mode:

| Mode | What a legal seed must satisfy | Dice respects it? | Outcome of a random seed |
|---|---|---|---|
| `regional_deep_dive` | on the list × country × decade checklist | ✗ | 422 after hydration (S1-01) |
| `canon_island` | on the chosen canon list | ✗ (any list's badge counts as "acclaimed") | 422 `This run has no valid canon list` / off-list |
| `decade_sieve` | release year in `target_decade` | ✗ | 422 |
| `method_actor` | on the actor's curated track; later steps must follow it in order | ✗ | 422. If legal, a seed mid-career **burns the track before it** (order rule: "later than the previous film") |
| `auteur_marathon` | on the director's filmography, in order | ✗ | same as Method Actor |
| `march_madness` | **nothing**: `validate_candidate` *always* blocks ("logged by deciding matchups") | ✗ | **every seed is a guaranteed 422** |
| `rt_split` | cached RT + IMDb with a ≥ 25 gap | ✗ | 422 unless already rated. If legal, the seed is stamped `watched` **without `household_score`**, so it can never score (see S1-07) |
| `roulette` | none | n/a | harmless but meaningless (the spinner picks the films) |
| `world_passport`, `chrono_climb`, `historical_time_travel`, `semantic_trope`, `aesthetic_gradient` | data present | ✓ (presence only) | OK |
| `rabbit_hole` | Tier 1 = anything | ✓ | OK |
| `tug_of_war` | anything; seed flagged so it doesn't pull | ✓ | OK |
| `meet_in_the_middle` | two **different** films | partial: each picker keeps its own `seen` list, so Partner B's roll can return Partner A's film, which trips the "different films" blocker. Nothing stops a distance-1 pair either, which makes for a dull tunnel. | P2 |
| any graph mode + **ruleset** | `min_runtime` (default 40), excluded genres, modifier start-state (e.g. `runtime_staircase`) | ✗ | rare 422, otherwise legal |

| ID | Sev | Finding |
|---|---|---|
| S1-03 | **P0** | March Madness always shows a seed slot, and any seed makes Create fail. The slot shouldn't exist for that mode. |
| S1-04 | **P1** | Checklist/track trackers (Regional, Canon Island, Decade Sieve, Method Actor, Auteur) offer a free-search seed. The only legal seeds are films the server already computes in `prepare_run`, so the seed control should **pick from that list** (or be hidden). The hint must say what a seed means here: it ticks the first checklist item, or *starts the track at this film* and forfeits earlier films. |
| S1-05 | **P1** | There is no **seed policy** in engine metadata. The frontend can't tell whether a mode takes no seed, an optional free seed, a seed from a derived list, or two seeds. `TRACKER_MODES`/`STANDALONE_MODES` in `lib/gameModes.ts` are hand-kept sets that duplicate backend capability knowledge (the drift class flagged in Phase 1). |
| S1-06 | P2 | Meet in the Middle seeds: no cross-picker exclusion and no "interesting distance" check. |

### 1.3 Point: countries render as raw `["IN"]`

**Root cause.** `HeroSeedPreview.tsx` renders
`` ` · ${isoToFlagEmoji(details.origin_country)} ${details.origin_country}` ``.
`origin_country` is a **JSON-encoded string** (`'["IN"]'`, see `CachedMovie.origin_country` and
`cache_repo.upsert_movie` → `json.dumps`). `isoToFlagEmoji` (`lib/countries.ts`) returns its input
unchanged when `iso.length !== 2`, so the line prints `· ["IN"] ["IN"]`: the raw string twice, no
flag. It isn't mobile-specific. On mobile the hero preview stacks *above the fold*, which is why
players noticed it there. A multi-country film (`["GB","US"]`) shows the whole array.

**Vertical (every country render site):** `PickNextHub` (candidate card), `ChainTimeline`,
`MovieDetailModal`, `MoviePreviewModal` and `MapPage` correctly call `parseOriginCountries`.
`HeroSeedPreview` is the **only** site that skips it. `ChainLink`, `PassportPage`, `ModifierChips`,
`RunStatsSection` and `ExpeditionBoard` already receive bare ISO codes. The plain-code fallback
(`{country}` next to the flag) is inconsistent too: `PickNextHub`/`MapPage`/`ChainLink` show
`countryName(code)` ("India"), while `ChainTimeline`, `MovieDetailModal` and `MoviePreviewModal`
show the bare code ("IN").

**Horizontal (the leaky contract).** The API sends a *serialisation format* as a field. Five
response schemas expose `origin_country: str | None` holding JSON (`schemas/movies.py`
`MovieSummary`, `schemas/discovery.py` `DiscoveryCandidate`, `schemas/engine.py` ×2,
`schemas/runs.py` `movie_origin_country`), and every client has to remember to parse it. The backend
has the same problem: three parsers (`bridge_paths.parse_countries`,
`passport.parse_country_codes`, `modifiers.step_country`) and at least one **unguarded**
`json.loads(step.movie_origin_country)` in `cinechain.compute_run_stats` (used by every graph
mode's `compute_stats`). A
legacy row holding `"US, GB"` (which `passport.parse_country_codes` explicitly allows for) raises a
`JSONDecodeError` there, a 500 on the run's stats. `conditions._countries_visited` guards the
same loop, so the two are inconsistent.

| ID | Sev | Finding |
|---|---|---|
| S1-07 | **P1** | `HeroSeedPreview` renders the JSON string, giving `["IN"] ["IN"]`. Fix: `parseOriginCountries` + flag + `countryName` for every code. |
| S1-08 | P2 | Country labels are inconsistent (code vs. name). Add one `<CountryFlags codes={…} />` component and use it at all eight sites. |
| S1-09 | **P1** | Contract smell: the API should send `origin_countries: list[str]` (computed, normalised through one backend parser) and keep the legacy string field for one release. One canonical backend helper should replace the three parsers. |
| S1-10 | **P1** | `cinechain.compute_run_stats`: an unguarded `json.loads` on `movie_origin_country` can 500 on legacy rows. |

### 1.4 Point: the Rotten Tomatoes Split blocked on *Knight and Day*

**Trace.** Split scores come only from `cached_movie_ratings` (`RottenTomatoesSplitEngine.scores_of`
→ `split_scores`). Ratings are filled by `cache_repo.get_movie_ratings`:

1. **Title-based lookup.** `OMDbClient.get_ratings_by_title(title, year)` queries `?t=…&y=…`.
   TMDB's detail payload carries `imdb_id`, but `CachedMovie` doesn't store it, so the precise
   `?i=tt…` lookup is never used. Year skew (TMDB's earliest release vs. OMDb's US year),
   punctuation, remakes and same-name films all miss or **mis-match**, which is worse: a
   mis-match silently imports another film's scores.
2. **Failure is negative-cached forever.** On any HTTP error, timeout, non-`True` response
   (including OMDb's daily cap `"Request limit reached!"`) or title miss, the client returns
   `None`. `upsert_ratings(movie_id, None)` then writes an all-`NULL` row with `fetched_at = now`.
   `get_cached_ratings` returns that row on every later call (nothing reads `fetched_at`), so
   **one transient failure permanently marks the film "no scores"**.
3. **Seed path never fetches.** `create_run` has no OMDb dependency. RT Split's
   `validate_candidate(seed)` reads the cache only, so a dice-rolled "Popular pick" with no cached
   rating is rejected (`Seed movie rejected: No Rotten Tomatoes and IMDb scores on file for Knight
   and Day (needs OMDb)`). If the hero preview's `useMovieDetail` *did* fetch and fail, step 2
   above makes the rejection permanent.
4. **No escape hatch on the board.** `SplitBoard` offers only the pool (cached films with a gap ≥
   25) and "Rate N more cached films". There is no search, no "we watched something else", no
   *retry ratings* and no *void/no-contest*. A household that already watched a film the app
   can't score has nowhere to record it, and the game stalls.
5. **Seeds are meaningless here.** A seed is inserted as a `watched` step with no
   `household_score`, so it can't produce a point. The wizard shouldn't offer one (see S1-05).

| ID | Sev | Finding |
|---|---|---|
| S1-11 | **P0** | Negative OMDb results are cached permanently. Add a TTL for all-null rows (e.g. retry after 24 h, or immediately on an explicit user "Retry ratings"), and don't persist a row at all for *transport* failures (HTTP error/timeout/rate-limit). |
| S1-12 | **P1** | OMDb lookup by title+year. Store `imdb_id` from TMDB detail (one nullable column plus a migration) and query OMDb by `i=` first, falling back to title+year. This removes both misses and wrong-film matches. |
| S1-13 | **P0** | RT Split has no dead-end exit. It needs a "Retry ratings" action and a **No-contest** log (watched, scores no point, doesn't count toward Passport *bonuses*), so a household is never stuck. |
| S1-14 | **P1** | RT Split shouldn't take a seed (`seed_policy = "none"`). |

### 1.5 Horizontal: third-party data resilience (TMDB, OMDb, 0-vote films)

| Data gap | Where it bites today | Current behaviour | Verdict |
|---|---|---|---|
| TMDB `vote_average = 0.0` / tiny `vote_count` | `movie_filters.rating_of` (fixed in Phase 2c, `vote_count ≥ 10`) | OK | ✓ |
| same | **`routes_router._rating_of`** (Marathon Router whiplash scoring) | uses `movie.vote_average` with **no vote-count gate**, so a 3-vote 9.4 counts as a masterpiece | **S1-15 P2**: route through `rating_of` |
| same | `chaos._b_movie` | via `rating_of` → unknown = lenient | ✓ |
| same | `method_actor.build_career_track` milestones | vote tiers with fallbacks; `prestige_peak` falls back to ≥ 20 votes | OK, but an obscure actor can end up with **no** breakout/peak, and the UI doesn't explain the missing badge (P2) |
| Missing runtime/country/language | modifiers, chaos, bounties | "unknown never blocks" (lenient), *except* Chrono, the Chaser (strict) and RT Split (strict) | Mostly ✓. The strict exceptions aren't surfaced in the UI as "unverified" chips. **S1-16 P2** |
| Missing ratings (OMDb) | RT Split, Rabbit Hole Tier 5, `RatingBadges` | Tier 5 falls back to TMDB. RT Split hard-blocks (S1-11/13). `RatingBadges` renders nothing, so "no data" and "not loaded" look the same | **S1-17 P2**: show a muted "no critic data" chip plus a retry |
| Missing poster image / proxy 404 | `MoviePoster` | placeholder only when `path` is null; a broken URL shows the browser's broken-image icon (no `onError`) | **S1-18 P2** |
| TMDB outage | every route that calls `cache_repo.get_movie` on a cold row | no global `TMDBError` handler (only a few routes catch it), so the player sees a bare **500** | **S1-19 P1**: register one FastAPI exception handler that maps `TMDBRateLimitError` → 429 and `TMDBError` → 503, with a friendly `detail` |
| OMDb daily cap | split pool scan, detail views | silent `None`, then negative-cached (S1-11) | P0 via S1-11 |
| Canon list without hydrated countries | Regional Deep Dive | 90 s synchronous hydration on Create, 503 "try again" | **S1-20 P1**: warm in the background via `task_runner` when a list is enabled/synced, and show "indexing N/M" in the slice picker together with the **live slice size** (see S1-21) |

**Decision-paralysis lens.** The Regional Deep Dive slice picker shows *every* country in
`EXPEDITION_COUNTRIES` whatever the chosen list contains. A player can choose "Australia × 1960s"
on Sight & Sound and only learn on Create that the slice is empty (`RunSetupError "No films on …
match that slice"`).

| ID | Sev | Finding |
|---|---|---|
| S1-21 | **P1** | Add `GET /curated-lists/{id}/slices` → `{country: count}` and `{decade: count}` from the cached badges × `CachedMovie`. The dropdowns then list only non-empty slices, with counts ("🇯🇵 Japan · 14 films"), and add a **"🎲 Surprise me"** slice roll. One cheap SQL aggregate removes a whole class of dead ends. |

### 1.6 Step 1 design recommendation: a server-owned *seed policy*

Make the engine the single source of truth for seeding, and let the dice ask the engine:

```python
# BaseChallengeEngine
seed_policy: ClassVar[Literal["none", "free", "derived", "pair"]] = "free"

async def seed_candidates(self, rules: dict, user_id: str, limit: int = 50) -> list[int] | None:
    """Legal seed ids for these (un-prepared) rules; None = no restriction (use the global pool)."""
```

* `march_madness`, `rt_split`, `roulette` → `"none"` (no slot is rendered).
* `regional_deep_dive`, `canon_island`, `decade_sieve` → `"derived"`: a cheap SQL pre-slice of the
  same predicate `prepare_run` uses (the hydrated rows only; never TMDB calls).
* `method_actor`, `auteur_marathon` → `"derived"`, constrained to the **first K films of the
  track** (default K = 1, i.e. the debut). Any other seed must show the warning "starting here
  skips N earlier films".
* `meet_in_the_middle` → `"pair"`. The suggest call takes `exclude` from both pickers and prefers
  pairs with BFS distance ≥ 3 (time-boxed and cached via the existing tunnel-distance cache).
* `GET /movies/seed-suggestion` accepts the draft `rules_config` (POST body, or a compact query
  form) and intersects `seed_candidates()` with the existing acclaimed→popular preference and
  the base ruleset (`min_runtime`, excluded genres). The response states *why*: "On Sight &
  Sound #42 · 🇦🇺 Australia".
* `create_run` validates the seed **before** `prepare_run` whenever `seed_candidates` can decide
  it, and skips the seed entirely for `seed_policy == "none"` (422 if one is sent, so a forged
  payload fails loudly).
* `/engines` metadata exposes `seed_policy`, so the wizard stops hard-coding mode sets.

---

## Step 2 — Engine Dynamics, Actor Context & UI Filters

### 2.1 Point: Method Actor run length is rigid

**Trace.** `MethodActorEngine.prepare_run` → `build_career_track(credits, birthday)`
(`backend/app/engines/method_actor.py`). The track is always *every milestone film plus the
best-known leading roles*, padded to `MIN_TRACK_FILMS = 5` and capped at `MAX_TRACK_FILMS = 25`.
`evaluate_run_outcome` completes the run **only** when the track's *last* film is logged. Pacing
comes from `max_skip` (default 2), but:

* `max_skip` is **not exposed anywhere in the creator**. `Step2RunSetup` hides `ModeOptions` and
  `RulesetFields` for trackers (`!isTracker`), and `ActorConfig` only renders `ActorPicker`. The
  only way to change it is raw JSON (admin-gated). `CareerTrack` merely *displays*
  "skip at most 2".
* For a prolific actor the minimum run is therefore fixed by arithmetic: 25 films with
  `max_skip = 2` means ⌈25/3⌉ ≈ 9 films minimum, and the player can't choose a 3-film "taster".
* Defaults disagree: Auteur Marathon's UI says `max_skip ?? 1`, Method Actor's says `?? 2`, and
  neither is explained.

| ID | Sev | Finding |
|---|---|---|
| S2-01 | **P1** | No run-length choice. Add `track_length` to Method Actor and Auteur: `"milestones"` (milestone films only, min 3), `"short"` (≈ 6), `"feature"` (≈ 12), `"full"` (≤ 25), or `"endless"` (whole credited feature filmography, uncapped up to a sane 60; the player ends it with **"Wrap the marathon"**, scored as a completion with a % summary rather than a forfeit). `build_career_track` already ranks by `keep_rank`, so length is just the slice size. Lower `MIN_TRACK_FILMS` to 3. |
| S2-02 | **P1** | Expose pacing as a three-way **Strictness** control instead of a raw number: *Strict order* (`max_skip=0`), *Relaxed* (2), *Free order* (any order, still on-track). Free order needs one new branch in `_check` (`max_skip = None` → skip the order check). Apply it to Auteur Marathon too. |
| S2-03 | P2 | Unify the `max_skip` default between Method Actor (2) and Auteur (1), or label each in the UI. |

### 2.2 Point: deep career context without hallucination

**What we already get from TMDB (already fetched by `prepare_run`):** for each credit
`release_date`, `character`, `order` (billing), `genre_ids`, `vote_average`, `vote_count`,
`original_language`, `popularity`, `adult`; for the person `birthday`, `known_for_department`,
`place_of_birth`. **Cheap extra TMDB facts:** `/movie/{id}/release_dates` (release *types*:
1 Premiere/festival, 2 Theatrical limited, 3 Theatrical, 4 Digital, 5 Physical, 6 TV) and
`/movie/{id}/keywords` (`TMDBClient.get_movie_keywords` already exists, used by Time-Travel).
**Existing local models:** Arctic ONNX overview embeddings (`services/embeddings.py`) and Qwen
trope extraction (`extracted_tropes`). Both are already JIT-loaded, so the constraint allows them.

Today's milestones are four (`debut`, `breakout`, `prestige_peak`, `modern_resurgence`). Every
proposed addition below is **derived deterministically from facts** and carries an `evidence`
string the UI shows on hover. The model never invents a fact.

| Milestone | Rule (deterministic) | Evidence shown | Covers the playtest ask |
|---|---|---|---|
| `debut` (exists) | first credited feature | date + character | ✓ |
| `first_theatrical` | first film whose `release_dates` contain type 2/3 when the debut's earliest is type 1/4/6 (festival/digital/TV). Only the first ≤ 3 films are queried (3 calls). | "Premiered at festival 1988 · first cinema release 1992" | **debut vs first theatrical** |
| `first_lead` | first `order == 0` credit | "Top-billed as *Raj*" | billing arc |
| `breakout` (exists) | first top-3 billed with ≥ 500 (→100) votes | vote count | ✓ |
| `genre_pivot` | a film whose genre set has Jaccard distance ≥ 0.75 from the union of the previous 4 films, **and** the next 2 films stay closer to the new set than to the old (it *starts an era* rather than being a one-off) | "Drama/History after 4 Romance/Comedy films" | **massive genre shifts** |
| `against_type` | overview-embedding cosine distance from the actor's trailing-5 centroid ≥ μ + 2σ of the career's own distances (embeddings of tracked films only, at most 25 vectors, existing JIT model) | "Furthest from their usual roles (top 5% of career)" | **SRK in *Swades*:** the genres alone (Drama) wouldn't flag it, but the overview's semantic distance (rural development, NRI scientist vs. romance) does |
| `comeback` | gap ≥ 4 years between consecutive credits | "First film in 6 years" | career context |
| `language_crossover` | first credit whose `original_language` differs from the actor's modal language | "First English-language role" | career context |
| `prestige_peak` (exists) | best-rated with ≥ 200 (→20) votes | rating + votes | ✓ |
| `modern_resurgence` (exists) | best-known top-5 role in the last 5 years | vote count | ✓ |

**"Villain era" (needs care).** TMDB has **no** structured antagonist flag. Ranked options:

1. **Player-tagged eras (no AI, zero hallucination):** a "🏷️ Tag this era" action on the
   CareerTrack lets players mark a contiguous span ("Villain era", "Rom-com years"). It's stored
   as user annotation in `rules_config["career_eras"]` (user-editable, so *not* server-owned).
   This adds participation and costs nothing.
2. **Keyword-grounded suggestion:** a film whose TMDB keywords include
   `villain`/`supervillain`/`antagonist`/`serial killer`/`psychopath` **and** whose overview
   *mentions the actor's character name* is offered as "🦹 Possible villain turn? (from TMDB
   keywords)". It's a suggestion chip the player confirms, never an auto-badge.
3. **Qwen (optional, behind `llm_provider != off`):** a yes/no/unknown classification over
   `{overview, character}`. Accept "yes" only when the character name appears verbatim in the
   overview, always label it "AI-read", and require player confirmation like option 2.

**Eras over decades.** `CareerTrack` groups by decade (`decadeOf`), which is arbitrary for a
career. Once `genre_pivot`/`against_type`/`comeback` exist, group the track into **eras** split at
those milestones ("1992–2003 · Romance King", named from the era's dominant genre; or the player's
tag), keeping decades as a fallback.

| ID | Sev | Finding |
|---|---|---|
| S2-04 | **P1** | Add the deterministic milestones above (`first_theatrical`, `first_lead`, `genre_pivot`, `against_type`, `comeback`, `language_crossover`) with an `evidence` string. Pure functions next to `build_career_track`; `against_type` is a post-pass that degrades to "not computed" when embeddings are unavailable. |
| S2-05 | P2 | Player-tagged eras plus keyword/LLM *suggestions* for villain turns, always confirmed by the player. Never auto-assert a subjective label. |
| S2-06 | P2 | Group CareerTrack/AuteurTrack by **eras** split at pivot milestones. |
| S2-07 | P2 | When a milestone is missing (e.g. no film reaches 20 votes for `prestige_peak`), show a muted "No prestige peak on record (too few ratings)" line instead of silently omitting it. Same for Auteur. |

### 2.3 Point: "Roguelike challenges feel static"

**Trace.** `rabbit_hole.TIERS` is a fixed tuple: the *same five rules in the same order every
run* (Freefall → Retro < 2000 → Non-English → < 100 min → rated < 6.0). `compliance()` and
`violation_reason()` are hard-wired by `tier.number` (`if tier.number == 2 …`). Tiers don't
stack: at depth 10 the Retro Lock simply *ends*. The only randomness is the life-costing re-roll
(`reroll_rabbit_hole_tier`: `random.choice` of tiers 2–5 minus the scheduled one, unseeded).

**Horizontal: three hand-written predicate catalogues describe the same film properties:**

| Catalogue | Predicates |
|---|---|
| `rabbit_hole.compliance` | year < 2000, non-English, runtime < 100, rating < 6.0 |
| `chaos.HANDICAPS` | year < 1970, rating < 6.0, runtime ≥ 150, runtime ≤ 85, non-English |
| `bounties` catalogue | runtime < 90, year < 1960, popularity < 12, non-English & non-US, female director, runtime > 150 |

Each catalogue has its own "unknown data" semantics, label text, and TMDB-hydration logic
(`needs` / `needs_detail` / `needs_directors`). That's why roguelike variety is expensive: every
new rule must be written three times.

| ID | Sev | Finding |
|---|---|---|
| S2-08 | **P1** | Extract a single **predicate registry** (`app/engines/predicates.py`, new): `Predicate(id, label, emoji, check(movie, ctx) -> bool \| None, needs: set[str], difficulty: 1-5, params)`. Rabbit Hole tiers, Chaos handicaps and Bounties become *views* over it. This is the foundation for Step 3's composable modifiers. |
| S2-09 | **P1** | **Procedural tier deck.** At creation, draw a server-owned `tier_deck` (list of predicate ids + params) from the registry with a server-owned `seed` (`random.Random(seed)`), ordered by `difficulty`. Tier 1 stays Freefall. Parameters jitter per run (Retro cutoff 1980/1990/2000, Micro-Clock 90/100/110). |
| S2-10 | **P1** | **Curse stacking** (opt-in "Hard mode"): from Tier 4, the previous tier's rule persists as a *curse*. Pair it with **relics** (rewards at each tier boundary: +1 life, a free re-roll, or "skip one curse"), giving the run a build-up arc instead of a flat sequence. |
| S2-11 | **P1** | **Feasibility guard:** before committing a deck, estimate each predicate's pass-rate on the local cache (`SELECT count(*) … WHERE …` over `cached_movies`) and reject draws below ~3%, or combos (curse + tier) below ~1%. That stops the procedural deck from dealing an impossible depth, the same class of bug as S3's impossible bounties. |
| S2-12 | P2 | **Daily Dive:** a date-derived seed (`hash(date)`) shared by all players, reusing the Daily Bridge pattern, so households can compare depth. |
| S2-13 | P2 | Seed the re-roll from the run seed + depth so it's reproducible and testable, and exclude predicates that fail the feasibility guard *given the current frontier* (no re-roll into a dead end). |

### 2.4 Point: Pick Next needs mode-specific filters

**Trace.** `PickNextHub` → `DiscoveryGrid` keeps a fixed filter set in local state: person chips
(AND/OR), text search, genre, decade, sort (Match/Year/Popularity/IMDb/RT, plus "Tug points" when
`tugMode`), and the toggles Chaser, Underdog, Tagline Roulette, plus trope chips when
`gameType === SEMANTIC_TROPE`. Mode awareness is **ad-hoc booleans** (`tugMode`, `craftMode`,
`tropeMode`, `gameType === RABBIT_HOLE`) and per-card mechanic branches in the card renderer
(`HISTORICAL_TIME_TRAVEL`, `chrono_climb`, `world_passport`, `aesthetic_gradient`). Filtering is
client-side over the fetched pool.

**Orphaned capability.** `GET /runs/{id}/suggestions` already accepts
`country`, `decade`, `genre_id`, `chaser`, `sort_by` and calls
`engine.get_suggestions(..., SuggestionFilters(...))`, but **no frontend code calls it**.
Server-side country-filtered discovery (which can reach beyond the cached pool via TMDB
`with_origin_country`, as `WorldPassportEngine.discover_rule_candidates` already does) exists and
is unreachable.

**Per-mode filter gaps (what a player wants at decision time):**

| Mode | Missing filter / shortcut | Data already on `DiscoveryCandidate` |
|---|---|---|
| World Passport | **Country** select (with pool counts), **"🆕 New stamps only"** (not yet visited this run), locked countries shown disabled with the cooldown reason | `origin_country`, `mechanic.to_country` |
| Regional-flavoured graph runs (any) | Country select | `origin_country` |
| Chrono Climb / Time-Travel | Year window bounded by the frontier ("next 10 years") | `year_delta`, `narrative_year` |
| Genre Pendulum | "Target genre only" defaulted on | `genre_ids` |
| Rabbit Hole | The pool is already pre-filtered to the active tier server-side. What's missing is a "Show off-tier films (costs a life)" toggle (needs a `include_off_tier` server param), so the player can *see* the price of a forced pick instead of hunting via direct search | `tier_compliant` |
| Tug of War | "Steals only" / "My territory" quick filters | `tug_effect`, `tug_points` |
| Any + runtime modifiers | Runtime range | `runtime` (only when the detail is cached; else "unknown") |
| Any | **"🎲 Pick for me"** from the *filtered* list, and a **"Top 3"** shortlist strip | n/a |

| ID | Sev | Finding |
|---|---|---|
| S2-14 | **P1** | Engine-declared **filter schema**: `BaseChallengeEngine.discovery_filters: ClassVar[list[FilterSpec]]` (`{key, kind: select\|toggle\|range, label, source: "candidate.origin_country" \| "candidate.tug_effect" \| …, default, server_param?}`), exposed on `/engines`. `DiscoveryGrid` renders a generic `<ModeFilterBar specs=…>` beside the universal filters and drops the `tugMode`/`craftMode`/`tropeMode` booleans. Client-side predicates are keyed by `source`, so no `eval`. |
| S2-15 | **P1** | World Passport: Country filter plus "New stamps only" (default **on** once ≥ 3 countries are stamped), counts per option, and locked countries greyed out with "Cooling down (2 more films)". |
| S2-16 | **P1** | **Anti-paralysis defaults in Pick Next:** (a) a 3-card "Director's Picks" strip above the grid (best `match`, best mode-points, one wildcard-free *underdog*); (b) "🎲 Pick for me" that respects active filters; (c) when the filtered pool is > 48, show "Showing the 48 best matches · narrow with a filter" rather than an endless scroll as the primary path. |
| S2-17 | P2 | Either wire `/runs/{id}/suggestions` to a server-side "Search further" action for a selected country/decade (when the client-side pool is empty for that filter), or delete it. Dead endpoints rot. |
| S2-18 | P2 | `DiscoveryCandidate.runtime` exists but is `None` for uncached films. A runtime filter must treat unknown as "unverified" (show the film, with a `?` chip) to match the lenient-unknown rule of `modifiers.py`. |

**S4 implementation correction:** Time-Travel's frontier is its narrative setting year, so its range uses `narrative_year`, not `release_year`. The orphaned suggestions endpoint did not actually search beyond the pool for standalone runs: `MutatorEngine.get_suggestions` returned an empty list without a cast-link modifier. S4 adds one-page filtered standalone discovery and up to 20 history-aware validations under a 20-second request deadline, with nullable connector fields rather than invented actors. Crew/person and trope chips remain because they reflect real connection/extraction capabilities; mode-specific Tug filters now come from engine metadata.

---

## Step 3 — Synergy, Modifiers & Rulesets

### 3.1 Point: bounties offer impossible tasks

**Trace.** `services/bounties.py`: `prepare_board(rules, rng)` draws
`rng.sample(list(BOUNTIES), 3)` and `draw_replacement(active, completed, rng)` draws from the
never-completed ones. **Neither receives the engine, the run's derived state or the history.**
The only engine-level gate is `supports_bounty_board` (False only for March Madness). AI bounties
(`generate_custom_bounty`) get a context-free prompt (`"Invent a new bounty."`), and
`normalize_rule` checks the rule's *syntax* but never whether any reachable film can satisfy it.

**Impossible or degenerate combinations (by construction):**

| Mode + bounds | Bounty | Why |
|---|---|---|
| Decade Sieve `target_decade=2010` | 📼 Time Capsule (< 1960) | every legal film is 2010–2019, so it's **impossible** |
| Regional Deep Dive `JP × 1970s` | 📼 Time Capsule | slice is 1970–79, so it's **impossible** |
| Regional Deep Dive `JP` | 🌍 Foreign Horizon | every film qualifies, so it's a **free wildcard** each step |
| Method Actor (career starting 2005) / Auteur (director debut 1999) | 📼 Time Capsule | **impossible** (the track's years are known at creation) |
| Method Actor / Auteur | 🎥 Female Gaze on a male director's Auteur run | **impossible** |
| Chrono Climb (ascending) once the chain passes 1960 | 📼 Time Capsule | becomes **impossible mid-run**, and nothing retires it |
| Rabbit Hole Tier 4 (< 100 min) | 🏔️ Epic Odyssey (> 150 min) | contradicts the tier, so only completable by spending a life (a net-zero trade) |
| World Passport / Tower of Babel tier | 🌍 Foreign Horizon | near-automatic |
| RT Split (pool = cached films with a 25-pt gap) | 💎 Hidden Gem (popularity < 12) | near-impossible: split films need OMDb ratings, which skew popular |

**Worse, a synergy hole in the reward.** The reward is a wildcard (`bounties.award` →
`wildcards_budget += 1`), and wildcards only buy **soft** violations (`_enforce_run_rules`:
`if not result.valid and result.blocked: raise 409`). In Regional Deep Dive, Decade Sieve and
RT Split, **every rule is a hard block** (Canon Island's list rule is hard too, but its cast link stays soft), so the wildcard a
player earns there **can't be spent on anything**. The board is enabled for them anyway, since
`supports_bounty_board` defaults to `True`. The Rabbit Hole already shows the right pattern:
`BaseChallengeEngine.award_bounty` is overridden to convert the reward into a life.

| ID | Sev | Finding |
|---|---|---|
| S3-01 | **P0** | Bounty draws ignore the engine's bounds, so impossible bounties are dealt. Add `BaseChallengeEngine.bounty_feasible(rules, history, bounty) -> Feasibility(ok: bool, reason: str, pass_rate: float \| None)`. **Checklist/track trackers:** exact, by evaluating the bounty over the remaining `movie_ids`/`filmography` (cached rows; unknown = feasible). **Decade Sieve:** a year-range intersection. **Graph modes:** cache pass-rate (shared with S2-11) plus engine bounds (chrono direction × frontier year, tier rule). `prepare_board`/`draw_replacement` take a `feasible` predicate and fall back to the least-bad option, never an impossible one. |
| S3-02 | **P1** | **Mid-run expiry.** After every logged step, re-check active bounties. One that's become impossible is retired as *"Expired: your chain climbed past 1960"* and replaced for free (server-owned `expired_bounties`, with a forge test). |
| S3-03 | **P1** | Trivial bounties (pass-rate > ~80% of the reachable pool) are excluded too. A bounty is supposed to be a *detour*. |
| S3-04 | **P0** | **Reward must fit the mode.** Add `bounty_reward: ClassVar[Literal["wildcard", "life", "hint", "star"]]`: wildcard for graph modes with soft rules, life for Rabbit Hole (exists), a tunnel hint token for Meet in the Middle, and **⭐ star** (score-only, shown in the victory text "Expedition complete · ⭐⭐⭐") for hard-rule-only modes (Regional, Decade Sieve, RT Split). Never hand out a currency the mode can't spend. |
| S3-05 | **P1** | The AI bounty prompt gets a compact **context line** (mode, decade/era bounds, tier rule, remaining-checklist summary), and the generated rule passes through `bounty_feasible` before it's accepted (one retry, then fall back to a static feasible bounty). |
| S3-06 | P2 | Let players **discard one bounty per run** for free (instead of only "replace the oldest with an AI one"). This is the cheapest anti-frustration valve. |

### 3.2 Horizontal: other "the game offered something it will reject" paths

The same *draw without consulting the engine's bounds* defect appears in:

* **Seed dice** (S1-01): the same root cause, already covered.
* **Chaos Button** (`chaos.roll`): a uniform draw over 5 handicaps, regardless of mode/frontier.
  On a Rabbit Hole Tier 4 (< 100 min), rolling *The Long Haul (≥ 150 min)* makes the next step
  contradictory, so the only way out is spending a life. On Chrono Climb once the chain is past
  1970, rolling *Time Machine (pre-1970)* is impossible because the climb forbids going back. **S3-07 P1:** filter the roll through the same
  feasibility check against the **current Pick Next pool** (the pool is already computed and
  cached client-side, and the server can recompute it cheaply), and show "re-rolled: no film in
  reach could satisfy *X*".
* **Rabbit Hole re-roll** (S2-13): it can re-roll into an infeasible tier.
* **Regional slice picker** (S1-21): it offers empty slices.

A shared `feasibility.py` (new) with `pass_rate(predicate, pool_ids)` and
`exists(predicate, pool_ids)` serves all four, so the rule is written once.

### 3.3 Modifier architecture today

`engines/modifiers.py` (Engine V3) supports exactly **three pair rules** (`chrono_direction`,
`runtime_staircase`, `country_cooldown`) plus `require_cast_link`, as **flat keys** in
`rules_config`. Adding a modifier currently means touching:

1. `modifiers.PAIR_MODIFIER_KEYS`, `merge_modifiers`, `modifier_problems`, `pair_modifier_violation`, `modifier_notes`;
2. `BaseChallengeEngine._modifiers_need_detail` (per-key `if`s), `filter_by_modifiers`, `describe_run_constraint`;
3. frontend `lib/modifiers.ts` (`MODIFIER_KEYS`, `availableModifiers`, `modifierWarnings`, `modifierPayload`), `ModeOptions.tsx` (one hand-built row per key), `ModifierChips.tsx`;
4. `types/api.ts` `RulesConfig`.

Drift has already happened: frontend `MAX_COUNTRY_COOLDOWN = 10` vs backend `20`, and frontend
`DEFAULT_COOLDOWN = { world_passport: 3 }` duplicates `WorldPassportEngine.default_modifiers`.
`supports_modifiers` is a boolean, so **every tracker rejects every modifier**
(`modifiers_requested` → 422), even film-scoped ones that would make sense on a checklist.

### 3.4 Composable modifiers: A–Z, Number in Title, Ascending Numbers

Turn modifiers into a **registry of rule objects** that any engine can host, judged by scope:

```python
# app/engines/modifier_registry.py (new)
class ModifierSpec(Protocol):
    key: str                      # "alphabet_run"
    label: str; emoji: str; blurb: str
    scope: Literal["film", "pair", "sequence"]
    needs: frozenset[str]         # CachedMovie fields; {"title"} never needs hydration
    params: type[BaseModel]       # pydantic schema → JSON Schema for the UI
    def check(self, ctx: ModCtx, film: CachedMovie) -> Verdict: ...        # ok | violation(reason) | unknown
    def progress(self, ctx: ModCtx) -> ModProgress | None: ...            # "Next letter: F · 5/26"
    def outcome(self, ctx: ModCtx) -> RunOutcome | None: ...              # optional overlay win condition
    def compatible(self, engine: type[BaseChallengeEngine]) -> str | None: ...  # None = OK, else why not
```

* **Storage:** `rules_config["modifiers"] = [{"key": "alphabet_run", "params": {...}}]`. The
  three legacy flat keys are read as aliases (`merge_modifiers` keeps working), so existing runs are
  unaffected. Derived progress (e.g. `alphabet_next`) is **folded from history** each request and
  never stored. If cached for the UI, it goes into `SERVER_OWNED_RULES` with a forge test (Rule 9).
* **Pipeline:** `modifier_violation`, `_modifiers_need_detail`, `filter_by_modifiers` and
  `describe_run_constraint` iterate the registry instead of branching per key. Trackers gain
  `scope == "film"` support (pool filter plus validation) and *sequence* support when their order
  is free (Regional, Canon, Decade Sieve).
* **`/engines`** exposes, per engine, `modifiers: [{key, label, blurb, params_schema, compatible,
  incompatible_reason}]`. `ModeOptions` renders rows generically, and incompatible ones are greyed
  out with the reason (no trial-and-error 422s).
* **Feasibility:** each overlay is checked at creation against the engine's known bounds
  (checklists exactly, graph modes by cache pass-rate). At play time Pick Next shows an
  **"overlay progress chip"** and, when the current pool has *no* film satisfying the overlay, the
  hub says so up front ("No *Q* films within reach: spend a wildcard to skip Q"). The wildcard
  is pre-checked before spending.

**The three requested overlays:**

| Key | Scope | Rule (deterministic) | Params | Overlay win | Hard cases |
|---|---|---|---|---|---|
| `alphabet_run` (A–Z) | sequence | normalised title's first letter == expected letter. Expected = letter after the last *matching* watched non-seed step (the seed may set the start letter if `seed_sets_start`) | `direction: az\|za`, `ignore_articles: bool` (The/A/An/Le/La/Der…), `wild_letters: ["Q","X","Z"]` (any title may stand in), `strict: bool` (false → any *later* letter, i.e. non-decreasing) | Reaching Z (or A) completes the run with "A to Z conquered in N films" | Normalise with NFKD + strip accents; a leading digit counts as `#` (wild by default); non-Latin titles use the TMDB English `title`, which the cache already stores |
| `number_in_title` | film | the title contains a number token: digits, number words (one…twenty, thirty…hundred, thousand, million), ordinals (first…twentieth), standalone Roman numerals II–XX | `allow_years: bool` (default false: a 4-digit 1900–2099 token doesn't count, so *1917* needs the flag) | none (pure constraint) | Avoid false Romans: only uppercase standalone tokens, never "I" alone and never words like "MIX" |
| `ascending_numbers` | sequence | the number parsed from the title is **> previous** (`mode: increasing`) or **== previous + 1** (`mode: count_up`, "Count to 10") | `mode`, `start: 1`, `target: 10`, `allow_years` | `count_up` reaching `target` wins | Requires `number_in_title` semantics (it implies it). Feasibility = an index of title-numbers over the cache (single SQL scan, memoised per process). |

**Compatibility matrix (computed via `compatible()`, shown in the UI):**

| Engine | A–Z | Number in title | Ascending numbers |
|---|---|---|---|
| CineChain / Canon Island / Crew & Craft / Auteur Relay / Tug / Rabbit Hole | ✓ (pair over the chain; feasibility per frontier) | ✓ | ✓ |
| World Passport / Chrono / Time-Travel / Pendulum / Trope / Aesthetic | ✓ | ✓ | ✓ |
| Meet in the Middle | ✗ (two frontiers: ambiguous order) | ✓ | ✗ |
| Regional Deep Dive / Decade Sieve | ✓ as `strict=false` (watch the checklist alphabetically) | ✓ only when the checklist has ≥ 3 qualifying films | ✓ when ≥ 3 numbered films exist |
| Method Actor / Auteur Marathon | ✗ conflicts with the career-order rule (the feasibility DP shows no valid ordering) | ✓ (filters the track; completion = the last *qualifying* film) | ✗ |
| March Madness / Roulette | ✗ (no step choice) | ✗ | ✗ |
| RT Split | ✗ | ✓ (pool filter) | ✗ |

| ID | Sev | Finding |
|---|---|---|
| S3-08 | **P1** | Replace per-key modifier branches with the registry (port the existing 3 + cast link 1:1 first, behaviour-neutral, with legacy keys aliased). |
| S3-09 | **P1** | Add `alphabet_run`, `number_in_title` and `ascending_numbers` as registry entries with pure, table-tested title parsers (`app/utils/title_tokens.py`, new). |
| S3-10 | **P1** | Expose per-engine modifier compatibility and param schemas on `/engines`, and drive `ModeOptions` from them. Delete `lib/modifiers.ts`'s hard-coded `availableModifiers`/`DEFAULT_COOLDOWN`/`MAX_COUNTRY_COOLDOWN` (drift). |
| S3-11 | P2 | Overlay progress chip on the run header ("🔤 Next: F · 5/26", "🔢 Next: 4") and in Pick Next (cards that satisfy the overlay get a ✓, the same pattern as `tier_compliant`). |

### 3.5 Rulesets: "Standard / Purist / Casual" don't fit most games

**Trace.** `RULE_PRESETS` lives **only in the frontend** (`components/RulesetFields.tsx`); the
backend knows only `DEFAULT_RULES_CONFIG` (`models/run.py`, preset `standard`). Every preset
tunes the same **cast-chain knobs** (`max_cast_order`, `no_consecutive_actor`, `min_runtime`,
`allow_repeats`, `wildcards_budget`). `Step2RunSetup` shows `RulesetFields` only for non-trackers,
and `castRules={castLinked}` hides actor options for standalone modes, yet the pills still say
"Purist" with nothing actor-related left to be pure about. Meanwhile each mode's *real* difficulty
knobs (Tug target and sudden death, Rabbit Hole lives and escape depth, Method Actor skip/length,
RT Split target, tunnel hint tokens) live in ad-hoc `mode-config/*Config.tsx` panels, so there's
no "Hard Rabbit Hole" one-tap.

| ID | Sev | Finding |
|---|---|---|
| S3-12 | **P1** | Server-owned **rule schemas**: `BaseChallengeEngine.rule_fields: ClassVar[list[RuleField]]` (`key, kind: int\|bool\|enum\|range, label, help, min, max, options, default, group: "core"\|"advanced"`) and `presets: ClassVar[dict[str, Preset]]` (`{label, emoji, blurb, values}`), exposed on `/engines`. `validate_rules_config` validates against the same schema (one source of truth). |
| S3-13 | **P1** | `RulesetFields` becomes a generic renderer over `rule_fields` and the engine's presets. Mode-flavoured preset names: **Rabbit Hole** *Tourist* (5 ❤️, re-rolls) / *Spelunker* (3 ❤️) / *Ironman* (1 ❤️, no re-roll, curses); **Tug** *Friendly* (first to 5) / *Rivalry* (7, sudden death) / *Blood Feud* (9, no sudden death); **Method Actor** *Taster* (milestones, free order) / *Biopic* (12, relaxed) / *Completist* (full, strict); **Cast chain** keeps Standard/Purist/Casual. The default preset is pre-selected, so the player can always just press Create. |
| S3-14 | P2 | Ship the modifier overlays (S3-09) as optional **preset add-ons** ("+ 🔤 A–Z") in the same picker, so the common combinations are one tap rather than a settings hunt. |

---

## Step 4 — Onboarding, Shared UI & the Game-Theory Gap

### 4.1 Point: "Why would I pick a movie my opponent wants?" (Tug of War)

The players' confusion is **legitimate**: the v2 incentives are muddled, and the UI misstates
them. I verified each point below by running `tug_of_war.tally` / `preview_pull` directly on
synthetic steps (pure functions, no DB).

**(a) The preview over-promises a steal.** `preview_pull` always returns
`("invasion", 2 * multiplier)`, and `lib/tugOfWar.ts` renders it as **"⚔️ Steal · 2-point
swing"**. But `tally` clamps the victim: `scores[opponent] = max(0, scores[opponent] - multiplier)`.
At 0–0, Team A invading gives **`{team_a: 1, team_b: 0}`**, a rope move of **+1, not +2**, and the
pull log still records `points=2`. The decision-time number is wrong exactly when players are
learning the mechanic, and at that moment a home pull (+1) is worth the same.

**(b) Momentum is inert.** The streak is a single `(streak_team, streak)` pair, and every pull
overwrites `streak_team`. Since Phase 2a enforces strict A/B alternation, an opposing pull
always intervenes. Simulated: 8 alternating home pulls give `streak = 1` on every pull, and
`momentum_cap = 3` is unreachable. The 🔥 chips in `TugOfWarMeter` advertise a mechanic that
can't fire.

**(c) Rational play deadlocks, then parity decides.** With home = +1 for both sides, alternating
home pulls tie 4–4 indefinitely. Sudden Death (after 12 pulls) shrinks the target by one every 2
pulls until it reaches 1. Whoever pulls first once the target is 1 wins: **Team A (the owner,
who moves first) by turn parity, not skill**. Mutual invasions hover around 0–1 because of the
clamp.

**(d) The real strategic levers are untaught.** Under v2 *any* territorial film scores for the
picker (home or invasion); only neutral films don't. The meaningful choices are therefore
**tempo** (anchor: a neutral pull banks ×2 for your next pull), **denial** (your pick sets the
frontier your opponent must link from, so leaving them a frontier whose links are mostly neutral
starves them) and **timing** (in Sudden Death a neutral costs you a point). Nothing in the UI
says any of this. The meter footer offers one sentence: "Films from 1975–2005 are neutral
anchors; invasions steal ground."

| ID | Sev | Finding |
|---|---|---|
| S4-01 | **P0** | `preview_pull` must return the **actual rope delta** (steal amount = `min(opponent_score, multiplier)`), and the pull log should record the realised delta. Strictly, this is a correctness bug in the decision UI. |
| S4-02 | **P1** | **Tug rules v3** (new `tug_rules_version=3`; v1/v2 fold paths untouched, per the Phase 2a lesson): *per-team* streaks (`streaks = {A: n, B: m}`); a home pull grows your own streak; **a raid (invasion) steals 1 *and breaks the defender's streak***; an anchor resets your own streak but banks ×2. This gives a clear rock-paper-scissors: *Build* (home) beats passivity, *Raid* punishes a builder, *Anchor* sets up a burst. Model the rope directly (`rope += delta`, no clamp) so the meter and the maths agree. |
| S4-03 | **P1** | **Fair Sudden Death:** shrink the target only at **round** boundaries (after Team B's pull), and in Sudden Death the **trailing** team pulls first in each round (catch-up rule). Add a property test (the Phase 2a lesson asks for one) showing that mirror-strategy games aren't won by parity. |
| S4-04 | **P1** | **Decision triad in Pick Next** for Tug: three pinned cards above the grid, *Best Build (+N, streak → k)*, *Best Raid (+N, breaks Ana's 🔥2)* and *Bank an Anchor (×2 next, or −1 in Sudden Death)*, each with the realised delta. This is the single biggest anti-paralysis win for versus play. |
| S4-05 | P2 | **"Leaves them"** look-ahead on hover: how many scoring vs. neutral films the opponent could reach from this frontier, computed from **cached** cast links only, time-boxed (≤ 1.5 s, the "searches stay time-boxed" rule), shown as "Leaves Ana: 6 scoring · 31 neutral". This teaches denial without a tutorial. |
| S4-06 | P2 | Rename labels to verbs that express intent: "⚔️ Steal" → "⚔️ Raid (+1 you · −1 them · breaks their streak)", "⚓ Anchor ×2" → "⚓ Bank (next pull ×2)". |

### 4.2 Vertical: strategic legibility across the other versus/co-op mechanics

| Mechanic | Legibility today | Gap |
|---|---|---|
| RT Split | `HouseholdRatingModal` previews who wins the point as you type | ✓ good pattern; reuse it |
| Rabbit Hole | HUD with tier, lives and next-tier warning; ✓ compliant badges | ✓ |
| Meet in the Middle | distance, near-miss and hints | ✓ |
| Blind Fork | offer/veto/pick flow | doesn't say *why* to offer a trap film (no "your partner vetoes one" reminder at offer time) — P2 |
| Bounty Board | criteria visible | feasibility and expiry are invisible (S3-01/02) |
| Golden Veto | a bar with a token count | the rule "only your partner's step" isn't stated until a 409 — P2 |
| Chaos | banner | no "why this is hard" pass-rate hint (S3-07) |

### 4.3 Point: logging a partner's turn on one device is clumsy

**What the server assumes.** The authenticated account is the actor everywhere except Tug:

| Mechanic | Identity check | On a shared device (one login) |
|---|---|---|
| Tug of War | client sends `tug_team = next_team` (`MovieSearchAutocomplete`, `PickNextHub`) | works, but there's no "pass the device" cue, and a mistaken double log is silently credited to the *other* team's turn |
| Blind Fork | `_require_partner_of_offer`: `offered_by_id == current_user.id` → **403** "Your partner answers your offer" | **unplayable**: the offerer can never be answered |
| Golden Veto | tokens per account (`consume_veto_token(current_user)`); non-Tug "partner's step" = `logged_by_user_id != current_user.id`; Tug = `team_of(players, current_user.id)` | **dead**: every step is "your own", and only the logged-in owner's team can ever veto |
| March Madness voting | one vote per `current_user.id`; strict majority of all participants | the partner can't vote, so every matchup ties and someone must manually "Advance Winner" |
| Passport | `logged_by_user_id` (must keep its meaning, per LESSONS) | correct: don't change |

| ID | Sev | Finding |
|---|---|---|
| S4-07 | **P0** | **Table Mode (hot-seat)**, opt-in at creation (`rules_config["table_mode"] = true`, shown as "📱 One device, many players"). Actions that are about *a player* (log step, fork offer/veto/pick, Golden Veto, bracket vote) accept an `acting_participant_id`. The server accepts it only if the run is in Table Mode **and** both the caller and the acting id are participants. It stamps `transition_metadata["acting_participant_id"]` (**server-owned**, added to `SERVER_OWNED_METADATA`, forge test) and never touches `logged_by_user_id` (Rule 9). Veto tokens are drawn from the *acting* participant's balance. |
| S4-08 | **P1** | **Seat switcher + handover interstitial.** A persistent "🎮 Ana's turn" pill in the run header (team colour, avatar). Turn-based modes auto-advance the seat (Tug → `next_team`; Blind Fork → the partner after an offer). On a seat change, a full-screen "Pass to Ben → tap when ready" card appears; for Blind Fork it **hides the offer** until tapped, preserving the "blind". |
| S4-09 | **P1** | Explicit attribution feedback: the toast reads "Logged for **Ana** (Team Old School) · +1 rope", with "Wrong player? Undo" for 10 s (uses the existing step deletion; no new mutation semantics). |
| S4-10 | P2 | Table vote for brackets: in Table Mode, the matchup shows one toggle row per participant ("Ana: A · Ben: B"), submitted as N acting votes. |

### 4.4 Horizontal: every game card and run page lacks a "How to Play"

**Inventory.** Mode copy lives in two places: backend `display_name` and `description` (one
sentence per engine, on `/engines`) and frontend `GAME_MODE_STYLES` (`tagline`, `tags`,
`progression`). Neither explains a turn, scoring, losing or strategy. Run pages have a
`RulesSummaryCard` (graph modes only, a key/value list of settings); tracker boards have none.
There are no coach marks, no glossary and no first-run hint for *wildcard*, *life*, *anchor*,
*steal*, *tier*, *bounty* or *seed*. Overlays (Bounty Board, Chaos, modifiers) each explain
themselves differently, if at all.

**Design: a composable, server-sourced Rulebook.**

```python
# app/engines/rulebook.py (new)
@dataclass(frozen=True)
class RuleSection:
    goal: str            # "Pull the rope 4 past the centre."
    turn: list[str]      # numbered steps of one turn
    scoring: list[str]   # how points/progress move (templated with run values)
    lose: list[str]      # how it ends badly (may be empty)
    tips: list[str]      # 2-3 strategy hints (game theory, not mechanics)
    glossary: list[str]  # term keys used, resolved from GLOSSARY

GLOSSARY: dict[str, str] = {"wildcard": "...", "life": "...", "anchor": "...", "raid": "...", ...}
```

* Each engine declares `rulebook: ClassVar[RuleSection]` with `{target}`-style placeholders.
  Each overlay (Bounty Board, Chaos, every registry modifier, Table Mode) declares its own
  `RuleSection` fragment. `render_rulebook(engine, rules)` composes engine + active overlays and
  formats the placeholders with the run's real numbers ("First to **5**", "**3** ❤️", "Raid steals
  **1**").
* Served from `GET /engines` (generic, for cards) and `GET /runs/{id}/rulebook` (rendered, for
  the run).
* **Frontend: one `<HowToPlay>` component, three entry points:**
  1. a **"?"** on every `GameModePicker` card → a popover with *Goal · Your turn · How to win*
     (3 lines) and "Full rules";
  2. a **"📖 How to play"** button in every run header → a side drawer with the composed rulebook
     and glossary;
  3. **first-visit auto-open** per `game_type` (a `localStorage` flag), collapsed to the
     3-line card so it never blocks play.
* **Contextual glossary chips:** effect labels (⚔️ Raid, ⚓ Bank, ❤️, 🎟️) become buttons that open
  their glossary entry (the existing `Popover`).
* **Guard test:** a backend test iterates `ENGINE_REGISTRY` and the modifier registry and fails
  if any entry lacks a non-empty `goal`, `turn` and `scoring`, so a new mode can't ship without
  rules.

| ID | Sev | Finding |
|---|---|---|
| S4-11 | **P1** | No How-to-Play anywhere. Build the server-sourced `Rulebook` plus `<HowToPlay>` with three entry points and the registry guard test. |
| S4-12 | P2 | Collapse the duplicated mode copy: `GAME_MODE_STYLES` keeps *visual* identity only, and text (`tagline`, `tags`) moves to engine metadata alongside the rulebook. |
| S4-13 | P2 | Extend `RulesSummaryCard` (or replace it with the rulebook drawer's "This run" tab) to trackers too. |

---

## Review: root causes & priority roll-up

Five root causes explain most of the findings above:

| Root cause | Symptoms (finding IDs) | Structural fix |
|---|---|---|
| **R1. Random draws that don't consult the engine's bounds** | seed dice (S1-01…06), bounties (S3-01…05), Chaos (S3-07), tier re-roll (S2-13), empty Regional slices (S1-21) | one `feasibility.py` plus engine hooks (`seed_candidates`, `bounty_feasible`) |
| **R2. Engine knowledge duplicated in the frontend** | `TRACKER_MODES`/`STANDALONE_MODES`, `RULE_PRESETS`, `availableModifiers`, `DEFAULT_COOLDOWN`, `MAX_COUNTRY_COOLDOWN` drift (10 vs 20), duplicate `EngineMeta` interface in `types/api.ts`, mode copy split across two places | `/engines` becomes the schema: `seed_policy`, `rule_fields`, `presets`, `modifiers`, `discovery_filters`, `rulebook` |
| **R3. Third-party gaps treated as permanent facts** | OMDb negative-cache forever (S1-11), title-based lookup (S1-12), no TMDB outage handler (S1-19), RT Split dead end (S1-13) | TTL'd negatives, `imdb_id`, a global error mapping, an escape hatch in every hard-block mode |
| **R4. Identity = authenticated account** | Blind Fork, Golden Veto, bracket votes on a shared device (S4-07…10) | Table Mode with a server-validated `acting_participant_id` |
| **R5. Mechanics that the maths or the UI don't actually deliver** | Tug preview vs clamp (S4-01), inert momentum (S4-02), parity-decided Sudden Death (S4-03), wildcard rewards in hard-only modes (S3-04) | Tug v3, mode-fit rewards, property tests |

**Severity count (tabled findings):** P0 × 8 (S1-01, S1-03, S1-11, S1-13, S3-01, S3-04, S4-01, S4-07) · P1 × 33 ·
P2 × 18, plus the untabled S1-15…S1-20 resilience rows in §1.5. The phased fix order is in [`SYNERGY_IMPLEMENTATION.md`](./SYNERGY_IMPLEMENTATION.md).

## Follow-up finding: Semantic Tropes tagging quality

| ID | Severity | Finding and required fix |
|---|---|---|
| S2-19 | **P1** | Player-reported: the existing local Arctic/Qwen pipeline assigns nonsensical tags, such as `cyberpunk` to pure romantic comedies. Introduce a **Genre Gate** against hard TMDB `genre_ids` and a strict **Confidence Threshold** for overview-to-trope cosine similarity. Silently discard conceptually genre-incompatible or below-threshold tags before storing or using them as game links. Schedule with S10; preserve existing JIT model loading/unloading and add no ML models or dependencies. |

This is a follow-up report, not a newly reproduced model evaluation. S10 must verify the
rom-com/cyberpunk case using deterministic fixture vectors and explicit genre/trope compatibility
rules; unknown genres must not be invented, and service failures must remain observable.

**Future architecture (not scheduled):** integrate a custom **TVTropes Scraper Hybrid Pipeline**
in a later release. Evaluate source permissions, attribution, rate limits, provenance and a
hybrid merge with the existing local pipeline before implementation. No scraper or infrastructure
change belongs to S1.
