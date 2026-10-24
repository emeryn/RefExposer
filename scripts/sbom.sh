#!/usr/bin/env sh
# CycloneDX software bill of materials of the images, one file per image, plus their digests.
#
#   scripts/sbom.sh            writes dist/sbom/*.cdx.json (what "make sbom" runs)
#
# Docker is the only prerequisite. The SBOM lists the OS packages, the Python packages and the JavaScript
# files actually present in the image, so it describes what is deployed, not what the manifests ask for.
set -eu

MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

cd "$(dirname "$0")/.."
REPO=$(pwd)
OUT=${SBOM_OUT:-$REPO/dist/sbom}
TRIVY_IMAGE=${TRIVY_IMAGE:-aquasec/trivy:0.76.0}
CACHE=${TRIVY_CACHE:-$REPO/dist/.trivy-cache}
IMAGES=${SBOM_IMAGES:-emeryn/refexposer-backend:latest emeryn/refexposer-frontend:latest}

mkdir -p "$OUT" "$CACHE"
: > "$OUT/images.txt"

for image in $IMAGES; do
    name=$(echo "$image" | tr '/:' '__')
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        printf 'not built: %s (run "make build" first)\n' "$image" >&2
        exit 1
    fi
    docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$CACHE:/root/.cache" \
        -v "$OUT:/out" \
        "$TRIVY_IMAGE" image --quiet --format cyclonedx -o "/out/$name.cdx.json" "$image"
    # The SBOM describes one exact image: its identifier is recorded next to it
    docker image inspect "$image" --format '{{.RepoTags}} {{.Id}} {{.Created}}' >> "$OUT/images.txt"
    printf '%s -> %s\n' "$image" "$OUT/$name.cdx.json"
done

printf '\nImages described: %s\n' "$OUT/images.txt"
printf 'Sign them with cosign before publishing them (see README, Supply chain).\n'
