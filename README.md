# NFL Online Revival

Reviving the dead online servers for **ESPN NFL 2K5** and **Madden NFL 2004** on
the original Xbox and PlayStation 2. These services were shut down years ago
(original Xbox Live in April 2010, GameSpy in 2014, EA's servers for this era
long before), so this is preservation and reconstruction — there is no live
service anywhere in the picture.

## The framing that makes this tractable

You will never have 2K's or EA's server binaries. So the work is **not**
"reverse engineer the server." It is:

> observe the game client (which you *do* have) → reconstruct the protocol it
> speaks → write a new, compatible server that answers it.

That reframe tells you exactly where the hard parts are — not in a missing
binary, but in reconstructing server-side game logic (leagues, franchise,
persistence) that only ever existed on machines that are gone.

## Roadmap

| Phase | Goal | Deliverable | Main risk |
|---|---|---|---|
| **1. Recon** | learn what each title sends | per-title protocol map + connection state machine | **done** — both PS2 titles captured |
| **2. Front door** | get past platform auth | client reaches the game's own login layer | ~~highest~~ **done for Madden** — DNAS was one word, not a wall |
| **3. Matchmaking** | reimplement the master server | two clients see each other's game | medium — no GameSpy reuse; written from scratch |
| **4. Peer connect** | a game completes | one full online head-to-head, server-brokered | medium (NAT traversal) |
| **5. Crown jewels** | leagues, VIP/crib, rosters, leaderboards | incremental, stateful features | high — the months-long slog |

## Where this actually stands

Recon is done, and it overturned the plan this file used to describe. Keeping
the old bets around would send the next reader after the wrong things, so:

**Confirmed against a running console** — Madden 2004, `SLUS-20752`, under
**PCSX2 in Sockets mode**, not a physical PS2. Every item below appears in a
capture transcript rather than only in tests. The distinction matters: under
Sockets mode every guest reports `ADDR=192.0.2.100`, which is why two emulated
consoles cannot yet reach each other.

- the DNAS gate is passed, by a one-word patch;
- the login chain runs end to end: `@dir` → `addr` → `skey` → `auth`/`acct` →
  `cper` → `pers` → `sele` → `cusr` → `news`, plus `~png` keepalives.
  (`addr` precedes `skey`, and on a first run `acct` precedes `auth`; an earlier
  version of this list had both the wrong way round.)
- an EA account and a persona were created and are reloaded on reconnect.

**Exercised by a console since (2026-08-01):** roster download over HTTP, room
creation (`C.NEW ROOM`, id 79), `move` in and out of a lobby, quickmatch
(`KIND=-24256204`) paired to a `+ses`, the buddy service answering `AUTH`,
`PSET` and `RGET`, and the console dialling a peer on UDP 3658.

**Built and unit-tested, still never exercised by a console:** chat. No
transcript contains a `chat` or `mesg` message.

**No game has been played online**, and it cannot be yet: the peer link needs
two hosts with distinct addresses, and every PCSX2 guest reports 192.0.2.100.
Note also that the one pairing observed introduced a console to a second socket
of *itself* -- it proves the `+ses` path through the real client, not that two
separate players can meet.

**A modified roster has been installed on a console and observed.** A payload
differing from the disc by one player name was served, downloaded, verified and
installed; twelve whole `PLAY` records read out of live console memory are
byte-identical to what was served. Roster delivery works end to end.

**Dead ends, so nobody re-walks them:**

- **GameSpy is not involved in either title.** The original bet was that Madden
  rode GameSpy and that OpenSpy/RetroSpy would hand over matchmaking nearly for
  free. Capture showed no GameSpy traffic anywhere; Madden is EA **DirtySDK**
  (FESL-family framing), so that reuse never materialised. Madden still made the
  right first slice, but for unrelated reasons — intact symbols, a literal
  hostname, a documented SDK.
- **DNAS was not the wall it was billed as.** We write the server, so it only
  ever had to satisfy the *client's own* check. One word.

**ESPN 2K5 is a separate protocol problem.** Its service is
`nfl2k5.games.espnvideogames.com`, which earlier string scans missed because the
game stores its strings **UTF-16LE**. Its DNAS gate is located but untested on
hardware, and its wire format is binary under numeric message ids — none of
Madden's text framing transfers.

**Still worth reusing:** the [Insignia](https://insignia.live) stack, if the
Xbox versions are ever attempted — reimplementing Kerberos/IPsec is months of
wasted effort. That one still holds.

## Phase 1 harness (`recon/`)

Standard-library Python 3.9+, so it drops onto the rig with a file copy — no
pip, no virtualenv to go wrong mid-session. That is a deliberate choice for the
capture harness rather than a rule for the whole project. The harness doesn't
revive anything — it watches a client that still tries to phone home and turns
that into a written protocol map.

```bash
# 1. Answer the game's DNS so it connects to your capture box.
#    (port 53 needs privilege; see docs/emulator-capture.md)
sudo python3 -m recon dns --ip 10.0.0.5            # send every name to you
python3 -m recon dns --map easo.ea.com=10.0.0.5    # or redirect specific hosts

# 2. Accept and log every connection the redirected client opens.
python3 -m recon sink --tcp 80,443,18300 --udp 27900,28900 --out captures/run.jsonl

# 3. Fingerprint what you captured (GameSpy / EA / DNAS / TLS / plaintext).
python3 -m recon classify --transcript captures/run.jsonl
python3 -m recon classify capture.pcap             # or a NIC pcap from tcpdump
python3 -m recon pcap capture.pcap                 # raw flow dump

# 4. For a service the client reaches over TLS: terminate it and find out
#    whether the client validates the certificate (serve-vs-patch decision).
#    NOTE: the TLS sinkhole needs a DNS responder running too, or the title
#    never resolves the gateway and never connects. Use the script:
./dnas_probe.sh <rig-ip> madden       # runs BOTH; this is the DNAS spike
```

Session scripts, so the halves cannot be run apart:

| Script | Runs | For |
|---|---|---|
| `./capture.sh <label> <rig-ip>` | DNS responder + tcpdump | discovering hostnames and ports |
| `./dnas_probe.sh <rig-ip> [label]` | DNS responder + TLS sinkhole | the serve-vs-patch decision |

The typical loop: run `dns` to enumerate hostnames, take one NIC capture to
learn ports, then `sink` those ports and `classify` the result.

Tests (offline — no sockets, no capture, no game data):

```bash
python3 tests/test_recon.py          # 105 tests
```

Failure modes that would otherwise look like "the game never phoned home" are
covered deliberately: `tcpdump -i any` link types (libpcap >= 1.10 writes
LINUX_SLL2), an unbindable sinkhole port, and IP fragments. Each is a named
regression test.

## Safety on the rig

The rig shares one VR headset across three emulators. Before launching any
emulator, run the H-2 live-session check as its own command and read it — any
hit means someone may be in the headset, so stop and ask:

```bash
pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f "qemu-system-i38[6]"
```

Never chain an emulator launch behind that check. Read-only access to rig files
over SSH is always fine. Machine-local details (the rig address) go in an
untracked `.env.local` — see `.env.local.example` — never committed.

## Layout

```
recon/                 Phase 1 harness (stdlib only)
  dnsd.py              controllable DNS responder
  sinkd.py             TCP/UDP connection sinkhole + JSONL transcript
  pcapreader.py        classic-pcap reader (no scapy)
  classify.py          stack fingerprinter
  __main__.py          CLI: python -m recon <dns|sink|classify|pcap>
tools/
  madden_tdb.py        DB_TEAMS.DAT reader: TERF container + bit-packed TDB
  roster_checksum.py   the roster CSUM the console compares against
  fake_console.py      a stand-in client: drives login/lobby/matchmaking and
                       checks what comes back, so mistakes cost seconds rather
                       than a console boot
docs/
  emulator-capture.md  rig-side runbook: DNS redirect, plaintext vs NIC, tcpdump
  protocol-notes.md    living per-title findings (fill during capture)
  roster-checksum.md   how the CSUM was reversed, and what is still unproven
captures/              transcripts and pcaps land here (contents gitignored)
```

## Testing without a console

Hardware is a poor debugger: one bit of feedback per boot, and most mistakes in
this protocol are ones the server cannot see -- a client that disagrees does not
complain, it goes quiet. `fake_console.py` speaks the client's half and asserts
the things the real one silently requires.

```bash
./serve-madden.sh &
python3 tools/fake_console.py --host 127.0.0.1 --pair     # two clients, quickmatch
python3 tools/fake_console.py --host 127.0.0.1 --persona alice --say hi
```

To give a **real console** an opponent, stand a bot in the queue:

```bash
SPAR=1 ./serve-madden.sh          # server + a test opponent, together
```

That implies `--pair-any`, which relaxes matchmaking to pair any two waiting
clients. It has to: a console derives its `KIND` from its own build stamp and
game settings, so nothing else can reproduce the value it will send, and without
the relaxation the two never meet in the queue. Real pairing stays exact-match —
`KIND` equality is the only thing the client itself can verify about a match.

Be clear about what that proves. It shows the console sends `quik`, that the
server pairs it, and that it accepts the `+ses` and tries to dial its peer.
It cannot play a game: the peer link is UDP on 3658 and nothing here answers,
so about ten seconds after the introduction the console reports a failed
connection. **Reaching the introduction is the pass condition; the timeout after
it is expected.**

`--pair` is the matchmaking test: it logs two clients in, queues both, and holds
the resulting `+ses` records to the rules that matter -- only `SELF` differs,
`HOST` agrees, `SEED` matches, `WHEN` is non-zero, and neither console is handed
the address it reported rather than the one it connected from.

It cannot prove the address *crossing*, because two clients on one host share an
address. That part is covered in `tests/test_matchmaking.py`, with distinct ones.

## Serving a current roster

The point of all of it. Build a season and serve it:

```bash
python3 tools/build_year_roster.py --year 2025 \
    --template extract/madden_TEMPLATE.DAT -o rosters/2025.dat
ROSTER_PAYLOAD=rosters/2025.dat ./serve-madden.sh
```

`ROSTER_PAYLOAD` is required — serving nothing is a silent failure, where the
console asks for a manifest, receives an empty one, and reports a vague error
while the server looks perfectly healthy. An evening went that way once.

**2023 is the year to build.** It is the newest season EA published ratings for,
so every player carries the ratings EA shipped for him and nothing is invented.
Build a later year and there is no authority for anyone who has entered the
league since; those players are left out unless you pass `--estimate-missing`,
which invents ratings from position, age and experience and is off for a reason.

Teams are keyed off the game's own `TEAM` table, not the scraped file's team
ids. The two agree for twenty-nine clubs and disagree for three -- the game has
30 Titans, 31 Vikings, 32 Texans where the scraped rosters have Texans, Titans,
Vikings -- so trusting the file puts three entire rosters on the wrong teams
while the other twenty-nine make it look correct.

The server announces the checksum of the roster it is **serving**, derived from
the payload itself. That matters: the console recomputes over what it installed,
so announcing some other roster's value would have it conclude the fresh install
was already stale and offer the same update forever.

## Rosters

The console computes a checksum over its own roster and compares it against the
`CSUM` the server announces. That algorithm is reversed and implemented, so the
server can state a version the console agrees with:

```bash
python3 tools/roster_checksum.py /path/to/DB_TEAMS.DAT
./serve-madden.sh                  # reads RIG_IP from .env.local
REPLACE=1 ./serve-madden.sh        # ...taking over from one already running
```

**Only one backend can run at a time, and that is enforced by the server, not by
remembering to use the script.** It takes an exclusive `flock` on the port set
at startup; a second one exits with the holder's PID and command line however it
was launched. The kernel drops the lock if the holder dies, so a crash cannot
leave one stranded.

This is not fussiness. Two servers on the same ports is a silent failure, not a
loud one: the second cannot bind while the first keeps answering, so a console
goes on talking to whatever configuration the *old* one had. That is exactly how
a hardware test spent an evening on an empty roster manifest — the server had
been "restarted" several times and the console never once spoke to the new one.

`REPLACE=1` takes over, and it identifies the holder by reading the lock rather
than by `pgrep`. A `pgrep -f` for the server's own command line also matches any
shell whose command line contains that text — including the ssh invocations used
to drive the rig, which has both killed live sessions and produced a false
"already running".

**Use the script rather than assembling the command by hand.** Four flags matter
and none of them announce themselves when wrong — every mistake below produces
the same "an error happened when connecting to the server" on the console:

| Flag | Getting it wrong |
| --- | --- |
| `--db` | Defaults to `backend.db`. Point it elsewhere and existing accounts disappear: correct name and password, server answers `auth` with status `miss`. |
| `--port` | Defaults to `10000,10001` and needs **both** — a brief `@dir` contact on 10000, then the session on 10001. Narrowing it to 10001 gets the first connection refused and the server logs nothing at all. |
| `--buddy-port` | The presence stub the client is told about in `news`. |
| `--advertise-host` | Must be a dotted quad; the client parses it octet by octet. |

Always pass `--transcript` when something misbehaves. The console reports one
generic error for every one of these, so the transcript is the only place the
actual failure is visible.

No game data lives in this repository; point these at your own extracted disc.
The console computes the same value — measured out of a savestate, see
`docs/roster-checksum.md`.
