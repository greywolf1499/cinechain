from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
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
    omdb_soft_cap: int = Field(default=0, ge=0)
    omdb_api_base: str = "https://www.omdbapi.com/"

    # --- Pathfinder budget (Phase 6) ---
    pathfinder_max_depth: int = 5
    pathfinder_cast_limit: int = 15
    pathfinder_actor_credit_limit: int = 120
    # No longer enforced on the streamed solve (bounded by depth + bridge_max_duration_seconds);
    # kept so existing env files still load.
    pathfinder_call_budget: int = 150
    # --- Embedding provider for the Semantic Trope Web (admin settings override these) ---
    embedding_provider: str = "local_onnx"  # local_onnx | ollama | openai
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    # On-device model for local_onnx: arctic-embed-xs | multilingual-e5-small | all-minilm-l6-v2
    # ONNX_MODEL_PRESET is accepted as a friendlier alias for EMBEDDING_LOCAL_PRESET.
    embedding_local_preset: str = Field(
        default="arctic-embed-xs",
        validation_alias=AliasChoices("embedding_local_preset", "onnx_model_preset"),
    )

    # --- Generative model for pitches/teasers (opt-in; admin settings override these) ---
    llm_provider: str = "off"  # off | local_gguf | ollama | openai
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # Local GGUF: seconds idle before the model is unloaded (0 = unload right after each call).
    # LLM_IDLE_TIMEOUT_SECONDS is accepted as an alias for LLM_KEEP_ALIVE_SECONDS.
    llm_keep_alive_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices("llm_keep_alive_seconds", "llm_idle_timeout_seconds"),
    )
    # TVTropes ingestion is separately opt-in and still requires runtime robots permission.
    tvtropes_enabled: bool = False

    # --- Bridge solver pacing (Phase 15.5) ---
    bridge_max_duration_seconds: int = 45

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
