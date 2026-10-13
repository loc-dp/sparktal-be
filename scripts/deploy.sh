#!/usr/bin/env bash
# Deploy the API, worker and beat to the VPS: build the image here on the Mac, ship it over SSH,
# swap the containers (the one-shot migrate service runs first). The 4GB VPS never builds.
#
#   scripts/deploy.sh             deploy HEAD (it must already be on origin/main)
#   scripts/deploy.sh <commit>    deploy that commit
#   scripts/deploy.sh rollback    put back the image that ran before the last deploy
#
# .github/workflows/deploy-vps.yml runs this same script on a self-hosted runner on the Mac
# once CI passes on main. DEPLOY_HOST and DEPLOY_SSH_KEY point it at another box.
set -euo pipefail

HOST="${DEPLOY_HOST:-root@104.237.11.190}"
KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/id_ed25519_vpsdime}"
DIR=/srv/sparktal/sparktal-be
IMAGE=sparktal-be:prod
PREVIOUS=sparktal-be:previous

die() { echo "deploy: $*" >&2; exit 1; }
vps() { ssh -i "$KEY" -o BatchMode=yes "$HOST" "$@"; }

# The VPS adds compose.vps.yaml (not committed) on top of compose.prod.yaml when it exists.
REMOTE_COMPOSE='compose() {
  local files=(-f compose.prod.yaml)
  [ -f compose.vps.yaml ] && files+=(-f compose.vps.yaml)
  docker compose --env-file .env.production "${files[@]}" "$@"
}'

# On the VPS: keep the image the API runs now as $PREVIOUS, so `rollback` can bring it back.
keep_previous() {
  vps bash -s -- "$DIR" "$PREVIOUS" <<REMOTE
set -euo pipefail
cd "\$1"
$REMOTE_COMPOSE
id=\$(compose ps -q api)
[ -z "\$id" ] || docker tag "\$(docker inspect -f '{{.Image}}' "\$id")" "\$2"
REMOTE
}

# On the VPS: start whatever $IMAGE is now and wait for the API's health check.
swap() {
  vps bash -s -- "$DIR" <<REMOTE
set -euo pipefail
cd "\$1"
$REMOTE_COMPOSE
compose up -d --no-build
for _ in \$(seq 1 40); do
  [ "\$(docker inspect -f '{{.State.Health.Status}}' "\$(compose ps -q api)")" = healthy ] && exit 0
  sleep 3
done
compose logs --tail=60 migrate api
echo "deploy: api is not healthy after 2 minutes" >&2
exit 1
REMOTE
}

if [ "${1:-}" = rollback ]; then
  vps docker image inspect "$PREVIOUS" > /dev/null 2>&1 || die "no $PREVIOUS image on the VPS"
  vps docker tag "$PREVIOUS" "$IMAGE"
  swap
  echo "deploy: rolled back to the previous image (migrations already applied stay applied)"
  exit 0
fi

cd "$(git rev-parse --show-toplevel)"
sha=$(git rev-parse --verify "${1:-HEAD}^{commit}")
git fetch -q origin main
git merge-base --is-ancestor "$sha" origin/main || die "$sha is not on origin/main, push it first"
docker info > /dev/null 2>&1 || die "Docker is not running"

echo "deploy: building ${sha:0:7} for linux/amd64"
git archive --format=tar "$sha" | docker build --platform linux/amd64 --target prod \
  --label org.opencontainers.image.revision="$sha" -t "$IMAGE" -

echo "deploy: shipping the image"
keep_previous
docker save "$IMAGE" | gzip | vps 'gunzip | docker load'

# Keep the VPS checkout on the deployed commit: compose.prod.yaml is read from there.
vps "cd $DIR && git fetch -q origin main && git merge -q --ff-only $sha"
swap
vps docker image prune -f > /dev/null
echo "deploy: ${sha:0:7} is live"
