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

_status: **not captured yet.** A run was attempted 2026-07-30 14:53 but predates
the PCSX2 DEV9 fix (applied 15:06), so the console never used our DNS: zero
queries, no game traffic. Needs a re-run._

- DNAS gate: **unknown — is it reachable / stubbable?**
- NIC capture likely plaintext at the app layer.

## Madden NFL 2004 — Xbox

_status: not captured yet_

- GameSpy vs EA-proprietary: **unknown — this is the key question for slice choice.**

## Madden NFL 2004 — PS2

_status: **captured 2026-07-30**, PCSX2 Sockets mode, DNS redirected to the rig._

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

**2026-07-30 — the GameSpy bet is dead for Madden 2004 (PS2).** It uses EA's own
service behind DNAS, so there is no OpenSpy shortcut to inherit and the cheap
Phase 3 that justified picking it first does not exist. Both PS2 titles now look
like they share the same first obstacle — DNAS — which means the DNAS spike is
the deciding experiment for the whole project, not a per-title detail. Hold the
slice decision until ESPN 2K5 is captured and DNAS is understood.
