# Changelog

Notable changes to CineChain are recorded here.

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

[1.3.0]: https://github.com/greywolf1499/cinechain/compare/v1.2.1...v1.3.0
