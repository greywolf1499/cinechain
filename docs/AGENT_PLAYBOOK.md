# CineChain Implementation Agent Playbook

**Audience:** the automated implementation agent executing [`UX_IMPLEMENTATION_PLAN.md`](./UX_IMPLEMENTATION_PLAN.md).
**Authority:** these rules are binding. They extend [`.github/copilot-instructions.md`](../.github/copilot-instructions.md); if the two ever disagree, the stricter rule wins.

---

## 1. Before every task

1. **Read `docs/STATE.md` and `docs/LESSONS.md` before starting any task.** `STATE.md` says which phase is current. `LESSONS.md` lists pitfalls that have already cost time (stub hydration, server-owned keys, SAVEPOINTs, shared-device attribution, TMDB `0.0` ratings, the root-owned Vite cache).
2. Read the phase section of `docs/UX_IMPLEMENTATION_PLAN.md` **and** the referenced finding rows in `docs/MASTER_UX_AUDIT.md` (`P*-**` static, `D-**` runtime). The audit holds the evidence; the plan holds the change.
3. **Re-verify line numbers before editing.** Every `L123` reference was correct at commit `dd54626`. Locate code by symbol name (function/component), not by line, and expect drift once earlier phases land.
4. Work on **one phase (or sub-phase) at a time**, in the order given in `STATE.md`. Don't start the next phase until the current one is validated and committed.

## 2. Missing files, dependencies or ambiguity: stop and ask

- **If you cannot find a referenced file, symbol or dependency, do not assume it exists or reconstruct it yourself. Pause and ask the human for clarification.** "Reconstruct" includes recreating a deleted helper from memory, guessing a module path, or adding a package because an import fails.
- The exception is files the plan explicitly marks **(new)**: create those as specified.
- Also stop and ask when:
  - a plan step conflicts with the current code in a way the plan doesn't anticipate (e.g. the function's signature changed);
  - a change would alter **public API shapes** beyond what the plan states;
  - a test that the plan says must "stay green" fails for reasons outside the phase's scope;
  - you are about to delete or migrate user data.
- When you ask, state what you looked for, where you looked (commands/paths) and the options you see.

## 3. Hard constraints (never violate)

| Rule | Detail |
|---|---|
| **No Redis, Celery, cron, message brokers or vector databases** | Background work goes only through the existing ephemeral `app/services/task_runner.py` (FastAPI `BackgroundTasks` + `SystemTask` rows). |
| **No new ML models or ML dependencies** | Don't add packages such as `torch`, `transformers`, `sentence-transformers`, `onnxruntime` variants or new GGUF/ONNX models. The existing JIT-loaded models (Arctic ONNX embeddings, local Qwen via `llama-cpp-python`) stay as they are: loaded just-in-time and garbage collected, never kept resident. |
| **No new heavy frontend libraries** | The primitives in Phase 0 (`Popover`, `ExpandableText`, modal focus trap, Pick Next paging) are hand-written. No virtualisation, positioning or headless-UI packages unless the human approves. |
| **Searches stay in-request and time-boxed** | Pathfinding/BFS must keep explicit `max_depth` and `max_duration_seconds` budgets. |
| **Server-owned state** | Any key the server computes (`tug_team`, `tug_momentum`, `seed`, `tunnel_distance`, `tunnel_hints_remaining`, `near_miss_with`, `tier_override`, `life_lost`, …) must be added to `SERVER_OWNED_METADATA` (`app/api/routes_runs.py`) or `SERVER_OWNED_RULES` (`app/services/blind_fork.py`) **in the same commit**, with a forge-attempt test. |
| **Pre-check before spending** | Tokens, lives, hints and vetoes are only spent after every precondition passes. |
| **Never repurpose `logged_by_user_id`** | It means "the authenticated account that logged this step" and feeds Passport. Game attribution uses explicit fields. |
| **No secrets in code or docs** | Read keys from settings/env; never print `.env` values. |
| **Don't touch the user's real data** | Never run migrations, tests or dev servers against `config/cinechain.db`. Use a scratch `CONFIG_DIR` (e.g. `/tmp/cinechain-dev`). Never fire Radarr/Seerr "Request/Add" calls against configured homelab services. |

## 4. Validation gates (every phase)

Run the smallest set that covers the phase, and all of it before committing.

| Scope | Command (from repo root) | Must be |
|---|---|---|
| Backend tests | `cd backend && uv run pytest -q` (or `just backend-test`) | all green; new tests listed in the phase exist and pass |
| Backend lint | `cd backend && uv run ruff check .` (or `just backend-lint`) | clean |
| Migrations (if any) | `cd backend && CONFIG_DIR=/tmp/cinechain-dev uv run alembic upgrade head && uv run alembic heads` | upgrade succeeds on a fresh DB; **exactly one head** |
| Frontend | `cd frontend && npm run build` (or `just frontend-build`) | `tsc` + `vite build` clean. There is no frontend unit-test runner; don't add one without approval. |
| Live check | the phase's **Acceptance** / **Live re-check** bullets | re-measured, results noted in the commit body or `LESSONS.md` |

Live checks, when the phase asks for them:

```bash
# backend on a scratch DB (loads the TMDB key from .env without printing it)
cd backend && set -a && . ../.env && set +a && export CONFIG_DIR=/tmp/cinechain-dev \
  && uv run alembic upgrade head && uv run uvicorn app.main:app --port 8787
# frontend; if node_modules/.vite is root-owned, use a wrapper config with cacheDir=/tmp/... (see LESSONS)
cd frontend && npm run dev
```

Stop every server you start before finishing the phase.

If a gate fails in code you changed, fix it before moving on. If it fails in code you didn't touch, record the failure and ask (§2).

## 5. Commits & bookkeeping

1. One **conventional commit** per phase/sub-phase, using the message suggested in the plan (`feat(ui): …`, `fix(cache): …`). Include the `Co-authored-by` trailer required by the environment.
2. Commit only after the §4 gates pass. Never commit secrets, scratch DBs or build output.
3. After each phase, **update `docs/STATE.md`** (current phase → next phase, status) and **append to `docs/LESSONS.md`** in the existing format (`## Phase X: …`, then bullets for problems, decisions, files and how it was verified). Then remind the human that both were updated.
4. If a finding turns out to be wrong during implementation, don't silently skip it. Add a short "Correction" note to `docs/MASTER_UX_AUDIT.md` (as §5.3 does) and update the plan's traceability row.

## 6. Scope discipline

- Make the changes the phase lists: complete and surgical. Don't fix unrelated issues you notice; add them to the plan's **Backlog** table instead.
- Preserve the invariants in plan §1.2 (run creation) and the legacy (v1) Tug scoring path.
- Prefer existing helpers (`useTrackedTask`, `useMovieDetail`, `EmptyState`, `Modal`, `cache_repo`, `task_runner`) over new ones. Create new modules only where the plan marks them **(new)**.
- `docs/planning/` holds long-range ideas (e.g. the Time-Travel Wormhole). Read it only if a phase references it, and never implement backlog items unasked.

## 7. Phase checklist (copy into your working notes)

```
[ ] Read STATE.md, LESSONS.md, the plan phase and its audit rows
[ ] Verified every referenced file/symbol exists (else: ask)
[ ] Implemented the phase rows only
[ ] Added/updated tests named in the phase (+ forge tests for new server-owned keys)
[ ] Backend: pytest + ruff (+ single alembic head)  |  Frontend: npm run build
[ ] Live/acceptance re-check (scratch CONFIG_DIR), servers stopped
[ ] Conventional commit with trailer
[ ] STATE.md + LESSONS.md updated; human reminded
```
