#!/usr/bin/env bash
# Game-server probe: DNS responder + connection sinkhole + packet capture.
#
#   ./game_probe.sh <rig-ip> <label> [tcp-ports] [udp-ports]
#   ./game_probe.sh 192.168.68.85 madden-ea 10000
#
# For use once a title is past its platform auth and is dialling its own game
# service. The sinkhole accepts the connection and hexdumps what the client
# sends -- which is the protocol we are here to reconstruct.
#
# tcpdump runs alongside so a port we did not think to sinkhole is still seen;
# the sinkhole gives payloads, the capture gives coverage.
#
# Ports above 1024 need no privilege. Port 53 does, and one grant covers it:
#     sudo setcap cap_net_bind_service=+ep "$(readlink -f $(command -v python3))"
#
# Launches no emulator. Headset check first:
#   pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f "qemu-system-i38[6]"

set -uo pipefail

RIG_IP="${1:-}"
LABEL="${2:-game}"
TCP_PORTS="${3:-10000}"
UDP_PORTS="${4:-}"

if [ -z "$RIG_IP" ]; then
    echo "usage: $0 <rig-ip> <label> [tcp-ports] [udp-ports]" >&2
    echo "  e.g. $0 192.168.68.85 madden-ea 10000" >&2
    exit 2
fi

cd "$(dirname "$(readlink -f "$0")")" || exit 1
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "no $PYTHON on PATH" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p captures
SINKLOG="captures/${LABEL}-${STAMP}-sink.log"
SINKJSON="captures/${LABEL}-${STAMP}-sink.jsonl"
DNSLOG="captures/${LABEL}-${STAMP}-dns.log"
PCAP="captures/${LABEL}-${STAMP}.pcap"

# Availability is decided by binding, never by reading `ss` output: a
# loopback-only listener such as systemd-resolved on 127.0.0.53:53 does not
# prevent binding 0.0.0.0:53, and a name-based check refuses to start for it.
check_port() {
    local proto="$1" port="$2"
    "$PYTHON" - "$proto" "$port" <<'BINDTEST'
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
}

for spec in "udp:53" $(printf 'tcp:%s\n' ${TCP_PORTS//,/ }); do
    proto="${spec%%:*}"; port="${spec##*:}"
    reason=$(check_port "$proto" "$port")
    if [ -n "$reason" ]; then
        echo "!! cannot bind ${proto}/${port}: $reason"
        [ "$reason" = "EADDRINUSE" ] && {
            ss -H "-${proto:0:1}lpn" 2>/dev/null \
                | grep -E "(0\.0\.0\.0|\*):${port} " | sed 's/^/     /'
            echo "   Usually a previous run: pgrep -af 'recon (dns|sink|tls)'"
        }
        [ "$reason" = "EACCES" ] && \
            echo "   sudo setcap cap_net_bind_service=+ep \"\$(readlink -f \$(command -v $PYTHON))\""
        exit 1
    fi
done

echo "=============================================================="
echo " game probe: $LABEL"
echo "   tcp ports -> $TCP_PORTS${UDP_PORTS:+   udp ports -> $UDP_PORTS}"
echo "   sink log  -> $SINKLOG"
echo "   sink json -> $SINKJSON"
echo "   dns log   -> $DNSLOG"
echo "   pcap      -> $PCAP"
echo "=============================================================="

# Capture everything to/from the rig except the noise: SSH, the VR headset
# stream, mDNS and NetBIOS. Without this the file is ~99% not-the-game.
EXCLUDE="${EXCLUDE-not port 22 and not port 9757 and not port 5353 and not port 137 and not port 138}"
rm -f captures/.tcpdump.err
tcpdump -i any -s0 -U -w "$PCAP" "host $RIG_IP and ($EXCLUDE)" \
    >/dev/null 2>captures/.tcpdump.err &
TCPDUMP_PID=$!
sleep 1
if ! kill -0 "$TCPDUMP_PID" 2>/dev/null; then
    echo "!! tcpdump did not start; continuing with the sinkhole only."
    sed 's/^/     /' captures/.tcpdump.err 2>/dev/null
    echo "   For packet capture: sudo setcap cap_net_raw,cap_net_admin+eip \"\$(command -v tcpdump)\""
fi

SINK_ARGS=(--tcp "$TCP_PORTS" --out "$SINKJSON")
[ -n "$UDP_PORTS" ] && SINK_ARGS+=(--udp "$UDP_PORTS")
"$PYTHON" -u -m recon sink "${SINK_ARGS[@]}" > "$SINKLOG" 2>&1 &
SINK_PID=$!
sleep 1
if ! kill -0 "$SINK_PID" 2>/dev/null; then
    echo "!! the sinkhole failed to start:" >&2
    sed 's/^/     /' "$SINKLOG" >&2
    kill -TERM "$TCPDUMP_PID" 2>/dev/null
    exit 1
fi
tail -f "$SINKLOG" & TAIL_PID=$!

cleanup() {
    echo
    echo "[probe] stopping"
    # -TERM, not -INT: a shell starts background jobs with SIGINT ignored.
    kill -TERM "$SINK_PID" "$TCPDUMP_PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$SINK_PID" 2>/dev/null || break
        sleep 0.3
    done
    kill -KILL "$SINK_PID" 2>/dev/null
    kill "$TAIL_PID" 2>/dev/null
    wait "$SINK_PID" "$TCPDUMP_PID" 2>/dev/null
    echo
    echo "=============================================================="
    echo " WHAT THE CLIENT SENT"
    echo "=============================================================="
    # Count from the transcript, not the console log: the console renders
    # direction as an arrow, so grepping it for "recv" always found nothing.
    if [ -s "$SINKJSON" ]; then
        echo "  messages from the client: $(grep -c '"dir": "recv"' "$SINKJSON" 2>/dev/null || echo 0)"
        echo
        "$PYTHON" -m recon classify --transcript "$SINKJSON" 2>/dev/null \
            || echo "  (classify found nothing yet)"
    else
        echo "  Nothing arrived on the sinkholed ports."
        echo "  Check the pcap for a port we did not cover:"
        echo "    $PYTHON -m recon classify $PCAP"
    fi
    echo
    echo " logs: $SINKLOG / $DNSLOG / $PCAP"
}
trap cleanup EXIT

echo "[probe] sinkhole up (pid $SINK_PID), tcpdump (pid $TCPDUMP_PID)"
echo
echo ">>> Boot the game and go to online."
echo ">>> Press Ctrl-C here when it connects or errors."
echo

"$PYTHON" -m recon dns --ip "$RIG_IP" --port 53 --out "$DNSLOG"
