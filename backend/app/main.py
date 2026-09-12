from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_system import router as system_router
from app.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().config_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="CineChain",
              default_response_class=JSONResponse, lifespan=lifespan)

api_router_prefix = "/api"
app.include_router(system_router, prefix=api_router_prefix)

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
