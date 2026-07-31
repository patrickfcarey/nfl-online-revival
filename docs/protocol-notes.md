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
| Other EA host seen | `demangler01.pogo.com` (NAT/connection assist) |

`ROOM` + `GAME` + `USER` is a room-based lobby model, and four-character
uppercase tokens are the shape of EA's FESL-family protocols. That matters:
unlike GameSpy, this is a *documented family* with prior reverse-engineering
work on later EA titles, so the "reusable prior art" hope is not entirely dead —
it just moved from GameSpy to DirtySDK.

`/DATA/ONLINE.DAT` is almost entirely EULA text, plus `bio_gethostbyname`.
`/NETGUI/NTGUI.ELF` is Sony's stock network-config GUI, not EA code.

### ESPN NFL 2K5 — everything hidden in the packed containers

The main ELF `SLUS_209.19` (12.6 MB) contains **no hostnames and no lobby
vocabulary at all** — only `sceLibnetInitialize`, `socket`, and
`authorityKeyIdentifier`. That last one is an X.509 extension name, so 2K5
carries certificate-parsing code, which is directly relevant to whether DNAS
can be impersonated.

Its game code lives in `/VC_20919/0` .. `/4` — five Visual Concepts packed
containers, ~4.3 GB total, VC-LZ compressed. **This is the same pack-table and
VC-LZ format the 2K5 mod tools already decode**, so unpacking them and
re-scanning is a known quantity rather than new research.

`/VC_20919/DATA/DNAS271.IMG` (269 KB) is the DNAS module, version 2.7.1.

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
| 4 | 4 | transaction id, big-endian; 0 on the opening message |
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
