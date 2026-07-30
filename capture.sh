#!/usr/bin/env bash
# One capture session: DNS responder + packet capture, into timestamped files.
#
#   ./capture.sh <label> <rig-ip> [extra tcpdump filter]
#   ./capture.sh madden2004-ps2 192.168.68.85
#
# Starts tcpdump in the background and the DNS responder in the foreground.
# Boot the game, drive it to its online menu, then press Ctrl-C here: the
# responder prints the deduplicated hostname list, tcpdump is stopped cleanly,
# and the exact classify commands for this run are printed.
#
# Needs packet-capture rights. Either grant them once (preferred, so the whole
# session runs unprivileged):
#     sudo setcap cap_net_raw,cap_net_admin+eip "$(command -v tcpdump)"
# or run this script under sudo.
#
# This script never launches an emulator, so it cannot collide with a VR
# session. Booting the game stays a deliberate, separate act -- run the headset
# check first: pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f "qemu-system-i38[6]"

set -uo pipefail

LABEL="${1:-}"
RIG_IP="${2:-}"
EXTRA_FILTER="${3:-}"

if [ -z "$LABEL" ] || [ -z "$RIG_IP" ]; then
    echo "usage: $0 <label> <rig-ip> [extra tcpdump filter]" >&2
    echo "  e.g. $0 madden2004-ps2 192.168.68.85" >&2
    exit 2
fi

cd "$(dirname "$(readlink -f "$0")")" || exit 1

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "no $PYTHON on PATH" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p captures
PCAP="captures/${LABEL}-${STAMP}.pcap"
DNSLOG="captures/${LABEL}-${STAMP}-dns.log"

# The DNS port is the one thing that may need privilege. Check before starting
# anything, so a failure costs nothing rather than half a session.
DNS_PORT=53
if [ "$(id -u)" -ne 0 ]; then
    if ! "$PYTHON" - <<'PROBE' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.bind(("0.0.0.0", 53))
except OSError:
    sys.exit(1)
finally:
    s.close()
PROBE
    then
        echo "!! Cannot bind UDP/53 as this user, so the console's DNS lookups"
        echo "!! cannot be answered. Fix with ONE of:"
        echo "     sudo setcap cap_net_bind_service=+ep \"\$(readlink -f \$(command -v $PYTHON))\""
        echo "     sudo $0 $LABEL $RIG_IP"
        exit 1
    fi
fi

# `host RIG_IP` alone also catches everything the rig itself does -- browser
# traffic, package updates, the SSH session you are typing in, and the VR
# headset stream. The first real capture was ~99% noise. Excluded by default;
# override with EXCLUDE="" if a title ever turns out to use one of these.
EXCLUDE="${EXCLUDE-not port 22 and not port 9757 and not port 5353 and not port 137 and not port 138}"
FILTER="host $RIG_IP"
[ -n "$EXCLUDE" ] && FILTER="$FILTER and ($EXCLUDE)"
[ -n "$EXTRA_FILTER" ] && FILTER="$FILTER and ($EXTRA_FILTER)"

echo "=============================================================="
echo " capture session: $LABEL"
echo "   pcap     -> $PCAP"
echo "   dns log  -> $DNSLOG"
echo "   filter   -> $FILTER"
echo "=============================================================="

# -s0 = full packets (never truncate a payload we are trying to read).
# -U   = flush per packet, so the file is complete even if we are killed hard.
tcpdump -i any -s0 -U -w "$PCAP" "$FILTER" >/dev/null 2>captures/.tcpdump.err &
TCPDUMP_PID=$!

cleanup() {
    echo
    echo "[capture] stopping tcpdump (pid $TCPDUMP_PID)"
    # -INT is ignored for background jobs; -TERM is what actually arrives.
    kill -TERM "$TCPDUMP_PID" 2>/dev/null
    wait "$TCPDUMP_PID" 2>/dev/null
    echo
    if [ -s "$PCAP" ]; then
        echo "=============================================================="
        echo " next: read what was captured"
        echo "=============================================================="
        echo "  $PYTHON -m recon classify $PCAP"
        echo "  $PYTHON -m recon pcap $PCAP --max 40"
        echo
        echo " hostnames:  $DNSLOG"
        echo " record findings in docs/protocol-notes.md"
    else
        echo "!! $PCAP is empty. tcpdump said:"
        sed 's/^/     /' captures/.tcpdump.err
        echo "!! Check that the emulator's NIC is enabled and that $RIG_IP is right."
    fi
}
trap cleanup EXIT

sleep 1
if ! kill -0 "$TCPDUMP_PID" 2>/dev/null; then
    echo "!! tcpdump failed to start:" >&2
    sed 's/^/     /' captures/.tcpdump.err >&2
    echo "!! Grant capture rights: sudo setcap cap_net_raw,cap_net_admin+eip \"\$(command -v tcpdump)\"" >&2
    exit 1
fi
echo "[capture] tcpdump running (pid $TCPDUMP_PID)"
echo
echo ">>> Now boot the game and go to its online menu."
echo ">>> Press Ctrl-C here when done."
echo

# Foreground: its Ctrl-C ends the session and prints the hostname summary.
"$PYTHON" -m recon dns --ip "$RIG_IP" --port "$DNS_PORT" --out "$DNSLOG"
