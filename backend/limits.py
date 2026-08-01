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
from typing import Dict, Optional

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
