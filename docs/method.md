# Method

Whoever picks this up next -- including whoever wrote it, months later, having
forgotten most of it. The technical facts about the protocol, the roster
format and the rig live in `docs/protocol-notes.md`, `docs/roster-checksum.md`,
`docs/backend-data-model.md` and `docs/emulator-capture.md`. This document is
about something else: how to find things out reliably in this project, and the
specific, checkable ways that finding-out went wrong.

The second half is the one worth reading slowly. A great many confident
conclusions in this project's history turned out to be false -- not from
carelessness, but because a short list of failure patterns kept recurring:
trusting a value without asking where it came from, mistaking "we sent it" for
"the console used it", building on another agent's claim without checking it,
validating an answer against nothing but itself. Part 2 catalogues those
failures by name, each one checkable against the commit that made it and the
commit that caught it. Part 3 turns the catalogue into rules. Read Part 2
before trusting your own next conclusion here -- the odds are good it rhymes
with one already on the list.

Everything below is checkable: `git log` in this repo carries the corrections
in the commit messages themselves, in more detail than is repeated here, and
`git log -p` shows the code alongside them.

## Part 1 -- the tools

Quick reference, then detail on each:

| Tool | For | Reach for it when |
|---|---|---|
| `tools/pine.py` | live read/write of a running PCSX2's EE memory | you need one number out of (or into) a booted console, now |
| `tools/fake_console.py` | a scripted client that drives the protocol and asserts on replies | testing a server change, or a two-console scenario Sockets mode can't give you |
| `tools/read_roster_checksum.py` | one word out of a PCSX2 savestate | PINE isn't an option but a `.p2s` is |
| `tools/madden_tdb.py` | decode the TERF container / bit-packed TDB format | reading or writing anything roster-shaped |
| `tools/roster_checksum.py` | compute the CRC the console compares `CSUM` against | announcing a roster version the console will agree with |
| `tools/build_roster.py` | extract the one servable payload; also build/subset TERF containers for inspection | preparing something to serve -- **only** `--extract-member` produces bytes the console will accept |
| `tools/mark_roster.py` | edit one player's name in place and reseal the TDB block checksums | proving an install happened instead of inferring it |
| `recon/` | the passive capture harness: DNS responder, TCP/UDP sinkhole, pcap reader, stack classifier, MIPS disassembler | any time a title's traffic or code needs observing rather than guessing |
| `serve-madden.sh` | starts the backend with the known-good flags, and refuses to start a second instance | every time -- don't hand-invoke `python3 -m backend` |
| `tests/*.py` | an offline, mutation-checked regression suite | before trusting that a fix is a fix and not just a plausible edit |

### `tools/pine.py` -- ask the console directly

Before PINE, every measurement in this project went through a savestate: patch
the game to store a value somewhere, ask a person to press F1, unzip 32 MB of
(now Zstandard-compressed) memory, read one word. That loop has a human in it,
costs minutes per iteration, and can only ever see values something was
deliberately patched to write down. Five candidate roster-checksum algorithms
were tested against hardware that way, in one morning, before anyone noticed
measuring was cheaper than guessing (`8c3cc7b`).

PINE is PCSX2's own instrumentation socket (`EnablePINE` in `PCSX2.ini`). With
it, any EE address can be read or written while the game runs, in one request/
reply round trip, no patch or reboot required. `Pine.read`, `Pine.write`,
`Pine.read_bytes`, `Pine.read_string` cover memory; `Pine.title`/`game_id`/
`version` identify what's on the other end. Two path details matter and were
wrong in an earlier version of this file's own docstring, which is worth
noting precisely because it shows a corrected tool can still ship a wrong
comment about itself for a while (`c35b3d6`): the socket falls back to `/tmp`
when `XDG_RUNTIME_DIR` is unset -- the normal case over SSH -- not to
`/run/user/<uid>`, and `PINESlot` is not TCP-only; any non-default slot
appends `.<slot>` to the socket filename.

**Reads are always safe. Writes are not.** A write lands in live EE RAM with
no validation, no bounds check, and no confirmation that the emulator on the
other end is running the game you think it is, or that nobody is in the
headset. Before any write: run the H-2 live-session check as its own command,
then call `Pine.title()`/`game_id()` to confirm the connection is the instance
you meant. Both cost one round trip.

Everything the roster-checksum saga eventually rested on came from this tool:
the console's own computed CRC read out of `gp-19396` live (`201db29`), the
list-record count at `gp+17660` that revealed the real bug behind a "roster
still out of date" report (also `201db29`), and the completion code at
`0x00560af4` that finally settled what a week of reasoning about the `news`
reply could not (see Part 2).

### `tools/fake_console.py` -- a client that checks, not just drives

Hardware gives one bit of feedback per boot: it works, or it shows a generic
error. Worse, the two-console case was untestable on real hardware entirely,
because every PCSX2 guest in Sockets mode reports the same address,
`192.0.2.100`, so two emulated consoles cannot dial each other.

`fake_console.py` speaks the client's half of the protocol -- login, room,
matchmaking -- well enough to drive the server through a real session. **The
assertions matter more than the driving.** Almost every protocol mistake this
project found was one the server itself could not see, because a real client
that disagrees does not complain; it goes quiet, or silently discards the
message. The `news` reply is the standing example: it was answered with the
wrong type for weeks, the exchange looked completed from the server's side,
and the console discarded every reply without a word (`2d18ff0`, and see the
full saga in Part 2). So `expect()` and `check_session()` don't just wait for
*a* reply, they hold it to what the real client requires: that a `news` reply
carries `new0`/`new1`/`new2` in the status word rather than the type; that
`+ses` names both `SELF` and `HOST` and they differ for exactly one player;
that `ADDR`/`FROM` are dotted quads and are the *observed* peer address rather
than the console's self-reported one; that `WHEN` is non-zero, or the record
is silently never delivered.

`--pair` is the matchmaking test proper: two clients, logged in independently,
paired by quickmatch, with the resulting `+ses` pair checked against every
rule above. `--spar` stands a bot in the queue so a *real* console has an
opponent; it needs `--pair-any` because a console's `KIND` is a CRC over its
own build and settings, so nothing else can reproduce the value it will send.
Read `run_spar`'s docstring before trusting what a spar run proves: it shows
the console sends `quik`, gets paired, and dials its peer on UDP 3658 -- it
does not and cannot play a game, because nothing here answers on 3658, and the
~10s timeout that follows is the expected end of the test, not a regression.

The tool itself had a bug worth knowing about: an earlier version handed the
first message of a batch to whoever was waiting and filed the rest straight
into `pushes`, which nothing consulted. Since the subscription pushes after
`sele` routinely arrive in the same TCP segment as the reply, a reply could be
silently lost depending on how the kernel happened to chunk the stream --
which read as an intermittent *server* bug and was pure test-client artifact
(`a637ec9`). Everything decoded now goes through one inbox, consumed in
arrival order.

### `tools/read_roster_checksum.py` -- reading a savestate when PINE isn't there

The pre-PINE fallback, kept because it is still sometimes the only option: a
PCSX2 savestate is a ZIP holding a raw 32 MB `eeMemory.bin`, so any EE address
is just a file offset. The one wrinkle is that current PCSX2 saves that member
with Zstandard (compression method 93), which Python's `zipfile` cannot
decompress on its own; rather than require a pip install on the rig, this
lifts the compressed bytes out by hand and shells out to the `zstd` binary,
streaming through a feeder thread so a 16 MB write doesn't deadlock against a
pipe buffer. Take the state *after* the console has reached the check you care
about -- before that, the slot holds whatever was last written into it, which
is easy to mistake for a result.

### The roster toolchain -- `madden_tdb.py`, `roster_checksum.py`, `build_roster.py`, `mark_roster.py`

`madden_tdb.py` is the format reader everything else sits on: an outer **TERF**
container (offsets relative to the *end* of its own directory block, not the
file) holding TDB databases, each a table directory of 4-character names
followed by bit-packed records. Two conventions bite anyone who doesn't know
them going in: 4-character codes are stored **byte-reversed** on disc and in
the executable (`'YALP'` is `PLAY`), and record fields are packed
least-significant-bit-first, both within a byte and within a field --
established empirically against a known-good Madden 08 sample by a sibling
project (`NCAA-Draft-Class-Editor`) and reused here rather than re-derived.

`roster_checksum.py` computes the CRC the console compares `CSUM` against:
zlib CRC-32 over 31 fixed columns per player (order is load-bearing -- it is
memory order, not alphabetical or logical order), seeded not from 0 or
`0xFFFFFFFF` but from the row count. Reach for this whenever the server needs
to announce a version the console will agree with; get any input to it wrong
(wrong file, wrong sort direction, wrong column order) and the output is a
plausible-looking wrong number, not an error.

`build_roster.py` builds two different things and only one of them can ever
reach a console: `--extract-member` pulls out a raw TDB (member 0 of
`template.dat`, 253,044 bytes on retail) -- **this is the only servable
payload**. Its other mode builds TERF *containers*, which is what the tool was
originally written to do and is still useful for inspecting or subsetting an
archive, but the console's loader (`0x004c9e90`) rejects anything whose first
word isn't the raw-TDB magic `0x08004244`. Every container this tool ever
built was, by construction, something the console would refuse on sight. Read
the docstring before choosing a mode.

`mark_roster.py` exists for one reason: an unedited roster payload is
byte-identical to the disc, so serving it and later finding the same bytes in
console memory proves nothing about whether an install actually happened. This
tool rewrites one player's surname in place -- fixed-width field, fixed-width
record, so the file length and every other offset survive untouched -- and
reseals the TDB's own per-block CRC-32 checksums (a *different* algorithm and
seed from the manifest's CRC: poly `0x04C11DB7`, MSB-first, init
`0xFFFFFFFF`, no final XOR, against the manifest's ordinary reflected
`zlib.crc32`). If the edited name shows up on a console's screen, nothing
else can explain it. **Read the printed hazard before running this against
anything that matters**: the install path deletes every table in the league
database before it opens the stream or checks the magic, with no rollback, so
a payload that fails validation partway leaves the database empty or
half-built.

### `recon/` -- the capture harness, and `mipsdis.py`

The harness is deliberately standard-library only, so it lands on the rig by
file copy with nothing to break mid-session (`1734c2f` is worth reading in
full: that choice is a convenience of how this repo deploys, not a rule
anyone has to defend elsewhere). `recon/dnsd.py` answers a title's DNS queries
and, by itself, enumerates every host it contacts. `recon/sinkd.py` accepts
and hexdumps whatever a redirected client sends, optionally answering with
canned bytes, and writes a JSONL transcript `recon/classify.py` can read back
to fingerprint the stack (GameSpy / EA-FESL / DNAS / TLS / plaintext) by
signature rather than by guesswork. `recon/tlssink.py` terminates TLS with a
self-signed certificate to answer the one question that decides serve-vs-patch
for an auth gate: does the client validate the certificate? `recon/pcapreader.py`
reads classic pcap (not pcapng) without scapy or Wireshark, and `recon/eaproto.py`
/ `recon/easerver.py` are the codec and a data-driven stand-in server for
Madden's own wire format, once it was known.

`recon/mipsdis.py` exists because the rig has no MIPS-capable binutils and no
capstone. It is deliberately small: enough to read control flow -- loads,
stores, arithmetic, branches, jumps -- and to answer "where is a result
tested", not to produce a faithful listing. Two things follow from that scope,
and both matter more than they look:

- **It does not decode `REGIMM` (opcode `0x01`: `bltz`/`bgez` and their `-al`
  variants).** An instruction in that family prints as `.word`, and has to be
  identified by context instead -- which is what happened when a client-side
  record check turned out to hinge on `bltz`, not `blez` (`b8be939`).
- It also has no entry for opcode `0x00` funct values it doesn't list, which
  is exactly the gap that produced the disassembler's worst bug (see
  `movz`/`movn` in Part 2).

Everything it *does* decode is there because a gap in it cost something.
Branch-likely (`beql`/`bnel`/`blezl`/`bgtzl`) and the unaligned load/stores
(`lwl`/`lwr`/`swl`/`swr`, `ldl`/`ldr`/`sdl`/`sdr`) were originally left as
`.word` and had to be hand-decoded on sight -- which is exactly where a wrong
answer creeps in, and did (see the BEQL entry in Part 2). `movz`/`movn`
(conditional move) and the R5900's three-operand `mult`/`multu`/`div`/`divu`
were added after each one produced a real misreading, documented in the
source comments next to the fix. `find_address_refs` locates the `lui`/
`addiu` (or `ori`) pair that materialises a 32-bit address -- the way MIPS
builds a reference to a string or a global -- adjusting the high half for a
negative low half, which is the usual reason a naive version of this search
finds nothing. `find_jal_targets` and `find_immediate` are the
cross-referencers: once a function or a constant is identified, these find
every caller or every comparison against it in one pass.

### `serve-madden.sh` and the single-instance lock

Four flags matter to starting this server and none of them fail loudly when
wrong: `--db` (default `backend.db`, a different and empty database -- every
account silently vanishes), `--port` (needs *both* 10000 and 10001, and
narrowing it gets the first contact refused with nothing logged), `--buddy-port`,
`--advertise-host` (must be a dotted quad; the client parses it octet by
octet). Every one of those mistakes produces the identical, useless
"an error happened when connecting to the server" on the console. Use the
script; don't reconstruct the invocation by hand, and always pass
`--transcript`, because the transcript is the only place these failures are
distinguishable from each other.

The script -- and, since `c35aae0`, the server itself -- takes an exclusive
`flock` on the port set before doing anything else. A second instance exits
immediately, naming the PID and full command line of whoever holds the lock,
rather than failing deep inside Python with `[Errno 98] Address already in
use`, which reads like a server bug. `REPLACE=1` takes over: it reads the
holder from the lock file, not from `pgrep`, stops every process it names
(not just the first), and waits for each to actually exit before proceeding.
This exists because it happened for real, more than once: see "stale
processes" and "`pgrep -f` self-matching" in Part 2.

### The test suites -- proof a fix stays fixed

`tests/test_recon.py`, `test_backend.py`, `test_matchmaking.py`,
`test_roster_checksum.py`, `test_build_roster.py` and `test_roster_delivery.py`
run offline: no sockets, no capture, no game data, so they run identically on
the rig and off it. The count for each file is quoted in the commit that last
touched it, and not as a vanity metric: a shrinking or stagnant count on a
commit that should add coverage is itself a signal, and the count can go
stale on its own -- `test_recon.py` is 105 tests today, and a claim that it
was still 55 (true only at `336b2b5`, the very first hardening pass) survived
long enough to need its own correction (`64cf021`).

The habit that makes them worth trusting is **mutation-checking**: after
writing a regression test for a defect, revert the fix and confirm the test
actually fails. `336b2b5` did this for all six defects in the first hardening
pass ("the sinkhole mutant hangs forever, which is precisely the bug"), and
it recurs through the project specifically because a test that would pass
regardless of the bug it claims to guard is worse than no test -- it is a
false sense of coverage. Part 2 catalogues a case where this habit was skipped
and the test shipped broken for a while (`AddressPropagationTests`).

## Part 2 -- what went wrong, and how it was caught

Each entry: what was believed, why it was believable, what it actually was,
and how it was caught. Citations are short commit hashes (`git show <hash>`
has the full message) and code addresses or file paths, so every claim here
can be checked independently of this document.

### Reading capture and disc data

**The GameSpy bet.** The working plan committed to before any capture assumed
Madden rode GameSpy and that OpenSpy/RetroSpy would hand over matchmaking
almost for free -- a reasonable prior; EA titles of this era often did. The
first real capture showed no GameSpy traffic anywhere in either title: no
backslash-delimited key/value framing, no 27900/28900 traffic, no
master-server hostnames (`6b9d90c`). This one isn't a bug so much as the
cleanest example in the project of a plausible prior overturned by the first
real measurement rather than by more thinking about it -- which is the whole
argument for capturing early.

**"ESPN 2K5 has no network strings at all."** A first ASCII `strings` pass over
the 12.6 MB main ELF found no hostname and no lobby vocabulary, and concluded
the network code had to live inside the unopened `VC_20919` VC-LZ containers
-- 4.3 GB that would need unpacking before anything could be searched
(`a9eb338`). It was an artifact of the search, not a property of the game: 2K5
stores nearly all of its strings as **UTF-16LE**, and the exact same file
yields `nfl2k5.games.espnvideogames.com` and the rest of its service
vocabulary immediately once searched that way (`1511bab`, `c9d5743`). Kept in
`docs/protocol-notes.md` as a named dead end with the generalization attached:
any "this title has no strings" finding on a Visual Concepts game should be
re-run as UTF-16LE before it's believed.

**A whole capture file attributed to the wrong title.** Both PS2 titles ran
back to back in one capture session; the run's label said "madden", so a
first pass attributed every packet in the file to Madden and reported that
2K5's run needed a re-capture (`6b9d90c`). Wrong -- both titles were in the one
file. PCSX2's own `emulog.txt` settled it independently and unambiguously, via
its `Name:`/`Serial:`/`ELF Loading:` boot lines with timestamps (`cef86e7`).
The rule that came out of it, now in `docs/emulator-capture.md`: attribute
findings by the emulog's boot lines, never by the capture filename or a run
label, because two titles played back to back land in one capture file every
time. The same session also caught 6,138 packets of a VR headset's own UDP
stream (WiVRn, port 9757) briefly mistaken for game protocol traffic, for the
same underlying reason -- the capture filter was `host <rig>`, which cannot
distinguish the title's traffic from everything else the rig happens to be
doing (`6b9d90c`).

**Port availability decided by parsing `ss` instead of by binding.** A
pre-flight check for "is this port already in use" matched `ss` output on the
port number alone, and refused to start on every single run -- `systemd-resolved`
holds `127.0.0.53:53` on every modern Ubuntu, a loopback-only bind that does
not conflict with binding `0.0.0.0:53` at all (`6999c63`). A check meant to
explain a failure was manufacturing one. Fixed by asking the only question
that actually answers it: try to bind the port, and consult `ss` only
afterward, to name the holder of a conflict the bind already proved.

### The disassembler

**BEQL at 0x13 instead of 0x14.** An earlier MIPS disassembler used on this
project encoded branch-likely opcodes one off from the MIPS IV manual, which
silently inverts the sense of every `beql`/`bnel`/etc. it prints -- the branch
still looks taken or not-taken, just backwards. `recon/mipsdis.py` replaced
it with the checked encodings (`33170bf`). The error did not stay confined to
that one tool: the same off-by-one had already been repeated back as settled
fact in the material used to brief whoever picked up this project's
disassembly work next, so fixing the code was necessary but not sufficient --
anyone still working from what they'd been told, rather than from a freshly
checked encoding table, would reintroduce it by hand. The mechanical fix that
actually holds is the one in `mipsdis.py` itself: the correct encoding, with a
comment naming the wrong one right next to it, so the mistake is visible to
the next reader rather than just absent.

**`movz`/`movn` printed as unconditional, and `mult` missing its R5900
third operand.** MIPS I's `mult`/`multu`/`div`/`divu` write only HI/LO; the
R5900 also writes the low result to `rd`. Printing only `rs, rt` hides that
write and makes an ordinary multiply-accumulate loop read as though it
discarded its product -- "sums digits" rather than "builds a number"
(`03c847c`, caught before it produced a wrong conclusion, this time). `movz`/
`movn` (conditional move) fell into the same unhandled-funct gap and printed
as `.word`, which was then hand-decoded as an ordinary move -- unconditional
-- rather than as the conditional write it is. That single misreading
produced two separate wrong analytical conclusions in this project: one
caught ten minutes later by the very next commit, and one that survived
long enough to be built on by a follow-up commit before it was retracted
(see "movn is conditional" and "the buffer-lifecycle install proof" below).
`recon/mipsdis.py` now prints
`rd` on the three-operand forms and treats `movz`/`movn` as the two-operand
conditional writes they are, with both fixes commented in place next to the
mistake they correct.

**"`movn` returns 2 outright" -- the DNAS caller decides nothing.** Patching
Madden's DNAS gate involved reading `0x00305278`, `movn s0, v1, v0`, as
though it unconditionally set the completion code to 2. It doesn't: `s0`
becomes 2 only when `DirtyDnasRelExit` returns non-zero on that path,
otherwise it stays 1 (`d10aabf`). The disassembler had no entry for that
funct code and printed `.word`; the gap was filled by assumption rather than
by decoding it -- "the exact failure the tool exists to prevent," in the
commit's own words. The same review also downgraded the confidence of the
whole patch: the poller's only caller never tests its return value at all, so
which of 1/2/3 means "proceed" is decided by the title's own script bytecode
and literally cannot be read out of the executable. The patch shipped anyway,
correctly labeled as an experiment with a well-located target rather than a
proof -- and it turned out to work (`a0d2997`).

**An SSLv2-format hello read as "not TLS".** The first probe that reached
Madden's DNAS gateway on port 443 reported "NOT TLS ... plaintext instead of
TLS". The console's hello was fine; the parser only recognized the modern
`0x16` record type and the console's opening byte was `0x80`, SSLv2 record
framing, which a 2004-era title uses even when the handshake underneath
intends TLS 1.0 (`321cb9b`). The tool's narrow parser produced a false
negative about the console, not a finding about it. Fixed by parsing the
legacy framing by hand (2-byte length with the high bit set, then message
type, version, three-byte cipher specs) rather than assuming one wire format.

### The `news` reply, in full -- an error chain worth reading end to end

This is the richest single thread in the project's history and worth tracing
as one story rather than four isolated bullets, because each step corrected
the one before it and each was wrong in a different way.

The console's `news` reply is not just a chat-adjacent message: it is how the
client learns the buddy service's address and the roster `DATE`/`CSUM` it
compares against its own. For weeks, every `news` reply this server sent was
silently discarded -- the request looked answered from the server's side, and
the console neither complained nor retried (`2d18ff0`). Diagnosing *why*
produced, in order:

1. **An agent-reported fact, trusted and built on without being checked.**
   `b2b6a68` wrapped `news` fields in an invented `new0` sub-block, reasoning
   from a 4CC walker at `0x0044f400`. The very next commit retracted it in
   full, and is the one that names why: that sub-block theory rested "on the
   strength of an agent listing `new0` among the selectors walked by
   `0x0044f400`. That listing is wrong and I built on it without checking."
   (`d95175f`). All nine callers of that walker are in the tournament module;
   `new0` appears nowhere in the executable as data, in either byte order --
   it exists only as the `lui`/`ori` immediate pair inside the client's own
   `NewsRequest` handler. There never was a sub-block layer in this protocol.
2. **A savestate reading treated as live state when it was freed stack.**
   The retraction in step 1 still reported, as a measured fact, that the
   reply's out-struct read `[0x00001ac0, 0, 0, 0]`. It doesn't mean anything:
   that struct lives on `NewsRequest`'s own stack frame, which had returned
   long before the savestate was taken, so the four words describe whatever
   reused that memory next, not the reply (`aafb7e0`). Withdrawn explicitly,
   "because it looked like evidence and would have been treated as a
   constraint... by whoever picked this up next."
3. **The actual mechanism**, finally read correctly: the second header word
   is a **status tag**, not a type or a transaction id, settled independently
   at two call sites (`0x0034eb20` and `0x004e1e00`, the latter comparing it
   against literal error tags `uusr`/`ingm`). The reply must be typed `news`
   -- matching the pending-queue's match-by-type rule -- with the roster
   category riding in the status word as `new0`/`new1`/`new2` (`33170bf`).

Two general points fall out of this chain and recur elsewhere in the
catalogue: an agent's claim is a claim, not a fact, until independently
checked against the artifact it claims to describe (this is the concrete
instance behind "agent-reported facts taken on trust" -- the same failure
mode also produced a wrong 4CC, `PVOR` for `POVR`, caught before it shipped
and pinned by `tests/test_roster_checksum.py::FieldList::test_order_is_the_wire_order`,
which asserts `POVR` is present and `PVOR` is not); and a savestate or a
memory read is only evidence about what wrote it, which the "buffer
lifecycle" entry below gets wrong in the opposite direction.

### The roster checksum and the roster install

**"Nothing else reads that slot."** The measurement patch that let the
console's own computed checksum be read out of a savestate stores it at
`gp-19396`. An early note claimed nothing else reads that address. False --
`0x0012a990` is a getter for the same word, called from `0x00354674`, which
serializes it into an outgoing buffer, so the same patch that made the value
measurable also made the console transmit a wrong checksum (`33170bf`). Now
recorded directly in `tools/read_roster_checksum.py`'s docstring rather than
left as a claim nobody re-checks.

**`0x0012a730` called the accumulator.** For several commits, the checksum's
running total was believed to be built at `0x0012a730` (`438dd47`, `9387a04`).
It isn't: that address is a varargs marshaller that hands the query VM a
format string and 31 pointers across a 124-byte buffer -- the format string,
not the marshaller, is the authority for which columns are hashed and in what
order. The real accumulator is `0x0039d7e8`, an ordinary zlib CRC-32 whose
256-entry table matches the reflected `0xEDB88320` polynomial exactly
(`97a78ba`, corrected in the record at `1511bab`). The practical cost of the
mislabeling was small -- the real work was still ahead -- but it is a clean
instance of assigning a role to a function because it was the obvious
suspect, not because its behavior was read.

**The ascending-sort statistic that was invented.** The checksum's query ends
`order by 'DIGP'` with no explicit direction. A comment justified assuming
ascending on the grounds that "every other query in the executable spells
`asc` explicitly." That statistic was fabricated, and it was also load-bearing
-- of 526 sort keys in the executable, 326 say `asc`, 68 say `desc`, 118 take
their direction from a runtime vararg, and 14, this query among them, omit it
entirely (`64cf021`). The conclusion (ascending is correct) was right; the
argument for it was fiction. The real proof sits at the parser: at
`0x004ce688`, `asc` (keyword 33) stores 0 at `0x004ce7c4`, `desc` stores 1 at
`0x004ce7e0`, and an omitted direction falls through to `0x004ce7a0`, which
stores the same 0 -- omitted is byte-identical to `asc`. This stayed
unresolved long enough to cost five checksum candidates tested against real
hardware (`8c3cc7b`) specifically because that branch is a `beql`, which the
disassembler was, at the time, still printing as `.word`.

**LEAG is `template.dat`, not `DB_TEAMS.DAT` -- right for the wrong reason.**
The checksum was computed by reading `DB_TEAMS.DAT` and assuming the runtime
"merges" its 232 members into the `LEAG` database the console actually
queries. It does not. `0x003b6c48` has exactly one caller, registering
`("template.dat", index 0)`, and that single member is what loads as `GAEL`
(`ad4a91c`, corrected in `docs/roster-checksum.md`). The checksum was right
anyway, because both files happen to carry the same shipped roster and both
yield the identical 1,743 filtered rows and `0x8108963c` -- which is exactly
why the wrong mechanism survived several commits without anyone noticing.
`docs/roster-checksum.md` keeps its own verdict on this one verbatim, because
it is worth repeating rather than paraphrasing: *"A conclusion that comes out
right for the wrong reason is the hardest kind to catch."*

**"393,216 bytes transfers completely."** A hardware run reported that a
393,216-byte roster payload transferred with zero interruptions while larger
payloads were refused mid-transfer, and read that as a discovered size
ceiling (`88635aa`). Wrong twice over: 393,216 bytes already exceeds the
real cap (253,044, the exact size of the servable member) and would have been
refused unread, and what "completed" was the server's own `sendall` call
returning once the *kernel* accepted the bytes into its send buffer -- which
says nothing about whether the console ever read them (`ad4a91c`). Now stated
directly in `tools/build_roster.py`'s docstring: distinguish what the server
observed itself do from what the client is known to have done, and prefer a
signal from the client's own side (the CRC check, the HTTP request it issues,
a byte read back out of its memory) whenever one exists.

**The install "proof" from a buffer's lifecycle.** A modified roster was
reported installed on a live console, on the strength of two memory words --
`gp+2696` and `gp+2700` -- both reading zero, interpreted as "the buffer was
allocated, filled, checksummed and freed, so the whole install path ran"
(commit before `181a7b3`; see also `d8faedf`). Every part of that argument was
wrong. Those two words are zero **in the ELF image itself**, at file offsets
`0x507178` and `0x50717c` -- zero/zero is simply the boot default, not a
measurement of anything. There is also a code path that allocates and frees
*without* installing (when `0x00306008` returns 0), which reaches the same
terminal state. And the install itself was assumed to be straight-line code
when it is not: `0x00352814`, `bne s1, zero, 0x00352834`, jumps *past* the
install at `0x00352828` whenever the status or CRC comparison is non-zero.
That comparison is branchless -- its result immediately gates a branch --
which is what got mistaken for "unconditional", explicitly named in the
retraction as "the exact misreading this project's own disassembler
docstring warns about" (`181a7b3`). Compounding all of it: the payload served
was byte-identical to the disc's own copy, so even a genuine install and an
ordinary boot would leave indistinguishable bytes in memory -- the test could
not have detected the thing it was meant to detect no matter how the memory
read out. `tools/mark_roster.py` exists entirely because of this entry: it
edits one player's name so an install becomes something a screen can show,
and the real proof followed four commits later -- twelve whole `PLAY` records
in live memory, byte-identical to the served payload and differing from the
disc by exactly the twelve edited bytes (`fb3b6d1`).

**`reseal()` validated by its own output.** `tools/mark_roster.py` recomputes
every block checksum in an edited TDB. An early version of that function
computed a data block's length as "everything up to the next table header".
The console's own checker uses a different rule -- the table's full declared
capacity, `field_count * 16 + record_bytes * max_records` -- and the two
coincide only because the retail file happens to be packed with no padding
between blocks (`c35b3d6`). The wrong rule reproduced every stored checksum
in the file and round-tripped an edit perfectly, which is precisely how a
rule can be validated entirely by its own output and still be wrong: nothing
about "it worked on the one file I have" distinguishes the real rule from a
coincidentally-equivalent one, and any file with padding, or an edited
`max_records`, would have broken it silently. Fixed by re-deriving the rule
from the two addresses that actually compute it in the checker
(`0x004c8ab0`, `0x004c8b54`) rather than from reproducing the retail file's
output.

### Transcripts, tests, and process hygiene

**`+ses` absent from every transcript, read as "never sent".** A review
scanned every capture, found zero `+ses` records, and concluded the server
had never sent one. The conclusion followed from the evidence and was wrong
about the world: `hub.push()` wrote straight to the socket and returned, and
only the request/reply path in `service.py` was ever recorded to the
transcript -- so every `+ses`, `+usr`, `+rom` and `+msg` this server had ever
sent was invisible to the very tool built to observe it (`f83e9fb`). It
mattered immediately: a console was introduced to a peer and dialled it on
UDP 3658, visible in the emulator log and in the stand-in client's own
output, while the transcript showed nothing at all. Pushes are now logged
with direction `"push"`, distinct from `"send"`, specifically so a reply and
an unsolicited message are never conflated in the record again.

**A test that could not fail.** `AddressPropagationTests` was written to
guard a real bug -- `+usr`'s address field must carry the observed TCP peer
address, not the console's self-reported (and, under PCSX2, always identical)
`192.0.2.100`. The original test only read `Session.observed_addr` off the
session object. It would have passed unchanged even if the caller that builds
the pushed record had gone back to reading `client_addr` -- exactly the
regression it existed to catch (`c35b3d6`). The fixed version, still in
`tests/test_backend.py`, builds a real `move` through the actual handler
chain, captures what would have gone out over the wire via a stand-in `Hub`,
and asserts on the decoded `+usr` record's `A` field -- its own docstring now
says plainly why the old version was worthless. The `news`-reply test had the
identical shape of failure earlier in the project: it asserted the reply's
*type* was `"news"`, which is exactly the value that was wrong, so the test
"encoded the bug rather than catching it" until the assertion moved to the
status tag instead (`2d18ff0`).

**`pgrep -f` matching itself.** The H-2 headset check and the backend's own
"is one already running" check both, at different points, used `pgrep -f`
against a command-line substring. Sent over SSH as `bash -c '...'`, the
*command performing the check* contains the same literal text it is searching
for, so it matches itself. This produced a false positive on the headset
check (`336b2b5`) and, worse, killed the live SSH session running the check
four separate times across the project's history (`14ca3f8`, `a674a3b`,
`c35aae0`), plus one false "already running" report where the check matched
its own invocation rather than a real backend (`c35aae0`). Fixed the same way
in both places: a bracket inside the pattern (`qemu-system-i38[6]`) makes the
search string unable to match itself, and the backend now identifies a
running instance by reading its `flock`-based lock file -- an authoritative
handle naming a PID -- rather than by pattern-matching process listings at
all.

**Stale processes answering a console that thinks it's talking to the new
one.** More than once, a hardware session investigated a wrong-looking reply
-- an empty roster manifest, a `dupl` room error -- as a protocol bug, when
the actual cause was a leftover server instance, backgrounded with `nohup`
and forgotten, still bound to the ports and still answering (`a674a3b`,
`39b5edc`). One case was worse than a simple duplicate: `REPLACE=1`'s guard
took the first PID out of a multi-line lock reading (`head -1`) and killed
only that one, leaving a second instance alive to keep answering while the
"replacement" server sat unable to bind (`39b5edc`). The server now takes an
exclusive `flock` on its port set as the very first thing it does, so two
instances on the same ports is impossible rather than merely inadvisable, and
`REPLACE=1` stops *every* process the lock names, waiting for each to
actually exit rather than assuming a signal worked.

### Also caught along the way

Shorter entries, each a real defect found by review or by hardware, kept
brief because the shape is one already covered above:

- A `ping` request handler was simply missing. Because the client matches
  replies to its pending queue strictly by type with no timeout eviction, one
  unanswered verb silently blocked *every later reply* on the connection for
  up to ten seconds at a time (`64cf021`).
- The server's keepalive `~png` handler echoed the client's echo back,
  which would have had the two sides pinging each other forever; the client
  never originates a keepalive itself, so the server has to (`108fdf0`).
- Roster list records were first encoded one field per line instead of one
  record per line, so a single roster entry became three malformed records;
  caught only by reading `gp+17660` (the record count) live over PINE
  (`6589583`). The fields within one record were then separated with a space,
  which the client's own field-copy routine treats as a terminator -- caught
  when the console's *own HTTP request line* echoed back the truncated URL it
  had parsed (`73ee685`). Both are the same lesson twice: the client's own
  behavior, read carefully, told us exactly what was wrong.
- A field-encoding bug let a value containing a newline forge a second field
  (`NAME="eve\nADMIN=1"` produced two records at the client), and a value
  containing a NUL silently truncated everything after it (`6959944`).
- A password check read `if row["PASS"] and presented != row["PASS"]`, so an
  account stored with an *empty* password accepted any password at all --
  the opposite of what an empty password should mean (`ff6a40f`).
- A transcript logged a re-encoding of each decoded message rather than the
  bytes that actually arrived. The two agree only while parsing is correct --
  exactly the condition a transcript exists to let you check independently
  (`ff6a40f`).
- `game_probe.sh` counted received client messages with `grep -c "recv"` over
  a log that renders direction as an arrow and never contains the word
  "recv" at all; it would report zero messages no matter how much the
  console sent (`14ca3f8`).
- Two live-socket test classes started a service on two ports but polled
  only the first for readiness before connecting to the second, so they
  failed roughly one run in three depending on bind order and scheduling
  (`b8f92b1`, and the same bug again in a different test class, `a1798c0`).
  "A test that fails intermittently is worse than no test: it trains you to
  re-run rather than read."

## Part 3 -- lessons, as rules

### Measure, don't reason, whenever measuring is possible

Five roster-checksum candidates were tested against real hardware before
anyone noticed that reasoning about the algorithm was cheaper than rebooting
for it (`8c3cc7b`) -- and then `tools/pine.py` made even that reasoning
unnecessary by turning "what value does the console actually hold right now"
into one socket round trip instead of a patch-and-savestate cycle. The
checksum's correctness, the news fix, the roster-count-of-one bug, and the
final roster install were all settled this way, not argued into place. The
mechanical form of this rule: before writing a paragraph about what an
address "must" contain or what a function "should" do, ask whether PINE (live)
or `read_roster_checksum.py` (a savestate) can just show you.

### A conclusion that comes out right for the wrong reason is the hardest kind to catch

It happened at least three times here: the `reseal()` data-block length rule
reproduced every stored checksum while using the wrong span, because the
retail file happens to be packed tight enough that the two rules coincide.
The belief that `DB_TEAMS.DAT`'s members "merge" into `LEAG` produced the
correct checksum for several commits, because `DB_TEAMS.DAT` and the actual
runtime source, `template.dat`, happen to carry the same shipped roster. And
the fabricated "every other query spells `asc` explicitly" statistic
supported the correct sort direction anyway, because ascending really was
right -- just not for that reason. In each case the output matched every
available check for a while, and what eventually surfaced the error was
*not* a failing test -- it was independently re-deriving the mechanism from
the code that actually enforces it (the checker's own length formula, the
one real caller of the container loader, the parser's own `asc`/`desc`
branch) rather than continuing to trust an answer that merely reproduced
known-good output. Treat "it matches on the one file I have" as weak
evidence, and stronger the more differently-shaped that file is from what
you're deriving.

### "Sent" is not "accepted". Keep the two words apart

`sendall()` returning means the kernel took the bytes, not that the console
read them -- that distinction alone produced the "393,216 bytes transfers
completely" error. A `news` reply landing in the client's receive buffer is
not the same as the client's parser accepting it as the type it's waiting on
-- that distinction is the entire `news` saga. And a payload byte-identical
to the disc means "installed" and "never touched" are indistinguishable after
the fact, no matter how much process evidence (allocated, filled,
checksummed, freed) accumulates for the path in between -- which is why
`mark_roster.py` exists at all. The mechanical remedy is the same in every
case: prefer a signal that can only exist if the far side actually acted on
what you sent -- a value read back out of live memory, a name that shows up
on screen, a request the client only issues after successfully parsing your
last reply -- over any signal generated on your own side of the wire.

### Absence in a log is not absence in the world; know what your logging omits

Zero `+ses` records in a transcript meant "pushes aren't logged", not "pushes
weren't sent" (`f83e9fb`). A DNS responder's log line silently dropped the
one field (`qname`) that made it useful, so two different hostnames logged
identically and a whole session's hostname list was unrecoverable (`650e1f0`).
Three distinct unanswered requests, each timing out on its own schedule,
looked at first like one message being retried three times, because nothing
distinguished "a fresh request" from "the same request again" in the read
(`1511bab`). Before treating an absence as a finding, check what the
recording path is actually wired to capture -- and prefer fixing the logging
gap on the spot (as `f83e9fb` does, tagging pushes with their own direction)
over reasoning carefully about what an incomplete log implies.

### A test validated only by its own output proves nothing

`AddressPropagationTests` read a property that the buggy code path never
touched, so it would have stayed green through the exact regression it was
named for (`c35b3d6`). A `news`-reply test that asserted the reply's type was
`"news"` was asserting the bug (`2d18ff0`). The general check: revert the fix
the test claims to protect and confirm the test fails. `336b2b5` made this
routine rather than occasional -- six defects, six regression tests, each
proven to fail against the un-fixed code before being trusted -- and it is
worth doing for every test that guards something a client can silently
ignore rather than loudly reject, because that is exactly the class of bug a
weak test is most likely to miss.

### Ask reviewers to refute, not confirm

The best single catch in this project's history came from a round where five
reviewers were explicitly told to find reasons a set of claims were wrong,
not to check that they were plausible (`64cf021`) -- between them: one live
bug, one completely unhandled message type, one fabricated statistic, and a
long list of stale documentation. A separate adversarial pass went line by
line through every comment, doc and test *against the artifacts* -- the
bytes, the addresses, the actual transcripts -- rather than re-reading the
prose for internal consistency, and it is the pass that caught
`AddressPropagationTests` and the `reseal()` span rule (`c35b3d6`). And when
two independent readings of the same reply format disagreed outright, the
resolution was not to average them or defer to whichever sounded more
confident -- it was to check the disagreement directly against the client's
own parser (`6570473`). What made all three effective was the same thing:
each reviewer was pointed at a specific, checkable artifact and asked to find
where a claim and that artifact disagreed, rather than asked for a general
opinion on the work.

---

None of this makes the project's conclusions untrustworthy -- most of what's
in `docs/protocol-notes.md` and `docs/roster-checksum.md` today is confirmed
against a live console, a savestate, or the executable directly, and says so.
It means the confirmed parts earned that status by surviving exactly the kind
of check described above, and the parts that haven't yet been checked that
way are marked as such rather than written with the same confidence. Do the
same for whatever you add next.

## Part 4 -- what the tests do and do not cover

A coverage audit on 2026-08-01 found roughly 2,000 lines with no test at all.
Two were closed because of what they underwrite rather than their size:

**`tools/build_year_roster.py`** produced the roster that ships on the USB kit.
Its failure mode is not a crash but a file the console cheerfully installs with
the wrong men on the wrong teams -- or with a geometry that fails the checksum
*after* the install path has already wiped the league database. Now covered by
`tests/test_build_year_roster.py` against a synthetic TDB, since no game data
belongs in this repository.

**`recon/mipsdis.py`** underwrites every address in every document here. A bug
in it produces a plausible listing, and the wrong conclusion is then written
down as fact and built on -- which has already happened once, when a
disassembler written by another tool had BEQL at 0x13 instead of 0x14.
`tests/test_mipsdis.py` pins the R5900 traps: branch-likely encodings,
conditional moves, the three-operand `mult`, sign-extended branch offsets, and
the `lui`/`addiu` high-half adjustment.

Both suites were checked by mutation rather than by passing: reintroducing the
0x13 bug fails three tests, trusting the scraped `tgId` over the game's TEAM
table fails two, and dropping the `PWGT` −160 offset fails two.

The remaining four zero-coverage modules were closed the same day, along with
`backend/__main__.py`, which was at 0% while holding every guard that stops a
misconfigured server from looking like a working one.

Measured with `coverage.py`, not by counting imports -- a module imported by one
test is not a module that is tested:

    python3 -m coverage run --source=backend,tools,recon -m unittest discover -s tests
    python3 -m coverage report --sort=cover

**794 tests, 90% of statements.** Every module is above 80%; `protocol.py` is
at 100%.

Six bugs surfaced from writing those tests rather than from reading the code:

* `recon/easerver.py` called `_reply_for`, which does not exist -- the function
  is `_replies_for` -- so **the replay server died with `NameError` on the
  first message any client sent it**. The call site was stale in shape as well
  as name: it treated the result as a single reply when a list is returned, and
  the list is the point (this protocol needs a follow-up push, because the
  server both answers and volunteers, and a client can be waiting on the second
  message). Nothing caught it because nothing had ever driven a message through
  that path in a test.

* `read_roster_checksum.py` rejected the last word of EE RAM (`< EE_SIZE - 4`
  where the last valid address *is* `EE_SIZE - 4`), and leaked a `zstd`
  subprocess on every short read.
* `patch_iso_roster.py` crashed in its summary line, which sits outside every
  `try` -- so a roster whose tables it could not walk produced a traceback
  *after* the image had already been written and verified. A completed write
  reported as a failure invites running it again.
* `backend/limits.py` validated a rate/burst pair only when a bucket was
  built, and buckets are built per connection -- so `--rate 20 --rate-burst 0`
  started cleanly and then killed every client as it arrived. The CLI guard
  written to catch it could not fire.
* `backend/__main__.py` had a stray conditional expression wrapping a `print`,
  which suppressed a roster diagnostic whenever `--roster-crc` was passed.

### Testing a loop that runs until Ctrl-C

`sinkd.serve` and `easerver.serve` end in `while True: time.sleep(3600)`. The
tests swap the module's `time` for a proxy whose `sleep` is a rendezvous: the
test waits until the listeners are up, drives traffic through them, then
releases the proxy, which raises `KeyboardInterrupt` and lets the real shutdown
path run. Only the module under test sees the substitution, so nothing else in
the suite is affected by a patched clock.

`tlssink.serve` blocks in `accept()` instead, and there is no equivalent way to
interrupt that from another thread, so its handler is tested directly. That is
why it sits at 80% while the other two are at 90%.

Worth recording, because two tests were written against it before it was
checked: on Linux, closing a socket that another thread is already blocked in
`accept()` on does **not** wake it. Asserting that a loop exits on close is
testing the kernel, not the loop. Passing an already-closed socket tests the
same branch and is deterministic.

### Still uncovered, and why

What remains is the last stretch of `backend/__main__.py` -- the call into
`serve_forever` itself -- and `tlssink.serve`'s accept loop.

## Part 5 -- mutation testing

Coverage records that a line ran. It does not record that anything depended on
the result, and this project has shipped two defects that were fully covered
and still wrong: a rate-limit guard that could never fire, and a peak gauge
wired to the wrong scope. Both had passing tests over the exact lines.

`tools/mutate.py` closes that gap. Change the source in a way that must alter
behaviour, run the tests, and see whether they fail. A mutant that survives is
a behaviour nothing checks.

    python3 tools/mutate.py --regressions          # the shipped defects
    python3 tools/mutate.py --file backend/limits.py

**Regression mode replays the defects this project actually shipped** -- BEQL
at 0x13, the roster keyed off the scraped `tgId`, the peak gauge summed across
connections, the replay server's missing function, twelve in all. Each must be
killed. This is the mode worth running often, because it is a regression suite
for the *tests*: it pins the ones written to catch a real bug so they cannot
quietly stop catching it.

**Generated mode** rewrites the syntax tree -- comparison operators, boolean
connectives, integer constants, returned booleans -- and reports survivors.
Slower and noisier; survivors need reading rather than fixing, because some are
equivalent mutants where the change genuinely cannot be observed.

Running only the test modules that exercise a file is what makes this usable at
all: the full suite takes minutes, so `TEST_MODULES` maps each source to its
tests and a run takes seconds. A file missing from that map falls back to the
whole suite, which is correct and slow.

### What the first run found

Twelve regressions, ten killed. Both survivors were real:

* **`eager-rate-validation` survived.** Deleting the eager `TokenBucket` checks
  from `RateLimiter.__init__` left the whole suite passing -- and that eager
  check *is* the fix for the "guard that could never fire" bug. Nothing
  constructed an invalid limiter. The mutation suite found a hole in the fix
  written to close a hole.
* **`news-status-tag` was stale**, naming a line that had since been reworded.
  It applied nothing and reported SURVIVED: a false alarm indistinguishable
  from a real gap. `tests/test_mutate.py` now checks every catalogue entry
  against its source in a tenth of a second, so staleness is caught by the
  suite rather than after a long mutation run.

A generated pass over `backend/metrics.py` then found three more: nothing
asserted the HTTP status code (any 2xx would have passed, and a scraper wants
200), nothing pinned the documented default port, and nothing checked that the
endpoint's threads are daemons -- a non-daemon metrics server turns a clean
shutdown into a hang whose cause is invisible, because the game service has
already stopped answering.

### The bytecode cache will poison the repository if you let it

The sharpest thing this exercise turned up was in the tool itself, and it is
worth knowing about far beyond mutation testing.

Restoring the source is not enough. Python decides a `.pyc` is current by
comparing the source's `(mtime, size)` -- and every mutation here replaces text
with text of the *same length*, restored within the *same second*. Both halves
of that check still match, so the next import gets the mutant's bytecode out of
`__pycache__` while the file on disk is correct.

The symptom was three tests failing in `recon/mipsdis.py` against a file that
was byte-for-byte identical to HEAD, with a clean `git status`. Every instinct
says look at the diff, and the diff is empty. Deleting `__pycache__` fixed it
instantly and explained nothing until the `(mtime, size)` rule was recalled.

So every write goes through `write_source`, which drops the cached bytecode and
calls `importlib.invalidate_caches()`, and `_Restorer` does the same and then
*verifies* the bytes match what it read. Two tests pin it.

The general lesson: **any tool that rewrites source in place and then runs
something has to invalidate the bytecode cache**, and a same-length edit inside
one second is exactly the case where the cache silently lies.

### Safety

The tool edits files in place. Every path restores the original in a `finally`
and again through `atexit`, and it refuses to start if the target has
uncommitted changes -- a crash mid-run would otherwise take those edits with
it. Never point it at a working tree you have not committed, and never run two
mutation passes at once.

Beyond code coverage, three categories no test here can reach:

* **Never exercised by a console.** Chat end to end, `quik`/`chal`/`+ses`, the
  peer link, and the verb stubs. Console-verified today is login, the `news`
  manifest, the HTTP download and the install -- nothing further.
* **Load-bearing assumptions read rather than observed.** The idle timeout
  rests on "the client echoes every `~png`", which comes from `0x00448C58` and
  not from a transcript. Whether the console closes its `@dir` connection is
  likewise unestablished. Both would produce disconnections of real players if
  wrong, which is why neither earns a ban strike.
* **Wrong-scope wiring.** Two of the six defects found in the same review were
  code that did exactly what it said while connected to the wrong object. No
  unit test finds that; only reading the wiring does.
