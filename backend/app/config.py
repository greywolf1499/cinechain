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
    # No longer enforced on the streamed solve (bounded by depth + bridge_max_duration_seconds);
    # kept so existing env files still load.
    pathfinder_call_budget: int = 150
    # --- Semantic Trope Web (ONNX embeddings, fetched once on first use) ---
    onnx_model_url: str = (
        "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx"
    )
    onnx_tokenizer_url: str = (
        "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json"
    )

    # --- Embedding provider for the Semantic Trope Web (admin settings override these) ---
    embedding_provider: str = "local_onnx"  # local_onnx | ollama | openai
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""

    # --- Generative model for pitches/teasers (opt-in; admin settings override these) ---
    llm_provider: str = "off"  # off | local_gguf | ollama | openai
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # Local GGUF: seconds idle before the model is unloaded (0 = unload right after each call).
    llm_keep_alive_seconds: int = 300
    llm_gguf_url: str = (
        "https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-Q4_K_M.gguf"
    )

    # --- Bridge solver pacing (Phase 15.5) ---
    bridge_max_duration_seconds: int = 45

    @property
    def llm_model_dir(self) -> Path:
        return self.config_dir / "models" / "qwen3.5-0.8b"

    @property
    def onnx_model_dir(self) -> Path:
        return self.config_dir / "models" / "all-MiniLM-L6-v2"

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
