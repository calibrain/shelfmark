#!/usr/bin/env bash

set -euo pipefail

LATEST_IMAGE="${LATEST_IMAGE:-ghcr.io/calibrain/shelfmark:latest}"
LEGACY_IMAGE="${LEGACY_IMAGE:-ghcr.io/calibrain/shelfmark:v1.0.2}"
WAIT_SECONDS="${WAIT_SECONDS:-5}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-120}"

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

cleanup() {
    local name="$1"
    docker rm -f "$name" >/dev/null 2>&1 || true
}

wait_for_startup() {
    local name="$1"
    local elapsed=0

    while [ "$elapsed" -lt "$STARTUP_TIMEOUT_SECONDS" ]; do
        if ! docker inspect "$name" >/dev/null 2>&1; then
            echo "Container $name no longer exists" >&2
            return 1
        fi

        if [ "$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)" != "running" ]; then
            echo "Container $name exited before startup completed" >&2
            docker logs --tail 120 "$name" 2>&1 || true
            return 1
        fi

        if docker exec "$name" sh -lc "id appuser >/dev/null 2>&1 && ps -eo comm,args | awk '\$1 == \"gunicorn\" && index(\$0, \"shelfmark.main:app\") { found=1 } END { exit(found ? 0 : 1) }'" >/dev/null 2>&1; then
            return 0
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo "Timed out waiting for $name to finish startup" >&2
    docker logs --tail 120 "$name" 2>&1 || true
    return 1
}

start_container() {
    local name="$1"
    local image="$2"
    local pre_entrypoint_script="${3:-}"

    cleanup "$name"

    if [ -n "$pre_entrypoint_script" ]; then
        docker run -d \
            --name "$name" \
            --entrypoint sh \
            -e PUID=1000 \
            -e PGID=1000 \
            -e TZ=UTC \
            "$image" \
            -lc "$pre_entrypoint_script
exec /app/entrypoint.sh" >/dev/null
    else
        docker run -d \
            --name "$name" \
            -e PUID=1000 \
            -e PGID=1000 \
            -e TZ=UTC \
            "$image" >/dev/null
        sleep "$WAIT_SECONDS"
    fi

    wait_for_startup "$name"
}

run_probe() {
    local name="$1"
    local mode="${2:-default}"
    docker exec -u appuser -e PROBE_MODE="$mode" "$name" sh -lc 'python3 - <<'"'"'PY'"'"'
import asyncio
import os
import shelfmark.bypass.internal_bypasser as ib


async def run_probe():
    driver = None
    probe_mode = os.environ.get("PROBE_MODE", "default")

    if probe_mode == "proxy_auth" and hasattr(ib, "_get_proxy_string"):
        ib._get_proxy_string = lambda _url: "user:pass@127.0.0.1:8888"

    if hasattr(ib, "_create_cdp_browser"):
        try:
            driver = await ib._create_cdp_browser("https://example.com")
            profile = getattr(getattr(driver, "config", None), "user_data_dir", "")
            print(f"PROBE=OK mode={probe_mode} fn=_create_cdp_browser profile={profile}")
        except Exception as e:
            print(f"PROBE=ERR mode={probe_mode} fn=_create_cdp_browser type={type(e).__name__} msg={e}")
        finally:
            if driver and hasattr(ib, "_close_cdp_driver"):
                await ib._close_cdp_driver(driver)
        return

    if hasattr(ib, "_create_driver"):
        try:
            driver = await ib._create_driver()
            print(f"PROBE=OK mode={probe_mode} fn=_create_driver driver_type={type(driver).__name__}")
        except Exception as e:
            print(f"PROBE=ERR mode={probe_mode} fn=_create_driver type={type(e).__name__} msg={e}")
        finally:
            if driver and hasattr(ib, "_quit_driver"):
                await ib._quit_driver(driver)
        return

    print(f"PROBE=ERR mode={probe_mode} fn=unknown type=RuntimeError msg=no supported startup function found")


asyncio.run(run_probe())
PY'
}

show_logs() {
    local name="$1"
    docker logs --tail 80 "$name" 2>&1 | tail -n 20
}

scenario_latest_baseline() {
    local name="sb-lab-latest-baseline"
    echo
    echo "== latest baseline =="
    start_container "$name" "$LATEST_IMAGE"
    run_probe "$name"
    cleanup "$name"
}

scenario_latest_drivers_readonly() {
    local name="sb-lab-latest-drivers"
    echo
    echo "== latest drivers readonly =="
    start_container "$name" "$LATEST_IMAGE" '
        chown -R root:root /usr/local/lib/python3.10/site-packages/seleniumbase/drivers &&
        chmod -R a-w /usr/local/lib/python3.10/site-packages/seleniumbase/drivers &&
        ls -ld /usr/local/lib/python3.10/site-packages/seleniumbase/drivers
    '
    run_probe "$name"
    cleanup "$name"
}

scenario_latest_proxy_auth_baseline() {
    local name="sb-lab-latest-proxy-baseline"
    echo
    echo "== latest proxy auth baseline =="
    start_container "$name" "$LATEST_IMAGE"
    run_probe "$name" "proxy_auth"
    cleanup "$name"
}

scenario_latest_downloads_readonly() {
    local name="sb-lab-latest-downloads"
    echo
    echo "== latest downloaded_files readonly =="
    start_container "$name" "$LATEST_IMAGE" '
        mkdir -p /app/downloaded_files &&
        touch /app/downloaded_files/pipfinding.lock /app/downloaded_files/proxy_dir.lock &&
        chown -R root:root /app/downloaded_files &&
        chmod -R a-w /app/downloaded_files &&
        find /app/downloaded_files -maxdepth 2 -printf "%M %u:%g %p\n"
    '
    run_probe "$name"
    show_logs "$name"
    cleanup "$name"
}

scenario_latest_proxy_auth_downloads_readonly() {
    local name="sb-lab-latest-proxy-downloads"
    echo
    echo "== latest proxy auth with readonly downloaded_files =="
    start_container "$name" "$LATEST_IMAGE" '
        mkdir -p /app/downloaded_files &&
        touch /app/downloaded_files/pipfinding.lock /app/downloaded_files/proxy_dir.lock &&
        chown appuser:appuser /app/downloaded_files/pipfinding.lock /app/downloaded_files/proxy_dir.lock &&
        chmod 0666 /app/downloaded_files/pipfinding.lock /app/downloaded_files/proxy_dir.lock &&
        chown root:root /app/downloaded_files &&
        chmod 0555 /app/downloaded_files &&
        ls -ld /app/downloaded_files &&
        ls -la /app/downloaded_files
    '
    run_probe "$name" "proxy_auth"
    show_logs "$name"
    cleanup "$name"
}

scenario_latest_bind_mount_readonly() {
    local name="sb-lab-latest-bind-ro"
    local bind_dir
    bind_dir="$(mktemp -d /tmp/sb-lab-bind.XXXXXX)"
    echo
    echo "== latest readonly bind mount for downloaded_files =="
    chmod 0555 "$bind_dir"
    cleanup "$name"
    docker run -d \
        --name "$name" \
        -e PUID=1000 \
        -e PGID=1000 \
        -e TZ=UTC \
        --mount "type=bind,src=${bind_dir},target=/app/downloaded_files,readonly" \
        "$LATEST_IMAGE" >/dev/null
    wait_for_startup "$name"
    run_probe "$name"
    show_logs "$name"
    cleanup "$name"
    rm -rf "$bind_dir"
}

scenario_legacy_drivers_readonly() {
    local name="sb-lab-legacy-drivers"
    echo
    echo "== legacy drivers readonly =="
    start_container "$name" "$LEGACY_IMAGE" '
        chown -R root:root /usr/local/lib/python3.10/site-packages/seleniumbase/drivers &&
        chmod -R a-w /usr/local/lib/python3.10/site-packages/seleniumbase/drivers &&
        ls -ld /usr/local/lib/python3.10/site-packages/seleniumbase/drivers
    '
    run_probe "$name"
    show_logs "$name"
    cleanup "$name"
}

main() {
    require_cmd docker

    scenario_latest_baseline
    scenario_latest_drivers_readonly
    scenario_latest_proxy_auth_baseline
    scenario_latest_downloads_readonly
    scenario_latest_proxy_auth_downloads_readonly
    scenario_latest_bind_mount_readonly
    scenario_legacy_drivers_readonly
}

main "$@"
