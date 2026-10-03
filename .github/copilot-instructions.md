# CineChain Copilot Directives

1. **Context First:** Before writing any code or proposing solutions, you MUST read `docs/STATE.md` to understand the current objective and `docs/LESSONS.md` to avoid known pitfalls.
2. **State Management:** When you complete a task or resolve a significant bug, you MUST remind the human to update `docs/STATE.md` and append any new learnings to `docs/LESSONS.md`.
3. **Constraint Enforcement:** Never suggest Redis, Celery, cron jobs, or heavy vector databases. All background tasks must be ephemeral. All ML/Graph models must be loaded JIT and garbage collected.
4. **Documentation Sync:** If a task involves game mechanics or UI evolution, ask the human if you should read specific files in `docs/planning/` before proceeding.
5. **Git Commits:** Upon successfully completing a phase and verifying the code compiles/tests pass, you MUST automatically stage and commit your changes using a descriptive conventional commit message.
