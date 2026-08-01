# Hardening for a public deployment

Everything in this repo was built against a console on a LAN. That is a trusted
network with one player on it. This document scopes what has to change before
the same code faces the open internet on a rented arm64 box.

It is a scope, not a changelog. Nothing here is implemented yet.

The organising fact: **the client cannot be changed.** Madden NFL 2004 shipped
in 2003 and every limit we impose has to sit above what a retail console
actually does, with no way to negotiate, back off, or report an error the game
will render. A rate limit that is too tight does not produce a polite failure;
it produces a console that hangs on "Checking rank..." forever, because an
unanswered request wedges the client's pending queue (`0x00446ce0` — see
`docs/ea-protocol.md`). Every control below is therefore fail-*open* toward a
well-behaved client and fail-closed only against traffic that a real console
provably cannot generate.

---

## 1. Topology: split the public surface from the session state

The proposal is an edge VM that terminates public traffic and a core VM that
owns sessions and the database. That is the right shape, and it is worth doing
for a reason beyond DoS: the core process holds the SQLite file, the roster
payload, and every account. It should not be reachable from the internet at
all.

```
                 ┌─────────────────────── EDGE VM (public) ────────────────────────┐
   console ─────▶│  :53/udp   DNS          (recon/dnsd.py, map-only)               │
                 │  :3658/udp peer relay   (NOT YET WRITTEN — highest volume)      │
                 │  :10000-2  TCP ingress  ── DNAT / PROXY-protocol ──┐            │
                 │  :80       roster HTTP  ── may terminate here ─────┤            │
                 │  rate limits · conn caps · ban list · nftables     │            │
                 └────────────────────────────────────────────────────┼────────────┘
                                                                      │ private net
                 ┌────────────────────── CORE VM (private) ───────────▼────────────┐
                 │  backend.service   lobby, personas, rooms, matchmaking          │
                 │  SQLite on a persistent volume · roster payload                 │
                 │  no public IP · accepts only from the edge's private address    │
                 └────────────────────────────────────────────────────────────────┘
```

### The trap that will silently break matchmaking

**A normal L7 or L4 proxy destroys this protocol's matchmaking.**

`+ses` tells each console the address of its opponent, and those addresses are
derived from the *observed TCP peer address* — deliberately, because the address
the client reports in its own `addr` message is its local address, and under
PCSX2 Sockets mode every guest reports `192.0.2.100`
(`backend/handlers.py:129`, and the override at `0x004deb58` documented in
`docs/lobby-and-matchmaking.md`).

Put a proxy in front and every connection appears to originate from the edge.
The server then hands both consoles the edge's address as their opponent, and
they dial the proxy instead of each other. Nothing errors. Matchmaking simply
never completes — and it has never completed yet, so this failure would be
indistinguishable from the bug we already have.

Three ways through, in order of preference:

| Approach | Source IP preserved | Work required |
|---|---|---|
| **nftables DNAT on the edge** | Yes, natively | Firewall rules only. No code. |
| **PROXY protocol v2** (HAProxy L4) | Yes, in a header | ~40 lines to parse in `service.py` before the first frame, plus a trusted-source check |
| Plain TCP proxy | **No — breaks matchmaking** | Do not use |

DNAT is the recommendation: it gives the isolation you want, preserves the
source address for free, and needs no protocol changes. The core VM's default
route has to go back through the edge, which is the one piece of routing care
it requires.

If you later want real load balancing across several core VMs, that is when
PROXY protocol earns its complexity — and at that point the header parse must
reject connections from anything but the edge's private address, or a client
can forge its own source IP and poison another player's `+ses`.

### What belongs on the edge

- **DNS.** Stateless, must be public, and must never share a failure domain
  with the lobby. It is already written (`recon/dnsd.py`, 232 lines, stdlib).
- **The UDP relay.** Not yet written. It is pure packet forwarding, it will
  carry gameplay traffic rather than lobby traffic, and it is the piece most
  exposed to volumetric abuse. It belongs here for all three reasons.
- **Roster HTTP.** 253,044 bytes per download, served identically to everyone.
  This is a static file; it can be served straight off the edge (or a CDN /
  object store) and never touch the core.

### What must stay on the core

The lobby, the store, and matchmaking — because they are stateful and because
`+ses` correctness depends on seeing real client addresses.

---

## 2. Attack surface, port by port

| Port | Proto | Public? | Exposure |
|---|---|---|---|
| 53 | UDP | **Yes, unavoidable** | Reflection/amplification; open-resolver abuse reports |
| 3658 | UDP | **Yes, unavoidable** | Relay abuse — using us to bounce traffic at a third party |
| 10000 | TCP | **Yes, unavoidable** | Connection exhaustion; the client hardcodes this port |
| 10001–2 | TCP | Yes (advertised) | Same, plus session state |
| 80 / 10080 | TCP | Yes (in manifest) | Bandwidth: 253 KB × N |

Ports 53, 3658 and 10000 are fixed on the client side and cannot be moved.
Everything else we advertise ourselves and can relocate.

### DNS amplification: real but low-value

Worth stating precisely rather than alarmingly. We answer `A` records and
`NXDOMAIN` everything else. A query is ~60 bytes, a response ~76 — an
amplification factor near **1.3×**. Attackers want 50×+ (`ANY`, `TXT`,
`DNSKEY`), so this is a poor reflector by construction.

That is not a reason to skip response-rate limiting. It *is* a reason not to
spend much on it: `recon/dnsd.py` must run in **map-only mode** (`--host`
entries, no `--ip` default) so it NXDOMAINs everything outside our zone, and
that single configuration choice removes most of the risk. The `--ip` catch-all
mode is a capture tool for the rig. It must never be used on a public box.

### The relay is the one that can get you terminated

An unauthenticated UDP forwarder that accepts a destination from the packet is
an open reflector, and that is the kind of thing that gets a cloud account
suspended. The relay must therefore:

- forward **only** between two addresses already paired by the lobby, looked up
  from matchmaking state — never a destination taken from packet contents;
- expire a mapping when the lobby session ends;
- cap per-pair bandwidth and packet rate;
- refuse to forward to RFC1918, loopback, link-local, or multicast.

This is a design constraint on code that does not exist yet, which is the
cheapest possible time to record it.

---

## 3. Findings in the code as it stands

Each is a real defect, with the line that shows it.

### Already correct — do not "fix" these

- **`protocol.py:285`** — `split_stream` enforces `MAX_MESSAGE_SIZE = 8192`
  against the *declared* length before waiting for bytes. A peer declaring a
  4 GB frame is rejected immediately rather than buffered. The read buffer is
  bounded at ~72 KB by construction. No change needed.
- **`protocol.py:203`** — `_check_field` refuses `\n` and NUL in keys and
  values, which is what stops field injection through a persona name.
- **`hub.py` `SAFE_UNSOLICITED`** — pushes are restricted to types that cannot
  be mistaken for a reply. This is load-bearing and subtle; leave it alone.

### Gaps, by severity

**S1 — `service.py:246`: unbounded thread-per-connection.**
`_accept_loop` spawns a daemon thread per `accept()` with no cap. `listen(16)`
bounds the *backlog*, not concurrency. N connections means N threads; a trivial
script opens thousands and the box dies of scheduler pressure and stack memory
long before bandwidth matters.

**S1 — `service.py:101`: no socket timeout.**
`conn.recv(65535)` blocks forever. Open a TCP connection, send nothing, hold a
thread indefinitely. This is textbook slowloris and it is the cheapest attack
available: one SYN per thread consumed. Combined with S1 above, a single host
takes the service down from a laptop.

**S1 — `rosterfile.py:127`: roster HTTP is single-threaded.**
`http.server.HTTPServer` handles exactly one request at a time. One client that
opens a socket and never sends a request line blocks **every** roster download
indefinitely.

This is also a plain functional bug and not only a security one: two consoles
joining together serialise their 253 KB downloads, and the install path wipes
the league database *before* validating (`rosterfile.py` docstring,
`0x004c9ee8`), so a download that stalls behind another leaves a console with an
empty database recoverable only by reboot. `ThreadingHTTPServer` plus a
connection cap and a request timeout.

**S2 — `hub.py:83`: a slow consumer wedges broadcast.**
`Connection.send` takes a per-connection lock and calls `sendall` with no
timeout. `Hub.broadcast` walks its targets serially. A peer that stops reading
fills its socket buffer, `sendall` blocks, the lock is held, and the
broadcasting thread stops — so **one dead console silently freezes chat and
presence for everyone in the room**. Needs a send timeout and a
disconnect-on-timeout policy; a bounded per-connection send queue with a
drop-and-close rule is the standard form.

**S2 — `service.py:75`: resources committed before authentication.**
`hub.register()` runs on accept, so an unauthenticated peer already holds a
thread, a user id, and a hub slot. There is no deadline by which a connection
must reach `auth`. Standard practice is a short authentication deadline —
connect, then prove yourself within N seconds or be closed.

**S2 — no per-IP limit anywhere.** One address can consume every slot.

**S3 — no message rate limit.** An authenticated peer can loop `auth` or `pers`
as fast as the socket allows, and each one reaches SQLite. Cheap request,
expensive handler — the classic asymmetry.

**S3 — no ban list, no metrics.** There is no way to eject a misbehaving peer
short of restarting, and no counter that would tell you it is happening.

---

## 4. The controls

Standard practice for a small stateful TCP service. Nothing exotic — the value
is in the thresholds being defensible.

### Bounded concurrency

Replace thread-per-connection with a **fixed worker pool plus an accept
gate**. Beyond the cap, `accept()` then immediately `close()` — do not leave
connections queued in the backlog, because the client will sit waiting on a
socket that will never answer, and waiting forever is the one failure mode this
client handles worst.

| Limit | Proposed | Reasoning |
|---|---|---|
| Global concurrent connections | 512 | ~4 MB of thread stacks; comfortable on a 4-core Ampere A1 |
| Per source IP | 8 | A console opens :10000 then :10001 after redirect, so 2–3 transiently; 8 allows a household with two consoles |
| Accept rate per IP | 10/s burst 20 | Far above any legitimate reconnect |
| Roster HTTP concurrent | 16 | 253 KB each; 16 is ~4 MB in flight |

**CGNAT caveat.** A per-IP cap of 8 will eventually reject legitimate players
sharing a carrier-grade NAT address. For a hobby service with tens of concurrent
players this is theoretical, but the limit should be configurable and the
rejection should be *logged with the address* so the cause is visible rather
than mysterious.

### Timeouts

| Timeout | Proposed | Reasoning |
|---|---|---|
| Pre-auth deadline | 30 s | Login is a handful of round trips |
| Idle read | 90 s | We ping at 25 s (`hub.py:50`) and the client's own deadline is 60 s (`hub.py:48`), so a healthy connection is never quiet for 90 s |
| `sendall` | 10 s | Above any real network stall, below "wedged forever" |
| Total session | none | Players stay in a lobby for hours; a cap here would be user-hostile |

The idle timeout is the one number that is genuinely well-grounded: it derives
from the protocol's own keepalive, not from a guess.

### Rate limiting

A **token bucket per connection**, and a second per source IP.

Honest caveat: the local transcripts that would let me set these from measured
traffic did not survive — the figures I have are aggregate (2,241 messages,
mean 27 bytes, max 187). So the starting point should be deliberately loose,
and **the first task before go-live is one calibration pass**: run a full
session against a real console with the limiter in log-only mode, take the
observed peak 1-second burst, and set the limit at 10× it. Shipping a guessed
rate limit into a client that hangs on refusal is a bad trade.

Structurally: unauthenticated connections get a much smaller bucket than
authenticated ones, since everything before `auth` is a fixed, short sequence.

### Slow consumers and abuse

- Bounded send queue per connection; on overflow, close rather than block.
- A ban list keyed on source IP with a TTL, populated on repeated framing
  errors, rate-limit violations, or pre-auth timeouts. In memory is fine —
  bans expiring on restart is acceptable at this scale.
- Counters for every rejection reason, exposed on a **private** port. Without
  these you cannot distinguish "nobody is playing" from "everybody is being
  rejected", and those look identical from the outside.

### What to push down to the kernel

Anything volumetric should never reach Python. On the edge VM, nftables handles
what it is better at:

- `ct state new limit rate` per source address on the TCP ports
- SYN cookies (`net.ipv4.tcp_syncookies=1`)
- a UDP rate limit on 53 and 3658
- conntrack table sizing for the relay

Cloud security groups sit in front of all of it, allowing only 53/udp,
3658/udp, 10000-10002/tcp and 80/tcp inbound, and restricting the core VM to
the edge's private address.

---

## 5. Container and deployment hardening

The stack is pure stdlib Python with no compiled dependencies and every
`struct` format explicitly endian-prefixed, so `python:3-slim` on arm64 needs
no special handling.

- Non-root user; read-only root filesystem; `--cap-drop=ALL`.
- **No `CAP_NET_BIND_SERVICE` needed.** Listen on 5353 inside the container and
  publish `-p 53:5353/udp`. The privileged bind is done by the daemon on the
  host side, so the process itself never needs the capability.
- **`systemd-resolved` conflict**: Ubuntu cloud images bind `127.0.0.53:53`,
  which collides with a `0.0.0.0:53` publish. Set `DNSStubListener=no` in
  `/etc/systemd/resolved.conf`, or publish on the public IP explicitly. This
  will bite on first deploy if it is not handled.
- Memory and PID limits per container (`--memory`, `--pids-limit`) so a thread
  leak cannot take the host with it.
- SQLite on a named volume. A container without one loses every account on
  redeploy.
- A restart policy, and a healthcheck that opens a TCP connection and completes
  a trivial exchange — not merely "the port is open", which stays true when
  every worker is wedged.

---

## 6. Sequencing

**Phase A — the ones that are exploitable from a laptop. DONE 2026-08-01.**
Socket timeouts, global and per-IP connection caps, `ThreadingHTTPServer` with
a request timeout, `sendall` timeout. See §7.

**Phase B — the edge/core split.** Two VMs, nftables DNAT, private network,
security groups. Mostly infrastructure; the only code change is binding the
core to a private address.

**Phase C — the application-layer controls. DONE 2026-08-01.** Token buckets in
log-only mode, pre-auth deadline, ban list, counters. See §8. The calibration
session and the switch to enforcing remain, and cannot be done here — they need
a real console.

**Phase D — the relay**, built with the constraints in §2 from the start.

Phase A is worth doing regardless of whether the service ever goes public — the
`sendall` wedge and the single-threaded roster server are bugs that will bite on
a LAN with two players, and two players on a LAN is exactly the next test.

---

## 7. Phase A as built

All five S1/S2 findings in §3 are closed. New module `backend/limits.py`;
changes in `hub.py`, `service.py`, `buddy.py`, `rosterfile.py`, `__main__.py`.

| Was | Now |
|---|---|
| unbounded thread per accept | gated *before* the thread exists; refusal is accept-then-close |
| `recv()` blocks forever | socket timeout; 30 s first-byte deadline, 120 s idle |
| `sendall` blocks forever under a lock | 10 s bound; on expiry the peer is abandoned |
| roster HTTP one request at a time | `ThreadingHTTPServer`, 16 slots, 30 s request timeout |
| no per-IP limit | 8 per address (4 on the buddy endpoint) |

Knobs: `--max-connections`, `--max-connections-per-ip`, `--send-timeout`,
`--idle-timeout`, `--first-byte-timeout`. Zero disables any of them, which is
reasonable on a LAN and is not on a public address. The startup banner prints
the effective values, so a session log always records what was enforced.

Three implementation notes worth keeping, because each was a choice between two
plausible options:

**One socket timeout serves both directions.** It bounds `sendall` for every
writer and paces the read loop so the deadlines can be checked at all. The
alternative — `select()` before each write — needs no shared value but breaks
above file descriptor 1024, and the cap is 512 connections across three ports.

**A timed-out write aborts with `shutdown`, not `close`.** The thread that owns
the connection is blocked in `recv`; `shutdown` wakes it and lets it close the
descriptor in its own `finally`. Closing from the writer would free a
descriptor another thread is about to read from, and `accept` reuses the
number — so the reader would resume against an unrelated client's socket.

**`socket.timeout` is caught in a nested `try`, before `except OSError`.** It
subclasses `OSError`, so caught by the outer handler every poll would look like
a socket error and drop a healthy connection.

### Verification

15 new tests in `tests/test_limits.py`, 2 in `tests/test_roster_delivery.py`;
337 pass.

The one that matters is
`SlowConsumer.test_a_stalled_peer_does_not_block_the_room`. It registers the
stalled peer *first*, so a hub that writes serially and blocks never reaches the
healthy one. Confirmed non-vacuous by reproducing the old blocking `sendall`
against a peer that never reads: it does not return in 8 seconds, or ever.

Also asserted deliberately: `test_a_talkative_connection_survives`. A limit that
drops real players is worse than no limit, because this client's response to a
dropped connection is to wait rather than report anything.

### Not done in Phase A

The pre-*auth* deadline is Phase C. What ships here is a first-*byte* deadline,
which is what closes the slowloris hole. A connection that sends one byte and
then stalls forever without ever authenticating is still bounded only by the
120 s idle timeout and the connection caps.

## 8. Phase C as built

New module `backend/metrics.py`; `backend/limits.py` gains `TokenBucket`,
`RateLimiter` and `BanList`; wiring in `service.py` and `__main__.py`.

### Rate limiting ships observing, not enforcing

This is the central decision and everything else follows from it. The
thresholds are **not measured** — the transcripts that would have set them did
not survive — and this client's response to a message that gets no reply is to
wait forever, because an unanswered request wedges its pending queue
(`0x00446ce0`). Shipping a guessed threshold in enforcing mode risks cutting off
a real player in the one way they cannot diagnose.

So `--rate-limit` has three settings: `off`, `observe` (default), `enforce`.
Observing counts and logs violations and never acts on them. Two tests hold that
line — `test_observing_never_drops_a_connection` and
`test_observing_never_bans`.

Token buckets rather than fixed windows: a window lets a client spend its whole
allowance at the boundary and again immediately after, which is the burst the
limit exists to bound.

### The calibration loop

`nfl_rate_peak_messages_per_second` is the busiest one-second window on the
busiest **single** connection — a maximum over connections, never a total
across them. That distinction is the whole value of the gauge: `--rate` is
spent by one connection, so a figure summed across all of them would be
inflated by however many clients were online, and the limit derived from it
would be that many times too loose. (It shipped summed on 2026-08-01 and was
corrected the same day; see §10.)

That gauge is the entire point of log-only mode — it turns the guess into a
measurement:

1. Run a full session with a real console, `--rate-limit observe`.
2. Read the peak off `http://127.0.0.1:9109/`.
3. Set `--rate` and `--rate-burst` near ten times it.
4. Switch to `--rate-limit enforce`.

Until step 4 the defaults (20/s per connection, 60/s per address) are loose
guesses and are documented as such in the banner, which prints the warning on
every start so nobody mistakes observing for protection.

### Pre-auth deadline

Distinct from the first-byte deadline and it closes a different hole: a socket
that sends one byte a minute passes every silence check forever while holding a
thread, a slot, and a user id. 60 s, against `Session.authenticated`
(`account is not None`). Connections that only ask `@dir` are exempt in practice
rather than by rule — they are gone long before it expires.

### Bans

Five strikes in 120 s earns 600 s, in memory. Bans that expire on restart are
acceptable at this scale, and persisting them would mean a mistake in the strike
rules outlives the fix for it.

**Only hard signals earn strikes**: malformed framing, and a connection that
never speaks at all. A rate violation counts *only when enforcing* — banning on
an unmeasured threshold would take a real player off the service for ten minutes
with no way to tell them why. The ban check runs before the connection limiter,
so a banned address does not also consume a slot to be refused.

**Failing to authenticate is not a strike**, though it does close the
connection. Connections to the redirector port send `@dir` and legitimately
never authenticate, and nothing in `docs/ea-protocol.md` establishes that the
console closes that socket promptly — it says only that the session moves to a
second connection. If it holds the first one open, striking would cost a strike
per login and ban a real player after five.

**One ban list and one rate limiter span both endpoints.** A ban earned on the
game ports keeps that address off the buddy port too; otherwise the endpoint
with fewer controls is the one worth attacking. The *connection* limiters stay
separate, because a console holds one buddy socket and two game ones and they
should not compete for a single per-address budget.

Both maps are keyed on attacker input, so both are bounded (`_MAX_TRACKED`)
rather than relying on expiry running.

### Counters

Prometheus text on loopback, `--metrics-port 9109`. Two rules, both about not
leaking players:

- **The listener refuses a non-loopback bind** unless `--metrics-allow-public`.
  There is no authentication on it and none is intended.
- **No counter carries a source address as a label.** Aggregate totals answer
  every operational question here — how many were refused, not who — and a
  metrics page that enumerates player addresses is a log of who played and when.
  `test_no_counter_carries_a_source_address` asserts the shape, not a list of
  names, since a label is the only way an address could get onto the page.

Every counter is declared at zero on startup. A counter that appears only once
non-zero cannot be told apart from one that was never wired up, so an absent
`refused` line would read as "nothing was refused" when it might mean the check
does not run.

Losing the metrics port is a warning, never fatal. Refusing to run a game server
because a diagnostic port is taken would be the wrong trade.

### Verification

31 new tests (`tests/test_limits.py` 15 → 36, `tests/test_metrics.py` new);
368 pass.
Confirmed live: banner, counters, and the peak gauge reading 12 after a
12-message burst.

One structural fix worth noting: the socket-level tests had shared a harness by
subclassing, which made unittest collect the parent's tests again through the
child and run the whole accept-gate suite twice. The harness is now a plain
mixin.

## 9. Zero does not mean the same thing everywhere

`0` disables `--max-connections`, `--max-connections-per-ip`,
`--ban-threshold`, `--idle-timeout`, `--first-byte-timeout` and
`--pre-auth-timeout`. It does **not** disable two others, and both are refused
at startup rather than accepted and silently catastrophic:

- **`--send-timeout 0`** would call `settimeout(0)`, which means *non-blocking*,
  not *no timeout*. `recv` then raises `BlockingIOError` — an `OSError` but not
  `socket.timeout` — so it falls past the poll handler and every connection is
  dropped on its first quiet moment. There is also no safe "unlimited" value
  here: that is the stalled-peer wedge the timeout exists to prevent.
- **`--rate-burst 0` with a positive `--rate`** would refuse every message,
  because zero capacity can never hold a token long enough to spend one. Use
  `--rate-limit off`; both values zero is the internal "unlimited".

## 10. Review corrections (2026-08-01)

Six defects found reviewing the Phase A and C commits, all fixed the same day.
Recorded because four of them are the kind that pass a test suite:

| # | Defect | Why the tests missed it |
|---|---|---|
| 1 | Peak gauge summed across connections while `--rate` is per-connection | Wired to the wrong scope; every unit test used one connection |
| 2 | `Thread.start()` unguarded in both accept loops — leaked a slot and an fd | Reachable only under thread exhaustion, which nothing simulated |
| 3 | `--send-timeout 0` dropped every connection | Boundary value; only the happy path was tested |
| 4 | Buddy endpoint had no Phase C controls at all | No test asserted coverage *across* endpoints |
| 5 | Pre-auth timeout struck a strike, banning legitimate `@dir` clients | Rested on an unverified claim in a docstring |
| 6 | `--rate-burst 0` refused everything | Boundary value |

Each now has a regression test, including
`test_peak_is_per_connection_not_a_total_across_them` and the whole of
`tests/test_buddy_limits.py`. 380 pass.

Finding 5 is the one worth remembering: the docstring asserted that a client
which only asks `@dir` is "gone long before this expires". That was never
verified and is not in the protocol notes. The lesson generalised into a rule
here — a signal only earns a strike if a legitimate client provably cannot
produce it — which is also why the buddy endpoint has no pre-auth deadline.

## 11. What this does not cover

- **Application-layer authentication is weak by design.** The client's `auth`
  exchange is what it is; we cannot add TLS or a modern credential flow without
  changing a client we cannot change. Accounts should be treated as
  low-value — do not reuse passwords, and do not store anything sensitive.
- **The peer link has never worked**, so relay behaviour under real gameplay
  traffic is unmeasured. The bandwidth numbers above are estimates.
- **DDoS.** Nothing here survives a volumetric attack; that needs upstream
  scrubbing. The goal is to survive one bored person with a script, which is
  the realistic threat for a hobby game server.
