# Protocol notes (living document)

Fill these in during capture. One section per title+platform. The goal of each
is a **connection state machine**: what the client contacts, in what order, and
where it stalls without a valid server response.

Template per endpoint:

- **hostname(s)** resolved
- **port / transport** (tcp/udp)
- **stack** (gamespy / ea-fesl / theater / 2k-proprietary / dnas / http / tls)
- **first client bytes** (hex + ascii)
- **what unblocks the next step** (what the client wants back)
- **blocked?** (auth wall, encryption, DNAS, etc.)

---

## ESPN NFL 2K5 — Xbox (Xbox Live)

_status: not captured yet_

- Insignia support for this title: **unknown — confirm.**
- Expect IPsec on the wire; plan for the socket-boundary hook for plaintext.

## ESPN NFL 2K5 — PS2 (DNAS + 2K servers)

_status: **captured 2026-07-30** (`SLUS-20919`, CRC `42F9D5AF`), same session as
Madden, PCSX2 Sockets mode._

**Hostnames resolved:**

| Hostname | Type | Note |
|---|---|---|
| `gate1.us.dnas.playstation.org` | A | Sony DNAS auth gateway |
| `100.2.0.192.in-addr.arpa` | PTR | **not a server** -- see below |

**It resolved no 2K or ESPN hostname at all.** Its only forward lookup was the
DNAS gateway. Then TCP/443, refused, retry. The title never reveals where its
game servers live, because it does not get that far.

The PTR is an artifact, not a finding: PCSX2's `InterceptDHCP` assigns the
virtual PS2 **192.0.2.100/24** (gateway `192.0.2.1`) from TEST-NET-1, regardless
of the `PS2IP` setting, which it ignores. `100.2.0.192.in-addr.arpa` is simply
the title reverse-resolving **its own address**. PCSX2 *does* honour the
configured DNS (`DHCP: DNS 192.168.68.85` in its log).

## Madden NFL 2004 — Xbox

_status: not captured yet_

- GameSpy vs EA-proprietary: **unknown — this is the key question for slice choice.**

## Madden NFL 2004 — PS2

_status: **captured 2026-07-30** (`SLUS-20752`, CRC `14F8B841`), PCSX2
Sockets mode, DNS redirected to the rig._

Loads a separate network module, `cdrom0:\NETGUI\NTGUI.ELF` (CRC `2B77CEE1`),
when entering online. That ELF is where the network code lives and is the
natural disassembly target once static analysis starts.

**Hostnames resolved** (in order):

| Hostname | Type | Hits |
|---|---|---|
| `ps2madden04.ea.com` | A | 2 |
| `gate1.us.dnas.playstation.org` | A | 5 |
| `100.2.0.192.in-addr.arpa` | PTR | 2 |

**What it did next.** With both names pointed at the rig, the title opened
**TCP/443** five times (source ports 42996, 41358) and retried. Nothing was
listening, so each attempt was refused and no payload was captured. The
repeated DNAS lookups interleaved with those retries.

**Reading.** This is **not GameSpy** — no `\gamename\` framing, no 27900/28900
traffic, and the master-server hostnames are absent. It is EA's own service
(`ps2madden04.ea.com`), gated behind **Sony DNAS**
(`gate1.us.dnas.playstation.org`) on TLS/443. The working bet that Madden would
ride GameSpy and inherit OpenSpy for free is **overturned**.

The title never reached EA's game service: it stalls at DNAS. That makes DNAS
the *first* wall, not a later one -- which matches the roadmap's call that
Phase 2 carries the highest risk, and moves it to the front of the queue.

**Open:** the 443 payload is unknown, because nothing answered. Next run should
sinkhole 443 to capture the ClientHello and confirm TLS plus any SNI.

**Both titles were captured in one session**, Madden first (t=12s-267s in the
emulog) then 2K5 (t=288s onward). Attribute by PCSX2's own boot lines, not by
the capture filename -- the label on that run says madden for both.

**Noise warning for whoever reads the pcap.** The capture filter was
`host <rig>`, which also caught the rig's own browsing, package updates, the SSH
session, and — at 6138 packets — the WiVRn VR headset stream on UDP/9757. That
stream is *not* game traffic; it was briefly mistaken for the title's protocol.
capture.sh now excludes those ports by default.

---

## Static analysis of the discs (2026-07-30)

No emulator needed; read-only. Both ISOs listed with `xorriso`, key files
extracted, `strings` run per file rather than over the whole 4 GB image.

### Madden NFL 2004 — EA DirtySDK, symbols intact

| Fact | Evidence |
|---|---|
| Network SDK is **EA DirtySock (DirtySDK)** | `Dirtysock`, `DirtyClut` in `SLUS_207.52` |
| DNAS is wrapped by EA, not called raw | `DirtyDnasRelInit`, `DirtyDnasRelAuthStart`, `DirtyDnasRelUpdateDnas`, `DirtyDnasRelUpdateHttp`, `DirtyDnasRelExit` |
| **Two hooks worth their weight in gold** | `DirtyDnasRelSimError` (simulate an error) and `DirtyDnasRelSetProxy` (redirect) |
| Sony DNAS API used underneath | `sceDNAS2AuthNetStart`, `sceDNAS2AuthGetUniqueID`, `sceDNAS2AuthDataDownload`, `sceDNAS2AuthInstall` |
| Server hostname is **stored literally** | `ps2madden04.ea.com` in `SLUS_207.52` |
| DNAS module ships as a disc file | `/DNAS270.IMG` (251 KB) |
| Protocol vocabulary (4-char tokens, FESL family) | `AUTH`, `CHAT`, `DISC`, `GAME`, `GAMEID`, `PING`, `ROOM`, `STAT`, `STATE`, `STATUS`, `USER`, `USERNAME` |
| ~~Other EA host seen~~ | ~~`demangler01.pogo.com`~~ — **retracted 2026-07-31.** Not present in `SLUS_207.52`, `NTGUI.ELF`, `ONLINE.DAT` or 2K5's ELF; `demangler`, `pogo`, `NetGame`, `CommUDP` and `ProtoMangle` all return zero hits. Do not build a NAT-traversal theory on it. |

`ROOM` + `GAME` + `USER` is a room-based lobby model, and four-character
uppercase tokens are the shape of EA's FESL-family protocols. That matters:
unlike GameSpy, this is a *documented family* with prior reverse-engineering
work on later EA titles, so the "reusable prior art" hope is not entirely dead —
it just moved from GameSpy to DirtySDK.

`/DATA/ONLINE.DAT` is almost entirely EULA text, plus `bio_gethostbyname`.
`/NETGUI/NTGUI.ELF` is Sony's stock network-config GUI, not EA code.

### ESPN NFL 2K5 — not hidden at all; the scan was wrong

**Corrected 2026-07-31. The earlier conclusion in this section was false and is
kept here only so the mistake is not repeated.**

It used to read: "the main ELF contains no hostnames and no lobby vocabulary at
all", and concluded the network code must live inside the `/VC_20919/0`..`/4`
packed containers, ~4.3 GB of VC-LZ that would need unpacking before anything
could be found.

That was an artefact of the search, not a property of the game. **2K5 stores
nearly all of its strings as UTF-16LE**, and an ASCII scan steps straight over
them. Searching the same 12.6 MB ELF for UTF-16LE finds everything immediately:

| String | Offset |
|---|---|
| `nfl2k5.games.espnvideogames.com` | `0x93b166` |
| `Locating Servers.` | `0x93b1a6` |
| `The ESPN VIDEOGAMES service is down` | `0x93ae6a` |
| `Could not connect to the Leaderboard server` | `0x97257e` |
| `ESPN Messenger` | `0x971b94` |

So the networking is in the main executable, reachable without touching the
containers at all. Note also that `libdnas2`, `2.71` and `sceDNAS` do **not**
appear as literals — the "libdnas2 2.71" attribution comes from the separate
`DNAS271.IMG` module, not from strings in the ELF.

There is no DirtySDK here: `DirtySDK`, `DirtySock`, `DirtyDnas`, `ProtoSSL` and
`NetConn` all have zero occurrences. None of Madden's protocol work transfers.

`/VC_20919/DATA/DNAS271.IMG` (269 KB) is the DNAS module, version 2.7.1.

**Lesson worth generalising:** any "this title has no strings" finding on a
Visual Concepts game should be re-run as UTF-16LE before it is believed.

### Both discs

DNAS ships **as a file on the disc** in both cases, so it is extractable and
analysable offline. Error strings recovered: "The authentication has failed.",
"A network authentication system error has occurred.", "Connection to the
network authentication server has timed out."

---

## Madden DNAS gate — located, patched, unproven (2026-07-30)

Static, from `SLUS_207.52`. The main ELF embeds a relocatable module whose
string table exports exactly three names, so `DirtyDnasRel` is DirtyDnas
**Rel**ocatable. The loader at `0x004f2270` resolves them and stores the
pointers at `0x00574248` / `0x0057424c` / `0x00574250`; thin trampolines at
`0x004f2338` (Init), `0x004f2360` (Updt) and `0x004f2388` (Exit) call through.

`Updt`'s trampoline has exactly one caller — the DNAS poller at `0x00305238`,
which returns **1, 2 or 3**:

| Condition | Result |
|---|---|
| DNAS-active flag (`-3592(gp)`) is 0 | 1 |
| `Updt` returns non-zero | 3 |
| `Updt` returns 0 → `Exit` teardown | 2 **if `Exit` returned non-zero**, else 1 |

With the servers dead it is 3 forever, which is what the endless
"Authenticating DNAS data" screen is.

**What is not known, and cannot be settled from this executable.** The poller's
only caller (`0x00353eb0`) never tests the value: it does `sw v0, 0(s2)` and
jumps to a shared exit — one entry in a dispatch table of script-callable
functions. Which of 1/2/3 means "proceed" is decided by the game's script. The
patch is therefore an experiment with a well-located target, not a proof.

`patches/14F8B841.pnach` carries three variants: force the `Updt` test
unconditional (A, active), return 2 immediately (B), or report 1 as though DNAS
was never enabled (C). All three encodings were re-decoded against the ELF and
every branch keeps its original target.

**Correction worth keeping.** `0x00305278` is `movn s0, v1, v0` — *conditional*.
An earlier note claimed the completion path always returns 2; it returns 2 only
when `Exit` returns non-zero. The disassembler had no entry for funct `0x0b` and
printed `.word`, and the gap was filled by assumption rather than checked. It
now decodes `movz`/`movn`, the 64-bit forms and `ld`/`sd`.

---

## BREAKTHROUGH — Madden past DNAS, EA game service found (2026-07-30)

The pnach Variant A works. With `patch=1,EE,00305258,word,10000003` applied at
ELF load, Madden NFL 2004 no longer touches DNAS at all:

| | before the patch | after |
|---|---|---|
| `gate1.us.dnas.playstation.org` | resolved, repeatedly | **never looked up** |
| TCP/443 (DNAS over TLS) | 5 attempts, all refused | **none** |
| `ps2madden04.ea.com` | resolved but never dialled | resolved |
| EA game service | never reached | **TCP port 10000** |

```
DEV9: DNS: Q0 Name ps2madden04.ea.com
DEV9: Socket: Creating New TCP Connection to 10000
DEV9: TCP: Recv error: 111          <- ECONNREFUSED, nothing listening yet
```

**EA's game service for this title is TCP/10000 on `ps2madden04.ea.com`.** That
is the endpoint a revival has to implement, and its protocol is the next thing
to capture -- `game_probe.sh` sinkholes it.

Note the patch removes the DNAS *lookup*, not merely its result: the title
skips the gateway entirely rather than trying and being satisfied. No DNAS
ticket is obtained, so if TCP/10000 validates one, that is handled server-side.

### Getting here required a restart, and that hid the result once

PCSX2 applies patches at **ELF load**. The pnach was written 20 minutes after
the last boot, so the first "patched" run tested unpatched code and looked like
a failure. Confirm a patch is live by finding `Patch: Disabling any bundled
'patches.zip' patches` at a timestamp *after* the ELF load line, in the current
session's emulog.

### The DNAS handshake, for the record

Captured before the patch made it moot, and worth keeping if the 2K5 route ever
needs a served DNAS endpoint:

```
80 64 01 03 01 00 4b 00 00 00 10 ...
^^^^^ SSLv2 record framing      ^^ 16-byte challenge
      ^^ CLIENT-HELLO
         ^^^^^ version 0x0301 = TLS 1.0
```

25 cipher specs including `0x000a` (3DES-SHA), `0x0005`/`0x0004` (RC4),
`0x0009` (DES) and the `0x0062`-`0x0066` export set -- that range is OpenSSL's
`export1024` group, so the client is an **OpenSSL 0.9.x** stack asking for
TLS 1.0 behind a legacy hello. Ordinary TLS above the framing, which is why
translating the hello and relaying through stdlib `ssl` would be viable if
serving DNAS ever becomes necessary.

---

## EA's game protocol, captured (2026-07-30)

With DNAS bypassed, Madden connects to `ps2madden04.ea.com` **TCP/10000** and
sends this as its opening message — the first time this project has seen the
protocol it exists to reconstruct:

```
40 64 69 72  00 00 00 00  00 00 00 57
"@dir"        txn = 0       length = 87

PROD=MADDEN-PS2-2004
VERS="PS2/MS5-Jun 17 2003"
LANG=en
SLUS=BASLUS-20752
\0
```

### Framing (established from the wire)

| Offset | Size | Meaning |
|---|---|---|
| 0 | 4 | message type, four ASCII bytes |
| 4 | 4 | **status / error tag**, four ASCII bytes; 0 means success |
| 8 | 4 | length, big-endian, **counting the header** |
| 12 | .. | `KEY=VALUE` lines, `\n` separated, NUL-terminated |

87 = 12 + 75, verified byte-exact against the capture. A reader that forgets
the header is included in the length desynchronises on the next message.
Values are quoted only when they contain spaces (`VERS`), and the quotes are
not part of the value.

This is EA's **FESL-family** framing, which the static analysis predicted from
`Dirtysock` plus the four-character token vocabulary (`AUTH`, `CHAT`, `GAME`,
`ROOM`, `USER`) found in `SLUS_207.52`. `@dir` is a **directory lookup**: the
client asking where to go next, which is why it is the first thing sent and why
nothing else follows until it is answered.

`recon/eaproto.py` implements the codec; it round-trips the captured message to
exactly 87 bytes. `split_stream` keeps a partial trailer, because TCP does not
preserve message boundaries.

### The reply, recovered from the client's own parser

The client contains the parser, so the reply format is readable without ever
seeing a server. Recovered by disassembly and independently cross-checked:

**The reply is typed `@dir`** — the server echoes the request type. `rdir` is
*not* a wire type; it is the client's internal state token at `conn+12` meaning
"awaiting directory reply". Both are checked, in sequence, at `0x00448d18`:

```
lui v0,0x7264 / ori v0,0x6972   -> 'rdir'
bne  v1, v0                      require STATE == 'rdir'
lui v0,0x4064 / ori v0,0x6972   -> '@dir'
bnel v1, v0                      require RECEIVED TYPE == '@dir'
```

Fields the reply parser reads (block `0x00448d40`-`0x00448ee0`), all via
`TagFieldFind` at `0x0044acc8`:

| Key | vaddr | Converter | Stored | Notes |
|---|---|---|---|---|
| `DIRECT` | `0x608828` | presence test | — | if present, **ADDR/PORT are skipped** |
| `ADDR` | `0x6087a0` | `0x44c628` | `conn+948` | **dotted quad**, default 0 |
| `PORT` | `0x608830` | `0x44c550` | `conn+944` | decimal |
| `SESS` | `0x608838` | `0x44c550` | `conn+1208` | decimal, default 0 |
| `MASK` | `0x6087f0` | `0x44c9b0` | `conn+1212` | string, 64 bytes, default "" |
| `DOWN` | `0x608840` | `0x44c9b0` | `conn+952` | **only read when ADDR==0**; sets "server down" flag `0x400` |

`0x44c628` is a dotted-quad IPv4 parser, not an integer one — it compares
against 46 (`'.'`) and shifts the accumulator left 8 bits per octet, so
**`ADDR` must be written `192.168.68.85`**, not hex and not decimal. `0x44c550`
is an ordinary atoi. That distinction is the difference between a working
redirect and a silent failure.

**`@dir` is a redirector.** With `ADDR` non-zero the client sets state `conn`,
logs `"connecting to %08x:%d"`, reconnects to `ADDR:PORT` and continues there.
So whatever port the reply names must also be listening, or the redirect
dead-ends in a refused connection that looks like a rejected reply.

Minimal reply, evidenced: `ADDR` and `PORT`. Everything else has an explicit
default. `SESS` is consumed later during login (`0x004f7110`), so it is
plausibly required downstream — not yet proven.

### The complete message vocabulary

Message types are **32-bit big-endian immediates built by `lui`/`ori`**, never
strings — which is why `@dir` cannot be found by grepping the binary. Read out
of data they appear byte-reversed (`erc#` in a hexdump is `#cre` on the wire);
that inversion is the single easiest way to misread this protocol.

The send function is `0x00453220(sess, type, txid, payload, len|-1)`; it
computes `strlen(payload) + 1 + 12`, confirming the framing independently.

**Client -> server:** `@dir` `sele` `addr` `skey` `#cre` `#new` `#joi` `#lea`
`#sta` `#sea` `#mem` `#dat`, plus `PING` in a separate subsystem.

**Server -> client:** `~png` `@dir` `auth` `acct` `snap` `sele` `pers` `move`
`room`, and the push-only subscriptions `+ses` `+msg` `+who` `+rom` `+pop`
`+usr` `+rnk` `+snp` (these never reach the send function).

**Record types** inside a buffer of concatenated frames: `*dir` `*usr` `user`
`strt` `game` `gam2` `roll` `crnd`. Four of them sit in a contiguous table at
`0x005f60d8` — `"*dir*usrstrtgame"` — used as the value of key `FILT` on a
`#sea` subscription.

**The second header word is a status tag, not a transaction id.**

This was recorded as a transaction id for a long while, on the strength of the
`~png` handler (`0x00448ca4`) passing `a1 = received type` and `a2 = received
word` back to the send function. It looked like an echoed correlator, and it was
zero in every captured exchange — because every captured exchange succeeded.

It is a **four-character status tag, 0 for success**. Settled at `0x004e1e68`,
where the challenge reply callback loads word 2 and aborts on any non-zero
value, and at `0x004e1e00`, which compares that same word against the literals
`uusr` and `ingm`. Those are error names, not identifiers.

So replies echo the request's *type*, and set word 2 to 0 or to an error tag.
There is no correlator in the header at all — requests are matched to replies
positionally, against the head of a pending queue (`0x00446ce0`).

**That queue is why every request must be answered.** Nothing pops the head on a
timeout, so one unanswered message blocks every later reply on that connection.
A verb we simply do not handle is not a cosmetic gap; it wedges the session.

**Replies use the request's type.** `@dir` is answered with `@dir`. The one
documented exception is `news`, whose reply is tagged `new0`/`new1`/`new2`
according to its `NAME` parameter — see `docs/roster-checksum.md`.

### Error vocabulary

`.data` at `0x0056d4dc` holds sixteen 12-byte triples of
`(request type, internal tag, wire tag)`:

| Request | | |
|---|---|---|
| `#cre` | `xist`/`dupl`, `maxt`/`full`, `filt`/`fane` |
| `#joi` | `dupl`/`uniq`, `ajoi`/`dupl`, `nspc`/`full`, `many`/`many`, `mist`/`twic` |
| `#lea` | `uusr`/`miss` |
| `#sea` | `many`/`parm` |
| `#mem` | `uusr` |
| `#new` | `nspc`/`size` |
| `#dat` | (one entry) |
| *(no type)* | `dber`/`misc`, `nspc`/`misc`, `invp`/`parm` |

These are the error tags a server sends back, and they match the user-facing
strings: `full` -> "The selected tournament is full.", `dupl` -> "That
tournament name is already taken.", `strt` -> "That tournament has started.",
`time` -> "The server is not responding."

### The wider feature set

The string catalogue and state tokens map the whole service: states run
`offl` -> `conn` -> `sele` -> `auth`/`acct`/`pass`/`skey` -> `pers` -> `room` ->
`play`, with `idle`, `down`, `disc`, `term`, `ping`/`~png`, `snap`, `move`.
Subscription channels `+ses +msg +who +rom +pop +usr +rnk +snp` carry
`chat`/`cast`/`priv`. Errors are 4-char ASCII tags, not numbers, dispatched by
binary search at `0x3583a0`: `full`, `time`, `room`, `dupl`, `name`, `fane`,
`strt`. Account fields live at `0x609248`: `PERS CDEV AGE NAME MAIL SPAM CPAT
TOS PASS ALTS BORN GEND MINAGE PMAIL CHNG OPTS`.

A revived server therefore needs: directory redirect, EA-account login with
personas, buddy list with presence, rooms with chat, ranked matchmaking,
leaderboards, tournaments, and keepalive.

---

## Transport rules the server must obey (2026-07-31)

Recovered from the client's own reader. Each of these is cheap to get wrong and
expensive to diagnose.

**The server must speak within 60 seconds.** The client resets its receive
deadline to `now + 60s` on every message that arrives (`0x00448C4C`, 0xEA60) and
tears the session down when it lapses. The directory phase allows 120 s
(`0x00447854`).

**The client never originates a keepalive.** It intercepts any `~png` at
`0x00448C58`, echoes it verbatim, and returns *before* anything else runs. So
the ping must come from the server -- and the server must **not** answer the
echo, or the two ping each other forever.

**Replies are matched by type, against the head of the pending queue only**
(`0x00446CE0`). Consequences:

* One reply per request, exact type, in order. An extra, wrong or out-of-order
  reply pops the wrong record and fires its callback.
* Only types the client never sends may be pushed unsolicited: `+ses` `+msg`
  `+who` `+rom` `+pop` `+usr` `+rnk` `+snp`. `~png` is also safe, but for a
  different reason -- it is intercepted before the matching runs.
* A non-match is harmless: the out-record is memset first, so callback is 0.

**Maximum message 8192 bytes.** The client clamps its socket buffer to that
(`0x00452D90`, called with 0x8000) and writes a NUL at
`base + cursor + declared_length - 1` without bounds checking, so an
over-declared length is an out-of-bounds write in the client.

**One TCP write per message; no reassembly exists.** The reader handles several
whole messages coalesced in one read, but a message split across two reads is
lost -- the completion callback resets the cursor and discards the remainder
(`0x004533FC`).

**Status `auth` terminates the session** (`0x00449090`). Never use it as a
general error tag.

**There is no graceful close.** A FIN simply stalls the client until its 60 s
timer fires.

## Rooms and the lobby

`+rom` and `+usr` are ordinary `KEY=VALUE`, **one record per message** -- not
concatenated record buffers.

Both are dropped unless the subscription slot exists, which is created by
answering `sele`. `conn+784` is a 20-byte-stride slot array: slot 0 is ROOMS,
slot 1 is USERS (`conn+804`).

* **`+rom`** (104-byte record): `I` id (default -1; negative discards the
  record), `F` letter-bitmask, `L`, `T`, `N` name (32), `H` (32), `P` ping,
  `A` dotted quad. **Send `F=` explicitly** -- an absent `F` defaults to
  0xFFFFFFFF, which sets the `P` bit and makes the client show the room as
  password-protected.
* **`+usr`** (312-byte record): `I`, `F`, `N` (32), `P` (8), `A` quad, `R`,
  `S` (128), `X` (128), `T`. `F` letter `U` marks "this record is me".
* **`N` absent means delete that id.**

**Rooms are joined by NAME, not by id** -- the client sends `move` with `NAME`
and `PASS` (`0x00356A30`); leaving is the same message with `NAME=""`. On a
successful `move` to a different room the client **drains and frees its entire
user list**, so the server must re-push `+usr` for every occupant afterwards.

Ordering: the `sele` reply must land first (no slots, no pushes), then `+who`
or `move` to establish the room, then `+rom`, then `+usr`.

## Matches are peer to peer

The server introduces; it relays nothing.

`+ses` fills a 272-byte record at `conn+512`, memset first so every field
defaults to empty. `SELF`, `HOST` and `OPPO` are **persona-name strings**
(proven by `strcmp` at `0x004e2cbc`). `WHEN` is a validity gate: the record is
only delivered while it is non-zero (`0x00447ffc`), and is cleared after
delivery.

Then `0x004e2c88` decides the role:

| Condition | Connects to | Using | Flags |
|---|---|---|---|
| `SELF == HOST` | `OPPO` | `ADDR` | 0x102 |
| `SELF != HOST` | `HOST` | `FROM` | 0x101 |

and `0x004e2bf8` formats `"%d.%d.%d.%d:%d"` with **port 3658, hardcoded**
(`0x004e2c1c`), handing it to DirtySDK's NetGameUtil/NetGameLink. Which side
binds is decided inside `DRTYSCKF.IRX` on the IOP and is not visible in this
executable.

So a server starts a match by sending each player a `+ses` naming the other and
the address to reach them on. Gameplay never touches us again.

## The buddy service

Same framing, same send function (`0x00453220`), despite XMPP-shaped verbs. It
is a **separate endpoint** whose address arrives in-band from the main service,
*after* login -- so it cannot gate reaching a lobby, and a stub suffices:
accept, answer `AUTH` with status 0, echo `PING`, return an empty `ROST`.
`SHOW` is an integer selecting a string from a table at `0x005742b8`:
0 DISC, 1 CHAT, 2 AWAY, 3 XA, 4 DND, 5 PASS.

---

## ESPN NFL 2K5 — DNAS located, service found (2026-07-31)

**Its networking is in the main ELF after all**, roughly `0x003b0000`-`0x003e0000`,
entered at `0x003c1910(20919, ...)` straight off the DNAS success path. The
VC_20919 containers did not need unpacking.

**Server: `nfl2k5.games.espnvideogames.com`** (file offset `0x93b166`). Earlier
scans missed it because **2K5 stores nearly all strings as UTF-16LE** — that
one detail is why the title looked like it had no network strings at all.
Companion strings confirm the service: "Locating Servers.", "The ESPN
VIDEOGAMES service is down", "Could not connect to the Leaderboard server",
"ESPN Messenger" (a separate messaging service, as Madden has a separate buddy
service).

**DNAS.** libdnas2 2.71 is statically linked into a non-ALLOC `.dnas` section at
`0x01ee8280` — invisible to a loader that only maps PT_LOAD. Five call sites,
all from one wrapper; a per-frame state machine at `0x00403848` polls it and
stashes a result at `0x00BA7630`, and `0x00402f00` returns `(result == 2)`.

**The polarity is inverted from the intuitive reading**: 2 is authenticated and
1 is failed, so the stub at `0x004038ec` — which writes 1 — is a *failure*
stub. Patching it through without also correcting the constant would have
forced permanent failure rather than a bypass. `patches/42F9D5AF.pnach` carries
both a one-word gate patch (the pattern that worked for Madden) and the
two-word variant that skips DNAS entirely.

**2K5 does not speak Madden's protocol.** No DirtySDK signature, no four-char
ASCII types, no `KEY=VALUE`. Sends route through
`0x0014c848(ctx, id16, channel=3, buf, len)` — a binary, numeric-ID scheme. The
backend in this repo is Madden's and will not serve 2K5 without separate work.

The constant **3658** appears here too, stored as a halfword into the online
config, but at `0x003d2e98` it is passed as `a1 & 0xffff` with `a2=3, t0=16`,
which reads more like a message id and channel than a port. **Do not stand up a
listener on it without a live capture** — the coincidence with Madden's
peer-to-peer port is not evidence.

---

## The roster version, and how a server is meant to answer it

The exchange is a version *announcement*, not a challenge:

* the server publishes, in its `news` reply, the `DATE` and `CSUM` of the
  roster it considers current;
* the console computes the checksum of the roster **it** holds;
* they are compared at `0x0012a960`, and a difference means "yours is stale".

So the original server never knew any console's value. It simply stated the
current one, and consoles that disagreed fetched an update.

**What the checksum covers.** `0x0012a888` runs a query against the game's own
record store, whose full text sits at `0x00579b90`:

```
use 'GAEL' declare <cur> cursor for select * from 'YALP'
where ('DIGT' >= 1) and ('DIGT' <= 32) order by 'DIGP'
```

The four-character names are byte-reversed, as everywhere else here: `GAEL` is
**LEAG**, `YALP` is **PLAY**, `DIGT` is **TGID**, `DIGP` is **PGID**. So it is
`select * from PLAY where TGID between 1 and 32 order by PGID` -- every player
on all thirty-two teams, in player-id order.

**Status: solved and implemented (2026-07-31).** See `docs/roster-checksum.md`
for the full derivation. In brief, and correcting two things this section used
to say:

* `0x0012a730` is **not** the accumulator. It is a varargs marshaller that hands
  the query VM a format string at `0x00579958` plus 31 pointers spaced four
  bytes apart across a 124-byte buffer. That format string is the authority for
  which columns are hashed and in what order.
* The real accumulator is `0x0039d7e8`, whose table at `0x00565510` matches the
  reflected `0xEDB88320` polynomial on all 256 entries -- plain zlib CRC-32.

The loop seeds from the **row count**, read via `lhu`, not from 0 and not from
`0xFFFFFFFF`. A `0xFFFFFFFF` does sit nearby at `sp+8` and looks like a
conventional CRC init; it is cursor-struct setup and never reaches the
accumulator.

Retail `DB_TEAMS.DAT` yields 1743 players across 32 teams and a checksum of
`0x8108963c`. `tools/roster_checksum.py` computes it; `--roster-db` makes the
server announce it. The pnach line that bypassed the comparison is therefore
disabled -- leaving it on would suppress a *legitimate* update, which is the
mechanism a roster download has to hang off.

**Not yet confirmed by a console.** The algorithm is verified against the
executable and the extraction is verified against real player data, but no
hardware run has reached the comparison.

---

## What the console has actually done (2026-07-31)

Worth separating from what merely has tests, because the transcripts are
unambiguous and the distinction keeps getting blurred.

**Exchanged with a real console:** `@dir`, `skey`, `addr`, `auth`, `acct`,
`cper`, `pers`, `sele`, `cusr`, `news`, and `~png` keepalives. An account and a
persona were created and are reloaded on reconnect.

**Never sent by a console, in any capture:** `room`, `chat`, `mesg`, `move`. The
lobby, chat, matchmaking and buddy code is unit-tested only. No game has been
played.

`addr` is answered with silence deliberately — the client does not wait on it.
That is by design, not a gap.

**The stall, and the evidence it is fixed.** Before the handlers for `news` and
`cusr` existed, the console sent them and got nothing:

```
12:33:20  recv cusr      12:33:50  recv news      12:34:40  recv news
```

— 30-50 seconds apart, which is the client retrying, and exactly the "it sits
for about a minute" symptom reported from the sofa. After the fix, in the same
capture file:

```
13:02:08  recv cusr / send cusr      13:02:08  recv news / send news
```

Every request answered in the same second. When counting a transcript for
unanswered messages, note that one file can span a code change.

---

## The disc's modules, and what is in each

Established while hunting the roster download, so the next search starts in the
right file:

| File | Size | Contents |
|---|---|---|
| `SLUS_207.52` | 5.4 MB | main ELF: the EA protocol, the record store, the roster checksum |
| `NTGUI.ELF` | 4.2 MB | separate network-GUI module, loaded from `cdrom0:\NETGUI\NTGUI.ELF` |
| `ONLINE.DAT` | 1.2 MB | **a TERF container**, same format as `DB_TEAMS.DAT`: DNAS plus RSA **SSL-C 2.1.0** (not OpenSSL, despite `EVP_*`/`SHA1_*` symbol names) |
| `DB_TEAMS.DAT` | 8.4 MB | TERF container of 232 Madden TDB databases — the rosters |

Two dead ends recorded so they are not re-walked:

* `ONLINE.DAT` contains `http://www.ea.com/global/legal/privacy_noseal` three
  times. That is the **privacy-policy boilerplate** in the membership agreement
  text, not a service endpoint. There is no HTTP roster URL on this disc.
* It also contains `"A download error has occurred."` at `0x96998`. That string
  sits inside the **DNAS** error table, between "The network authentication
  server is not in service." and "DNAS Error (%d)". It is `sceDNAS2`'s
  auth-data download, unrelated to rosters.

---

## Open: how a roster is actually delivered

Knowing the checksum does not yet mean a roster can be **sent**, and this is now
the only thing between here and serving real rosters.

What is known: when the console was offered a mismatching `CSUM` it displayed
its update prompt, and the attempt then failed. What is *not* known is by what
route it expected the data.

The negative evidence is the interesting part. On the failed update the console:

* opened **no new TCP connection** -- nothing beyond the ports already in use;
* resolved **no new hostname** -- the DNS responder logged nothing further.

So the download is not a separate service on a separate host, which rules out
the most obvious shape. That leaves it either riding the existing lobby
connection as a message type we have not implemented and therefore never
answered, or reusing an already-resolved endpoint on a port we were not
listening on.

Distinguishing those is the next piece of work, and the entry point is the code
immediately after the comparison at `0x0012a97c`.

---

## Slice decision log

Record here, once recon is in, which title+platform becomes the first
vertical slice and why (most reusable infrastructure wins). Working bet before
data: Madden 2004, if it rides GameSpy.

**2026-07-30 — the GameSpy bet is dead, and DNAS is the whole ballgame.**

Both PS2 titles were captured. Neither shows a trace of GameSpy: no backslash
key/value framing, no 27900/28900, no master-server hostnames. Madden uses EA's
own service (`ps2madden04.ea.com`); 2K5 names no game server at all.

Both stop at the same place: `gate1.us.dnas.playstation.org` over TLS/443. Six
connection attempts, five `ECONNREFUSED`, then give up.

The consequence is bigger than a slice choice. **DNAS is a hard prerequisite for
all further PS2 recon** -- neither title will reveal its game-server protocol
until DNAS is satisfied, and 2K5 will not even name its servers. So there is no
useful "pick the cheaper title first" move on PS2: the next experiment is the
DNAS spike itself, and it decides whether the PS2 path is viable at all.

If DNAS proves to be a wall, the fallback is the **Xbox** side of both titles,
where Insignia already solves the platform-auth layer. That reverses the
original platform preference and is worth re-testing early rather than late.
Note the rig currently holds **no Xbox images** of either title, so that path
has an acquisition step before it can even be tested.

**2026-07-30, after static analysis — DNAS is a gate, not necessarily a wall,
and Madden is still the first slice.**

The pessimism above was overstated. We are writing the game server, so it can
accept whatever token the client presents; DNAS only has to satisfy the
*client's own* check. That gives two routes, and the second is well-trodden:

1. **Serve DNAS** — needs Sony's keys. Hard, and the TLS spike decides whether
   it is even possible (does the client validate our certificate?).
2. **Patch the client's check** — PCSX2 has a native patch/cheat system, so this
   needs no modified ISO. Madden hands us the hooks: `DirtyDnasRelSimError` and
   `DirtyDnasRelSetProxy` are EA's own error-simulation and redirection entry
   points, and the symbols are intact.

**Madden 2004 (PS2) is the first vertical slice** — not for the original reason
(GameSpy reuse, dead) but because it is the tractable one: intact symbols, a
literal hostname, a documented SDK family, and EA's own bypass-shaped hooks.
2K5 hides its network code inside VC-LZ containers and reveals no server at all,
so it is strictly harder and should follow, reusing whatever Madden teaches.
