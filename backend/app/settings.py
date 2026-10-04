from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, overridable with REFEX_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="REFEX_", env_file=".env", extra="ignore")

    # URL used to reach RefExposer (e.g. https://refexposer.example.com): OpenID Connect callback, generated API
    # documentation, URLs shown in the interface, secure cookies when https. Derived from each request when empty.
    public_url: str | None = None

    # Level of the container output: trace, debug, info, warning, error or critical
    log_level: Literal["trace", "debug", "info", "warning", "error", "critical"] = "info"

    data_dir: Path = Path("/data")
    config_dir: Path = Path("/app/config")

    # What to do at startup: download referentials without data, everything, or nothing.
    refresh_on_startup: Literal["missing", "all", "none"] = "missing"
    scheduler_enabled: bool = True
    timezone: str = "Europe/Paris"
    max_concurrent_jobs: int = 2
    keep_runs: int = 200
    # Internal referentials: edits are published once no other edit arrived for this many seconds (0 = at once)
    internal_publish_delay: float = 2.0

    # HTTP downloads. Proxies are read from HTTP(S)_PROXY / NO_PROXY.
    http_timeout: float = 600.0
    http_verify: str = "true"  # "true", "false" or a path to a CA bundle
    http_user_agent: str = "RefExposer/1.0 (+https://github.com/)"

    # Query limits
    api_max_limit: int = 1000
    sql_timeout: float = 30.0
    sql_max_rows: int = 10_000
    export_max_rows: int = 5_000_000

    # Large referentials: above this number of rows, expensive operations (full profile,
    # change tracking, facets, CSV/Excel downloads, unindexed full-text search) are restricted
    large_rows: int = 50_000_000
    # Search facets are computed up to this number of rows (empty = large_rows)
    facets_max_rows: int | None = None
    # Maximum duration of a query issued through the API (seconds)
    query_timeout: float = 60.0

    # Manual import folder: files dropped in <import_dir>/<referential id>/ are imported
    import_dir: Path | None = None
    import_poll_seconds: int = 20
    import_keep_done: int = 5  # imported batches kept in .done/ per referential

    # Sync folder: every file or folder of <sync_dir> is a referential, imported again when its files change
    sync_dir: Path | None = None
    sync_poll_seconds: int = 60
    sync_settle_seconds: int = 30  # files must be untouched this long before an import (copy finished)

    # Git sources: protocols allowed (https only by default: never ssh, file or ext), time limit of a fetch
    git_protocols: str = "https"
    git_timeout: int = 900

    duckdb_memory_limit: str | None = None
    duckdb_threads: int | None = None

    cors_origins: list[str] = []

    # Key protecting secrets stored in the database (generated in data/.secret_key when empty)
    secret_key: str | None = None

    # Database (PostgreSQL in docker compose, SQLite file in data_dir otherwise)
    database_url: str | None = None

    # Bootstrap administrator, created at startup when no active admin exists
    admin_username: str = "admin"
    admin_password: str | None = None
    admin_email: str | None = None
    admin_must_change_password: bool = True
    admin_reset_password: bool = False  # reset the bootstrap admin password at startup (recovery)

    # Authentication
    # Sign-in of local accounts (username / password kept by RefExposer) in the web interface. false: single
    # sign-on only (OpenID Connect, LDAP directory); API tokens and service accounts keep working
    local_login: bool = True
    session_ttl_hours: float = 12.0
    cookie_secure: bool = False  # set to true behind HTTPS
    password_min_length: int = 12
    login_max_attempts: int = 10
    login_lockout_minutes: int = 15
    api_token_max_days: int | None = None  # maximum lifetime of API tokens (None = unlimited)

    # Argon2id parameters (RFC 9106 second recommended option by default)
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4

    @field_validator("log_level", mode="before")
    @classmethod
    def _log_level(cls, v: str) -> str:
        return str(v).strip().lower() or "info"

    @field_validator("public_url")
    @classmethod
    def _check_public_url(cls, v: str | None) -> str | None:
        v = (v or "").strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("REFEX_PUBLIC_URL must start with http:// or https://")
        return v

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure or bool(self.public_url and self.public_url.startswith("https://"))

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'refexposer.db').as_posix()}"

    @property
    def verify(self) -> bool | str:
        v = self.http_verify.strip()
        if v.lower() in ("true", "1", "yes"):
            return True
        if v.lower() in ("false", "0", "no"):
            return False
        return v

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / ".tmp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
