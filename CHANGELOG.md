# Changelog

Notable changes to CineChain are recorded here.

## [3.0.0] - 2026-10-06

### The Synergy Update

CineChain's complete S0–S11 overhaul makes movie-night challenges easier to start,
clearer to play, and fairer to finish — whether everyone has an account open or
one device is passed around the table.

### Setup without dead ends

- **Rules-aware seed dice:** Recommendations respect the selected mode and its
  rules. Modes that need no seed, a derived seed, or a pair of seeds now guide
  setup accordingly; illegal selections are blocked before unnecessary fetching.
- **Useful regional slices:** Regional Deep Dive offers only populated country
  and decade slices, with background indexing progress instead of empty choices.
- **One-tap difficulty:** Mode-specific presets tailor Rabbit Hole, Tug of War,
  Method Actor and Auteur challenges. Choose flexible marathon lengths and
  strict, relaxed or free ordering; endless marathons can be wrapped explicitly.

### Data resilience that keeps play moving

- **IMDb-first ratings:** Exact IMDb IDs take priority over title/year matching.
  Temporary OMDb failures no longer become permanent missing-rating results.
- **RT Split escape hatches:** Retry ratings when a provider recovers, or log a
  film as no-contest without stalling the game.
- **Consistent countries and provider errors:** Typed country lists and shared
  flag rendering replace raw JSON country labels. Clear TMDB failure responses
  and poster fallbacks make third-party outages less disruptive.

### Complete onboarding, right where you play

- **Server-sourced rulebooks:** Every game mode explains its goal, turn, scoring,
  failure conditions and strategic levers. Mode cards and run headers open
  How to Play without sending players away from their game.
- **Contextual glossaries:** Glossary chips explain effects such as Build, Raid
  and Bank; rulebooks also cover enabled bounties, Chaos, modifiers, Blind Fork
  and Golden Veto.

### A more focused Pick Next

- **Director's Picks:** Best match, Best for mode and Underdog highlight three
  useful starting points, with a Pick for me option when you want a surprise.
- **Mode-aware filters:** Country, decade, genre and other relevant controls
  follow the active game. Search further when a filtered cached pool runs dry.
- **Off-tier visibility:** Rabbit Hole can show films outside the current tier
  so players can deliberately weigh a life-spending escape against legal picks.
- **Fair bounties and Chaos:** Draws consult shared predicates and feasibility.
  Rewards match the mode's usable currency; impossible bounties expire and are
  replaced for free, with one free discard per run.

### Game logic built for strategy and shared movie nights

- **Tug of War V3:** An unclamped rope, per-team momentum, meaningful Build/Raid/
  Bank effects and round-aware Sudden Death replace turn-parity advantages.
  Decision Triad picks and cached opponent lookahead make trade-offs visible.
  Existing v1/v2 runs keep their original scoring.
- **Procedural Rabbit Hole:** New runs deal Freefall plus four to six
  difficulty-ordered, feasibility-checked tiers. Hard mode adds compatible
  persistent curses; boundary relics grant capped lives, free re-rolls or a
  one-hop skip of the newest curse. Re-rolls use the reachable frontier, spend
  tokens before lives, and undo restores the exact resource state.
- **Daily Dive:** A shared UTC-date seed offers a daily descent. Identical caches
  and rules produce identical decks; different household caches may select
  different feasible tiers. Insufficient evidence gives an actionable setup
  message instead of an unproven challenge. Legacy Rabbit Hole runs are unchanged.
- **Table Mode pass-and-play:** Choose the acting participant on one logged-in
  device. Seat indicators, private handovers, explicit attribution and per-player
  votes support Blind Fork, Golden Veto, brackets and Tug without confusing the
  authenticated account with the player taking a turn.

### Composable challenges and evidence-backed career stories

- **Modifier overlays:** Combine A–Z, Number in Title and Ascending Numbers with
  compatible modes rather than choosing a separate game. Dynamic setup controls,
  progress chips and wildcard preflight make requirements visible.
- **Method Actor and Auteur milestones:** Explore verifiable theatrical debuts,
  first leads where applicable, genre pivots, comebacks and language crossovers,
  with evidence popovers and honest missing-evidence fallbacks.
- **Personal career eras:** Group a track into player-named eras. Optional
  against-type enrichment uses the existing local embeddings only when evidence
  is available; keyword-based villain suggestions require player confirmation.
- **Semantic guardrails:** Genre compatibility and a strict confidence threshold
  reject conflicting or weak trope suggestions, including cached tags that
  cannot be verified. No new models or ML dependencies were added.

### Reliability and upgrading

- Server-owned rules and metadata are protected against forged state. Reversible
  resource accounting, legacy behavior tests and isolated browser checks back
  the new mechanics.
- Release verification: **1,294 passing backend tests**, one intentional legacy
  scoring xfail, clean Ruff lint/format checks and a successful TypeScript/Vite
  production build. Existing framework deprecations and build-size warnings
  remain; Alembic has exactly one head.
- **Back up your `/config` volume before upgrading.** The container applies
  migrations at startup, including the cached IMDb-ID field introduced during
  this overhaul. Keep the volume mounted to preserve users, runs and cached data.
- The release image is `ghcr.io/greywolf1499/cinechain:3.0.0`; `latest` continues
  to follow default-branch builds. Publication is handled by the GHCR workflow.
- Background work remains ephemeral and searches remain request-scoped and
  time-boxed. No Redis, Celery, cron, message broker or vector database is needed.
  The TVTropes Scraper Hybrid Pipeline remains future work, not part of this release.

## [1.3.0] - 2026-10-05

### A smoother start for every movie night

- **Two-step Run Creator:** Choose a game mode, then configure your run in a
  focused setup screen. Seed-film previews, mode-specific options, and explicit
  missing-requirement messages make starting a run easier.
- **Readable, accessible film details:** Expand long descriptions without losing
  your place, inspect clipped titles, and open pitches in portal-based popovers
  that no longer get cut off by surrounding cards.

### More rewarding competitive and co-op modes

- **Tug of War V2:** Steal territory, build streak momentum, and use neutral
  anchors to change the contest. Sudden death brings the end zones closer to
  prevent stalemates, while explicit team attribution supports shared-device
  play. Existing runs retain their original scoring.
- **Meet in the Middle:** Spend hint tokens for an actor or film bridge hint,
  track warmer/colder distance trends, and see near misses against the opposite
  frontier's history. Cached distance results and frontier-swap controls make
  navigating a co-op tunnel easier.
- **The Rabbit Hole:** Complete bounties to recover lives, sacrifice a life for a
  one-depth tier re-roll, or set an optional escape goal between depths 25 and 60.
  Zero-life dead ends offer an explicit "Accept your fate" action alongside
  manual search.

### Better connections to your film library

- **March Madness championship:** A champion banner celebrates the winner with
  Jellyfin playback when available or an acquisition action through configured
  Radarr/Seerr integrations. Library badges now appear throughout the bracket.
- **Persistent watchlist sync:** Letterboxd sync status, the last username, and
  in-flight progress survive navigation. Returning to the integrations screen
  resumes tracking an active sync.

### Reliability and maintenance

- Fixed concurrent cache-insert failures affecting Pick Next and improved
  country metadata on logged films.
- Tier 5 TMDB ratings with fewer than 10 votes, or no known vote count, are now
  unverified rather than falsely treated as qualifying B-movies. Cached IMDb
  ratings still take precedence.
- Added database migrations for persistent watchlist status and cached TMDB vote
  counts, strengthened server-owned game state, and expanded regression coverage.
- Standardized backend formatting and aligned backend/frontend package versions
  with the release tag.

### Upgrading

- Back up your `/config` volume before upgrading. The container applies database
  migrations at startup; keep that volume mounted to preserve users, runs, and
  cached data.
- The versioned image is `ghcr.io/greywolf1499/cinechain:1.3.0`. The `latest` tag
  continues to follow builds of the default branch.
- Searches remain request-scoped and time-boxed; background sync uses the existing
  ephemeral task runner without additional services.

[3.0.0]: https://github.com/greywolf1499/cinechain/compare/v1.3.0...v3.0.0
[1.3.0]: https://github.com/greywolf1499/cinechain/compare/v1.2.1...v1.3.0
