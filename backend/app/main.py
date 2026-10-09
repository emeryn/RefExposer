from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import __version__
from . import appsettings, logsetup, network, syslog
from . import mcp
from .api import admin, admin_referentials, auth, config_transfer, discovery_api, notifications_api, branding, catalog, records, referentials, secrets_api, settings_api, system, tasks_api
from .auth import bootstrap_admin, purge_expired_sessions
from .crypto import check_key
from . import secretstore
from .db import dispose_db, init_db, session_factory
from .api.deps import ORJSONResponse
from .service import Service
from .settings import get_settings

logsetup.setup(get_settings().log_level)


def check_writable(*folders, limit: int = 100_000) -> None:
    """The application runs as an unprivileged account: say clearly how to fix the folders and files it cannot
    write (e.g. left by an older image running as root), instead of failing later on one of them."""
    uid, gid = os.getuid(), os.getgid()
    fix = (f"give them to this account: 'docker compose up' does it (init-permissions service), or on the host "
           f"'sudo chown -R {uid}:{gid} data import'; or build the image with --build-arg REFEX_UID=<owner uid> "
           "--build-arg REFEX_GID=<owner gid>")
    for folder in folders:
        if folder is None:
            continue
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            raise RuntimeError(f"{folder} is not writable by the account running RefExposer (uid {uid}, gid {gid}): {e}. {fix}") from e
        # Below the folder: every directory must accept new files, every file must be replaceable
        blocked, seen = [], 0
        for root, dirs, files in os.walk(folder):
            for name in [*dirs, *files]:
                seen += 1
                path = os.path.join(root, name)
                if not os.access(path, os.W_OK) or (name in dirs and not os.access(path, os.X_OK)):
                    blocked.append(path)
            if seen > limit or len(blocked) >= 5:
                break
        if blocked:
            raise RuntimeError(f"not writable by the account running RefExposer (uid {uid}, gid {gid}): "
                               f"{', '.join(blocked[:5])}{'…' if len(blocked) >= 5 else ''}. {fix}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_writable(settings.data_dir, settings.import_dir)
    check_key()
    init_db(settings.db_url)
    secretstore.install()
    appsettings.clear_cache()
    with session_factory()() as db:
        bootstrap_admin(db, settings)
        purge_expired_sessions(db)
        network.set_proxy(appsettings.load(db, "proxy"))
        if not settings.local_login:
            sso = [n for n in ("ldap", "oidc") if appsettings.load(db, n).enabled]
            if sso:
                logging.getLogger(__name__).info("Local sign-in disabled: single sign-on only (%s)", ", ".join(sso))
            else:
                logging.getLogger(__name__).warning(
                    "Local sign-in disabled (REFEX_LOCAL_LOGIN=false) but neither LDAP nor OpenID Connect is enabled: "
                    "nobody can sign in to the interface. Set REFEX_LOCAL_LOGIN=true to configure single sign-on first.")
        syslog.install(appsettings.load(db, "syslog"), appsettings.load(db, "proxy"))
    service = Service(settings)
    app.state.service = service
    service.start()  # also schedules the system tasks (session purge, backups...: tasks.py)
    try:
        yield
    finally:
        service.stop()
        syslog.shutdown()
        dispose_db()


app = FastAPI(
    title="RefExposer",
    version=__version__,
    description=(
        "Unified exposure of referentials (CSV, TSV, JSON, TXT, Parquet, Excel...). "
        "Each referential is downloaded, normalized to Parquet and queryable through this API or in SQL."
    ),
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=2048)
if get_settings().cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(auth.router, prefix="/api")
app.include_router(branding.router, prefix="/api")
app.include_router(admin_referentials.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(secrets_api.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(discovery_api.router, prefix="/api")
app.include_router(notifications_api.router, prefix="/api")
app.include_router(config_transfer.router, prefix="/api")
app.include_router(tasks_api.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(records.router, prefix="/api")
app.include_router(referentials.router, prefix="/api")
app.include_router(system.router, prefix="/api")


def custom_openapi():
    """Declare the Bearer (API token) scheme so that Swagger offers an 'Authorize' button."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "bearer": {"type": "http", "scheme": "bearer", "description": "Personal API token (rfx_...)"}
    }
    schema["security"] = [{"bearer": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
