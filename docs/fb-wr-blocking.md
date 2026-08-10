# Why the fullback and the slot receiver don't block anybody

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), closing
open question #15. Corrects `lead-blocker-targeting.md` and extends
`block-cycle.md`.

> "The FB runs past the guy he's supposed to kick out, and a slot WR
> never seals the corner."

**Both complaints are real, they have different causes, and neither is
what we guessed.** The dominant one is not about the blocker at all:
**a cornerback in coverage is not an eligible block target.**

## How a blocking assignment is actually installed

The assignment is authored as a **class byte**, and the engine resolves
the class to a chain of states.

| step | address | what |
|---|---|---|
| per-player play record | `0x00242848(play, playerIdx, 0)` | `record+11` = assignment class |
| class → state chain | `0x00243c98` = `blob + 40·class + 63` | blob from `0x00248360(side)` |
| installer | `0x00243980` | walks 4-byte `{id,p1,p2,p3}` records; bit 7 of byte 0 = "more follow" |
| chain object | `*(player+0x2FC)` | `[0]` current state, `[1..3]` params, `[4]` next state |

**State 72 is the authored "block" class, and it is a runtime
dispatcher** (`0x001b67c8`): a geometry test picks state **47** (lead) if
nobody is on you; otherwise play type plus a live "the play turned into a
run" global bit picks **31** (pass pro) or **33** (run block). That is
the decider `pass-vs-run-blocking.md` was missing for its "31↔33 convert
live" observation.

Two other authored blocking classes exist — states 25 and 26 — and both
reference a **teammate** (`GetPlayer(self.side, record.p1)`), never a
defender.

**There is no position gate anywhere in this path.** The only position
read in the installer (`0x002439c4`) toggles the assignment source for
positions 17/18 (FS/SS) only, through a *conditional* `movz`.

## There is no authored block target — closed-set negative

A field census of every read of `*(player+0x2FC)` inside states
25/26/31/33/47/72, cross-checked against all 45 `SetTarget 0x001f7398`
call sites:

* state 31/33 read `record+1` → a **hold timer**, and `record+2` → a
  single pass-pro **flag bit**;
* state 47 reads `record+2` → a **bearing**, or `record+1/+3` → an
  **x/y landmark**;
* states 25/26 read `record+1` → a **teammate index**.

Every player reference the play data can supply is fetched with the
blocker's **own** side byte. **No play in the game says "block the man
over #2".** Targets are engine-assigned, every frame.

## Where targets do get assigned — and the two gates that break it

The engagement manager `0x001f7298` calls **`0x001f5590`** as its
assignment stage: build a blocker list, build a defender list, score
every pair in `0x001f4790`, take the max, resolve conflicts, apply via
`SetTarget`. (The "choosers" `0x001f00d8`/`0x001f06a0` that
`block-cycle.md` documented only *consummate* an assignment already
made — they are phase 2, not phase 1.)

**This corrects `lead-blocker-targeting.md`'s "selection is
proximity-only, no threat ranking".** There is a threat ranking; it is
just extremely restrictive.

### Gate A — the lead-blocking FB is excluded outright

The blocker list `0x001f2ea0` admits **block modes 1 and 2 only**
(`0x001f2efc`). State 47's enter is the **only** writer of mode 3
(`0x001b6780`) in the whole image.

> **A lead-blocking fullback is in state 47 ⇒ mode 3 ⇒ never entered
> into the assignment system at all.** His only targeting is state 47's
> own cone-lean. *That is the FB half of the complaint.*

### Gate B — a corner in coverage is not in the defender list

`0x001f2cd8` filters defenders by state. During a live play the pool is
exactly: **state 2 (pursuit), 30 (rush/engaged), 51 (authored wait), or
human-controlled.**

Per this project's own `press-and-routes.md` and `zone-bunching.md`, a
corner in man coverage is **state 22** and in zone is **37/38/40**.
**None of those is in the accepted set.** No blocker — WR, FB or
lineman — can be assigned to a defender who is covering somebody.

> ⚠ Delay-slot trap at `0x001f2d0c–18`: `daddu s6, v0, zero` sits in the
> delay slot of `jal 0x002605b0`, so `s6` holds the return of the
> *previous* call `0x0015ada0` (play phase). Read the other way, this
> whole finding inverts.

### Gate C — and even in the pre-snap window, his threat score is pinned

Phase 1 skips any defender whose threat score is ≤ **5.0**. The
classifier `0x001f2830` routes coverage states 22/37/38/39/40/41 to a
restrictive arm that, past **20 frames**, requires one specific authored
play type (`blob[20] == 5`) and otherwise returns **not a threat** —
score pinned to exactly 5.0, below the gate.

Two useful by-products: six state ids (49/50/62/73/89/92) return
"never a threat" in *both* this table and the list filter, which
cross-validates the field at `+0xBCC` as a state id; and defenders
already at engagement kind 4/5/6 return 0 — **a real dedup**, another
correction to "no dedup".

### Gate D — the WR is the only skill position with no pairing logic

The pair scorer `0x001f31d0` is proximity-dominant (`88 − distance`)
with modifiers, then dispatches on blocker position through a 24-entry
table at `0x00583860`. HB/FB, TE/tackles, guards and centre each get a
bespoke arm. **The WR's arm is byte-identical to the out-of-range
default** — he is the only offensive position with no logic of his own.
And the HB/FB arm is **pass-protection-only**, so on a run play a
fullback in state 33 is scored exactly like the receiver.

## The answer to "why doesn't the slot WR seal the corner?"

Of the four candidates we listed, the honest scoring is:

* *never given a block mode* — possible, unprovable here (it is play-file
  data, still unread). **No engine-side position filter excludes a WR**
  from block mode, the blocker list, or the sweep passes.
* *proximity picks a nearer man* — partly true (Gate D).
* *an eligibility filter excludes him* — true of the **FB**, not the WR.
* *his state has no target concept* — true of the approach.

**The dominant cause is a fifth one: the corner is not an eligible
target, on two independent gates (B and C).** He becomes blockable only
once he leaves coverage for pursuit, or if a human drives him. "Especially
in the slot" follows directly — the slot defender is the one the WR most
obviously *should* seal, and with him removed from the pool the next-best
candidate by `88 − distance` is a safety several yards away, which reads
on screen as the receiver wandering.

## The #5 fix set does not cover this

Same pipeline, different defect — and two of the three #5 fixes are
sited on the wrong function.

| #5 fix | verdict |
|---|---|
| **B** (AWR gate at `0x1ef820`) | **Wrong site.** That function promotes kinds; it never sets a target. B belongs on the return of `0x001f4290`. |
| **C** (on-route selection window) | Concept transfers, but into the existing scorer, not a new nearest-player loop. |
| **D** (dedup before `SetTarget kind=5`) | **Already implemented twice** — phase 2 of `0x001f4790`, and the kind-4/5/6 reject at `0x001f2860`. **Retire D.** |
| A / A2 / A3 | Genuinely shared — they act on state 47, which the FB does run. |

## Fix candidates

**Data — one word each, no cave** (file offset = vaddr − 0xFF000):

| # | change | site |
|---|---|---|
| **W1** | Give WRs a real pairing arm: repoint the WR slot of the position table from the generic default `0x001F3848` to the TE/tackle arm `0x001F3518` | `0x00583868` |
| **W2** | Give FBs a run-play arm (currently shares the HB pass-pro-only arm) | `0x00583864` |

**Code — one word each, in place:**

| # | change | site | risk |
|---|---|---|---|
| **C1** *(highest value)* | Put coverage defenders back in the pool: make the state filter unconditional — `beq s6,zero` → `beq zero,zero` (`0x1000000f`) | `0x001F2D60` | Med — pair with C2 |
| C2 | Stop coverage defenders being de-threatened past 20 frames: nop the `blob[20]==5` reject, or widen the window | `0x001F2A60` / `0x001F2A18` | Med |
| **C3** | Let lead blockers into the assignment system: `sltiu v0,v1,3` → `4` | `0x001F2F00` | **High** — state 47 has its own steering; test with #5's A/A2 |
| C4 | Lateral pairing cutoff `13.333` | `0x005FF180` | Low |
| C5 | Widen the FB/HB arm's pass-only gate | `0x001F3788` | Med |

**Inert, do not bother:** the DB threat floor `6.0` at `0x001F2C9C` and
its 75-frame window. `defRec+100` is read *only* as the `> 5.0` gate, so
raising 6.0 buys nothing.

Cave hook if C1b (the narrow version — accept 22/37–41 as well as 51) is
preferred: cave #1 `0x00139A68`, 456 bytes, the only sizeable cave in the
gameplay band.

## Hazards

1. **Delay-slot mis-attribution at `0x001f2d18` is load-bearing** (above).
2. **Branch-likely at `0x001b67dc`** selects state 47 vs 31/33. Also
   `0x001f48d4` (the 5.0 gate), `0x001f4a34`, `0x001f2a84`.
3. **`movz` at `0x002439dc`** — the FS/SS assignment-source toggle.
4. **Four jump tables hand-decoded**, each a patch surface: `0x00583860`
   (blocker position, 24), `0x00583530` (threat by state, 71),
   `0x00583650` (defender list by `+0xBCC`, 77), `0x005820a0` (state 26
   sub-mode, 6).
5. **`+0xBCC` as a state id is inference**, corroborated by the six-id
   agreement between two independent tables, not proven.
6. `0x001f31d0`, `0x001f2ea0` and `0x001f558c` have **zero `jal` callers
   at the address `fstart` reports.** Do not repeat the "no callers ⇒
   dead" mistake.

## Searched and not found

* Any authored defender target — closed censuses on both sides.
* Any position filter excluding WRs from blocking.
* Any store to `player+0xBCC` with a 16-bit displacement (14 readers,
  written through a computed base).
* **Which class a WR actually receives per play** — lives in the play
  file, still unreadable (`play-data.md`). This is the one unknown that
  separates "never told to block" from "told to block, but the corner is
  not a legal target". Both look identical on screen; the eligibility
  half is proven, the authoring half is not.
* Which play type `blob[20] == 5` is. If it is a narrow pass sub-type,
  coverage defenders are essentially never blockable past frame 20.
* When a coverage corner transitions to pursuit — so we can say he is
  unblockable while covering, but not precisely when he stops.
