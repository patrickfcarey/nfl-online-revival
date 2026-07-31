"""Pairing two consoles, and the two ways the client asks for it.

The client has two routes into a match and they meet at the same place -- a
``+ses`` push to both players, after which the server is done and gameplay runs
peer to peer.

**Quickmatch** (``quik``, sent from 0x0011c468). One field, ``KIND=<signed
decimal>``, computed client-side as a CRC-32 over the build stamp and the game
settings. The server cannot interpret it and does not need to: two clients whose
``KIND`` matches are playing the same variant, which is the whole question.
Exact equality is also the only rule the client could verify, so it is the only
one worth implementing. ``KIND=*`` withdraws.

**Direct challenge** (``chal``, sent from 0x004e26e0). ``PERS=<opponent>`` plus
``HOST=<0|1>``. Both players send it, and they disagree on ``HOST`` by exactly
one bit -- that disagreement is how the server learns who hosts. The reply must
carry status 0 and arrive within 30 seconds (descriptor at 0x00573d88).

Two rules from the client's transport that shape everything here:

* **Every request must be answered.** Replies are matched against the *head* of
  a pending queue (0x00446ce0) and nothing pops it on a timeout, so one
  unanswered ``quik`` silently blocks every later reply on that connection.
  Both handlers therefore always return exactly one reply, including on the
  paths where there is nothing useful to say.
* **A queued client waits forever.** There is no client-side timeout in the
  quickmatch queue, so a player left in it is stuck until they cancel.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

#: The value that withdraws rather than queues, for both verbs.
CANCEL = "*"


class Matchmaker:
    """The waiting room. One instance per server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: KIND -> connections waiting on it, oldest first.
        self._waiting: Dict[str, List] = {}
        #: connection -> the KIND it is waiting on, so a disconnect can be
        #: cleaned up without scanning every queue.
        self._kind_of: Dict[int, str] = {}
        #: persona -> (connection, hosts) for challenges seen but not yet paired.
        self._challenges: Dict[str, Tuple] = {}

    # -- quickmatch ----------------------------------------------------

    def enqueue(self, connection, kind: str) -> Optional[Tuple]:
        """Queue a client, or pair it with one already waiting.

        Returns ``(host_connection, guest_connection)`` when this call completes
        a pair, otherwise ``None``. The waiting client becomes the host, purely
        because it has been connected longer and is therefore the more likely to
        be reachable -- the client expresses no preference.
        """
        with self._lock:
            self._forget(connection)
            queue = self._waiting.setdefault(kind, [])
            while queue:
                other = queue.pop(0)
                self._kind_of.pop(id(other), None)
                if other is connection or getattr(other, "closed", False):
                    continue
                return (other, connection)
            queue.append(connection)
            self._kind_of[id(connection)] = kind
            return None

    def withdraw(self, connection) -> bool:
        """Take a client out of the queue. True if it was in one."""
        with self._lock:
            return self._forget(connection)

    def waiting_count(self, kind: Optional[str] = None) -> int:
        with self._lock:
            if kind is not None:
                return len(self._waiting.get(kind, ()))
            return sum(len(q) for q in self._waiting.values())

    # -- direct challenge ----------------------------------------------

    def offer(self, persona: str, connection, hosts: bool) -> None:
        """Record one side of a challenge."""
        with self._lock:
            self._challenges[persona] = (connection, hosts)

    def resolve(self, persona: str, opponent: str) -> Optional[Tuple]:
        """Pair two challenges that name each other.

        Returns ``(host_connection, guest_connection)`` once both sides have
        been seen, else ``None``. Whoever set ``HOST=1`` hosts; if the two
        somehow agree, the player who challenged first does.
        """
        with self._lock:
            mine = self._challenges.get(persona)
            theirs = self._challenges.get(opponent)
            if mine is None or theirs is None:
                return None
            del self._challenges[persona]
            self._challenges.pop(opponent, None)
            my_conn, i_host = mine
            their_conn, they_host = theirs
            if i_host and not they_host:
                return (my_conn, their_conn)
            if they_host and not i_host:
                return (their_conn, my_conn)
            # Both claimed or both declined: fall back to the earlier offer.
            return (their_conn, my_conn)

    def cancel_challenge(self, persona: str) -> bool:
        with self._lock:
            return self._challenges.pop(persona, None) is not None

    # -- housekeeping --------------------------------------------------

    def forget(self, connection) -> None:
        """Drop a client from every queue. Call this on disconnect.

        Without it a dead connection stays in the queue and the next arrival is
        paired with a socket that has gone, which the client experiences as a
        match that never starts.
        """
        with self._lock:
            self._forget(connection)
            for persona, (conn, _hosts) in list(self._challenges.items()):
                if conn is connection:
                    del self._challenges[persona]

    def _forget(self, connection) -> bool:
        """Unqueue. Caller holds the lock."""
        kind = self._kind_of.pop(id(connection), None)
        if kind is None:
            return False
        queue = self._waiting.get(kind)
        if queue and connection in queue:
            queue.remove(connection)
            if not queue:
                del self._waiting[kind]
            return True
        return False
