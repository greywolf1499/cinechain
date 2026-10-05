# CineChain: Comprehensive UX, Logic, and Game-Theory Audit

**Role:** Principal Product Designer, Game Systems Architect, and Lead QA Engineer
**Date:** October 2026

This document presents a systemic, full-surface audit across the CineChain application via static codebase analysis. It identifies blind spots, UX friction points, logical deadlocks, and integration gaps across all primary subsystems.

---

## 1. Holistic User Journey & Navigation Gaps

### Navigation & State Flow
* **Run Creation Flow:** As noted in preliminary analysis, `RunsPage.tsx` relies on a monolithic `NewRunModal` containing 500+ lines of UI, causing severe cognitive overload and vertical scrolling fatigue.
* **Empty States & Loading:** The application lacks consistent empty states. For instance, when a user has no active runs or no curated lists synced, pages often display bare layouts without a clear call-to-action (CTA).
* **Back Navigation & Persistence:** Filtering and sorting states on pages like `BridgePage.tsx` and `MarathonRouterPage.tsx` often reset upon navigating to a `RunDetailPage` and returning, losing the user's context.

---

## 2. Game-Design Mechanics & Logic Impasses

### Tug of War: The Zero-Sum Deadlock
* **Diagnosis:** The engine (`tug_of_war.py`) assigns scores based purely on metadata (e.g., era or geography). It ignores *which* player logged the step (`step.logged_by_user_id`). This allows a $+1/-1$ periodic deadlock where players alternate scoring for their own team, resulting in infinite oscillation without progress. Neutral films score 0, acting as wasted turns.
* **Resolution Mechanics Needed:**
  * **Invasion Steal:** Picking in the opponent's territory steals a point.
  * **Streak Momentum:** Consecutive scoring picks for the same team yield escalating points ($+1, +2, +3$).
  * **Neutral Anchor:** Neutral films (e.g., 1975-2005) grant a $2\times$ multiplier for the *next* scoring pick.
  * **Sudden Death:** After 12 steps, point weights increase to ensure termination.

### Meet in the Middle: The Bridge to Nowhere
* **Diagnosis:** `meet_in_middle.py` requires players to find a path between two disparate films. The `collides` function relies on a fast, time-boxed BFS (`DISTANCE_MAX_DEPTH = 5`). If players pick obscure films, they can easily get stuck with no viable shared actors within a reasonable depth, leading to frustration and abandoned runs.
* **Resolution Mechanics Needed:**
  * **Bridge Hints:** Spend a "token" to reveal a connecting actor or genre that lies on the shortest path.
  * **Frontier Swap:** Allow a player to laterally shift their frontier to a co-star without advancing the collision depth, opening up new branches.

### Rabbit Hole: The Unforgiving Abyss
* **Diagnosis:** At deep tiers (e.g., Tier 5: The B-Movie Abyss, rated < 6.0), finding a film that *also* shares a credited actor with the previous film is exceptionally difficult. If a user runs out of lives (`DEFAULT_LIVES = 3`), the run is failed (`RUN_STATUS_FAILED`).
* **Resolution Mechanics Needed:**
  * **Bounty Board Integration:** Allow users to earn extra lives by completing side-quests (e.g., "Watch a film from 1920").
  * **Sacrificial Re-roll:** Allow burning a life to change the current tier requirement for one step.

### Historical Time-Travel: Temporal Dead Ends
* **Diagnosis:** The strict "must be set later" (Forward) or "earlier" (Backward) rule (`historical_time_travel.py`) creates logical dead ends. If a user reaches a film set in 3000 AD, finding a cast-linked film set in 3001+ AD is nearly impossible.
* **Resolution Mechanics Needed:**
  * **Temporal Loop (Wormhole):** If a user hits a boundary (e.g., future sci-fi or ancient history), allow a "Wormhole" jump to the opposite end of the timeline at the cost of a temporary constraint (e.g., next film must be a specific genre).

---

## 3. Information Density & UI Truncation

### Aggressive CSS Text-Clamping Locations
A codebase scan identified numerous instances where `line-clamp` and `overflow: hidden` sever AI pitches, synopses, and user reviews without an expansion toggle. This is particularly problematic on living-room displays where users sit far away and rely on d-pad/remote navigation.
* **Affected Components:**
  * `MovieDetailModal.tsx` (`line-clamp-4`)
  * `MoviePreviewModal.tsx` (`line-clamp-6`)
  * `PickNextHub.tsx` (`line-clamp-3`)
  * `BracketView.tsx` (`line-clamp-5`)
  * `ForkOfferPanel.tsx` (`line-clamp-4`)
  * `BlindDraft.tsx` (`line-clamp-4`)
  * `CuratedListCard.tsx`
  * `BridgeSwapPanel.tsx`
  * `TunnelTimeline.tsx`
  * `GameModePicker.tsx`
  * `RouletteSpinner.tsx`
  * `BridgePathView.tsx`
  * `MarathonRouterPage.tsx`
  * `ChainTimeline.tsx`
  * `DailyBridgePage.tsx`
  * `CuratorsPage.tsx`
  * `PitchButton.tsx` (popover bounded by `w-60` without expansion)

### Screen-Space Utilization
* **Living-Room Readability:** On large screens, standard cards feel disproportionately small, leaving massive gutters. Conversely, dense modals (like the Run Creator) fail to utilize grid layouts effectively, resulting in long vertical scrolls.

---

## 4. Integration Continuity

### Watchlist Syncing Disconnects
* **Diagnosis:** As noted, `CuratedCanonsCard.tsx` uses ephemeral React state (`watchlistSyncedAt`) which resets on navigation. There is no backend endpoint (`GET /watchlist/status`) to retrieve the actual sync state from the database, leaving the UI showing "Never Synced" despite a successful Letterboxd sync in the background.

### Media Server Availability (Jellyfin/Radarr)
* **Diagnosis:** End-of-run screens and brackets lack integration visibility. In `BracketView.tsx` (March Madness), the `Podium` and `MatchupCard` components do not render `OnServerBadge` or `AcquisitionControl`. Users cannot tell if a contender is available on Jellyfin before voting, nor can they immediately request the Champion on Radarr.

---

## 5. Conclusion & Next Steps
The application has a strong foundational architecture, but suffers from game-theoretic deadlocks in edge-case states and significant UI cramping in discovery and configuration flows. The master implementation blueprint must be expanded to address these systemic issues across 5 phased rollouts.
