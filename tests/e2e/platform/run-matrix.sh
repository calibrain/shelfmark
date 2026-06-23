#!/usr/bin/env bash
# Run the full config matrix: every profile, in sequence, aggregating results.
#
#   ./run-matrix.sh                 # all profiles
#   ./run-matrix.sh baseline dns-manual   # a subset (by profile name)
set -uo pipefail

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PLATFORM_DIR"

if [[ $# -gt 0 ]]; then
  PROFILES=("$@")
else
  PROFILES=(baseline bypasser-external bypasser-disabled dns-manual dns-blocked dns-doh \
            proxy-http proxy-socks tor client-transmission client-deluge)
fi

# Build the (heavy) images once, then reuse them across every profile so the
# matrix doesn't rebuild the xvfb/chromium layer N times. Set NO_PREBUILD=1 to
# skip (e.g. to let each run rebuild from source).
if [[ "${NO_PREBUILD:-0}" != "1" ]]; then
  echo "==> pre-building images once (reused by all profiles)"
  ./build-images.sh
  export E2E_NO_BUILD=1
fi

declare -A RESULT
FAILED=0
for p in "${PROFILES[@]}"; do
  echo "========================================================================"
  echo "  PROFILE: $p"
  echo "========================================================================"
  if ./run-e2e.sh "env/$p.env"; then
    RESULT[$p]="PASS"
  else
    RESULT[$p]="FAIL"
    FAILED=1
  fi
done

echo "========================================================================"
echo "  MATRIX SUMMARY"
echo "========================================================================"
for p in "${PROFILES[@]}"; do
  printf "  %-22s %s\n" "$p" "${RESULT[$p]:-SKIP}"
done
exit $FAILED
