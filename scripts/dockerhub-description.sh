#!/usr/bin/env sh
# Push README.md to the Docker Hub description of an image.
#
#   scripts/dockerhub-description.sh backend --print    render it, change nothing (no credentials needed)
#   scripts/dockerhub-description.sh backend            render it and upload it
#   scripts/dockerhub-description.sh frontend
#
# Docker Hub renders the description on its own page, where the README's relative links go nowhere: every
# one of them is rewritten to an absolute GitHub URL, documents to /blob/ and pictures to raw. Each image
# also gets a few lines of its own above the README, because somebody landing on the frontend image needs to
# know it is one half of a pair and where the other one is.
#
# Credentials, for the upload only (the same two the publish workflow uses):
#   DOCKERHUB_USERNAME   the account that owns the images
#   DOCKERHUB_TOKEN      a Docker Hub personal access token with write access
#
# Only curl is needed. The description is a property of the repository, not of a tag: uploading it replaces
# whatever was there, so the workflow does it on a release and not on every dev build.
set -eu

cd "$(dirname "$0")/.."

REPO_URL=${REPO_URL:-https://github.com/emeryn/RefExposer}
# The branch the links point at. `main` rather than the tag: this description is replaced by each release,
# so it always describes the newest images, and its links should lead to the matching documentation.
REF=${REF:-main}
BLOB="$REPO_URL/blob/$REF"
RAW=$(printf '%s' "$REPO_URL" | sed 's|github.com|raw.githubusercontent.com|')/$REF

usage() {
    echo "usage: $0 <backend|frontend> [--print]" >&2
    exit 2
}

[ $# -ge 1 ] || usage
IMAGE=$1
PRINT=${2:-}
case "$IMAGE" in
    backend|frontend) ;;
    *) usage ;;
esac

if [ "$IMAGE" = backend ]; then
    ROLE="the **backend**: the API, the ingestion and the DuckDB engine"
    SHORT="RefExposer backend: expose, monitor and query heterogeneous referentials"
    OTHER=frontend
    PORT=8000
else
    ROLE="the **frontend**: the web interface, served by nginx as an unprivileged account"
    SHORT="RefExposer frontend: web interface for the RefExposer referential platform"
    OTHER=backend
    PORT=8080
fi

OUT=$(mktemp)
trap 'rm -f "$OUT" "$OUT.short"' EXIT

# ----- the lines that belong to this image, above the README
cat > "$OUT" <<EOF
> This image is $ROLE.
> It is one half of a pair and does not run on its own — it needs
> \`${DOCKERHUB_USERNAME:-emeryn}/refexposer-$OTHER\` and a PostgreSQL database.
> [**Source, documentation and issues on GitHub**]($REPO_URL)

\`\`\`bash
# Docker Hub
docker pull ${DOCKERHUB_USERNAME:-emeryn}/refexposer-$IMAGE:latest
# GitHub Packages — same build, same digest
docker pull ghcr.io/emeryn/refexposer-$IMAGE:latest
\`\`\`

| | |
|---|---|
| Tags | \`latest\` and \`<version>\` for a release, \`dev\` for the development branch |
| Listens on | \`$PORT\` |
| Runs as | an unprivileged account, read-only root filesystem supported |
| Compose file and rootless Podman units | [in the repository]($BLOB/docker-compose.yml), see [Deployment]($BLOB/docs/deployment.md) |
| Changes | [CHANGELOG.md]($BLOB/CHANGELOG.md) |

---

EOF

# ----- the README, with every relative target made absolute
#
# The character classes exclude `:` on purpose: an address that is already absolute carries one in its
# scheme, so it is left alone without having to be matched.
# The delimiter is `%`, not `|`: with `|` as the delimiter, the `\|` of an alternation reads as an escaped
# delimiter — a literal pipe — and the alternation silently never matches, which sent every screenshot to a
# /blob/ address that serves an HTML page instead of the picture.
sed \
    -e "s%src=\"\([^\":]*\)\"%src=\"$RAW/\1\"%g" \
    -e "s%](\([^):]*\.\(png\|jpe\?g\|gif\|svg\)\))%]($RAW/\1)%g" \
    -e "s%](\([^)#:][^):]*\)\(#[^)]*\)\?)%]($BLOB/\1\2)%g" \
    README.md >> "$OUT"

if [ "$PRINT" = "--print" ]; then
    cat "$OUT"
    echo "---- short description ($(printf '%s' "$SHORT" | wc -c | tr -d ' ') characters, 100 max) ----" >&2
    echo "$SHORT" >&2
    exit 0
fi

: "${DOCKERHUB_USERNAME:?set DOCKERHUB_USERNAME}"
: "${DOCKERHUB_TOKEN:?set DOCKERHUB_TOKEN}"

# ----- sign in (a personal access token is accepted as the password) and replace the description
TOKEN=$(curl -fsS -X POST https://hub.docker.com/v2/users/login/ \
    -H 'Content-Type: application/json' \
    --data-binary @- <<EOF | sed -n 's/.*"token"[ ]*:[ ]*"\([^"]*\)".*/\1/p'
{"username": "$DOCKERHUB_USERNAME", "password": "$DOCKERHUB_TOKEN"}
EOF
)
[ -n "$TOKEN" ] || { echo "could not sign in to Docker Hub (username or token?)" >&2; exit 1; }

# The body is built with python rather than by hand: a README holds quotes, backslashes and newlines, and
# none of them may break the JSON
python3 - "$OUT" "$SHORT" <<'PY' > "$OUT.short"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.dumps({"full_description": f.read(), "description": sys.argv[2]}))
PY

curl -fsS -X PATCH "https://hub.docker.com/v2/repositories/$DOCKERHUB_USERNAME/refexposer-$IMAGE/" \
    -H "Authorization: JWT $TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "@$OUT.short" > /dev/null

echo "description of $DOCKERHUB_USERNAME/refexposer-$IMAGE updated ($(wc -c < "$OUT" | tr -d ' ') bytes)"
