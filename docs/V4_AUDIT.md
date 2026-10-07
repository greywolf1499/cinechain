# CineChain v4 Audit: Mechanics, Taxonomy & Core UX (post-v3.0.0 playtest)

**Baseline:** `master` @ `c6e3584` (v3.0.0, Synergy blueprint S0–S11 shipped).
**Method:** each playtest observation is traced through three lenses:
**Point** (the reported symptom), **Vertical** (every feature, endpoint and UI layer that depends on
the same component), **Horizontal** (the same *class* of problem anywhere in the codebase).
**Binding constraints (unchanged from v3):** no Redis/Celery/cron/brokers/vector databases; all
background work stays on the ephemeral `services/task_runner.py`; **no new ML models or ML
dependencies** (Arctic ONNX embeddings and local Qwen stay JIT-loaded and garbage collected);
searches stay time-boxed; every server-computed key goes into `SERVER_OWNED_METADATA` /
`SERVER_OWNED_RULES` with a forge test; legacy runs keep their folds via `*_rules_version`.

Finding IDs: `V<step>-<nn>`. Severity: **P0** blocks play / corrupts state, **P1** misleads or
stalls the player, **P2** polish/consistency/extensibility. Code references are by symbol
(line numbers drift). Files marked **(new)** do not exist yet.

---

## Step 1 — Tug of War Topology & Decoupled Traversal Rules

### 1.1 Point: shared cast between distant poles bottlenecks the rope

**Trace.** `TugOfWarEngine` (`backend/app/engines/tug_of_war.py`) subclasses `CineChainEngine`
and never overrides `validate_primary`, so **every pull must share a top-`max_cast_order` actor
with the frontier film**. The rope is scored by `_territory(release_year, origin_country, rules)`,
which knows exactly two hard-coded dimensions:

| Dimension | Team A | Team B | Neutral band |
|---|---|---|---|
| `era` | `release_year < era_a_before` (1975) | `release_year > era_b_after` (2005) | 1975–2005 |
| `geography` | first production country ∈ `WESTERN_COUNTRIES` (US + Europe, 52 codes incl. RU/BY/UA/SU) | every other first country | **none** (only an unknown country) |

Shared-cast graphs are strongly clustered by industry and language. A Hindi-language frontier film
has almost no top-15 cast routes into US/European films and vice versa, so the frontier stays
inside whichever cluster it is in. Because the v3 effect of a pull is decided by the film's
territory relative to the puller (`preview_pull_v3`), **the cluster decides the move, not the
player**: inside a Bollywood cluster Team B can only Build and Team A can only Raid; the
strategic triad from S6 (Build/Raid/Bank) collapses into a forced move. The S6 cache-only
lookahead (`GET /runs/{id}/tug/lookahead`) makes this visible (all reachable films score for one
side) but cannot fix it.

| ID | Sev | Finding |
|---|---|---|
| V1-01 | **P1** | The shared-cast requirement is inherited, not chosen. Between low-overlap poles (Bollywood vs Western, silent era vs 2010s) the frontier gets trapped in one cluster and the Build/Raid/Bank decision degenerates into a forced move. The only escape is a soft wildcard, which Tug presets do not budget. |
| V1-02 | **P1** | The `geography` plane has **no neutral band**: `_territory` returns `None` only when the country is unknown. "Bank" is therefore reachable only through missing data, so the v3 triad is really a duo on that plane, and Sudden Death's neutral rules fire on data accidents. |
| V1-03 | **P2** | Only two planes exist, and their parameters are not player-facing: `era_a_before`/`era_b_after` are validated but absent from `rule_fields` and the `TugConfig` wizard, so every Era game is the same 1975/2005 split. Replayability relies on the opponent, not the board. |
| V1-04 | **P2** | `WESTERN_COUNTRIES` is an ad-hoc taxonomy (Russia/Belarus/Soviet codes are "Western"; Canada, Australia and New Zealand are "Rest of World"). It is a political label, not a measurable cinema cluster, and it is not balanced: a typical cache is dominated by US titles, so Team A starts with most of the pool. |

### 1.2 Vertical: everything that knows what a "territory" is

| Layer | Symbol | Coupling |
|---|---|---|
| Engine scoring | `_territory`, `step_team`, `_v1_compute_scores`, `tally_v2`, `tally_v3`, `preview_pull*` | Territory is **recomputed from `RunStep.movie_release_year` / `movie_origin_country` on every fold**. A plane that needs any other fact (genres, language, runtime, setting year) has nothing to read: `RunStep` stores only title, poster, year and country. |
| Discovery | `TugOfWarEngine.discover_candidates` | Calls `CineChainEngine.discover_candidates` (actor filmographies) and annotates `tug_effect`/`tug_points`. The pool *is* the cast graph, so a non-graph plane has no pool generator. |
| Lookahead | `routes_runs._cached_tug_lookahead` | Re-implements engine knowledge in the route: imports `_territory` and hard-codes `rules.get("dimension") == "geography"` / `"era"` to decide what counts as missing data. A third plane silently reports wrong `partial` flags. |
| Rulebook | `TugOfWarEngine.rulebook_values` | Inline `if dimension == era else "US and Europe"` strings. `tags = [..., "Era or geography"]`. |
| Rules | `validate_rules_config`, `prepare_rules_config`, `tug_config` | `DIMENSIONS = (era, geography)` literal; `tug_config` defaults `target_lead` to **4** while the `RuleField` default is **7**. |
| Frontend | `lib/tugOfWar.ts#TUG_DIMENSIONS`, `TugConfig.tsx`, `TugOfWarMeter.tsx`, `RunDetailPage.tsx`, `types/api.ts#TugDimension` | A hand-written mirror of the two planes. `DEFAULT_TARGET_LEAD = 4` in the lib, `?? 7` in `TugConfig`, `?? 4` in `RunDetailPage`. Labels ("Western (US & Europe)") duplicate backend copy. |
| Capabilities | `TugOfWarEngine.capabilities` | Keeps `solve_bridge` because the parent has it; a non-cast traversal would advertise a Bridge Solver that cannot represent its hops. |
| Table Mode / Golden Veto / Blind Fork | `step_turn_team`, `tug_team` metadata | Unaffected by planes (they key on teams, not territories), which is the seam a refactor must preserve. |

| ID | Sev | Finding |
|---|---|---|
| V1-05 | **P1** | Territory is derived from denormalised step columns, so any new plane (genre, language, runtime, setting era) needs a server-owned **territory stamp** on the step. Without one, a later cache refresh (for example a TMDB genre edit) would silently rescore finished games. |
| V1-06 | **P2** | The lookahead route duplicates plane logic and hard-codes dimension names; it must ask the plane what is "unknown". |
| V1-07 | **P2** | Default drift: `target_lead` 4 (`tug_config`, `lib/tugOfWar.ts`, `RunDetailPage`) vs 7 (`RuleField`, `TugConfig`). A raw-JSON run without `target_lead` gets a different game than the wizard advertises. |
| V1-08 | **P2** | Capabilities are inherited; a decoupled traversal must derive `solve_bridge`/`bridge_swap`/lookahead from the traversal rule, not the class tree. |

### 1.3 Horizontal: traversal is hard-wired by inheritance in five different shapes

The question "what makes film B a legal successor of film A?" is answered in at least five
incompatible places:

| Where | Shape of the link rule |
|---|---|
| `CineChainEngine.validate_primary` | Shared top-N actor, plus `reunions.character_hop`. Inherited by Tug, Meet in the Middle, Canon Island, Rabbit Hole. |
| `MutatorEngine.optional_cast_link` + `cast_link_required(rules)` | Class flag + the `require_cast_link` modifier boolean; only five standalone engines may drop the cast link. |
| `AuteurRelayEngine._validate_link` | Actor/director alternation with `connection_type` bookkeeping. |
| `CrewCraftEngine` | Any person in any key craft role. |
| `services/graph.py#find_links` (Daily Bridge) and `PathConstraints(use_directors, alternate_edges)` (constrained pathfinder) | A third and fourth representation of link kinds. |

The **same root cause** as Tug's bottleneck exists in Meet in the Middle (two cast frontiers in
different industries may never collide; the tunnel BFS just reports "no route"), in Canon Island
(a non-Western canon list starves the cast pool) and in Rabbit Hole Tier "Tower of Babel"
(non-English films reached only through cast hops out of an English cluster).

| ID | Sev | Finding |
|---|---|---|
| V1-09 | **P1** | There is no first-class **traversal (link) policy**. Link kind, cast depth, alternation and "no link" are spread across class flags, a modifier and two pathfinders. Each new mode must subclass the right ancestor to get the right link, and capabilities follow the class tree rather than the rule. |
| V1-10 | **P2** | Every "two sides" mechanic (Tug territories, RT Split critic/audience, Meet in the Middle sides) has a bespoke partition function and bespoke UI mirror. A shared, data-defined **pole** abstraction would let all of them render, explain and balance-check the same way. |

### 1.4 Design: the Tug Plane Registry

A **plane** is a data-defined pair of poles plus a neutral complement plus a traversal rule.
It replaces the `dimension` literal. Planes are registered in code (like `ENGINE_REGISTRY` and the
modifier registry), parameterised with a strict Pydantic schema, and balance-checked against the
household cache before a run can be created.

```python
# backend/app/engines/tug_planes.py (new)
class PoleSpec(BaseModel):
    label: str                      # rendered: "1970s", "France", "Spectacle"
    emoji: str
    match: FacetQuery               # Step 2 facet expression (until then: predicate ids + params)

class TraversalRule(BaseModel):
    kind: Literal["shared_cast", "shared_director", "shared_any_person",
                  "attribute", "draft"]
    cast_limit: int = Field(15, ge=1, le=30)          # graph kinds only
    attribute: Literal["genre_overlap", "decade_adjacent", "language", "shared_trope"] | None = None
    deal_size: int = Field(5, ge=3, le=8)             # "draft" only
    portals_per_team: int = Field(1, ge=0, le=3)      # anti-bottleneck escape, see 1.5

class TugPlane(BaseModel):
    id: str                         # "bipolar_decades", "country_pair", "genre_clusters", ...
    version: int                    # bump = new snapshot; old runs keep theirs
    label: str; blurb: str
    params_model: type[BaseModel]   # e.g. DecadePairParams(a: int, b: int) with a validator
    resolve: Callable[[BaseModel], tuple[PoleSpec, PoleSpec]]
    overlap: Literal["contested_neutral", "first_wins", "majority"]  # a film matching both poles
    default_traversal: TraversalRule
    allowed_traversals: frozenset[str]
    balance: BalanceSpec            # thresholds below
```

**Initial catalogue (balanced by construction, then verified by `BalanceSpec`):**

| Plane id | Poles | Neutral | Default traversal | Why it is balanced |
|---|---|---|---|---|
| `bipolar_decades` | decade `a` vs decade `b`, ≥ 30 years apart (1950s vs 1990s, 1970s vs 2010s, …) | every other decade | `shared_cast` | Long careers bridge 30-year gaps; the decades in between are a real Bank band. Parameter rotation gives 20+ distinct boards. |
| `era_classic` (legacy) | `< era_a_before` vs `> era_b_after` | between | `shared_cast` | Frozen mapping of today's `era` dimension; v1–v3 runs read it. |
| `geo_west_rest` (legacy) | `WESTERN_COUNTRIES` vs rest | unknown only | `shared_cast` | Frozen mapping of today's `geography` dimension. Not offered for new runs. |
| `country_pair` | country `a` vs country `b` (first production country) | all other countries | **per pair**: `shared_cast` for high-overlap pairs (US/GB, US/CA, FR/BE, FR/IT, DE/AT, ES/MX, KR/JP*), `attribute: genre_overlap` or `draft` for low-overlap pairs (IN/US, NG/GB) | Pairs are only offered when both poles pass the cache pass-rate floor; low bridge density automatically switches the default traversal. *KR/JP is offered with `attribute` unless the cache proves cast overlap. |
| `language_pair` | `original_language` `a` vs `b` (hi vs ta, en vs fr, es vs pt) | other languages | `shared_cast` for same-country pairs, else `attribute` | Fixes the "Bollywood vs Tollywood" request without political labels. |
| `genre_clusters` | **Spectacle** {Action 28, Adventure 12, Science Fiction 878, Fantasy 14, War 10752} vs **Intimate** {Drama 18, Romance 10749, Music 10402, Family 10751} | films with neither cluster, or (default) films in both (`contested_neutral`) | `shared_cast` | Actors cross genres constantly, so cast routes are dense; the contested overlap guarantees a Bank band. |
| `runtime_poles` | ≤ 95 min vs ≥ 150 min | 96–149 | `shared_director` | Directors' careers mix short and long films; a director link avoids the cast cluster problem. |
| `setting_eras` | narrative year < 1900 vs > current year (Period vs Futurist, from `historical_era`) | contemporary settings | `attribute: shared_trope` | Reuses `narrative_year`; the trope link makes the hop thematic rather than cast-bound. |
| `critic_audience` | Tomatometer − audience ≥ 15 vs ≤ −15 (RT Split facts) | gap < 15 | `draft` | Ratings are cache-only and sparse; dealing cards from the rated pool avoids an empty graph. |

**Balance check (`BalanceSpec`, run at creation and when offering a parameterisation).** Over the
reality-eligible, min-runtime-eligible cached films, using the existing `feasibility` evidence:
- each pole's pass-rate ≥ 5% and the pole ratio within 0.5–2.0;
- the neutral band (or contested overlap) between 10% and 50%, so Bank is a real option (fixes V1-02);
- for graph traversals, **bridge density** ≥ 2%: the share of sampled pole-A cast members with at
  least one cached credit in pole B (and vice versa), computed cache-only with the same deadline
  pattern as the S6 lookahead. Below that, the plane's default traversal is downgraded to the
  next allowed non-graph kind and the creator says so ("France vs Japan has few shared actors in
  your cache; this match uses Genre Overlap links").
- Parameterisations that fail are not offered; the dice ("🎲 Random plane") draws only passing ones,
  following the S8 rule that random draws must consult feasibility.

### 1.5 Design: per-plane traversal rules (decoupling "shared cast" from the mode)

```python
# backend/app/engines/traversal.py (new)
class LinkPolicy(Protocol):
    kind: str
    graph: bool                      # True => bridge solver / lookahead are meaningful
    async def validate(self, engine, prev_id, next_id, rules, previous_transition) -> ValidationResult: ...
    async def discover(self, engine, frontier_id, rules, history) -> list[DiscoveryCandidate]: ...
    def metadata(self, result: ValidationResult) -> dict | None: ...   # server-owned link evidence
```

| `kind` | `validate` | `discover` | Reuses |
|---|---|---|---|
| `shared_cast` | today's `CineChainEngine.validate_primary` | actor filmographies | `CineChainEngine` |
| `shared_director` | a shared director | directors' directed credits | `AuteurRelayEngine` director path, `cache_repo.get_director_credits` |
| `shared_any_person` | any shared actor or key crew | Crew & Craft pool | `CrewCraftEngine` |
| `attribute` | a pair predicate (`genre_overlap` = Jaccard > 0, `decade_adjacent` = ≤ 10 years, `language`, `shared_trope`) | `MutatorEngine.discover_rule_candidates`-style cache + TMDB discover, per pole | `MutatorEngine` pools, Genre Pendulum overlap, Semantic Trope `shared_trope` |
| `draft` | the film must be one of the server's current `tug_deal` | the deal itself | `RouletteEngine.draw` (distinct, seeded) |

**`draft` is the "pure attribute pull" done right.** Dropping the graph entirely would make every
turn a free Build. Instead the server deals `deal_size` feasible films per pull, seeded by
`random.Random(f"{tug_seed}:{pull_index}")`, drawn from the plane universe with a fixed mix
(≥ 1 home, ≥ 1 opponent, ≥ 1 neutral when they exist). The puller chooses among Build, Raid and Bank
cards: the triad stays a real decision and the game is replayable from the seed. The deal is
server-owned and regenerated only when a pull is logged or deleted.

**Anti-bottleneck "Portal" (graph kinds only).** Each team gets `portals_per_team` (default 1)
free unlinked hops **into the neutral band only**. A portal is pre-checked before spending
(LESSONS: "pre-check before spending") and is offered automatically when the S6 lookahead reports
zero scoring replies for the puller. It cannot score directly, which keeps it from being a free
Build, but it breaks the cluster lock.

**Data model (`rules_config`, versioned):**

```jsonc
{
  "tug_rules_version": 4,                                  // server-owned (already)
  "tug_plane": {"id": "country_pair", "params": {"a": "FR", "b": "IT"}},   // client input
  "tug_traversal": {"kind": "shared_cast", "cast_limit": 15},             // client input, must be in allowed_traversals
  "tug_plane_snapshot": {                                   // server-owned, frozen at creation
    "id": "country_pair", "version": 1,
    "poles": {"team_a": {"label": "France", "emoji": "🇫🇷", "match": {...}},
              "team_b": {"label": "Italy",  "emoji": "🇮🇹", "match": {...}}},
    "overlap": "contested_neutral",
    "traversal": {"kind": "shared_cast", "cast_limit": 15, "portals_per_team": 1},
    "balance": {"a": 0.12, "b": 0.09, "neutral": 0.79, "bridge_density": 0.041}
  },
  "tug_seed": 196..., "tug_deal": [ids...], "tug_portals": {"team_a": 1, "team_b": 1}  // server-owned
}
```

Step metadata (server-owned): `tug_territory` (`team_a | team_b | null`) with
`tug_territory_evidence` (`{"facet": "first_country", "value": "FR"}`), and `tug_link`
(`{kind, person_id?, attribute?}`) or `tug_portal: true`.

**Fold compatibility.** `tally_v3` gains a `territory_of(step)` callback: v4 reads the stamp,
v1–v3 keep `_territory` exactly (mapped through `era_classic` / `geo_west_rest`). No migration is
needed: everything lives in `rules_config` / `transition_metadata`. The lookahead route asks
`plane.unknown(row)` and `policy.graph` instead of hard-coding dimensions (V1-06). Capabilities are
derived: `solve_bridge` only when `policy.graph`; `bridge_swap` never; a `tug_draft` capability for
`draft` (V1-08).

**Frontend.** `GET /engines` publishes the plane catalogue (`TugPlaneMeta`: id, label, blurb,
params schema, allowed traversals, default traversal). `TugConfig.tsx` renders a plane card grid,
generic parameter controls (reusing the S9 metadata-driven controls), a traversal selector limited
to `allowed_traversals`, the balance readout ("France 12% · Italy 9% · Neutral 79% · shared actors
4%") and a 🎲 Random plane button. `TUG_DIMENSIONS` is deleted; `TugOfWarMeter` reads pole labels
from `tug_plane_snapshot` (fixes V1-07 drift by removing the mirror).

### 1.6 Step 1 summary

| ID | Sev | Remedy |
|---|---|---|
| V1-01 | P1 | Per-plane `TraversalRule`, bridge-density downgrade, Portals |
| V1-02 | P1 | `BalanceSpec` neutral-band floor; legacy geography not offered for new runs |
| V1-03 | P2 | Parameterised plane catalogue + random plane |
| V1-04 | P2 | Measurable country/language pairs replace the West/Rest taxonomy |
| V1-05 | P1 | Server-owned `tug_territory` stamp; v4 folds read stamps |
| V1-06 | P2 | Lookahead asks the plane/policy |
| V1-07 | P2 | Single default source (`RuleField`), frontend mirror deleted |
| V1-08 | P2 | Capabilities derived from the policy |
| V1-09 | P1 | `LinkPolicy` registry shared by Tug first, then Meet in the Middle, Canon Island, Rabbit Hole |
| V1-10 | P2 | `PoleSpec` reused by RT Split / Meet in the Middle displays |

---

## Step 2 — The Universal Metadata Matrix (Facet Engine)

### 2.1 Point: modifiers and predicates are unidirectional

**Trace.** The S9 overlays in `engines/modifier_registry.py` are partly bidirectional:
`AlphabetParams.direction` supports `az`/`za`, `ChronoParams` `climb`/`descent`,
`RuntimeParams` `ascending`/`descending`. But `AscendingParams` (`ascending_numbers`) has only
`mode ∈ {increasing, count_up}`: there is no `decreasing` or `count_down` ("countdown from 10").
The S8 predicate registry (`engines/predicates.py#_SPECS`) is worse: it ships **10 ids**, and the
numeric ones only point one way: `year_lt` (no `year_gt`), `rating_lt` (no `rating_gt`),
`popularity_lt` (no `popularity_gt`). Every consumer inherits the bias:

| Consumer | Draws | Missing inverse |
|---|---|---|
| Rabbit Hole v2 deck (`rabbit_hole.py` catalogue) | `year_lt` 1980/1990/2000, `rating_lt` 5/6/7, `popularity_lt` 15/30/50, `runtime_lt`, `runtime_ge`, `non_english`, `non_us_non_english` | "Post-2010", "Acclaimed (≥ 7.5)", "Blockbuster" tiers |
| Chaos (`engines/chaos.py`) | `pre_1970`, `b_movie`, `epic_length`, `short_flick`, `foreign_tongue` | "Modern only", "Crowd-pleaser", "English only" |
| Static bounties (`services/bounties.py`) | short, time capsule, hidden gem, foreign, female gaze, epic | "Brand new", "Certified hit" |

| ID | Sev | Finding |
|---|---|---|
| V2-01 | **P1** | Ordering and threshold rules are declared per feature, so direction support is accidental. `ascending_numbers` cannot count down; the predicate registry cannot express "after", "above" or "more popular than"; Chaos/Rabbit Hole/bounties consequently only ever push players towards older, shorter, worse or obscurer films. |

### 2.2 Vertical: every place that asks "does this film have property X?"

| # | Implementation | Vocabulary | Notes |
|---|---|---|---|
| 1 | `engines/predicates.py` (S8 registry) | 10 ids, three-valued | The intended single source; already consumed by bounties, Chaos, Rabbit Hole, feasibility. |
| 2 | `services/bounties.py#normalize_rule` / `custom_bounty` (AI bounties) | `runtime{min,max}`, `year{min,max}`, `decade`, `genre` (own `GENRE_IDS` map), `keyword` | `keyword` is a **substring/prefix search** over `title + tagline + overview` concatenated in `facts_of`. |
| 3 | `frontend/src/lib/bingo.ts` | ~35 client-side squares (classic, imdb-high, asian, european, latam-africa, not-us-uk, genres…) | Own region sets (`ASIA`, `EUROPE`, `LATAM_AFRICA`), own thresholds; not shared with the server. |
| 4 | `engines/trackers.py#RouletteEngine` | runtime/rating ranges, genre AND/OR | Raw SQL with `json_each(genre_ids)` and `CAST(imdb_rating AS REAL)`. |
| 5 | `services/pool_options.py` (Chaser, Underdog) | runtime ≤ 95 + Comedy/Animation; popularity ordering | Hard-coded genre ids. |
| 6 | `services/seed_suggestions.py#MODE_REQUIREMENTS` | data-presence checks | |
| 7 | `engines/conditions.py` (win/fail) | `decades_spanned`, `countries_visited`, … | Run-level metrics, not film facets, but the same data. |
| 8 | `engines/modifier_registry.py` + `utils/title_tokens.py` | first letter, title numbers | Re-tokenises every title in the **whole history on every check** (`TitleModifier.state` walks `ctx.history`). |
| 9 | `engines/tug_of_war.py#WESTERN_COUNTRIES`, `genre_pendulum.TMDB_GENRE_IDS`, `ForkInTheRoadModal` genre list, `lib/pendulum.ts` | Region and genre taxonomies | Three region taxonomies and four genre maps. |

| ID | Sev | Finding |
|---|---|---|
| V2-02 | **P1** | There are at least **nine** independent film-property vocabularies. A new property (for example "one-word title") must be added to each consumer separately, and the client-side Bingo squares can disagree with the server's predicates about the same film. |
| V2-03 | **P1** | **Ad-hoc string parsing at query time.** `origin_country` is a JSON array stored as a string and parsed by `parse_countries` in every consumer; OMDb ratings are stored as strings (`"7.5"`, `"88%"`) and cast in SQL or Python each read; title tokens are re-derived on every check; keyword bounties scan free text. None of these can use an index. |
| V2-04 | **P1** | `services/feasibility.Evidence` loads **every** `CachedMovie`, `CachedMovieDirector` and `CachedMovieRating` row into Python on first use in a request, then evaluates predicates in a loop. That is fine for 10 predicates on a small cache and does not scale to dozens of intersecting facets or a 20k-film cache. |
| V2-05 | **P2** | Tri-state is only partially preserved at rest. `extracted_tropes` distinguishes `NULL` from a value; director gender `0` means "unspecified"; but nothing records *whether a derived property was computed*, so "false" and "not yet known" collapse for any new property. |
| V2-06 | **P2** | Requested reception and production facets need data that is fetched but discarded: TMDB `budget`, `revenue` and `belongs_to_collection` (already in the `/movie/{id}` payload `TMDBClient.get_movie` fetches, but dropped by `_normalize_movie_detail`) and person `deathday` (in `/person/{id}`, already called by Method Actor). OMDb has **no Rotten Tomatoes audience score** (LESSONS 27b), so "audience" must be the IMDb rating, as RT Split already does. |
| V2-07 | **P2** | Predicate `difficulty` is a hand-assigned integer (2 or 3). Rabbit Hole orders decks by it; it should be derived from measured cache pass-rates. |

### 2.3 Horizontal: taxonomy drift

The same class of problem (a *classification* hand-coded in more than one place) also covers
region sets (Tug, Bingo, Passport historical-code folding), genre ids (bounties, Pendulum,
Fork modal, Bingo, Pendulum UI), "rating" (IMDb vs verified TMDB fallback, which S0 fixed for the
router and S2 for ratings, but Bingo still reads `imdb_rating` only), and thresholds that encode
the same idea with different numbers ("short" = < 85 in Bingo, < 90 for the Short King bounty,
≤ 85 for Chaos, < 100 for Micro-Clock, ≤ 95 for Chaser).

| ID | Sev | Finding |
|---|---|---|
| V2-08 | **P2** | Shared *named concepts* ("short", "classic", "Asian cinema", "hidden gem") have different thresholds in each feature. Players see "short" mean four different things. A facet catalogue should own named concepts with one default threshold and explicit, labelled parameter variants. |

### 2.4 Design: the Facet Engine

**Principles.** (1) Facets are *declared in code* (id, kind, family, evaluator, version, needs),
like predicates and modifiers; *values* live in SQLite. (2) Three-valued everywhere: a missing
value is **unknown**, never false (S8 contract). (3) Values are typed and indexed so that
intersections are SQL, not Python loops. (4) No new infrastructure or ML: cheap facets are
computed inline on upsert; expensive ones are filled by the Data Spa (Step 4) through
`task_runner`, or JIT within existing per-request budgets. (5) Every evaluator is versioned;
bumping a version marks old values stale and they are recomputed lazily, exactly like the
embedding fingerprint (`overview_embedding_model`).

**Schema (one migration, additive).** SQLite in the shipped `python:3.12-slim` image may predate
JSONB (3.45), so the design uses plain tables + JSON1 only.

```sql
-- One row per (movie, facet, value). Set-valued facets (tropes, title numbers, genres,
-- countries) have several rows. Booleans are value_num 0/1.
CREATE TABLE movie_facets (
  facet_id    TEXT    NOT NULL,
  value_text  TEXT    NOT NULL DEFAULT '',     -- categorical / set member ('' for numeric)
  value_num   REAL,                             -- numeric / boolean
  movie_id    INTEGER NOT NULL REFERENCES cached_movies(tmdb_id) ON DELETE CASCADE,
  source      TEXT    NOT NULL,                 -- 'derived' | 'tmdb' | 'omdb' | 'llm' | 'tvtropes' | 'manual'
  confidence  REAL,                             -- 0..1 for semantic facets, NULL = exact
  PRIMARY KEY (facet_id, value_text, value_num, movie_id)
) WITHOUT ROWID;                                 -- the PK is the covering index for lookups
CREATE INDEX ix_movie_facets_movie ON movie_facets(movie_id, facet_id);

-- Tri-state bookkeeping: has a facet *family* been computed for this film, by which version?
CREATE TABLE movie_facet_status (
  movie_id    INTEGER NOT NULL REFERENCES cached_movies(tmdb_id) ON DELETE CASCADE,
  family      TEXT    NOT NULL,                 -- 'lexical', 'reception', 'production', 'semantic', 'tropes'
  version     INTEGER NOT NULL,
  status      TEXT    NOT NULL,                 -- 'ok' | 'unavailable' (source has nothing) | 'error' (retryable)
  computed_at TIMESTAMP NOT NULL,
  PRIMARY KEY (movie_id, family)
) WITHOUT ROWID;

-- New raw columns needed by facets (already present in fetched payloads, see V2-06).
ALTER TABLE cached_movies ADD COLUMN budget INTEGER;          -- NULL unknown, 0 = TMDB has none
ALTER TABLE cached_movies ADD COLUMN revenue INTEGER;
ALTER TABLE cached_movies ADD COLUMN collection_id INTEGER;
ALTER TABLE cached_actors ADD COLUMN deathday TEXT;           -- ISO date, filled when /person is read
ALTER TABLE cached_directors ADD COLUMN deathday TEXT;
```

Why EAV and not a JSON column or a wide table: a wide table needs a migration per facet
(the S8/S9/S10 pace would mean a migration per phase), and a JSON column cannot be indexed per key
without a generated column (again a migration each). EAV with a covering `WITHOUT ROWID` PK makes
`facet_id = ? AND value_num BETWEEN ? AND ?` and `facet_id = ? AND value_text = ?` index range
scans. Size estimate: ~40 rows/film → ~800k rows / ~35 MB for a 20k-film cache, which the existing
`cache_flush` VACUUM path already reclaims; the flush must delete facet rows with their movie
(the `ON DELETE CASCADE` above plus an explicit delete, because SQLite FK enforcement is per
connection).

**Facet declaration.**

```python
# backend/app/facets/registry.py (new package app/facets/)
@dataclass(frozen=True)
class Facet:
    id: str                        # 'title_word_count', 'cult_classic', 'trope'
    family: str                    # status bookkeeping unit
    kind: Literal["bool", "num", "cat", "set"]
    label: str; emoji: str
    ops: frozenset[str]            # 'is','eq','lt','le','gt','ge','between','in','has','has_any','has_all'
    needs: frozenset[str]          # raw inputs, for hydration decisions (S8 'needs' contract)
    version: int
    tier: Literal[0, 1, 2]         # 0 pure/inline, 1 cache-join, 2 enrichment (network/model)
    evaluate: Callable[[FacetInputs], list[FacetValue] | None]   # None = unknown
    order: Literal["none", "numeric", "lexical"] = "none"        # usable by sequence modifiers
    named_variants: dict[str, dict] = {}                          # 'short': {'op':'lt','value':90}
```

### 2.5 Facet catalogue (v4 scope)

**Lexical (tier 0, `family='lexical'`, computed inline in `cache_repo.upsert_movie` from `title`; pure functions in `utils/title_tokens.py`).**

| Facet | Kind | Notes |
|---|---|---|
| `title_first_letter`, `title_last_letter` | cat (`A`–`Z`, `#`) | Articles ignored (existing `first_letter`); drives A–Z **and** Z–A, plus new "Last-letter chain" (the next title starts with the previous title's last letter, a *pair* modifier). |
| `title_length` | num | Letters only, normalised; enables "Shortest to longest title" staircases both ways. |
| `title_word_count`, `one_word_title` | num, bool | Tokenised on the same normaliser as A–Z; hyphenated compounds count as one word. |
| `title_number` | set (num) | Existing `title_numbers` output, stored once; `allow_years` becomes a query parameter over a separate `title_year_token` facet. |
| `title_palindrome` | bool | Letters-only, ≥ 3 letters, case/diacritic-folded. |
| `title_has_subtitle`, `title_sequel_marker` | bool | Colon/dash subtitle; trailing numeral/Roman/"Part"; complements `collection_id`. |

**Temporal & production (tier 0/1, `family='production'`).**

| Facet | Kind | Source |
|---|---|---|
| `release_year`, `release_decade` | num | `release_date` (replaces `parse_release_year` in consumers). |
| `micro_era` | set (cat) | A versioned, region-aware table in code: `silent` (< 1929), `pre_code` (1929–1934, US), `golden_age` (1930–1959), `french_new_wave` (1958–1968, FR), `new_hollywood` (1967–1980, US), `hk_new_wave` (1979–1990, HK), `blockbuster` (1975–1999, US), `dogme` (1995–2005, DK), `streaming` (≥ 2013)… A film can sit in several. |
| `director_debut` | bool | Tier 1: true when the film is the earliest dated directed credit of any of its directors **and** that director's filmography is cached (a `cached_directors` row exists; presence = filmography fetched); otherwise unknown. Reuses Auteur Marathon's curation filters (no shorts/TV/docs). |
| `director_film_index` | num | Same evidence; enables "Sophomore film" and late-career facets. |
| `posthumous_release` | bool | Tier 1: a top-5 billed actor or a director has `deathday < release_date`. Unknown until those people's `deathday` is cached. |
| `in_collection`, `collection_id` | bool, cat | TMDB `belongs_to_collection`. |
| `runtime`, `runtime_band` | num, cat | `runtime_band` named variants unify "short"/"epic" (V2-08). |
| `origin_country`, `region`, `original_language` | set, set, cat | **One** region taxonomy (UN M49 subregions + "Western Europe"/"Anglosphere" convenience groups) replaces `WESTERN_COUNTRIES` and Bingo's sets. |
| `genre` | set (num) | Mirrors `genre_ids` for indexed `has_all`/`has_any`; one genre map (`facets/genres.py`) replaces the four copies. |
| `setting_year`, `setting_era` | num, cat | Existing `narrative_year`/`narrative_era_label` (Historical Time-Travel). |

**Reception (tier 0 on ratings refresh, `family='reception'`).**

| Facet | Kind | Definition (versioned) |
|---|---|---|
| `imdb_rating`, `tomatometer`, `metacritic` | num | Parsed **once** from `cached_movie_ratings` strings when ratings are upserted (fixes V2-03). |
| `rating` | num | The existing `rating_of` precedence (IMDb, else verified TMDB with `vote_count ≥ 10`). |
| `critic_audience_gap` | num | `tomatometer − imdb_rating × 10` (the RT Split convention). Positive = critics liked it more. |
| `cult_classic` | bool | `critic_audience_gap ≤ −20` **or** (`tomatometer < 60` and `imdb_rating ≥ 7.0`), with `vote_count ≥ 1000` and release ≥ 15 years ago. Unknown when any input is missing. |
| `critic_darling` | bool | `critic_audience_gap ≥ 20` and `tomatometer ≥ 85`. |
| `box_office_bomb`, `sleeper_hit` | bool | `budget ≥ 1M` and `revenue < 0.5 × budget`; `revenue ≥ 5 × budget`. Unknown when TMDB reports 0 for either (TMDB uses 0 for "not recorded"). |
| `popularity_percentile`, `vote_count_band` | num, cat | **Relative facets** are not stored: they are computed at query time (`NTILE` window over the cache) because they change as the cache grows. |

**Semantic & trope (tier 2, `family='semantic'` / `'tropes'`; uses only the existing JIT models).**

| Facet | Kind | Method |
|---|---|---|
| `trope` | set (cat), `confidence` | Union of `extracted_tropes` (Qwen, `source='llm'`), the TVTropes hybrid scraper (Step 4, `source='tvtropes'`) and player confirmations (`source='manual'`). Slugs are normalised against a curated `facets/tropes.py` alias table; the S10c Genre Gate and 0.85 provider-normalised confidence floor stay the acceptance test. |
| `vibe_valence`, `vibe_arousal` | num (−1..1) | **No new model.** Embed ~8 short anchor descriptions per pole ("a joyful, uplifting, warm story" vs "a bleak, tragic, despairing story"; "a frantic, explosive, high-stakes thriller" vs "a quiet, still, contemplative drama") with the configured local preset, store the pole centroids once per embedding fingerprint (`config_dir/facet_anchors/<fingerprint>.npy`), and project each film's existing `overview_embedding` onto the `(positive − negative)` axis, rescaled per preset (the `similarity_floor` lesson from 25a). |
| `vibe_quadrant` | cat | `euphoric` (+V +A), `serene` (+V −A), `tense` (−V +A), `melancholic` (−V −A); unknown when \|value\| < 0.1 on either axis. |
| `heaviness` | num (0..1) | Distance-based score used by the Vibe Controller (Step 6). |

### 2.6 Evaluation pipeline

| Tier | When | Budget | Writes |
|---|---|---|---|
| 0 (pure) | Inside `cache_repo.upsert_movie` and `upsert_ratings`, same transaction | microseconds | delete + insert the family's rows, `status='ok'` |
| 1 (cache-join) | After the inputs land (director credits, deathdays), and in the Data Spa sweep | per-request JIT ≤ 30 films (existing `HYDRATE_BUDGET` pattern) | same |
| 2 (enrichment) | Data Spa batches (Step 4) via `task_runner`; JIT only on explicit player action (as S10 tropes) | rate-limited, resumable | rows with `source`/`confidence`; `status='error'` is retryable, never cached as false |

A single `facets.refresh(session, movie_ids, families)` entry point recomputes stale families
(`status.version < Facet.version`). Changing an input column (title, overview, ratings) clears the
dependent family's status, mirroring `upsert_movie`'s existing feature invalidation.

### 2.7 Injection into the S8 Predicate Registry

1. **`FacetQuery` AST** (`app/facets/query.py`, new): `{"all": [...]}` / `{"any": [...]}` /
   `{"not": q}` / leaf `{"facet": id, "op": op, "value": v}`. Pydantic-validated against the
   catalogue (unknown facet or op → 422). Evaluated with Kleene three-valued logic
   (AND: any False → False, else any None → None, else True).
2. **`FacetPredicate`** implements the existing `Predicate` protocol (`id`, `label`, `emoji`,
   `needs`, `difficulty`, `params`, `ranges`, `check`). Consumers that take a `Predicate`
   (bounties, Chaos, Rabbit Hole decks, feasibility, AI bounties) work **unchanged**. The 10 legacy
   ids become aliases (`year_lt` → `{"facet":"release_year","op":"lt"}`) in a behaviour-neutral
   port, then the inverses (`year_gt`, `rating_gt`, `popularity_gt`) come free (fixes V2-01).
3. **SQL compiler.** `compile(query) -> (sql, params)` emits one `EXISTS (SELECT 1 FROM
   movie_facets …)` per leaf, combined with AND/OR/NOT; an unknown-count companion query joins
   `movie_facet_status`. `feasibility.pass_rate` becomes a `COUNT` over the compiled SQL scoped to
   the candidate universe, replacing the whole-cache `Evidence` load (fixes V2-04). Python
   `check()` remains for single films and for facets that are relative.
4. **Measured difficulty.** `difficulty = clamp(1 + round(-log2(pass_rate)), 1, 6)` from the
   compiled count, cached per request (fixes V2-07). Decks, Chaos and bounty draws sort by it.
5. **Catalogue API.** `GET /api/facets` (id, label, emoji, kind, ops, named variants, cache
   coverage %) and `POST /api/facets/count` (`{query, scope?: run_id}` → `{matches, unknown,
   pass_rate, sample[≤6]}`), cache-only and time-boxed. These feed the custom bounty editor,
   Rabbit Hole deck preview, Bingo board generator and Tug plane balance check.

**How each system consumes it ("Cult Classic + One-Word Title + Horror"):**

```json
{"all": [{"facet": "cult_classic", "op": "is", "value": true},
         {"facet": "one_word_title", "op": "is", "value": true},
         {"facet": "genre", "op": "has", "value": 27}]}
```

| System | Change |
|---|---|
| Procedural Rabbit Hole decks (S11) | The deck builder draws **compositions** of 1–3 facet leaves, rejecting any whose compiled pass-rate is outside the tier's band (Tier 2 ≈ 30%, Tier 6 ≈ 3%) on both the cache and the reachable pool; seeds stay deterministic (`rh_seed`). New `rh_rules_version = 3`; v1/v2 decks read their stored predicate ids through the alias table. |
| Chaos handicaps | Each handicap is a named facet query (adds inverses: "Modern only", "Crowd-pleaser", "English only", plus "One-word titles", "Cult classics"). The S8b fairness gate (feasible and non-trivial on the actual Pick Next pool) is unchanged. |
| Custom AI Bounties | `normalize_rule` accepts `{"facet", "op", "value"}` leaves whitelisted by the catalogue (max 3), keeping the legacy condition types as aliases. The LLM prompt lists the 25 best-covered facets for the run's universe with their coverage; acceptance stays "feasible and non-trivial", then the static fallback. |
| Bingo | Squares become server-defined facet queries (`GET /tools/bingo/squares`), deleting `lib/bingo.ts` predicates (fixes V2-02 drift). |
| Tug planes (Step 1) | `PoleSpec.match` is a `FacetQuery`; the balance check is two compiled counts. |
| Modifiers | A generic `SequenceModifier(facet, direction ∈ {asc, desc}, strict, mode ∈ {increasing, count})` replaces the bespoke `alphabet_run` / `ascending_numbers` / staircase logic (aliases keep stored keys). Any `order != "none"` facet gets both directions by construction: Count Down, Title-length staircase, Rating climb, Into Obscurity (popularity descent). History folding reads stored facet values instead of re-tokenising titles. |
| Pick Next `FilterSpec` | A `facet` source lets engines declare facet chips; unknown values keep the S4 "?" chip convention. |
| Grid Crawler (Step 6) | Cell conditions are facet queries. |

### 2.8 Step 2 summary

| ID | Sev | Remedy |
|---|---|---|
| V2-01 | P1 | Ordered facets + `SequenceModifier` with both directions; inverse predicate aliases |
| V2-02 | P1 | One catalogue (`app/facets/`); Bingo/AI bounty/Chaos/Rabbit Hole/Tug consume it |
| V2-03 | P1 | Typed, indexed `movie_facets` rows computed once |
| V2-04 | P1 | Compiled SQL counts replace the whole-cache `Evidence` scan |
| V2-05 | P2 | `movie_facet_status` (ok/unavailable/error + version) |
| V2-06 | P2 | Persist budget/revenue/collection/deathday from payloads already fetched |
| V2-07 | P2 | Measured difficulty |
| V2-08 | P2 | Named variants own shared concepts and thresholds |

---

## Step 3 — Core UX Loop: "Up Next" Queue & Ubiquitous Detail

### 3.1 Point: RT Split and Regional Deep Dive can't queue, and their cards open nothing

**RT Split.** `routes_runs.create_step` (the `split` branch) raises
`422 "Log a split film as watched, with the household's rating (1-100)"` for any
`status != "watched"`. `MarkWatchedRequest` (`schemas/runs.py`) carries only
`acting_participant_id`, `watched_at` and `user_notes`, so even if a planned split step were
allowed, the planned → watched transition could not carry the `household_score` that
`RottenTomatoesSplitEngine.settle` needs. `SplitBoard.tsx` therefore offers only "Log Movie"
(→ `HouseholdRatingModal`), and its pool cards have no queue action.

**Regional Deep Dive.** `ExpeditionBoard.tsx` *does* have a Queue button (`LogFilmButtons`), but a
queued film then renders a static `✓ Queued` label: **no Mark Watched, no Unqueue, no delete**.
Because `RunDetailPage` swaps the whole `ChainTimeline` out for the board on Regional, Method
Actor, Auteur Marathon, March Madness and RT Split, the timeline's Mark Watched / Delete Step /
notes controls (which live in the step-bound `MovieDetailModal`) are unreachable. The only way
out of "Queued" on these boards is Table Mode's 10-second Undo toast. The player perceives this
as "there is no queue": the queued state is a trap, so nobody uses it.

| ID | Sev | Finding |
|---|---|---|
| V3-01 | **P0** | On `CareerTrack`, `AuteurTrack` and `ExpeditionBoard`, "Queued" is a terminal UI state: the backend supports `PATCH …/mark-watched` and step deletion, but no control reaches them. A queued film can never be logged from the board. |
| V3-02 | **P1** | RT Split has no Stage 1 at all: planned steps are rejected and `MarkWatchedRequest` cannot carry `household_score` / `no_contest`, so the settlement can't move to the watch moment. |
| V3-03 | **P1** | Poster/title clicks on every board (`SplitBoard`, `ExpeditionBoard`, `CareerTrack`, `AuteurTrack`, `BracketView`) open nothing: no plot, cast, Radarr/Seerr acquisition or Jellyfin status before committing to a film. |

### 3.2 Vertical: the two-stage lifecycle across all modes

Stage 1 = a queued candidate (`RunStep.status = "planned"`); Stage 2 = log/rate/score
(`watched`, plus mode settlement such as Tug pull, split point, bounty award).

| Mode(s) | Stage 1 entry | Stage 1 visibility | Stage 1 → 2 | Detail on click |
|---|---|---|---|---|
| CineChain & graph modes (Chrono, Passport, Auteur Relay, Crew & Craft, Pendulum, Semantic, Aesthetic, Historical, Canon Island, Rabbit Hole, Tug) | Pick Next "Queue Up Next", direct search, Bridge "Queue to Active Run" | Planned steps on `ChainTimeline` | Timeline "Mark as Watched" / `MovieDetailModal` | Timeline: `MovieDetailModal` (step-bound). Pick Next: its own in-hub movie screen with `AcquisitionControl` |
| Meet in the Middle | Pick Next per side | `TunnelTimeline` planned badge | Tunnel card actions | **None** (`TunnelTimeline`, `TunnelFrontierCard` have no detail hook) |
| Method Actor / Auteur Marathon | Board "Queue" | Sky tint in a long list | **None (V3-01)** | **None** |
| Regional Deep Dive | Board "Queue" | Sky tint in a rank-ordered grid | **None (V3-01)** | **None** |
| RT Split | **Not allowed (V3-02)** | — | — | **None** |
| March Madness | No queue (winner logged watched on advance) | — | Advance/vote | Matchup card has acquisition (Phase 4), no full detail |
| Roulette / Blind Draft | "Plan for Later" | Timeline | Timeline | Masked by design until reveal |
| Decade Sieve | Direct search | Timeline | Timeline | Timeline modal |
| Tools (Daily Bridge, Router, Bingo, Bridge page) | n/a (Router: "Queue as Challenge Run") | — | — | Bridge page only (`MoviePreviewModal`) |

**Three different detail views exist:** `MovieDetailModal` (requires a `RunStep`; notes, watched
date, mark-watched, delete, acquisition, Jellyfin; used only by `ChainTimeline`),
`MoviePreviewModal` (takes a `movieId`; acquisition, Jellyfin, tagline; used only by
`BridgePage`), and the `PickNextHub` movie screen (a third implementation). Of the 26 components
that render `MoviePoster`, **only three** reach any detail view.

| ID | Sev | Finding |
|---|---|---|
| V3-04 | **P1** | Detail is coupled to *where* a film is shown, not to the film. There is no movie-id-keyed detail entry point usable from any surface, so each new board re-implements (or omits) acquisition, Jellyfin and plot display. |
| V3-05 | **P1** | Board modes lose step management entirely (notes, watched-date edit, delete/undo), because those controls only exist inside the step-bound modal opened from `ChainTimeline`. |
| V3-06 | **P2** | Queue vocabulary drifts: "Queue Up Next" (Pick Next), "Queue" (boards), "Plan for Later" (Roulette, Blind Draft), "Queue Bridge to Active Run", "Queue as Challenge Run" (a different thing: creates a run), chips "Queued" vs "Planned". |
| V3-07 | **P2** | No mode surfaces an explicit **Up Next** slot. Planned steps are only distinguished by tint inside the timeline/board, so "what are we watching tonight?" is not answerable at a glance, and on graph modes the planned film silently becomes the frontier. |

### 3.3 Horizontal: interaction contracts are per-component

The same class of problem (a cross-cutting interaction implemented per surface) also appears in:
validation pre-flight (each board calls `/runs/{id}/validate` itself, e.g. `ExpeditionBoard.log`),
skip-overlay confirmation (re-implemented in Pick Next, direct search and Expedition), and
acquisition (Bracket, Pick Next, both modals). Phase 0 fixed this for primitives (`Modal`,
`Popover`); v4 needs the same treatment for **movie actions**.

| ID | Sev | Finding |
|---|---|---|
| V3-08 | **P2** | Logging, queuing, pre-flight validation and skip confirmation are re-implemented per board. A single `useLogFilm(run)` action hook would make Stage 1/Stage 2 behave identically everywhere. |

### 3.4 Design: the universal two-stage loop

**Backend.**
1. **Planned steps are universal.** `create_step` accepts `status="planned"` for every engine
   that logs films (RT Split included). Mode settlement that needs player input moves to the
   planned → watched transition:
   `MarkWatchedRequest` gains `household_score: int | None (1–100)` and `no_contest: bool`. The
   split engine's checks (`settle`, no-contest stamping) move into a shared
   `_settle_on_watch(run, step, payload)` called by both `create_step` (watched now) and
   `mark_watched`/`PATCH step` (later). Existing settlement keys stay server-owned; a forge test
   covers settlement on the transition path.
2. **Queue semantics are declared per engine:** `queue_policy: ClassVar[Literal["frontier",
   "slot", "none"]]`. `frontier` (graph modes): a planned film *is* the next frontier (today's
   behaviour). `slot` (boards/trackers/Split): planned films are an **Up Next shelf** that does
   not move any frontier or fold until watched (the Tug v3 "watch order is game order" lesson
   already stamps fold time at the watch moment). `none` (March Madness: the bracket is the
   queue). `/engines` publishes it.
3. **Unqueue** = `DELETE /runs/{id}/steps/{step_id}` restricted to planned steps when the run's
   engine is board-based (no rewind side effects), so boards don't need the full Undo flow.

**Frontend.**
1. **`UpNextShelf`** (new): a compact strip above every board/timeline listing planned steps
   (poster, title, "Mark watched", "Unqueue", detail). It is the single Stage 1 surface; Stage 2 is
   its "Mark watched" (opening `HouseholdRatingModal` for Split, the Table Mode seat for shared
   devices). Boards replace the terminal `✓ Queued` label with the same two actions (fixes V3-01).
2. **`useLogFilm(run)`** (new hook): queue, watch, mark-watched, unqueue, pre-flight
   `/validate`, overlay-skip confirmation and Table Mode `actingFields()` in one place; the boards,
   Pick Next and direct search call it (fixes V3-08).
3. **One detail surface.** Merge `MovieDetailModal` and `MoviePreviewModal` into
   **`MovieDetailSheet`** keyed by `movieId`, with an optional `step` (adds the notes / watched-date
   / delete section) and an optional `actions` slot (Queue / Log / mode-specific buttons provided by
   the caller via `useLogFilm`). It owns plot, tagline, cast strip, ratings, canon badges,
   `AcquisitionControl` and Jellyfin. `PickNextHub`'s movie screen renders the same body inline.
4. **Ubiquitous posters.** A global `useMovieDetail().open(movieId, {step?, actions?})` store
   (zustand, like `activeRunStore`) and a `PosterButton` wrapper: `MoviePoster` gains `movieId`
   and becomes a button that opens the sheet. **Concealment is explicit**: a `concealed` prop
   (Blind Draft, Roulette before reveal, Tagline Roulette, Rabbit Hole fog cards, Grid Crawler fog
   cells) keeps the current non-interactive mask. A guard test lists every `MoviePoster` call site
   without `movieId` and requires either `movieId` or `concealed` (fixes V3-03/V3-04).
5. **Vocabulary.** One verb pair everywhere: **"Queue"** (Stage 1) and **"Log watched"** (Stage 2);
   chips read "Up next" / "Watched". "Queue as Challenge Run" becomes "Start a run from this"
   (fixes V3-06).

### 3.5 How-to-Play guidelines (bite-sized, dynamic, jargon-free)

Evidence: S3 shipped server rulebooks (`engines/rulebook.py`, `RuleSection` per engine,
`HowToPlay.tsx` card/drawer/full modal). Static sentences average ~10 words, but the *dynamic*
strings are long (Tug's v3 `tug_scoring` is ~45 words) and the **global glossary leaks
implementation terms** to every player: "V3 moves the rope 2…; legacy raids…", "v2 Sudden Death
concedes 1", "force a soft violation", "Hard mode rules and modifiers cannot be bought".

| ID | Sev | Finding |
|---|---|---|
| V3-09 | **P2** | Version and engine jargon (`V3`, `v2`, `legacy`, `soft violation`, `modifiers`, `overlay`) appears in player-facing copy for runs where it does not apply. |
| V3-10 | **P2** | Rules are static per configuration; nothing tells the player what matters **right now** (whose turn, what the next tier is, which mechanic just became available). Players read the manual instead of being taught in context. |

**Guidelines (enforced by a backend guard test over every engine's rendered rulebook):**

1. **Three layers, progressively disclosed.** *Card*: one goal sentence ≤ 14 words.
   *Drawer*: "Your turn" ≤ 3 bullets, each ≤ 16 words, imperative verb first. *Full rules*: only on
   demand; still no bullet over 25 words.
2. **One idea per bullet.** Split compound rules ("Build …; Raid …; Bank …") into separate bullets
   with an emoji anchor matching the in-game chip (🔥 Build, ⚔️ Raid, ⚓ Bank).
3. **No implementation vocabulary.** A banned-term list in the guard test: `v1`, `v2`, `v3`,
   `legacy`, `server`, `metadata`, `predicate`, `overlay`, `modifier`, `soft violation`, `fold`.
   Glossary entries become **variant-selected** server-side (`glossary(rules)`), so a v3 Tug run
   never sees v2 text.
4. **Use the run's real values and real films.** Keep S3's `format_map` values, and add example
   slots filled from the current pool ("e.g. *Heat* → *Collateral* via Tom Cruise").
5. **Teach just-in-time, not up front.** A server `GET /runs/{id}/coach` (cache-only) returns at most
   one **"Right now"** line derived from state (Tug: "Ben's streak is ×3; a Raid breaks it";
   Rabbit Hole: "Tier 3 starts next hop: non-English only"; Bounty: "*Heat* would complete Epic
   Odyssey"). The drawer shows it first; a one-shot toast introduces each mechanic the first time it
   becomes relevant (seen keys per mechanic, extending S3's per-mode first-visit storage).
6. **Show the consequence, not the formula.** Pick Next chips already show the effect
   ("⚔️ Raid +2"); rulebooks point at the chip rather than restating arithmetic.
7. **Readability budget.** Guard test computes words/sentence and a syllable heuristic
   (no new dependency); fails above grade ~8 for card/drawer layers.

### 3.6 Step 3 summary

| ID | Sev | Remedy |
|---|---|---|
| V3-01 | P0 | Board Stage 1 → 2 actions via `UpNextShelf` + `useLogFilm` |
| V3-02 | P1 | Planned Split steps; settlement on `mark-watched` |
| V3-03 | P1 | `PosterButton` + `MovieDetailSheet` everywhere except `concealed` |
| V3-04 | P1 | Single movie-id-keyed detail surface |
| V3-05 | P1 | Step section (notes/date/delete) inside the sheet for any surface |
| V3-06 | P2 | "Queue" / "Log watched" vocabulary |
| V3-07 | P2 | `queue_policy` + Up Next shelf |
| V3-08 | P2 | `useLogFilm` shared action hook |
| V3-09 | P2 | Banned-term guard, variant glossary |
| V3-10 | P2 | `/coach` "Right now" line + per-mechanic just-in-time tips |

---

## Step 4 — The Data Spa, Sync Resilience & TVTropes Hybrid Scraper

### 4.1 Point: curated list syncs fail on missing TMDB details

**Trace.** `POST /curated/sync/{list_id}` scrapes the list (`letterboxd.scrape_letterboxd_list`
→ `_scrape_paginated` → `enrich_entry`). `enrich_entry` resolves an id from the inline
`data-tmdb-id`, the film page (deep mode, which also reads the IMDb id), or
`resolve_tmdb_multipass` (exact year → ±1 year → title only → slug query).
`_persist_sync_result` then keeps only `matched = [f for f in films if f.get("tmdb_id")]`, and
`_queue_canon_hydration` starts the `canon_hydrate` task.

| ID | Sev | Finding |
|---|---|---|
| V4-01 | **P0** | `canon_hydrate` is **all-or-nothing**: after `RegionalDeepDiveEngine._hydrate`, any film without `origin_country` or a decade raises `RunSetupError("Indexed 98/100 films; some TMDB details are unavailable…")`, so the whole task is `failed` and the list shows an error even though 98% of it is usable. Legitimate causes (a film TMDB files with an empty country list, an undated festival entry, a TV item) make the failure permanent: "sync again to retry" can never succeed. |
| V4-02 | **P1** | `tmdb_type` is parsed by the scraper (`enrich_entry`, `extract_deep_metadata`) but **consumed nowhere else**. A Letterboxd entry that resolves to a TMDB *TV* id is persisted as a `CanonMovieBadge.movie_id` and later fetched as `/movie/{id}`: a 404 (feeding V4-01) or, worse, an unrelated film that happens to share the numeric id. |
| V4-03 | **P1** | The resolver has no **normalised-title** pass (diacritics, `&`/`and`, punctuation, leading articles, Roman/Arabic numerals) and no **IMDb-id** pass (`/find/{imdb_id}?external_source=imdb_id`) even though deep mode already scrapes the IMDb id (`_imdb_id_from`). Unmatched entries are silently dropped by `_persist_sync_result`: no record, no count shown, no retry. |
| V4-04 | **P1** | `_persist_sync_result` deletes every badge of the list and **commits**, then inserts the new badges and commits again. A crash or exception between the two commits leaves an enabled list with zero films, and the previous good snapshot is gone (Phase 4 fixed exactly this pattern for watchlists). |

### 4.2 Vertical: sync and task visibility

| Layer | Today | Gap |
|---|---|---|
| `services/task_runner.py` | `submit_task` → `SystemTask` row, ≤ 2 concurrent (`_slots`), dedupe key, throttled progress, restart → `failed` (`fail_interrupted_tasks`), 7-day prune | No cancel, no resumable *cursor* convention (scrapers checkpoint ad hoc via `CheckpointManager`), no notion of a recurring maintenance job, no per-provider budget (OMDb's daily cap). |
| `api/routes_tasks.py` | `GET /tasks`, `GET /tasks/{id}`, SSE `/tasks/stream` (1 s SQLite poll per connection) | Fine as a transport. |
| Frontend `lib/tasks.ts` | `useLiveTasks` (only `TasksPanel`), `useTrackedTask` (resume on mount: only `WatchlistSyncCard`, `ImportHistory`), `runTask/waitForTask` (component-local polling in `CuratedCanonsCard`, `CuratedListCard`) | **No global indicator.** `layouts/AppLayout.tsx` has no task awareness. Leaving Settings > Integrations aborts the local wait; returning does not resume list syncs; the only place to see progress is Settings > Tasks & Logs. `TASK_TITLES` covers 3 of the 8 task names (`canon_hydrate`, `diary_import_csv`, `diary_import_rss`, `passport_backfill_directors`, `llm_model_download` fall back to raw ids). |

| ID | Sev | Finding |
|---|---|---|
| V4-05 | **P1** | Background work is invisible outside the component that started it. Navigation hides progress, completions are silent, and failures surface only on the Tasks page. |
| V4-06 | **P2** | Each opened `EventSource` re-reads SQLite every second; a naive per-component indicator would multiply that. One app-level connection must be shared. |

### 4.3 Point: Semantic Trope run seeded with *The Fabelmans* → 0 tropes, empty Pick Next

**Trace.** `discover_next_movies` (`routes_runs.py`) → `engine.discover_with_modifiers` →
`MutatorEngine.discover_candidates` → (standalone) `SemanticTropeEngine.discover_rule_candidates`:

1. `prepare([frontier])` embeds the overview (local ONNX JIT: needs the model file; first use
   downloads it) and `prepare_tropes([frontier])` extracts tropes **only if the LLM provider is
   on** (`llm.extract_tropes` returns `[]` when `off`, the default) and the S10c guard accepts them.
2. **If the frontier has neither a usable embedding nor tropes, it returns `[]` immediately.**
3. Otherwise the pool is: cached films already embedded *by the same model fingerprint* + one TMDB
   `recommendations/similar` call (`get_related_movies`), hydrated within `HYDRATE_BUDGET` and
   embedded within `POOL_FEATURE_BUDGET`, then thresholded by `violation`.

So a household with the LLM off and either an offline/undownloaded ONNX model, a fresh preset
(new fingerprint → no previously embedded rows match), or a frontier whose TMDB related list is
thin gets **zero candidates with no explanation**. The UI then prints `PickNextHub`'s generic
"No films match these filters." even though no filter is active.

| ID | Sev | Finding |
|---|---|---|
| V4-07 | **P1** | Trope evidence is never hydrated just-in-time for a seed when the LLM is off, and there is no non-LLM source of tropes, so "0 tropes" is the default state for most households. |
| V4-08 | **P1** | `discover_next_movies` has **no dry-pool fallback**: when the engine returns too few candidates it does not widen (more TMDB pages, discover by the frontier's genres/keywords, a second related page) and does not say why. The empty list is indistinguishable from "filters removed everything". |
| V4-09 | **P2** | The discover response is a bare `list[DiscoveryCandidate]`; there is no envelope for diagnostics (`pool_size_before_filters`, `reason`, `widened`), so the UI cannot explain an empty pool. |

### 4.4 Horizontal: missing-data handling is "fail" or "silently drop"

The same class (a missing third-party fact either aborts a batch or silently removes the item)
appears in: `canon_hydrate` (abort), list persistence (drop unmatched), Semantic pools (return
`[]`), Regional slices (S1 made them non-empty, but only for already-cached facts), RT Split
pools (empty until OMDb rows exist; `/split-pool?scan=N` is a manual crawl), Bingo hydration loop,
and Rabbit Hole `constraint_unverified`. S2 established "never persist a transient failure"; v4
needs the complementary rule: **partial success is success, recorded with per-item status, and
the gaps are visible and retryable**.

| ID | Sev | Finding |
|---|---|---|
| V4-10 | **P1** | There is no cache maintenance surface. Missing runtimes, countries, ratings, directors, embeddings and tropes are only filled JIT by whichever feature trips over them, within per-request budgets; nothing fills them proactively, so the same gaps recur in every mode. |

### 4.5 Design: resilient Letterboxd matching

`services/tmdb_resolver.py` (async, already shared with diary import) becomes the **single**
resolver; the sync scraper calls it through a thin sync adapter instead of
`resolve_tmdb_multipass` (removes the duplicated ranking path).

| Tier | Input | Method | Accept when |
|---|---|---|---|
| 0 | inline `data-tmdb-id` + `data-tmdb-type` | trust, but **respect type**: `tv` entries are stored as unmatched with reason `tv_title` (never as a movie id) | always |
| 1 Exact | title + year | `/search/movie?query&primary_release_year` | `rank_candidates` top score ≥ 0.92 and year equal |
| 2 Normalised | `normalize_title()` (NFKD fold, strip punctuation, `&`↔`and`, leading article move, Roman↔Arabic numerals) ± 1 year | same search with the normalised query | score ≥ 0.85, \|Δyear\| ≤ 1, director tie-break as today |
| 3 Search | title only, then the slug query (non-Latin titles) | existing passes | score ≥ 0.80 and (director match or unique candidate) |
| 4 IMDb | `imdb_id` from the deep page (fetched lazily only for entries that failed 1–3) | `/find/{imdb_id}?external_source=imdb_id` → `movie_results[0]` | exact |

Every entry gets a persisted outcome: new table `curated_list_entries` (`list_id, position,
slug, title, year, tmdb_id NULL, match_tier, status ∈ matched|unmatched|tv_title|ambiguous,
reason, attempted_at`) (new, one migration). Persistence becomes **one transaction**: write entries
and badges, then delete the badges that are no longer present (fixes V4-04); a failed scrape keeps
the previous snapshot. `canon_hydrate` becomes **partial-success**: per-film outcomes are counted
(`indexed`, `no_country`, `undated`, `not_found`, `transient`) and stored in
`progress_data.result`; the task fails only on a systemic error (auth, no key, every request
transient). Slices simply exclude films they cannot place (fixes V4-01/V4-02). The list card shows
"97 matched · 2 unmatched · 1 TV title — Review" with a manual "Match…" picker (search → set
`tmdb_id`, `status=matched`, `match_tier='manual'`).

### 4.6 Design: global background-task indicator

- **One connection.** `TaskStreamProvider` (new) at the `AppLayout` root owns the single
  `EventSource('/api/tasks/stream')` and the React Query `TASKS_KEY` cache (today's
  `useLiveTasks` logic moves here; `TasksPanel` and `useTrackedTask` read from it). Polling
  fallback stays at 15 s.
- **`TaskIndicator`** (new) in the top bar: hidden when idle; a spinner with a count while any of
  the user's tasks is active; a popover listing each active task (title from an exhaustive
  `TASK_TITLES` map, guarded by a backend test that every `submit_task` name has a title), progress
  bar (existing `TaskProgressBar`), elapsed time and a "View" link to the originating page
  (`progress_data.link`, set by `submit_task(…, link=…)`).
- **Completion toasts.** When a task transitions to `completed`/`failed` during the session, a
  toast reports the outcome ("Sight & Sound 2022 synced: 247 films, 3 need review").
- **Resume everywhere.** `useTrackedTask` resumes by `dedupe_key` for list syncs as well, so
  returning to a card re-attaches to its running task.
- **Cancel.** `POST /tasks/{id}/cancel` sets a `cancel_requested` flag on the row; `TaskContext`
  exposes `ctx.cancelled()`; long loops check it between items and stop at a checkpoint. No
  thread killing, no broker.

### 4.7 Design: the Data Spa (cache maintenance)

A new **Settings > Data Spa** page (admin) shows cache health and runs batched, resumable,
rate-limited repair jobs, all through `task_runner` (no daemon, no cron: jobs run when an admin
starts them, and each run processes a bounded batch).

**Health dashboard** (`GET /api/system/cache/health`, SQL counts only):
coverage per field family over reality-eligible cached films (runtime, country, language,
directors, cast, OMDb ratings, budget/revenue, overview embeddings for the active fingerprint,
trope evidence, facet families from Step 2 by version), plus "films referenced by runs / canon
lists / watchlists" so the most valuable gaps are fixed first.

**Treatments** (each a `SystemTask` kind with a `dedupe_key`, a batch cap and a cursor):

| Treatment | Selects | Calls | Budget / pacing |
|---|---|---|---|
| `spa_details` | stubs missing runtime/country/language/budget | `TMDBClient.get_movie` | existing global TMDB pacing + `fetch_with_backoff`; batch 200 |
| `spa_people` | movies missing directors/cast; people missing `deathday` | `/credits`, `/person` | same; batch 200 |
| `spa_ratings` | films with `imdb_id` and no / expired ratings | OMDb by id | **daily budget** (`omdb_daily_budget`, default 900 of the free 1000) tracked in a `provider_budgets(provider, day, used)` row; stops cleanly when spent |
| `spa_embeddings` | overviews without a vector for the active fingerprint | `embed_batch` (JIT ONNX, unloaded after the batch) | batch 256 |
| `spa_facets` | facet families with stale versions (Step 2) | none (tier 0/1 pure) | batch 2,000 |
| `spa_tropes` | films without trusted trope evidence | TVTropes hybrid (below) and/or Qwen | scraper pacing below; LLM batch 20 (local Qwen loaded once per batch, unloaded after) |

Priority order within each treatment: films in active runs → canon lists → watchlists → most
popular. Each item records success/`unavailable`/`error` in `movie_facet_status` (Step 2), so a
transient failure is retried next time and a genuine absence is not re-requested for 30 days.
The "Fix all" button chains treatments in one task (respecting `MAX_CONCURRENT_TASKS = 2`).
A per-run "Prepare this run" action runs the same treatments scoped to the run's pool, which is
how Semantic Trope / RT Split / Regional runs get warmed before play.

### 4.8 Design: TVTropes Hybrid Scraper

**Source constraints (checked against `https://tvtropes.org/robots.txt` during this audit):** the
site is Cloudflare-managed and declares `Content-Signal: search=yes, ai-train=no` for `*`, with
`ai-input` neither granted nor restricted. Therefore:

- **Never train** on scraped content (we don't; no fine-tuning exists in CineChain).
- **Ingest only trope page identifiers** (`/pmwiki/pmwiki.php/Main/<TropeName>` links listed on a
  film's work page) and the trope's display name — not article prose. Validation embeds the
  *trope name plus our own curated one-line definition*, not TVTropes text.
- **Opt-in, disclosed, household-local.** An admin setting (`tvtropes_enabled`, default off) with
  the robots/content-signal note and an attribution link on every trope chip ("via TV Tropes").
  Verify the site's content licence and terms at implementation time; if they forbid this use,
  ship the pipeline with only the LLM + manual sources.
- **Polite fetching.** Identifying User-Agent, robots.txt honoured at runtime, ≥ 8 s between
  requests, ≤ 1 concurrent, ≤ 150 pages per Spa batch, `ETag`/`Last-Modified` revalidation and a
  90-day cache of the extracted link list in `config_dir/tvtropes/` (raw HTML discarded after
  parsing). A Cloudflare challenge or 403/429 **stops the batch** (`CloudflareBlock` pattern) — no
  evasion beyond what the existing Letterboxd client does for ordinary pages.

**Pipeline (`services/tvtropes.py`, new; reuses `curl_cffi` session helpers from `letterboxd.py`):**

1. **Resolve the work page.** Candidate URLs from the title (`Film/<CamelCaseTitle>`,
   `Film/<CamelCaseTitle><Year>`) and TVTropes' own search page; accept a page whose header year
   matches the TMDB release year ± 1. Store the mapping (`tvtropes_work_url`) or `unavailable`.
2. **Extract.** Collect `Main/<Trope>` links from the trope list sections; normalise
   CamelCase → kebab slug.
3. **Map to the curated taxonomy.** `facets/tropes.py` holds a curated alias table (TVTropes
   slug ↔ CineChain trope slug, e.g. `TimeLoop` → `time-loop`) plus our own one-line definitions
   and S10c genre requirements. Unmapped slugs are kept with `source='tvtropes'`,
   `confidence=None` and are **not** used for gameplay until validated.
4. **Validate locally (no new model).** For each candidate trope: cosine of the film's overview
   embedding against the embedded trope definition, normalised per preset (`normalize_similarity`);
   accept if ≥ the S10c 0.85 floor **or** if the Qwen judge (when on) answers a constrained yes/no
   prompt ("Does this plot contain <definition>? yes/no"). The S10c Genre Gate still blocks
   genre-exclusive tropes on conflicting genres. Accepted rows land in `movie_facets`
   (`facet_id='trope'`, `source='tvtropes'`, `confidence`).
5. **Merge.** The `trope` facet is the union of validated TVTropes, Qwen and manual tags;
   gameplay (`SemanticTropeEngine._shared_trope`, bounties, Grid Crawler) reads the facet instead
   of `extracted_tropes` directly. `extracted_tropes` stays as Qwen's raw cache.

### 4.9 Design: `discover_next_movies` JIT fallback

1. **Envelope.** `GET /runs/{id}/discover?envelope=1` returns
   `{candidates, diagnostics: {engine_pool, after_modifiers, after_filters, widened: [..], reason}}`;
   the bare list stays the default for one release (S2 compatibility precedent).
2. **Dry-pool ladder** (in `BaseChallengeEngine.discover_with_modifiers`, only when
   `len(pool) < DRY_POOL_MIN = 8`, within a shared 12 s deadline and the existing hydrate budgets):
   (a) engine-specific widen hook `widen_pool(frontier, rules, history, step)` — Semantic: next
   `recommendations` page, then `/discover/movie` with the frontier's top two genres and its TMDB
   keywords; graph modes: raise `cast_limit` by 10 and fetch the uncached filmographies of the next
   actors; Passport/Chrono: two more sampled countries/decades; (b) facet-driven cache query for
   the engine's rule (Step 2 compiled SQL); (c) stop and report. Each rung that ran is listed in
   `diagnostics.widened`.
3. **Semantic seed JIT.** `validate_candidate`/`create_run` for Semantic Trope run the
   `spa_tropes` + `spa_embeddings` treatments synchronously for the seed only (bounded to that one
   film), so the first Pick Next has evidence; if the embedding model is unavailable the setup
   returns the actionable error "Download the embedding model in Settings > AI & Embeddings"
   rather than an empty pool later.
4. **UI.** `PickNextHub` distinguishes "your filters hid N films" (with Clear filters) from "the
   engine found nothing" (shows `diagnostics.reason`, a "Search wider" button that calls the
   ladder explicitly, and a "Prepare this run in the Data Spa" link).

### 4.10 Step 4 summary

| ID | Sev | Remedy |
|---|---|---|
| V4-01 | P0 | Partial-success `canon_hydrate` with per-film outcomes |
| V4-02 | P1 | Respect `tmdb_type`; TV entries recorded, never stored as movies |
| V4-03 | P1 | 5-tier resolver incl. normalised title + IMDb `/find`; `curated_list_entries` |
| V4-04 | P1 | Single-transaction persistence keeping the last good snapshot |
| V4-05 | P1 | `TaskStreamProvider` + `TaskIndicator` + completion toasts + resume |
| V4-06 | P2 | One shared SSE connection |
| V4-07 | P1 | Seed JIT trope/embedding preparation + TVTropes source |
| V4-08 | P1 | Dry-pool widening ladder |
| V4-09 | P2 | Discover diagnostics envelope |
| V4-10 | P1 | Data Spa health + treatments + per-run "Prepare" |

---

## Step 5 — Rabbit Hole UX & JIT Cache Resolution

### 5.1 Point A: the run creator spoils (and misstates) the descent

**Trace.** `lib/gameModes.ts` gives the Rabbit Hole card a static
`progression: ["Freefall", "Pre-2000", "Non-English", "Under 100 min", "B-movies"]`, rendered by
`GameModePicker` as tier chips. That is the **v1** `TIERS` schedule. Since S11 every new run is
`rh_rules_version = 2` with a seeded, feasibility-checked `tier_deck` of 4–6 jittered predicates,
so the card advertises tiers the player will usually not meet. Inside the run, `RabbitHoleHud`
renders the whole deck (names and params) and the full `tier_deck` is part of the `rules_config`
returned by `GET /runs/{id}`, so the future is public from Depth 0.

| ID | Sev | Finding |
|---|---|---|
| V5-01 | **P1** | The creator's static preview is the legacy v1 schedule: it is wrong for v2 decks and removes the roguelike unknown. |
| V5-02 | **P2** | There is no hidden-information layer: the HUD shows every future tier, and even a hidden HUD would leak through `rules_config.tier_deck`, Pick Next's `upcoming_tier_warning` text ("Tier 3 (Non-English) begins…") and `/constraint`. |
| V5-03 | **P2** | The frontend mirrors the v1 schedule (`lib/rabbitHole.ts#RABBIT_TIERS`) and the warning window (`WARNING_WINDOW = 2`) by hand. |

### 5.2 Point B: Tier 2 candidates show "Rule unverified" although their runtime is cached

**Trace.** Pick Next → `GET /runs/{id}/discover` → `RabbitHoleEngine.discover_with_modifiers`:

1. `RabbitHoleEngine.discover_candidates` builds the cast pool (often 900+ films), then
   `_hydrate_pool` fetches full detail for at most `HYDRATE_BUDGET = 30` of the most popular
   films that need it within `HYDRATE_SECONDS = 20`. It then computes each verdict once:
   `candidate.tier_compliant = compliance(...)`, `candidate.constraint_unverified = verdict is None`.
2. `BaseChallengeEngine.filter_by_modifiers` runs **after** that and calls `_hydrate_pool` again
   with the remaining budget; it can fill a film's runtime but never revisits the tier verdict.
3. Back in the route, `pool_options.shape_pool` (Chaser/Underdog) may fetch **another** 30 details
   (its own `HYDRATE_BUDGET`), and the loop then sets `candidate.runtime = row.runtime` from the
   now-hydrated cache.

The response can therefore contain a card with `runtime: 84` *and*
`constraint_unverified: true` for a "< 90 min" tier. The same happens across requests: opening a
film's detail (`GET /movies/{id}` hydrates it JIT) or any other feature caches its runtime, but
the cached Pick Next response (React Query key `["runs", id, "discover", frontier, mode, …]`)
keeps the stale flag, and the client has no way to evaluate the rule itself because the tier's
predicate is not sent in an evaluable form.

| ID | Sev | Finding |
|---|---|---|
| V5-04 | **P1** | Verdicts are computed **before** later hydration passes in the same request and are never recomputed, so known facts and "unverified" flags contradict each other on the same card. |
| V5-05 | **P1** | The client cannot resolve a rule at render time: the tier rule reaches the UI only as prose (`tier_rule`, `upcoming_tier_warning`), and candidates lack some facts a rule may need (`original_language`, the effective `rating`). |
| V5-06 | **P2** | Unverified cards beyond the 30-film hydration budget stay unverified until the pool is refetched; there is no way to verify just the cards the player is looking at. |

### 5.3 Vertical: who computes "unverified" and when

| Producer | Flag | Computed before later hydration? |
|---|---|---|
| `RabbitHoleEngine.discover_candidates` | `tier_compliant`, `constraint_unverified` | **yes** (V5-04) |
| `MutatorEngine._filter_pool` (Passport, Chrono, Historical, Semantic, Aesthetic, Pendulum) | `constraint_unverified = _needs_hydration(row)` | yes, then `filter_by_modifiers` may hydrate more |
| `BaseChallengeEngine.filter_by_modifiers` | `constraint_unverified = True` when modifiers lack data | last engine pass, but before `shape_pool` |
| `HistoricalTimeTravelEngine` pool | `constraint_unverified` when a setting year is unresolved | yes |
| `SemanticTropeEngine` | implicit (no score/tropes) | yes |
| Frontend `PickNextHub` | renders `Rule unverified` / `unverified`; `DirectorsPicks` excludes unverified from recommendations; `guaranteedConnected` | trusts the server flag |

### 5.4 Horizontal

Two classes recur across the codebase:

1. **Order-dependent annotation.** A per-candidate verdict is written in the middle of a
   multi-pass pipeline (engine → modifiers → pool options → route). Any later pass that learns new
   facts silently invalidates earlier verdicts. This affects every engine with a computed rule
   (table above).
2. **Hidden information is presentation-only.** Concealment is done in components (Blind Draft
   blur, Tagline Roulette masks, Daily Bridge's route reveal is the one server-enforced
   exception), while the API returns the full state. For Fog of War to mean anything the server
   must redact.

| ID | Sev | Finding |
|---|---|---|
| V5-07 | **P2** | No engine-level "public view" of rules: `RunDetail.rules_config` is the raw stored dict for every viewer. |

### 5.5 Design: Fog of War and the Periscope (opt-in)

**Creation input** (`rules_config["fog"]`, creation-only, validated): `"off"` (today), `"fog"` or
`"abyss"` (hardcore). It is opt-in: the default stays `"off"`, so existing and new runs behave as today unless the player chooses fog.

| Fog level | Current tier | Next tier | Later tiers | Warnings |
|---|---|---|---|---|
| `off` | full | full | full | today's 2-hop warning with the rule |
| `fog` | full | **silhouette** until 1 hop away: category emoji + difficulty pips (e.g. "⏱️ ●●○ — a time rule"), rule text revealed at the 1-hop warning | silhouettes ("?" + difficulty pips) | "Something changes in 2 hops" → rule at 1 hop |
| `abyss` | full | hidden until arrival | hidden | depth markers only ("Tier boundary in 2 hops") |

**Periscope** (server-owned resource `periscope_charges`): starts at 1 in `fog`, 0 in `abyss`; a new
relic kind `periscope` (+1 charge) joins S11's deterministic boundary relics when fog is on.
`POST /runs/{id}/rabbit-hole/periscope {depth}` reveals the tier starting at `depth` (must be a
future, unrevealed boundary; pre-checked before spending, LESSONS "pre-check before spending"),
appends it to `revealed_depths` (server-owned) and stamps nothing on steps (no fold impact).
Undo of the step that granted a periscope relic restores `rh_resources_before` as S11 already
does; a periscope already spent is not refunded (consistent with relic semantics).

**Redaction (fixes V5-02/V5-07).** New hook `BaseChallengeEngine.public_rules(rules, run) -> dict`
(default: identity) applied in `_to_run_detail`, `GET /runs/{id}/constraint`, the rulebook values
and the discover response. `RabbitHoleEngine.public_rules` replaces each unrevealed deck entry with
`{number, start_depth, hidden: true, emoji, difficulty}` and strips predicate params; curses on
unrevealed tiers are hidden the same way. `upcoming_tier_warning` and `RabbitHoleState.next_tier_name`/`next_tier_rule`
use the redacted text. Game Over (`status != active`) returns the full deck so
`RabbitHoleGameOver` can show "What lay below". Server-side validation still uses the stored deck,
so redaction cannot change legality. A forge test confirms `periscope_charges`,
`revealed_depths` and `fog` are server-owned after creation.

**Creator UX (fixes V5-01).** The mode card replaces the static chips with a **depth gauge**:
"Freefall → ? → ? → ? → ?" with difficulty heat increasing downwards and the copy "Your deck is
dealt when you start." With Fog off, the setup step shows a **live deck preview** generated by a
dry-run `POST /engine/rabbit-hole/preview {rules}` (same `draw_deck`, a throwaway seed, cache-only)
so the player sees a *representative* deck, labelled as such; Daily Dive previews today's deck
only when Fog is off. `RABBIT_TIERS` and `WARNING_WINDOW` move to `/engines` metadata (fixes V5-03).

### 5.6 Design: resolve cached rules at card-render time

1. **Annotate last, once (fixes V5-04).** Split verdicts from generation: engines keep generating
   and *filtering* pools, but per-candidate verdict fields are written by a single
   `engine.annotate_candidates(candidates, rules, history)` pass that `discover_next_movies` calls
   **after** `shape_pool` and after all hydration. Rabbit Hole's `discover_candidates` keeps its
   filter (drop `False` unless off-tier) but no longer writes `constraint_unverified`;
   `annotate_candidates` recomputes `compliance` from the current cache rows. `filter_by_modifiers`
   and `_filter_pool` follow the same rule.
2. **Ship the rule, not just the prose (fixes V5-05).** `ConstraintInfo` gains
   `rule_query: FacetQuery | None` (Step 2 AST; until the Facet Engine lands, the existing
   `predicate_data(test)` dicts, which are already JSON-serialisable). `DiscoveryCandidate` gains the
   facts any registered predicate needs: `original_language`, `rating` (the `rating_of`
   precedence), `release_year` and `runtime` (already present), `countries` (already present as a
   list since S2).
3. **Client evaluator.** `lib/ruleEval.ts` (new) evaluates the query three-valued against the
   candidate's facts **merged with any cached `useMovieDetail` data** for that film. The card shows
   ✓ / ✗ / ? from the client verdict, so a runtime learned by opening the detail sheet (or by any
   other query) resolves the chip instantly. The server remains authoritative at log time
   (`validate_primary` unchanged); a client/server disagreement is a test failure, not a gameplay
   path.
4. **Verify what is visible (fixes V5-06).** `POST /runs/{id}/verify-candidates {movie_ids ≤ 24}`
   hydrates only those films (shared budget/deadline, `fetch_with_backoff`) and returns their facts;
   `PickNextHub` calls it for the unverified cards on the current page (the S4 48-card page) and
   merges the facts into the discover query cache with `setQueryData`, without refetching the pool.
5. **Tests.** `test_rabbit_hole.py`: a candidate hydrated by `shape_pool` or `filter_by_modifiers`
   in the same request is annotated compliant; a cached-runtime candidate is never
   `constraint_unverified`; the redacted `rules_config` hides unrevealed tiers in `fog`/`abyss` and
   reveals all at Game Over; periscope spending is pre-checked and forge-proof. A parity test runs
   the Python predicates and a JSON fixture of `ruleEval` expectations on the same facts.

### 5.7 Step 5 summary

| ID | Sev | Remedy |
|---|---|---|
| V5-01 | P1 | Depth gauge + representative deck preview (Fog off) |
| V5-02 | P2 | `fog`/`abyss` levels, Periscope charges and relic |
| V5-03 | P2 | Tier/warning metadata from `/engines` |
| V5-04 | P1 | Single final `annotate_candidates` pass |
| V5-05 | P1 | `rule_query` + candidate facts + client `ruleEval` |
| V5-06 | P2 | `verify-candidates` for the visible page |
| V5-07 | P2 | `public_rules` redaction hook |

---

## Step 6 — Narrative Systems & New Game Modes

> **Scope note.** "The heaviness PID controller" does not exist in code. It is a Vault concept
> (`docs/planning/Future Game Modes & Engines Vault.md`, "The Vibe Controller (PID / Fatigue State
> Machine)"). Its shipped precursor is the **Chaser** (`services/pool_options.py`,
> `components/ChaserPrompt.tsx`, `lib/chaser.ts`), which is what this step audits. Grid Crawler,
> Connect the Canon and Canon Infiltration are likewise Vault concepts; the audit traces the
> existing components they would reuse.

### 6.1 Tale of the Tape (March Madness)

**Trace.** `POST /runs/{id}/bracket/commentary {matchup_id}` → `llm.generate_matchup_commentary`
with `COMMENTARY_SYSTEM` ("one punchy 'Tale of the Tape' sentence of at most 35 words"). Input is
`_card_blurb` per film (title, year, runtime, ≤ 120-char tagline, ≤ 300-char plot). The answer is
stored in `rules_config["bracket_commentary"][matchup_id]` (server-owned). With the model off,
the button is hidden.

| ID | Sev | Finding |
|---|---|---|
| V6-01 | **P2** | The output is one free-form sentence, so the "tape" carries no structured comparison: nothing about tone, era, reception, runtime or theme is computed; the 0.8B model is asked to invent the contrast from two plot snippets. Players experience it as a gimmick they click manually once per matchup. |
| V6-02 | **P2** | No grounding/validation: numbers or claims in the sentence are not checked against facts, and there is no non-LLM rendering, so households with the model off (the default) get nothing. |

**Horizontal.** Seven LLM features (`pitch`, `critic`, `teaser`, `extract_tropes`,
`generate_matchup_commentary`, AI bounties, narrative-era one-shot) each own a prompt, a cache key
scheme and a bespoke parser (`parse_tropes`, `parse_custom_bounty`, the era JSON parser). Only
bounties and tropes validate structure.

| ID | Sev | Finding |
|---|---|---|
| V6-03 | **P2** | No shared structured-generation path (schema, facts-only grounding, retry, deterministic fallback). Each new narrative feature re-learns the "0.8B models miss the format" lesson (27d). |

**Design: the Tape pipeline (`services/tale_of_the_tape.py`, new).**

1. **Facts (deterministic, cache-only).** Build a `TapeCard` per film from the Facet Engine
   (Step 2): year/decade/`micro_era`, runtime, `rating`, `critic_audience_gap`, `vibe_quadrant`,
   `heaviness`, top validated tropes, first country/language, director + `director_film_index`,
   canon badges, `box_office_bomb`/`sleeper_hit`. Missing facets are omitted, never guessed.
2. **Axis selection (deterministic).** Score candidate axes by normalised contrast:
   Tone (vibe quadrant distance), Era (year gap / 60), Reception (critic–audience gap difference),
   Scale (runtime + budget), Theme (trope Jaccard distance: *low* overlap is contrast, *high*
   overlap is a "mirror match"), Origin (country/language difference), Pedigree (canon rank,
   director career stage). Pick the **three** most contrasting axes plus one "common ground" axis
   when one exists. Seeded by `matchup_id` for stable output.
3. **Rendering.** `llm.generate_structured(schema=TapeOut, facts=…, system=TAPE_SYSTEM)` asks for
   `{"headline": str ≤ 20 words, "axes": [{"axis", "left", "right", "edge": "left|right|even"}]}`
   with exactly the chosen axes, giving the model only the facts table. **Validator:** axis names
   must match the request; any digit sequence must appear in the facts; no film/person names except
   the two titles; length caps; one retry; otherwise the **template renderer** fills
   "⏳ Era: 1974 vs 2019 — 45 years apart", "🎭 Tone: Melancholic vs Euphoric", etc. The feature
   therefore works with the model off (V6-02).
4. **Lifecycle.** Generated JIT when a matchup becomes *ready* (both slots filled) during the
   advance/vote request via that request's `BackgroundTasks` through `task_runner` (no cron), or
   on first open; stored as `rules_config["bracket_tape"][matchup_id] = {axes, headline, source:
   llm|template, version}` (server-owned, first writer wins, as today). `bracket_commentary` stays
   readable for v3 runs.
5. **UI.** `MatchupCard` shows a two-column "tape" (left film | axis | right film) with the
   headline above; no button press needed. Voting copy reads "Who takes the Era round?" for Table
   Mode partner votes.
6. **Shared infra (fixes V6-03).** `generate_structured(config, system, facts, schema, retries=1,
   fallback=callable)` in `services/llm.py`; pitch/critic/teaser/bounties/tropes migrate to it
   behaviour-neutrally.

### 6.2 The Vibe Controller (culturally neutral heaviness)

**Trace (precursor).** `pool_options.needs_chaser(runtime, genre_ids)` =
`runtime ≥ 135` **or** `Drama ∈ genres`; `pool_options.is_chaser` qualifies a film with
`runtime ≤ 95` **and** (Comedy or Animation). The frontend mirrors it in `lib/chaser.ts`.

| ID | Sev | Finding |
|---|---|---|
| V6-04 | **P1** | **Cultural bias by construction.** TMDB tags most non-US commercial cinema as Drama, and Indian popular cinema routinely runs 150+ minutes (song sequences, intermission structure), so nearly every Bollywood/Tollywood film "needs a chaser", while a bleak 90-minute English comedy never does. Genre and absolute runtime measure *format*, not *emotional weight*. |
| V6-05 | **P2** | The trigger is per-film and memoryless: three heavy films in a row and one heavy film look the same; there is no notion of accumulated fatigue, recovery or the household's own tolerance. |
| V6-06 | **P2** | Duplicated thresholds (backend constants vs `lib/chaser.ts`), the same drift class as V2-08. |

**Design: heaviness from semantic distance, controlled by a PID with hysteresis.**

*Measurement (`facets` family `semantic`, Step 2; no new model).*
- Two anchor centroids embedded once per embedding fingerprint with the configured local preset:
  **Tragedy** (≈ 10 short anchor descriptions: grief, terminal illness, war atrocity, despair,
  injustice, loneliness, abuse, collapse…) and **Spectacle/Levity** (joyful adventure, slapstick,
  musical celebration, heist caper, romantic comedy, family fun, superhero spectacle…). Anchors are
  written to be culture-neutral (no genre or country words) and versioned in
  `facets/anchors.py`.
- Raw score `d = cos(e, T) − cos(e, S)` on the film's existing `overview_embedding` (and, when a
  validated trope set exists, the mean of trope-definition embeddings, weighted 0.3).
- **Calibrated within the household's own cache:** `heaviness = percentile_rank(d)` among cached
  films sharing the embedding fingerprint (the relative-facet pattern), so the scale does not
  depend on how a preset clusters scores (25a anisotropy lesson) and does not import an external
  population's norms.
- **Fatigue (runtime) is relative, not absolute:** `length_load = clamp((runtime −
  median_runtime(original_language)) / 60, 0, 1)` from cache medians, so a 160-minute Hindi film
  is judged against Hindi-language norms. Total load per film:
  `load = 0.8 · heaviness + 0.2 · length_load`. Unknown embedding → `load = None` (contributes
  nothing; never assumed heavy).
- **Bias guard test:** on a fixture cache, the mean heaviness of `Drama`-tagged films per
  `original_language` must not differ by more than 0.15 from each other when their overviews are
  drawn from the same anchor distribution, and Drama tag alone must not predict heaviness above a
  set correlation.

*Control (`services/vibe_controller.py`, new, pure).* Per run, fold watched steps (oldest first):
- error `e_k = load_k − setpoint` (setpoint from the player's **comfort** setting: Gentle 0.45,
  Balanced 0.55, Brave 0.7);
- `P = e_k`; `I = Σ λ^(k−j) e_j` with decay `λ = 0.7` and anti-windup clamp `|I| ≤ 2`;
  `D = e_k − e_{k−1}`; `u = 0.6P + 0.3I + 0.1D`.
- **Hysteresis state machine:** `steady` → `fatigued` when `u ≥ 0.25` (or three consecutive
  films with `load ≥ 0.75`); `fatigued` → `recovering` after one film with `load ≤ setpoint − 0.15`;
  `recovering` → `steady` when `u ≤ 0.05`. Fold-derived, never stored as truth (Tug's "caches, not
  inputs" lesson); cached in `rules_config["vibe_state"]` (server-owned) for the UI.
- **Actuation:** `soft` (default): Pick Next re-ranks by `load` and shows the "🍵 Palate cleanser
  suggested" banner; `strict` (opt-in overlay, `film` scope in the S9 registry): while `fatigued`,
  films with `load > setpoint` are a hard block, unknown load allowed. It works as a **modifier**
  on any pool engine, replacing the Chaser trigger; `chaser=true` becomes "pool filtered to
  `load ≤ setpoint − 0.15` and below-median length for its language".
- **Explainability:** a `VibeMeter` shows the rolling load and the reason ("3 heavy films in a row;
  next pick should be lighter"), with the per-film load chip on candidate cards.

### 6.3 Grid Crawler (Bingo Mode)

**Existing pieces.** Watchlist Bingo (`/tools/bingo`) is a *tool*, not an engine: ~35 client-side
squares (`lib/bingo.ts`), board and stamps in `localStorage`, no server validation, no adjacency.
Feasibility (S8), the S9 bipartite matcher for "distinct films cover N requirements" and the
Facet Engine (Step 2) provide what a server-side board needs.

| ID | Sev | Finding |
|---|---|---|
| V6-07 | **P2** | Bingo's rules live in the browser, so a board can contain unwinnable squares, two devices disagree, and nothing can be scored, shared or made multiplayer. |

**Design: `GridCrawlerEngine` (`engines/grid_crawler.py`, new; a `TrackerEngine` with an optional
`LinkPolicy`).**

- **Board** (`rules_config["grid"]`, server-owned): `size` 5 (4–6 allowed), `cells[r][c] =
  {id, label, emoji, query: FacetQuery, difficulty, revealed}`, `grid_seed`. Generation draws
  cell queries from the facet catalogue (single facets and 2-facet compositions) with a difficulty
  gradient (edges easier, centre harder, or the reverse for "Siege" layout), using
  `random.Random(grid_seed)`.
- **Feasibility at generation:** every cell's compiled pass-rate in the universe (whole cache,
  watchlist, or a canon list) is between 2% and 60%; at least one **winning line** is coverable by
  **distinct** films (S9 bipartite matching over the cells' candidate sets); the free centre is
  optional. Boards failing this are redrawn (bounded attempts → actionable 422).
- **Movement:** the first film claims any cell on the start edge; each later film must claim an
  unclaimed cell **orthogonally adjacent** to a claimed cell (or to the *last* claimed cell in
  "Crawler" variant) and satisfy its query. Optional hop rule between consecutive films via the
  Step 1 `LinkPolicy` (`none` default, `shared_cast`, `attribute`). A film satisfying several
  adjacent cells lets the player pick which one (`grid_cell` in the step payload, validated).
- **Stamps:** `transition_metadata["grid_cell"]` (server-owned, validated) ; claims are folded from
  steps, so deleting a step frees the cell.
- **Victory variants:** `bingo` (any full row/column/diagonal), `crossing` (connected path from
  start edge to opposite edge, Hex-style), `blackout` (all cells). Fail: no adjacent claimable cell
  with any feasible film → offer a **Jump** (wildcard: claim any unclaimed cell) if budget remains.
- **Fog:** optional `fog: true` hides a cell's query until a neighbour is claimed (uses V5-07
  `public_rules` redaction).
- **Two players / Table Mode:** alternate claims; a claimed cell blocks the opponent; first
  completed line wins. Uses S7 acting-participant attribution.
- **Pick Next:** pool = union of the claimable cells' compiled queries (cache SQL) plus the link
  policy's pool when a hop rule is on; each candidate carries `grid_cells: [ids]` it would satisfy.
- Watchlist Bingo becomes a thin client over the same board generator (`universe=watchlist`,
  `mode=tool`), deleting the client predicates (V2-02).

### 6.4 Connect the Canon & Canon Infiltration

**Existing pieces.** `services/pathfinder.solve_bridge_bipartite` (bidirectional, time-boxed,
`exclude_movie_ids`, `min_hops`, disjoint-path preference), `constrained_pathfinder` (rule-aware
movie-level search), `daily_puzzle` (cached-graph BFS from a fixed seed, verified hop by hop with
`find_links`, par, share grid, anti-cheat lock on the solver), Meet in the Middle (`distance`,
hints with pre-checked spending), canon lists (`CanonMovieBadge`).

| ID | Sev | Finding |
|---|---|---|
| V6-08 | **P2** | Every "reach a target" mechanic (Daily Bridge, Meet in the Middle collision, Bridge Solver) re-implements target bookkeeping, par, distance and anti-cheat. None supports **multiple ordered targets** or a **target set**. |

**Shared service: `services/goal_graph.py` (new).** Multi-source / multi-target bounded BFS over
the *cached* movie↔person graph with a `LinkPolicy` (Step 1) for edges and an explicit
`max_depth` + `max_seconds` (LESSONS: searches stay time-boxed). Backward initialisation from a
**set** of targets is free in a bipartite BFS (seed the backward frontier with every target), so
"distance to any canon film" costs one search. It returns `distance`, one verified path and the
`par`. Daily Bridge's BFS and Meet in the Middle's `distance` move onto it behaviour-neutrally.

**Connect the Canon (`engines/connect_canon.py`, new; `CineChainEngine` + LinkPolicy).**
- Setup: 3 waypoint films (picked by the player, or 🎲 drawn from a chosen canon list with the
  same feasibility rule as S8 draws) in a fixed order (`ordered`, default) or `best_order`
  (evaluate the 3 orderings with cached leg distances; choose the shortest, like the Marathon
  Router's exact path).
- Creation computes each leg's `par` with `goal_graph` (cache-first, per-leg budget 6 s, the
  previous legs' intermediate films excluded so legs are disjoint). A leg with no cached route
  within `max_depth` 6 is reported as "unknown par" rather than rejected, because the live solver
  can still find it in play.
- Server-owned: `waypoints`, `legs[{from, to, par, reached_at_step}]`, `current_leg`. Logging the
  current target waypoint stamps `waypoint_reached` and advances the leg; the run completes when
  the last waypoint is reached. Score = Σ(hops − par) ("−2 under par").
- Distance indicator and hints reuse Meet in the Middle's pre-checked hint spending. The Bridge
  Solver is **locked for the active leg** unless `assist: true` (Daily Bridge anti-cheat pattern,
  same 403 code), so the route-finding stays the game.

**Canon Infiltration (`engines/canon_infiltration.py`, new).**
- Setup: a **B-movie seed** drawn from a facet query (`rating ≤ 5.5`, `vote_count ≥ 50`, or
  `box_office_bomb`, or the player's own pick) and a **target set** = a canon list (Sight & Sound
  by default). Creation requires `goal_graph.distance(seed, canon_set)` in `[2, hop_limit]` on the
  cached graph (`hop_limit` default 4, range 3–6); the dice draws only seeds that pass (S8: random
  draws consult feasibility). Unknown distance (thin cache) → the seed is offered only if the live
  solver confirms within its budget.
- Rules: shared-cast (or the chosen LinkPolicy) hops; the run **wins** when any logged film is on
  the target canon list within `hop_limit` hops ("Infiltrated *Tokyo Story* in 3 hops"), and
  **fails** when the hop budget is spent (a new `max_hops` fail type in `engines/conditions.py`,
  alongside the existing `max_wildcards_used` / `max_repeats_used` / `max_same_actor_links`). Optional `sprint` timer is display-only (no server clock).
- Pick Next ranks candidates by cached `distance_to_target_set` (one backward multi-target BFS per
  request, cache-only, 1.5 s like the S6 lookahead) and shows "~2 hops from the canon" chips; Fog
  of War (V5) can hide them.
- Server-owned: `target_list_id`, `hop_limit`, `infiltration_par`, step `infiltrated`.

### 6.5 Step 6 summary

| ID | Sev | Remedy |
|---|---|---|
| V6-01 | P2 | Facet-grounded multi-axis Tape |
| V6-02 | P2 | Validator + template fallback (works with the LLM off) |
| V6-03 | P2 | `llm.generate_structured` for every narrative feature |
| V6-04 | P1 | Semantic heaviness (tragedy vs spectacle centroids), household-calibrated, language-relative runtime |
| V6-05 | P2 | PID + hysteresis Vibe Controller as a modifier |
| V6-06 | P2 | Single source for chaser/vibe thresholds |
| V6-07 | P2 | Server-side Grid Crawler with feasible boards |
| V6-08 | P2 | `goal_graph` multi-target BFS; Connect the Canon; Canon Infiltration |

---

## Review: root causes & priority roll-up

| Root cause | Description | Findings |
|---|---|---|
| **R1 Inheritance as configuration** | Link rules, territories and capabilities are decided by which class a mode subclasses, not by declared data. | V1-01, V1-02, V1-03, V1-08, V1-09, V1-10 |
| **R2 Vocabulary sprawl** | Film properties, taxonomies (regions, genres) and thresholds are re-declared per feature, with frontend mirrors that drift. | V1-04, V1-06, V1-07, V2-01 … V2-08, V5-03, V6-06 |
| **R3 Behaviour coupled to presentation** | Queueing, logging, detail, step management and concealment are implemented per component; the API exposes raw state. | V3-01 … V3-08, V5-01, V5-02, V5-07 |
| **R4 Missing data fails or vanishes** | A missing third-party fact aborts a batch or silently drops an item; gaps are only filled JIT by whichever feature trips on them; background work is invisible. | V4-01 … V4-10, V5-06 |
| **R5 One-shot, order-dependent annotation** | Verdicts and territories are computed once, mid-pipeline or from denormalised columns, and are not recomputed when facts arrive. | V1-05, V5-04, V5-05 |
| **R6 Unstructured AI output** | Narrative features ask a small local model for free prose without facts, schemas or a non-AI fallback. | V3-09, V3-10, V6-01 … V6-05 |

**Priority roll-up.**
- **P0 (blocks play / corrupts state):** V3-01 (board "Queued" dead end), V4-01 (all-or-nothing canon indexing).
- **P1 (misleads or stalls):** V1-01, V1-02, V1-05, V1-09, V2-01 … V2-04, V3-02 … V3-05, V4-02 … V4-05, V4-07, V4-08, V4-10, V5-01, V5-04, V5-05, V6-04.
- **P2 (polish / extensibility):** the remainder.

**Sequencing principle for v4 (inherits v3's anti-paralysis rule):** first remove the dead ends
and lies (P0 + "unverified" contradictions), then make every film inspectable and every
background job visible, then build the shared Facet Engine that the new mechanics stand on, and
only then add new planes, fog and modes.

---

*End of audit. The phased plan is in [`V4_IMPLEMENTATION.md`](./V4_IMPLEMENTATION.md).*
