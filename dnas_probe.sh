#!/usr/bin/env bash
# The DNAS spike: DNS responder + TLS-terminating sinkhole, together.
#
#   ./dnas_probe.sh <rig-ip> [label]
#   ./dnas_probe.sh 192.168.68.85 madden
#
# Both halves are required and that is the whole point of this script. The TLS
# sinkhole alone captures nothing: without a DNS responder the title cannot
# resolve the auth gateway, so it never opens a connection to terminate. A run
# where every emulog line reads "DNS: Answer Count 0" is this mistake.
#
# Answers the one question that picks the route:
#   handshake accepted -> the client does not validate our certificate, so a
#                         substitute DNAS endpoint can be served.
#   handshake refused  -> the client pins/validates, so patching its own check
#                         is the way in.
#
# Ports 53 and 443 are both privileged; one capability grant covers both:
#     sudo setcap cap_net_bind_service=+ep "$(readlink -f $(command -v python3))"
# or run this under sudo.
#
# Launches no emulator. Run the headset check yourself before booting:
#   pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f "qemu-system-i38[6]"

set -uo pipefail

RIG_IP="${1:-}"
LABEL="${2:-dnas}"

if [ -z "$RIG_IP" ]; then
    echo "usage: $0 <rig-ip> [label]" >&2
    echo "  e.g. $0 192.168.68.85 madden" >&2
    exit 2
fi

cd "$(dirname "$(readlink -f "$0")")" || exit 1
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "no $PYTHON on PATH" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p captures
TLSLOG="captures/${LABEL}-${STAMP}-tls.log"
TLSJSON="captures/${LABEL}-${STAMP}-tls.jsonl"
DNSLOG="captures/${LABEL}-${STAMP}-dns.log"

# Check BOTH privileged ports up front: failing halfway costs a whole session.
if [ "$(id -u)" -ne 0 ]; then
    for p in 53 443; do
        if ! "$PYTHON" - "$p" <<'PROBE' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
for family, kind in ((socket.AF_INET, socket.SOCK_DGRAM),
                     (socket.AF_INET, socket.SOCK_STREAM)):
    s = socket.socket(family, kind)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
    except OSError:
        sys.exit(1)
    finally:
        s.close()
PROBE
        then
            echo "!! Cannot bind port $p as this user. Both 53 (DNS) and 443 (TLS)"
            echo "!! are needed. Fix with ONE of:"
            echo "     sudo setcap cap_net_bind_service=+ep \"\$(readlink -f \$(command -v $PYTHON))\""
            echo "     sudo $0 $RIG_IP $LABEL"
            exit 1
        fi
    done
fi

echo "=============================================================="
echo " DNAS probe: $LABEL"
echo "   dns log  -> $DNSLOG"
echo "   tls log  -> $TLSLOG"
echo "   tls json -> $TLSJSON"
echo "=============================================================="

# TLS sinkhole in the background; its output is tailed so events interleave
# with the DNS log and you can watch the handshake happen.
"$PYTHON" -u -m recon tls --port 443 --out "$TLSJSON" > "$TLSLOG" 2>&1 &
TLS_PID=$!
sleep 1
if ! kill -0 "$TLS_PID" 2>/dev/null; then
    echo "!! the TLS sinkhole failed to start:" >&2
    sed 's/^/     /' "$TLSLOG" >&2
    exit 1
fi
tail -f "$TLSLOG" & TAIL_PID=$!

cleanup() {
    echo
    echo "[probe] stopping the TLS sinkhole"
    kill -INT "$TLS_PID" 2>/dev/null
    # Give it a moment to print its verdict before the tail is cut.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$TLS_PID" 2>/dev/null || break
        sleep 0.3
    done
    kill "$TAIL_PID" 2>/dev/null
    wait "$TLS_PID" 2>/dev/null
    echo
    echo "=============================================================="
    echo " TLS VERDICT"
    echo "=============================================================="
    sed -n '/TLS SINKHOLE RESULT/,$p' "$TLSLOG" 2>/dev/null \
        || echo " (nothing recorded -- see $TLSLOG)"
    if ! grep -q "connection" "$TLSLOG" 2>/dev/null; then
        echo
        echo " If no connections arrived, check the DNS log above: the title must"
        echo " resolve the gateway before it can open a TLS connection at all."
    fi
    echo
    echo " full logs: $TLSLOG  /  $DNSLOG"
}
trap cleanup EXIT

echo "[probe] TLS sinkhole up on 443 (pid $TLS_PID)"
echo
echo ">>> Boot the game and go to online. Watch for 'Authenticating DNAS data'."
echo ">>> Press Ctrl-C here when it finishes or errors."
echo

# DNS responder in the foreground: its Ctrl-C ends the session, and its live log
# is the immediate proof that the console is talking to us at all.
"$PYTHON" -m recon dns --ip "$RIG_IP" --port 53 --out "$DNSLOG"
