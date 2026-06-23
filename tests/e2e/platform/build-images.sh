#!/usr/bin/env bash
# Build every buildable image in the e2e stack once (the heavy `shelfmark` image
# plus the mock-* role images), so run-matrix.sh / run-e2e.sh with E2E_NO_BUILD=1
# can reuse them instead of rebuilding the xvfb/chromium layer per profile.
set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PLATFORM_DIR"

# Activate every profile that owns a buildable service so they all get built.
# (Download clients, coredns, proxies are pre-built images — nothing to build.)
export COMPOSE_PROFILES="bypasser-external,full,dns-doh"
echo "==> building shelfmark + mock images (one cold build of the chromium layer)"
docker compose -f docker-compose.e2e.yml build
echo "==> done. Reuse with: E2E_NO_BUILD=1 ./run-e2e.sh env/<profile>.env"
