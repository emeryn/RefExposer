# Base image, package index and Debian mirror: every default is the public one, and each can be pointed at
# a company repository instead (see enterprise/README.md). Nothing else in the build has to change.
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS base

# Unprivileged account running the application. The UID / GID can be aligned on the owner of the
# host folders mounted as volumes (data/, import/): docker compose build --build-arg REFEX_UID=$(id -u) ...
ARG REFEX_UID=10001
ARG REFEX_GID=10001

# Debian mirror replacing deb.debian.org and security.debian.org (empty: the public ones)
ARG APT_MIRROR=
# 1: apply the security updates of the base image and install git. 0: the base image already carries both,
# which is how a build with nothing to download works — see enterprise/README.md.
ARG OS_UPDATE=1
# Python package index, and anything else pip needs to reach it (--find-links, --trusted-host, --cert...)
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_ARGS=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REFEX_DATA_DIR=/data \
    REFEX_CONFIG_DIR=/app/config

# Security updates of the base image (the tag is rebuilt less often than the Debian advisories), then
# git: Git repository sources (GitHub, GitLab, Gitea...)
RUN if [ "$OS_UPDATE" = "1" ]; then \
        if [ -n "$APT_MIRROR" ]; then \
            sed -i "s|http://deb.debian.org|$APT_MIRROR|g; s|https://deb.debian.org|$APT_MIRROR|g; \
                    s|http://security.debian.org|$APT_MIRROR|g; s|https://security.debian.org|$APT_MIRROR|g" \
                /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
        fi; \
        apt-get update \
        && apt-get upgrade -y --no-install-recommends \
        && apt-get install -y --no-install-recommends git \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && command -v git >/dev/null || { echo "git is missing: the base image must carry it when OS_UPDATE=0" >&2; exit 1; }

RUN groupadd --gid "$REFEX_GID" refex \
    && useradd --uid "$REFEX_UID" --gid refex --home-dir /home/refex --create-home --shell /usr/sbin/nologin refex

WORKDIR /app

COPY requirements.txt .
# Empty in the repository. A build with no index to reach fills it (enterprise/vendor-python.sh) and passes
# PIP_EXTRA_ARGS="--no-index --find-links /app/wheels"; it is removed from the image in either case.
COPY wheels ./wheels
# pip itself is part of the image and gets its own advisories: updated before it installs anything.
# --require-hashes: the lock file (scripts/lock.sh) pins every version and every file hash, so the build
# either installs exactly what was reviewed or fails - it never picks a newer release by itself.
RUN pip install --index-url "$PIP_INDEX_URL" $PIP_EXTRA_ARGS --upgrade pip \
    && pip install --index-url "$PIP_INDEX_URL" $PIP_EXTRA_ARGS --require-hashes -r requirements.txt \
    && rm -rf wheels

# Application code owned by root: read-only for the account running it
COPY app ./app
# Referential definitions are mounted at runtime (see docker-compose.yml); /data is the only writable folder
RUN mkdir -p /app/config /data && chown refex:refex /data

# Empty in the repository. A build with nothing to download fills it (enterprise/vendor-duckdb.sh) and the
# extensions are then installed from those files instead of from extensions.duckdb.org.
COPY --chown=refex:refex duckdb-extensions /tmp/duckdb-extensions

USER refex
# Repository the extensions are downloaded from when they are not vendored (empty: the public one)
ARG DUCKDB_EXTENSION_REPOSITORY=
# DuckDB extensions pre-installed for the application account, so that nothing is downloaded at runtime
# (read-only filesystem, servers without Internet access):
# - excel: xlsx read/export
# - httpfs: OpenSSL crypto, required to write encrypted Parquet files (confidential referentials)
# - sqlite: SQLite databases (e.g. NIST NSRL RDSv3)
RUN python -c "import duckdb, glob, os; \
con = duckdb.connect(); \
repo = os.environ.get('DUCKDB_EXTENSION_REPOSITORY') or ''; \
con.execute(\"SET custom_extension_repository='\" + repo + \"'\") if repo else None; \
files = sorted(glob.glob('/tmp/duckdb-extensions/*.duckdb_extension')); \
[con.execute(\"INSTALL '\" + f + \"'\") for f in files] if files \
    else [con.execute('INSTALL ' + e) for e in ('excel', 'httpfs', 'sqlite')]" \
    && rm -rf /tmp/duckdb-extensions

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# --- test image: docker compose run --rm backend-tests
FROM base AS test
USER root
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_ARGS=
COPY requirements-dev.txt .
COPY wheels ./wheels
RUN pip install --index-url "$PIP_INDEX_URL" $PIP_EXTRA_ARGS --require-hashes -r requirements-dev.txt \
    && rm -rf wheels
COPY pytest.ini .
COPY tests ./tests
USER refex
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

# --- runtime image (default target, keep last)
FROM base AS runtime
# No package manager in the image that runs in production: nothing installs anything at runtime, and the
# libraries pip vendors (urllib3, msgpack, setuptools) are reported by scanners although they are unreachable
USER root
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip /usr/local/lib/python3.12/site-packages/pip* \
    && find / -name "*.whl" -path "*ensurepip*" -delete
USER refex
