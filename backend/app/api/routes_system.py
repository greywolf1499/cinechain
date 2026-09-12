from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    """Liveness/readiness probe used by the Docker healthcheck and manual checks."""
    try:
        from sqlalchemy import text

        from app.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - any DB failure should report unhealthy, not crash
        db_ok = False

    rss_bytes = _current_rss_bytes()

    return {"status": "ok", "db_ok": db_ok, "rss_bytes": rss_bytes}


def _current_rss_bytes() -> int | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    return None
