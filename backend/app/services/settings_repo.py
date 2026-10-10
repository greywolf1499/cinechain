"""Admin-configured settings overrides, stored in SQLite via `SystemSetting`.

Values here take precedence over the corresponding `.env`/`Settings` field.
Reads/writes always go through the request's own `Session` (never a
separate engine), so behavior in tests using `app.dependency_overrides` for
`get_session` stays correct automatically.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.system import SystemSetting
from app.utils.ids import utcnow

# The only fields exposed for override - never store arbitrary keys.
OVERRIDABLE_KEYS = (
    "tmdb_api_key",
    "jellyfin_url",
    "jellyfin_api_key",
    "omdb_api_key",
    "omdb_soft_cap",
    "bridge_max_duration_seconds",
    "radarr_url",
    "radarr_api_key",
    "radarr_default_quality_profile_id",
    "radarr_default_root_folder_path",
    "seerr_url",
    "seerr_api_key",
    "seerr_request_mode",
    "seerr_user_id",
    "embedding_provider",
    "embedding_base_url",
    "embedding_api_key",
    "embedding_model",
    "embedding_local_preset",
    "llm_provider",
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_keep_alive_seconds",
    "tvtropes_enabled",
)


def get_overrides(session: Session) -> dict[str, str]:
    """Returns whichever of OVERRIDABLE_KEYS currently have a non-empty DB override."""
    rows = session.exec(select(SystemSetting).where(SystemSetting.key.in_(OVERRIDABLE_KEYS))).all()
    return {row.key: row.value for row in rows if row.value}


def set_overrides(session: Session, values: dict[str, str | None]) -> None:
    """Upserts each provided key; a falsy value deletes the override (reverts to .env)."""
    for key, value in values.items():
        if key not in OVERRIDABLE_KEYS:
            continue
        existing = session.get(SystemSetting, key)
        if not value:
            if existing is not None:
                session.delete(existing)
            continue
        if existing is not None:
            existing.value = value
            existing.updated_at = utcnow()
            session.add(existing)
        else:
            session.add(SystemSetting(key=key, value=value))
    session.commit()
