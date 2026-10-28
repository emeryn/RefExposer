#!/bin/sh
# Download the Python dependencies of the backend into backend/wheels/, so a build machine with no index to
# reach can install them from files. The hashes of backend/requirements.txt are checked here, so a file that
# is not the reviewed one never reaches the bundle.
#
#   vendor-python.sh [--dev] [--index <url>] [--out <folder>]
#
# Then, on the build machine, with backend/wheels/ in place:
#   docker compose --env-file .env.build build \
#     --build-arg PIP_EXTRA_ARGS="--no-index --find-links /app/wheels"
set -eu

# Git Bash on Windows rewrites arguments that look like Unix paths before Docker sees them (/src becomes
# C:/Program Files/Git/src). Harmless everywhere else.
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT="$REPO/backend/wheels"
INDEX=""
REQ="requirements.txt"

while [ $# -gt 0 ]; do
    case "$1" in
        --dev) REQ="requirements-dev.txt"; shift ;;
        --out) OUT=${2:?--out needs a folder}; shift 2 ;;
        --index) INDEX=${2:?--index needs a URL}; shift 2 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

[ -f "$REPO/backend/$REQ" ] || { echo "backend/$REQ: no such file" >&2; exit 1; }
mkdir -p "$OUT"

# The wheels must match the image that will install them, not this machine: the download runs in that image
PYTHON_IMAGE=$(sed -n 's/^ARG PYTHON_IMAGE=//p' "$REPO/backend/Dockerfile")
PYTHON_IMAGE=${PYTHON_IMAGE:-python:3.12-slim}
echo "Downloading the dependencies of backend/$REQ with $PYTHON_IMAGE"

INDEX_ARG=""
[ -z "$INDEX" ] || INDEX_ARG="--index-url $INDEX"

# pip is downloaded too: the image upgrades it before installing anything, which must also work offline.
# It is not hash-pinned (it is the installer, not a dependency of the application).
docker run --rm \
    -v "$REPO/backend:/src:ro" \
    -v "$OUT:/out" \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e HTTP_PROXY -e HTTPS_PROXY -e NO_PROXY \
    "$PYTHON_IMAGE" sh -ec "
        pip download $INDEX_ARG --require-hashes --dest /out -r /src/$REQ
        pip download $INDEX_ARG --dest /out pip
    "

COUNT=$(find "$OUT" -type f \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l | tr -d ' ')
SIZE=$(du -sh "$OUT" | cut -f1)
echo
echo "$COUNT file(s), $SIZE in $OUT"
echo "Every dependency matched the hash recorded in backend/$REQ."
echo
echo "Transfer that folder (keeping it at backend/wheels/), then build with:"
echo "  docker compose --env-file .env.build build \\"
echo "    --build-arg PIP_EXTRA_ARGS=\"--no-index --find-links /app/wheels\""
