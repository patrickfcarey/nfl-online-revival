# Partial findings rescued from the three animation lanes (2026-08-11)

All three agents were terminated mid-run by an ACCOUNT session limit (resets
2:20am America/Chicago), not by any error in their work. Their last progress
messages carried real findings that would otherwise be lost with the
notifications. Recorded verbatim, UNVERIFIED by me, as pointers for the resume.

## Lane 1 (dispatcher) — the highest-value fragment

> "The odd-frame selector rolls on the +0x41C margin too and pushes id **168**
> (yes-set) with facing >85 deg vs <=85 deg sub-cases. Need the rest of it."

Why this matters: **the animation selector reads the contest margin.** +0x41C
is comp3, the late-phase contest component (pass-vs-run-blocking.md), which is
computed from ratings AND is one of the three fields the block sliders scale.
So the bridge the operator's weight+STR law needs ALREADY EXISTS -- the
selector consumes contest output. The law does not have to invent a path to
the animation system; it has to change what the contest says.

Also: id 168 is in the yes-set {146-151,168-170,173}, and it is SELECTED on a
margin roll with facing sub-cases at 85 degrees. That is the first evidence of
a margin-driven clip choice, i.e. the engine already picks different
animations by who is winning.

## Lane 3 (clip inventory)

> "The instance records (0x7C) are copies of staged 124-byte records --
> consistent. Let me eyeball the group's other three sections for the
> per-sequence rows (where displacement specs would live)."

Animation instances are 124-byte (0x7C) records copied from staged records;
the agent was about to look for per-sequence rows carrying displacement specs
-- i.e. root motion data. That is exactly the structure Route C needs decoded.

## Lane 2 (mass law)

> "Now the cave re-census and the lock-in prologue/epilogue for register
> availability."

It had finished the derivation phase and was costing out the patch site.

## Resume instructions

Send each agent its original brief plus: "you were cut off by an account rate
limit, not an error; resume from where your last message left off." Their
context is intact. Do not restart them from scratch.
