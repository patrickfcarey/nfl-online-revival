#!/usr/bin/env bash
# Copy the harness to the machine running the emulator, stamping its revision.
#
# The rig is not a checkout -- the repo lives on the dev box and the harness is
# copied over -- so `git rev-parse` there returns nothing and every measured row
# would be recorded as "unknown". A results file that cannot say which revision
# produced it is the exact failure the runner's provenance exists to prevent, so
# the stamp is written here rather than left to whoever remembers.
#
# Usage: tools/deploy_lab.sh [user@host] [remote-dir]
set -euo pipefail

TARGET="${1:-}"
REMOTE_DIR="${2:-m4probe}"

if [ -z "$TARGET" ]; then
    # Machine-local, deliberately untracked: these repos are public.
    if [ -f .env.local ]; then
        # shellcheck disable=SC1091
        . ./.env.local
        TARGET="${RIG_SSH:-}"
    fi
fi
if [ -z "$TARGET" ]; then
    echo "usage: $0 user@host [remote-dir]" >&2
    echo "   or set RIG_SSH in .env.local (untracked -- never commit an address)" >&2
    exit 2
fi

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"

SHA="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain)" ]; then
    SHA="${SHA}-dirty"
    echo "warning: tree is dirty; rows will be stamped ${SHA}" >&2
fi

STAMP="tools/madden_lab/REVISION"
printf '%s-deployed\n' "$SHA" > "$STAMP"
trap 'rm -f "$STAMP"' EXIT      # never leave it in the working tree

echo "deploying ${SHA} to ${TARGET}:${REMOTE_DIR}"
ssh "$TARGET" "mkdir -p ${REMOTE_DIR}/tools/madden_lab"
scp -q tools/__init__.py tools/pine.py "${TARGET}:${REMOTE_DIR}/tools/"
scp -q tools/madden_lab/*.py tools/madden_lab/*.yaml "$STAMP" \
    "${TARGET}:${REMOTE_DIR}/tools/madden_lab/"

echo -n "verifying: "
ssh "$TARGET" "cd ${REMOTE_DIR} && python3 -c \
    'from tools.madden_lab.results import git_revision; print(git_revision())'"
