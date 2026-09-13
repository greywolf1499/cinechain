from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central app configuration, sourced from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core ---
    config_dir: Path = Path("/config")
    port: int = 8787
    # Optional explicit override; falls back to the persisted secret_key_file.
    secret_key: str = ""
    # Set true when served over TLS (e.g. behind a reverse proxy) so session
    # cookies get the `Secure` flag.
    cookie_secure: bool = False

    # --- TMDB ---
    tmdb_api_key: str = ""
    tmdb_api_base: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p"

    # --- Homelab integrations (all optional) ---
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    radarr_url: str = ""
    radarr_api_key: str = ""
    seerr_url: str = ""
    seerr_api_key: str = ""

    # --- OMDb ratings (optional) ---
    omdb_api_key: str = ""
    omdb_api_base: str = "https://www.omdbapi.com/"

    # --- Pathfinder budget (Phase 6) ---
    pathfinder_max_depth: int = 5
    pathfinder_cast_limit: int = 15
    pathfinder_actor_credit_limit: int = 120
    pathfinder_call_budget: int = 150

    @property
    def database_path(self) -> Path:
        return self.config_dir / "cinechain.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def secret_key_file(self) -> Path:
        return self.config_dir / "secret.key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
