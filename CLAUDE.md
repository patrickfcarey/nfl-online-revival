# Project rules for agents working in this repo

These are non-negotiable and exist because breaking them has cost real work.

## 1. Scope-test every new requirement against the blast radius

A requirement that arrives mid-design does **not** join the change in flight
by default. Ask: *which code path does this behaviour live in?* If it is not
the path the current change modifies, capture it as a separate item with its
own acceptance test, mark it explicitly unscoped, and say why.

**Blast radius is about code paths, not topics.** A requirement can be
obviously true, come from the domain expert, and be about the same subject as
the change, and still belong to different code. If you cannot tell which path
it lives in, that is an open question that gates the design — not an
assumption to build on.

See `docs/lessons-learned.md` ("Scope-test every new requirement").

## 2. Test each patch individually, then integrate

Every game-code patch is verified alone before being combined with any other.
It passes only if it (a) moves its own acceptance metric and (b) leaves the
regression surface unchanged — including every "must not break" case, each on
its own savestate. Only then are patches combined and run against the full
acceptance suite plus `tests/test_madden_lab_*.py`.

A monolithic routine that can only be tested all-at-once violates this: keep
patches separable, or give the cave per-requirement toggles.

## 3. Requirements before patches, with acceptance tests

No game-code patch is written until its requirement exists in writing with a
**measurable acceptance test** (a harness metric and a threshold). A patch
ships when its test passes, not when a number looks better.

## 4. Verify claims against the binary or live memory, never from memory of prior work

Every wrong answer this project has produced came from an address that had
drifted, a negative that was never proven, or a plausible-looking listing.
Re-derive before relying. Treat "nothing writes this" / "this is unused"
claims as unproven until re-checked with cross-function base tracking and a
wide `lui` pairing window — both were silently wrong before
(`docs/fact-check-2026-08.md`).

## 5. Never attribute commits to Claude

No `Co-Authored-By` trailer, and set both identity fields on every commit.
