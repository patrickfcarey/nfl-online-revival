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
| **1. Recon** | learn what each title sends | per-title protocol map + connection state machine | low — this is where guesses become facts |
| **2. Front door** | get past platform auth | client reaches the game's own login layer | **highest** — PS2 DNAS may be a wall; Xbox leans on Insignia |
| **3. Matchmaking** | reimplement the master server | two clients see each other's game | medium (low if GameSpy) |
| **4. Peer connect** | a game completes | one full online head-to-head, server-brokered | medium (NAT traversal) |
| **5. Crown jewels** | leagues, VIP/crib, rosters, leaderboards | incremental, stateful features | high — the months-long slog |

## Strategy

**Vertical-slice one title, one platform all the way to Phase 4 before
breadth.** That single slice teaches the whole pipeline — auth → match → peer →
gameplay — cheaply; everything after is repetition and reconstruction.

Working bet: **Madden 2004 is the cheaper first slice** — if it rides GameSpy,
[OpenSpy](https://github.com/nitrogenlabs/openspy-core)-style reuse hands you
Phase 3 nearly for free. Prove the pipeline there, then bring it to ESPN 2K5,
which is the flagship but almost certainly a proprietary, stateful stack (the
hard one you want to attack *after* you know the moves). Recon (Phase 1)
confirms or overturns this before anyone commits.

**Leverage — don't reinvent:**
- **Xbox Live auth/IPsec** → adopt the [Insignia](https://insignia.live) stack.
  Reimplementing the Kerberos/IPsec plumbing from scratch is months of wasted
  effort.
- **GameSpy matchmaking** → OpenSpy / RetroSpy reimplementations.
- **PS2 DNAS** → the one that might genuinely block; treat it as an early spike.

## Phase 1 harness (`recon/`)

Standard-library Python 3.9+, no dependencies, so it drops onto the rig with no
install. The harness doesn't revive anything — it watches a client that still
tries to phone home and turns that into a written protocol map.

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
```

The typical loop: run `dns` to enumerate hostnames, take one NIC capture to
learn ports, then `sink` those ports and `classify` the result.

## Safety on the rig

The rig shares one VR headset across three emulators. Before launching any
emulator, run the H-2 live-session check as its own command and read it — any
hit means someone may be in the headset, so stop and ask:

```bash
pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f qemu-system-i386
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
docs/
  emulator-capture.md  rig-side runbook: DNS redirect, plaintext vs NIC, tcpdump
  protocol-notes.md    living per-title findings (fill during capture)
captures/              transcripts and pcaps land here (contents gitignored)
```
