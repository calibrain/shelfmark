#!/usr/bin/env bash
# Run the Shelfmark e2e platform for a single config profile.
#
#   ./run-e2e.sh [env/<profile>.env] [extra pytest args...]
#
# Boots the stack defined by the profile env file, waits for health, runs the
# matching cluster tests (the suite skips tests not applicable to the profile),
# then tears down. Set KEEP_UP=1 to leave the stack running for debugging.
set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PLATFORM_DIR"

ENV_FILE="${1:-env/baseline.env}"
shift || true
PYTEST_ARGS=("$@")

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: env file not found: $ENV_FILE" >&2
  echo "available profiles:" >&2
  ls env/*.env >&2
  exit 2
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a   # export SM_*, COMPOSE_PROFILES, E2E_PROFILE
PROFILE="${E2E_PROFILE:-baseline}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.e2e.yml)

STATE_DIR="$PLATFORM_DIR/.state"
LOG_FILE="$STATE_DIR/shelfmark.$PROFILE.log"
mkdir -p "$STATE_DIR/config" "$STATE_DIR/books" "$STATE_DIR/downloads" "$STATE_DIR/tmp"

cleanup() {
  if [[ "${KEEP_UP:-0}" != "1" ]]; then
    echo "==> tearing down ($PROFILE)"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  else
    echo "==> KEEP_UP=1: leaving stack running ($PROFILE)"
  fi
}
trap cleanup EXIT

# E2E_NO_BUILD=1 reuses already-built images (see `make e2e-platform-build` /
# run-matrix.sh) so a matrix run builds the heavy shelfmark image only once.
if [[ "${E2E_NO_BUILD:-0}" == "1" ]]; then
  echo "==> [$PROFILE] starting stack, reusing built images (profiles='${COMPOSE_PROFILES:-<none>}')"
  "${COMPOSE[@]}" up -d --no-build
else
  echo "==> [$PROFILE] building + starting stack (profiles='${COMPOSE_PROFILES:-<none>}')"
  "${COMPOSE[@]}" up -d --build
fi

echo "==> [$PROFILE] waiting for shelfmark health"
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8084/api/health >/dev/null 2>&1; then HEALTHY=1; break; fi
  sleep 2
done

# Capture boot diagnostics for the entrypoint/permission tests.
"${COMPOSE[@]}" logs shelfmark > "$LOG_FILE" 2>&1 || true
RESTARTS="$(docker inspect -f '{{.RestartCount}}' e2e-shelfmark 2>/dev/null || echo 0)"
echo "==> [$PROFILE] healthy=$HEALTHY restarts=$RESTARTS log=$LOG_FILE"

if [[ "$HEALTHY" != "1" && "$PROFILE" != "tor" ]]; then
  echo "error: shelfmark never became healthy under profile '$PROFILE'" >&2
  "${COMPOSE[@]}" logs --tail 40 shelfmark >&2 || true
  exit 1
fi

# Hand context to the pytest suite.
export E2E_PROFILE="$PROFILE"
export E2E_BASE_URL="http://localhost:8084"
export E2E_BOOKS_DIR="$STATE_DIR/books"
export E2E_TMP_DIR="$STATE_DIR/tmp"
export E2E_SHELFMARK_LOG="$LOG_FILE"
export E2E_SHELFMARK_RESTARTS="$RESTARTS"

echo "==> [$PROFILE] running suite"
set +e
( cd "$PLATFORM_DIR/../../.." && \
  uv run pytest tests/e2e/platform/suite -m platform -o addopts="--tb=short" "${PYTEST_ARGS[@]}" )
RC=$?
set -e

echo "==> [$PROFILE] pytest exit=$RC"
exit $RC
