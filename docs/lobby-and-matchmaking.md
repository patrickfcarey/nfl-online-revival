# Lobby, chat, matchmaking and the peer link

This is the lobby half of Madden NFL 2004 (PS2, `SLUS-20752`): rooms, chat,
quickmatch and direct challenge, and the `+ses` handoff that starts a game.
The backend side lives in `backend/lobby.py`, `handlers.py`, `matchmaking.py`
and `hub.py`; the wire framing itself (`KEY=VALUE` bodies, the four-character
type/status words, the transport rules every handler must obey) is covered in
`docs/protocol-notes.md` and is assumed here rather than repeated.

**On provenance.** Three different kinds of claim appear below, and they are
labelled every time, because conflating them has cost this project real time
(`docs/method.md` catalogues several cases where it did). *Read from the
client* means the PS2 executable's own disassembly says so -- solid, but
nobody has watched it happen. *Console-observed* means an actual PlayStation
2 running under PCSX2 produced the bytes or log line cited, checked in this
pass against the JSONL captures and `~/.config/PCSX2/logs/emulog.txt` on the
rig (both read read-only for this document; a `pcsx2-qt` session was live on
the rig at the time, so nothing was started, stopped, or written). *Inferred*
means a plausible reading of adjacent evidence that has not been nailed down
either way. Every address below was re-checked against
`madden_SLUS_207.52` for this document using `recon/mipsdis.py`
(vaddr `0x00100000` = file `0x1000`, `gp = 0x006056f0`); where that
re-check turned up more than the source comment it was drawn from, the extra
is included and marked as such.

## How the lobby turns on

Nothing lobby-shaped exists for a client until it asks for it. The client
sends `sele` with a space-separated body (`ROOMS=1 USERS=1 RANKS=1 MESGS=1`,
observed verbatim from a console -- see below), and only once that reply has
gone out does the server have anywhere to push `+rom`/`+usr`/`+pop` *to*: the
client keeps a 20-byte-stride slot array at `conn+784` (slot 0, rooms) and
`conn+804` (slot 1, users), and both `+rom`'s and `+usr`'s parsers check their
slot before doing anything else (`lw v0, 784(s2)` / `beq v0, zero, ...` at
`0x0044968c`/`0x00449690` for rooms, `0x00449a68`/`0x00449a6c` for users) --
read from the client. A push that arrives before the `sele` reply is not
queued for later; it is silently dropped. `backend/handlers.py`'s
`after_subscribe` sends the room list only after that reply has gone out,
for exactly this reason.

## Rooms

### `+rom` and `+usr` are one record per message

Both are ordinary `KEY=VALUE` text, ended by a NUL, exactly like every other
message on this connection -- **not** the concatenated multi-record buffers
used by `news`'s list replies (`docs/protocol-notes.md` covers that other
shape). Each push describes exactly one room or one occupant. The client's
entry points confirm the type word directly: the `+rom` handler compares the
incoming type against the literal built from `lui v0,0x2b72`/`ori
v0,v0,0x6f6d` -- bytes `2b 72 6f 6d`, i.e. `"+rom"` -- at `0x00449680`, and
the `+usr` handler does the same for `"+usr"` at `0x00449a5c`. Read from the
client.

`+rom` is a 104-byte record; `+usr` is 312 bytes. Both figures come straight
from the allocation right before the record is filled in (`addiu a0, zero,
104` / `jal` the 104-byte memset at `0x004496d0`-`0x004496ec` for rooms;
`addiu a0, zero, 312` at `0x00449ab4`, memset size `312` at `0x00449ac8` for
users) -- read from the client.

**Fields, `+rom`.** Every key site below is the exact address of the
`TagFieldFind` call (`jal 0x0044acc8`) for that key, confirmed by reading the
string each call site resolves to, not just inferred from position:

| Key | vaddr | Size / converter | Default |
|---|---|---|---|
| `I` | `0x0044969c` | integer (`0x0044c550`) | **-1** |
| `N` | `0x004496f8` | 32 bytes | -- (absence = deletion) |
| `H` | `0x00449724` | 32 bytes | -- |
| `F` | `0x00449748` | letter bitmask (`0x0044c5d8`) | **-1** |
| `A` | `0x00449768` | dotted quad (`0x0044c628`) | 0 |
| `T` | `0x00449784` | integer (`0x0044c550`) | 0 |
| `L` | `0x004497a4` | integer (`0x0044c550`) | 0 |
| `P` | `0x0044980c` | integer (`0x0044c550`) | **-1** |

`I` (id), `F` (flags), `N` (name), `H` (host name), `T` (occupants), `L`
(limit), `A` (address), `P` (ping). `P`'s three-state default (`-1` when the
key is absent, `0` when it is present and zero, positive otherwise) is why
`lobby.room_record` only ever sends `P` when it has a real value: the client
renders those three cases differently (blank / `---` / `~Nms`).

**Fields, `+usr`.** `I` and `N` were independently re-confirmed the same way;
the remaining sizes (`P` 8 bytes, `S`/`X` 128 bytes each) come from
`docs/backend-data-model.md`'s earlier pass and were not individually
re-walked for this document, so treat those specific widths as read-from-the-
client-but-not-re-verified-here rather than freshly confirmed:

| Key | Size / converter | Default |
|---|---|---|
| `I` | integer | **-1** |
| `F` | letter bitmask (same `0x0044c5d8` as `+rom`) | **0** (`0x00449b50`) |
| `N` | 32 bytes | -- (absence = deletion) |
| `P` | 8 bytes | -- |
| `A` | dotted quad | 0 |
| `R` | integer | 0 |
| `S` | 128 bytes | -- |
| `X` | 128 bytes | -- |
| `T` | integer | 0 |

### Flags: one shared letter bitmask

`F` is not a number the server can pick freely -- it indexes a 256-entry
table at `0x005f56f0`, one 32-bit mask per possible byte value, used
identically by `+rom` `F`, `+usr` `F`, and (see Chat, below) `+msg` `F`. Every
entry was read out of the ELF for this document, not sampled:

| Char | Bit | Char | Bit | Char | Bit |
|---|---|---|---|---|---|
| `@` | 0 | `L` | 12 | `X` | 24 |
| `A` | 1 | `M` | 13 | `Y` | 25 |
| `B` | 2 | `N` | 14 | `Z` | 26 |
| `C`..`K` | 3-11 | `O` | 15 | `0` | 27 |
| | | `P` | **16** | `1` | 28 |
| | | `Q`-`W` | 17-23 | `2` | 29 |
| | | | | `3` | 30 |

Every byte outside `@`, `A`-`Z`/`a`-`z` and `0`-`3` maps to zero. Lowercase
mirrors uppercase exactly (`a` gives the same bit as `A`, through `z`/`Z`),
so the flag letters are case-insensitive in practice even though nothing
about the wire format announces that.

Two letters carry meaning the client acts on: room flag `P` (bit 16) marks a
room password-protected, and user flag `U` (bit 21) marks "this record is
me". The self-flag check is a branch-*likely* at `0x00449d0c`
(`lui v0,0x0020` / `and v0,a0,v0` / `bnel v0,zero,0x00449d64`), and because a
branch-likely's delay slot runs only when the branch is taken, the write it
guards -- `sw a0, 436(s2)`, the client's own self-identity slot -- happens
**only** when bit 21 is set. Miss that and every occupant looks like nobody
in particular to the client's own "which row is me" logic. Chat reuses the
same table: `B` (bit 2) marks a broadcast line and `P` (bit 16, the *same*
bit as room-privacy) marks a private one -- one mechanism, three contexts.

### Deletion and ids

**A record with no `N` is a deletion**, for both types. `+rom` checks for
`N`'s presence *before* allocating the record at all: `TagFieldFind` for `N`
runs at `0x004496c4`, and `beq v0, zero, 0x004498ec` sends a null result
straight to the removal path, skipping the 104-byte allocation entirely.
`+usr` does the equivalent with a branch-likely (`beql v0, zero, 0x00449d24`
at `0x00449aa8`), and the deletion path it jumps to is concrete: `jal
0x0044e570` looks the id up in the user list, and if found, `jal 0x0044e5c8`
removes it. Read from the client, both sites.

**Ids must not be negative**, and it is enforced the same way in both
parsers: `bltz s1, ...` at `0x004496b4` (`+rom`) and `0x00449a90` (`+usr`)
discards the whole record if the parsed id is negative. Since `I`'s default
is also `-1` (the delay slot of the atoi call at each site: `addiu a1, zero,
-1`), a **missing** `I` is discarded by the same check as a negative one.
Zero is a legal id -- it is `+pop`, not `+rom`/`+usr`, that treats 0 as a
terminator (below).

### The occupancy trap in `+usr`'s deletion path

The add and delete code paths for `+usr` converge at `0x00449d68`, and what
happens there is not what the record's own fields would suggest. That
address is a `TagFieldFind` call for `T` (confirmed by string, same
`-30456`-from-`s4` key pointer used for `+rom`'s `T`), read with a default of
0, and its result is handed straight to `jal 0x00447010` along with `a1 =
440(s2)` -- **the receiving client's own current room id**, not any room id
carried by the `+usr` record itself. In other words: whatever `T` says
becomes the occupant count displayed for whatever room the *client reading
this push* currently believes it is in, full stop. A deletion record that
omits `T` -- which is exactly what `lobby.user_removal` sends unless told
otherwise -- applies `T`'s default of 0, so **every departure silently
zeroes the displayed occupancy of the room the reader is sitting in** unless
the real count is sent explicitly. `lobby.user_removal`'s docstring already
flags this; it is repeated here because it is the single easiest way to make
a room's occupant count wrong while every individual push looks correct in
isolation.

### `+pop`: occupancy for many rooms at once

One key, `Z`, holding decimal `id,count` pairs separated by any non-digit,
read into a 512-byte buffer and stopping at a NUL or at an id of exactly
zero (confirmed by walking the digit-accumulation loop at
`0x00449998`-`0x00449a50`: each id is parsed base-10 with `mult v1, a2/s1,
v1(=10)`, and `bne s1, zero, 0x00449998` only continues the outer loop while
the just-parsed id is non-zero). Because 0 terminates the list, room ids
handed to `+pop` must start at 1 -- `lobby.population` enforces this and
raises rather than silently truncating the list.

There is one more piece of behaviour here that is easy to miss reading the
protocol cold: at `0x004499f0`, `beq s1, t0, 0x00449a04` compares the
just-parsed id (`s1`) against `t0 = lw t0, 440(s2)` -- the client's own
current room, the same field used throughout `move` and the `+usr`
convergence above -- and skips writing that pair into wherever the parsed
list is being applied. **The pair whose id equals the client's own room is
silently dropped from a `+pop` update.** Read from the client; no console has
been observed sending anything that would exercise this path from the server
side (a server only ever *sends* `+pop`, never receives it), but the
behaviour matters for choosing what to put in one: including the client's
own room in a `+pop` batch is not wrong, exactly, but it is a no-op for that
one entry, so do not rely on `+pop` to correct the count of the room the
recipient is currently in -- use `+rom` or `+usr`/`T` for that instead.

### `+who`: unsolicited placement

`+who` tells a client which room it is in without it having asked -- the
same effect as a `move` reply, but server-initiated, so a server can place or
relocate a client on its own. Its `N` (persona) and `R` (room name) fields
are read through **64-byte** buffers, not the 32 bytes `+rom`/`+usr` give
their name fields (`0x0044955c`, `0x004495f8`) -- clipping a `+who` name to
31 characters would truncate names the client is otherwise willing to
display in full. `F` and `RF` default to 0 here (not -1, unlike `+rom`'s
`F`), so an empty value is merely tidy rather than load-bearing. As with any
change to the room id a client holds, sending `+who` with a new `RI` makes
the client drain and free its whole user list, so every occupant of the
target room has to be re-pushed via `+usr` afterward or the client will show
an empty room.

### Creating a room: `room`

`room` is a **create**, not a status query -- a subtlety this project got
wrong once (`backend/handlers.py`'s `create_room` docstring records it): a
server that answers `OK` without actually creating anything leaves the
client immediately trying to `move` into a room that does not exist, and (see
below) a failed `move` used to be able to wipe the client's entire lobby
state.

The client has **two** send sites for `room`, and they are meaningfully
different, confirmed by walking both:

- `0x00119cf0` builds `NAME=<generated>` plus `IGNEXIST=1`. The name comes
  from a generator at `0x00119cc8` that writes the literal bytes `0x58`
  (`'X'`) and `0x2e` (`'.'`) before tail-calling into a string-append
  routine -- i.e. every name this path sends is `X.<persona>`, with the
  leading `X` hardcoded, not player-chosen. `IGNEXIST` means "an existing
  room of this name is not an error."
- `0x00356480` -- the path a console actually used -- builds `NAME=<typed
  name>`, `PASS=<typed password>`, a **literal** `DESC=None` (the four
  characters `N`,`o`,`n`,`e`, not an absent field), and a **hardcoded**
  `MAX=50` sent through the integer-field appender rather than the string
  one. **This path sends no `IGNEXIST` at all.**

Console-observed, both fields lists confirmed byte-for-byte against the
strings referenced at each site (`NAME`/`IGNEXIST` at the first; `NAME`,
`PASS`, `DESC`, `MAX` at the second, in that order).

The consequence for a server: because the console's own path never sends
`IGNEXIST`, a server that refuses a same-named room with a `dupl`-style
error means **a console can create its default room exactly once per
server lifetime** -- every later attempt in a later session hits an error
screen instead of joining the room it already has. The fix implemented here
is to treat an existing room of the requested name as success (returning its
id, not an error) unless a password is set and does not match; `store.py`'s
`room` table backs this with `NAME TEXT NOT NULL UNIQUE` and `ensure_room`'s
`INSERT OR IGNORE`, so the create is naturally idempotent by name. Room
names may contain spaces -- `C.NEW ROOM` has been observed verbatim from a
console, id **79**, both on 2026-08-01 and again in a later session the same
day, the *same* id both times because the room persists in the SQLite store
across restarts and the second `room` request simply matched the first by
name. That double-observation is itself evidence the idempotent-create
behaviour works as intended. Room names are free-form because they are typed
on the in-game keyboard, not machine-generated, on the path a console
actually exercises.

One more thing a server needs the room's numeric id for: `store.py`'s own
migration note explains why the table has an `ID` column at all --
*"The client refers to rooms by a numeric id in every message except the
join"* -- so `move`, `+rom`, `+pop` and `+usr` all need a stable integer per
room even though creation and joining both happen by name.

### Joining and leaving: `move`

Rooms are joined and left by `NAME` -- there is no join-by-id message.
Leaving is the same message with an empty `NAME`.

The reply carries two halves. `LIDENT`/`LCOUNT` describe the room being
*left*; `IDENT`/`COUNT`/`NAME` describe the room being *entered* (or, on a
leave, the fact that there now is none). Getting the failure case right here
matters more than it looks, and it is worth walking precisely because a
wrong answer here doesn't produce an error on screen -- it silently empties
the client's lobby.

The client's own reader has two independent gates, and only one of them is
skipped on failure:

- **The status gate**, at `0x0044a2ac` (`bne v0, zero, 0x0044a30c`), skips
  reading `LIDENT`/`LCOUNT` when the reply's status is non-zero.
- **Everything else is gated on the message's *type*, not its status** --
  `0x0044a310` (`bnel a2, v0, 0x0044a40c`, matched against the literal
  `"move"`) is a *branch-likely* keyed only on whether this reply is a
  `move` reply at all. So on a **failed** `move`, the client still reads
  `IDENT` (with a default of 0, confirmed at the atoi call's delay slot,
  `0x0044a32c`: `daddu a1, zero, zero`), still compares it against its
  currently-held room id at `0x0044a35c` (`beq s0, v0, 0x0044a408`), still
  *adopts* whatever it just read as its current room at `0x0044a364` (`sw
  s0, 440(s2)`) if that compare misses, and, if the room actually changed,
  still drains its entire user list in the loop at `0x0044a3d8`.

So a bare failure reply -- status set, body otherwise empty -- is read by
the client as "you are now in room 0 (or whatever `IDENT` defaulted to)",
which is essentially never true, and it wipes the occupant list to match.
`backend/handlers.py`'s `move_room` avoids this by having its `refuse()`
path **echo the room the session currently holds** in both `LIDENT`/`IDENT`
(and the corresponding counts): the compare at `0x0044a35c` then matches,
the client sees no change, and its list survives. The value to echo has a
wrinkle of its own -- the client holds room id **0** before it has ever
joined anything, but **-1** after a leave, because that -1 is exactly what
the leave reply sent it (`session.left_a_room` in the implementation tracks
which of the two applies).

The other consequence of the drain-on-change behaviour: after any
**successful** room change, the client's user list for the new room is
empty until the server re-sends it. `after_move` in `handlers.py` does this
by pushing `+usr` for every current occupant, and it runs strictly *after*
the `move` reply has gone out -- sending it earlier would have the client
apply the pushes against the room id it is about to leave, since it hasn't
processed the reply yet.

## Chat

### `mesg`, the client's outgoing chat

The client's chat message is `mesg`, confirmed at its one send site,
`0x0034e6b0`, which also settles the field names beyond doubt: the body is
written under key `TEXT` at literal string address **`0x00605f80`**, and an
optional private recipient under key `PRIV` at **`0x00605f88`** -- both
addresses read directly off the `lui`/`addiu` pair feeding each
`TagFieldFind`-style field-append call, not inferred from naming. `PRIV` is
genuinely optional: the send site skips appending it entirely when the
caller passed a null or empty-string recipient (`beq s0, zero, ...` /
`lb v0, 0(s0); beq v0, zero, ...`), so a broadcast line simply has no `PRIV`
field at all rather than an empty one. Earlier server code read `USER` and
`BODY` for these two -- keys the client's send site never writes and its
consumer (below) never reads -- which is why that version made no private
message actually private: everything went to the room regardless of intent,
with no error on either side to reveal it.

### `+msg`, the server's push

The consumer side is deliberately narrow. `+msg`'s handler, entered at
`0x00449488` (confirmed against the `"+msg"` type literal), reads **only**
`F` -- via the exact same letter-bitmask converter (`0x0044c5d8`) used for
room and user flags -- and forwards the whole record to a callback stored at
`conn+0x51C` (`= conn+1308`). That slot is populated at boot: `0x004de668`
calls `jal 0x00447770` with `a1 = 1`, and the trampoline at `0x00447770`
computes the storage address as `conn + (a1 << 3) + 1300` -- with `a1 = 1`
that is `conn + 8 + 1300 = conn + 1308`, i.e. exactly `conn+0x51C`. (This
detail goes a little further than the brief this document was written from:
the `index*8+1300` computation and the resulting address were independently
re-derived and confirmed to land on the same offset the `+msg` handler
reads from, not merely assumed to match.) The actual chat consumer behind
that callback reads `N` (sender, 64 bytes) and `T` (text, 256 bytes); it
does not read `PERS`, `USER`, `BODY`, `TYPE` or `TIME`, so a server sending
any of those instead of `N`/`T` produces a push the client discards without
complaint. `backend/handlers.py`'s `_chat_push` sends `N`+`T`+`F` for this
reason -- `F` set to `B` (broadcast, bit 2) or `P` (private, bit 16) from
the same shared table described above.

### What has -- and hasn't -- been observed

**No console has ever sent `mesg`.** This is checked, not assumed: every
JSONL capture on the rig (`~/nfl-online-revival/captures/*.jsonl`) was
searched for the literal message type `mesg`, and there are zero. A naive
case-insensitive text search for `mesg` *does* turn up hits in nearly every
capture -- but every one of them is the substring `MESGS` inside a `sele`
subscription body (`"ROOMS=1 USERS=1 RANKS=1 MESGS=1"`), not the chat
message type. That false positive is worth remembering the next time
someone greps a transcript for this verb.

Because chat has never been exercised end to end, everything in this section
about `+msg`'s wire shape is *read from the client*, not console-observed --
solid, since it comes straight from the disassembly rather than a guess, but
nobody has watched a real `+msg` land on a screen.

## Matchmaking

Two independent routes exist, and both end the same way: a `+ses` push to
both players, after which the server's job is done and gameplay runs peer to
peer. Both handlers are governed by one transport rule that is worth stating
before either verb, because getting it wrong wedges the connection rather
than producing a visible error: **replies are matched against the head of a
pending queue, by type, and nothing pops that head on a timeout except the
client's own `ping` probe** (which carries a 10-second deadline of its own,
set at `0x00448404` and swept at `0x00446e64` -- distinct from the server's
`~png` keepalive, which the client only ever echoes, never originates). One
unanswered `quik` or `chal` therefore blocks *every later reply on that
connection*, forever, not just the one that was skipped. This is not a
theoretical risk in this project -- a `ping` handler was simply missing at
one point, and because of exactly this head-of-queue matching, it silently
blocked every later reply on affected connections for up to ten seconds at a
stretch until the sweep caught it. Both `quickmatch` and `challenge` in
`handlers.py` always return exactly one reply, including on paths -- an
empty `KIND`, an unresolved persona -- that have nothing useful to say.

### Quickmatch: `quik`

One field, `KIND=<signed decimal>`. The client computes it at
`0x00354630`, and that routine is worth naming precisely: it accumulates
several 4-byte inputs (the build stamp and current game settings) through
repeated calls to `0x0039d7e8` -- **the identical CRC-32 accumulator**
`docs/roster-checksum.md` identifies for the roster version check, reused
here for an unrelated purpose. Because it is a checksum over the client's
own build and settings, a server cannot compute or predict the value a real
console will send, and neither can a hand-written stand-in client -- which
is the whole reason `Matchmaker.pair_any` exists as an explicit, off-by-
default flag rather than a protocol feature: real pairing has to stay exact-
match, because `KIND` equality is the only thing about a pairing the client
itself can ever verify. `KIND=*` withdraws from the queue rather than
joining it.

Console-observed: a real console has sent `quik` with `KIND=-24256204` (and,
separately, `KIND=*` to withdraw) in captures from 2026-08-01. The waiting
client becomes the host purely because it has been queued longer, not from
any preference the client expresses (`matchmaking.py`'s `enqueue`).

### Direct challenge: `chal`

`PERS=<opponent persona>` plus `HOST=<0|1>`, sent from `0x004e26e0`. Both
players send it -- and are expected to disagree on `HOST` by exactly one bit,
which is how the server learns who hosts (`matchmaking.py`'s `resolve`
mirrors this: whoever claimed `HOST=1` hosts, and if both or neither did, the
earlier offer wins). The reply must carry status 0 and land within a fixed
window: at `0x00573d88` sits a small descriptor whose first word is
literally `0x1e` -- **30**, in seconds -- immediately followed by a code
pointer (`0x004e1e40`), read directly off the ELF for this document rather
than taken on faith. Console-observed: `chal` has been sent twice in one
session, both acknowledged.

### The challenge negotiation vocabulary

Before the `chal` message itself, the client and server are expected to
negotiate over `mesg`/`+msg` using a small set of four-character verbs:
`CHAL`, `ACPT`, `DECL`, `CNCL`, `BLOC`, and a field `ATTR=N2`. These strings
genuinely exist in the ELF -- `BLOC`, `DECL`, `ACPT`, `CHAL`, `CNCL` and
`ATTR` sit contiguously at `0x609348`-`0x609380`, and every one of them is
referenced by code clustered at `0x004e1f90`-`0x004e2174`, right alongside
the `chal`/timeout logic above. That much is read from the client and
independently re-confirmed for this document (both the string table's exact
location and the fact that the challenge-handling code, not some unrelated
routine, references it). What has **not** been traced is the exact framing
-- whether `ATTR=N2` and the verbs above are literally sent as
`KEY=VALUE` text inside a `mesg` `TEXT` body, or assembled some other way --
because that would require watching an actual negotiation happen, and since
no console has ever sent `mesg` at all (previous section), none of this
sub-protocol has been exercised either. Treat this paragraph as inferred,
not proven, and do not build a server implementation of it without capturing
a real negotiation first.

## The peer link: `+ses`

### The record

`+ses` fills a record at `conn+512`, confirmed at the parser entry
`0x00449254` (matched against the `"+ses"` literal) by both its base address
(`addiu s1, s2, 512`) and its total size (`addiu a2, zero, 272` at the
memset that clears it first). The field layout below is the complete,
byte-for-byte result of walking every field copy in the parser for this
document -- more precise than the size-only summary this document started
from, and each offset is the exact destination the corresponding
`0x0044c9b0`/`0x0044c628`/`0x0044c550` copy call writes to:

| Field | Record offset | Size | How it's read |
|---|---|---|---|
| `NAME` | 0 | 32 | string |
| `SELF` | 32 | 32 | string |
| `HOST` | 64 | 32 | string |
| `OPPO` | 96 | 32 | string |
| `P1` | 128 | 16 | string |
| `P2` | 144 | 16 | string |
| `P3` | 160 | 16 | string |
| `P4` | 176 | 16 | string |
| `ADDR` | 192 | 4 | dotted quad (`0x0044c628`) |
| `FROM` | 196 | 4 | dotted quad (`0x0044c628`) |
| `SEED` | 200 | 4 | integer (`0x0044c550`) |
| `WHEN` | 204 | 4 | via `0x0044d160` |
| `AUTH` | 208 | 64 | string |

0 + 32×4 + 16×4 + 4×4 + 64 = 272 -- the record is accounted for exactly, with
no gap. The `P1`-`P4` copies alone were re-walked address by address for
this document and land at `0x00449310` through `0x00449390`, matching the
range `lobby.py`'s own docstring already cited for those fields exactly at
both ends.

### Who dials whom

The role decision, at `0x004e2cbc`, is a `strcmp`-equivalent
(`jal 0x004b53f0`) between `SELF` and `HOST`:

| Comparison | Dials | Using | Flags |
|---|---|---|---|
| `SELF == HOST` | `OPPO` | `ADDR` (record offset 192) | `0x102` (258) |
| `SELF != HOST` | `HOST` | `FROM` (record offset 196) | `0x101` (257) |

Both the flag values and the field-offset reads were confirmed directly off
this instruction sequence, not taken from the field table alone: the
"equal" path stores `258` and reads offset 192; the "not-equal" path stores
`257` and reads offset 196.

Critically, **`ADDR` and `FROM` mean the same thing in every `+ses` the
server sends for one match** -- `ADDR` is always the guest's address and
`FROM` is always the host's, in both players' copies. Only `SELF` differs
between the two pushes. This is what lets the server build one `ADDR`/`FROM`
pair and reuse it for both recipients: each client works out for itself,
by comparing its own `SELF` against the shared `HOST`, whether it should be
dialling `ADDR` (if it's about to discover it *is* the host, having just
compared equal) or `FROM` (otherwise). Getting `ADDR`/`FROM` backwards, or
swapping them per-recipient, is a natural mistake to make from the field
names alone; `session_invite`'s own tests (`test_addresses_are_crossed`,
`test_only_self_differs_between_the_two`) exist specifically to catch it,
and (see Traps) this exact crossing has been the subject of a regression
test that once passed while guarding nothing.

An empty `SELF` or an empty `HOST` is refused outright by the implementation
(`session_invite` raises) rather than sent, because two empty strings
compare equal at `0x004e2cbc` just as validly as two matching real names --
every recipient would take the "I am the host" branch and nobody would dial
anybody.

### `WHEN`: delivery is conditional, and it self-clears

`WHEN` is not just another field -- it gates whether the record is ever
handed to anything that reads it. The accessor at `0x00448008` loads `v1 =
716(a0)` (`conn+716`, i.e. record offset 204 -- exactly where the field
table above says `WHEN` lives) and returns the record pointer (`a0+512`)
**unless** `v1` is zero, in which case a conditional move (`movz v0, zero,
v1`) blanks the result to null instead. So `WHEN=0` is not "deliver an empty
session" -- it is "there is no session", full stop, at the level of every
caller that asks. The same accessor, called with a different selector,
clears `WHEN` back to zero once the client has consumed the record (the
store at `0x00448038`-`0x00448044`, gated on the caller passing the state
token `"play"`) -- so a `+ses` is a one-shot delivery from the client's own
point of view, not a value the client keeps re-reading. `session_invite`
refuses to build a record with `WHEN=0` for the same reason `SELF`/`HOST`
emptiness is refused: it would produce a push the client discards on
arrival.

### The address trap

**This is the one worth getting right before anything else in this
document.** The client does **not** simply dial the address a `+ses`
hands it. `0x004deb58` takes the persona name it is about to dial and the
address it was just told to use, looks that persona up in the client's own
*user list* (the same list `+usr` populates), and -- if found -- **overrides
the invite's address with whatever address that user-list entry carries**,
via `movn s0, v0, v0` at `0x004deb90`: a self-referential conditional move
that keeps the looked-up value only when it is non-null, otherwise falls
back to the address the `+ses` actually named. Read from the client, and
independently re-confirmed for this document down to the exact instruction.

The trap: every PCSX2 guest under Sockets mode reports its own address as
the same value, `192.0.2.100`, in the `addr` message it sends on connect.
If a server's `+usr` `A` field is ever populated from that self-reported
value (`Session.client_addr` in this implementation) rather than the TCP
peer address actually observed on the connection (`Session.observed_addr`),
then the moment two players have shared a room -- which populates each
other's user-list entries -- the override above silently replaces a
perfectly good `+ses` invite with `192.0.2.100` again, and both consoles
dial the same wrong host. Nothing about this fails loudly: the `+ses` looks
correct on the wire, the connect attempt simply goes nowhere. This
implementation guards it with a regression test
(`tests/test_backend.py`'s `AddressPropagationTests`) that is itself worth
knowing the history of: an earlier version of that test read
`Session.observed_addr` directly off the session object and asserted on
*that*, which would have kept passing even if the code that actually builds
the pushed `+usr` record silently reverted to `client_addr` -- exactly the
regression the test was named for. The current version builds a real `move`
through the actual handler chain and asserts on the field that goes out
over the wire, which is the only version of this check that can fail when
the bug it guards against comes back.

### The port

Peer traffic is UDP, hardcoded to port **3658** in the client -- confirmed
not just at the cited site (`addiu t3, zero, 3658` at `0x004e2c1c`) but as
the *only* occurrence of the 16-bit immediate `3658` anywhere in the 5.4 MB
executable, checked by scanning every instruction in the ELF for this
document. The server never listens on it and never needs to; `+ses` is an
introduction, and `0x004e2bf8` formats `"%d.%d.%d.%d:%d"` and hands it
straight to DirtySDK's `NetGameUtil`/`NetGameLink` on the client side. Which
side of the resulting socket binds first is decided inside `DRTYSCKF.IRX` on
the IOP and is invisible from this executable.

## What a console has actually done

Worth stating precisely, because the gap between "the client's code does
this" and "a console has been seen doing this" is exactly where this
project has been burned before (`docs/method.md`, Part 2). Checked directly
against the JSONL captures and `emulog.txt` on the rig for this document:

**Proven, console-observed, from the JSONL transcripts.** A console has
created a room (`C.NEW ROOM`, landing on id 79 in two separate sessions --
the same id both times, since the room already existed the second time),
moved into and out of a room (5 `move` exchanges captured, including the
seeded `Open Lobby`), sent `quik` with a real `KIND` (`-24256204`) and
withdrawn from the queue (`KIND=*`), sent `chal` twice, and sent `peek`,
`flag` and `user` -- all of which the current server only acknowledges with
an empty `OK` (`handlers.UNIMPLEMENTED_VERBS`); none of the three has real
handling yet, so "sent and acknowledged" is all that can be claimed for
them. The server's own room-list push, `+rom`, also appears in the
transcripts (8 times) -- proof the write succeeded, in this project's usual
sense of "sent" (the kernel accepted the bytes), not proof a console
rendered them.

**Proven, but not from the backend's own transcript.** No `+ses` record
appears in any capture at all -- zero, across every file. Read alone that
would say "never sent." It would also be wrong: `hub.push()` writes straight
to the socket and only recently started recording anything to the
transcript for pushes specifically (`hub.py`'s own comment on `_transcript`
tells this story, and `tests/test_matchmaking.py`'s
`PushesAreTranscribed` suite exists to keep it from regressing). The
stronger evidence sits one layer down, in PCSX2's own DEV9 network log,
which the backend never touches: at `emulog.txt:1042`-`1043`, `DEV9: Socket:
Binding UDP fixed port 3659` immediately followed by `DEV9: Socket: Creating
New UDP Connection from fixed port 3659 to 3658` -- and nowhere else in the
current log does port 3658 appear. That connection sat idle until PCSX2's
own idle reaper closed it roughly 130 seconds later (`emulog.txt:1063`-
`1064`, `"UDP: Fixed port max idle reached"`). Since 3658 is hardcoded in
exactly one place in the client and reached only by way of a delivered
`+ses`, this is solid evidence a `+ses` was received and acted on, even
though the push itself left no trace in the JSONL transcript. This matches
`docs/method.md`'s account of the same finding, which also cites it as
visible in the stand-in test client's own output (`tools/fake_console.py
--spar`), not just the emulator log -- two independent sources agreeing,
neither of them the backend's own transcript.

**What that one dial does *not* prove.** `fake_console.py`'s own `run_spar`
documents the expected shape of this exact scenario up front: the console
sends `quik`, gets paired, and dials 3658, and roughly ten seconds later
(the client's own give-up deadline, cited there at `0x0011c318`) it reports
the connection failed, because nothing on this project's server answers
game traffic on 3658 -- that IS the pass condition for that test, not a
regression. The ~130-second gap in the emulog before PCSX2 reclaimed the
UDP mapping is a *different*, emulator-level idle timeout, not the game's
own; both numbers are consistent with "the client gave up quickly, and the
now-unused NAT mapping lingered a while after." Separately, and more
fundamentally: the one pairing that has actually been observed necessarily
introduced a single running console to a **second connection from itself**,
because every PCSX2 guest in Sockets mode reports the same address and only
one physical console has been available to test with. That exercises the
`+ses` parse, the role decision, and the address-override path through the
*real* client -- which is real evidence and not nothing -- but it does not
demonstrate that two genuinely distinct consoles can find each other. No
game has been played. That needs two hosts with distinct addresses, which
PCSX2 Sockets mode cannot supply on its own.

**Never observed at all.** Chat. Zero `mesg` sends in any capture, and (see
above) the near-misses that turn up on a naive text search are `MESGS=1`
substrings, not the verb. The `CHAL`/`ACPT`/`DECL`/`CNCL`/`BLOC` negotiation
vocabulary described under Matchmaking is downstream of `mesg` and so has
necessarily never been exercised either.

## Traps

Things that look right and are not, gathered in one place.

**`+rom`'s `F` defaults to -1; almost everything else defaults to 0.** Omit
`F` from a `+rom` push and the client reads all bits set, including the
private-room bit -- a room that should be open shows a lock icon for no
reason anyone will think to blame on a missing field. `+usr` `F`, `+who`
`F`/`RF` and `move` `FLAGS` all default to 0 on the same converter, so the
same omission is harmless there. Always send `F` explicitly on a `+rom`,
even as an empty string.

**A `+usr` deletion's `T` sets the *viewer's own current room's* occupancy,
not the departed user's room.** Both add and delete converge on the same
code (`0x00449d68`) that reads `T` and applies it to whatever room `conn+440`
says the *receiving* client is in. Send a bare deletion (no `T`) and every
client in a room watches its own occupant count silently drop to zero the
next time anyone leaves any room whose departure they happen to be told
about.

**A failed `move` is not safe to answer with a bare error.** The client's
type-gated read (as opposed to its status-gated read) runs regardless of
success or failure, defaults `IDENT` to 0, and drains the user list if that
default doesn't match what the client already holds. Always echo the room
the session currently holds -- 0 before any join, -1 after a leave -- in
both halves of the reply on every failure path.

**Room creation is not idempotent on the wire unless the server makes it
so.** Only the client's unused, generated-name path sends `IGNEXIST`; the
path a console actually takes does not. Reject a same-named room and a
player's default room can be created exactly once per server lifetime.

**Chat's field names are not the ones a first guess would reach for.**
The request uses `TEXT`/`PRIV`, not `BODY`/`USER`; the push's consumer reads
only `N`/`T`, not `PERS`/`USER`/`BODY`/`TYPE`/`TIME`. Both wrong-key mistakes
have already shipped in this project once -- silently, since a client that
doesn't recognise a key just doesn't populate that part of its UI, without
an error on either side.

**A console's self-reported address must never reach a `+usr` push.**
Every PCSX2 guest says `192.0.2.100`; the client overrides a `+ses` invite's
dial address with whatever it has on file for that persona from `+usr`
*whenever the two players have shared a room* (`0x004deb58`). Populate
`+usr`'s `A` from the address actually observed on the TCP connection, never
from the `addr` message's self-reported value.

**`KIND` cannot be forged, guessed, or matched by a hand-rolled test
client.** It is a CRC over the console's own build stamp and settings. A
matchmaking test needs either a real console on the other end, or the
server's explicit `--pair-any` escape hatch -- never a hardcoded value that
happens to work once.

**Every request needs exactly one reply, even the ones with nothing to
say.** Replies are matched head-of-queue by type with no general timeout;
a missing handler (this project shipped one for `ping` at one point) blocks
every later reply on the connection, not just the one that went unanswered.
The client's own `ping` probe is the sole exception, timing out after ten
seconds -- it is not the `~png` keepalive, which the server must originate
(the client only ever echoes it) and must never answer, or the two sides
ping each other forever.

**Grepping a transcript is not the same as checking what happened.** Two
independent false-negative traps sit in this exact area: a case-insensitive
search for `mesg` matches `MESGS=1` inside a `sele` body, and -- more
consequentially -- `+ses`, `+usr`, `+rom` and `+msg` pushes were, for a
period of this project's history, written to the socket but never recorded
to the transcript at all, so "zero hits in the capture" meant "pushes
weren't logged," not "pushes weren't sent." The fix (pushes now log under
their own `"push"` direction, distinct from `"send"`) closes this for new
captures, but it does not retroactively add pushes to the transcripts
already on the rig -- an older capture's silence on any of those four types
is not evidence either way.
