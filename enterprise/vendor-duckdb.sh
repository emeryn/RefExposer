#!/bin/sh
# Collect the DuckDB extensions the backend image pre-installs, so a build machine with nothing to download
# does not have to reach extensions.duckdb.org.
#
#   vendor-duckdb.sh            (run where extensions.duckdb.org is reachable, after vendor-python.sh)
#
# They are installed with the exact DuckDB of backend/requirements.txt, in the base image the build uses, so
# the version and the platform of the files match what will load them. The result lands in
# backend/duckdb-extensions/ and the build picks it up on its own — no argument to pass.
set -eu

# Git Bash on Windows rewrites arguments that look like Unix paths before Docker sees them (/src becomes
# C:/Program Files/Git/src). Harmless everywhere else.
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT="$REPO/backend/duckdb-extensions"
WHEELS="$REPO/backend/wheels"
EXTENSIONS="excel httpfs sqlite"

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT=${2:?--out needs a folder}; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

PYTHON_IMAGE=$(sed -n 's/^ARG PYTHON_IMAGE=//p' "$REPO/backend/Dockerfile")
PYTHON_IMAGE=${PYTHON_IMAGE:-python:3.12-slim}
mkdir -p "$OUT"

# The DuckDB that will load them: the one pinned in the lock file, from the wheels when they are there
SOURCE="--index-url https://pypi.org/simple"
if [ -n "$(find "$WHEELS" -name 'duckdb-*' -print -quit 2>/dev/null)" ]; then
    SOURCE="--no-index --find-links /wheels"
    echo "Using the DuckDB wheel of backend/wheels/"
else
    echo "backend/wheels/ has no DuckDB wheel: taking it from PyPI (run vendor-python.sh first to match exactly)"
fi

echo "Collecting the extensions ($EXTENSIONS) with $PYTHON_IMAGE"
docker run --rm \
    -v "$REPO/backend:/src:ro" \
    -v "$WHEELS:/wheels:ro" \
    -v "$OUT:/out" \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e HTTP_PROXY -e HTTPS_PROXY -e NO_PROXY \
    "$PYTHON_IMAGE" sh -ec "
        pip install --quiet $SOURCE \$(grep -oE '^duckdb==[0-9.]+' /src/requirements.txt || echo duckdb)
        python - <<'PY'
import duckdb
con = duckdb.connect()
for name in '$EXTENSIONS'.split():
    con.execute(f'INSTALL {name}')
print('duckdb', duckdb.__version__, 'platform', con.execute('PRAGMA platform').fetchone()[0])
PY
        # The installed files, whatever the version and platform folders are called
        find /root/.duckdb /home -name '*.duckdb_extension' -exec cp {} /out/ \;
    "

COUNT=$(find "$OUT" -name '*.duckdb_extension' | wc -l | tr -d ' ')
[ "$COUNT" -gt 0 ] || { echo "no extension collected: see the output above" >&2; exit 1; }
echo
echo "$COUNT extension(s) in $OUT:"
find "$OUT" -name '*.duckdb_extension' -exec basename {} \; | sed 's/^/  /'
echo
echo "The build installs them from there on its own. Nothing to pass."
