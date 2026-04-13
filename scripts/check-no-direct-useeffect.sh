#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
frontend_src="$repo_root/src/frontend/src"

allowed_files=(
  "src/hooks/useMountEffect.ts"
  "src/hooks/useBodyScrollLock.ts"
  "src/hooks/useEscapeKey.ts"
  "src/hooks/useDismiss.ts"
  "src/hooks/app/useStatusChangeNotifications.ts"
  "src/hooks/useRealtimeStatus.ts"
  "src/hooks/useActivity.ts"
)

rg_args=(
  -n
  -P
  -e '^(?!\s*(//|/\*|\*)).*?(useEffect\(|React\.useEffect\()'
  "$frontend_src"
)

for allowed_file in "${allowed_files[@]}"; do
  rg_args+=(--glob "!$allowed_file")
done

if rg "${rg_args[@]}"; then
  cat <<'EOF'
Direct useEffect usage found outside the approved sync layer.
Move the logic behind useMountEffect or a named sync hook, or add the file to the allowlist if it is genuinely part of the approved infra layer.
EOF
  exit 1
fi
