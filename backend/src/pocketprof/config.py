"""Runtime settings (SDS 9.2, Table 10). Everything comes from environment variables or `.env`.

Development defaults run with no external services: SQLite instead of PostgreSQL and Qdrant's embedded
local mode instead of a Qdrant server. Production (Compose) sets DATABASE_URL and QDRANT_URL.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
                                      env_file_encoding="utf-8", extra="ignore")

    env: str = Field("development", description="development | demo | pilot")
    public_url: str = "http://localhost:5173"   # where the web app is served (CORS, OAuth return)
    api_url: str = "http://localhost:8000"      # where this API is reachable (OAuth redirect URI)

    # Stores
    data_dir: Path = REPO_ROOT / "data"
    database_url: str = ""                      # empty -> sqlite:///<data_dir>/pocketprof.db
    qdrant_url: str = ""                        # empty -> embedded local mode under <data_dir>/qdrant
    qdrant_api_key: str = ""

    # Secrets (never committed, NFR-10)
    jwt_secret: str = "dev-only-change-me-dev-only-change-me"
    pseudonym_key: str = "dev-only-pseudonym-key"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    allow_signup: bool = True                   # students may create their own account

    # Google Classroom connector
    google_client_id: str = ""
    google_client_secret: str = ""
    force_ipv4: bool = True                     # some networks have a broken IPv6 route to Google
    enforce_roster: bool = False                # joining requires the student's email on the Classroom roster
    sync_seconds: int = 30                      # 0 disables automatic Classroom sync
    run_scheduler: bool = True                  # run the scheduler inside the API process (development)
    migrate_on_start: bool = True               # apply migrations when the API starts (development)

    # Models
    model_cache_dir: Path | None = None          # default <data_dir>/models
    embed_model: str = "BAAI/bge-small-en-v1.5"  # 384-d in development; bge-base-en-v1.5 (768-d) target
    sparse_model: str = "Qdrant/bm25"
    gen_provider: str = "auto"                   # auto | anthropic | openai | none
    gen_model: str = ""
    gen_base_url: str = ""                       # OpenAI-compatible endpoint (OpenRouter, Ollama, Gemini)
    gen_api_key: str = ""
    gen_fallback_models: str = ""                # OpenRouter only, comma separated
    gen_timeout_s: float = 60.0
    active_arm: str = "shipped-v1"

    # Limits (SDS Table 9)
    max_upload_mb: int = 50
    rate_limit_per_minute: int = 60

    # Analytics (SDS Table 10); used by M6 in a later phase and by cohort suppression now
    min_cohort: int = 5

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v):
        p = Path(v)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'pocketprof.db').as_posix()}"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.api_url.rstrip('/')}/api/v1/google/callback"

    @property
    def is_dev(self) -> bool:
        return self.env == "development"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
