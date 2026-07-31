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
TRANSCRIPT="${TRANSCRIPT:-captures/madden-$(date +%Y%m%d-%H%M%S).jsonl}"

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

echo "backend: db=$DB  advertise=$HOST  buddy=$BUDDY_PORT"
echo "transcript: $TRANSCRIPT"
exec python3 -m backend \
    --advertise-host "$HOST" \
    --db "$DB" \
    --buddy-port "$BUDDY_PORT" \
    --transcript "$TRANSCRIPT" \
    "${roster_args[@]}" \
    "$@"
