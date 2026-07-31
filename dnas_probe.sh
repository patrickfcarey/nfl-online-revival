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
# Ports 53 and 443 are both privileged; one capability grant covers both:
#     sudo setcap cap_net_bind_service=+ep "$(readlink -f $(command -v python3))"
# or run this under sudo. Prefer the capability: run unprivileged and any
# stray process is yours to kill without sudo.
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

# Both ports are checked by actually binding them. Reading `ss` output and
# matching on the port number cannot tell a conflicting listener from a
# harmless one: systemd-resolved sits on 127.0.0.53:53 and 127.0.0.54:53 on
# every modern Ubuntu, and neither prevents binding 0.0.0.0:53. A name-based
# check refuses to start for no reason, which is exactly what it did.
# ss is still used -- but only to explain a failure the bind already proved.
for spec in "udp:53" "tcp:443"; do
    proto="${spec%%:*}"; port="${spec##*:}"
    reason=$("$PYTHON" - "$proto" "$port" <<'BINDTEST'
import errno, socket, sys
proto, port = sys.argv[1], int(sys.argv[2])
kind = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
sock = socket.socket(socket.AF_INET, kind)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError as exc:
    print("EACCES" if exc.errno == errno.EACCES else
          "EADDRINUSE" if exc.errno == errno.EADDRINUSE else str(exc))
    sys.exit(1)
finally:
    sock.close()
BINDTEST
)
    if [ -n "$reason" ]; then
        echo "!! cannot bind ${proto}/${port}: $reason"
        case "$reason" in
            EACCES)
                echo "   Ports below 1024 need privilege. Grant it once:"
                echo "     sudo setcap cap_net_bind_service=+ep \"\$(readlink -f \$(command -v $PYTHON))\""
                echo "   or run: sudo $0 $RIG_IP $LABEL"
                ;;
            EADDRINUSE)
                echo "   Something already holds it:"
                ss -H "-${proto:0:1}lpn" 2>/dev/null \
                    | grep -E "(0\.0\.0\.0|\*):${port} " | sed 's/^/     /'
                echo "   Usually a previous run of this script. Find and clear it:"
                echo "     pgrep -af 'recon (dns|tls)'"
                ;;
        esac
        exit 1
    fi
done

echo "=============================================================="
echo " DNAS probe: $LABEL"
echo "   dns log  -> $DNSLOG"
echo "   tls log  -> $TLSLOG"
echo "   tls json -> $TLSJSON"
echo "=============================================================="

# TLS sinkhole in the background; its output is tailed so handshake events
# interleave with the live DNS log.
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
    # NOT -INT: a shell starts background jobs with SIGINT ignored, so that
    # signal is dropped and the listener survives to squat the port next time.
    kill -TERM "$TLS_PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$TLS_PID" 2>/dev/null || break
        sleep 0.3
    done
    kill -KILL "$TLS_PID" 2>/dev/null   # last resort; the port must come free
    kill "$TAIL_PID" 2>/dev/null
    wait "$TLS_PID" 2>/dev/null
    echo
    echo "=============================================================="
    echo " TLS VERDICT"
    echo "=============================================================="
    sed -n '/TLS SINKHOLE RESULT/,$p' "$TLSLOG" 2>/dev/null \
        || echo " (nothing recorded -- see $TLSLOG)"
    if ! grep -q "connected on" "$TLSLOG" 2>/dev/null; then
        echo
        echo " No connection arrived. Read the DNS log first: the title has to"
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

# DNS responder in the foreground: its Ctrl-C ends the session, and its live
# log is the immediate proof the console is talking to us at all.
"$PYTHON" -m recon dns --ip "$RIG_IP" --port 53 --out "$DNSLOG"
