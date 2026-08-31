#!/usr/bin/env bash
#
# One command to take a checkout to a running stack.
#
#     ./run.sh              # rebuild changed images and bring everything up
#     ./run.sh --pull       # git pull first, then the above
#     ./run.sh --fresh      # recreate containers from scratch (keeps volumes)
#     ./run.sh --clean      # also delete the databases, then rebuild
#     ./run.sh --status     # what is running
#     ./run.sh --stop       # stop everything, keep containers and volumes
#     ./run.sh --down       # remove containers and networks, keep volumes
#     ./run.sh --logs api   # follow one service
#
# Runs the same way from Git Bash on Windows and from a shell on Ubuntu. It
# deliberately drives the whole Compose project rather than individual
# services: bringing up `api` alone leaves the migration job unrun and the
# worker on last week's image, which then fails in ways that look like product
# bugs.
#
# Which services make up "everything" comes from COMPOSE_FILE in .env, so the
# engine choice stays in one place instead of being duplicated here.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# ── output ────────────────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
    BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; RESET=''
fi
step() { printf '%s\n==> %s%s\n' "$BOLD" "$1" "$RESET"; }
info() { printf '    %s\n' "$1"; }
warn() { printf '%s    ! %s%s\n' "$YELLOW" "$1" "$RESET"; }
fail() { printf '%s\n!!  %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }
ok()   { printf '%s    ok %s%s\n' "$GREEN" "$1" "$RESET"; }

# Windows Docker Desktop rejects the MSYS path rewriting Git Bash does to
# anything that looks like a path, so container-side paths survive intact.
export MSYS_NO_PATHCONV=1

# ── arguments ─────────────────────────────────────────────────────────────
DO_PULL=0; DO_FRESH=0; DO_CLEAN=0; ACTION=up; LOGS_SERVICE=''; SKIP_BUILD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --pull)    DO_PULL=1 ;;
        --fresh)   DO_FRESH=1 ;;
        --clean)   DO_CLEAN=1 ;;
        --no-build) SKIP_BUILD=1 ;;
        --status)  ACTION=status ;;
        --stop)    ACTION=stop ;;
        --down)    ACTION=down ;;
        --logs)    ACTION=logs; LOGS_SERVICE="${2:-}"; [ $# -gt 1 ] && shift ;;
        -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         fail "unknown option: $1  (try --help)" ;;
    esac
    shift
done

# ── prerequisites ─────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH"

if docker compose version >/dev/null 2>&1; then
    dc() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    dc() { docker-compose "$@"; }
else
    fail "docker compose is not available (need Docker Compose v2, or docker-compose)"
fi

docker info >/dev/null 2>&1 || fail \
    "the Docker daemon is not responding. Start Docker Desktop (Windows/macOS) or 'sudo systemctl start docker' (Linux), then run this again."

# ── environment ───────────────────────────────────────────────────────────
# The credential store is encrypted with SECRET_ENCRYPTION_KEY. Generating a
# new one silently makes every stored credential undecryptable, so .env is
# created once from the example and then left alone.
if [ ! -f .env ]; then
    [ -f .env.example ] || fail ".env is missing and there is no .env.example to copy"
    step "Creating .env from .env.example"
    cp .env.example .env
    if grep -qE '^SECRET_ENCRYPTION_KEY=\s*$' .env 2>/dev/null; then
        KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())' 2>/dev/null || true)"
        if [ -n "$KEY" ]; then
            # BSD and GNU sed disagree about -i, so rewrite the file instead.
            awk -v k="$KEY" '/^SECRET_ENCRYPTION_KEY=/{print "SECRET_ENCRYPTION_KEY=" k; next} {print}' \
                .env > .env.tmp && mv .env.tmp .env
            ok "generated SECRET_ENCRYPTION_KEY"
        else
            warn "python not found: set SECRET_ENCRYPTION_KEY in .env before starting"
        fi
    fi
    warn "review .env before using this for anything real"
fi

# Back up .env on every run. A rewritten key is unrecoverable and takes the
# whole credential store with it; a dated copy makes that a five-second fix.
mkdir -p .env.backups
if [ -f .env ]; then
    STAMP="$(date +%Y%m%d-%H%M%S)"
    LATEST="$(ls -1t .env.backups/env-*.bak 2>/dev/null | head -1 || true)"
    if [ -z "$LATEST" ] || ! cmp -s .env "$LATEST"; then
        cp .env ".env.backups/env-$STAMP.bak"
        # Keep the last 20; they are a few hundred bytes each.
        ls -1t .env.backups/env-*.bak 2>/dev/null | tail -n +21 | xargs -r rm -f
    fi
fi

# Read the values Compose will use, so the URLs printed at the end are real.
get_env() {
    local key="$1" default="$2" value=''
    if [ -f .env ]; then
        value="$(sed "s/#.*//" .env | grep -E "^[[:space:]]*${key}=" | tail -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
    fi
    printf '%s' "${!key:-${value:-$default}}"
}
PROXY_PORT="$(get_env PROXY_PORT 8080)"
API_PORT="$(get_env API_PORT 8010)"
ENGINE_TYPE="$(get_env ENGINE_TYPE AIRBYTE_EMBEDDED)"

# ── non-build actions ─────────────────────────────────────────────────────
case "$ACTION" in
    status)
        dc ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' || true
        exit 0 ;;
    logs)
        [ -n "$LOGS_SERVICE" ] || fail "--logs needs a service name, e.g. --logs api"
        exec dc logs -f --tail 200 "$LOGS_SERVICE" ;;
    stop)
        step "Stopping"; dc stop; ok "stopped (containers and volumes kept)"; exit 0 ;;
    down)
        step "Removing containers and networks"; dc down; ok "removed (volumes kept)"; exit 0 ;;
esac

# ── pull ──────────────────────────────────────────────────────────────────
if [ "$DO_PULL" = 1 ]; then
    step "Pulling the latest code"
    command -v git >/dev/null 2>&1 || fail "git is not installed"
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        warn "you have uncommitted changes; git pull may refuse to run"
    fi
    git pull --ff-only || fail "git pull failed. Resolve it, then run this again."
    ok "at $(git rev-parse --short HEAD)"
fi

# ── clean ─────────────────────────────────────────────────────────────────
if [ "$DO_CLEAN" = 1 ]; then
    step "Deleting all data"
    warn "this removes the product database, the demo warehouse, and engine state."
    warn "credentials survive only because SECRET_ENCRYPTION_KEY in .env is untouched,"
    warn "but everything stored in the database is going away."
    printf "    Type 'delete' to confirm: "
    read -r CONFIRM
    [ "$CONFIRM" = "delete" ] || fail "cancelled"
    dc down -v --remove-orphans || true
    ok "volumes removed"
fi

# ── build ─────────────────────────────────────────────────────────────────
export BUILD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ "$SKIP_BUILD" = 0 ]; then
    step "Building images from the current checkout"
    info "containers run code baked into the image, so this is what makes"
    info "a git pull actually take effect"
    dc build || fail "build failed. Fix the error above, then run this again."
    ok "images built ($BUILD_SHA)"
fi

# ── up ────────────────────────────────────────────────────────────────────
step "Starting the stack"
UP_ARGS="-d --remove-orphans"
[ "$DO_FRESH" = 1 ] && UP_ARGS="$UP_ARGS --force-recreate"
# shellcheck disable=SC2086
dc up $UP_ARGS || fail "the stack did not start. './run.sh --logs api' usually says why."

# ── wait until it is actually serving ─────────────────────────────────────
# `up -d` returns once containers are created, which is well before the API can
# answer. Waiting here is what makes this safe to chain in a deploy script.
step "Waiting for the API to serve"
# Any HTTP answer means the service is listening. `curl -f` is deliberately not
# used: /readyz reports degraded state with a 503 that still proves the process
# is up, and the proxy answers / with a 307 redirect to the login page.
http_status() {
    # %{http_code} concatenates a code per hop when curl follows redirects, so
    # this never follows them and reports the single status it was given.
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || true)"
    printf '%s' "${code:-000}"
}

# A container that exited non-zero will never become ready, so the wait stops
# early and says which one. The migration job exiting 0 is by design.
exited_badly() {
    local cid name code
    for cid in $(dc ps --status exited --quiet 2>/dev/null); do
        name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||')"
        code="$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null || echo 0)"
        if [ "$code" != "0" ]; then
            printf '%s (exit %s)' "$name" "$code"
            return 0
        fi
    done
    return 1
}

DEADLINE=$(( $(date +%s) + 300 ))
READY=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    STATUS="$(http_status "http://127.0.0.1:${API_PORT}/readyz")"
    if [ "$STATUS" != "000" ]; then
        READY=1
        break
    fi
    if BROKEN="$(exited_badly)"; then
        fail "$BROKEN. See: ./run.sh --logs ${BROKEN%% *}"
    fi
    sleep 3
done

if [ "$READY" = 1 ]; then
    if [ "$STATUS" = "200" ]; then
        ok "API is ready"
    else
        warn "the API is up but /readyz answered ${STATUS}; a dependency is degraded"
        warn "detail: curl http://127.0.0.1:${API_PORT}/readyz?deep=1"
    fi
else
    warn "the API did not answer within 5 minutes"
    warn "check: ./run.sh --logs api"
fi

# The frontend is served through the proxy; a ready API with a dead proxy still
# means nobody can log in.
PROXY_STATUS="$(http_status "http://127.0.0.1:${PROXY_PORT}/")"
if [ "$PROXY_STATUS" != "000" ]; then
    ok "web UI is serving"
else
    warn "the web UI is not answering on port ${PROXY_PORT} yet; give it a moment"
fi

# ── summary ───────────────────────────────────────────────────────────────
step "Running"
dc ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' || true

DEMO_EMAIL="$(get_env NEXT_PUBLIC_DEMO_EMAIL '')"
printf '\n'
info "web UI    http://localhost:${PROXY_PORT}"
info "API       http://localhost:${API_PORT}"
info "engine    ${ENGINE_TYPE}"
[ -n "$DEMO_EMAIL" ] && info "sign in   ${DEMO_EMAIL}"
printf '\n%s' "$DIM"
info "./run.sh --status      what is running"
info "./run.sh --logs api    follow a service"
info "./run.sh --stop        stop without losing anything"
printf '%s\n' "$RESET"
