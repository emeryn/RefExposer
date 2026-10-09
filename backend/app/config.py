"""Declarative referential definitions, loaded from YAML files in the config directory."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{(REFEX_SOURCE_[A-Z0-9_]+)\}")
# Secret of the secret manager (secretstore.py): ${secret:<name>}
SECRET_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
SECRET_REF = re.compile(r"\$\{secret:(" + SECRET_NAME + r")\}")
# Set by secretstore.py: (name, URL the value is sent to) -> value; raises ValueError (unknown, host not allowed)
SECRET_RESOLVER: Callable[[str, str | None], str] | None = None


def expand_env(value: str, url: str | None = None) -> str:
    """Replace ${REFEX_SOURCE_...} with the environment variable and ${secret:<name>} with the secret of the secret
    manager, so that source credentials (API keys, license keys) stay out of the definitions. Only this prefix of
    variables is expanded: other variables (secret key, database password...) can never be sent to a source.
    `url`: where the value is sent (a secret may be restricted to some hosts)."""
    def repl(m: re.Match) -> str:
        if m.group(1) not in os.environ:
            raise ValueError(f"environment variable {m.group(1)} is not defined")
        return os.environ[m.group(1)]

    def secret(m: re.Match) -> str:
        if SECRET_RESOLVER is None:
            raise ValueError(f"secret '{m.group(1)}': the secret manager is not available")
        return SECRET_RESOLVER(m.group(1), url)

    return SECRET_REF.sub(secret, _ENV_REF.sub(repl, value))


def secret_names(value: Any) -> set[str]:
    """Names of the secrets referenced anywhere in a value (definition, source...)."""
    if isinstance(value, str):
        return set(SECRET_REF.findall(value))
    if isinstance(value, dict):
        return set().union(*(secret_names(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(secret_names(v) for v in value)) if value else set()
    return set()

Format = Literal["csv", "tsv", "json", "jsonl", "txt", "parquet", "xlsx", "xml", "bloom", "mmdb", "sqlite"]
FORMATS: tuple[str, ...] = Format.__args__  # type: ignore[attr-defined]
DownloadFormat = Literal["csv", "csv.gz", "xlsx", "json", "jsonl", "parquet"]

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$")
_DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(value: str) -> timedelta:
    m = _DURATION_RE.match(value)
    if not m:
        raise ValueError(f"invalid duration '{value}' (expected: 30m, 12h, 2d, 1w)")
    return timedelta(**{_DURATION_UNITS[m.group(2)]: int(m.group(1))})


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # http: downloaded · local: files in data/<id>/ · internal: rows managed in RefExposer (editor / API)
    # sync: file or folder of the sync folder (generated, see sync.py) · git: files of a Git repository
    type: Literal["http", "local", "internal", "sync", "git"] = "http"
    # http: one or several URLs. Each one becomes {source0}, {source1}... in transforms.
    urls: list[str] = Field(default_factory=list)
    # local: glob relative to data/<id>/ (e.g. "raw/*.csv") · git: glob relative to the repository root
    path: str | None = None
    # git: HTTPS URL of the repository (GitHub, GitLab, Gitea...), branch / tag / commit (default branch when
    # empty), access token of a private repository (${REFEX_SOURCE_*} recommended) and its user name
    repository: str | None = None
    ref: str | None = None
    token: str | None = None
    username: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    # HTTP Basic authentication "user:password" (e.g. MaxMind "account_id:license_key")
    basic_auth: str | None = None
    # Glob applied to archive members (zip / tar); all files are kept when omitted.
    extract: str | None = None

    def request_headers(self, url: str | None = None) -> dict[str, str]:
        """Headers actually sent to `url`: ${REFEX_SOURCE_*} variables and ${secret:*} expanded, Basic
        authentication added."""
        out = {k: expand_env(v, url) for k, v in self.headers.items()}
        if self.basic_auth:
            out["Authorization"] = "Basic " + base64.b64encode(expand_env(self.basic_auth, url).encode()).decode()
        return out

    @model_validator(mode="after")
    def _check(self) -> "SourceConfig":
        # URLs and paths are shown in logs and pages: a secret only goes where it is never displayed
        if secret_names([self.urls, self.path, self.repository, self.ref, self.username, self.extract, list(self.headers)]):
            raise ValueError("a ${secret:...} reference is only allowed in header values, 'basic_auth' and 'token'")
        if self.type == "http" and not self.urls:
            raise ValueError("an http source must define at least one url")
        if self.type in ("local", "sync", "git") and not self.path:
            raise ValueError(f"a {self.type} source must define 'path'")
        if self.type == "git":
            if not self.repository or not re.match(r"^https?://\S+$", self.repository):
                raise ValueError("a git source needs the HTTPS URL of the repository ('repository')")
            if re.match(r"^https?://[^/]*@", self.repository):
                raise ValueError("no credentials in the repository URL: use 'token' (and 'username')")
            if self.ref and not re.match(r"^[A-Za-z0-9._/@+-]+$", self.ref):
                raise ValueError("invalid branch, tag or commit")
            if ".." in (self.path or "").split("/"):
                raise ValueError("the path must stay inside the repository")
        elif self.repository or self.token:
            raise ValueError("'repository' and 'token' only apply to git sources")
        return self


ColumnType = Literal["text", "integer", "number", "boolean", "date", "datetime", "list"]
DUCKDB_TYPES: dict[str, str] = {
    "text": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE", "boolean": "BOOLEAN",
    "date": "DATE", "datetime": "TIMESTAMP", "list": "VARCHAR[]",
}


class ColumnDef(BaseModel):
    """Column of an internal referential."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    type: ColumnType = "text"
    required: bool = False
    description: str = ""
    auto: bool = False  # key generated automatically (UUID) when not provided


class ValidationConfig(BaseModel):
    """Guards preventing a broken download from replacing good data."""

    model_config = ConfigDict(extra="forbid")

    min_rows: int = 1
    max_drop_pct: float | None = None  # reject a version losing more than X% rows
    unique_key: bool = False  # reject a version where the key is not unique


class StorageConfig(BaseModel):
    """Physical layout, for large referentials (hundreds of millions of rows and more)."""

    model_config = ConfigDict(extra="forbid")

    # Rows are written sorted on these columns: exact / prefix / range lookups on the first
    # one only read the relevant row groups (zone maps) instead of the whole file.
    sort_by: list[str] = Field(default_factory=list)
    # Additional copies sorted on each of these columns (fast lookups on them too).
    indexes: list[str] = Field(default_factory=list)
    row_group_size: int = Field(122_880, ge=10_000, le=10_000_000)
    keep_raw: bool = True  # keep downloaded files after the build (disk space)
    keep_previous: bool = True  # keep the previous version (changes, rollback)
    profile: Literal["full", "sample", "none"] = "full"
    track_changes: bool = True
    check_key: bool = True


class IncrementalConfig(BaseModel):
    """Updates by deltas, instead of downloading the whole source again.

    Bloom filter: each delta (stable URL, revalidated with ETag / Last-Modified) adds its values to the published filter.
    SQLite database (e.g. NIST NSRL RDSv3): the full database is kept, each delta (SQL file of INSERT / UPDATE /
    DELETE statements) is applied once, in the order of the list, then the referential is rebuilt from the database."""

    model_config = ConfigDict(extra="forbid")

    # Bloom: delta files, at least one · SQLite: deltas to apply on top of the full database (source.urls), in order;
    # add the new delta at the end of the list (it may stay empty: deltas imported by hand)
    urls: list[str] = Field(default_factory=list)
    # values: one value per line (# comments, .gz accepted) · bloom: filter of the same size and hash functions
    # sql: SQL statements run in the SQLite database (default for a SQLite referential)
    format: Literal["values", "bloom", "sql"] | None = None
    # The full filter (source.urls) is checked at most this often (e.g. 30d); every run when empty.
    # A new full filter replaces the published one, then the deltas are applied again on top of it.
    full_every: str | None = None
    # A delta making the estimated false positive rate exceed this value is rejected (filter saturated:
    # a new full filter, sized for more elements, is needed). Default: 10 times the rate the filter was built for.
    max_fp_rate: float | None = Field(default=None, gt=0, lt=1)

    @field_validator("full_every")
    @classmethod
    def _check_full_every(cls, v: str | None) -> str | None:
        if v:
            parse_duration(v)
        return v


class ReferentialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    name: str
    description: str = ""
    category: str = "General"
    tags: list[str] = Field(default_factory=list)
    homepage: str | None = None
    license: str | None = None
    owner: str | None = None

    source: SourceConfig
    format: Format
    options: dict[str, Any] = Field(default_factory=dict)
    transform: str | None = None

    key: str | None = None
    search_columns: list[str] = Field(default_factory=list)
    # Internal referentials only: typed columns of the rows managed in RefExposer
    columns: list[ColumnDef] = Field(default_factory=list)

    schedule: str | None = None  # crontab expression
    max_age: str | None = None  # data considered stale after this duration
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    enabled: bool = True
    # Confidential: the data is encrypted at rest (Parquet AES-GCM, internal rows in the database), the source
    # files and the generated downloads are not kept on disk
    confidential: bool = False
    # Download formats generated right after each update (others are generated on first request)
    downloads: list[DownloadFormat] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    # Bloom filters only: deltas applied between two full downloads
    incremental: IncrementalConfig | None = None

    # Filled by the loader
    config_file: str | None = Field(default=None, exclude=True)
    origin: Literal["file", "database", "sync"] = Field(default="file", exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _internal_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and (data.get("source") or {}).get("type") == "internal":
            data = {**data, "format": data.get("format") or "jsonl"}
        return data

    @model_validator(mode="after")
    def _check_internal(self) -> "ReferentialConfig":
        if self.source.type == "internal":
            names = [c.name for c in self.columns]
            if not names:
                raise ValueError("an internal referential needs at least one column")
            if len(set(n.lower() for n in names)) != len(names):
                raise ValueError("duplicate column names")
            if not self.key or self.key not in names:
                raise ValueError("an internal referential needs a key, which must be one of its columns")
            key_col = next(c for c in self.columns if c.name == self.key)
            if key_col.type not in ("text", "integer"):
                raise ValueError("the key must be a text or integer column")
            if any(c.auto and (c.name != self.key or c.type != "text") for c in self.columns):
                raise ValueError("only a text key can be generated automatically")
        elif self.columns:
            raise ValueError("'columns' only applies to internal referentials")
        if self.incremental:
            inc = self.incremental
            if inc.format is None:  # deltas of a Bloom filter: values; of a SQLite database: SQL
                inc.format = "sql" if self.format == "sqlite" else "values"
            if self.format not in ("bloom", "sqlite") or self.source.type != "http" or len(self.source.urls) != 1:
                raise ValueError("'incremental' only applies to a Bloom filter or a SQLite database downloaded from a single URL")
            if self.format == "bloom" and (not inc.urls or inc.format == "sql"):
                raise ValueError("incremental Bloom filter: give at least one delta URL, in the 'values' or 'bloom' format")
            if self.format == "sqlite" and self.confidential:
                raise ValueError("an incremental SQLite database cannot be confidential: the full database is kept on disk to apply the deltas")
            if self.format == "sqlite" and (inc.format != "sql" or inc.full_every or inc.max_fp_rate):
                raise ValueError("incremental SQLite database: the deltas are SQL files ('full_every' and 'max_fp_rate' "
                                 "only apply to Bloom filters)")
        if self.format in ("bloom", "mmdb"):  # published as is: no table to transform, sort or export
            label = "a Bloom filter" if self.format == "bloom" else "a MaxMind DB"
            if self.confidential:
                raise ValueError(f"{label} cannot be confidential: its file is served as is to tools")
            if self.transform:
                raise ValueError(f"no SQL transformation for {label}")
            if self.downloads or self.storage.sort_by or self.storage.indexes:
                raise ValueError(f"no generated downloads, sort or indexes for {label}: the file is published as is")
        return self

    @property
    def is_internal(self) -> bool:
        return self.source.type == "internal"

    @property
    def is_sync(self) -> bool:
        return self.source.type == "sync"

    @field_validator("schedule")
    @classmethod
    def _check_schedule(cls, v: str | None) -> str | None:
        if v:
            CronTrigger.from_crontab(v)
        return v

    @field_validator("max_age")
    @classmethod
    def _check_max_age(cls, v: str | None) -> str | None:
        if v:
            parse_duration(v)
        return v

    @field_validator("options")
    @classmethod
    def _check_options(cls, v: dict[str, Any]) -> dict[str, Any]:
        for k in v:
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", k):
                raise ValueError(f"invalid option name: {k}")
        return v

    @property
    def is_bloom(self) -> bool:
        """Bloom filter (membership tests only, no table)."""
        return self.format == "bloom"

    @property
    def is_mmdb(self) -> bool:
        """MaxMind DB (GeoIP2 / GeoLite2, DB-IP, IPinfo...): IP lookups, no table."""
        return self.format == "mmdb"

    @property
    def is_sqlite(self) -> bool:
        """SQLite database: one table or view read (options.table), possibly updated by SQL deltas."""
        return self.format == "sqlite"

    @property
    def is_artifact(self) -> bool:
        """Binary file published as is (Bloom filter, MaxMind DB): no table, raw download for tools."""
        return self.format in ("bloom", "mmdb")

    @property
    def table(self) -> str:
        return self.id.replace("-", "_")

    @property
    def config_hash(self) -> str:
        """Hash of everything that affects the produced dataset."""
        payload = {
            "format": self.format,
            "options": self.options,
            "transform": self.transform,
            "source": self.source.model_dump(),
            "storage": {k: v for k, v in self.storage.model_dump().items() if k in ("sort_by", "indexes", "row_group_size")},
            "columns": [c.model_dump() for c in self.columns],
        }
        # Fields added later only count when set: the hash of existing definitions stays the same (no rebuild)
        for f in ("basic_auth", "repository", "ref", "token", "username"):
            if payload["source"].get(f) is None:
                payload["source"].pop(f, None)
        if self.confidential:  # encrypted output
            payload["confidential"] = True
        if self.incremental and self.is_bloom:  # deltas: another set of values (full download again when they change)
            payload["incremental"] = {"urls": self.incremental.urls, "format": self.incremental.format}
        # SQLite: the deltas are not part of the hash, the kept database records which ones were applied
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

    @property
    def max_age_delta(self) -> timedelta | None:
        return parse_duration(self.max_age) if self.max_age else None


class ConfigError(BaseModel):
    file: str
    error: str


def _format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


def validate_definition(data: dict[str, Any]) -> ReferentialConfig:
    """Validate a definition (raises ValueError with a readable message)."""
    try:
        return ReferentialConfig.model_validate(data)
    except ValidationError as e:
        raise ValueError(_format_validation_error(e)) from e


def load_db_registry(db) -> tuple[dict[str, ReferentialConfig], list[ConfigError]]:
    """Definitions created from the administration UI (table referential_definitions)."""
    from sqlalchemy import select

    from .models import ReferentialDefinition

    refs: dict[str, ReferentialConfig] = {}
    errors: list[ConfigError] = []
    for d in db.scalars(select(ReferentialDefinition).order_by(ReferentialDefinition.id)):
        try:
            ref = validate_definition({**d.config, "id": d.id})
        except ValueError as e:
            errors.append(ConfigError(file=f"base:{d.id}", error=str(e)))
            continue
        ref.origin = "database"
        refs[ref.id] = ref
    return refs, errors


def load_registry(config_dir: Path) -> tuple[dict[str, ReferentialConfig], list[ConfigError]]:
    """Load every *.yml / *.yaml file of the config directory (recursively).

    A file contains either a `referentials:` list or a single referential mapping.
    Invalid entries are reported but do not prevent the others from loading.
    """
    refs: dict[str, ReferentialConfig] = {}
    errors: list[ConfigError] = []
    if not config_dir.exists():
        errors.append(ConfigError(file=str(config_dir), error="configuration folder not found"))
        return refs, errors

    files = sorted([*config_dir.rglob("*.yml"), *config_dir.rglob("*.yaml")])
    for path in files:
        rel = str(path.relative_to(config_dir))
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            errors.append(ConfigError(file=rel, error=f"invalid YAML: {e}"))
            continue
        if isinstance(doc, dict) and "referentials" in doc:
            entries = doc.get("referentials") or []
        elif isinstance(doc, dict) and "id" in doc:
            entries = [doc]
        elif isinstance(doc, list):
            entries = doc
        else:
            errors.append(ConfigError(file=rel, error="no 'referentials' key found"))
            continue
        for i, entry in enumerate(entries):
            label = f"{rel}#{entry.get('id', i) if isinstance(entry, dict) else i}"
            try:
                ref = ReferentialConfig.model_validate(entry)
            except ValidationError as e:
                errors.append(ConfigError(file=label, error=_format_validation_error(e)))
                continue
            if ref.id in refs:
                errors.append(ConfigError(file=label, error=f"identifier '{ref.id}' already defined in {refs[ref.id].config_file}"))
                continue
            ref.config_file = rel
            refs[ref.id] = ref
    log.info("Loaded %d referential(s) from %s (%d error(s))", len(refs), config_dir, len(errors))
    return refs, errors
