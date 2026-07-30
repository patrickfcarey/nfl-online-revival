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
