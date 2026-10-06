#!/usr/bin/env bash
# Runs the real QA-Board server locally, built from this checkout, for demos and videos. See README.md.
#
#   videos/stack/stack.sh up [--build]   build missing images (all with --build), start, wait until healthy
#   videos/stack/stack.sh down           stop and remove the containers (keeps the database and results)
#   videos/stack/stack.sh reset          wipe the demo database and results storage (asks for no confirmation)
#   videos/stack/stack.sh status         containers and API health
#   videos/stack/stack.sh logs [svc...]  follow logs (default: backend, proxy)
#   videos/stack/stack.sh url            print the URLs of the web app and of the demo pages
#   videos/stack/stack.sh seed           create a demo git repo with 2 commits and upload their results with `qa`
#   videos/stack/stack.sh build          rebuild the images from the current sources
#   videos/stack/stack.sh compose ...    any docker compose command on the demo stack
#
# Environment:
#   QABOARD_DEMO_PORT=5151            where the web app is served (http://localhost:$QABOARD_DEMO_PORT)
#   QABOARD_DEMO_STORAGE=/mnt/qaboard results storage, mounted at the same path in the containers
#   QABOARD_DEMO_PROJECT=qaboard-demo docker compose project name
#   QABOARD_DEMO_CA_BUNDLE=           CA bundle to trust during image builds (behind TLS-intercepting proxies)
#   QABOARD_DEMO_WORKDIR=/tmp/qaboard-demo  where `seed` creates the demo git repo
#   QA=<repo>/.venv/bin/qa            the qa CLI used by `seed`
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROJECT="${QABOARD_DEMO_PROJECT:-qaboard-demo}"
PORT="${QABOARD_DEMO_PORT:-5151}"
STORAGE="${QABOARD_DEMO_STORAGE:-/mnt/qaboard}"
WORKDIR="${QABOARD_DEMO_WORKDIR:-/tmp/qaboard-demo}"
URL="http://localhost:$PORT"
DEMO_PROJECT="demo/sample"
SERVICES=(db redis backend frontend proxy cantaloupe)
BUILT=(backend frontend proxy cantaloupe)
MARKER=".qaboard-demo-storage"  # reset only wipes a storage folder that has it

export QABOARD_PORT_HTTP="$PORT" QABOARD_DEMO_STORAGE="$STORAGE"
unset QABOARD_VERSION  # docker-compose.yml uses it in image tags

log() { echo "[stack] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }
# Never go through an HTTP proxy to reach localhost
http_get() { curl -fsS --noproxy '*' --max-time 5 "$@"; }

compose() {
  local files=(-f "$REPO/docker-compose.yml" -f "$REPO/production.yml" -f "$HERE/compose.demo.yml")
  if [ -n "${QABOARD_DEMO_CA_BUNDLE:-}" ]; then files+=(-f "$HERE/compose.ca.yml"); fi
  docker compose -p "$PROJECT" --project-directory "$REPO" "${files[@]}" "$@"
}

# Base images that trust $QABOARD_DEMO_CA_BUNDLE, used by compose.ca.yml in place of the Dockerfiles' FROM
build_ca_bases() {
  [ -n "${QABOARD_DEMO_CA_BUNDLE:-}" ] || return 0
  [ -f "$QABOARD_DEMO_CA_BUNDLE" ] || die "QABOARD_DEMO_CA_BUNDLE=$QABOARD_DEMO_CA_BUNDLE does not exist"
  local base
  for base in python:3.13-slim-bookworm node:24; do
    if [ -n "${FORCE_BUILD:-}" ] || ! docker image inspect "qaboard-demo-ca/$base" >/dev/null 2>&1; then
      log "building qaboard-demo-ca/$base (trusts \$QABOARD_DEMO_CA_BUNDLE)"
      docker buildx build -q --load -t "qaboard-demo-ca/$base" --build-arg "BASE=$base" \
        --secret "id=ca,src=$QABOARD_DEMO_CA_BUNDLE" "$HERE/ca" >/dev/null
    fi
  done
}

build() {
  local missing=() s
  for s in "${BUILT[@]}"; do
    if [ -n "${FORCE_BUILD:-}" ] || ! docker image inspect "qaboard-demo:$s" >/dev/null 2>&1; then missing+=("$s"); fi
  done
  [ ${#missing[@]} -gt 0 ] || return 0
  build_ca_bases
  log "building ${missing[*]} from $REPO (first time: ~10-15 min, logs in $WORKDIR/build.log)"
  mkdir -p "$WORKDIR"
  local start=$SECONDS
  if ! compose build --progress=plain "${missing[@]}" >"$WORKDIR/build.log" 2>&1; then
    tail -n 30 "$WORKDIR/build.log" >&2
    die "build failed, see $WORKDIR/build.log"
  fi
  log "built in $((SECONDS - start))s"
}

prepare_storage() {
  if [ ! -d "$STORAGE" ]; then
    mkdir -p "$STORAGE" 2>/dev/null || sudo mkdir -p "$STORAGE"
    touch "$STORAGE/$MARKER" 2>/dev/null || sudo touch "$STORAGE/$MARKER"
  elif [ -z "$(ls -A "$STORAGE")" ]; then
    touch "$STORAGE/$MARKER" 2>/dev/null || sudo touch "$STORAGE/$MARKER"
  fi
  # the CLI runs as you, the backend as root
  chmod 777 "$STORAGE" 2>/dev/null || sudo chmod 777 "$STORAGE" || true
}

wait_healthy() {
  local start=$SECONDS timeout="${1:-240}"
  until http_get "$URL/api/v1/health" >/dev/null 2>&1; do
    local crashing
    crashing="$(compose ps --status restarting --format '{{.Service}}' 2>/dev/null | tr '\n' ' ')"
    if [ -n "${crashing// /}" ] && [ $((SECONDS - start)) -gt 20 ]; then
      compose logs --tail=30 $crashing >&2
      die "crash loop: $crashing"
    fi
    if [ $((SECONDS - start)) -gt "$timeout" ]; then
      compose ps >&2
      compose logs --tail=30 backend proxy >&2
      die "$URL/api/v1/health is not responding after ${timeout}s"
    fi
    sleep 2
  done
  # The web app bundle is copied by the "frontend" one-shot container
  until http_get "$URL/" 2>/dev/null | grep -q '<div id="root"'; do
    [ $((SECONDS - start)) -le "$timeout" ] || die "$URL does not serve the web app"
    sleep 1
  done
  # The image viewers need the IIIF server (cantaloupe, a JVM: ~15s to start)
  until http_get -o /dev/null "$URL/iiif/2" 2>/dev/null; do
    [ $((SECONDS - start)) -le "$timeout" ] || die "the IIIF image server ($URL/iiif/2) is not responding"
    sleep 1
  done
  log "healthy after $((SECONDS - start))s: $URL"
}

up() {
  [ "${1:-}" = "--build" ] && FORCE_BUILD=1
  build
  prepare_storage
  compose up -d --no-build --quiet-pull "${SERVICES[@]}" 2>&1 | grep -vE '^\s*(Container|Network|Volume) ' >&2 || true
  # nginx resolves backend/cantaloupe once: if they were recreated (new IPs), it would hang on the old ones
  compose exec -T proxy nginx -s reload >/dev/null 2>&1 || true
  wait_healthy
}

reset() {
  compose down --volumes --remove-orphans 2>&1 | grep -vE '^\s*(Container|Network|Volume) ' >&2 || true
  if [ -d "$STORAGE" ]; then
    if [ -f "$STORAGE/$MARKER" ]; then
      # files are written by the CLI (you) and the backend (root): delete them from a container
      docker run --rm -v "$STORAGE:/storage" postgres:12-alpine \
        sh -c 'find /storage -mindepth 1 -maxdepth 1 ! -name '"$MARKER"' -exec rm -rf {} +'
      log "wiped $STORAGE"
    else
      log "not wiping $STORAGE: it was not created by stack.sh (no $MARKER file in it)"
    fi
  fi
  rm -rf "$WORKDIR/repo"
  log "reset done, run: $0 up"
}

status() {
  compose ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  if http_get "$URL/api/v1/health" >/dev/null 2>&1; then echo "API: healthy ($URL)"; else echo "API: down ($URL)"; fi
}

api() { http_get "$URL/api/v1/$1"; }

# Prints the demo pages. Commits come from the API: the 2 latest commits of the demo project
url() {
  echo "Web app:    $URL/"
  local commits
  commits="$(api "commits/?project=$DEMO_PROJECT" 2>/dev/null \
    | python3 -c 'import json,sys; print(" ".join(c["id"] for c in json.load(sys.stdin)[:2]))' 2>/dev/null || true)"
  if [ -z "$commits" ]; then
    echo "(no results for $DEMO_PROJECT yet: run \`$0 seed\`)"
    return
  fi
  read -r new ref <<<"$commits"
  echo "Project:    $URL/$DEMO_PROJECT"
  echo "Commit:     $URL/$DEMO_PROJECT/commit/$new"
  echo "Outputs:    $URL/$DEMO_PROJECT/commit/$new?selected_views=output-list"
  if [ -n "${ref:-}" ]; then
    echo "Compare:    $URL/$DEMO_PROJECT/commit/$new?reference=$ref&selected_views=table-compare"
    echo "Compare (images): $URL/$DEMO_PROJECT/commit/$new?reference=$ref&selected_views=output-list"
  fi
  echo "History:    $URL/$DEMO_PROJECT/history"
  echo "Dashboard:  $URL/$DEMO_PROJECT/dashboard"
}

qa_cli() {
  local qa="${QA:-$REPO/.venv/bin/qa}"
  [ -x "$qa" ] || die "no qa CLI at $qa: run \`uv sync\` in $REPO or set QA=/path/to/qa"
  # `qa batch` starts `qa run` subprocesses: qa must be in the PATH
  PATH="$(dirname "$qa"):$PATH" QABOARD_URL="$URL" QA_NO_CHECK_FOR_UPDATES=1 NO_COLOR=1 "$qa" "$@"
}

# A git repo with 2 commits on master, whose results are uploaded like a CI would (batch "default"),
# then a developer's experiment with `qa --share` on top of the latest one.
seed() {
  http_get "$URL/api/v1/health" >/dev/null || die "the stack is not running: $0 up"
  local repo="$WORKDIR/repo" git=(git -c user.name="Ada Lovelace" -c user.email=ada@example.com -c commit.gpgsign=false)
  rm -rf "$repo"
  mkdir -p "$WORKDIR"
  cp -r "$HERE/demo_project" "$repo"
  cd "$repo"
  "${git[@]}" init -q -b master
  "${git[@]}" add .
  "${git[@]}" commit -qm "Denoise images with a box blur"
  log "commit 1/2: $(git rev-parse --short HEAD), running qa batch demo..."
  CI=true qa_cli batch demo >"$WORKDIR/seed-1.log" 2>&1 || { tail -20 "$WORKDIR/seed-1.log" >&2; die "qa batch failed"; }

  sed -i -e 's/^FILTER = "box"/FILTER = "median"/' -e 's/^RADIUS = 1/RADIUS = 2/' qa/main.py
  "${git[@]}" commit -qam "Use a 5x5 median filter to preserve edges"
  log "commit 2/2: $(git rev-parse --short HEAD), running qa batch demo..."
  CI=true qa_cli batch demo >"$WORKDIR/seed-2.log" 2>&1 || { tail -20 "$WORKDIR/seed-2.log" >&2; die "qa batch failed"; }

  log "experiment: qa --share --label radius-3 --tuning '{\"radius\": 3}' batch demo"
  qa_cli --share --label radius-3 --tuning '{"radius": 3}' batch demo >"$WORKDIR/seed-3.log" 2>&1 || { tail -20 "$WORKDIR/seed-3.log" >&2; die "qa batch failed"; }
  log "seeded $repo (logs: $WORKDIR/seed-*.log)"
  url
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  up) up "$@" ;;
  down) compose down --remove-orphans 2>&1 | grep -vE '^\s*(Container|Network) ' >&2 || true ;;
  reset) reset ;;
  status) status ;;
  logs) if [ $# -eq 0 ]; then set -- backend proxy; fi; compose logs -f --tail=100 "$@" ;;
  url) url ;;
  seed) seed ;;
  build) FORCE_BUILD=1 build ;;
  compose) compose "$@" ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; [ -z "$cmd" ] || exit 1 ;;
esac
