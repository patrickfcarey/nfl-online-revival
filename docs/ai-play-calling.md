# AI play calling: the pool, the favourite, and the missing plan

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #18.

> "End of half and end of game playcalling is contradictory… It generally
> feels like the AI only selects from a much smaller list of plays than
> it has access to."

**Both halves of the report are correct, and they have different causes.**

## The small pool is real, and it is authored

The AI never sees the playbook. The candidate enumerator builds a
three-table SQL join at runtime with **exactly one literal predicate:
`PBAI.AIGR == <group>`**. There is **no formation filter, no personnel
filter, no situational sub-list in code, and no top-N truncation** — the
roulette walks every returned row.

So the pool is precisely *"every row of the playbook's AI table whose
group matches the request"*. Two numbers bound it:

* the candidate buffer holds **225 slots**;
* `PBAI` in the create-a-playbook template holds **175 rows total, across
  every group**. A playbook using ten AI groups therefore averages **under
  18 plays per group**.

The felt "much smaller list" is authored data, not a code filter. Actual
per-group counts for the shipped team playbooks are on the disc and can
only be measured from an ISO.

## But the steep favourite is in code — and it explains the Boise dive

The offence weighting does a **two-pass class renormalisation**: every
candidate is bucketed into one of two families, and the families are
rescaled so that one owns exactly `r` of the total pool weight —
**regardless of how many plays are in it**. `r` comes from a coach
database field (fallback 0.80).

Worked example: an AI group with **one** play of the favoured class and
eight of the other gives that lone play **80% of the roulette**. That is
exactly *"Boise goes to the Strong-I FB weak dive predictably rather than
choosing among the short-yardage plays in the book"* — a thin run slice
in a short-yardage group gets handed the entire run share.

*(The family labels are inferential; the structure — two disjoint
families with the share fixed by a coach field — is solid.)*

A second amplifier: the **matchup-memory term is on a different numeric
scale from everything else.** The other modifiers are fractions of 1;
this one adds *yards*. One remembered 15-yard gain against the same
defensive call multiplies a play's weight by roughly **16×**.

## The AI applies no anti-repetition to itself

Every `ptrk` read in the offence weighting is for the **opposing** side.
The offence measures *the defence's* repetition and *the defence's*
recent success. **No term anywhere reduces a play's weight because the AI
just called it.** The anti-repetition tracker punishes the human for
repeating plays (`play-tendency-ai.md`) and is blind to the AI's own
repetition — a direct answer to the tension noted when that system was
first documented.

## The contradictory clock management: state exists, a plan does not

**A complete game-situation state exists** — a 41-slot variable array
populated every play with **timeouts remaining (both teams), score
differential, quarter, time left in quarter, time left in game, down,
distance, line of scrimmage, current play ids, and who is
CPU-controlled.** Nothing is missing.

**The policy over that state is a bytecode script, not ELF code.** There
is a small VM (opcode = high nibble, comparison operators in a table) whose
script is **loaded as a resource from the disc**, not compiled into the
binary. Its commands include "AI select play from group", "set specific
play", clock/huddle pokes, and a probable "call timeout".

So the hurry-up-then-run-then-timeout incoherence is **independent rules
in a data-authored script firing without a shared intent variable**. The
VM has no notion of a plan; each invocation matches one rule and emits one
action. Two structural amplifiers make incoherence likely:

* the script picks the *group*, but the run/pass balance **inside** that
  group is imposed afterwards by the coach's tendency field, which knows
  nothing about the clock. A script correctly asking for a two-minute
  group can still be handed a run.
* the CPU deliberation clock reads **only the skill level** — nothing in
  the timing path consults the game clock or score. The CPU takes the same
  time to call a play at 0:12 as at 12:00.

## Fix candidates

| # | change | risk |
|---|---|---|
| **F1** | **Kill the steep favourite** — two `nop`s remove the class renormalisation, so run/pass mix follows the playbook author's per-play percentages instead of being forced to the coach's global ratio. **Highest value, no cave** | Low |
| F2 | Move the fallback tendency ratio (the live value is a roster-DB field, so the real lever is a database edit) | Low |
| F3 | Rescale the matchup memory — the −5.0 penalty is one word; damping the yards-scaled reward needs a cave | Med |
| F4 | Let the CPU pinch and spread its line (both are weighted **zero** in the shift tables — the CPU literally never pinches) | Low |
| F5 | Widening the pool is a playbook-database edit — **with a landmine**: the row-fetch loop has **no bound check**, so more than 225 rows in one group overwrites the stack frame. Raise the buffer first | — |
| F6 | **Situational coherence is not patchable in the ELF** — it lives in the script resource on the disc. The engine already supplies every variable a correct rule would need | — |

## Correction to an earlier anchor

`0x001459b4` was recorded in the ledger as the CPU play caller. **It is
not** — that function picks the pre-snap defensive line/linebacker shift.
The real chain runs through the enumerator described above. A related
finding from the same code: the CPU's shift weights make it **never
pinch** and **never spread the line**.

## Open / needs the rig

The matchup-memory term reads the opponent's current play, and the play
selection writes into that same field — so *whichever side calls second
reads the other side's actual call*. Either the offence AI is psychic or
it is reading last snap's call; the write order cannot be settled
statically.
