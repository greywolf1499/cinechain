# CineChain: Master UX, Game-Systems & QA Audit (Verified)

**Roles:** Principal Product Designer · Game Systems Architect · Lead QA Engineer
**Date:** 2026-10-05
**Baseline:** [`UX_AUDIT.md`](./UX_AUDIT.md) (static audit, unverified)
**Method:** Every baseline claim below was re-checked against the source at commit `74b8f44`. Each finding is tagged:

- ✅ **Confirmed:** the baseline claim matches the code.
- ⚠️ **Corrected:** the claim is partly right; the code differs in a way that changes the fix.
- ❌ **Refuted:** the claim does not hold in the current code.
- 🆕 **New:** found during verification; not in the baseline.

Constraints honoured throughout (see `.github/copilot-instructions.md` / [`LESSONS.md`](./LESSONS.md)): no Redis/Celery/cron/vector DB, ephemeral background work only, JIT-loaded ML models, and server-owned rule keys (`SERVER_OWNED_RULES`) must never be client-writable.

---

## Phase 1: Baseline Ingestion & the "Create New Run" Flow

### 1.1 Baseline claims vs. code

| # | Baseline claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `RunsPage.tsx` relies on a monolithic `NewRunModal` with 500+ lines of UI | ✅ Confirmed | `frontend/src/pages/RunsPage.tsx` is 649 lines; `function NewRunModal` runs from **L97 to L624** (~528 lines), declared inline in the page module. It is not exported, so nothing else can reuse or test it. |
| 2 | Causes cognitive overload & vertical-scroll fatigue | ✅ Confirmed | See the render-order map (§1.2). The 20-card `GameModePicker` grid renders **before** any mode configuration. The `Modal` body is fixed at `max-h-[70vh] overflow-y-auto` (`components/Modal.tsx`), so on a 1080p TV the user scrolls about 2 viewport-heights of mode cards before reaching the controls for the mode they just chose. |
| 3 | Pages lack consistent empty states / CTAs | ⚠️ Corrected | A shared `EmptyState` component **does** exist (`components/EmptyState.tsx`) and `RunsPage` uses it (L60). But it takes only `icon`, `title` and `description`: there is **no `action` prop**. "No runs yet" therefore has no "Start your first run" button. The user has to find the header "New Run" button. The empty-state copy ("chaining films by shared cast") is also wrong for 12 of the 20 modes, which are cast-free. |
| 4 | Seed picker gives no context (from the prior plan) | ✅ Confirmed | `SeedMoviePicker.tsx` shows the chosen film as a `w-11` (44 px) poster plus title/year only. `MovieSummary` (`types/api.ts` L305) has no overview, runtime or ratings. However, `useMovieDetail(movieId)` (`lib/queries.ts` L236) already returns `MovieDetail` (overview, tagline, runtime, language, `ratings`), and `RatingBadges` / `MovieTagline` already exist. A hero preview needs **zero backend work**. |

### 1.2 Render-order map of `NewRunModal` (as shipped)

All 17 regions are in one scrolling column (`flex flex-col gap-4`):

| Order | Line | Region | Shown when | Notes |
|---|---|---|---|---|
| 1 | ~271 | Run name `<input>` | always | Required; the submit button is disabled while empty, but there is **no inline validation message**. |
| 2 | ~280 | `GameModePicker` (20 cards, `grid-cols-1 sm:2 lg:3`) | always | Each card has its own "⚙️ Customize Modifiers" drawer that expands to `col-span-3` **inside the grid** (`GameModePicker.tsx`), which pushes later cards down. Descriptions are `line-clamp-4`. |
| 3 | L296 | 📜 Bounty Board toggle | `canBounty` | A cross-mode modifier, mixed in with mode-specific panels. |
| 4 | L319 | RT Split "First to (points)" | `isSplit` | |
| 5 | L339 | Canon list `<select>` | `canon_island` | Empty-list warning is inline amber text with no link to Lists. |
| 6 | L361 | Decade `<select>` | `decade_sieve` | |
| 7 | L377 | `BracketSeedPicker` (16 films) | `march_madness` | The heaviest sub-form, embedded mid-scroll. |
| 8 | L379 | `ActorPicker` | `method_actor` | |
| 9 | L385 | `ActorPicker department="Directing"` | `auteur_marathon` | |
| 10 | L397 | Dive list + country + decade | `regional_deep_dive` | Duplicates the canon-list `<select>` from region 5. |
| 11 | L456 | `GenreCycleInput` | `genre_pendulum` | |
| 12 | L465 | Tug dimension radio cards + target-lead slider | `tug_of_war` | The copy says "Every watched film scores one point". That is false for neutral films, which score 0 (see Phase 3). |
| 13 | L515 | Participants checklist | always | **The order of clicks matters**: for Tug of War / RT Split, Team B is "the next participant you add" (`participantIds` keeps insertion order), and nothing in the UI shows this. |
| 14 | L534 | Seed picker (×2 for Meet in the Middle) | always | Optional for most modes; required ×2 for the tunnel mode. |
| 15 | L561 | Admin "Raw JSON Override" switch | admin & `json_rules` capability | |
| 16 | L589 | `RawRulesEditor` **or** `RulesetFields` | non-tracker | Generic cast-depth / wildcard / repeat rules sit **after** the seed, far from the mode they configure. |
| 17 | ~605 | Error + "Create run" button | always | The only primary action is at the very bottom. Disabled-state reasons (`missingMode`, `sameSeeds`) are never shown as text. |

**State surface:** 24 `useState` hooks (name, gameType, participants, 2 seeds, rules, canonListId, targetDecade, tugDimension, tugLead, genreCycle, swingFrequency, bracketFilms, actor, director, diveListId/Country/Decade, bountyBoard, splitTarget, rawMode, rawText). The derived values `formRules` (L163) and `missingMode` (L189) and the hand-written `reset()` (L219, which must list every hook) together hold the hidden contract of the form. Adding one mode currently means touching 4 places in this file.

### 1.3 Root causes (design-level)

1. **No progressive disclosure:** the mode choice and the mode configuration share one scroll container, and the choice (20 cards) physically pushes the configuration out of view.
2. **No configuration registry:** the mode-specific panels are a chain of `isX && (...)` branches inside the modal, not a lookup keyed by `game_type`. `formRules`/`missingMode` duplicate the same branching.
3. **Silent validation:** the submit button disables for 6+ different reasons (`!name`, `missingMode` across 6 modes, `sameSeeds`, raw-JSON parse errors) and none are surfaced in the UI.
4. **Two separate modifier surfaces:** per-mode modifiers live in the `GameModePicker` card drawer (`ModeOptions`), while generic rules (`RulesetFields`) and the Bounty Board live elsewhere in the form.

### 1.4 Target architecture: 2-Step Creator wizard

New directory `frontend/src/components/run-creator/`:

| Component | Responsibility | Built from (existing) |
|---|---|---|
| `NewRunWizard.tsx` | Modal shell, `step: 1 \| 2` state, header stepper ("① Mode → ② Setup"), sticky footer (Back / Next / Create). Owns `useCreateRun`. Replaces `NewRunModal` in `RunsPage.tsx`. | `Modal` (needs a `footer` slot and a `size="wide"` option, see §1.5) |
| `useRunDraft.ts` | **A single `useReducer`** replacing the 24 `useState`s. Exposes `draft`, `dispatch`, `buildPayload()` (the current `formRules` + `handleSubmit` body) and `blockers: string[]` (the current `missingMode`/`sameSeeds`/name/raw checks as **human-readable reasons**). `reset` becomes `dispatch({type:"reset"})`, so a new field can no longer be forgotten. | Logic moved verbatim from `RunsPage.tsx` L163–L261 |
| `Step1ModeSelect.tsx` | Full-width mode grid with **category filter chips** (Cast Chains · Rule Chains · Co-op/Versus · Trackers · Tournaments), derived from `STANDALONE_MODES`/`TRACKER_MODES` in `lib/gameModes.ts` plus the existing `tags`. Selecting a card highlights it and enables **Next**. Double-click or Enter selects and advances. The modifier drawers are **removed from the cards** and move to Step 2. | `GameModePicker` (card body reused; drawer removed) |
| `Step2RunSetup.tsx` | Two-column layout `grid lg:grid-cols-[minmax(0,1fr)_420px]`. **Left:** run name, `ParticipantPicker`, `<ModeConfigPanel>`, a collapsible "Advanced rules" section (`ModeOptions` + `RulesetFields` + Bounty Board + admin Raw JSON). **Right:** `HeroSeedPreview` (×2 side by side for Meet in the Middle). A chip at the top shows the chosen mode with a "Change" link back to Step 1. | `ModeOptions`, `RulesetFields`, `RawRulesEditor` |
| `ModeConfigPanel.tsx` | **Registry** `MODE_CONFIG: Record<string, FC<ModeConfigProps>>` keyed by `game_type`, holding the 11 existing panels (regions 4–12 above, extracted 1:1 into `mode-config/*.tsx`: `TugConfig`, `SplitConfig`, `CanonListConfig`, `DecadeConfig`, `BracketConfig`, `ActorConfig`, `DirectorConfig`, `DiveConfig`, `PendulumConfig`). Modes with no extra inputs render a one-line "No setup needed" note. | Extracted JSX from `RunsPage.tsx` L319–L513 |
| `ParticipantPicker.tsx` | A pill toggle list that **shows team assignment** for versus modes ("You → Team A · Alex → Team B") and keeps insertion order. | Participants block L515 |
| `HeroSeedPreview.tsx` | Wraps `SeedMoviePicker`. Once a film is chosen it fetches `useMovieDetail(tmdb_id)` and renders a large poster, title/year, `MovieTagline`, `RatingBadges`, runtime, flag, and a synopsis through the Phase 2 `ExpandableText` component. **Empty state:** dashed drop-zone with "🎲 Recommend Seed Movie" as the primary CTA and the search box as the secondary one. | `SeedMoviePicker`, `useMovieDetail`, `RatingBadges`, `MovieTagline`, `MoviePoster` |
| `CreateBlockers.tsx` | Renders `draft.blockers` next to the disabled Create button ("Pick 16 bracket films (9/16)", "Partners need different seeds"). | none |

**Layout sketch (Step 2):**

```
┌ Start a new run ─────────────── ① Mode ✓ ── ② Setup ─────────────────┐
│ [🪢 Tug of War · Change]                                              │
│ ┌──────────── LEFT (config) ─────────────┐ ┌──── RIGHT (seed) ─────┐ │
│ │ Run name  [Bacon Sunday            ]   │ │  ┌──────┐  The Thing  │ │
│ │ Players   (You→A) (Alex→B) (+ Sam)     │ │  │poster│  1982 · 1h49│ │
│ │ ── Tug of War setup ──                 │ │  │      │  IMDb 8.2 🍅│ │
│ │ (•) Era  ( ) Geography   Lead: 4 ━━●━  │ │  └──────┘  🇺🇸        │ │
│ │ ▸ Advanced rules (cast depth, bounty…) │ │  Synopsis… [Show more]│ │
│ └────────────────────────────────────────┘ │ [🎲 Re-roll] [✕]      │ │
│                                            └───────────────────────┘ │
├───────────────────────────────────────────────────────────────────────┤
│ ⚠ Add a second player for Team B          [◀ Back]   [Create run ▶]  │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.5 Supporting component changes required

| File | Change | Why |
|---|---|---|
| `components/Modal.tsx` | Add an optional `footer?: ReactNode` (rendered outside the scroll area) and allow `bodyClassName` to override `max-h-[70vh]` (wizard: `max-h-[calc(100vh-10rem)]`). | Keeps the primary actions visible. Backwards compatible: every other caller keeps its defaults. |
| `components/EmptyState.tsx` | Add an optional `action?: { label: string; onClick: () => void; icon?: LucideIcon }`. | A reusable CTA for every empty page (Runs, Lists, Curators, Bounty Board). |
| `pages/RunsPage.tsx` | Delete `NewRunModal`, `TRACKER_RULES`, `DECADES`, `inputClass`, `Field` (move them to `run-creator/`). Empty state gets `action={{label:"Start your first run", onClick: open}}` and mode-neutral copy. Run cards gain a mode chip (`gameModeStyle(run.game_type)`), since `Run.game_type` is already in the list payload. | The page drops from 649 to ~110 lines. |
| `components/GameModePicker.tsx` | Add a `variant: "wizard" \| "legacy"` prop, or split out `ModeCard`. The drawer is not rendered in wizard mode. `EditRulesModal` keeps using `ModeOptions` directly and is unaffected. | Removes the in-grid layout shift. |
| `lib/gameModes.ts` | Add `MODE_CATEGORIES` and a `category` field on `GameModeStyle`. | Drives the Step 1 filter chips. |

### 1.6 Behavioural invariants the refactor MUST preserve (QA checklist)

1. `selectMode` clears modifiers when the mode changes (`clearModifiers`, L198–L202).
2. Tracker modes submit `TRACKER_RULES`, not the cast-rule form (L176).
3. `modifierPayload(gameType, rules, capabilities)` is merged after the base rules (L177).
4. Raw JSON: admin only, only when `capabilities.includes("json_rules")`. It seeds from `{...RAW_RULES_EXAMPLE, ...formRules}`. For Meet in the Middle, a raw override still requires both seeds.
5. `tail_seed_movie_id` is sent **only** for `meet_in_the_middle`, and must differ from the head seed.
6. Server-owned keys (`bracket`, `bracket_films`, `actor`, `filmography`, `tug_scores`, `tug_players`, `pending_fork`, `lives_remaining`) are **never** built on the client. The client only sends inputs (`bracket_movie_ids`, `actor_id`, …).
7. Participant insertion order decides Team B (Tug of War / RT Split).
8. Closing the modal at any step fully resets the draft. **New:** a confirm prompt when the draft is dirty and the user presses Esc on Step 2.

### 1.7 Phase 1 severity summary

| ID | Severity | Finding |
|---|---|---|
| P1-01 | 🟠 High | Monolithic 528-line inline `NewRunModal`; 24 state hooks; the mode config is hidden below a 20-card grid inside a `70vh` scroll box. |
| P1-02 | 🟠 High | Silent disabled submit: 6+ blocking reasons are never explained to the user. |
| P1-03 | 🟡 Medium | Seed picker shows no synopsis, ratings or runtime, although `useMovieDetail` already provides them. |
| P1-04 | 🟡 Medium | `EmptyState` has no CTA slot; the Runs empty-state copy is wrong for cast-free modes. |
| P1-05 | 🟡 Medium 🆕 | Team assignment for versus modes depends on invisible checkbox click order. |
| P1-06 | ⚪ Low 🆕 | Run list cards don't show the game mode, although `Run.game_type` is already fetched. |

---

## Phase 2: Component Ergonomics & Text Truncation

### 2.1 Method

I grepped `frontend/src` for `line-clamp`, `truncate`, `overflow-hidden`, fixed-width popovers and scroll containers, then read each hit in context. That gives **23 `line-clamp` sites in 16 files** and **51 `truncate` sites** (50 without a `title` tooltip). Each site is classified by **what is being cut**:

- **Prose** (synopsis, AI pitch/teaser, bio, user notes, mode description): cutting it loses meaning, so it **must** be expandable.
- **Label** (film title, actor name): a 2-line clamp is fine for grid rhythm, **provided** the full text is reachable (a `title` tooltip or a details view).

### 2.2 Verification of the 17 baseline components

| # | Component (line) | Clamp | Content | Verdict | Severity / note |
|---|---|---|---|---|---|
| 1 | `MovieDetailModal.tsx` L152 | `line-clamp-4` | Overview | ✅ | 🟠 High: this is the **detail** view, the one place where the full synopsis should be shown, and there is no other place to read it. |
| 2 | `MoviePreviewModal.tsx` L92 | `line-clamp-6` | Overview | ✅ | 🟠 High: same as #1. |
| 3 | `PickNextHub.tsx` L1436 (movie screen) | `line-clamp-3` | Overview | ✅ | 🟠 High: the user decides on a pick here. Also L984/L1705 clamp titles with **no tooltip** (🟡). |
| 4 | `BracketView.tsx` L254 (`MatchupCard` film panel) | `line-clamp-5` | Tagline **+** overview in one `<p>` | ⚠️ | 🟠 High: the `<em>` tagline sits **inside** the clamped paragraph and uses up to 2 of the 5 lines, so the logline is cut even shorter. Partners vote on this text. |
| 5 | `ForkOfferPanel.tsx` L257 | `line-clamp-4` | Overview | ✅ | 🟠 High: the partner vetoes/accepts on this text. |
| 6 | `BlindDraft.tsx` L187 | `line-clamp-4` | Logline / **AI cryptic teaser** | ✅ | 🟠 High: a cut teaser hides the puzzle clue the mode is built on. |
| 7 | `CuratedListCard.tsx` L24 | `line-clamp-4` | List description | ❌ Refuted | Already has a local `Description` with a "Read more / Show less" toggle (240-char heuristic). **Use it as the reference pattern** and replace it with the shared component. |
| 8 | `BridgeSwapPanel.tsx` L137 | `line-clamp-2` | Film title | ⚠️ Label | 🟡 Medium: no tooltip. |
| 9 | `TunnelTimeline.tsx` L128 | `line-clamp-2` | Film title in a `w-28` card | ⚠️ Label | ⚪ Low (title is the poster's `alt` only). **🆕 The real bug in this file is §2.3.** |
| 10 | `GameModePicker.tsx` L105 | `line-clamp-4` | Mode rules description | ✅ | 🟡 Medium: the rules of the game are cut at the moment of choosing it. Handled by the Phase 1 wizard (a full description on the selected card / Step 2 header). |
| 11 | `RouletteSpinner.tsx` L261 | `line-clamp-3` | Tagline **or** overview | ✅ | 🟡 Medium. |
| 12 | `BridgePathView.tsx` L81, L138 | `line-clamp-2` | Title / actor | ❌ Refuted | Both have `title=` tooltips and `break-words`. This is acceptable label handling. |
| 13 | `MarathonRouterPage.tsx` L429 | `line-clamp-2` | Overview | ✅ | 🟡 Medium. |
| 14 | `ChainTimeline.tsx` L253, L280 | `line-clamp-2` | Title; **user notes** | ⚠️ | ⚪ Low: notes are fully readable/editable in `MovieDetailModal` (`notes` textarea, L70). Add an inline expand anyway, because the timeline is the "story" view. |
| 15 | `DailyBridgePage.tsx` L236 | `line-clamp-2` | Film title | ⚠️ Label | 🟡 Medium: no tooltip. |
| 16 | `CuratorsPage.tsx` L120 | `line-clamp-2` | Curator bio | ✅ | 🟡 Medium. |
| 17 | `PitchButton.tsx` popover | `w-60`, no clamp | AI pitch | ⚠️ Corrected | 🟠 High: the text is **not** clamped. The real defect is **clipping and placement**, see §2.3. |

**Net verified:** 11 prose sites need expansion; 4 label sites need tooltips; 2 baseline items are refuted (`CuratedListCard`, `BridgePathView`).

### 2.3 🆕 `PitchButton` popover clipping (root cause)

`PitchButton.tsx` renders its popover as `absolute bottom-full left-0 z-40 w-60`, positioned relative to its own `<span class="relative">`. It is mounted in three places:

| Mount | Ancestor | Effect |
|---|---|---|
| `TunnelTimeline.tsx` `TunnelCard` (L147) | `<div class="flex … overflow-x-auto">` (L76) | Per CSS overflow rules, a non-`visible` `overflow-x` forces `overflow-y` to compute to `auto`. The 240 px popover opens **upward out of a 112 px (`w-28`) card inside a scroll box**, so it is clipped or creates a nested scrollbar. **The pitch is not readable in Meet in the Middle.** |
| `PickNextHub.tsx` candidate card (L1021) | `grid-cols-2 … md:grid-cols-4` (L789) | `left-0` anchoring: in the right-most column the 240 px popover runs past the grid edge, and on a modal-hosted Pick Next it is clipped by `Modal`'s `overflow-y-auto` body. |
| `ChainLink.tsx` (L138) | timeline | Works, but has no viewport-collision handling. |

Secondary issues: the popover is `role="tooltip"` but is click-toggled interactive content (it should be `role="dialog"`/`aria-haspopup="dialog"`). It only closes on `mousedown`/Esc, so a remote's d-pad focus leaving the button never dismisses it. It has no `max-h`, so a long model answer grows upward off the screen.

### 2.4 Root cause (systemic)

- There is **no shared primitive for "long text"**. 11 components each hard-code a `line-clamp-N`, and the one component that solved it (`CuratedListCard.Description`) keeps the fix private.
- There is **no shared popover primitive**, so every floating element is `absolute` inside whatever overflow context it happens to land in.
- The character-length heuristic in `CuratedListCard` (`LONG_DESCRIPTION = 240`) is wrong for TV scaling and narrow cards: the toggle should appear **only when the text is actually clamped**.

### 2.5 Centralised solution

#### A. `components/ui/ExpandableText.tsx` (new)

```tsx
const CLAMP = { 2: "line-clamp-2", 3: "line-clamp-3", 4: "line-clamp-4", 5: "line-clamp-5", 6: "line-clamp-6" } as const;
// Static class map: Tailwind v4 only emits classes it can see literally in the source.

type Props = {
  text: string | null | undefined;
  lines?: keyof typeof CLAMP;          // default 4
  fallback?: ReactNode;                // "No overview available."
  className?: string;                  // typography, merged with cn()
  as?: "p" | "div" | "span";
  lead?: ReactNode;                    // e.g. a tagline/✨ prefix rendered OUTSIDE the clamp
  moreLabel?: string; lessLabel?: string; // "Read more" / "Show less"
  expandMode?: "inline" | "dialog";    // dialog = open full text in <Modal> (cramped cards)
  dialogTitle?: string;
};
```

Behaviour contract:
1. **Measure, don't guess:** a `useLayoutEffect` + `ResizeObserver` compares `scrollHeight > clientHeight + 1` while clamped. The toggle renders **only** when the text overflows, and is re-checked when the width changes (TV and responsive grids).
2. **D-pad/remote friendly:** the toggle is a real `<button>` with `aria-expanded` and `aria-controls`, and a visible `focus-visible:ring`. Its hit target is at least 32 px tall (`py-1.5`) for living-room use.
3. **`expandMode="dialog"`** is for fixed-height cards (bracket, Pick Next grid, Blind Draft) where inline growth would break the grid's `items-stretch` alignment. It opens the full text in the existing `Modal`.
4. `whitespace-pre-line break-words` by default (keeps the paragraph breaks `CuratedListCard` already preserves).
5. `lead` keeps taglines and the AI ✨ marker out of the clamp budget (fixes the `BracketView` tagline bug).

#### B. `components/ui/ClampedLabel.tsx` (new, tiny)

`<ClampedLabel lines={2} text={title} className=… />` renders `line-clamp-N break-words` and **always** sets `title={text}`. It replaces the 4 tooltip-less title clamps. Applying it to the 50 tooltip-less `truncate` sites is optional follow-up work (low priority, mechanical).

#### C. `components/ui/Popover.tsx` (new)

A portal-based floating panel: `createPortal` to `document.body`, positioned from `anchorRef.getBoundingClientRect()` with flip (top ↔ bottom) and shift (clamped to the viewport by 8 px). It recomputes on scroll/resize (capture-phase listener) and closes on outside `pointerdown`, Esc **and** `focusout` of the anchor+panel. Width is `w-72 max-w-[min(90vw,24rem)]`, with `max-h-[50vh] overflow-y-auto`. No new dependency: this is ~80 lines, consistent with the project's "no heavy deps" stance. `PitchButton` then renders its `PitchState` inside `<Popover>` and changes `role="tooltip"` to `role="dialog"`.

### 2.6 Migration map

| File | Replace | With |
|---|---|---|
| `MovieDetailModal.tsx` L152 | `<p className="mt-2 line-clamp-4 …">` | `<ExpandableText lines={6} expandMode="inline" …/>` (a detail view expands in place) |
| `MoviePreviewModal.tsx` L92 | `line-clamp-6` `<p>` | `<ExpandableText lines={6} />` |
| `PickNextHub.tsx` L1436 | `line-clamp-3` `<p>` | `<ExpandableText lines={4} />`; L984/L1705 → `<ClampedLabel>` |
| `BracketView.tsx` L254 | tagline-inside-clamp `<p>` | `<ExpandableText lead={<MovieTagline …/>} lines={4} expandMode="dialog" dialogTitle={film.title} />` |
| `ForkOfferPanel.tsx` L257 | `line-clamp-4` | `<ExpandableText lines={4} />` (keeps the `isHydrating && "animate-pulse"` via `className`) |
| `BlindDraft.tsx` L187 | `line-clamp-4` | `<ExpandableText lead={aiTeaser ? "✨ " : null} lines={4} expandMode="dialog" />` |
| `CuratedListCard.tsx` L17–L39 | private `Description` | `<ExpandableText lines={4} moreLabel="Read more" />` (delete `LONG_DESCRIPTION`) |
| `RouletteSpinner.tsx` L261 | `line-clamp-3` | `<ExpandableText lines={3} />` |
| `MarathonRouterPage.tsx` L429 | `line-clamp-2` | `<ExpandableText lines={2} />` |
| `ChainTimeline.tsx` L280 | notes `line-clamp-2` | `<ExpandableText lines={2} />`; L253 → `<ClampedLabel>` |
| `CuratorsPage.tsx` L120 | bio `line-clamp-2` | `<ExpandableText lines={2} />` |
| `GameModePicker.tsx` L105 | description `line-clamp-4` | Unchanged on grid cards. The full text is shown in the wizard's selected-mode header (Phase 1). |
| `BridgeSwapPanel.tsx` L137, `DailyBridgePage.tsx` L236 | title `line-clamp-2` | `<ClampedLabel>` |
| `PitchButton.tsx` | `absolute … w-60` popover | `<Popover>` (portal, flip/shift, `max-h-[50vh]`, `role="dialog"`) |

### 2.7 Phase 2 severity summary

| ID | Severity | Finding |
|---|---|---|
| P2-01 | 🟠 High | Synopsis is clamped with no expansion in both detail modals (`MovieDetailModal`, `MoviePreviewModal`), the places users go *for* the full text. |
| P2-02 | 🟠 High | Decision surfaces (Pick Next movie screen, Fork offer, Bracket matchup, Blind Draft teaser) cut the text the user is deciding on. |
| P2-03 | 🟠 High 🆕 | `PitchButton` popover is clipped inside `TunnelTimeline`'s `overflow-x-auto` track and overflows the right-most Pick Next column. Wrong ARIA role; no d-pad dismissal. |
| P2-04 | 🟡 Medium ⚠️ | `BracketView` puts the tagline inside the clamped paragraph, which shortens the logline further. |
| P2-05 | 🟡 Medium | 4 title clamps and 50 `truncate`s have no tooltip. |
| P2-06 | ⚪ Low ❌ | `CuratedListCard` and `BridgePathView` are already compliant (refuted baseline items); `CuratedListCard` is the reference pattern to generalise. |

---

## Phase 3: Backend Game Engines & Synergies

### 3.1 Tug of War (`backend/app/engines/tug_of_war.py`)

#### 3.1.1 Verification

| Claim | Verdict | Evidence |
|---|---|---|
| Scoring ignores who logged the step | ✅ Confirmed | `compute_scores(steps, rules)` (L104–L113) loops over `watched` steps and credits `step_team(step, rules)`, which reads **only** `movie_release_year` / `movie_origin_country`. `RunStep.logged_by_user_id` (`models/run.py` L84) is populated by `_log_step` (`routes_runs.py` L666) but never read by the engine. |
| ±1 periodic deadlock | ✅ Confirmed (proof below) | |
| Neutral films are wasted turns | ✅ Confirmed | `step_team` returns `None` for 1975–2005 / unknown country; `compute_scores` skips it. No carry-over effect. |
| 🆕 The seed scores | 🆕 Bug | `create_run` inserts the seed as `status="watched"`, `logged_by_user_id=current_user.id` (`routes_runs.py` L473–L481), and `compute_scores` counts it. A pre-1975 seed starts Team A at **+1**. With `target_lead=2` (the allowed minimum) Team A is one film from winning before Team B has moved. |
| 🆕 No turn order | 🆕 Gap | No turn/alternation check anywhere (`grep whose_turn\|current_turn\|turn_order` → none). One player can log `target_lead` films in a row and win alone. |
| 🆕 Shared-device problem | 🆕 Constraint | `RunStepCreate` (`schemas/runs.py` L60) has no "picked by" field. In the living-room scenario one logged-in account logs **both** players' picks, so attributing by `logged_by_user_id` alone would credit everything to one team. `logged_by_user_id` also means "who watched" for `services/passport.py` (L67, L127), so **its meaning must not change**. The Golden Veto (`routes_runs.py` L1006) has the same shared-device blind spot. |
| Copy says "every watched film scores one point" | 🆕 Bug | `RunsPage.tsx` Tug panel and the engine `description`. False for neutral films. |

**Deadlock proof (current rules).** Let `L = A − B`. Each player, acting rationally, only logs films in their own territory (a film in the opponent's territory gives the opponent the point, so it is strictly dominated). With alternation, `L` follows `0 → +1 → 0 → +1 …`. For any `target_lead ≥ 2` the inequality `|L| ≥ target_lead` is never true, so the run can only end by forfeit. Neutral films keep `L` constant. The engine therefore rewards the *least* interesting strategy, and the only "skill" left (finding a cast link back into your own era) is symmetric.

**Strategic asset the current design wastes:** the chain is **shared**. My pick becomes my opponent's frontier, and that frontier's cast decides which territory they can reach. Positional denial is the real game, and the scoring should reward it.

#### 3.1.2 Programmatic changes

**(a) Explicit, turn-validated attribution (prerequisite for everything else)**

- `schemas/runs.py` `RunStepCreate`: add `tug_team: Literal["team_a","team_b"] | None = None`. For a shared device it defaults to the team of `current_user`.
- `routes_runs.py` `_enforce_run_rules` (Tug only): resolve `team = payload.tug_team or team_of(current_user)`. Refuse with **409** "It's <name>'s pull" when `team == last_scoring_turn_team` (alternation). The seed and planned steps don't count as turns. Stamp `extra_metadata["tug_team"] = team`.
- Add `"tug_team"` to `SERVER_OWNED_METADATA` (`routes_runs.py` L108) so `create_step` strips it and `PATCH step` re-applies the stored value (the same pattern as `collision`/`tunnel_side`).
- Blind Fork in a Tug run: `accept_fork_movie` (`routes_runs.py` L956, `_log_step` call at L975) must stamp `tug_team = team_of(fork.offered_by_id)`. The offerer made the move; the partner only vetoed.
- Legacy steps without `tug_team` fall back to `team_of(step.logged_by_user_id)`, so old runs re-score deterministically.

**(b) Pure tally replacing `compute_scores`**

`compute_scores` returns a bare dict today. Replace it with a pure fold that keeps the "always recomputed from steps" invariant from LESSONS 24b:

```python
@dataclass(frozen=True)
class Pull:            # one per watched, non-seed step, for the UI's rope history
    step_id: str; puller: str; territory: str | None
    kind: Literal["home", "invasion", "neutral", "sudden_neutral"]
    points: int; streak: int; multiplier: int

@dataclass(frozen=True)
class TugTally:
    scores: dict[str, int]          # {"team_a": n, "team_b": n}
    streak: tuple[str | None, int]  # (team, length)
    anchor: str | None              # team holding a Neutral Anchor (x2 next scoring pull)
    effective_target: int           # target after Sudden Death shrink
    sudden_death: bool
    pulls: list[Pull]

def tally(steps, rules, players) -> TugTally: ...
```

Fold rules, applied per watched step in `logged_at` order, **skipping step 0 (seed)**. Fixes the 🆕 seed bug.

| Pick | Effect | Game-theory purpose |
|---|---|---|
| **Home pull** (puller's own territory) | `+base` to the puller, where `base = min(streak_len, 3)` (**Streak Momentum** +1, +2, +3). | Rewards keeping the opponent off the scoreboard. |
| **Invasion Steal** (opponent's territory) | `+1` to the puller **and** `−1` to the opponent (floor 0). Breaks the opponent's streak. | Turns the dominated move into a real option: a 2-point swing, at the risk of leaving the frontier in enemy territory for the opponent's next home pull. |
| **Neutral Anchor** (1975–2005 / unknown country) | 0 points. The puller gains `anchor`: their **next** scoring pull is ×2. Resets *both* streaks. Anchors don't stack. | A tempo sacrifice that also defuses an opponent streak. |
| **Sudden Death** after `sudden_death_after` scored turns (default 12) | `effective_target = max(1, target_lead − ⌊(turns − sudden_death_after) / sudden_death_every⌋)` (default every 2 turns). In Sudden Death a neutral pick concedes 1 point to the opponent (`sudden_neutral`). | **Guaranteed termination** (proof below). |

The streak is counted per team over consecutive **turns** in which the opposing team scored nothing (alternation is enforced, so "consecutive" means across the opponent's blank turns).

**Termination proof (new rules).** Sudden Death starts at turn `S`, and the target falls by 1 every `E` turns, so by turn `S + E·(target_lead − 1)` it is 1. From then on every pick changes `L`: home/invasion moves it by ≥ 1, and a neutral pick concedes 1. So the first pick that leaves `L ≠ 0` wins. With `L = 0`, any pick produces `|L| ≥ 1 = target`. **The run ends at the latest on turn `S + E·(target_lead − 1) + 1`.** With the defaults (`S = 12`, `E = 2`, `target_lead = 4`) that is turn 19.

**(c) Touch-points**

| Location | Change |
|---|---|
| `tug_of_war.py` `compute_scores` / `leading_team` | Replace with `tally()` / `winner(tally)`. Keep `compute_scores` as a thin wrapper returning `tally.scores` for backward compatibility with `test_fork_veto_tug.py` until those tests are updated. |
| `tug_of_war.py` `sync_run_state` | Cache `tug_scores`, plus new server-owned `tug_momentum = {streak_team, streak, anchor, effective_target, sudden_death}`. |
| `tug_of_war.py` `validate_rules_config` | Accept `steal_enabled` (bool, default true), `momentum_cap` (1–5, default 3), `sudden_death_after` (4–50, default 12), `sudden_death_every` (1–10, default 2). Legacy runs (no keys) keep old behaviour **only if** `engine_version` is legacy; V2 runs get the new defaults. |
| `services/blind_fork.py` `SERVER_OWNED_RULES` | Add `"tug_momentum"`. |
| `tug_of_war.py` `evaluate_run_outcome` | Use `tally.effective_target`. Victory copy: "Tug of War won by Ana, 7–3 (Sudden Death)!". |
| `discover_candidates` (inherited from `CineChainEngine`) | **Override** to annotate each candidate with `tug_effect: "home" \| "invasion" \| "neutral"` and `tug_points` for the current puller, so Pick Next shows "+2 🔥 streak" / "⚔️ Steal (2-pt swing)" / "⚓ Anchor ×2" chips. This is the payoff that makes the mechanics legible. |
| `schemas/discovery.py` `DiscoveryCandidate` | Add optional `tug_effect`, `tug_points`. |
| Frontend `TugOfWarMeter.tsx`, `lib/tugOfWar.ts` | Render `effective_target` (the shrinking end-zones), streak flames, the anchor icon, "Next pull: <name>" and a pull-history ribbon from `pulls`. Fix the creation copy. |

#### 3.1.3 Synergies & interference

- **Golden Veto:** it already acts as a hard counter (it removes the partner's latest step). Under the new fold it also cancels that step's streak/anchor effects for free, because everything is recomputed. It must use `tug_team` (not `logged_by_user_id`) to decide "partner's step", or the shared-device case breaks.
- **Blind Fork:** the offerer picks 3 films, so they can offer three *invasions*. The partner's veto becomes a real defensive choice. Synergy is positive; the attribution rule in (a) is mandatory.
- **Chaos Button** (one-step handicap): it is cleared after one step in `_log_step`, so it composes with the fold without changes.
- **Bounty Board:** independent. Wildcards don't touch Tug scoring.

### 3.2 Meet in the Middle (`backend/app/engines/meet_in_middle.py`)

#### 3.2.1 Verification

| Claim | Verdict | Evidence |
|---|---|---|
| "`collides` relies on a fast, time-boxed BFS (`DISTANCE_MAX_DEPTH = 5`)" | ❌ Refuted | `collides()` (L95–L111) is a **single-hop** `validate_next_step(frontier, movie_id)` against the opposite frontier, which is exact and cheap. The BFS (`solve_bridge_bipartite`, `max_depth=5`, `max_duration_seconds=8`) is used **only** by `distance()` (L113–L133), the advisory "~N hops" indicator served by `GET /runs/{id}/tunnel` (`routes_runs.py` L1055). **The depth limit never makes the run unwinnable**: it only blinds the indicator ("No route within 5 hops yet."). |
| Players get stuck with obscure films | ✅ Confirmed (as a *navigation* problem) | With the indicator null, the UI offers no direction. Players also can't see *which* actor/film the shortest path goes through, although the BFS already found it. |
| 🆕 The shortest path is computed and then thrown away | 🆕 | `distance()` keeps only `event["hops"]`. `_finish_result` → `_build_result` (`services/pathfinder.py` ~L490–L530) also returns `path` (movie nodes) and `connections` (the actor/director per hop). **Bridge Hints need no new search algorithm.** |
| 🆕 Collision only counts the opposite *frontier* | 🆕 Design note | A pick that shares cast with an *older* film on the partner's track is a silent near miss: nothing tells the players they were one swap away. |
| 🆕 Frontier Swap already partly exists | ⚠️ Corrected | `TunnelTimeline.TunnelCard` already offers "Undo" on a side's newest step (`removable`), which reopens the previous frontier. The baseline's "Frontier Swap" therefore needs **no engine rule**: it needs a combined UI action (see below). |
| Indicator cost | ✅ OK | `useTunnelState` keys on `stepCount` with `staleTime: 60_000`, so the 8 s BFS runs at most once per step. It is ephemeral and in-request, which complies with the no-daemon constraint. |

#### 3.2.2 Programmatic changes

1. **`TunnelDistance` carries the path:** add `path_movie_ids: list[int]` and `connections: list[SharedActorConnection]` and fill them from the `result` event in `distance()`. `TunnelState` (`schemas/engine.py` L195) does **not** expose them (that would spoil the puzzle). They are only read by the hint route.
2. **Bridge Hint tokens:** `MeetInTheMiddleEngine.prepare_rules_config` sets `tunnel_hints_remaining = rules.get("tunnel_hints", 2)` (0–5). Add `"tunnel_hints_remaining"` to `SERVER_OWNED_RULES`. New route `POST /runs/{id}/tunnel/hint` with body `{side, level: "actor" | "film"}`:
   - Check every precondition **before** spending (run active, not collided, tokens > 0, a path was found). This follows the veto pattern from LESSONS 24b.
   - Re-run the search with an **escalated budget** (`max_depth=7`, `max_duration_seconds=20`), still in-request and ephemeral. The player paid, so a deeper search is justified.
   - `level="actor"` returns `connections[0]` from the requesting side (first link only, 1 token). `level="film"` returns the midpoint film `path[len//2]` (2 tokens).
   - Decrement and commit only after a hint is produced. "No path found" refunds the token by never spending it.
3. **Near-Miss collision hint:** in `_enforce_run_rules` (tunnel branch, `routes_runs.py` ~L194–L261), after a valid non-colliding step, check the new film against `opposing_steps[:-1]` (cast-only, bounded to the last 5 steps, all cache hits). On a match, stamp server-owned `near_miss_with = <step title>`; the UI then shows "💫 You brushed Partner B's chain at *Heat* (2 steps back)". Add `near_miss_with` to `SERVER_OWNED_METADATA`. **The win rule does not change.**
4. **Hot/Cold trend:** the frontend keeps the previous `distance_hops` (React Query `placeholderData` / previous key) and shows ▲ warmer / ▼ colder / = in `TunnelTimeline`'s trench. No backend change.
5. **Frontier Swap (UI only):** `TunnelFrontierCard` gets "↔ Swap frontier", which calls the existing step delete and then opens `PickNextHub` on the side's previous frontier, excluding the swapped-out film. No new rule. A swap is just Undo + Pick.

### 3.3 The Rabbit Hole (`backend/app/engines/rabbit_hole.py`)

#### 3.3.1 Verification

| Claim | Verdict | Evidence |
|---|---|---|
| Tier 5 "B-Movie Abyss" = rating < 6.0, at depth 20+ | ✅ Confirmed | `TIERS` (L55–L61), `compliance()` tier 5 → `rating_of(...) < 6.0`. |
| Tier 5 is "exceptionally difficult" | ⚠️ Corrected | Tiers are **not cumulative**. Tier 5 requires only `rating < 6.0` + the cast link, not pre-2000/non-English/short. **🆕 Data bug:** `rating_of` (`services/movie_filters.py` L52–L61) falls back to `CachedMovie.vote_average`, and **no `vote_count` is cached** (`models/cache.py` L26). TMDB reports `0.0` for unrated films, so obscure unrated films are marked **compliant ✓** at Tier 5. In practice Tier 5 is *easier* than intended, and Pick Next shows a false green badge. The likely difficulty wall is the **Tier 2 → 3** switch (English pre-2000 frontier → non-English) at depth 10. This is a hypothesis; verify it with the telemetry query in 3.3.3. |
| "Out of lives ⇒ run failed" | ⚠️ Corrected | Zero lives does **not** fail the run. `_enforce_run_rules` refuses further forced hops (409 "No lives remaining"), and only `PATCH status=forfeited` at zero lives becomes `failed` via `forfeit_outcome` (L157–L166). **🆕 Soft-lock:** at 0 lives with an empty Pick Next pool there is no "dead end" detection. The player sees an empty grid and has to know to forfeit. |
| Bounty Board for extra lives | ⚠️ Conflicts with existing design | `RabbitHoleEngine.supports_bounty_board = False` (L133) is deliberate (LESSONS 27b: "has lives not wildcards → 422"). The block is about *what gets awarded*, not bounties themselves. |
| 🆕 No victory condition | 🆕 | `evaluate_run_outcome` is not overridden, so after Tier 5 the descent is endless. A rogue-like without an exit offers no closure and no "escape" goal. |

#### 3.3.2 Programmatic changes

1. **Bounty award hook:** add `BaseChallengeEngine.award_bounty(rules, completed_id, replacement_id) -> dict`, which by default delegates to `bounties.award` (+1 wildcard). `RabbitHoleEngine` overrides it with `lives_remaining = min(max_lives, lives + 1)`. In `_log_step` (`routes_runs.py` ~L645–L672), replace `bounties.award(...)` with `engine.award_bounty(...)`. Then flip `supports_bounty_board = True` for the Rabbit Hole. The board's existing wildcard pricing copy must say "❤️ +1 life" (`BountyBoardPanel.tsx`, `lib/bounties.ts`).
2. **Sacrificial Re-roll:** `POST /runs/{id}/rabbit-hole/reroll`. Preconditions (checked before spending): run active, `lives ≥ 2` (you can't spend your last life on a re-roll), current tier > 1, no override already active for this depth. Effect: set server-owned `tier_override = {"depth": d, "tier": n}` with `n` drawn from the *other* tiers in 2..5, and `lives -= 1`. `tier_for_depth(depth, rules)` checks the override when `depth == override.depth`. `sync_run_state` drops a stale override once `len(steps) > depth`. Add `tier_override` to `SERVER_OWNED_RULES`. Note: `tier_for_depth` gains a `rules` parameter, and every call site (`_needs_hydration`, `validate_primary`, `discover_candidates`, `tier_state`, `forfeit_outcome`) has to pass it.
3. **Dead-end detection:** in `discover_candidates`, if `kept == []` **and** `lives == 0`, set `RabbitHoleState.dead_end = True` on the `/constraint` response. `RabbitHoleHud` then shows "🕳️ No way down: Accept your fate (end run) / Search manually". It never auto-fails, because the pool is cache/TMDB-bounded and manual search may still find a film.
4. **Tier-5 data accuracy:** add a `cached_movies.vote_count` column (Alembic migration, filled at `cache_repo.py` L106 and L137 next to `vote_average`). In `rating_of`, ignore `vote_average` when `vote_count < 10` (return `None`, which means unverified). This keeps the "unknown never costs a life" rule but **stops showing a false ✓**. Unverified films are sorted after verified ones.
5. **Escape condition (optional rule):** `escape_depth` (default `None`, allowed 25–60). `evaluate_run_outcome` returns `COMPLETED` "Escaped the Rabbit Hole at Depth N with ❤️×k" when `len(steps) ≥ escape_depth`.

#### 3.3.3 Telemetry query (run before tuning tier numbers)

```sql
-- Where are lives actually lost? (SQLite)
SELECT (rn - 1) / 5 + 1 AS tier, COUNT(*) AS lives_lost
FROM (SELECT s.transition_metadata, ROW_NUMBER() OVER (PARTITION BY s.run_id ORDER BY s.logged_at) - 1 AS rn
      FROM run_steps s JOIN runs r ON r.id = s.run_id WHERE r.game_type = 'rabbit_hole')
WHERE json_extract(transition_metadata, '$.life_lost') = 1
GROUP BY tier ORDER BY tier;
```

🆕 Caveat: `life_lost` (stamped at `routes_runs.py` L320) is **not** in `SERVER_OWNED_METADATA`, so a client can send it on a normal step. It has no gameplay effect, but it pollutes this telemetry. Add it to the tuple together with the new keys.

### 3.4 Historical Time-Travel (baseline §2, brief verification)

✅ Confirmed: `pair_violation` (`historical_time_travel.py` L92–L107) uses **strict** `year_b > year_a` / `<`. Two films set in the same year are also a violation, which makes dense periods (e.g. multiple WW2 films set in 1944) harder than intended. The "Wormhole" proposal stays in the backlog. A cheap first step is to allow `year_b == year_a` when `rules.allow_same_year` is set. **Out of scope** for this blueprint unless requested; see `docs/planning/Future Game Modes & Engines Vault.md`.

### 3.5 Phase 3 severity summary

| ID | Severity | Finding |
|---|---|---|
| P3-01 | 🔴 Critical | Tug of War cannot end under rational alternating play (proved). Attribution-blind scoring. |
| P3-02 | 🟠 High 🆕 | Tug: no turn enforcement; one player can win alone. The seed pre-scores for Team A. |
| P3-03 | 🟠 High 🆕 | Shared-device attribution: no "picked by" input. `logged_by_user_id` must not be repurposed (Passport depends on it). |
| P3-04 | 🟡 Medium ❌/⚠️ | MitM: collision is exact (baseline refuted); the real gap is navigation. The BFS path is computed and discarded, so hints are cheap. |
| P3-05 | 🟡 Medium 🆕 | Rabbit Hole: soft-lock at 0 lives with an empty pool; no escape/victory condition. |
| P3-06 | 🟡 Medium 🆕 | Rabbit Hole Tier 5: unrated films (`vote_average 0.0`) get a false ✓ badge. |
| P3-07 | ⚪ Low | Time-Travel: strict inequality blocks same-year settings. |

---

## Phase 4: Integrations & End-of-Run Polishing

### 4.1 Watchlist sync status (`CuratedCanonsCard.tsx` ↔ `routes_curated.py`)

#### 4.1.1 Verification

| Claim | Verdict | Evidence |
|---|---|---|
| `watchlistSyncedAt` is ephemeral React state | ✅ Confirmed | `CuratedCanonsCard.tsx` L28 `useState<string \| null>(null)`, set only in the mutation's `onSuccess` (L56) to the **client clock** (`new Date().toISOString()`), not the server's `synced_at`. Unmounting (navigating away from Settings) resets it, and `SyncBadge` (L223) then renders "Never synced". |
| Username is dropped | ✅ Confirmed | `watchlistUsername` (L27) is also ephemeral. The form comes back empty on every visit. |
| No `GET /watchlist/status` endpoint | ✅ Confirmed | `routes_curated.py` exposes only `POST /curated/watchlist/sync` (L354). No other route reads `LetterboxdWatchlist` apart from `routes_tools.py` (Bingo, L144/L204), which returns films, not sync state. |
| 🆕 The data needed already exists | 🆕 | `LetterboxdWatchlist` (`models/curated.py` L76–L87) stores `letterboxd_username` and `synced_at` **on every row**. `_persist_watchlist` (L390–L425) replaces rows atomically. Status = `MAX(synced_at)`, `COUNT(*)` and the username for `user_id`, so **no migration is needed for the common case**. |
| 🆕 Edge case: empty watchlist | 🆕 | A successful sync of a **public but empty** watchlist writes 0 rows, so the status is indistinguishable from "never synced" and the username is lost. Fixing this needs persisted per-user sync metadata (see 4.1.2 option B). |
| 🆕 In-flight progress is lost too | 🆕 | The card runs `runTask` inside a `useMutation` (L31–L66). Navigating away mid-sync drops the progress text, and coming back shows an idle button, even though the `SystemTask` (`name="watchlist_sync"`, `routes_curated.py` L384) is still running. A resumable hook **already exists**: `useTrackedTask({ resumeNames })` (`lib/useTrackedTask.ts`), used by `ImportHistory.tsx` L86, polls `/tasks?active=true` on mount. |
| 🆕 No downstream invalidation | 🆕 | After a sync nothing is invalidated. `BracketSeedPicker` ("seed from watchlist") and `BingoPage` read the watchlist through separate calls. Bingo fetches on mount, so it is OK; any future cached watchlist query would go stale. |

#### 4.1.2 Fix design

**Backend** (`backend/app/api/routes_curated.py`; the curated schemas live inline there, next to `WatchlistSyncRequest` at L350):

```python
class WatchlistStatus(BaseModel):
    letterboxd_username: str | None
    synced_at: datetime | None
    total_items: int

@router.get("/watchlist/status", response_model=WatchlistStatus)
def get_watchlist_status(session=Depends(get_session), current_user=Depends(get_current_user)):
    row = session.exec(
        select(LetterboxdWatchlist.letterboxd_username,
               func.max(LetterboxdWatchlist.synced_at), func.count())
        .where(LetterboxdWatchlist.user_id == current_user.id)
        .group_by(LetterboxdWatchlist.letterboxd_username)
        .order_by(func.max(LetterboxdWatchlist.synced_at).desc())).first()
    ...
```

- **Option A (no migration, ships first):** the query above. It covers every non-empty watchlist.
- **Option B (complete):** add `users.letterboxd_username` and `users.watchlist_synced_at` (Alembic migration; backfill from `MAX(synced_at)` per user). Write them inside `_persist_watchlist` in the **same transaction** as the row replace, so status and rows can never disagree. `get_watchlist_status` reads the user columns and counts the rows. This handles empty watchlists and pre-fills the username even after the list is cleared.
- `synced_at` is serialised as timezone-aware UTC (the existing `utcnow()` convention) so `SyncBadge`'s `Date.now() - new Date(syncedAt)` is correct.
- Respects constraints: there is no daemon. Status is a read-only aggregate, and the sync itself stays on the existing `task_runner` (FastAPI `BackgroundTasks`).

**Frontend:**

| File | Change |
|---|---|
| `lib/queries.ts` | `export const watchlistStatusKey = ["curated","watchlist","status"]` and `useWatchlistStatus()` (`staleTime: 30_000`). |
| `types/api.ts` | `WatchlistStatus` interface. |
| `components/CuratedCanonsCard.tsx` | Delete `watchlistSyncedAt` state. Use `<SyncBadge syncedAt={status?.synced_at} />` plus "· N films". Pre-fill `watchlistUsername` from `status.letterboxd_username` once (a `useEffect` guarded by "user hasn't typed"). Replace the `useMutation(runTask)` with `useTrackedTask({ resumeNames: ["watchlist_sync"], onFinished })`. `onFinished` invalidates `watchlistStatusKey` and keeps the `watchlist_not_found` warning / toast branches. Progress text comes from `describeProgress(tracked.task)`. |

### 4.2 March Madness championship (`BracketView.tsx`)

#### 4.2.1 Verification

| Claim | Verdict | Evidence |
|---|---|---|
| `Podium` lacks `OnServerBadge` / `AcquisitionControl` | ✅ Confirmed | `Podium` (L128–L156) renders `Trophy`, `MoviePoster`, title and year only. |
| `MatchupCard` lacks them | ✅ Confirmed | `MatchupCard` (L159–L309) film `article` (L244–L292) renders poster, title, year/runtime, logline, votes, Vote / Advance buttons. No server state. |
| 🆕 `MatchupBox` (the bracket grid) has none either | 🆕 | L76–L126: a text-only row per film. |
| 🆕 No Jellyfin lookup in the component at all | 🆕 | `BracketView` imports no integration hooks. Every other surface (`PickNextHub` L372 & L1327, `MovieDetailModal` L61, `MoviePreviewModal` L37, `ForkInTheRoadModal` L109) **inlines its own** `useQuery(["jellyfin","lookup",ids])` → `POST /integrations/jellyfin/lookup`. There is no shared hook. |
| 🆕 `play_url` is never rendered | 🆕 | `JellyfinItemSummary.play_url` (`types/api.ts` L657) is built by `integrations/jellyfin.py` L122/L183 but no `.tsx` uses it. The natural place for "▶ Play on Jellyfin" is the champion. |
| 🆕 Clipping hazard | 🆕 | `Podium` sits **inside** the bracket's `overflow-x-auto` scroller (L38). `AcquisitionControl`'s "more options" menu is `absolute right-0 top-full` (`AcquisitionControl.tsx` L116), so it would be clipped there, the same defect class as `PitchButton` (P2-03). |

#### 4.2.2 Exact injection points

1. **Shared hook:** add `useJellyfinLookup(tmdbIds: number[])` to `lib/queries.ts`, keeping the existing key shape `["jellyfin","lookup",ids]` so caches are shared. Migrating the 5 inline copies to it is optional cleanup.
2. **`BracketView` (root, ~L27):** `const filmIds = Object.keys(films).map(Number);` then `const { data: server } = useJellyfinLookup(filmIds);`. That is one bulk call for all 16 contenders. Pass `server` down.
3. **`MatchupBox` (L111–L113, inside each row `<span>`):** a 6 px emerald dot with `title="On your server"` when `server[id]?.on_server`. Cards stay compact (`w-48`), so no full badge here.
4. **`MatchupCard` article (after the title/year block, ~L252):**
   ```tsx
   <div className="flex flex-wrap items-center justify-center gap-1.5">
     <OnServerBadge onServer={server?.[String(id)]?.on_server} />
     <AcquisitionControl tmdbId={id} title={film.title} onServer={server?.[String(id)]?.on_server} />
   </div>
   ```
   The `MatchupCard` renders inside `Modal` (portal, outside the bracket scroller), but `Modal`'s body is `overflow-y-auto`, so the dropdown should use the Phase 2 `Popover` primitive. Also apply `onServerCardClass` to the `article` for the green tint used on Pick Next cards.
5. **Champion experience (`Podium` + a new `ChampionBanner`):** keep the in-scroller `Podium` minimal (add `OnServerBadge` only, below the year at ~L148). Add a **`ChampionBanner`** rendered by `BracketView` **above** the scroller (outside `overflow-x-auto`) when `bracket.champion !== null`. It shows a large poster, title, `OnServerBadge`, **"▶ Play on Jellyfin"** (`<a href={play_url} target="_blank" rel="noreferrer">`) when `on_server`, else `AcquisitionControl` ("Request on Radarr/Seerr"), and the logline via `ExpandableText`. This is the end-of-run payoff and the only place the champion is actionable.
6. **`RunDetailPage.tsx` L234:** no change; `BracketView` stays self-contained.

### 4.3 Cross-cutting synergy notes

- The **Popover primitive** (Phase 2 §2.5-C) is a shared dependency of `PitchButton` (P2-03) and `AcquisitionControl` in the bracket (4.2). Build it once, in the Phase 2 rollout, before Phase 4 starts.
- **`ExpandableText`** is reused by `HeroSeedPreview` (Phase 1), `MatchupCard`/`ChampionBanner` (Phase 4) and all Phase 2 sites.
- **`useJellyfinLookup`** can also power an "On Server" badge on the Phase 1 `HeroSeedPreview` ("start your run with something you can watch tonight").
- **Server-owned keys** added across phases (`tug_team`, `tug_momentum`, `tunnel_hints_remaining`, `near_miss_with`, `tier_override`, `life_lost`) must all be registered in `SERVER_OWNED_METADATA` (`routes_runs.py` L108) or `SERVER_OWNED_RULES` (`services/blind_fork.py` L18) **in the same commit that introduces them**, with a forge-attempt test (the established pattern in `test_fork_veto_tug.py::test_clients_cannot_forge_a_pending_fork_or_scores`).

### 4.4 Phase 4 severity summary

| ID | Severity | Finding |
|---|---|---|
| P4-01 | 🟠 High | Watchlist status and username are lost on navigation; there is no status endpoint, although the DB already holds the data. |
| P4-02 | 🟡 Medium 🆕 | In-flight watchlist sync progress is not resumable; `useTrackedTask(resumeNames)` already exists and is unused here. |
| P4-03 | 🟡 Medium 🆕 | An empty public watchlist looks like "never synced" (requires persisted user sync metadata). |
| P4-04 | 🟠 High | Bracket (grid, matchup modal, podium) has zero media-server visibility; the champion can't be played or requested. |
| P4-05 | ⚪ Low 🆕 | `play_url` is shipped by the API but never rendered anywhere. |
| P4-06 | ⚪ Low 🆕 | Jellyfin lookup is copy-pasted inline in 5 components; there is no shared hook. |

---

## Appendix: Consolidated Finding Index

| Phase | 🔴 | 🟠 | 🟡 | ⚪ | Refuted/corrected baseline items |
|---|---|---|---|---|---|
| 1 Run creation | 0 | 2 | 3 | 1 | Empty states (⚠️ the component exists but has no CTA) |
| 2 Text/UI | 0 | 3 | 2 | 1 | `CuratedListCard`, `BridgePathView` (❌); `PitchButton` (⚠️ clipping, not clamp) |
| 3 Engines | 1 | 2 | 3 | 1 | MitM collision BFS (❌); Rabbit Hole "out of lives = failed" and Tier-5 difficulty (⚠️); Bounty Board conflict (⚠️) |
| 4 Integrations | 0 | 2 | 2 | 2 | none; all confirmed, plus 4 new |
