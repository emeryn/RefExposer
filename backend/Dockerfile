FROM python:3.12-slim AS base

# Unprivileged account running the application. The UID / GID can be aligned on the owner of the
# host folders mounted as volumes (data/, import/): docker compose build --build-arg REFEX_UID=$(id -u) ...
ARG REFEX_UID=10001
ARG REFEX_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REFEX_DATA_DIR=/data \
    REFEX_CONFIG_DIR=/app/config

# Security updates of the base image (the tag is rebuilt less often than the Debian advisories), then
# git: Git repository sources (GitHub, GitLab, Gitea...)
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "$REFEX_GID" refex \
    && useradd --uid "$REFEX_UID" --gid refex --home-dir /home/refex --create-home --shell /usr/sbin/nologin refex

WORKDIR /app

COPY requirements.txt .
# pip itself is part of the image and gets its own advisories: updated before it installs anything.
# --require-hashes: the lock file (scripts/lock.sh) pins every version and every file hash, so the build
# either installs exactly what was reviewed or fails - it never picks a newer release by itself.
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.txt

# Application code owned by root: read-only for the account running it
COPY app ./app
# Referential definitions are mounted at runtime (see docker-compose.yml); /data is the only writable folder
RUN mkdir -p /app/config /data && chown refex:refex /data

USER refex
# DuckDB extensions pre-installed for the application account, so that nothing is downloaded at runtime
# (read-only filesystem, servers without Internet access):
# - excel: xlsx read/export
# - httpfs: OpenSSL crypto, required to write encrypted Parquet files (confidential referentials)
# - sqlite: SQLite databases (e.g. NIST NSRL RDSv3)
RUN python -c "import duckdb; duckdb.execute('INSTALL excel'); duckdb.execute('INSTALL httpfs'); duckdb.execute('INSTALL sqlite')"

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# --- test image: docker compose run --rm backend-tests
FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install --require-hashes -r requirements-dev.txt
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
