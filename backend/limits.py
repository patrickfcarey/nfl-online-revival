"""How many connections may exist, and how many any one host may hold.

Everything in this project was built against a console on a LAN -- one player,
a trusted network, and no reason to count anything. On a public box the accept
loop's `thread per connection, forever` shape is the whole vulnerability: a
laptop opens sockets until the host dies of thread stacks and scheduler
pressure, and it costs the attacker one SYN per thread.

Two numbers stop that, and they are deliberately separate:

**A global cap** bounds what the machine has to survive. It is a memory and
scheduler bound, not a fairness one.

**A per-address cap** stops one host taking every slot. Without it the global
cap is no defence at all -- it just decides how many connections a single
attacker needs.

Refusal is immediate and rude on purpose: accept, then close. The alternative
is leaving connections in the listen backlog, which looks polite and is much
worse. This client has no useful behaviour for a connection that is accepted
and then ignored -- it waits, and an unanswered request wedges its pending
queue permanently (0x00446ce0). A closed socket at least produces a retry.

**On CGNAT.** A per-address cap will eventually refuse a legitimate player
sharing a carrier-grade NAT address with other players. At this scale that is
theoretical, but it is why `per_ip` is configurable and why every refusal is
counted and logged with its address: the failure has to be visible as a limit
rather than as an unexplained inability to connect.

Zero means unlimited, for tests and for a LAN where none of this is worth
enforcing.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Deque, Dict, List, Optional, Tuple

#: Roughly 4 MB of thread stacks. Comfortable on the smallest arm64 instance
#: worth renting, and far above any plausible player count for this title.
DEFAULT_MAX_CONNECTIONS = 512

#: A console connects to :10000, is redirected, and connects to the advertised
#: port -- so two at once transiently, plus the buddy endpoint. Eight allows a
#: household with two consoles and leaves room for the overlap during a
#: reconnect.
DEFAULT_MAX_PER_IP = 8


class ConnectionLimiter:
    """A counter of live connections, globally and per source address.

    Safe to share between listeners: the main service accepts on three ports
    and a single console legitimately occupies two of them at once, so the
    count that matters spans all of them rather than each in isolation.
    """

    def __init__(self, total: int = DEFAULT_MAX_CONNECTIONS,
                 per_ip: int = DEFAULT_MAX_PER_IP) -> None:
        if total < 0 or per_ip < 0:
            raise ValueError("limits cannot be negative")
        self.total = total
        self.per_ip = per_ip
        self._lock = threading.Lock()
        self._active = 0
        self._by_ip: Dict[str, int] = collections.defaultdict(int)
        self.refused_total = 0
        self.refused_per_ip = 0

    def acquire(self, address: str) -> Optional[str]:
        """Take a slot for *address*.

        Returns None when the connection may proceed, or a human-readable
        reason to refuse it. The reason is returned rather than raised because
        every caller logs it and closes -- there is no path that recovers.
        """
        with self._lock:
            if self.total and self._active >= self.total:
                self.refused_total += 1
                return ("server is full: %d/%d connections"
                        % (self._active, self.total))
            if self.per_ip and self._by_ip[address] >= self.per_ip:
                self.refused_per_ip += 1
                return ("%s already holds %d connections (limit %d)"
                        % (address, self._by_ip[address], self.per_ip))
            self._active += 1
            self._by_ip[address] += 1
            return None

    def release(self, address: str) -> None:
        """Give a slot back. Safe to call for an address never acquired."""
        with self._lock:
            if self._active:
                self._active -= 1
            held = self._by_ip.get(address, 0)
            if held <= 1:
                self._by_ip.pop(address, None)
            else:
                self._by_ip[address] = held - 1

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def held_by(self, address: str) -> int:
        with self._lock:
            return self._by_ip.get(address, 0)

    def describe(self) -> str:
        """One line for the startup banner and the periodic log."""
        with self._lock:
            return ("%d active, %d distinct address(es); refused %d over-total, "
                    "%d over-per-ip"
                    % (self._active, len(self._by_ip), self.refused_total,
                       self.refused_per_ip))


# ---------------------------------------------------------------------------
# message rate
# ---------------------------------------------------------------------------

#: Sustained messages per second one connection may send.
#:
#: **These numbers are not measured, and that is the whole reason the limiter
#: ships observing rather than enforcing.** The transcripts that would have set
#: them from real traffic did not survive; what is known is aggregate -- 2,241
#: messages, mean 27 bytes, max 187 -- which bounds size but says nothing about
#: burst. Login is a short fixed exchange and lobby activity after that is
#: human-paced, so these are deliberately loose. Calibrate before enforcing:
#: run a full session with --rate-limit off, read the observed peak out of the
#: metrics, and set the limit near ten times it.
DEFAULT_RATE = 20.0
DEFAULT_BURST = 60

#: Per source address, allowing for a household with several consoles.
DEFAULT_IP_RATE = 60.0
DEFAULT_IP_BURST = 180

#: Longest run of message timestamps kept for the peak observation. Past this
#: the peak is already far beyond anything worth measuring precisely, and the
#: deque must not grow on attacker input.
_PEAK_SAMPLE_CAP = 4096


class TokenBucket:
    """Classic token bucket: `burst` capacity, refilled at `rate` per second.

    Chosen over a fixed window because a window lets a client send its whole
    allowance at the boundary and again immediately after, which is exactly the
    burst the limit exists to bound. A bucket smooths that without penalising
    the legitimate case, which for this client is a short flurry at login and
    then near-silence.
    """

    def __init__(self, rate: float, burst: float) -> None:
        if rate < 0 or burst < 0:
            raise ValueError("rate and burst cannot be negative")
        self.rate = float(rate)
        self.burst = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, count: float = 1.0) -> bool:
        """Spend `count` tokens. False when the bucket is dry."""
        if not self.rate and not self.burst:
            return True                      # unlimited
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst,
                               self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= count:
                self._tokens -= count
                return True
            return False

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._tokens


class _PeakObserver:
    """The busiest one-second window seen, for calibrating the limits.

    This is the point of running in log-only mode: after a real session the
    peak is a measured number rather than the guess above, and the limit can be
    set from it instead of from an argument about what a console probably does.
    """

    def __init__(self) -> None:
        self._stamps: Deque[float] = collections.deque()
        self._lock = threading.Lock()
        self.peak = 0

    def record(self, now: Optional[float] = None) -> int:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._stamps.append(now)
            while self._stamps and now - self._stamps[0] > 1.0:
                self._stamps.popleft()
            if len(self._stamps) > _PEAK_SAMPLE_CAP:
                self._stamps.popleft()
            current = len(self._stamps)
            if current > self.peak:
                self.peak = current
            return current


class RateLimiter:
    """Message-rate limits, per connection and per source address.

    **Defaults to observing, not enforcing.** A rate limit that is too tight
    does not produce a polite failure on this client: the reply never comes,
    and an unanswered request wedges its pending queue permanently
    (0x00446ce0), so the console hangs rather than reporting anything. Shipping
    a guessed threshold into that is a bad trade, so the sequence is measure,
    then set, then turn on.
    """

    def __init__(self, rate: float = DEFAULT_RATE, burst: float = DEFAULT_BURST,
                 ip_rate: float = DEFAULT_IP_RATE,
                 ip_burst: float = DEFAULT_IP_BURST,
                 enforce: bool = False) -> None:
        self.rate = rate
        self.burst = burst
        self.ip_rate = ip_rate
        self.ip_burst = ip_burst
        self.enforce = enforce
        self._lock = threading.Lock()
        self._ip_buckets: Dict[str, TokenBucket] = {}
        self._ip_holders: Dict[str, int] = collections.defaultdict(int)
        self.connection_peak = _PeakObserver()
        self.violations = 0
        self.violations_per_ip = 0

    # -- per-connection ------------------------------------------------

    def new_bucket(self) -> TokenBucket:
        """A fresh bucket for one connection."""
        return TokenBucket(self.rate, self.burst)

    # -- per-address ---------------------------------------------------

    def attach(self, address: str) -> None:
        """Note that another connection from `address` exists."""
        with self._lock:
            self._ip_holders[address] += 1
            if address not in self._ip_buckets:
                self._ip_buckets[address] = TokenBucket(self.ip_rate,
                                                        self.ip_burst)

    def detach(self, address: str) -> None:
        """Drop the bucket once nothing is using it, so the map cannot grow
        once per address ever seen -- a slow leak keyed on attacker input."""
        with self._lock:
            held = self._ip_holders.get(address, 0)
            if held <= 1:
                self._ip_holders.pop(address, None)
                self._ip_buckets.pop(address, None)
            else:
                self._ip_holders[address] = held - 1

    # -- the check -----------------------------------------------------

    def check(self, bucket: Optional[TokenBucket],
              address: str) -> Optional[str]:
        """Charge one message. Returns None to allow, or a reason to refuse.

        A reason is returned even when not enforcing -- the caller decides what
        to do with it. That keeps "was this over the limit" and "should the
        connection die for it" as separate questions, which is what makes
        log-only mode possible at all.
        """
        self.connection_peak.record()
        if bucket is not None and not bucket.take():
            self.violations += 1
            return ("over the per-connection limit of %.0f/s (burst %.0f)"
                    % (self.rate, self.burst))
        with self._lock:
            ip_bucket = self._ip_buckets.get(address)
        if ip_bucket is not None and not ip_bucket.take():
            self.violations_per_ip += 1
            return ("%s is over the per-address limit of %.0f/s (burst %.0f)"
                    % (address, self.ip_rate, self.ip_burst))
        return None

    def describe(self) -> str:
        return ("%.0f/s burst %.0f per connection, %.0f/s burst %.0f per "
                "address, %s; peak seen %d msg/s"
                % (self.rate, self.burst, self.ip_rate, self.ip_burst,
                   "ENFORCING" if self.enforce else "observing only",
                   self.connection_peak.peak))


# ---------------------------------------------------------------------------
# bans
# ---------------------------------------------------------------------------

#: Strikes within `window` seconds before an address is refused outright.
DEFAULT_BAN_THRESHOLD = 5
DEFAULT_BAN_WINDOW = 120.0
DEFAULT_BAN_TTL = 600.0

#: Never hold more than this many addresses. Both maps are keyed on attacker
#: input, so they need a ceiling that does not depend on expiry running.
_MAX_TRACKED = 8192


class BanList:
    """Addresses that have earned a rest, and for how long.

    In memory on purpose. Bans that expire on restart are acceptable here --
    the threat is one bored person with a script, not a determined adversary --
    and persisting them would mean a mistake in the strike rules survives the
    fix for it.

    **Only hard signals earn strikes.** Malformed framing, a connection that
    never sends anything, one that never authenticates. A rate-limit violation
    counts only when the limiter is actually enforcing, because in log-only
    mode the thresholds are still guesses and banning on a guess would take a
    real player off the service for ten minutes with no way to tell them why.
    """

    def __init__(self, threshold: int = DEFAULT_BAN_THRESHOLD,
                 window: float = DEFAULT_BAN_WINDOW,
                 ttl: float = DEFAULT_BAN_TTL) -> None:
        self.threshold = threshold
        self.window = window
        self.ttl = ttl
        self._lock = threading.Lock()
        self._strikes: Dict[str, Deque[float]] = {}
        self._until: Dict[str, float] = {}
        self.banned_count = 0
        self.refused_count = 0

    def record(self, address: str, reason: str,
               now: Optional[float] = None) -> Optional[float]:
        """Add a strike. Returns the ban expiry if this one tipped it over.

        Disabled (threshold 0) still returns None rather than raising, so
        callers need no special case.
        """
        if not self.threshold:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            if len(self._strikes) >= _MAX_TRACKED and address not in self._strikes:
                return None
            marks = self._strikes.setdefault(address, collections.deque())
            marks.append(now)
            while marks and now - marks[0] > self.window:
                marks.popleft()
            if len(marks) < self.threshold:
                return None
            until = now + self.ttl
            self._until[address] = until
            self._strikes.pop(address, None)
            self.banned_count += 1
            return until

    def banned_for(self, address: str,
                   now: Optional[float] = None) -> float:
        """Seconds of ban remaining, 0 when the address may connect."""
        if not self.threshold:
            return 0.0
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._until.get(address)
            if until is None:
                return 0.0
            if until <= now:
                self._until.pop(address, None)
                return 0.0
            self.refused_count += 1
            return until - now

    def forget(self, address: str) -> None:
        with self._lock:
            self._until.pop(address, None)
            self._strikes.pop(address, None)

    def active(self, now: Optional[float] = None) -> List[Tuple[str, float]]:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            return sorted((a, u - now) for a, u in self._until.items())

    def _expire(self, now: float) -> None:
        """Caller holds the lock."""
        for address in [a for a, u in self._until.items() if u <= now]:
            self._until.pop(address, None)
        for address in [a for a, m in self._strikes.items()
                        if not m or now - m[-1] > self.window]:
            self._strikes.pop(address, None)
