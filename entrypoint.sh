#!/bin/bash

is_truthy() {
    case "${1,,}" in
        true|yes|1|y) return 0 ;;
        *) return 1 ;;
    esac
}

ENABLE_LOGGING_VALUE="${ENABLE_LOGGING:-true}"
LOG_PIPE_DIR=""
LOG_PIPE=""
TEE_PID=""

start_file_logging() {
    local logfile="$1"

    LOG_PIPE_DIR="$(mktemp -d)"
    LOG_PIPE="${LOG_PIPE_DIR}/shelfmark-log.pipe"
    mkfifo "$LOG_PIPE"

    tee -a "$logfile" < "$LOG_PIPE" &
    TEE_PID=$!

    exec 3>&1 4>&2
    exec > "$LOG_PIPE" 2>&1
}

stop_file_logging() {
    if [ -z "${TEE_PID:-}" ]; then
        return 0
    fi

    exec 1>&3 2>&4
    exec 3>&- 4>&-

    rm -f "$LOG_PIPE"
    rmdir "$LOG_PIPE_DIR" 2>/dev/null || true

    wait "$TEE_PID" 2>/dev/null || true
    TEE_PID=""
}

if is_truthy "$ENABLE_LOGGING_VALUE"; then
    LOG_DIR=${LOG_ROOT:-/var/log/}/shelfmark
    mkdir -p "$LOG_DIR"
    LOG_FILE="${LOG_DIR}/shelfmark_entrypoint.log"

    # Cleanup any existing files or folders in the log directory
    rm -rf "$LOG_DIR"/*
fi

(
    if [ "$USING_TOR" = "true" ]; then
        ./tor.sh
    fi
)

if is_truthy "$ENABLE_LOGGING_VALUE"; then
    start_file_logging "$LOG_FILE"
fi

echo "Starting entrypoint script"
if is_truthy "$ENABLE_LOGGING_VALUE"; then
    echo "Log file: $LOG_FILE"
else
    echo "File logging disabled (ENABLE_LOGGING=$ENABLE_LOGGING_VALUE)"
fi
set -e

# Print build version
echo "Build version: $BUILD_VERSION"
echo "Release version: $RELEASE_VERSION"

# Configure timezone
if [ "$TZ" ]; then
    echo "Setting timezone to $TZ"
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# Determine user ID with proper precedence:
# 1. PUID (LinuxServer.io standard - recommended)
# 2. UID (legacy, for backward compatibility with existing installs)
# 3. Default to 1000
#
# Note: $UID is a bash builtin that's always set. We use `printenv` to detect
# if UID was explicitly set as an environment variable (e.g., via docker-compose).
if [ -n "$PUID" ]; then
    RUN_UID="$PUID"
    echo "Using PUID=$RUN_UID"
elif printenv UID >/dev/null 2>&1; then
    RUN_UID="$(printenv UID)"
    echo "Using UID=$RUN_UID (legacy - consider migrating to PUID)"
else
    RUN_UID=1000
    echo "Using default UID=$RUN_UID"
fi

# Determine group ID with proper precedence:
# 1. PGID (LinuxServer.io standard - recommended)
# 2. GID (legacy, for backward compatibility with existing installs)
# 3. Default to 1000
if [ -n "$PGID" ]; then
    RUN_GID="$PGID"
    echo "Using PGID=$RUN_GID"
elif [ -n "$GID" ]; then
    RUN_GID="$GID"
    echo "Using GID=$RUN_GID (legacy - consider migrating to PGID)"
else
    RUN_GID=1000
    echo "Using default GID=$RUN_GID"
fi

if ! getent group "$RUN_GID" >/dev/null; then
    echo "Adding group $RUN_GID with name appuser"
    groupadd -g "$RUN_GID" appuser
fi

# Create user if it doesn't exist
if ! id -u "$RUN_UID" >/dev/null 2>&1; then
    echo "Adding user $RUN_UID with name appuser"
    useradd -u "$RUN_UID" -g "$RUN_GID" -d /app -s /sbin/nologin appuser
fi

# Get username for the UID (whether we just created it or it existed)
USERNAME=$(getent passwd "$RUN_UID" | cut -d: -f1)
echo "Username for UID $RUN_UID is $USERNAME"

test_write() {
    folder=$1
    test_file=$folder/shelfmark_TEST_WRITE
    mkdir -p $folder
    (
        echo 0123456789_TEST | sudo -E -u "$USERNAME" HOME=/app tee $test_file > /dev/null
    )
    FILE_CONTENT=$(cat $test_file || echo "")
    rm -f $test_file
    [ "$FILE_CONTENT" = "0123456789_TEST" ]
    result=$?
    if [ $result -eq 0 ]; then
        result_text="true"
    else
        result_text="false"
    fi
    echo "Test write to $folder by $USERNAME: $result_text"
    return $result
}

make_writable() {
    folder=$1
    set +e
    test_write $folder
    is_writable=$?
    set -e
    if [ $is_writable -eq 0 ]; then
        echo "Folder $folder is writable, no need to change ownership"
    else
        echo "Folder $folder is not writable, changing ownership"
        change_ownership $folder
        chmod -R g+r,g+w $folder || echo "Failed to change group permissions for ${folder}, continuing..."
    fi
    # Fix any misowned subdirectories/files (e.g., from previous runs as root)
    if [ -d "$folder" ]; then
        misowned_count=$(find "$folder" -mindepth 1 \( ! -user "$RUN_UID" -o ! -group "$RUN_GID" \) 2>/dev/null | wc -l)
        if [ "$misowned_count" -gt 0 ]; then
            echo "Fixing ownership of $misowned_count files/directories in $folder"
            find "$folder" -mindepth 1 \( ! -user "$RUN_UID" -o ! -group "$RUN_GID" \) \
                -exec chown "$RUN_UID:$RUN_GID" {} \; 2>/dev/null || true
        fi
    fi
    test_write $folder || echo "Failed to test write to ${folder}, continuing..."
}

# Ensure proper ownership of application directories
change_ownership() {
  folder=$1
  mkdir -p $folder
  echo "Changing ownership of $folder to $USERNAME:$RUN_GID"
  chown -R "${RUN_UID}" "${folder}" || echo "Failed to change user ownership for ${folder}, continuing..."
  chown -R ":${RUN_GID}" "${folder}" || echo "Failed to change group ownership for ${folder}, continuing..."
}

change_ownership /app
change_ownership /var/log/shelfmark
change_ownership /tmp/shelfmark

# SeleniumBase (internal bypasser) writes a patched chromedriver binary (uc_driver)
# into its own drivers directory. Some NAS/docker setups can apply restrictive ACLs
# to extracted image layers that block non-root writes; ensure the runtime UID owns it.
if [ "${USING_EXTERNAL_BYPASSER}" != "true" ]; then
    set +e
    SELENIUMBASE_DRIVERS_DIR=$(python3 -c "import pathlib, seleniumbase; print(pathlib.Path(seleniumbase.__file__).resolve().parent / 'drivers')" 2>/dev/null)
    set -e

    if [ -n "$SELENIUMBASE_DRIVERS_DIR" ] && [ -d "$SELENIUMBASE_DRIVERS_DIR" ]; then
        change_ownership "$SELENIUMBASE_DRIVERS_DIR"

        # If the driver already exists, ensure it's executable for the runtime user.
        if [ -f "${SELENIUMBASE_DRIVERS_DIR}/uc_driver" ]; then
            chmod +x "${SELENIUMBASE_DRIVERS_DIR}/uc_driver" || echo "Failed to chmod uc_driver, continuing..."
        fi
    fi
fi

# Test write to all folders
make_writable ${CONFIG_DIR:-/config}
make_writable ${INGEST_DIR:-/books}

# Fix permissions on directories configured in settings
echo "Checking for additional configured directories..."
if [ -f /app/scripts/fix_permissions.py ]; then
    configured_dirs=$(python3 /app/scripts/fix_permissions.py 2>/dev/null || echo "")
    if [ -n "$configured_dirs" ]; then
        echo "$configured_dirs" | while read -r dir; do
            if [ -n "$dir" ] && [ -d "$dir" ]; then
                echo "Checking configured directory: $dir"
                make_writable "$dir"
            fi
        done
    fi
fi

# Fallback to root if config dir is still not writable (common on NAS/Unraid after upgrade from v0.4.0)
CONFIG_PATH=${CONFIG_DIR:-/config}
set +e
test_write "$CONFIG_PATH" >/dev/null 2>&1
config_ok=$?
set -e

if [ $config_ok -ne 0 ] && [ "$RUN_UID" != "0" ]; then
    config_owner=$(stat -c '%u' "$CONFIG_PATH" 2>/dev/null || echo "unknown")
    if [ "$config_owner" = "0" ]; then
        echo ""
        echo "========================================================"
        echo "WARNING: Permission issue detected!"
        echo ""
        echo "Config directory is owned by root but PUID=$RUN_UID."
        echo "This typically happens after upgrading from v0.4.0 where"
        echo "PUID/PGID settings were not respected."
        echo ""
        echo "Falling back to running as root to prevent data loss."
        echo ""
        echo "To fix this permanently, run on your HOST machine:"
        echo "  chown -R $RUN_UID:$RUN_GID /path/to/config"
        echo ""
        echo "Then restart the container."
        echo "========================================================"
        echo ""
        RUN_UID=0
        RUN_GID=0
        USERNAME=root
    fi
fi

# Always run Gunicorn (even when DEBUG=true) to ensure Socket.IO WebSocket
# upgrades work reliably on customer machines.
# Map app LOG_LEVEL (often DEBUG/INFO/...) to gunicorn's --log-level (lowercase).
gunicorn_loglevel=$([ "$DEBUG" = "true" ] && echo debug || echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
command="gunicorn --log-level ${gunicorn_loglevel} --access-logfile - --error-logfile - --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 -t 300 -b ${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-8084} shelfmark.main:app"

# If DEBUG and not using an external bypass
if [ "$DEBUG" = "true" ] && [ "$USING_EXTERNAL_BYPASSER" != "true" ]; then
    set +e
    set -x
    echo "vvvvvvvvvvvv DEBUG MODE vvvvvvvvvvvv"
    echo "Starting Xvfb for debugging"
    python3 -c "from pyvirtualdisplay import Display; Display(visible=False, size=(1440,1880)).start()"
    id
    free -h
    uname -a
    ulimit -a
    df -h /tmp
    env | sort
    mount
    cat /proc/cpuinfo
    echo "==========================================="
    echo "Debugging Chrome itself"
    chromium --version
    mkdir -p /tmp/chrome_crash_dumps
    timeout --preserve-status 5s chromium \
            --headless=new \
            --no-sandbox \
            --disable-gpu \
            --enable-logging --v=1 --log-level=0 \
            --log-file=/tmp/chrome_entrypoint_test.log \
            --crash-dumps-dir=/tmp/chrome_crash_dumps \
            < /dev/null 
    EXIT_CODE=$?
    echo "Chrome exit code: $EXIT_CODE"
    ls -lh /tmp/chrome_entrypoint_test.log
    ls -lh /tmp/chrome_crash_dumps
    if [[ "$EXIT_CODE" -ne 0 && "$EXIT_CODE" -le 127 ]]; then
        echo "Chrome failed to start. Lets trace it"
        apt-get update && apt-get install -y strace
        timeout --preserve-status 10s strace -f -o "/tmp/chrome_strace.log" chromium \
                --headless=new \
                --no-sandbox \
                --version \
                < /dev/null
        EXIT_CODE=$?
        echo "Strace exit code: $EXIT_CODE"
        echo "Strace log:"
        cat /tmp/chrome_strace.log
    fi

    pkill -9 -f Xvfb
    pkill -9 -f chromium
    sleep 1
    ps aux
    set +x
    set -e
    echo "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^"
fi

# Verify /tmp has at least 1MB of space and is writable/readable
echo "Verifying /tmp has enough space"
rm -f /tmp/test.shelfmark
if dd if=/dev/zero of=/tmp/test.shelfmark bs=1M count=1 2>/dev/null && \
   [ "$(wc -c < /tmp/test.shelfmark)" -eq 1048576 ]; then
    rm -f /tmp/test.shelfmark
    echo "Success: /tmp is writable and readable"
else
    echo "Failure: /tmp is not writable or has insufficient space"
    exit 1
fi

echo "Running command: '$command' as '$USERNAME' (debug=$is_debug)"

# Set umask for file permissions (default: 0022 = files 644, dirs 755)
UMASK_VALUE=${UMASK:-0022}
echo "Setting umask to $UMASK_VALUE"
umask $UMASK_VALUE

stop_file_logging
exec sudo -E -u "$USERNAME" HOME=/app $command
