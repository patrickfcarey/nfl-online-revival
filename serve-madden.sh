#!/usr/bin/env bash
# Start the Madden NFL 2004 backend with the full known-good set of flags.
#
# This exists because the invocation has four flags that all matter and none of
# which announce themselves when missing:
#
#   --db            the default is backend.db, a *different, empty* database.
#                   Omit this and every existing account vanishes: the client
#                   sends a correct name and password, the server finds no such
#                   account, answers `auth` with status `miss`, and the console
#                   reports a connection error rather than a login failure.
#   --port          defaults to 10000,10001 and needs both. The client makes a
#                   brief `@dir` contact on 10000, is redirected, and runs the
#                   session on 10001. Narrowing it to 10001 gets the first
#                   connection refused; the server logs nothing at all and the
#                   emulator says `Closed Dead TCP Connection to 10000`.
#   --buddy-port    the presence stub. The client is told about it in `news`.
#   --advertise-host  must be a dotted quad: the client parses it octet by
#                   octet, so a hostname will not do.
#
# Every one of those failures looks the same from the console -- "an error
# happened when connecting to the server" -- which is why they are worth
# encoding once rather than retyping.

set -euo pipefail
cd "$(dirname "$0")"

# Machine-local settings; see .env.local.example. Never committed.
if [ -f .env.local ]; then
    # shellcheck disable=SC1091
    . ./.env.local
fi

HOST="${ADVERTISE_HOST:-${RIG_IP:-}}"
if [ -z "$HOST" ]; then
    echo "error: set RIG_IP (or ADVERTISE_HOST) in .env.local, or pass it as \$1" >&2
    echo "       it is the rig's LAN address, as a dotted quad." >&2
    [ $# -ge 1 ] && HOST="$1" && shift || exit 2
fi

DB="${BACKEND_DB:-madden.db}"
BUDDY_PORT="${BUDDY_PORT:-10002}"
ROSTER_DB="${ROSTER_DB:-extract/madden_data/DB_TEAMS.DAT}"
# A league database to SERVE as an update, if you have one. Its length must
# equal the console's own league file or the client refuses the download.
ROSTER_PAYLOAD="${ROSTER_PAYLOAD:-}"
HTTP_PORT="${HTTP_PORT:-10080}"
TRANSCRIPT="${TRANSCRIPT:-captures/madden-$(date +%Y%m%d-%H%M%S).jsonl}"

# Refuse clearly if an instance is already up. Python reports a taken port as
# "[Errno 98] Address already in use", which reads like a bug in the server
# rather than the obvious "you already have one running" -- and the previous
# instance is easy to lose track of when it was started with nohup.
#
# The bracket in the pattern stops pgrep matching its own command line. That
# self-match has killed a live SSH session on this project four times.
# Who owns the ports, from the lock the server itself takes. This is the
# authority, not pgrep: a `pgrep -f` for the server's command line also matches
# any shell whose own command line happens to contain that text -- including the
# ssh invocations used to drive this rig, which has killed a live session four
# times and produced a false "already running" once.
#
# The kernel drops the flock when the holder dies, so a crashed server cannot
# leave a lock that blocks the next one.
LOCK="${TMPDIR:-/tmp}/nfl-backend-10000-10001.lock"
existing=""
if [ -s "$LOCK" ]; then
    lock_pid=$(sed -n "s/^PID \([0-9]*\):.*/\1/p" "$LOCK" | head -1)
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
        existing="$lock_pid"
    fi
fi

if [ -n "$existing" ]; then
    echo "A backend already owns ports 10000,10001:" >&2
    sed "s/^/    /" "$LOCK" >&2
    echo >&2
    if [ -n "${REPLACE:-}" ]; then
        echo "REPLACE is set -- stopping PID $existing." >&2
        kill "$existing" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$existing" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$existing" 2>/dev/null; then
            kill -9 "$existing" 2>/dev/null || true
            sleep 1
        fi
        if kill -0 "$existing" 2>/dev/null; then
            echo "error: PID $existing will not die; stop it yourself." >&2
            exit 1
        fi
    else
        echo "Stop it first, or re-run with REPLACE=1 to take over:" >&2
        echo "    REPLACE=1 $0 $*" >&2
        exit 1
    fi
fi

if [ ! -f "$DB" ]; then
    echo "note: $DB does not exist yet; a new one will be created and you will"
    echo "      need to make a fresh EA account in-game."
fi

roster_args=()
if [ -f "$ROSTER_DB" ]; then
    roster_args=(--roster-db "$ROSTER_DB")
else
    echo "note: no roster at $ROSTER_DB -- the announced checksum will be a"
    echo "      placeholder, so the console will consider its rosters stale."
fi

# Set SPAR=1 to also stand a test opponent in the quickmatch queue, so a real
# console has someone to be matched against. Implies --pair-any, because a
# console's KIND is a CRC over its own build and settings and nothing else can
# reproduce it.
spar_args=()
if [ -n "${SPAR:-}" ]; then
    spar_args=(--pair-any)
fi

payload_args=()
if [ -n "$ROSTER_PAYLOAD" ] && [ -f "$ROSTER_PAYLOAD" ]; then
    payload_args=(--roster-payload "$ROSTER_PAYLOAD" --http-port "$HTTP_PORT")
fi

echo "backend: db=$DB  advertise=$HOST  buddy=$BUDDY_PORT"
echo "transcript: $TRANSCRIPT"
python3 -m backend \
    --advertise-host "$HOST" \
    --db "$DB" \
    --buddy-port "$BUDDY_PORT" \
    --transcript "$TRANSCRIPT" \
    "${roster_args[@]}" \
    "${payload_args[@]}" \
    "${spar_args[@]}" \
    "$@" &
SERVER_PID=$!

if [ -n "${SPAR:-}" ]; then
    sleep 3
    echo
    echo "standing up a test opponent (see tools/fake_console.py --spar)"
    python3 tools/fake_console.py --host 127.0.0.1 --spar \
        --account sparbot --persona SparBot &
fi
wait "$SERVER_PID"
