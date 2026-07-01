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
#   WIREGUARD_ENFORCE_DNS  when true (default), force /etc/resolv.conf so DNS
#                      lookups use a defined resolver instead of the container's
#                      inherited one. The resolver used is WIREGUARD_DNS if set,
#                      otherwise the tunnel config's own DNS = line.
#   WIREGUARD_DNS      optional explicit resolver(s) (comma/space separated) to
#                      write into /etc/resolv.conf when WIREGUARD_ENFORCE_DNS is
#                      true. Use this when the VPN provider's push DNS filters
#                      domains you need (e.g. Proton NetShield NXDOMAINs
#                      annas-archive.org). Point it at an on-LAN encrypted
#                      resolver (kept reachable via LAN_NETWORK) so queries stay
#                      private while book-source domains still resolve. When this
#                      is a LAN resolver, the DNS query leaves over the LAN and
#                      the resolver's own upstream encryption applies; the actual
#                      download still egresses through the tunnel.
#   WIREGUARD_DISABLE_IPV6  when true (default), strip IPv6 Address/AllowedIPs/DNS
#                      from the config before wg-quick. Containers frequently lack
#                      the ip6tables 'raw' table wg-quick needs, and IPv6 egress
#                      would be an additional leak surface. Set false only if the
#                      host exposes ip6tables and you explicitly want IPv6.

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
WIREGUARD_DISABLE_IPV6_VALUE="${WIREGUARD_DISABLE_IPV6:-true}"

echo "Build version: $BUILD_VERSION"
echo "Release version: $RELEASE_VERSION"

if [ ! -f "$WIREGUARD_CONFIG" ]; then
    echo "[✗] WireGuard config not found at $WIREGUARD_CONFIG"
    echo "    Mount your wg-quick config there (e.g. -v /host/wg0.conf:/config/wg0.conf:ro)"
    exit 1
fi

# wg-quick derives the interface name from the config file's basename, so stage
# the config as /etc/wireguard/<interface>.conf regardless of its mounted name.
RUNTIME_CONFIG="/etc/wireguard/${WIREGUARD_INTERFACE}.conf"
mkdir -p /etc/wireguard
cp "$WIREGUARD_CONFIG" "$RUNTIME_CONFIG"
chmod 600 "$RUNTIME_CONFIG"

# Extract the DNS line (if any) before wg-quick, so we can enforce it ourselves.
WG_DNS="$(grep -iE '^\s*DNS\s*=' "$RUNTIME_CONFIG" | head -n1 | cut -d'=' -f2- | tr ',' ' ' | xargs || true)"

# wg-quick will try to manage DNS via resolvconf which is not present in this
# image; strip the DNS line and enforce it ourselves below to avoid wg-quick
# aborting. Keep a copy for reference.
sed -i -E '/^\s*DNS\s*=/d' "$RUNTIME_CONFIG"

# Strip IPv6 to avoid wg-quick failing on the ip6tables 'raw' table that many
# container kernels don't expose, and to eliminate IPv6 as a leak path. This
# removes IPv6 CIDRs from Address= and AllowedIPs= and drops all-IPv6 lines.
if is_truthy "$WIREGUARD_DISABLE_IPV6_VALUE"; then
    echo "[*] Disabling IPv6 in tunnel config (WIREGUARD_DISABLE_IPV6=true)"
    # Remove IPv6 CIDRs (those containing a colon) from comma-separated
    # Address= and AllowedIPs= lines; drop the line entirely if nothing remains.
    awk '
        function trim(s){ sub(/^[ \t]+/,"",s); sub(/[ \t]+$/,"",s); return s }
        /^[ \t]*(Address|AllowedIPs)[ \t]*=/{
            eq=index($0,"="); key=substr($0,1,eq-1); val=substr($0,eq+1)
            n=split(val, parts, ","); out=""; sep=""
            for(i=1;i<=n;i++){ v=trim(parts[i]); if(v!="" && index(v,":")==0){ out=out sep v; sep=", " } }
            if(out==""){ next }
            print trim(key) " = " out; next
        }
        { print }
    ' "$RUNTIME_CONFIG" > "${RUNTIME_CONFIG}.v4" && mv "${RUNTIME_CONFIG}.v4" "$RUNTIME_CONFIG"
    chmod 600 "$RUNTIME_CONFIG"
fi

# Keep only IPv4 nameservers from the captured DNS list when IPv6 is disabled.
if is_truthy "$WIREGUARD_DISABLE_IPV6_VALUE" && [ -n "$WG_DNS" ]; then
    WG_DNS_V4=""
    for ns in $WG_DNS; do
        case "$ns" in
            *:*) : ;;                 # drop IPv6 resolver
            *) WG_DNS_V4="$WG_DNS_V4 $ns" ;;
        esac
    done
    WG_DNS="$(echo "$WG_DNS_V4" | xargs || true)"
fi

echo "[*] Bringing up WireGuard interface '$WIREGUARD_INTERFACE' from $WIREGUARD_CONFIG..."
# wg-quick unconditionally runs `sysctl -q net.ipv4.conf.all.src_valid_mark=1`,
# but in a container /proc/sys is read-only, so that write fails even though the
# value is already 1 (set at namespace creation via the compose `sysctls:` key /
# docker --sysctl). Shim sysctl so that this single redundant write is a no-op
# when the value is already correct; everything else falls through to the real
# binary. This avoids needing --privileged or a writable /proc/sys.
#
# The shim is written to a PERSISTENT path (not a mktemp dir) so the supervised
# healthcheck can reuse it when it bounces the tunnel on a stale handshake;
# otherwise wg-quick would fail again on the same sysctl write and never recover.
SYSCTL_SHIM_DIR="/app/wg-sysctl-shim"
REAL_SYSCTL="$(command -v sysctl || echo /usr/sbin/sysctl)"
mkdir -p "$SYSCTL_SHIM_DIR"
cat > "${SYSCTL_SHIM_DIR}/sysctl" <<SHIM
#!/bin/bash
for arg in "\$@"; do
    case "\$arg" in
        net.ipv4.conf.all.src_valid_mark=1)
            cur="\$(cat /proc/sys/net/ipv4/conf/all/src_valid_mark 2>/dev/null)"
            if [ "\$cur" = "1" ]; then exit 0; fi
            ;;
    esac
done
exec "${REAL_SYSCTL}" "\$@"
SHIM
chmod +x "${SYSCTL_SHIM_DIR}/sysctl"

# Helper: run wg-quick with the sysctl shim on PATH. Used for both the initial
# bring-up and the healthcheck's recovery bounce.
wg_quick_shimmed() {
    PATH="${SYSCTL_SHIM_DIR}:${PATH}" wg-quick "$@"
}

# wg-quick handles: interface creation, address, route for AllowedIPs, and a
# fwmark-based default route when AllowedIPs=0.0.0.0/0.
wg_quick_shimmed up "$WIREGUARD_INTERFACE"

echo "[*] WireGuard interface state:"
wg show "$WIREGUARD_INTERFACE" || true
ip -o addr show "$WIREGUARD_INTERFACE" || true

# ---------------------------------------------------------------------------
# Kill-switch (fail-closed, IPv4 + IPv6)
# ---------------------------------------------------------------------------
# wg-quick (with AllowedIPs=0.0.0.0/0) already installs a fwmark + suppress
# routing that sends everything except the encrypted tunnel packets through
# wg0, and blocks off-tunnel traffic to AllowedIPs. We add an explicit
# filter-table kill-switch as defence in depth: default DROP on OUTPUT, allow
# only loopback, the tunnel device, the LAN ranges, and the handshake to the
# WireGuard endpoint(s).
#
# Endpoints are read from the LIVE interface (`wg show <iface> endpoints`), not
# the config file: after wg-quick is up these are always concrete resolved
# IP:port values, so the allow rule can never fail on a hostname (which would
# otherwise drop the WireGuard encapsulation and break the tunnel). Each
# endpoint is added to iptables or ip6tables depending on its address family.
echo "[*] Installing kill-switch (iptables + ip6tables)..."

# ip6tables may be unusable in some container kernels (missing tables). Detect
# once so we can fail closed on IPv6 when possible and warn otherwise.
IP6TABLES_OK="true"
if ! ip6tables -L OUTPUT >/dev/null 2>&1; then
    IP6TABLES_OK="false"
    echo "[!] ip6tables unavailable in this kernel; disabling IPv6 in the kernel instead so v6 egress cannot leak."
    # Belt-and-braces: if we cannot program an IPv6 kill-switch, drop IPv6
    # entirely at the stack so non-tunnel v6 egress is impossible.
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true

    # Verify IPv6 is actually off. If /proc/sys is read-only (common in
    # containers) the sysctl write silently no-ops and IPv6 could still leak
    # off-tunnel with no kill-switch. In that case fail closed: either the
    # operator disables IPv6 for the container (sysctls/--sysctl or the host),
    # or provides a kernel with a usable ip6tables. Allow an explicit override
    # (WIREGUARD_ALLOW_IPV6_LEAK=true) for operators who have confirmed the
    # container genuinely has no IPv6 connectivity.
    V6_DISABLED="$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo unknown)"
    # If the IPv6 stack is entirely absent, there is nothing to leak.
    if [ ! -e /proc/sys/net/ipv6 ]; then
        echo "[*] No IPv6 stack present in this namespace; nothing to fail closed on."
    elif [ "$V6_DISABLED" != "1" ]; then
        if is_truthy "${WIREGUARD_ALLOW_IPV6_LEAK:-false}"; then
            echo "[!] WARNING: could not disable IPv6 and ip6tables is unavailable; WIREGUARD_ALLOW_IPV6_LEAK=true set, continuing WITHOUT an IPv6 kill-switch (v6 egress may bypass the tunnel)."
        else
            echo "[✗] Cannot enforce an IPv6 kill-switch: ip6tables is unavailable AND IPv6 could not be disabled" >&2
            echo "    (net.ipv6.conf.all.disable_ipv6=$V6_DISABLED; /proc/sys likely read-only)." >&2
            echo "    Refusing to run with a potential IPv6 leak. Fix by either:" >&2
            echo "      - disabling IPv6 for the container (e.g. compose sysctls: net.ipv6.conf.all.disable_ipv6=1," >&2
            echo "        or docker run --sysctl net.ipv6.conf.all.disable_ipv6=1), or" >&2
            echo "      - running on a kernel with a usable ip6tables, or" >&2
            echo "      - setting WIREGUARD_ALLOW_IPV6_LEAK=true if the container has no IPv6 connectivity." >&2
            exit 1
        fi
    else
        echo "[✓] IPv6 disabled at the kernel; no IPv6 leak path."
    fi
fi

# Allow the encrypted WireGuard handshake/data out to each peer endpoint.
#
# We pin the allow rule to the resolved endpoint destination IP *and* UDP port
# (not the port alone). Allowing any UDP to that port would leave an off-tunnel
# egress hole for arbitrary UDP to that destination port during tunnel
# downtime/bounces (when the fwmark routes may be gone) — weakening the
# fail-closed guarantee. Pinning the destination IP closes that hole: the only
# off-NIC traffic this permits is the encrypted WireGuard transport to the peer
# itself. Endpoints are read from the LIVE interface, so they are always
# concrete resolved IPs. If the provider rotates the endpoint IP, the tunnel
# goes stale and the healthcheck bounce re-derives the new live endpoint and
# re-opens the corresponding IP+port rule (see refresh_endpoint_rules), so a
# rotation self-heals on recovery without ever leaving a wildcard-port hole.
# Rules are de-duplicated by IP+port; this function is idempotent and
# re-runnable after a bounce.
apply_endpoint_rules() {
    local endpoints ep ep_port ep_host ep_ip seen_v4=" " seen_v6=" " key
    endpoints="$(wg show "$WIREGUARD_INTERFACE" endpoints 2>/dev/null | awk '{print $2}' | grep -v '^$' || true)"
    for ep in $endpoints; do
        # Split host:port from the right so IPv6 colons in the host are preserved.
        ep_port="${ep##*:}"
        ep_host="${ep%:*}"
        [ -z "$ep_port" ] && continue
        if printf '%s' "$ep_host" | grep -q ':'; then
            # IPv6 endpoint -> ip6tables. Strip the [] brackets for -d.
            ep_ip="${ep_host#[}"; ep_ip="${ep_ip%]}"
            key="${ep_ip}/${ep_port}"
            case "$seen_v6" in *" $key "*) continue ;; esac
            seen_v6="${seen_v6}${key} "
            if [ "$IP6TABLES_OK" = "true" ]; then
                ip6tables -C OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                    || ip6tables -A OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                    || echo "[!] Could not add IPv6 endpoint allow rule for ${ep_ip} udp/$ep_port"
            fi
        else
            # IPv4 endpoint -> iptables.
            ep_ip="$ep_host"
            key="${ep_ip}/${ep_port}"
            case "$seen_v4" in *" $key "*) continue ;; esac
            seen_v4="${seen_v4}${key} "
            iptables -C OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                || iptables -A OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                || echo "[!] Could not add IPv4 endpoint allow rule for ${ep_ip} udp/$ep_port"
        fi
    done
}

# --- IPv4 kill-switch ---
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -o "$WIREGUARD_INTERFACE" -j ACCEPT

# --- IPv6 kill-switch (fail closed) ---
if [ "$IP6TABLES_OK" = "true" ]; then
    ip6tables -F OUTPUT
    ip6tables -A OUTPUT -o lo -j ACCEPT
    ip6tables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
    ip6tables -A OUTPUT -o "$WIREGUARD_INTERFACE" -j ACCEPT
fi

apply_endpoint_rules

# Keep LAN reachable (WebUI, Prowlarr, qBittorrent, DNS on the LAN) off-tunnel.
DEFAULT_LAN="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
LAN_LIST="${LAN_NETWORK:-$DEFAULT_LAN}"
IFS=',' read -ra LAN_CIDRS <<< "$LAN_LIST"
for cidr in "${LAN_CIDRS[@]}"; do
    cidr="$(echo "$cidr" | xargs)"
    [ -z "$cidr" ] && continue
    if printf '%s' "$cidr" | grep -q ':'; then
        [ "$IP6TABLES_OK" = "true" ] && ip6tables -A OUTPUT -d "$cidr" -j ACCEPT 2>/dev/null
    else
        iptables -A OUTPUT -d "$cidr" -j ACCEPT
    fi
    echo "[*] Kill-switch: LAN allowed off-tunnel -> $cidr"
done

# Everything else is dropped: if the tunnel drops, non-LAN egress fails closed.
iptables -A OUTPUT -j DROP
if [ "$IP6TABLES_OK" = "true" ]; then
    ip6tables -A OUTPUT -j DROP
fi
echo "[✓] Kill-switch active (default-drop; egress only via $WIREGUARD_INTERFACE or LAN, IPv4+IPv6)."

# ---------------------------------------------------------------------------
# DNS enforcement (fail-closed): send resolver traffic through the tunnel.
# ---------------------------------------------------------------------------
if is_truthy "$WIREGUARD_ENFORCE_DNS_VALUE"; then
    # Prefer an explicit override; fall back to the tunnel config's DNS.
    DNS_TO_USE="${WIREGUARD_DNS:-$WG_DNS}"
    # Normalise separators (commas -> spaces).
    DNS_TO_USE="$(echo "$DNS_TO_USE" | tr ',' ' ' | xargs || true)"
    if [ -n "$DNS_TO_USE" ]; then
        echo "[*] Enforcing resolver(s): $DNS_TO_USE"
        # Writing /etc/resolv.conf can fail if it is a read-only bind mount.
        # If we cannot pin the resolver, the container would fall back to its
        # inherited resolver (often Docker's 127.0.0.11), which the LAN
        # allowlist permits and which can leak queries off-tunnel. Fail closed.
        if ! { : > /etc/resolv.conf; } 2>/dev/null; then
            echo "[✗] Could not write /etc/resolv.conf (read-only mount?)." >&2
            echo "    Cannot pin the resolver, so DNS could leak off-tunnel via the inherited resolver." >&2
            echo "    Provide a writable /etc/resolv.conf, or set WIREGUARD_ENFORCE_DNS=false only if" >&2
            echo "    you have pinned the resolver another way." >&2
            exit 1
        fi
        for ns in $DNS_TO_USE; do
            echo "nameserver $ns" >> /etc/resolv.conf
        done
    else
        # No resolver to enforce. Leaving the inherited resolver in place would
        # let DNS leak off-tunnel (Docker's 127.0.0.11 is inside the LAN
        # allowlist). Fail closed rather than silently leak.
        echo "[✗] WIREGUARD_ENFORCE_DNS=true but no resolver is defined (set WIREGUARD_DNS, or a DNS= line in the config)." >&2
        echo "    Refusing to run, because the inherited resolver could leak DNS off-tunnel." >&2
        echo "    Either set WIREGUARD_DNS to a resolver reachable via the tunnel (or an allowed LAN" >&2
        echo "    resolver), or explicitly set WIREGUARD_ENFORCE_DNS=false to accept the inherited resolver." >&2
        exit 1
    fi
else
    echo "[*] Leaving /etc/resolv.conf unchanged (WIREGUARD_ENFORCE_DNS=$WIREGUARD_ENFORCE_DNS_VALUE)"
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

WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg0}"
# Max seconds since last handshake before we consider the tunnel stale.
# WireGuard rehandshakes roughly every 2 minutes when there is traffic.
STALE_AFTER="${WIREGUARD_STALE_AFTER:-180}"

# Reuse the persistent sysctl shim the main script wrote. The recovery bounce
# runs `wg-quick up`, which unconditionally writes
# net.ipv4.conf.all.src_valid_mark=1; in a container /proc/sys is read-only so
# that write fails and wg-quick would abort, never recovering. Putting the shim
# ahead on PATH makes that one redundant write a no-op, mirroring the initial
# bring-up.
SYSCTL_SHIM_DIR="/app/wg-sysctl-shim"
if [ -x "${SYSCTL_SHIM_DIR}/sysctl" ]; then
    PATH="${SYSCTL_SHIM_DIR}:${PATH}"
fi

# Detect ip6tables usability independently (this script runs under supervisor in
# its own environment and does not inherit the parent's IP6TABLES_OK).
if ip6tables -L OUTPUT >/dev/null 2>&1; then
    IP6TABLES_OK="true"
else
    IP6TABLES_OK="false"
fi

latest_handshake_epoch() {
    wg show "$WIREGUARD_INTERFACE" latest-handshakes 2>/dev/null \
        | awk '{print $2}' | sort -nr | head -n1
}

# Re-open the WireGuard endpoint(s) in the kill-switch from the LIVE interface.
# The endpoint allow rules are first derived at startup, but a provider IP
# rotation or NAT rebinding can change the peer endpoint later. We pin each rule
# to the resolved endpoint destination IP *and* UDP port (not the port alone):
# a wildcard-port rule would leave an off-tunnel UDP hole to that port during/
# after a bounce while the tunnel isn't fully up. Re-deriving from the live
# interface means a rotated endpoint IP is re-permitted on recovery, while
# everything else stays forced through the tunnel by the default-DROP. Rules are
# guarded with -C so duplicates never stack, and INSERTed ahead of the DROP.
# IPv6 hosts have their [] brackets stripped for -d; IPv6 rules only run when
# ip6tables is usable.
refresh_endpoint_rules() {
    local eps ep ep_host ep_port ep_ip seen_v4=" " seen_v6=" " key
    eps="$(wg show "$WIREGUARD_INTERFACE" endpoints 2>/dev/null | awk '{print $2}' | grep -v '^$' || true)"
    for ep in $eps; do
        ep_port="${ep##*:}"
        ep_host="${ep%:*}"
        [ -z "$ep_port" ] && continue
        if printf '%s' "$ep_host" | grep -q ':'; then
            ep_ip="${ep_host#[}"; ep_ip="${ep_ip%]}"
            key="${ep_ip}/${ep_port}"
            case "$seen_v6" in *" $key "*) continue ;; esac
            seen_v6="${seen_v6}${key} "
            [ "$IP6TABLES_OK" = "true" ] && { ip6tables -C OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                || ip6tables -I OUTPUT 1 -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null || true; }
        else
            ep_ip="$ep_host"
            key="${ep_ip}/${ep_port}"
            case "$seen_v4" in *" $key "*) continue ;; esac
            seen_v4="${seen_v4}${key} "
            iptables -C OUTPUT -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null \
                || iptables -I OUTPUT 1 -d "$ep_ip" -p udp --dport "$ep_port" -j ACCEPT 2>/dev/null || true
        fi
    done
}

FAIL_COUNT=0
# Give the first handshake time to complete before judging health.
sleep 20

while true; do
    # Proactively re-open the current live endpoint IP+port every cycle. If the
    # provider rotates the endpoint IP while the tunnel is up, this adds the new
    # allow rule before the DROP so the next handshake to the new endpoint is
    # not blocked, minimising recovery delay (rather than waiting for a bounce).
    refresh_endpoint_rules

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
        # Bring the tunnel back up via the sysctl shim (read-only /proc/sys).
        # The DROP rule stays in place so we never leak during the bounce.
        wg-quick up "$WIREGUARD_INTERFACE" 2>/dev/null || echo "$(date): wg-quick up failed, will retry"
        # The peer endpoint may have rotated; re-open it so the kill-switch
        # does not strand the reconnect.
        refresh_endpoint_rules
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
