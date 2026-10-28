#!/bin/sh
# Copy the base images RefExposer builds on into a company registry.
#
#   mirror-images.sh --registry registry.example.com/refexposer [--dry-run]
#   mirror-images.sh --save bundle/base-images.tar                     (on a machine with Internet access)
#   mirror-images.sh --load bundle/base-images.tar --registry <prefix>  (on the disconnected side)
#
# The list of images is read from the Dockerfiles and the compose file, so it cannot drift from them.
set -eu

# Git Bash on Windows rewrites arguments that look like Unix paths before Docker sees them (/src becomes
# C:/Program Files/Git/src). Harmless everywhere else.
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

REPO=$(cd "$(dirname "$0")/.." && pwd)
REGISTRY=""
DRY=0
SAVE=""
LOAD=""

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --registry) REGISTRY=${2:?--registry needs a prefix, e.g. registry.example.com/refexposer}; shift 2 ;;
        --save) SAVE=${2:?--save needs a file name}; shift 2 ;;
        --load) LOAD=${2:?--load needs a file name}; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
done

# The default of each *_IMAGE argument, as the Dockerfiles and the compose file declare it
images() {
    sed -n 's/^ARG PYTHON_IMAGE=//p;s/^ARG NODE_IMAGE=//p;s/^ARG NGINX_IMAGE=//p' \
        "$REPO/backend/Dockerfile" "$REPO/frontend/Dockerfile"
    sed -n 's/.*POSTGRES_IMAGE:-\([^}]*\)}.*/\1/p' "$REPO/docker-compose.yml"
}

LIST=$(images | sort -u)
[ -n "$LIST" ] || { echo "no base image found in the Dockerfiles: has the build changed?" >&2; exit 1; }

echo "Base images of RefExposer:"
echo "$LIST" | sed 's/^/  /'
echo

run() {
    if [ "$DRY" = 1 ]; then
        echo "  would run: $*"
    else
        echo "  $*"
        "$@"
    fi
}

# --- transfer through a file, for a network with no route to Docker Hub -------------------------------
if [ -n "$SAVE" ]; then
    mkdir -p "$(dirname "$SAVE")"
    # shellcheck disable=SC2086  # the list is one image per line, deliberately split
    run docker pull --quiet $LIST
    # shellcheck disable=SC2086
    run docker save -o "$SAVE" $LIST
    [ "$DRY" = 1 ] || echo "
Saved to $SAVE. Transfer it, then on the build machine:
  $0 --load $SAVE --registry <your registry prefix>"
    exit 0
fi

if [ -n "$LOAD" ]; then
    [ -f "$LOAD" ] || { echo "$LOAD: no such file" >&2; exit 1; }
    run docker load -i "$LOAD"
fi

[ -n "$REGISTRY" ] || { echo "--registry is required (or use --save first)" >&2; usage 1; }
REGISTRY=${REGISTRY%/}

# --- copy into the company registry -------------------------------------------------------------------
echo "Copying into $REGISTRY/ :"
echo "$LIST" | while IFS= read -r image; do
    [ -n "$image" ] || continue
    target="$REGISTRY/$image"
    [ -n "$LOAD" ] || run docker pull --quiet "$image"
    run docker tag "$image" "$target"
    run docker push "$target"
done

echo
echo "Put these lines in your .env.build (see enterprise/.env.example):"
echo "$LIST" | while IFS= read -r image; do
    case "$image" in
        python:*) echo "PYTHON_IMAGE=$REGISTRY/$image" ;;
        node:*) echo "NODE_IMAGE=$REGISTRY/$image" ;;
        *nginx*) echo "NGINX_IMAGE=$REGISTRY/$image" ;;
        postgres:*) echo "POSTGRES_IMAGE=$REGISTRY/$image" ;;
    esac
done
