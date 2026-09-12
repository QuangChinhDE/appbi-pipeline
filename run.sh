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
#
# Everything a first run needs has to land in .env without anybody being asked
# to read a file and guess. What used to happen instead: the key generator only
# fired when the line was *empty*, .env.example shipped
# `SECRET_ENCRYPTION_KEY=REPLACE_ME__...`, so a fresh clone got a placeholder
# that is not a valid key. Compose's `${VAR:?}` guard passed it -- a placeholder
# is not empty -- the stack came up healthy, sign-in worked, and the failure
# surfaced later, at the first attempt to save a credential. Which is the
# hardest possible moment to connect back to a line in a file nobody edited.

#: 32 random bytes as urlsafe base64, from whatever this machine happens to
#: have.
#
# `python` alone was not enough. It is absent from a plain Windows install, and
# `python3` there is often a Microsoft Store shim that prints nothing and exits
# 0 -- which the old code read as success. Every branch is therefore checked
# for a 44-character result rather than trusted.
generate_key() {
    local key=''
    if command -v openssl >/dev/null 2>&1; then
        key="$(openssl rand -base64 32 2>/dev/null | tr '+/' '-_' | tr -d '\r\n')"
    fi
    if [ "${#key}" -ne 44 ] && [ -r /dev/urandom ]; then
        key="$(head -c 32 /dev/urandom | base64 2>/dev/null | tr '+/' '-_' | tr -d '\r\n')"
    fi
    if [ "${#key}" -ne 44 ]; then
        local candidate
        for candidate in python3 python py; do
            command -v "$candidate" >/dev/null 2>&1 || continue
            key="$("$candidate" -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())' 2>/dev/null | tr -d '\r\n')"
            [ "${#key}" -eq 44 ] && break
        done
    fi
    [ "${#key}" -eq 44 ] || return 1
    printf '%s' "$key"
}

#: Read a value out of .env, comments stripped.
env_value() {
    sed 's/#.*//' .env 2>/dev/null | grep -E "^[[:space:]]*$1=" | tail -1 \
        | cut -d= -f2- | tr -d '[:space:]'
}

#: Replace one value in place. The file is rewritten because BSD and GNU sed
#: disagree about -i.
set_env_value() {
    awk -v k="$1" -v v="$2" \
        'index($0, k "=") == 1 { print k "=" v; next } { print }' \
        .env > .env.tmp && mv .env.tmp .env
}

#: A value that was shipped rather than chosen. Counts as absent.
is_placeholder() {
    case "$1" in
        ''|REPLACE_ME*|*change-me*|*changeme*|*CHANGEME*) return 0 ;;
        *) return 1 ;;
    esac
}

#: Fill a secret that was never really set, and never touch a real one: a
#: rewritten SECRET_ENCRYPTION_KEY makes every stored credential
#: undecryptable, which is worse than any error message.
ensure_secret() {
    local key="$1" hint="$2" current generated
    current="$(env_value "$key")"
    is_placeholder "$current" || return 0
    if generated="$(generate_key)"; then
        set_env_value "$key" "$generated"
        ok "generated $key"
    else
        fail "cannot generate $key: this machine has no openssl, no readable
    /dev/urandom and no working python. Put a value in .env by hand:
        $hint"
    fi
}

# A password is not a key: it is typed by a person, so it is shorter and
# avoids the characters that are easy to lose across a copy and paste.
generate_password() {
    local raw
    if raw="$(generate_key 2>/dev/null)"; then
        printf '%s' "$raw" | tr -d '=+/' | cut -c1-20
        return 0
    fi
    return 1
}

# The administrator's password, generated per deployment rather than shipped.
#
# It used to be `Admin@123456`, written in .env.example, seeded by every
# install, and printed on the sign-in page along with four other accounts that
# shared it. Anybody who could open the page had a platform administrator.
ensure_admin_password() {
    local current generated
    current="$(env_value SEED_ADMIN_PASSWORD)"
    if [ -n "$current" ] && ! is_placeholder "$current"; then
        ADMIN_PASSWORD_SOURCE="da co trong .env"
        return 0
    fi
    if generated="$(generate_password)"; then
        set_env_value SEED_ADMIN_PASSWORD "$generated"
        ADMIN_PASSWORD_GENERATED="$generated"
        ADMIN_PASSWORD_SOURCE="vua sinh"
        ok "generated SEED_ADMIN_PASSWORD"
    else
        fail "cannot generate SEED_ADMIN_PASSWORD: this machine has no openssl,
    no readable /dev/urandom and no working python. Put one in .env by hand:
        SEED_ADMIN_PASSWORD=<a password you choose>"
    fi
}

if [ ! -f .env ]; then
    [ -f .env.example ] || fail ".env is missing and there is no .env.example to copy"
    step "Creating .env from .env.example"
    cp .env.example .env
    ok "wrote .env"
fi

# Runs on every invocation, not only on creation. A clone from six weeks ago
# has an .env without the keys added since, and a setting missing from the file
# is a setting nobody knows exists: CONNECTOR_MEMORY_LIMIT decides whether a
# sync survives on a small VM, and Compose's built-in default hid it.
sync_env_keys() {
    [ -f .env.example ] || return 0
    local added=0 line key
    while IFS= read -r line; do
        case "$line" in
            [A-Z]*=*) key="${line%%=*}" ;;
            *) continue ;;
        esac
        grep -qE "^[[:space:]]*$key=" .env && continue
        if [ "$added" -eq 0 ]; then
            printf '\n# ── run.sh: khóa mới có trong .env.example ──\n' >> .env
            added=1
        fi
        printf '%s\n' "$line" >> .env
        info "added $key"
    done < .env.example
    if [ "$added" -eq 1 ]; then
        warn "new settings were appended to .env; what they do is in .env.example"
    fi
    return 0
}
sync_env_keys

ensure_admin_password
ensure_secret SECRET_ENCRYPTION_KEY 'SECRET_ENCRYPTION_KEY=<44 ký tự urlsafe-base64>'
# Signs session cookies. Shipped as one fixed string, so anybody holding this
# repository could mint a session for a deployment that never changed it.
# Generated per machine now.
ensure_secret JWT_SECRET 'JWT_SECRET=<chuỗi ngẫu nhiên>'

# The key is the one value whose mistakes stay silent until the first
# credential is saved, so it is checked here instead of discovered there.
KEY_VALUE="$(env_value SECRET_ENCRYPTION_KEY)"
if [ "${#KEY_VALUE}" -ne 44 ]; then
    fail "SECRET_ENCRYPTION_KEY in .env is ${#KEY_VALUE} characters; it has to be 44
    (32 bytes, urlsafe base64). Nothing else would complain until the first
    Source is saved, so this stops here. Generate one with:
        openssl rand -base64 32 | tr '+/' '-_'"
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
# Read for the pre-flight port check. Every one of these is published on the
# host, so every one of them can collide with another project on this machine.
FRONTEND_PORT="$(get_env FRONTEND_PORT 3000)"
POSTGRES_PORT="$(get_env POSTGRES_PORT 55432)"
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

# ── pre-flight ────────────────────────────────────────────────────────────
#
# Checked here, after the read-only actions have already returned and before
# anything is built. A port collision used to surface as the last line of a
# ten-minute build -- `Bind for 127.0.0.1:8010 failed: port is already
# allocated` -- with nothing saying who holds it or which setting moves it.

#: The container publishing PORT on the host, if it is a container at all.
port_holder() {
    docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
        | awk -F'\t' -v p=":$1->" 'index($2, p) { print $1; exit }'
}

#: Is anything listening on PORT? Several probes because none of them exists
#: everywhere: Git Bash on Windows has none of ss, lsof or netstat by default.
port_busy() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | grep -qE "[:.]$port[[:space:]]" && return 0
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -qE "[:.]$port[[:space:]]+.*LISTEN" && return 0
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
    fi
    # Last resort, and the only one that works everywhere: ask Docker. It
    # misses non-Docker listeners, so it is a fallback rather than the rule.
    [ -n "$(port_holder "$port")" ]
}

check_ports() {
    local conflicts=0 name port holder mine
    # The project's own containers already hold these ports on a restart, and
    # that is not a conflict -- Compose is about to replace them.
    mine="$(docker ps --filter label=com.docker.compose.project=appbi-pipeline \
                --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"
    for entry in "PROXY_PORT $PROXY_PORT" "API_PORT $API_PORT" \
                 "FRONTEND_PORT $FRONTEND_PORT" "POSTGRES_PORT $POSTGRES_PORT"; do
        name="${entry%% *}"; port="${entry##* }"
        [ -n "$port" ] || continue
        port_busy "$port" || continue
        holder="$(port_holder "$port")"
        case " $mine " in *" $holder "*) continue ;; esac
        conflicts=1
        if [ -n "$holder" ]; then
            warn "port $port is taken by container '$holder' ($name)"
        else
            warn "port $port is taken by something on this machine ($name)"
        fi
    done
    if [ "$conflicts" = 1 ]; then
        fail "the ports above are in use, so the stack cannot start. Either stop
    whatever holds them, or move this deployment by setting the named
    variables in .env -- for example API_PORT=8011 -- and run this again.
    Nothing has been built yet."
    fi
}
check_ports

# Two containers run per sync, and the arithmetic is not obvious enough to
# leave to the reader: somebody on a 2-core VM with the shipped defaults is
# asking for 8 GB of connectors and will meet the OOM killer instead of an
# error message.
check_memory_budget() {
    local total_bytes total_mb runs limit_mb budget
    total_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    case "$total_bytes" in ''|*[!0-9]*) return 0 ;; esac
    [ "$total_bytes" -gt 0 ] || return 0
    total_mb=$(( total_bytes / 1024 / 1024 ))

    runs="$(get_env MAX_CONCURRENT_RUNS_GLOBAL 4)"
    case "$runs" in ''|*[!0-9]*) runs=4 ;; esac

    # `1g`, `1500m` or a bare byte count -- all three are what docker accepts.
    local raw_limit
    raw_limit="$(get_env CONNECTOR_MEMORY_LIMIT 1g)"
    limit_mb=0
    case "$raw_limit" in
        '')      limit_mb=0 ;;
        *[gG])   limit_mb=$(( ${raw_limit%[gG]} * 1024 )) ;;
        *[mM])   limit_mb="${raw_limit%[mM]}" ;;
        *[0-9])  limit_mb=$(( raw_limit / 1024 / 1024 )) ;;
    esac
    case "$limit_mb" in ''|*[!0-9]*) limit_mb=1024 ;; esac

    budget=$(( runs * 2 * limit_mb + 1000 ))
    if [ "$limit_mb" -gt 0 ] && [ "$budget" -gt "$total_mb" ]; then
        warn "Docker has ${total_mb} MB; ${runs} concurrent runs x 2 containers x
      ${limit_mb} MB plus ~1000 MB for AppBI needs about ${budget} MB. A sync
      may be killed for memory. Set MAX_CONCURRENT_RUNS_GLOBAL=1 in .env --
      on two cores concurrency costs RAM without buying speed."
    fi
}
check_memory_budget

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
if ! dc up $UP_ARGS; then
    # Naming `api` unconditionally sent people to the wrong logs: when the
    # migration job is what failed, `api` never started and its log is empty.
    # Ask Compose which container actually exited badly.
    BROKEN_SERVICE=''
    for cid in $(dc ps -a --status exited --quiet 2>/dev/null); do
        if [ "$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null)" != "0" ]; then
            BROKEN_SERVICE="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$cid" 2>/dev/null)"
            [ -n "$BROKEN_SERVICE" ] && break
        fi
    done
    if [ -n "$BROKEN_SERVICE" ]; then
        fail "the stack did not start: '$BROKEN_SERVICE' failed.
    ./run.sh --logs $BROKEN_SERVICE"
    fi
    fail "the stack did not start. Check the error above; './run.sh --logs api'
    and './run.sh --logs migrate' are the two worth reading."
fi

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

ADMIN_EMAIL="$(get_env SEED_ADMIN_EMAIL 'admin@appbi.local')"
printf '\n'
info "web UI    http://localhost:${PROXY_PORT}"
info "API       http://localhost:${API_PORT}"
info "engine    ${ENGINE_TYPE}"
info "sign in   ${ADMIN_EMAIL}"
if [ -n "${ADMIN_PASSWORD_GENERATED:-}" ]; then
    # Printed once, on the run that created it, and never again -- and never
    # in the web UI. After this it lives in .env, which is gitignored.
    printf '\n%s' "$BOLD"
    info "mat khau quan tri vua duoc sinh cho ban cai nay:"
    info "    ${ADMIN_PASSWORD_GENERATED}"
    printf '%s' "$RESET"
    info "luu lai ngay. Lan chay sau se khong in nua; no nam trong .env"
    info "o khoa SEED_ADMIN_PASSWORD. Doi gia tri do roi chay lai ./run.sh"
    info "thi mat khau trong co so du lieu doi theo."
else
    info "mat khau   xem SEED_ADMIN_PASSWORD trong .env"
fi
printf '\n%s' "$DIM"
info "./run.sh --status      what is running"
info "./run.sh --logs api    follow a service"
info "./run.sh --stop        stop without losing anything"
printf '%s\n' "$RESET"
