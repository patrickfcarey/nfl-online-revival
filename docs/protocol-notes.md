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

_status: not captured yet_

- DNAS gate: **unknown — is it reachable / stubbable?**
- NIC capture likely plaintext at the app layer.

## Madden NFL 2004 — Xbox

_status: not captured yet_

- GameSpy vs EA-proprietary: **unknown — this is the key question for slice choice.**

## Madden NFL 2004 — PS2

_status: not captured yet_

- GameSpy vs EA-proprietary + DNAS: **unknown.**

---

## Slice decision log

Record here, once recon is in, which title+platform becomes the first
vertical slice and why (most reusable infrastructure wins). Working bet before
data: Madden 2004, if it rides GameSpy.
