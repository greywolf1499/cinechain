# CineChain UX & Game-Systems Implementation Blueprint

**Status:** Ready for execution · **Date:** 2026-10-05
**Source of truth:** [`MASTER_UX_AUDIT.md`](./MASTER_UX_AUDIT.md) (verified findings). This plan supersedes the earlier unverified draft of this file.
**Finding IDs** (`P1-01` … `P4-06`) refer to the severity tables in the master audit.

---

## 0. Global rules for every phase

1. **Read first:** `docs/STATE.md` and `docs/LESSONS.md` before starting a phase. Update both when the phase lands.
2. **Platform constraints:** no Redis, Celery, cron or vector DB. Background work runs only through the existing ephemeral `task_runner` (FastAPI `BackgroundTasks`). No new ML models. All searches stay in-request and time-boxed.
3. **Server-owned state:** every new key the server computes goes into `SERVER_OWNED_METADATA` (`backend/app/api/routes_runs.py` L108) or `SERVER_OWNED_RULES` (`backend/app/services/blind_fork.py` L18) **in the same commit**, with a forge-attempt test (pattern: `tests/test_fork_veto_tug.py::test_clients_cannot_forge_a_pending_fork_or_scores`).
4. **Pre-checks before spending:** any token/life/hint spend validates every precondition first, so a refused action costs nothing (the veto pattern from LESSONS 24b).
5. **Validation per phase:**
   - Backend: `just backend-test` (`uv run pytest -q`) and `just backend-lint` (`uv run ruff check .`). For migrations: one Alembic head (`uv run alembic heads`).
   - Frontend: `just frontend-build` (`tsc && vite build`). There is no frontend unit-test runner; type-check and build are the gate, plus a manual click-through checklist.
6. **One conventional commit per phase/sub-phase** (`feat(ux): …`, `fix(tug): …`), each independently revertable.

### Dependency graph

```
            ┌──────────────────────────────┐
            │ Phase 0: Shared UI Primitives │
            └──────┬───────────┬───────────┬┘
                   │           │           │
        ┌──────────▼──┐ ┌──────▼──────┐ ┌──▼──────────────┐
        │ Phase 1     │ │ Phase 3     │ │ Phase 4         │
        │ Run Creation│ │ Global UI   │ │ Integrations    │
        └─────────────┘ └─────────────┘ └─────────────────┘

        ┌──────────────────────────────────────────────┐
        │ Phase 2: Game Logic (2a Tug │ 2b MitM │ 2c RH) │  ← no dependency on anything
        └──────────────────────────────────────────────┘
```

Phases 1, 3 and 4 depend **only** on Phase 0, never on each other. Phase 2 and its three sub-phases are fully independent and can ship in any order, in parallel with everything else.

---

## Phase 0: Shared UI Primitives (foundation, ~1 day)

**Goal:** build, once, the primitives that Phases 1, 3 and 4 consume, so those phases never block on each other.
**Findings enabled:** P1-02, P1-04, P2-01…P2-05, P4-04, P4-06.

| File | Component / function | Change |
|---|---|---|
| `frontend/src/components/ui/ExpandableText.tsx` **(new)** | `ExpandableText` | Props: `text`, `lines` (2–6, static `CLAMP` class map), `fallback`, `className`, `as`, `lead`, `moreLabel`/`lessLabel`, `expandMode: "inline" \| "dialog"`, `dialogTitle`. Measures overflow with `useLayoutEffect` + `ResizeObserver` (`scrollHeight > clientHeight + 1`) and only renders the toggle when the text is actually clamped. The toggle is a `<button aria-expanded aria-controls>` with `focus-visible:ring` and a ≥ 32 px hit target. `dialog` mode reuses `Modal`. |
| `frontend/src/components/ui/ClampedLabel.tsx` **(new)** | `ClampedLabel` | `line-clamp-N break-words` and always `title={text}`. |
| `frontend/src/components/ui/Popover.tsx` **(new)** | `Popover` | Portal to `document.body`. Positioned from `anchorRef.getBoundingClientRect()` with flip (top/bottom) and an 8 px viewport shift. Repositions on capture-phase scroll and resize. Closes on outside `pointerdown`, Esc and `focusout` of anchor+panel. `role="dialog"`, `max-h-[50vh] overflow-y-auto`, `max-w-[min(90vw,24rem)]`. No new npm dependency. |
| `frontend/src/components/Modal.tsx` | `Modal` | Add optional `footer?: ReactNode` (rendered outside the scroll body) and `bodyClassName?: string` (defaults to `max-h-[70vh] overflow-y-auto px-5 py-4`). Backwards compatible. |
| `frontend/src/components/EmptyState.tsx` | `EmptyState` | Add optional `action?: { label: string; onClick: () => void; icon?: LucideIcon }` rendered as a primary button. |
| `frontend/src/lib/queries.ts` | `useJellyfinLookup(tmdbIds: number[])` **(new)** | `useQuery({ queryKey: ["jellyfin","lookup",tmdbIds], queryFn: POST /integrations/jellyfin/lookup, enabled: tmdbIds.length > 0 })`. Same key shape as the 5 inline copies, so caches are shared. |

**Acceptance criteria**
- `ExpandableText` shows no toggle for short text and shows one for long text, at both 1280 px and 1920 px widths. Keyboard: Tab focuses the toggle, Enter expands it, `aria-expanded` flips.
- `Popover` stays fully on-screen when its anchor is in the right-most grid column and when the anchor is inside an `overflow-x-auto` scroller.
- `Modal` and `EmptyState` callers that don't pass the new props render exactly as before.

**Validate:** `just frontend-build`.
**Commit:** `feat(ui): add ExpandableText, ClampedLabel, Popover primitives and Modal/EmptyState slots`

---

## Phase 1: Critical Run Creation (2-Step Creator)

**Depends on:** Phase 0 (`Modal.footer`, `EmptyState.action`, `ExpandableText`, `useJellyfinLookup`).
**Findings:** P1-01, P1-02, P1-03, P1-04, P1-05, P1-06.

### 1.1 Files & functions

| File | Component / function | Change |
|---|---|---|
| `frontend/src/components/run-creator/useRunDraft.ts` **(new)** | `useRunDraft()` | One `useReducer` replacing the 24 `useState`s of `NewRunModal` (`RunsPage.tsx` L115–L137). Moves `formRules` (L163), `missingMode` (L189), `sameSeeds`, `selectMode` (L198), `toggleRawMode`, `toggleParticipant`, `reset` (L219) and the `handleSubmit` payload builder (L245) **verbatim**. Adds `blockers: string[]` with human-readable reasons ("Name the run", "Pick 16 bracket films (9/16)", "Choose a canon list", "Partners need different seeds", "Fix the JSON: …"). |
| `frontend/src/components/run-creator/NewRunWizard.tsx` **(new)** | `NewRunWizard` | `Modal` with `widthClassName="max-w-6xl"`, `bodyClassName="max-h-[calc(100vh-10rem)] overflow-y-auto"`, a stepper header (① Mode → ② Setup) and a `footer` holding `CreateBlockers`, Back, Next/Create. Owns `useCreateRun`. Esc on a dirty Step 2 asks for confirmation. |
| `frontend/src/components/run-creator/Step1ModeSelect.tsx` **(new)** | `Step1ModeSelect` | Category chips (All · Cast Chains · Rule Chains · Versus/Co-op · Survival · Trackers & Tournaments) and the mode-card grid. Enter / double-click selects and advances. The cards show **no** modifier drawer. |
| `frontend/src/components/run-creator/Step2RunSetup.tsx` **(new)** | `Step2RunSetup` | `grid lg:grid-cols-[minmax(0,1fr)_420px]`. **Left:** selected-mode header (full description, "Change" link), run name, `ParticipantPicker`, `ModeConfigPanel`, and an "Advanced rules" `<details>` holding `ModeOptions` + `RulesetFields` + Bounty Board + admin Raw JSON. **Right:** `HeroSeedPreview` (×2 for `meet_in_the_middle`). |
| `frontend/src/components/run-creator/ModeConfigPanel.tsx` **(new)** | `MODE_CONFIG` registry + `ModeConfigPanel` | `Record<game_type, FC<ModeConfigProps>>`. Modes without inputs render "No extra setup needed". |
| `frontend/src/components/run-creator/mode-config/*.tsx` **(new)** | `TugConfig`, `SplitConfig`, `CanonListConfig`, `DecadeConfig`, `BracketConfig`, `ActorConfig`, `DirectorConfig`, `DiveConfig`, `PendulumConfig` | Extracted 1:1 from `RunsPage.tsx` L319–L513. `CanonListConfig` and `DiveConfig` share a `CanonListSelect` whose empty state links to Lists (`EmptyState` with `action`). `TugConfig` copy fixed: neutral films score nobody. |
| `frontend/src/components/run-creator/ParticipantPicker.tsx` **(new)** | `ParticipantPicker` | Pill toggles. Keeps insertion order. For `tug_of_war`/`rt_split` it shows "You → Team A · <first pick> → Team B". |
| `frontend/src/components/run-creator/HeroSeedPreview.tsx` **(new)** | `HeroSeedPreview` | Wraps `SeedMoviePicker`. With a film chosen it calls `useMovieDetail(tmdb_id)` and renders a large `MoviePoster`, title/year/runtime, flag, `MovieTagline`, `RatingBadges`, `OnServerBadge` (via `useJellyfinLookup`) and the overview through `ExpandableText lines={5}`. **Empty state:** a dashed panel whose primary CTA is "🎲 Recommend Seed Movie", with search as the secondary option. |
| `frontend/src/components/run-creator/CreateBlockers.tsx` **(new)** | `CreateBlockers` | Renders the first blocker inline and the rest in a `title` tooltip. |
| `frontend/src/components/run-creator/shared.tsx` **(new)** | `Field`, `inputClass`, `DECADES`, `TRACKER_RULES` | Moved out of `RunsPage.tsx` (L627–L649). |
| `frontend/src/components/GameModePicker.tsx` | `GameModePicker` | Add `showModifierDrawer?: boolean` (default `true`, so legacy behaviour is untouched). The wizard passes `false`. Export `MODE_ORDER`. |
| `frontend/src/lib/gameModes.ts` | `GameModeStyle`, `GAME_MODE_STYLES` | Add `category` per mode and export `MODE_CATEGORIES`. |
| `frontend/src/pages/RunsPage.tsx` | `RunsPage`, delete `NewRunModal` | Render `<NewRunWizard>`. `EmptyState` gets `action={{ label: "Start your first run", onClick: open }}` and mode-neutral copy. Run cards show a mode chip from `gameModeStyle(run.game_type)`. |

### 1.2 Invariants (regression checklist, from master audit §1.6)
Modifiers are cleared on mode change · trackers send `TRACKER_RULES` · `modifierPayload` is merged last · Raw JSON is admin + `json_rules` only (MitM still needs both seeds) · `tail_seed_movie_id` is sent only for MitM and differs from the head seed · no server-owned keys are built on the client · participant order decides Team B · closing resets the draft.

### 1.3 Acceptance criteria
- From an empty Runs page, "Start your first run" opens the wizard.
- On a 1080p screen, every mode's configuration plus the seed preview is visible in Step 2 with at most one scroll. The Create button is always visible (footer).
- The disabled Create button always shows at least one blocker reason.
- Choosing a seed shows synopsis, runtime and ratings without leaving the wizard.
- Create a run for each of the 20 modes. Payloads are byte-identical to the pre-refactor `NewRunModal` for the same inputs (compare in the DevTools network tab).

**Validate:** `just frontend-build`, then the manual 20-mode creation pass.
**Commit:** `feat(runs): 2-step run creator wizard with hero seed preview and explicit blockers`

---

## Phase 2: Game Logic & Stalemate Resolution

**Depends on:** nothing. The sub-phases 2a/2b/2c are mutually independent.
**Shared backend touch-point:** `routes_runs.py` `_enforce_run_rules` / `_log_step`. Each sub-phase only edits its own `game_type` branch.

### Phase 2a: Tug of War (P3-01, P3-02, P3-03)

| File | Function / symbol | Change |
|---|---|---|
| `backend/app/schemas/runs.py` | `RunStepCreate` (L60) | Add `tug_team: Literal["team_a","team_b"] \| None = None` (shared-device support). |
| `backend/app/engines/tug_of_war.py` | `prepare_rules_config` | Stamp `tug_rules_version = 2` plus defaults `steal_enabled=True`, `momentum_cap=3`, `sudden_death_after=12`, `sudden_death_every=2`. Runs **without** `tug_rules_version` keep v1 scoring, so in-progress runs are not re-scored. |
| same | `validate_rules_config` | Validate the 4 new keys (bool, 1–5, 4–50, 1–10). |
| same | `team_of(players, user_id)`, `step_turn_team(step, players)` **(new)** | `transition_metadata["tug_team"]`, falling back to `team_of(step.logged_by_user_id)`. |
| same | `Pull`, `TugTally`, `tally(steps, rules, players)` **(new)** | Pure fold per master audit §3.1.2(b). Skips the seed (step 0). Home pull = `min(streak, momentum_cap)`. Invasion = +1 to the puller and −1 to the opponent (floor 0), and it breaks the opponent's streak. Neutral = sets `anchor` (×2 next scoring pull), resets streaks; in Sudden Death it concedes 1. `effective_target = max(1, target_lead − ⌊(turns − S)/E⌋)`. |
| same | `compute_scores`, `leading_team` | v2: thin wrappers over `tally()`. v1: unchanged code path. |
| same | `sync_run_state` | Also cache `tug_momentum = {streak_team, streak, anchor, effective_target, sudden_death, next_team}`. |
| same | `evaluate_run_outcome` | Compare against `effective_target`; the victory text notes "(Sudden Death)" when applicable. |
| same | `discover_candidates` **(override)** | Annotate each candidate with `tug_effect` (`home`/`invasion`/`neutral`) and `tug_points` for `next_team`. |
| `backend/app/schemas/discovery.py` | `DiscoveryCandidate` | Optional `tug_effect`, `tug_points`. |
| `backend/app/api/routes_runs.py` | `_enforce_run_rules` (Tug branch) | Resolve `team = payload.tug_team or team_of(current_user)`. The team must belong to a participant. **409** "It's <name>'s pull" when `team != tug_momentum.next_team` (v2 only). Stamp `extra_metadata["tug_team"]`. |
| same | `accept_fork_movie` (L956–L985) | For Tug runs, log with `tug_team = team_of(fork.offered_by_id)`. |
| same | veto step-target check (L1006) | For Tug runs, decide "partner's step" by `tug_team` instead of `logged_by_user_id`. |
| same | `SERVER_OWNED_METADATA` (L108) | Add `"tug_team"` and `"life_lost"`. |
| `backend/app/services/blind_fork.py` | `SERVER_OWNED_RULES` (L18) | Add `"tug_momentum"`. |
| `frontend/src/types/api.ts` | `RulesConfig`, `DiscoveryCandidate` | `tug_momentum`, `tug_effect`, `tug_points`. |
| `frontend/src/lib/tugOfWar.ts` | `tugTarget`, new `tugNextTeam`, `tugEffectLabel` | Read `effective_target`. Labels: "+2 🔥", "⚔️ Steal", "⚓ Anchor". |
| `frontend/src/components/TugOfWarMeter.tsx` | `TugOfWarMeter` | Shrinking end-zones (`effective_target`), streak flames, anchor icon, "Next pull: <name>", Sudden Death banner. |
| `frontend/src/components/PickNextHub.tsx` | candidate card (~L995) | `tug_effect` chip. Logging sends `tug_team = tugNextTeam(rules)`. |
| `backend/tests/test_tug_momentum.py` **(new)** | | Pure `tally()` table tests: seed ignored; alternation enforced (409); home/invasion/neutral/anchor/streak cap; Sudden Death shrink; **termination by turn `S + E·(target−1) + 1`** (property test over random home/neutral sequences); shared-device `tug_team`; fork attribution to the offerer; forging `tug_team`/`tug_momentum` is stripped; v1 runs unchanged. |

**Acceptance:** under strict alternating home pulls with defaults, the run completes by turn 19. Existing `test_fork_veto_tug.py` stays green (v1 path).
**Commit:** `feat(tug): attribution, turn order, steal/momentum/anchor and sudden death`

### Phase 2b: Meet in the Middle (P3-04)

| File | Function / symbol | Change |
|---|---|---|
| `backend/app/engines/meet_in_middle.py` | `TunnelDistance` | Add `path_movie_ids: list[int]`, `connections: list` (filled from the `result` event's `path`/`connections`). |
| same | `distance(...)` | Accept `max_depth`/`max_seconds` overrides (defaults stay 5 / 8). |
| same | `prepare_rules_config` **(new override)** | `tunnel_hints_remaining = clamp(rules.get("tunnel_hints", 2), 0, 5)`. |
| same | `near_miss(movie_id, opposing_steps, rules)` **(new)** | Cast-only validation against `opposing_steps[-6:-1]`. Returns the first matched step or `None`. |
| `backend/app/api/routes_runs.py` | `POST /runs/{id}/tunnel/hint` **(new)** `request_tunnel_hint` | Body `{side, level: "actor"\|"film"}` (cost 1 / 2). Pre-checks: active, not collided, enough tokens. Escalated search (`max_depth=7`, `max_seconds=20`). Spend and commit **only** if a path is found. Returns `{level, actor?, film?, tokens_remaining}`. |
| same | `_enforce_run_rules` (tunnel branch ~L194–L261) | After a valid non-colliding step, stamp `near_miss_with` from `engine.near_miss`. |
| same | `SERVER_OWNED_METADATA` / `SERVER_OWNED_RULES` | Add `"near_miss_with"` and `"tunnel_hints_remaining"`. |
| `backend/app/schemas/engine.py` | `TunnelState` (L195), `TunnelHintRequest/Response` **(new)** | `TunnelState.hints_remaining`. **No path exposed** on `TunnelState`. |
| `frontend/src/lib/queries.ts` | `useTunnelHint(runId)` **(new)**, `useTunnelState` | Hint mutation invalidates the tunnel key. Keep the previous `distance_hops` for the trend. |
| `frontend/src/components/TunnelTimeline.tsx` | trench | ▲ warmer / ▼ colder trend; "💡 Hint" buttons per side with token count; near-miss "💫" chip on `TunnelCard`. |
| `frontend/src/components/TunnelFrontierCard.tsx` | `TunnelFrontierCard` | "↔ Swap frontier" = existing step delete, then open `PickNextHub` on the previous frontier of that side. No backend rule. |
| `backend/tests/test_meet_in_the_middle.py` | extend | Hint pre-checks don't spend; actor/film levels; no-path refund; forged `tunnel_hints_remaining`/`near_miss_with` stripped; near-miss stamping; collision rules unchanged. |

**Commit:** `feat(tunnel): bridge hint tokens, near-miss detection and warmer/colder trend`

### Phase 2c: The Rabbit Hole (P3-05, P3-06)

| File | Function / symbol | Change |
|---|---|---|
| `backend/app/engines/base.py` | `BaseChallengeEngine.award_bounty(rules, completed_id, replacement_id)` **(new)** | Default delegates to `bounties.award` (wildcard). |
| `backend/app/api/routes_runs.py` | `_log_step` (~L645–L672) | Replace `bounties.award(...)` with `engine.award_bounty(...)`. |
| `backend/app/engines/rabbit_hole.py` | `supports_bounty_board = True`, `award_bounty` override | +1 life, capped at `max_lives`. |
| same | `tier_for_depth(depth, rules=None)` | Honour `rules["tier_override"] = {depth, tier}` when the depth matches. Update every call site (`tier_state`, `forfeit_outcome`, `_needs_hydration`, `validate_primary`, `discover_candidates`, `describe_constraint`). |
| same | `sync_run_state` **(override)** | Drop a stale `tier_override` once `len(steps) > override.depth`. |
| same | `evaluate_run_outcome` **(override)** | Optional `escape_depth` (25–60) → `COMPLETED` "Escaped the Rabbit Hole at Depth N with ❤️×k". |
| same | `validate_rules_config` | Validate `escape_depth`. |
| same | `discover_candidates` | When `kept == []` and lives are 0, flag `dead_end`. |
| `backend/app/schemas/engine.py` | `RabbitHoleState` | `dead_end: bool = False`, `tier_override: int \| None`. |
| `backend/app/api/routes_runs.py` | `POST /runs/{id}/rabbit-hole/reroll` **(new)** | Pre-checks: active, lives ≥ 2, tier > 1, no override at this depth. Then pick a different tier from 2–5 and spend 1 life. |
| `backend/app/services/blind_fork.py` | `SERVER_OWNED_RULES` | Add `"tier_override"` (and `"lives_remaining"` for defence in depth). |
| `backend/app/models/cache.py` + `backend/migrations/versions/<new>_add_vote_count.py` | `CachedMovie.vote_count` | New nullable int column. |
| `backend/app/services/cache_repo.py` | L106, L137 | Store `vote_count` next to `vote_average`. |
| `backend/app/services/movie_filters.py` | `rating_of` (L52) | Ignore `vote_average` when `vote_count` is `None` or `< 10` (→ `None` = unverified). |
| `frontend/src/components/RabbitHoleHud.tsx` | `RabbitHoleHud` | "🎲 Re-roll tier (−1 ❤️)" button; a dead-end panel with "Accept your fate" (forfeit) / "Search manually". |
| `frontend/src/components/BountyBoardPanel.tsx`, `frontend/src/lib/bounties.ts`, `frontend/src/lib/rabbitHole.ts` | reward copy | "❤️ +1 life" for Rabbit Hole runs. Remove `rabbit_hole` from `NO_BOUNTY_MODES`. |
| `backend/tests/test_rabbit_hole.py` | extend | Life award capped; re-roll pre-checks and no spend on refusal; override scoped to one depth; dead-end flag; escape victory; `vote_count < 10` ⇒ unverified; forged `tier_override` stripped. |

**Pre-work (no code):** run the telemetry query in master audit §3.3.3 against a real DB to confirm where lives are actually lost before tuning tier thresholds.
**Commit:** `feat(rabbit-hole): life bounties, sacrificial re-roll, dead-end detection, escape depth, rating accuracy`

**Phase 2 validate:** `just backend-test`, `just backend-lint`, `uv run alembic heads` (single head), `just frontend-build`.

---

## Phase 3: Global UI Expansion (text truncation & popovers)

**Depends on:** Phase 0 (`ExpandableText`, `ClampedLabel`, `Popover`).
**Findings:** P2-01 … P2-06.

| File | Location | Change |
|---|---|---|
| `frontend/src/components/MovieDetailModal.tsx` | L152 overview | `<ExpandableText lines={6} />` (inline). |
| `frontend/src/components/MoviePreviewModal.tsx` | L92 overview | `<ExpandableText lines={6} />`. |
| `frontend/src/components/PickNextHub.tsx` | L1436 overview; L984, L1705 titles | `ExpandableText lines={4}`; `ClampedLabel` for the titles. |
| `frontend/src/components/BracketView.tsx` | L254 `MatchupCard` logline | `<ExpandableText lead={<MovieTagline…/>} lines={4} expandMode="dialog" />` (tagline moved out of the clamp). |
| `frontend/src/components/ForkOfferPanel.tsx` | L257 | `ExpandableText lines={4}` (keep `animate-pulse` while hydrating). |
| `frontend/src/components/BlindDraft.tsx` | L187 | `ExpandableText lead={aiTeaser ? "✨ " : null} lines={4} expandMode="dialog"`. |
| `frontend/src/components/CuratedListCard.tsx` | L17–L39 `Description` | Delete; use `ExpandableText lines={4} moreLabel="Read more"`. Remove `LONG_DESCRIPTION`. |
| `frontend/src/components/RouletteSpinner.tsx` | L261 | `ExpandableText lines={3}`. |
| `frontend/src/pages/MarathonRouterPage.tsx` | L429 | `ExpandableText lines={2}`. |
| `frontend/src/components/ChainTimeline.tsx` | L280 notes; L253 title | `ExpandableText lines={2}`; `ClampedLabel`. |
| `frontend/src/pages/CuratorsPage.tsx` | L120 bio | `ExpandableText lines={2}`. |
| `frontend/src/components/BridgeSwapPanel.tsx` | L137 | `ClampedLabel`. |
| `frontend/src/pages/DailyBridgePage.tsx` | L236 | `ClampedLabel`. |
| `frontend/src/components/PitchButton.tsx` | popover (`absolute … w-60`) | Render the `PitchState` inside `<Popover anchorRef=…>`. Change `role="tooltip"` to `role="dialog"`, and give the button `aria-haspopup="dialog"`. Remove the local `mousedown` listener (the Popover owns dismissal). |
| *(optional, mechanical)* 50 tooltip-less `truncate` sites | `grep -rn "\btruncate\b" frontend/src` | Add `title=` where the text is user content. |

**Acceptance criteria**
- `rg "line-clamp-[3-6]" frontend/src` returns only `ui/ExpandableText.tsx` and `GameModePicker.tsx` (grid cards, intentionally).
- In a Meet in the Middle run, "✨ Why this link?" on a tunnel card shows the full pitch unclipped. On the right-most Pick Next column the popover stays on-screen.
- Every expandable is reachable and togglable with keyboard only (living-room d-pad simulation: Tab / Enter / Esc).

**Validate:** `just frontend-build`, then the manual pass over the 15 listed surfaces.
**Commit:** `feat(ui): replace hard line-clamps with ExpandableText and portal PitchButton popover`

---

## Phase 4: Integrations

**Depends on:** Phase 0 (`Popover`, `ExpandableText`, `useJellyfinLookup`).
**Findings:** P4-01 … P4-06.

### Phase 4a: Watchlist sync status (P4-01, P4-02, P4-03)

| File | Function / symbol | Change |
|---|---|---|
| `backend/app/api/routes_curated.py` | `WatchlistStatus` model **(new, inline beside `WatchlistSyncRequest` L350)**; `GET /curated/watchlist/status` → `get_watchlist_status` **(new)** | Return `{letterboxd_username, synced_at, total_items}` for the current user. **Step A:** aggregate over `LetterboxdWatchlist` (`MAX(synced_at)`, `COUNT(*)`, latest username). **Step B (same phase):** read the user columns below, with the aggregate as fallback. |
| `backend/app/models/user.py` + `backend/migrations/versions/<new>_user_watchlist_sync.py` | `User.letterboxd_username`, `User.watchlist_synced_at` | New nullable columns. Backfill from `letterboxd_watchlists` (`MAX(synced_at)` and username per user). |
| `backend/app/api/routes_curated.py` | `_persist_watchlist` (L390) | Set both user columns **in the same transaction** as the row replace (covers empty watchlists). |
| `backend/tests/test_curated_api.py` (or new `test_watchlist_status.py`) | | Never synced → nulls; after sync → username/time/count; empty watchlist → synced with 0; per-user isolation; failed sync keeps the previous status. |
| `frontend/src/types/api.ts` | `WatchlistStatus` | New interface. |
| `frontend/src/lib/queries.ts` | `watchlistStatusKey`, `useWatchlistStatus()` | `staleTime: 30_000`. |
| `frontend/src/components/CuratedCanonsCard.tsx` | `CuratedCanonsCard` (L27–L66, L220–L254) | Delete `watchlistSyncedAt`. `SyncBadge syncedAt={status?.synced_at}` plus "· N films". Pre-fill the username from status (only if the field is untouched). Replace `useMutation(runTask)` with `useTrackedTask({ resumeNames: ["watchlist_sync"], onFinished })`. `onFinished` invalidates `watchlistStatusKey` and keeps the `watchlist_not_found` warning / toast branches. |

**Acceptance:** sync, navigate away, come back: the badge shows "Synced Xm ago · N films" and the username is pre-filled. Navigating away mid-sync and returning resumes the progress text.
**Commit:** `feat(watchlist): persistent sync status endpoint and resumable sync progress`

### Phase 4b: March Madness championship (P4-04, P4-05, P4-06)

| File | Function / component | Change |
|---|---|---|
| `frontend/src/components/BracketView.tsx` | `BracketView` (~L27) | `const { data: server } = useJellyfinLookup(Object.keys(films).map(Number))`, one bulk call. Pass `server` to children. Render `<ChampionBanner>` **above** the `overflow-x-auto` scroller when `bracket.champion !== null`. |
| same | `MatchupBox` (L111–L113) | Emerald 6 px "on server" dot with `title`. |
| same | `MatchupCard` article (~L252) | `OnServerBadge` + `AcquisitionControl` row; `onServerCardClass` tint on the `article`. |
| same | `Podium` (~L148) | Add `OnServerBadge` only (the control lives in the banner, out of the clipping scroller). |
| same | `ChampionBanner` **(new, local)** | Large poster, title, `OnServerBadge`, **▶ Play on Jellyfin** (`play_url`, `target="_blank" rel="noreferrer"`) when on the server, else `AcquisitionControl`. Logline via `ExpandableText`. |
| `frontend/src/components/AcquisitionControl.tsx` | options menu (L116) | Render through `Popover` so it is never clipped by `Modal`/scroller overflow. |
| *(optional cleanup)* `PickNextHub.tsx` L372/L1327, `MovieDetailModal.tsx` L61, `MoviePreviewModal.tsx` L37, `ForkInTheRoadModal.tsx` L109 | inline Jellyfin `useQuery` | Swap for `useJellyfinLookup` (same key, no behaviour change). |

**Acceptance:** with Jellyfin configured, contenders on the server show the dot/badge. A crowned champion shows Play (on server) or Request (missing) without opening any other screen. With integrations off, the bracket renders exactly as today (badges return `null`).
**Commit:** `feat(bracket): media-server visibility and actionable champion banner`

**Phase 4 validate:** `just backend-test`, `just backend-lint`, `uv run alembic heads`, `just frontend-build`.

---

## Backlog (verified, intentionally out of scope)

| Item | Source | Note |
|---|---|---|
| Historical Time-Travel "Wormhole" / `allow_same_year` | Master audit §3.4, P3-07 | Strict `>`/`<` confirmed. Design in `docs/planning/Future Game Modes & Engines Vault.md`. |
| Filter/sort persistence on `BridgePage` / `MarathonRouterPage` | Baseline §1 | Not re-verified in this audit; candidate approach: URL search params. |
| Living-room large-screen scaling (card sizes, gutters) | Baseline §3 | Needs a design pass; partly mitigated by the Phase 1 wide wizard and the Phase 3 expanders. |
| Golden Veto shared-device attribution outside Tug | P3-03 | Needs a general "acting player" concept; Phase 2a fixes it for Tug only. |

## Traceability matrix

| Finding | Phase |
|---|---|
| P1-01, P1-02, P1-03, P1-04, P1-05, P1-06 | 1 (P1-04 also 0) |
| P2-01, P2-02, P2-04, P2-05, P2-06 | 3 |
| P2-03 | 0 (Popover) + 3 (PitchButton) |
| P3-01, P3-02, P3-03 | 2a |
| P3-04 | 2b |
| P3-05, P3-06 | 2c |
| P3-07 | Backlog |
| P4-01, P4-02, P4-03 | 4a |
| P4-04, P4-05, P4-06 | 0 (`useJellyfinLookup`) + 4b |
