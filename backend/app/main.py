import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_curated import router as curated_router
from app.api.routes_engine import router as engine_router
from app.api.routes_images import router as images_router
from app.api.routes_integrations import router as integrations_router
from app.api.routes_movies import router as movies_router
from app.api.routes_passport import router as passport_router
from app.api.routes_runs import router as runs_router
from app.api.routes_settings import router as settings_router
from app.api.routes_system import router as system_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_users import router as users_router
from app.config import get_settings
from app.integrations.omdb import OMDbClient
from app.services import task_runner
from app.services.tmdb import TMDBClient

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def _fail_interrupted_tasks() -> None:
    """Jobs left pending/running by a previous process can never finish."""
    from app.db import engine

    try:
        interrupted = task_runner.fail_interrupted_tasks(engine)
    except Exception:
        logger.warning("Could not reconcile interrupted tasks", exc_info=True)
        return
    if interrupted:
        logger.warning("Marked %d interrupted task(s) as failed", interrupted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().config_dir.mkdir(parents=True, exist_ok=True)
    # Cap the anyio worker-thread pool (used by anyio.to_thread.run_sync, e.g.
    # the pathfinder's DB reads) so a burst of concurrent sync work can't
    # spawn unbounded OS threads on the host laptop.
    anyio.to_thread.current_default_thread_limiter().total_tokens = 12
    _fail_interrupted_tasks()
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        app.state.http_client = http_client
        app.state.tmdb = TMDBClient(http_client)
        app.state.omdb = OMDbClient(http_client)
        yield


app = FastAPI(title="CineChain",
              default_response_class=JSONResponse, lifespan=lifespan)

api_router_prefix = "/api"
app.include_router(system_router, prefix=api_router_prefix)
app.include_router(auth_router, prefix=api_router_prefix)
app.include_router(users_router, prefix=api_router_prefix)
app.include_router(runs_router, prefix=api_router_prefix)
app.include_router(engine_router, prefix=api_router_prefix)
app.include_router(integrations_router, prefix=api_router_prefix)
app.include_router(movies_router, prefix=api_router_prefix)
app.include_router(settings_router, prefix=api_router_prefix)
app.include_router(curated_router, prefix=api_router_prefix)
app.include_router(images_router, prefix=api_router_prefix)
app.include_router(tasks_router, prefix=api_router_prefix)
app.include_router(passport_router, prefix=api_router_prefix)

# Serve built frontend assets (JS/CSS/images) under /assets.
assets_dir = STATIC_DIR / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str, request: Request):
    """Serve the SPA for any non-API route so client-side routing survives a hard refresh."""
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return JSONResponse(
        {"detail": "Frontend build not found. Run the frontend build first."}, status_code=503
    )
