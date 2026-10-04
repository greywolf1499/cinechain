# CineChain

A self-hosted movie-challenge companion and discovery engine for cinephile households. Chain
films together by shared cast ("Six Degrees" style), get an AI-free bidirectional-BFS **Bridge
Solver** to connect two completely unrelated films, track your run's cultural "Passport" stats,
and see which films are already sitting on your Jellyfin server — all from one lightweight,
single-container app that fits comfortably alongside the rest of your homelab.

## Features

- **CineChain rule engine** — every film you log must share at least one credited actor with the
  previous one. No popularity, vote-count, decade, or language filters: every film TMDB catalogs
  is fair game, from silent-era classics to this week's festival premiere.
- **Ruleset presets** — Standard / Purist / Casual / Custom presets control repeat policy,
  consecutive-actor rules, minimum runtime, maximum cast depth considered, and a wildcard budget
  for breaking the chain on purpose.
- **JIT bipartite pathfinding (Bridge Solver)** — iterative bidirectional BFS over a
  just-in-time-cached movie↔actor graph finds the shortest path between any two films (e.g.
  bridge from Bollywood to classic Japanese cinema in 2–4 hops), streamed live over
  Server-Sent Events with progress, cache-hit/TMDB-call counters, and a "retry with higher depth"
  fallback.
- **Co-op multi-user runs** — accounts with a lightweight cookie session; runs are owned by a set
  of participants, and every logged film is attributed to whoever logged it.
- **Jellyfin "On Server" badges** — batched, cached lookups against your Jellyfin library so you
  instantly know which suggested/bridge films you can already press play on.
- **In-app integration settings** — TMDB token and Jellyfin URL/API key can be tested and changed
  live from the Settings page (admin-only), stored in SQLite and layered over `.env` — no
  container restart required.
- **Passport stats** — countries visited, decades traversed, and your most-frequent "keystone"
  connecting actors for any run.

## Game Modes & Engines

Every mode is a pluggable engine (see [Extensibility](#extensibility-adding-a-new-challenge-engine)),
and most can be mixed with composable modifiers, veto tokens, bounties and the Chaos Button.

| Mode                          | The rule                                                                                         |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| **CineChain**                 | The classic: every film shares a credited actor with the previous one.                           |
| **Canon-Only Island**         | Shared-cast chain, but every film must belong to one curated canon list.                         |
| **Auteur Relay**              | Alternate links: a shared actor, then a shared director, then an actor...                        |
| **Crew & Craft Trail**        | Chain through anyone who worked on a film: actor, composer, cinematographer, writer, director.   |
| **Genre Pendulum**            | Each film must share a genre with the last _and_ carry the swinging target genre.                |
| **Chrono Climb**              | Every film must be released after (Climb) or before (Descent) the last.                          |
| **Historical Time-Travel**    | Move forward or backward by the year a film's _story_ is set, not its release year.              |
| **World Cinema Passport**     | Every film must come from a different country than the last.                                     |
| **Semantic Trope Web**        | Each film must be a close plot/trope match to the last (on-device embeddings).                   |
| **Aesthetic Gradient**        | Fade from poster to poster: dominant colours must stay close.                                    |
| **Meet in the Middle**        | Co-op tunnel: two partners extend chains towards each other until they collide.                  |
| **Tug of War**                | Two partners pull a rope: each film scores for one side (old vs new, West vs the world).         |
| **Watchlist March Madness**   | 16-film single-elimination tournament seeded from your Letterboxd watchlist.                     |
| **The Method Actor Marathon** | Follow one actor's career in order, from debut to modern resurgence.                             |
| **The Auteur Marathon**       | Work through one director's features in release order.                                           |
| **Regional Deep Dive**        | Slice a canon list by country and/or decade and conquer the slice.                               |
| **The Rabbit Hole**           | Rogue-like survival: a nastier rule every five films, three lives.                               |
| **The Rotten Tomatoes Split** | Critics vs audiences: watch films the two disagree on and see whose side the household lands on. |
| **Decade Sieve**              | Work through one decade of cinema.                                                               |
| **Movie Night Roulette**      | Can't decide? Spin for a random cached film (filters, Blind Draft).                              |

Plus the **Bounty Board** wildcard quests, the **Chaos Button**, **The Chaser**, B-Side flips and
AI bracket commentary.

## Tools Suite

Under **Tools** in the nav (`/tools`):

- **Bridge Solver 2.0** — bidirectional-BFS path finder with path tags (canon-heavy, multi-country,
  epic runtimes), same-actor node swapping, alternate routes and "Search Deeper"; queue any
  bridge straight into a run. The search time ceiling is configurable up to 600 s.
- **Daily Bridge** — a Cine-Wordle style puzzle: one deterministic start/target pair per UTC day,
  graded hops, a shareable result grid and "convert to run".
- **Watchlist Bingo** — deals a bingo board of cinephile challenges and suggests matching films
  from your Letterboxd watchlist.
- **Run Map** — plots a run's journey across the world map with numbered pins and route legs.
- **Marathon Router** — reorders a film list for the smoothest tonal flow (a genre / era / runtime /
  rating "whiplash" optimiser) and queues it as a run.

## AI & Homelab Features

- **Zero-daemon architecture** — no Redis, Celery, vector DB or sidecar: background work runs on
  FastAPI tasks and everything lives in one SQLite file.
- **Arctic-Embed-XS ONNX** — the default embedding model runs in-process on CPU via ONNX Runtime
  (downloaded once into `/config`); `multilingual-e5-small` and `all-minilm-l6-v2` are selectable
  presets.
- **Opt-in Qwen 0.8B** — pitches, teasers, trope extraction and commentary can use a local GGUF
  Qwen model. It is **off by default**, loaded on demand and unloaded after
  `LLM_IDLE_TIMEOUT_SECONDS` idle. `llama-cpp-python` ships pre-installed in the Docker image (from
  its pre-built CPU wheels, so no compiler is involved). To enable it, pick **Local Qwen
  (In-Process)** in **Settings → Integrations** and click **Download & Enable Qwen 0.8B** - the
  ~530 MB model is fetched into `/config/models` with a live progress bar; there are no paths or
  URLs to configure.
- **Ollama / OpenAI-compatible support** — point embeddings and/or the generative model at your
  homelab's Ollama (or any OpenAI-compatible endpoint) instead; this is the easiest way to get
  generative features in Docker.
- **Graceful cold boot** — a fresh install with an empty cache and no TMDB key starts cleanly and
  answers with friendly hints (Daily Bridge `503`, Roulette `404`, empty seed suggestion) rather
  than errors.

## Architecture

CineChain is a single Docker image: a multi-stage build compiles the React/Vite/Tailwind frontend
into static assets, and a `python:3.12-slim` FastAPI process serves both the JSON API and the SPA
on port **8787**. Data lives in one SQLite database (WAL mode) at `/config/cinechain.db` — no
Postgres, Redis, or Neo4j required. Game rules are decoupled from the platform via a
`BaseChallengeEngine` Strategy Pattern (see [Extensibility](#extensibility-adding-a-new-challenge-engine)
below), and the whole thing is built to idle under **120 MB RAM** on a laptop-class homelab host
that's already running Jellyfin, the *Arr stack, and everything else.

## Homelab Quickstart

Drop this into your existing `docker-compose.yml` stack (adjust the network/volume paths to match
your setup):

```yaml
services:
  cinechain:
    image: cinechain:latest
    build: . # path to your cloned movie-bacon checkout
    container_name: cinechain
    restart: unless-stopped
    ports:
      - "8787:8787"
    environment:
      PUID: 1000
      PGID: 1000
      TZ: America/New_York
      TMDB_API_KEY: ${TMDB_API_KEY}
      JELLYFIN_URL: http://jellyfin:8096
      JELLYFIN_API_KEY: ${JELLYFIN_API_KEY}
    volumes:
      - ${DATA_ROOT:-./data}/configs/cinechain:/config
    # Optional: join the same user-defined network as Jellyfin so
    # `http://jellyfin:8096` resolves by container name.
    networks:
      - homelab

networks:
  homelab:
    external: true
```

Then:

```sh
cp .env.example .env   # fill in TMDB_API_KEY at minimum
docker compose build
docker compose up -d
```

First launch creates the SQLite DB and prompts you to create the first (admin) account at
`http://<host>:8787`. TMDB and Jellyfin credentials can also be entered later, live, from
**Settings → Integrations** without touching `.env` or restarting the container.

### `.env` reference

| Variable           | Required | Default   | Description                                                                                                                                                       |
| ------------------ | :------: | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TMDB_API_KEY`     |  Yes\*   | _(empty)_ | TMDB v4 read-access token. Get one free at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api). Can also be set/changed later in **Settings**. |
| `PUID`             |    No    | `1000`    | Host UID the container process runs as — match your media-share owner.                                                                                            |
| `PGID`             |    No    | `1000`    | Host GID the container process runs as.                                                                                                                           |
| `TZ`               |    No    | `Etc/UTC` | Timezone for log timestamps.                                                                                                                                      |
| `JELLYFIN_URL`     |    No    | _(empty)_ | Base URL of your Jellyfin server (e.g. `http://jellyfin:8096`). Leave empty to disable Jellyfin badges.                                                           |
| `JELLYFIN_API_KEY` |    No    | _(empty)_ | Jellyfin API key (Dashboard → API Keys).                                                                                                                          |
| `RADARR_URL`       |    No    | _(empty)_ | Radarr base URL (defaults to `http://radarr:7878` once a key is set). Powers the "Request" button when Seerr is not configured. Also editable in Settings.        |
| `RADARR_API_KEY`   |    No    | _(empty)_ | Radarr API key (Settings → General). Setting it enables Radarr.                                                                                                   |
| `SEERR_URL`        |    No    | _(empty)_ | Overseerr/Jellyseerr base URL (defaults to `http://seerr:5055` once a key is set). Also editable in Settings.                                                     |
| `SEERR_API_KEY`    |    No    | _(empty)_ | Seerr admin API key. Setting it enables one-click requests (Auto-Route or per-request folder/server/profile).                                                     |
| `CINECHAIN_PORT`   |    No    | `8787`    | Host port published (container always listens on 8787 internally).                                                                                                |
| `CONFIG_DIR`       |    No    | `/config` | Where the DB, secret key and downloaded models live. Leave as-is in Docker (mount a volume at `/config`); use a local folder for non-Docker dev.                 |
| `SECRET_KEY`       |    No    | _(empty)_ | Session-signing key. Blank = auto-generate and persist `secret.key` in the config dir (recommended).                                                              |
| `COOKIE_SECURE`    |    No    | `false`   | Set `true` when served over HTTPS (reverse proxy) so session cookies get the `Secure` flag.                                                                       |
| `OMDB_API_KEY`     |    No    | _(empty)_ | Optional OMDb key for IMDb / Rotten Tomatoes ratings.                                                                                                             |
| `EMBEDDING_PROVIDER` | No     | `local_onnx` | `local_onnx` (in-process), `ollama` or `openai` (OpenAI-compatible). Also editable in Settings.                                                                |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL` | No | _(empty)_ | Remote embedding endpoint, key and model name (ollama / openai only).                                                             |
| `ONNX_MODEL_PRESET` | No      | `arctic-embed-xs` | On-device model: `arctic-embed-xs`, `multilingual-e5-small` or `all-minilm-l6-v2` (alias of `EMBEDDING_LOCAL_PRESET`).                                  |
| `LLM_PROVIDER`     |    No    | `off`     | Generative model: `off`, `local_gguf` (Local Qwen, preinstalled in Docker), `ollama` or `openai`.                                                                              |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | No | _(empty)_ | Endpoint, key and model name for `ollama` / `openai`.                                                                                                |
| `LLM_IDLE_TIMEOUT_SECONDS` | No | `300` | `local_gguf` only: idle seconds before the model is unloaded (`0` = right after each call). Alias of `LLM_KEEP_ALIVE_SECONDS`.                                    |
| `BRIDGE_MAX_DURATION_SECONDS` | No | `45` | Wall-clock ceiling for one Bridge search (5-600).                                                                                                              |

\* `TMDB_API_KEY` can be left blank in `.env` and set later via **Settings → Integrations** instead
— both paths write to the same effective config (DB overrides always take precedence over `.env`).

## Backup & Persistence

Everything CineChain needs to persist lives under the single `/config` volume:

- **`/config/cinechain.db`** — the SQLite database: users, runs, logged films, the JIT TMDB
  adjacency cache, and any in-app integration overrides. This is the one file that actually
  matters for a backup.
- **`/config/secret.key`** — the signing key for session cookies, generated on first boot
  (`chmod 0600`). Back it up alongside the DB if you want existing logins to survive a restore;
  otherwise everyone just logs in again after a fresh key is generated.

Because it's WAL-mode SQLite, take backups while the container is **stopped** (or use
`sqlite3 /config/cinechain.db ".backup /config/backup.db"` for a safe hot backup if you need
zero downtime):

```sh
# Cold backup (simplest, recommended for a nightly cron job)
docker compose stop cinechain
tar czf cinechain-backup-$(date +%F).tar.gz -C /path/to/config .
docker compose start cinechain

# Hot backup (no downtime)
docker exec cinechain sqlite3 /config/cinechain.db ".backup '/config/backup.db'"
docker cp cinechain:/config/backup.db ./cinechain-backup-$(date +%F).db
```

To restore, stop the container, replace `cinechain.db` (and `secret.key` if you want existing
sessions to remain valid) in the `/config` volume, and start it back up — `alembic upgrade head`
runs automatically on boot and is a safe no-op if the schema is already current.

## Extensibility: Adding a New Challenge Engine

Every game mode is a subclass of `BaseChallengeEngine` (`app/engines/base.py`), registered by
`game_type` string in `app/engines/registry.py`. The platform (runs, participants, steps, movie
caching, Jellyfin lookups) never contains game-specific logic — it always goes through
`get_engine(game_type, session, tmdb)` and calls the four Strategy methods below.

```python
# app/engines/registry.py
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

from sqlmodel import Session

from app.models.run import RunStep
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services.tmdb import TMDBClient


class BaseChallengeEngine(ABC):
    game_type: str
    display_name: str
    description: str
    capabilities: ClassVar[list[str]]

    def __init__(self, session: Session, tmdb: TMDBClient) -> None:
        self.session = session
        self.tmdb = tmdb

    @abstractmethod
    async def validate_next_step(self, from_movie_id: int, to_movie_id: int) -> ValidationResult:
        """Is `to_movie_id` a legal next film after `from_movie_id`?"""

    @abstractmethod
    async def get_suggestions(
        self, current_movie_id: int, exclude_movie_ids: list[int], filters: SuggestionFilters
    ) -> list[Suggestion]:
        """Candidate next films reachable from `current_movie_id`."""

    @abstractmethod
    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        """Passport-style stats for a run."""

    @abstractmethod
    def solve_bridge(
        self, from_movie_id: int, to_movie_id: int, max_depth: int
    ) -> AsyncIterator[dict]:
        """Bidirectional-BFS bridge solve as a stream of events."""
```

### Worked example: `DirectorLadderEngine`

Say you want a mode where every film must share its **director** (not an actor) with the previous
one — a "Director Ladder" climb. Here's the shape of a new engine (illustrative — TMDB credits
only expose cast via the existing `cache_repo.get_movie_cast`/`get_actor_credits` helpers today, so
a real implementation would first add a small `get_movie_director` helper there, following the
same JIT-cache-then-TMDB-fallback pattern already used for cast):

```python
# app/engines/director_ladder.py
from typing import ClassVar

from app.engines.base import BaseChallengeEngine
from app.models.run import RunStep
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services import cache_repo


class DirectorLadderEngine(BaseChallengeEngine):
    game_type = "director_ladder"
    display_name = "Director Ladder"
    description = "Every film must share its director with the previous one."
    capabilities: ClassVar[list[str]] = ["validate_next_step", "get_suggestions", "compute_stats"]

    async def validate_next_step(self, from_movie_id: int, to_movie_id: int) -> ValidationResult:
        from_director = await cache_repo.get_movie_director(self.session, self.tmdb, from_movie_id)
        to_director = await cache_repo.get_movie_director(self.session, self.tmdb, to_movie_id)
        if from_director and from_director["id"] == to_director.get("id"):
            return ValidationResult(valid=True, connections=[])
        return ValidationResult(valid=False, reason="Different director", connections=[])

    async def get_suggestions(self, current_movie_id, exclude_movie_ids, filters):
        ...  # walk the current director's filmography, same shape as CineChainEngine

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        ...  # e.g. countries/decades unchanged, "keystone_actors" repurposed as directors

    def solve_bridge(self, from_movie_id, to_movie_id, max_depth=None, call_budget=None):
        raise NotImplementedError("Director Ladder has no bridge solver yet")
```

A few things worth knowing before you start:

- **Engines are stateful, not pure functions.** `__init__` binds `session`/`tmdb` once; the
  abstract methods only take domain args. `get_engine()` is the single place that constructs a
  ready instance per request — never instantiate an engine class directly elsewhere.
- **`capabilities` is advertised, not enforced.** `GET /api/engines` returns each engine's
  `capabilities` list so the frontend can hide UI for features an engine doesn't support (e.g. no
  Bridge Solver tab for an engine that hasn't implemented `solve_bridge`). Raise
  `NotImplementedError` for anything you don't support yet.
- **Concrete methods may accept extra *optional* keyword arguments** beyond the ABC's minimal
  signature (e.g. `CineChainEngine.validate_next_step` also accepts an optional `cast_limit`, and
  `solve_bridge` accepts optional `call_budget`) — this keeps the Strategy interface clean while
  still letting a run's rules (`rules_config`) or a caller's budget flow through to a specific
  engine's implementation.
- **Register it**, then it's immediately available everywhere — no route changes needed:

  ```python
  # app/engines/registry.py
  from app.engines.director_ladder import DirectorLadderEngine

  ENGINE_REGISTRY: dict[str, type[BaseChallengeEngine]] = {
      CineChainEngine.game_type: CineChainEngine,
      DirectorLadderEngine.game_type: DirectorLadderEngine,
  }
  ```

  The new `game_type` immediately shows up in `GET /api/engines` and can be selected in the
  "New Run" modal's Game Type dropdown — no other frontend changes required.

A `DecadeClimbEngine` (each film must be from a later decade than the last) follows the exact same
shape, just with a decade comparison instead of a cast/director-overlap check in
`validate_next_step`.

## Development

```sh
just backend-dev      # uv-managed FastAPI, hot reload, uses ./.dev-config
just frontend-dev      # Vite dev server
just backend-test      # uv run pytest
just backend-lint      # uv run ruff check .
just docker-build       # docker compose build (full production image)
just docker-dev         # docker-compose.dev.yml, live-mounted source
```

### Resource budget & hardening

- Idle RSS target: **< 120 MB** (typically ~55–85 MB in practice, measured two ways —
  `docker stats` and the RSS reported by `GET /api/health`, which can legitimately differ slightly
  since they account for memory differently).
- Single uvicorn worker (`--workers 1 --no-access-log`), SQLite connection pool capped at
  `pool_size=5, max_overflow=5`, and the anyio worker-thread pool capped at 12 threads
  (`anyio.to_thread.current_default_thread_limiter().total_tokens = 12`) so a burst of concurrent
  sync DB work can't spawn unbounded OS threads on the host.
- Backend bytecode is precompiled at build time (`python -m compileall`) so the non-root runtime
  user never needs write access to `__pycache__` and the first request after boot isn't paying a
  compile-on-import cost.
- The runtime image contains **no Node.js/npm toolchain** — the frontend is compiled to static
  assets in a separate build stage and only the `dist/` output is copied into the final image.
- Runs as a non-root user; `PUID`/`PGID` are reconciled at container start via `gosu`
  (`entrypoint.sh` fixes up the baked-in user/group and `/config` ownership only when they don't
  already match, then drops privileges for the actual server process — `tini` stays PID 1).
