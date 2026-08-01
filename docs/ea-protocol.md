# The EA game-service protocol — Madden NFL 2004 (PS2, SLUS-20752)

This is a reference for the wire protocol the Madden NFL 2004 client speaks to
`ps2madden04.ea.com`, reconstructed from the client binary and cross-checked
against real console captures. It is EA DirtySDK / FESL-family: four-character
ASCII message types, a fixed 12-byte header, and a text body. Everything here
was recovered from a client that has no server to talk to any more — there is
no spec, so every claim below either carries the address it was read from or
is explicitly marked unverified.

**Audience.** Someone writing a server this client will accept, or reviving
another title on the same SDK generation. You should be able to read MIPS;
this document does not re-teach that, only the parts of this binary that are
easy to get backwards.

**Scope.** This covers the session/lobby wire protocol only: framing, the
status word, message dispatch, keepalive, encoding, and login. It does not
cover how a roster update is fetched and installed (`docs/roster-delivery.md`)
or how rooms, presence and matchmaking work once a session exists
(`docs/lobby-and-matchmaking.md`). Where this document touches those topics —
`news`, `sele`, `move` — it says only as much as the framing requires and
sends you to the other document for the rest.

**Sources.** The ELF is `SLUS_207.52` (single `PT_LOAD`, vaddr `0x00100000` =
file offset `0x1000`, `gp = 0x006056f0`). Addresses below are virtual
addresses into that image unless stated otherwise. The reference
implementation is `backend/protocol.py`, `handlers.py`, `hub.py`, `service.py`
in this repository; it was cross-checked against the same addresses cited
here. Several claims are additionally corroborated against real console
captures recorded on the test rig (`captures/madden-*.jsonl`); those are
called out explicitly, because they are the only source that can settle a
question about *behavior* rather than *code* — a completion code or a counter
value that lives in RAM, not in the file image.

This document supersedes `docs/protocol-notes.md` wherever the two disagree.
Two corrections are called out where they occur: an internal tournament tag
transcribed as `mist` is actually `imst` (§ Status word and error tags), and
the previously cited address for the client's 60-second receive deadline does
not hold the constant in question (§ Transport limits).

---

## Framing

Every message, in both directions, is:

```
[4 bytes: type, ASCII][4 bytes: status, big-endian][4 bytes: length, big-endian][body]
```

The length is a 32-bit big-endian integer counting the **whole message,
header included**. A reader that forgets the header is inside the count
desynchronizes on the very next message. The body is `KEY=VALUE` lines
separated by `\n`, NUL-terminated; a value containing a space is wrapped in
`"quotes"`, and the quotes are framing, not content.

Example, an `@dir` request captured from a real console:

```
40 64 69 72  00 00 00 00  00 00 00 57
"@dir"        status=0     length=87

PROD=MADDEN-PS2-2004
VERS="PS2/MS5-Jun 17 2003"
LANG=en
SLUS=BASLUS-20752
\0
```

87 = 12 + 75. This is not a one-off: a second, independently captured message
on the same connection (the `@dir` *reply*) is `ADDR=192.168.68.85\nPORT=10001\n
SESS=1\nMASK=GS\n\0`, and its declared length is 58 = 12 + 46, matching the
actual byte count exactly.

**The client's own send routine confirms the same arithmetic.** It lives at
`0x00453220`, called as `send(a0=connection, a1=type, a2=status, a3=payload,
t0=length)`. `t0` is a signed length or `-1`. When it is negative, the
function measures the payload itself — it calls the client's `strlen`
(`0x004b5590`, confirmed: a byte-scanning loop that returns `count - 1`) and
computes `total = strlen(payload) + 1 + 12` — one byte for the NUL, twelve for
the header. When the caller instead supplies a non-negative length (used for
raw/list bodies, see below), the function trusts it and computes
`total = length + 12` without adding a NUL. Both paths write the header
fields as big-endian by shifting the 32-bit value right 24/16/8 bits and
storing each byte individually — there is no byte-swap instruction; the
big-endianness is built one `sb` at a time.

**The ceiling is 8192 bytes, and it is a clamp, not a constant.** The
function at `0x00452d90` takes a requested size and returns: `4096` if the
request is `≤ 4095`; the request itself if it is between `4096` and `8192`;
otherwise `8192`. Its only call site is `0x00447598`, whose delay slot at
`0x0044759c` is `ori a0, zero, 0x8000` — it asks for 32768 and gets back
exactly 8192. That value becomes the client's socket receive buffer. A
message that declares a length over 8192 is a stream the client considers
desynchronized; the reference server's `MAX_MESSAGE_SIZE` enforces the same
limit on both directions for that reason. (Separately, and worth knowing
before you rely on the client to protect itself: the client's own reader
writes a NUL at `base + cursor + declared_length - 1` with no bounds check
against what it actually received, so an over-declared length is an
out-of-bounds write *in the client*, not a safe rejection. Never emit one.)

## Message types are packed integers, not strings

A four-character type or status is not a string in this binary — it is a
32-bit immediate, built with `lui`/`ori` (or `lui`/`addiu`) and compared with
ordinary integer branches. Read as hex digit pairs MSB→LSB, the value spells
the tag forward: `lui v0,0x6d65` / `ori v0,v0,0x7367` builds `0x6d657367`,
which is `'m' 'e' 's' 'g'` — `"mesg"`. That is also why grepping the ELF for
`"@dir"` finds nothing: the four bytes never sit adjacently as a string
constant in code. They do appear as data — inside lookup tables, where an
entry is compared with a single `lw` rather than rebuilt each time — and
there they are **stored byte-reversed**, because a little-endian `lw` of
bytes `[b0,b1,b2,b3]` (low address first) produces the word
`b0 | b1<<8 | b2<<16 | b3<<24`, and for that to equal the MSB-first spelling
above, the bytes in memory have to run backward. A hex editor looking at such
a table shows `gsem`, not `mesg`. This inversion is the single easiest way to
misread this protocol, and it is why the error tables below are given as
their *reconstructed* reading, not a raw dump.

Every tag named anywhere in this document was independently confirmed as a
`lui`/`ori` (or `lui`/`addiu`) pair building the expected 32-bit value
somewhere in the ELF. The client-sent verbs and the shared login/session
vocabulary each appear at multiple build sites, consistent with being
constructed every time the client sends one; the eight push-only server
types below each appear at **exactly one** site (`+ses` at two), consistent
with being pure comparison targets on the receive side — the client only
ever needs to recognize them once.

**Client → server:** `@dir` `addr` `skey` `auth` `acct` `cper` `dper` `pass`
`pers` `sele` `cusr` `news` `room` `move` `mesg` `quik` `chal` `peek` `flag`
`user` `onln` `rank` `snap` `lost` `edit` `ping` `~png`.

**Server → client (push-only, never sent by the client, safe to send
unsolicited):** `+ses` `+msg` `+who` `+rom` `+pop` `+usr` `+rnk` `+snp`.

**`DQUE`** is not a message type at all — it is a wildcard value used only on
the *asking* side of the reply-matching logic (see below). It resolved to the
same 32-bit-immediate pattern as everything else, at two sites.

`room`, `move`, `mesg`, `quik`, `chal`, `peek`, `flag`, `user`, `rank`,
`snap`, and the push types are lobby/matchmaking/chat concerns; see
`docs/lobby-and-matchmaking.md` for their field layouts. `news`'s general
category framing is covered below because it is where the status word's
double duty lives; its roster-specific category is covered in
`docs/roster-delivery.md`.

## The status word

**The second header field is a four-character tag, not a numeric error
code.** Zero means success; anything else is read back through a table to
choose an error screen. Two independent reply-completion callbacks confirm
the field is read this way, not as an integer:

- `0x004e1e40`–`0x004e1ea4` is the completion callback for a challenge reply
  (`handlers.py`'s own citation for `chal`'s 30-second timeout). At
  `0x004e1e68`, `lw v0, 8(a0)` loads the reply object's status word, and the
  following `bne v0, zero` aborts on *any* non-zero value — it never compares
  against a specific error, only against zero.
- `0x004e1da8`–`0x004e1e3c` is a sibling callback, gated on the reply's
  *type* being `mesg` (compared at `0x004e1dec`–`0x004e1f8`, i.e. this is
  chat-specific). At `0x004e1e00`, `lw v1, 8(a0)` loads the same field and
  compares it against two constructed literals: `0x75757372` (`"uusr"`) and
  `0x696e676d` (`"ingm"`) — plainly named error tags, not sentinel integers.

### Status auth terminates the session

Sending status `auth` is not a general-purpose error — it tells the client
to tear the connection down. Confirmed exactly at `0x00449090`: the
instruction there is `jal 0x00447490` (a termination/close routine, called
with reason code `a2=2`), reached only when a freshly-received status word
compares equal to the literal `auth` (`0x61757468`, built and checked at
`0x00449074`–`0x0044907c`). Never reuse `auth` as an error tag for anything
you want the client to recover from.

This is worth flagging because `auth`/`acct` are heavily overloaded in this
protocol and mean three unrelated things depending on which field they
appear in: a **message type** (`auth` = login, `acct` = registration), a
**session state token** the client tracks internally after login (compared
against at `0x00448390`–`0x0044839c`, alongside `conn`/`idle`), and — only in
the **status field** of a reply — a command to disconnect. Confusing any two
of these is an easy, silent mistake.

### Error tables

Two static tables hold the (tag → code) and (request, internal, wire)
mappings the client uses to pick error screens. Both were re-read directly
from the ELF this session; the account table turned out larger than
previously catalogued, and one tournament entry was mistranscribed.

**Accounts, at `0x00573c20`.** A contiguous array of 38 eight-byte entries —
a four-character tag (stored byte-reversed, as above) followed by a 4-byte
little-endian integer code. It is not sorted by code, so the array order
below is the file order:

| Tag | Code | Tag | Code | Tag | Code | Tag | Code |
|---|---|---|---|---|---|---|---|
| `-dif` | 0x34 | `-bsy` | 0x01 | `dupl` | 0x10 | `many` | 0x1a |
| `-dev` | 0x35 | `-con` | 0x2d | `mail` | 0x12 | `pmal` | 0x1b |
| `-cfg` | 0x2c | `-???` | 0x36 | `pass` | 0x13 | `born` | 0x1e |
| `-ini` | 0x03 | `-dns` | 0x37 | `filt` | 0x14 | `gend` | 0x1f |
| `-ton` | 0x02 | `-ath` | 0x2e | `tosa` | 0x15 | `spam` | 0x20 |
| `maut` | 0x0d | `miss` | 0x0e | `blak` | 0x16 | `ipas` | 0x21 |
| `imst` | 0x0f | `maxp` | 0x11 | `logn` | 0x17 | `inam` | 0x1d |
| `iper` | 0x19 | | | `elen` | 0x1c | `tooy` | 0x23 |
| `uusr` | 0x38 | `dete` | 0x39 | `lock` | 0x3a | `rsrv` | 0x3b |
| `auth` | 0x3c | `neml` | 0x3d | `cdev` | 0x3e | | |

`handlers.py`'s `ERR_*` constants use eleven of these
(`dupl inam elen mail pass tosa born gend tooy many miss`); `spam` and
`filt` are visible in the table but not yet wired to a named constant.
The meaning of the remaining tags (`-dif -dev -cfg -ini -ton -bsy -con -???
-dns -ath maut imst maxp blak logn iper pmal ipas dete lock rsrv neml cdev`)
is not established beyond the literal tag — several read plausibly (`lock` =
account locked, `rsrv` = reserved name, `cdev` next to the `CDEV` device-id
field) but that is a guess, not a finding, and is presented as such. The
table ends cleanly at `cdev`; the bytes immediately following it are a
pointer table that happens to include `0x004e1e40` and `0x004e1da8` — the
two callbacks described above — which is a reasonable coincidence of layout,
not a claim about a relationship between the two tables.

**Tournaments, at `0x0056d4dc`.** 24 rows, 12 bytes each — `(request type,
internal tag, wire tag)`, all three four-character and byte-reversed in
memory the same way — terminated by a 25th row of all `0xFFFFFFFF`. Rows
without a request type store `0x00000000` in that field; row 8 stores
`0x00000000` in the wire-tag field (a two-field row). Re-read byte-for-byte
this session:

| # | Request | Internal | Wire |
|---|---|---|---|
| 0 | `#cre` | `xist` | `dupl` |
| 1 | `#cre` | `maxt` | `full` |
| 2 | `#cre` | `filt` | `fane` |
| 3 | `#joi` | `dupl` | `uniq` |
| 4 | `#joi` | `ajoi` | `dupl` |
| 5 | `#joi` | `nspc` | `full` |
| 6 | `#lea` | `uusr` | `miss` |
| 7 | `#sea` | `many` | `parm` |
| 8 | `#mem` | `uusr` | *(none)* |
| 9 | `#new` | `nspc` | `size` |
| 10 | `#joi` | `many` | `many` |
| 11 | `#joi` | `imst` | `twic` |
| 12 | `#dat` | `tooy` | `same` |
| 13 | *(none)* | `dber` | `misc` |
| 14 | *(none)* | `nspc` | `misc` |
| 15 | *(none)* | `invp` | `parm` |
| 16 | *(none)* | `miss` | `parm` |
| 17 | *(none)* | `itrn` | `name` |
| 18 | *(none)* | `auth` | `auth` |
| 19 | *(none)* | `pass` | `pass` |
| 20 | *(none)* | `team` | `itid` |
| 21 | *(none)* | `nfnd` | `itid` |
| 22 | *(none)* | `tsta` | `strt` |
| 23 | *(none)* | `nown` | `ownr` |

**Correction:** row 11's internal tag is `imst`, not `mist` as a previous
version of `protocol-notes.md` recorded — the bytes at that offset, reversed,
read `i,m,s,t`, not `m,i,s,t`. `imst` also appears independently in the
account table above (code `0x0f`), which is corroborating rather than
coincidental — it reads as an "already in progress / already a member" class
of tag reused across subsystems. Rows 13–23 (eleven rows with no request
type) were previously represented by a sample of three; the full set is
given here. These rows read as a shared, subsystem-agnostic tail — `auth`,
`pass`, generic `misc`/`parm` — appended after the tournament-specific rows
rather than belonging to any one request.

## The `news` reply is not typed `news`

This is the exception that costs the most time, because there are two
different wrong readings and both look correct until tested. **The category
a `news` request asks for (0, 1, 2 — service config, headlines, roster
manifest) comes back in the reply's *status* word, not its type, and not
anywhere in the body.** The reply's type is `news`; its status is `new0`,
`new1`, or `new2`, chosen to match the request's `NAME` field.

**Why "reply type `news`, status 0" fails.** The client builds the tag it
will accept with simple integer addition, not string formatting:
`0x6e657730` (`"new0"`, built at `0x0034f4e0`–`0x0034f4e8`) plus the
requested category, added directly to the low byte at `0x0034f4f4` — adding
1 turns the trailing ASCII `'0'` (`0x30`) into `'1'` (`0x31`), and so on.
That computed value is compared, via `xor`, against the reply's *status*
word — loaded at `0x0034f4f8` from the same `+8` offset established below —
at `0x0034f500`. A `movz` at `0x0034f504` yields the body pointer only on
equality and silently yields NULL otherwise — **not** an error, just an
empty result. Tag it `news` with status `0` and the comparison fails every
time, silently.

**Why "reply type `new0`" also fails.** The client's reply-matching gates
(both of them, described below) match on *type*. A message actually
transmitted with type `new0` never satisfies the pending `news` request at
either gate, so it is never delivered to the news-consuming code at all —
the underlying completion machinery (next section) records this specific
failure mode as its own numbered outcome.

**The fix is what the reference server does:** send type `news`, status
`new0`/`new1`/`new2` matching the request's `NAME`, and put the actual
category payload in the body. `protocol.encode_raw` supports sending a status
tag with an arbitrary body for exactly this case.

### The completion-code mechanism, fully traced

This is worth documenting in full because it explains *why* both wrong
readings fail differently, not just that they do.

A single client-side function services every `news` request:
`0x0034f2a0(a0, a1, a2=NAME-buffer, a3=..., t0=...)`. Its default return
value is `4` (set at `0x0034f2cc`, before anything else runs). It first
calls a check function at `0x0034eb70`; if that reports "already have a
current answer," it returns `4` immediately without sending anything. Only
otherwise does it build a request record — at a **fixed global address**,
`0x00560af0` (built via `lui 0x0056` / `addiu 0xaf0`, confirmed at two call
sites) — and dispatch it through `0x004df3d8`, the client's general request
sender. That sender has exactly **27** call sites across the binary (an
exhaustive count, not an estimate), one of which is this one; the same
address is cited in `handlers.py` as the generic path every request type
funnels through, and the count matches its own comment. After sending, this
function polls (`0x00452b70` pumps the network, `0x0034eb70` re-checks) until
ready, then reads the outcome back out of `0x00560af0 + 4` — address
`0x00560af4` — and returns it.

That field is written by a **separate** callback, `0x0034ea48`, which uses
the same fixed base address (confirmed independently: `lui 0x0056` /
`addiu 0xaf0` again, at `0x0034ea54`/`0x0034ea74`). It writes exactly three
values, and the instruction that writes each is named here because they are
easy to re-derive and easy to get subtly wrong:

- **`3` — no reply object at all.** If the incoming record's own reply
  pointer (its `+4` field) is NULL, the branch at `0x0034ea7c` is taken with
  `v0 = 3` already loaded in its delay slot (`0x0034ea80`), landing on
  `sw v0, 4(s1)` at `0x0034eb40`. This is "the request was sent and nothing
  ever answered it" — the `new0`-tagged-message-never-arrives case.
- **`0` — delivered, but the status word is zero.** When a reply object *is*
  present, its 16-byte record is copied into the global struct
  (`ldl`/`ldr`/`sdl`/`sdr` at `0x0034ea90`–`0x0034eaac`), and its status
  field (offset `+8`) is loaded at `0x0034eb1c` and tested at `0x0034eb20`.
  If it is zero, `sw zero, 4(v0)` at `0x0034eb3c` records `0`. This is the
  "tagged `news`, status `0`" failure mode: the message *arrived*, matched
  the pending request by type, but carried nothing the news consumer will
  accept as a category.
- **`4` — accepted.** If the status word is non-zero (which, for `news`,
  means it holds a real `new<n>` category tag, since `0` is the only value
  a plain success status can take), `v0 = 4` is set at `0x0034eb28` and
  stored via `sw v0, 4(v1)` at `0x0034eb30`. Only this path lets
  `0x0034f2a0` return `4`, which is the value `0x0034f4f0` requires before
  it will even look at the tag comparison described above.

**`+8` is the status word** in both this trace and the general receive path:
the wire parser at `0x00453380` fills a caller-supplied status pointer by
reading header bytes 4–7 (confirmed at `0x004534fc`–`0x0045350c`, four
`lbu`s assembled big-endian) and storing the result at `0x00453524`; the
generic incoming-message dispatch struct built at `0x00448cc0` places that
same value at its own `+8` (traced back to a local fed by the same parser).
Two unrelated call sites agreeing on the same offset is why this is stated
as settled rather than merely likely.

Put together: a `news` reply is accepted only when its type is `news`
*(so it reaches the pending-request slot)* **and** its status is non-zero
*(so the completion code comes out `4` instead of `0`)* **and** that status,
compared as a 32-bit integer, equals `"new0"` plus the requested category
*(so the tag match at `0x0034f500` succeeds instead of yielding NULL)*. All
three conditions are necessary; the reference server's `_news_message` in
`handlers.py` is built to satisfy all three at once.

## Reply matching and the pending queue

The client has **no correlation id anywhere in the header.** Replies are
matched to outstanding requests purely by *type*, against the *head* of a
queue — never by content, never by position beyond "the oldest thing we're
still waiting on for this type." Two independent matching mechanisms exist,
confirmed as genuinely separate data structures:

**A per-connection one-shot handler list**, rooted at offset `1340` of the
connection object. `0x00446ce0(a0=connection, a1=..., a2=requested type)`
walks the list; the comparison at `0x00446d20` (`beq v0, s0`, where `v0` is
the list node's registered type and `s0` is the requested type) is the
match. If that fails, the code falls through to test whether the *requested*
type is the literal `DQUE` (`0x44515545`, built and compared at
`0x00446d24`–`0x0044602c` [sic, see next line] `0x00446d2c`) — and if so,
treats it as a match regardless of what the node actually holds. **The
wildcard is on the asking side, not the registered side**: a handler waiting
for `DQUE` accepts anything; a handler registered under some other type is
not matched by an incoming `DQUE`. Getting the direction backward makes a
wildcard receiver silently deaf. The registration counterpart is
`0x00446c08`, a sibling of the same list (confirmed as the function that
enqueues a pending `ping` expectation — see below).

**A second, global pending-request ring**, entirely independent of any one
connection: a fixed array at `gp + 15128` (i.e. `0x00609308`-relative — the
address is gp-relative in code, not a fixed absolute one), stride 24 bytes,
with a count byte at `gp + 15133` and an active flag at `gp + 15134`.
`0x004df098` looks a request up in this ring; the match is an `xor`-and-test
at exactly `0x004df0e0`. This is the mechanism the `news` completion code
above rides on.

**Consequently: one reply per request, exact type, in order, and nothing
pops the head on a timeout except one type.** An extra, wrong, mistyped, or
out-of-order reply is matched against whatever is actually at the head, not
against what it was meant to answer — so a wrong reply doesn't merely go
unnoticed, it satisfies (and removes) the wrong pending entry, corrupting
the queue for everything queued after it. A verb the server never answers at
all wedges the connection *permanently*: every later reply, of every type,
queues up behind the one nobody ever answered. This is why the reference
server acknowledges every client-sendable type with at least an empty
success reply (`handlers.UNIMPLEMENTED_VERBS`), even where there is nothing
useful to say yet.

**The one exception is `ping`** (not `~png` — see next section; this is the
client's own outbound latency probe, a different type entirely). Its pending
entries carry a deadline: registration goes through `0x00448340`, which
first checks the connection's state against `conn`/`idle`/`auth`/`acct`
(mirroring the same open-vs-established distinction `handlers.py`'s
`OPEN_STATES` encodes), then enqueues type `ping` (`0x70696e67`, built at
`0x004483c8`/`0x004483dc`) via `0x00446c08` and stores a deadline —
`now() + timeout` — at the new record's `+12`, exactly at `0x00448404`. The
timeout is a caller-supplied parameter, not a constant baked into this
function; its one actual value traces to the function's single call site,
`0x004e2e00`, immediately preceded by `addiu a3, zero, 10000` at
`0x004e2dfc` — **10000 ms**, i.e. ten seconds — reached from the lobby
latency feature beginning around `0x004e2d90`. A sweep, confirmed at
`0x00446e58`–`0x00446e90`, walks the *same* connection-1340 list, builds the
tag `ping` again (at `0x00446e64`–`0x00446e68`), and evicts only entries of
that type past their deadline. No other type gets this treatment: an
unanswered `room`, `move`, `chal`, or anything else simply sits at the head
forever.

## Keepalive: `ping` vs `~png`

These are two unrelated mechanisms that happen to share three letters, and
conflating them is a real trap:

- **`ping`** is the *client's* outbound latency probe, described just above
  — it goes through the ordinary request/reply machinery and gets a real
  reply from whoever it asks (reachable from the lobby via `0x004e2dc0`).
- **`~png`** is the *keepalive*, and the client never originates it. It only
  ever echoes one sent to it — and does so before the message even reaches
  the ordinary dispatch path. The interception is `0x00448C58` exactly: at
  that address, `bne a2, v1, 0x00448cc0` compares the just-parsed incoming
  type (`a2`, freshly loaded from a local at `0x00448c50`) against the
  literal `~png` (`0x7e706e67`, built immediately before at
  `0x00448c48`/`0x00448c54`). When they match, the branch is **not** taken,
  and control falls into a short ping-specific path instead of the general
  message-object construction at `0x00448cc0` that everything else goes
  through — meaning `~png` never enters the one-shot-list / pending-ring
  matching described above at all.

The echo is not verbatim. Type and body come back unchanged, but **the
status word carries a counter that increments across the session, not the
status you sent.** This is directly observable on hardware: across the
project's captures, returned `~png` status values cluster tightly —
`0x00000010` (224 occurrences) and `0x00000011` (435) dominate, with
scattered `0x0000000a`, `0x0000000e`, `0x0000000f`, `0x00000021`,
`0x00000022`, and one outlier `0x00000392`, plus `0` on the very first
exchange of a session (before the counter has advanced). Treat this field as
opaque and never compare it; the server has to originate `~png`, and must
**never** answer one — since the client's own reply *is* a `~png`, replying
to it produces another `~png`, and the two sides ping each other forever.
Nothing else pops a `~png` off any queue on a timeout, because it never
enters a queue.

## Percent-encoding

Two client-side routines govern what characters survive a round trip.

**The decoder, `0x0044c9b0`,** used whenever the client copies a value out of
an incoming message: strips one matched leading quote (checks for `0x22` or
`0x27` at `0x0044ca1c`–`0x0044ca30`); decodes `%XX` hex escapes and collapses
`%%` to a literal `%` (the `%` check, byte `0x25`, is at `0x0044ca5c`); and —
this is the one that matters most for a server — **stops copying at any byte
below 32** (`sltiu v0, v0, 32` and the following branch, at `0x0044ca50`–
`0x0044ca54`). A control character inside a value is not encoded, escaped,
or rejected — it silently ends the copy right there, truncating everything
after it. Anything you send that might legitimately contain a byte under 32
must be percent-escaped or it will be cut off with no error on either side.

**The encoder, `0x0044b840`,** used when the client formats a value for the
wire, escapes exactly four characters plus the whole non-printable range.
The four are set as immediate constants directly before the encoding loop
and are unambiguous: `t2 = 0x3D` (`'='`), `t1 = 0x22` (`'"'`), `t0 = 0x3A`
(`':'`), `a3 = 0x25` (`'%'`) — at `0x0044b830`, `0x0044b834`, `0x0044b838`,
`0x0044b83c` respectively. Separately, anything outside the printable range
`0x20`–`0x7E` is escaped regardless (the range test at `0x0044b840`–
`0x0044b848`). A server-side encoder only has to protect against these same
four characters plus non-printables — `protocol.percent_escape` handles the
one that actually shows up in practice (`%`) and the framing-level `_check_field`
rejects newline/NUL outright rather than relying on escaping for those.

## Two body layouts

**Ordinary replies** are one `KEY=VALUE` per line. The field lookup,
`0x0044acc8(haystack, key)`, scans the *entire* body text for a match rather
than assuming any particular line order or position — confirmed structurally
(it computes the search key's length up front, then scans using `=` as the
value delimiter, with no line-start anchoring visible in the entry logic).
Field order in a reply is therefore free.

**List replies** — currently only the `news` category-2 roster manifest, but
the mechanism is general — lay records out differently: **one record per
newline, fields within a record separated by a TAB**, not a space. This was
proven on real hardware the hard way: a manifest record built with spaces
between fields produced a console `GET` request reading
`/roster.dat CRC=... NAME=Roster HTTP/1.0` — the client's value copy (the
same `0x0044c9b0` decoder above) does not stop at a space, so the "space
between fields" was read as part of the URL value, swallowing the rest of
the line. A tab (`0x09`) *is* below 32 and does stop the copy, which is what
actually separates fields in this layout. **An empty list must be a body
with zero records, not a body with one field and no URL** — the record
count comes from splitting the body on newlines, so a single stray `NAME=`
line with nothing else in it is read as one complete (if useless) record,
not as "no records." I did not re-derive the tab/newline split from the ELF
this session — it is stated here as previously hardware-proven (see the
`GET /roster.dat` incident above, which was captured, not inferred), and the
reference implementation's `_news_list` / `RECORD_FIELD_SEP` encode it that
way. See `docs/roster-delivery.md` for the manifest's actual fields.

## Session establishment: the login sequence

Confirmed against two independent hardware captures, one of a returning
account and one of a first-time registration. The two agree on everything
except which verbs get sent, which is exactly what you'd expect: the client
always tries the "this already exists" verb first, and only sends a
creation verb after being told, explicitly, that it does not.

**Returning account/persona**, byte-exact from a capture:

```
@dir  (contacts port 10000; reply gives ADDR/PORT/SESS/MASK — see below)
addr  (recv only — the client does not wait for a reply; see @dir section)
skey  (session key exchange)
auth  (NAME + PASS; reply carries PERSONAS)
pers  (choose a persona)
sele  (subscribe to push channels; server pushes +rom here)
cusr  (persona preferences, e.g. SETFAV)
news  (NAME=0; reply status new0 carries BUDDY_URL/BUDDY_PORT, DATE, CSUM)
```

**First-time account, same capture family, showing the actual fallback
mechanics** (a previous summary of this sequence said "auth, or acct then
auth" without stating the trigger; the trigger is an explicit failure
status, not a client-side guess):

```
recv auth  ->  send auth  status=miss     (no such account — a real failure
                                            status, not silence, is what
                                            tells the client to move on)
recv acct  ->  send acct  status=ok       (registration)
recv auth  ->  send auth  status=ok       (retried automatically, succeeds)
recv pers  ->  send pers  status=miss     (no such persona yet)
recv cper  ->  send cper  status=ok       (persona creation)
recv pers  ->  send pers  status=ok       (retried, succeeds)
```

A server that answers an unknown `auth`/`pers` with silence rather than an
explicit non-success status will never see `acct`/`cper` — the client has no
other trigger to fall back to registration. This is a direct consequence of
the reply-matching rule above: silence just leaves `auth` sitting unanswered
at the head of the queue.

`addr` genuinely gets no reply, on purpose — captures confirm the client
sends it and moves straight on to `skey` without waiting; it exists so the
client can report its own pre-redirect address, which the reference server
deliberately ignores in favor of the address actually observed on the TCP
connection (see `Session.observed_addr` in `handlers.py` — under PCSX2's
Sockets mode every guest reports the same NAT address, `192.0.2.100`, so the
client's self-report is useless for introducing two players to each other
later).

**Then, on a separate connection, the buddy service**: `AUTH`, `PSET`,
`RGET` — confirmed in that order from captures, with the same 12-byte
header framing (it shares the client's one send routine). A representative
capture: `AUTH` carries `USER=<persona>/cso/madden-ps2-2004` and an opaque
binary `LKEY`; `PSET` carries `SHOW=CHAT` as the literal string, not the
numeric index from the client's internal table (`0 DISC 1 CHAT 2 AWAY 3 XA
4 DND 5 PASS` at `0x005742b8` — that table is what the client displays,
not what it sends); `RGET` is sent twice, once each for `LIST=B` and
`LIST=I`. The buddy service's address is not fixed — it arrives in-band as
`BUDDY_URL`/`BUDDY_PORT` inside the main service's `news` category-0 reply,
*after* login, so it cannot gate reaching a lobby and a minimal stub
(answer `AUTH` with success, echo `PING`, return empty rosters) is
sufficient; see `backend/buddy.py`.

## `@dir` and the redirect

`@dir` is the very first message on the wire, sent to **port 10000**
(confirmed both from captures and from the reference server's own default
port list). Its reply redirects the client to the actual session port —
**10001** by default — via `ADDR`/`PORT`, and everything after `addr` in the
sequence above happens on that second connection.

The reply's fields, their parsers, and where each lands in the client's
connection object were re-confirmed byte-for-byte this session, including
the destination offsets and buffer sizes:

| Field | Address | Converter | Stored at | Notes |
|---|---|---|---|---|
| `DIRECT` | `0x608828` | presence test only | — | If present, `ADDR`/`PORT` parsing is skipped entirely (confirmed: the branch at `0x00448d78` jumps straight past both) |
| `ADDR` | `0x6087a0` | `0x0044c628` (dotted quad) | connection `+948` | Must be a dotted quad; see below |
| `PORT` | `0x608830` | `0x0044c550` (atoi) | connection `+944` | Ordinary signed decimal |
| `SESS` | `0x608838` | `0x0044c550` (atoi) | connection `+1208` | Ordinary signed decimal |
| `MASK` | `0x6087f0` | `0x0044c9b0` (percent-decode) | connection `+1212` | String, 64-byte buffer |
| `DOWN` | `0x608840` | `0x0044c9b0` (percent-decode) | connection `+952` | String, 256-byte buffer; **only parsed when `ADDR` came back 0** (gated at `0x00448e18`), and parsing it also sets flag bit `0x400` at connection `+8` |

**`ADDR` must be a dotted-quad string, never a raw integer or hex value.**
`0x0044c628` is not a general integer parser: it explicitly checks each
separator byte against `46` (`'.'`) and, per octet, multiplies the running
accumulator by 10 and shifts the accumulator left 8 bits on each `.` —
confirmed exactly. `PORT` and `SESS`, by contrast, go through `0x0044c550`,
an ordinary signed-decimal `atoi` (handles a leading `+`/`-`, then digits) —
confirmed exactly, and structurally distinct from the dotted-quad reader.
Sending `ADDR` as anything but `N.N.N.N` silently parses as garbage rather
than failing.

## The buddy service

Covered above as part of the login sequence for its ordering; worth
restating on its own that it is a **separate TCP endpoint**, reusing the
main service's framing and send routine but running its own accept loop and
its own connection state. Nothing about reaching the lobby depends on it,
because its address is only ever learned after the client is already past
login. See `backend/buddy.py` for the minimal stub this project runs; a
fuller presence/roster implementation is out of scope for this document.

## Transport limits

Two deadlines and one size limit, beyond the 8192-byte message ceiling
already covered:

- **120 seconds during the directory phase** (before the client has picked a
  session port), confirmed exactly at `0x00447854`: the constant there is
  `lui v1, 0x0001` / `ori v1, v1, 0xd4c0` = `0x0001d4c0` = **120000**,
  milliseconds, added to the current time (via the client's own time
  function, `0x00453698`) and stored as the connection's deadline.
- **60 seconds for an established session**, reset on every message the
  client receives. This figure is carried forward from prior work in this
  project rather than independently re-derived this session, with one
  correction: the constant `60000` (`0xEA60`) does exist in the binary, but
  at `0x0044a43c` — not `0x00448C4C`, the address a previous pass cited.
  `0x0044a43c` sits immediately after a call to the header parser
  (`0x00453380`, the same one described under the `news` section) and
  immediately before a call taking the connection object as its argument,
  which is consistent with "reset the deadline on message arrival," but I
  could not confirm within this session exactly how the `60000` value
  reaches that second call — flagged here as **partially verified**: the
  constant and its general neighborhood are confirmed, the precise
  argument-passing is not.
- **One TCP write per message; there is no reassembly for a message split
  across reads.** The reader handles several whole messages coalesced into
  one read, but a message whose bytes arrive split across two separate
  reads is lost — the completion path resets its cursor and discards
  whatever was buffered. Never rely on Nagle, buffering, or multiple small
  `send()` calls composing a single logical message on the wire; write it
  as one blob or don't send it.
- **There is no graceful close.** The client does not react to a FIN; it
  just sits until its receive deadline (60s, or 120s during the directory
  phase) expires and then gives up. Closing a socket early doesn't fail
  faster, it just fails less informatively.

## Traps

A short list of things that read correctly and are not, gathered from the
sections above for anyone skimming rather than reading start to end.

**`news`'s category is the status word, not the type, and not a body
field.** Tag the reply `news`/status `new0..2`. Tagging it `new0`/`new1`/
`new2` as the *type* fails the pending-request match entirely; tagging it
`news`/status `0` matches but is then silently discarded because the
completion code comes out `0`, not `4`. Both failures are indistinguishable
from the client's side — it just never seems to get an answer.

**The reply-matching wildcard, `DQUE`, only works on the asking side.** A
handler registered under a concrete type is never matched by an incoming
`DQUE`; only a handler that is itself waiting *for* `DQUE` matches anything.

**`ping` and `~png` are unrelated verbs that share a substring.** `ping` is
the client's own outbound probe and gets an ordinary reply. `~png` is the
keepalive; only the server originates it, the client only echoes, and
answering an echo makes both sides ping forever. `~png`'s status word on the
echo is a session counter, not the value you sent — never compare it.

**An unanswered request wedges the connection, permanently, except for
`ping`.** Every message type but `ping` sits at the head of the pending
queue forever if nobody answers it; every later reply of every type queues
up behind it. A handler that legitimately has nothing to say still has to
send an empty success reply.

**Length includes the header.** A declared length of 87 means 75 bytes of
body, not 87. Getting this backward either truncates the last 12 bytes of
every body or reads 12 bytes of the next message's header as this message's
trailing content.

**A control byte (`< 0x20`) inside a value truncates it silently, with no
error on either side** — the client's value-copy routine just stops there.
This applies to values you send as much as values you parse; anything that
might legitimately contain such a byte must be percent-escaped first.

**Fields in a list body are TAB-separated, not space-separated** — proven
the hard way on real hardware (see `docs/roster-delivery.md`). A space
inside what should be a field boundary gets absorbed into the previous
field's value instead of ending it.

**Status `auth` disconnects the client.** It is not available as a general
error tag, and the string `auth`/`acct` means three different things
depending on whether it appears as a message type, a session-state token, or
a status word — check which field you're looking at before assuming which
of the three it is.

**`ADDR` in an `@dir` reply must be a dotted-quad string.** It goes through
a parser that looks for literal `.` bytes, not through the ordinary integer
parser used for `PORT`/`SESS`; anything else parses as silent garbage rather
than an error.

## See also

- `docs/roster-delivery.md` — the `news` category-2 manifest, the HTTP fetch,
  CRC verification, and TDB installation.
- `docs/lobby-and-matchmaking.md` — rooms, presence (`+rom`/`+usr`/`+pop`),
  `+ses` peer introduction, and quickmatch/challenge.
- `docs/roster-checksum.md` — the `CSUM`/`DATE` staleness comparison `news`
  category 0 announces, and how the checksum itself is computed.
- `docs/protocol-notes.md` — the original capture-by-capture research log
  this document distills and, in the two places noted above, corrects.
