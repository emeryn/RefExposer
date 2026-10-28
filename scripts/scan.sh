#!/usr/bin/env sh
# Supply-chain scan: dependencies, images and Dockerfiles.
#
#   scripts/scan.sh            everything (what "make scan" runs)
#   scripts/scan.sh deps       dependencies only (pip-audit, npm audit), no image needed
#   scripts/scan.sh images     built images (CVEs, secrets, misconfiguration)
#   scripts/scan.sh published  the images as they are in the registry, not the ones just built
#   scripts/scan.sh config     Dockerfiles and compose file
#
# Docker is the only prerequisite: every tool runs in a container, nothing is installed on the host.
# Exit code 1 as soon as one gate fails, so a CI job can call it as is.
#
# Gates:
#   - our own images (backend, frontend): no CRITICAL, no HIGH that has a fix
#   - pip-audit and npm audit (level high) on the dependency manifests
#   - trivy config on the Dockerfiles
# Third-party images (PostgreSQL) are reported but never gate: their content belongs to their publisher.
#
# A vulnerability with no published fix cannot gate a build - there is nothing to install. It is printed in a
# section of its own all the same, with its package: the answer to those is usually to drop the package (a
# smaller base image) or to wait for the distribution, and neither happens if nobody sees them.
set -eu

# Git Bash on Windows rewrites arguments that look like paths (/out, /src...)
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

cd "$(dirname "$0")/.."
REPO=$(pwd)
OUT=${SCAN_OUT:-$REPO/dist/scan}
TRIVY_IMAGE=${TRIVY_IMAGE:-aquasec/trivy:0.76.0}
NODE_IMAGE=${NODE_IMAGE:-node:22-alpine}
PYTHON_IMAGE=${PYTHON_IMAGE:-python:3.12-slim}
CACHE=${TRIVY_CACHE:-$REPO/dist/.trivy-cache}
OURS="emeryn/refexposer-backend:latest emeryn/refexposer-frontend:latest"
THIRD_PARTY=$(sed -n 's/^ *image: *\(postgres:[^ ]*\).*/\1/p' docker-compose.yml | head -1)
FAILED=""

mkdir -p "$OUT" "$CACHE"

trivy() {
    docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$CACHE:/root/.cache" \
        -v "$OUT:/out" \
        -v "$REPO:/src:ro" \
        "$TRIVY_IMAGE" "$@"
}

# Reads a trivy JSON report and lists, per package, the findings with no fix published
UNFIXED='
import collections, json, sys
data = json.load(sys.stdin)
rows = collections.defaultdict(lambda: collections.Counter())
for result in data.get("Results") or []:
    for v in result.get("Vulnerabilities") or []:
        if not v.get("FixedVersion"):
            rows[(v["PkgName"], v.get("InstalledVersion", ""))][v["Severity"]] += 1
if not rows:
    print("      none")
for (pkg, version), counts in sorted(rows.items()):
    detail = ", ".join(f"{n} {s.lower()}" for s, n in sorted(counts.items()))
    print(f"      {pkg} {version}: {detail}")
'

note() { printf '\n== %s\n' "$1"; }
fail() { printf '   FAILED: %s\n' "$1"; FAILED="$FAILED $1"; }

scan_deps() {
    note "Python dependencies (pip-audit on the lock files)"
    # The lock files pin every version, so this reports exactly what the images install
    for name in requirements requirements-dev; do
        docker run --rm -v "$REPO/backend:/b:ro" "$PYTHON_IMAGE" sh -c \
            "pip install -q pip-audit && pip-audit -r /b/$name.txt --no-deps --progress-spinner off" \
            > "$OUT/pip-audit-$name.txt" 2>&1 || fail "pip-audit ($name.txt)"
        printf '   %s.txt: %s\n' "$name" "$(tail -1 "$OUT/pip-audit-$name.txt")"
    done

    note "Python and JavaScript manifests (trivy fs)"
    # A second opinion from another database; Trivy only reads a pinned manifest, hence the lock files
    docker run --rm -v "$CACHE:/root/.cache" -v "$REPO:/src:ro" -v "$OUT:/out" "$TRIVY_IMAGE" fs \
        --quiet --scanners vuln --severity CRITICAL,HIGH --exit-code 1 \
        --file-patterns 'pip:backend/requirements.*\.txt' \
        --skip-dirs data --skip-dirs import --skip-dirs sync --skip-dirs dist \
        --skip-dirs frontend/node_modules /src || fail "trivy fs (dependency manifests)"

    note "JavaScript dependencies (npm audit on frontend/package-lock.json)"
    docker run --rm -v "$REPO/frontend:/app:ro" -w /app "$NODE_IMAGE" \
        sh -c 'npm audit --audit-level=high --no-fund' > "$OUT/npm-audit.txt" 2>&1 || fail "npm audit"
    tail -5 "$OUT/npm-audit.txt"
}

scan_images() {
    for image in $OURS; do
        name=$(echo "$image" | tr '/:' '__')
        note "Image $image (CVEs, secrets, misconfiguration)"
        if ! docker image inspect "$image" >/dev/null 2>&1; then
            printf '   not built: run "make build" first\n'
            fail "image $image missing"
            continue
        fi
        trivy image --quiet --scanners vuln,secret,misconfig --format json -o "/out/$name.json" "$image"
        # Gate: a CRITICAL, or a HIGH that could be fixed by rebuilding or upgrading
        trivy image --quiet --scanners vuln,secret --severity CRITICAL --exit-code 1 "$image" \
            || fail "$image has a CRITICAL vulnerability"
        trivy image --quiet --scanners vuln --severity HIGH --ignore-unfixed --exit-code 1 "$image" \
            || fail "$image has a fixable HIGH vulnerability"
        printf '   fixable now:\n'
        trivy image --quiet --scanners vuln,secret --severity CRITICAL,HIGH,MEDIUM --ignore-unfixed \
            --format table "$image" | tail -30
        # No fix published: nothing to install, so no gate - but the packages are named, because the way out
        # is often to stop shipping them at all
        printf '   no fix published (not a gate, watch the packages):\n'
        trivy image --quiet --scanners vuln --severity CRITICAL,HIGH,MEDIUM --format json "$image" \
            | docker run --rm -i "$PYTHON_IMAGE" python -c "$UNFIXED"
    done

    if [ -n "$THIRD_PARTY" ]; then
        note "Image $THIRD_PARTY (third party, report only)"
        name=$(echo "$THIRD_PARTY" | tr '/:' '__')
        trivy image --quiet --scanners vuln --format json -o "/out/$name.json" "$THIRD_PARTY" || true
        trivy image --quiet --scanners vuln --severity CRITICAL,HIGH --format table "$THIRD_PARTY" | head -20 || true
    fi
}

scan_published() {
    note "Images of the registry (what a registry scanner such as Docker Scout reports)"
    printf '   A pushed image keeps the packages it was built with: one fixed since then is still in it until\n'
    printf '   the image is rebuilt and pushed again. That is usually why a registry report differs from a\n'
    printf '   local scan of a fresh build.\n'
    for image in $OURS; do
        note "Registry image $image"
        docker pull --quiet "$image" >/dev/null 2>&1 || { printf '   not in the registry (or no access)\n'; continue; }
        name=$(echo "$image" | tr '/:' '__')
        trivy image --quiet --scanners vuln --severity CRITICAL,HIGH,MEDIUM --format json \
            -o "/out/$name-published.json" "$image"
        trivy image --quiet --scanners vuln --severity CRITICAL,HIGH --format table "$image" | tail -25
    done
}

scan_config() {
    note "Dockerfiles (trivy config)"
    # Only the Dockerfiles: pointing trivy at the repository would also walk data/ and import/
    rm -rf "$OUT/config" && mkdir -p "$OUT/config/backend" "$OUT/config/frontend"
    cp backend/Dockerfile "$OUT/config/backend/" && cp frontend/Dockerfile "$OUT/config/frontend/"
    cp docker-compose.yml "$OUT/config/"
    docker run --rm -v "$CACHE:/root/.cache" -v "$OUT/config:/cfg:ro" "$TRIVY_IMAGE" config \
        --quiet --misconfig-scanners dockerfile --severity HIGH,CRITICAL --exit-code 1 /cfg \
        || fail "trivy config"
}

case "${1:-all}" in
    deps) scan_deps ;;
    images) scan_images ;;
    published) scan_published ;;
    config) scan_config ;;
    all) scan_deps; scan_config; scan_images ;;
    *) echo "usage: $0 [all|deps|images|published|config]" >&2; exit 2 ;;
esac

printf '\nReports: %s\n' "$OUT"
if [ -n "$FAILED" ]; then
    printf 'Scan failed:%s\n' "$FAILED"
    exit 1
fi
printf 'Scan passed.\n'
