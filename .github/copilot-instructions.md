# CineChain Copilot Directives

1. **Context First:** Read `docs/STATE.md` and `docs/LESSONS.md` before starting any task, before writing code or proposing solutions. `STATE.md` holds the current objective; `LESSONS.md` lists known pitfalls.
2. **State Management:** When you complete a phase or resolve a significant bug, update `docs/STATE.md` and append new learnings to `docs/LESSONS.md` (existing format), then remind the human that you did.
3. **Constraint Enforcement:** Never introduce Redis, Celery, cron jobs, message brokers or vector databases. All background tasks must be ephemeral (the existing `task_runner`). **Do not add new ML models or ML dependencies.** The existing models (ONNX embeddings, local Qwen) must stay loaded JIT and garbage collected.
4. **Documentation Sync:** For game-mechanics or UI work, the active blueprint is `docs/UX_IMPLEMENTATION_PLAN.md` (evidence in `docs/MASTER_UX_AUDIT.md`). Read `docs/planning/` only if the task references it; if unsure, ask.
5. **Git Commits:** After a phase compiles and its tests pass, stage and commit the changes with a descriptive conventional commit message.
6. **Missing Things → Ask:** If you cannot find a referenced file, symbol or dependency, do not assume it exists or try to reconstruct it yourself. Pause and ask the human for clarification. (Files the plan marks **(new)** are the only exception.)
7. **Validate Per Phase:** Run the backend tests (`cd backend && uv run pytest -q`) and lint (`uv run ruff check .`), check for a single Alembic head when migrations change, and run the frontend build (`cd frontend && npm run build`) before every commit.
8. **Protect User Data:** Never run dev servers, migrations or tests against `config/cinechain.db`; use a scratch `CONFIG_DIR`. Never print secrets from `.env`, and never trigger Radarr/Seerr requests against configured services.
9. **Server-Owned Keys:** Every new server-computed metadata/rules key goes into `SERVER_OWNED_METADATA` / `SERVER_OWNED_RULES` in the same commit, with a forge-attempt test. Never repurpose `RunStep.logged_by_user_id` for game attribution.
10. **Full Playbook:** Follow `docs/AGENT_PLAYBOOK.md` for the complete handoff rules, validation gates and phase checklist.
