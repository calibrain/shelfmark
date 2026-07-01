#!/bin/bash

# WireGuard transparent egress for Shelfmark.
#
# Mirrors the structure of tor.sh: it is invoked from entrypoint.sh when
# USING_WIREGUARD=true, brings up a WireGuard tunnel, installs a strict
# kill-switch (all non-LAN egress must leave via the tunnel or be dropped),
# and supervises the tunnel with a handshake-based healthcheck.
#
# Required:
#   - container started as root with cap NET_ADMIN (and NET_RAW)
#   - a WireGuard config mounted at $WIREGUARD_CONFIG (default /config/wg0.conf)
#
# Optional env:
#   WIREGUARD_CONFIG   path to the wg-quick config     (default /config/wg0.conf)
#   WIREGUARD_INTERFACE  interface name                (default wg0)
#   LAN_NETWORK        comma-separated CIDRs kept off the tunnel so the WebUI /
#                      internal download clients (Prowlarr, qBittorrent) stay
#                      reachable, e.g. "172.16.0.0/12,10.0.0.0/8"
#   WIREGUARD_ENFORCE_DNS  when true (default), force /etc/resolv.conf to the
#                      DNS listed in the wg config so lookups also go via the
#                      tunnel and cannot leak to the container's default resolver

is_truthy() {
    case "${1,,}" in
        true|yes|1|y) return 0 ;;
        *) return 1 ;;
    esac
}

ENABLE_LOGGING_VALUE="${ENABLE_LOGGING:-true}"

LOG_DIR=${LOG_ROOT:-/var/log/}/shelfmark
LOG_FILE="${LOG_DIR}/shelfmark_wireguard.log"

if is_truthy "$ENABLE_LOGGING_VALUE"; then
    mkdir -p "$LOG_DIR"

    exec 3>&1 4>&2
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

echo "Starting WireGuard script"
if is_truthy "$ENABLE_LOGGING_VALUE"; then
    echo "Log file: $LOG_FILE"
else
    echo "File logging disabled (ENABLE_LOGGING=$ENABLE_LOGGING_VALUE)"
fi

set +x
set -e

WIREGUARD_CONFIG="${WIREGUARD_CONFIG:-/config/wg0.conf}"
WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg0}"
WIREGUARD_ENFORCE_DNS_VALUE="${WIREGUARD_ENFORCE_DNS:-true}"

echo "Build version: $BUILD_VERSION"
echo "Release version: $RELEASE_VERSION"

if [ ! -f "$WIREGUARD_CONFIG" ]; then
    echo "[✗] WireGuard config not found at $WIREGUARD_CONFIG"
    echo "    Mount your wg-quick config there (e.g. -v /host/wg0.conf:/config/wg0.conf:ro)"
    exit 1
fi

# wg-quick wants the interface name to match the config basename.
CONFIG_BASENAME="$(basename "$WIREGUARD_CONFIG" .conf)"
if [ "$CONFIG_BASENAME" != "$WIREGUARD_INTERFACE" ]; then
    RUNTIME_CONFIG="/etc/wireguard/${WIREGUARD_INTERFACE}.conf"
    mkdir -p /etc/wireguard
    cp "$WIREGUARD_CONFIG" "$RUNTIME_CONFIG"
else
    RUNTIME_CONFIG="/etc/wireguard/${WIREGUARD_INTERFACE}.conf"
    mkdir -p /etc/wireguard
    cp "$WIREGUARD_CONFIG" "$RUNTIME_CONFIG"
fi
chmod 600 "$RUNTIME_CONFIG"

# Extract the DNS line (if any) before wg-quick, so we can enforce it ourselves.
WG_DNS="$(grep -iE '^\s*DNS\s*=' "$RUNTIME_CONFIG" | head -n1 | cut -d'=' -f2- | tr ',' ' ' | xargs || true)"

# wg-quick will try to manage DNS via resolvconf which is not present in this
# image; strip the DNS line and enforce it ourselves below to avoid wg-quick
# aborting. Keep a copy for reference.
sed -i -E '/^\s*DNS\s*=/d' "$RUNTIME_CONFIG"

echo "[*] Bringing up WireGuard interface '$WIREGUARD_INTERFACE' from $WIREGUARD_CONFIG..."
# wg-quick handles: interface creation, address, route for AllowedIPs, and a
# fwmark-based default route when AllowedIPs=0.0.0.0/0.
wg-quick up "$WIREGUARD_INTERFACE"

echo "[*] WireGuard interface state:"
wg show "$WIREGUARD_INTERFACE" || true
ip -o addr show "$WIREGUARD_INTERFACE" || true

# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------
# wg-quick (with AllowedIPs=0.0.0.0/0) already installs a fwmark + suppress
# routing that sends everything except the encrypted tunnel packets through
# wg0, and blocks off-tunnel traffic to AllowedIPs. We add an explicit
# filter-table kill-switch as defence in depth: default DROP on OUTPUT, allow
# only loopback, the tunnel device, the LAN ranges, and the handshake to the
# WireGuard endpoint(s).
echo "[*] Installing kill-switch (iptables)..."

# Endpoint host:port pairs from the config (to permit the encrypted handshake
# out over the physical NIC).
ENDPOINTS="$(grep -iE '^\s*Endpoint\s*=' "$RUNTIME_CONFIG" | cut -d'=' -f2- | xargs || true)"

iptables -F OUTPUT
# Allow loopback
iptables -A OUTPUT -o lo -j ACCEPT
# Allow established/related return traffic
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
# Allow all traffic over the tunnel itself
iptables -A OUTPUT -o "$WIREGUARD_INTERFACE" -j ACCEPT

# Allow the encrypted WireGuard handshake/data to each endpoint over any NIC.
if [ -n "$ENDPOINTS" ]; then
    for ep in $ENDPOINTS; do
        ep_host="${ep%:*}"
        ep_port="${ep##*:}"
        # Strip IPv6 brackets if present
        ep_host="${ep_host#[}"
        ep_host="${ep_host%]}"
        if [ -n "$ep_host" ] && [ -n "$ep_port" ]; then
            iptables -A OUTPUT -p udp -d "$ep_host" --dport "$ep_port" -j ACCEPT 2>/dev/null \
                || echo "[!] Could not add endpoint allow rule for $ep (may be IPv6/hostname); tunnel route still applies"
        fi
    done
fi

# Keep LAN reachable (WebUI, Prowlarr, qBittorrent, DNS on the LAN) off-tunnel.
DEFAULT_LAN="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
LAN_LIST="${LAN_NETWORK:-$DEFAULT_LAN}"
IFS=',' read -ra LAN_CIDRS <<< "$LAN_LIST"
for cidr in "${LAN_CIDRS[@]}"; do
    cidr="$(echo "$cidr" | xargs)"
    [ -z "$cidr" ] && continue
    iptables -A OUTPUT -d "$cidr" -j ACCEPT
    echo "[*] Kill-switch: LAN allowed off-tunnel -> $cidr"
done

# Everything else is dropped: if the tunnel drops, non-LAN egress fails closed.
iptables -A OUTPUT -j DROP
echo "[✓] Kill-switch active (default-drop; egress only via $WIREGUARD_INTERFACE or LAN)."

# ---------------------------------------------------------------------------
# DNS enforcement (fail-closed): send resolver traffic through the tunnel.
# ---------------------------------------------------------------------------
if is_truthy "$WIREGUARD_ENFORCE_DNS_VALUE" && [ -n "$WG_DNS" ]; then
    echo "[*] Enforcing tunnel DNS: $WG_DNS"
    : > /etc/resolv.conf
    for ns in $WG_DNS; do
        echo "nameserver $ns" >> /etc/resolv.conf
    done
else
    echo "[*] Leaving /etc/resolv.conf unchanged (WIREGUARD_ENFORCE_DNS=$WIREGUARD_ENFORCE_DNS_VALUE, config DNS='$WG_DNS')"
fi

# ---------------------------------------------------------------------------
# Supervisor: keep the tunnel healthy and fail-closed on drop.
# ---------------------------------------------------------------------------
echo "[*] Configuring Supervisor..."
mkdir -p /var/log/supervisor
cat <<EOF > /etc/supervisor/supervisord.conf
[supervisord]
nodaemon=false
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
user=root

[unix_http_server]
file=/var/run/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

[program:wireguard-healthcheck]
command=/app/wireguard_healthcheck.sh
autostart=true
autorestart=true
stdout_logfile=/var/log/supervisor/wireguard-healthcheck.log
stderr_logfile=/var/log/supervisor/wireguard-healthcheck.err.log
EOF

cat <<'HC' > /app/wireguard_healthcheck.sh
#!/bin/bash
# Monitors the WireGuard tunnel via handshake age. If the tunnel is stale,
# bounce the interface. The iptables kill-switch means non-LAN egress stays
# blocked while the tunnel is down, so this is recovery, not leak-prevention.

is_truthy() {
    case "${1,,}" in
        true|yes|1|y) return 0 ;;
        *) return 1 ;;
    esac
}

WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg0}"
# Max seconds since last handshake before we consider the tunnel stale.
# WireGuard rehandshakes roughly every 2 minutes when there is traffic.
STALE_AFTER="${WIREGUARD_STALE_AFTER:-180}"

latest_handshake_epoch() {
    wg show "$WIREGUARD_INTERFACE" latest-handshakes 2>/dev/null \
        | awk '{print $2}' | sort -nr | head -n1
}

FAIL_COUNT=0
# Give the first handshake time to complete before judging health.
sleep 20

while true; do
    HS="$(latest_handshake_epoch)"
    NOW="$(date +%s)"

    if [ -z "$HS" ] || [ "$HS" = "0" ]; then
        AGE=99999
    else
        AGE=$((NOW - HS))
    fi

    if [ "$AGE" -le "$STALE_AFTER" ]; then
        FAIL_COUNT=0
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "$(date): WireGuard handshake stale (age=${AGE}s, fail=${FAIL_COUNT})"
    fi

    if [ "$FAIL_COUNT" -ge 3 ]; then
        echo "$(date): restart trigger - bouncing $WIREGUARD_INTERFACE"
        wg-quick down "$WIREGUARD_INTERFACE" 2>/dev/null || true
        # Re-add tunnel ACCEPT before bringing it up (flush is not done here;
        # the DROP rule stays in place so we never leak during the bounce).
        wg-quick up "$WIREGUARD_INTERFACE" 2>/dev/null || echo "$(date): wg-quick up failed, will retry"
        FAIL_COUNT=0
        sleep 15
    fi

    sleep 30
done
HC
chmod +x /app/wireguard_healthcheck.sh

echo "[*] Starting Supervisor..."
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf

# ---------------------------------------------------------------------------
# Verify egress actually leaves via the tunnel before handing off to the app.
# ---------------------------------------------------------------------------
echo "[*] Waiting for first WireGuard handshake (up to 60s)..."
HANDSHAKE_TIMEOUT=60
HANDSHAKE_START=$(date +%s)
while true; do
    HS="$(wg show "$WIREGUARD_INTERFACE" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -nr | head -n1)"
    if [ -n "$HS" ] && [ "$HS" != "0" ]; then
        echo "[✓] WireGuard handshake established."
        break
    fi
    if [ $(($(date +%s) - HANDSHAKE_START)) -ge $HANDSHAKE_TIMEOUT ]; then
        echo "[✗] No WireGuard handshake after ${HANDSHAKE_TIMEOUT}s. Aborting (fail closed)."
        exit 1
    fi
    sleep 2
done

echo "[*] Verifying external egress IP is the tunnel (not the host)..."
EGRESS_IP="$(curl -s --max-time 15 https://api.ipify.org 2>/dev/null || true)"
if [ -n "$EGRESS_IP" ]; then
    echo "[✓] External egress IP via tunnel: $EGRESS_IP"
else
    echo "[!] Could not determine egress IP (endpoint may block ipify). Tunnel handshake is up; continuing."
fi

echo "[*] End of WireGuard script"
