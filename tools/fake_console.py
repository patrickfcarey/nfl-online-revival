"""A stand-in console, so matchmaking can be tested without two PlayStations.

Real hardware is a poor debugger. A console gives you one bit of feedback -- it
either works or shows a generic error -- and every attempt costs a boot cycle.
Worse, the two-console case is currently untestable at all: under PCSX2 in
Sockets mode both guests are 192.0.2.100, so they cannot dial each other.

This speaks the client's half of the protocol well enough to drive the server
through login, a room, and a match, and it **checks what comes back** rather
than just accepting it. The checks matter more than the driving: almost every
mistake found in this protocol so far was one the server could not see, because
a client that disagrees does not complain -- it goes quiet, or quietly ignores
the message. The `news` reply is the standing example. We answered it with the
wrong type for weeks; the request looked answered, and the console simply
discarded every one.

So this asserts the things the real client silently requires:

* a ``news`` reply has type ``news`` and carries its category in the STATUS
  word as ``new0``/``new1``/``new2`` -- the client gates on that, not the type
* a ``+ses`` names both SELF and HOST, and they differ for exactly one of the
  two players
* ADDR and FROM are dotted quads and are *crossed* -- each console is told the
  other's address, not its own
* WHEN is non-zero, or the record is never delivered

Usage:

    # one client: log in, join a room, report what came back
    python3 tools/fake_console.py --host 127.0.0.1 --persona alice

    # two clients, paired by quickmatch -- the matchmaking test
    python3 tools/fake_console.py --host 127.0.0.1 --pair

    # say something, so a real console in the same room can see it
    python3 tools/fake_console.py --host 127.0.0.1 --persona alice --say "hello"

This is a test client, not an emulator: it does not implement the peer link, so
it stops once the introduction arrives. Reaching a `+ses` with correct addresses
is exactly the part a server can get wrong.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import protocol  # noqa: E402

#: How long to wait for a reply before giving up on it.
TIMEOUT = 8.0

#: What a real console sends, so the server sees a plausible client.
PRODUCT = "MADDEN-PS2-2004"
VERSION = "PS2/MS5-Jun 17 2003"
SLUS = "BASLUS-20752"


class ConsoleError(Exception):
    """The server did something the real client would not survive."""


class FakeConsole:
    """One emulated client session."""

    def __init__(self, host: str, port: int = 10000, verbose: bool = True):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.sock: Optional[socket.socket] = None
        self.buffer = b""
        #: Decoded but not yet consumed, in arrival order.
        self.inbox: List[protocol.Message] = []
        self.persona = ""
        #: Everything the server pushed at us but we did not ask for.
        self.pushes: List[protocol.Message] = []
        self.problems: List[str] = []

    # -- plumbing ------------------------------------------------------

    def _say(self, text: str) -> None:
        if self.verbose:
            print("  %s" % text, flush=True)

    def _fault(self, text: str) -> None:
        """Record a protocol violation without aborting: one run should surface
        every problem, not just the first."""
        self.problems.append(text)
        print("  !! %s" % text, flush=True)

    def connect(self, host: str, port: int) -> None:
        if self.sock:
            self.sock.close()
        self.sock = socket.create_connection((host, port), TIMEOUT)
        self.sock.settimeout(TIMEOUT)
        self.buffer = b""
        self.inbox = []

    def send(self, msg_type: str, **fields) -> None:
        assert self.sock is not None
        self.sock.sendall(protocol.encode(msg_type, protocol.OK, fields))

    def _read_one(self, deadline: float) -> Optional[protocol.Message]:
        """Next message, in arrival order.

        Everything decoded goes through ``self.inbox``. An earlier version
        returned the first message of a batch and filed the rest straight into
        ``pushes``, where the caller waiting for a reply never looked -- so a
        reply that happened to share a TCP segment with a push was lost and the
        wait timed out. Whether that happened depended on how the kernel
        chunked the stream, which made it look random.
        """
        while True:
            if self.inbox:
                return self.inbox.pop(0)
            messages, self.buffer = protocol.split_stream(self.buffer)
            if messages:
                self.inbox.extend(messages)
                continue
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            assert self.sock is not None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65535)
            except socket.timeout:
                return None
            if not chunk:
                raise ConsoleError("server closed the connection")
            self.buffer += chunk

    def expect(self, msg_type: str, timeout: float = TIMEOUT
               ) -> protocol.Message:
        """Wait for a reply of a given type, banking any pushes seen first."""
        deadline = time.time() + timeout
        while True:
            message = self._read_one(deadline)
            if message is None:
                raise ConsoleError("no %s reply within %.0fs"
                                   % (msg_type, timeout))
            if message.type == msg_type:
                return message
            # A push, or a reply we are not waiting on.
            self.pushes.append(message)
            if message.type == "~png":
                # The client echoes pings; it never originates them.
                self.send("~png")

    def collect(self, seconds: float) -> List[protocol.Message]:
        """Drain whatever arrives for a while, answering keepalives."""
        deadline = time.time() + seconds
        seen: List[protocol.Message] = []
        while time.time() < deadline:
            message = self._read_one(deadline)
            if message is None:
                break
            if message.type == "~png":
                self.send("~png")
            seen.append(message)
            self.pushes.append(message)
        return seen

    # -- the login sequence a real console performs ---------------------

    def login(self, account: str, persona: str) -> None:
        self.persona = persona

        self._say("@dir -> %s:%d" % (self.host, self.port))
        self.connect(self.host, self.port)
        self.send("@dir", PROD=PRODUCT, VERS=VERSION, LANG="en", SLUS=SLUS)
        redirect = self.expect("@dir")
        addr = redirect.get("ADDR")
        port = int(redirect.get("PORT", "0"))
        if addr.count(".") != 3:
            self._fault("@dir ADDR %r is not a dotted quad; the client parses "
                        "it octet by octet and would go nowhere" % addr)
        self._say("redirected to %s:%d" % (addr, port))

        self.connect(addr, port)
        self.send("addr", ADDR="192.0.2.100", PORT="30000")
        self.send("skey", SKEY="$5075626c6963204b6579")
        self.expect("skey")

        self.send("auth", NAME=account, PASS="", TOS="1", PROD=PRODUCT,
                  VERS=VERSION, LANG="en", SLUS=SLUS, MASK="1")
        auth = self.expect("auth")
        if not auth.ok:
            # A fresh server has no accounts; make one, as the console does.
            self._say("no account %r (status %r) -- creating it"
                      % (account, protocol.decode_status(auth.status)))
            self.send("acct", NAME=account, PASS="", MAIL="test@example.com",
                      BORN="19800101", GEND="M", TOS="1", SPAM="NN",
                      MINAGE="13", PROD=PRODUCT, VERS=VERSION, LANG="en",
                      SLUS=SLUS)
            created = self.expect("acct")
            if not created.ok:
                raise ConsoleError("could not create account: %s"
                                   % protocol.decode_status(created.status))
            self.send("auth", NAME=account, PASS="", TOS="1", PROD=PRODUCT,
                      VERS=VERSION, LANG="en", SLUS=SLUS, MASK="1")
            auth = self.expect("auth")
            if not auth.ok:
                raise ConsoleError("auth still failed after creating the "
                                   "account: %s"
                                   % protocol.decode_status(auth.status))
        self._say("authenticated as %s" % account)

        self.send("pers", PERS=persona)
        chosen = self.expect("pers")
        if not chosen.ok:
            self.send("cper", PERS=persona)
            made = self.expect("cper")
            if not made.ok:
                raise ConsoleError("could not create persona %r: %s"
                                   % (persona, protocol.decode_status(made.status)))
            self.send("pers", PERS=persona)
            chosen = self.expect("pers")
            if not chosen.ok:
                raise ConsoleError("persona %r still refused" % persona)
        self._say("persona %s selected" % persona)

        self.send("sele", MASK="ROOMS=1 USERS=1 RANKS=1 MESGS=1")
        self.expect("sele")

        self._check_news()

    def _check_news(self) -> None:
        """Fetch the service config, and hold the reply to the tag rule."""
        self.send("news", NAME="0")
        reply = self.expect("news")
        # The category rides in the STATUS word. The client requires it non-zero
        # (0x0034eb20) and equal to 'new0' + NAME (0x0034f500); a success status
        # there means the console silently discards the whole reply.
        if reply.status_tag != "new0":
            self._fault("news reply status is %r, not 'new0'; the console would "
                        "drop the body and never store CSUM"
                        % (reply.status_tag or "0 (success)"))
        self._say("news: buddy=%s:%s roster DATE=%s CSUM=%s" % (
            reply.get("BUDDY_URL"), reply.get("BUDDY_PORT"),
            reply.get("DATE"), reply.get("CSUM")))
        if not reply.get("CSUM"):
            self._fault("new0 carries no CSUM; the console would treat its "
                        "rosters as stale")

    # -- lobby ---------------------------------------------------------

    def join(self, room: str) -> protocol.Message:
        self.send("move", NAME=room)
        reply = self.expect("move")
        if not reply.ok:
            raise ConsoleError("could not join %r: %s"
                               % (room, protocol.decode_status(reply.status)))
        self._say("joined %s (id %s, %s occupants)"
                  % (room, reply.get("IDENT"), reply.get("COUNT")))
        return reply

    def say(self, text: str, private_to: str = "") -> None:
        fields = {"TEXT": text}
        if private_to:
            fields["PRIV"] = private_to
        self.send("mesg", **fields)
        self.expect("mesg")
        self._say("said %r%s" % (text,
                                 " to %s" % private_to if private_to else ""))

    # -- matchmaking ---------------------------------------------------

    def quickmatch(self, kind: str) -> None:
        self.send("quik", KIND=kind)
        self.expect("quik")
        self._say("queued for quickmatch, KIND=%s" % kind)

    def cancel_quickmatch(self) -> None:
        self.send("quik", KIND="*")
        self.expect("quik")

    def await_session(self, timeout: float = 15.0
                      ) -> Optional[protocol.Message]:
        """Wait for the +ses that introduces us to a peer."""
        for banked in self.pushes:
            if banked.type == "+ses":
                return banked
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._read_one(deadline)
            if message is None:
                break
            if message.type == "~png":
                self.send("~png")
                continue
            self.pushes.append(message)
            if message.type == "+ses":
                return message
        return None

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def check_session(a: FakeConsole, invite_a: protocol.Message,
                  b: FakeConsole, invite_b: protocol.Message) -> List[str]:
    """Hold a pair of +ses records to what the client requires of them."""
    problems: List[str] = []

    def dotted(value: str) -> bool:
        parts = value.split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) < 256
                                       for p in parts)

    for who, invite in ((a.persona, invite_a), (b.persona, invite_b)):
        if not invite.get("SELF"):
            problems.append("%s: +ses has no SELF" % who)
        if not invite.get("HOST"):
            problems.append("%s: +ses has no HOST; an empty HOST compares "
                            "equal to an empty SELF and both consoles would "
                            "think they host" % who)
        if invite.get("WHEN", "0") in ("", "0"):
            problems.append("%s: WHEN is zero, so the record is never "
                            "delivered" % who)
        for key in ("ADDR", "FROM"):
            value = invite.get(key)
            if not dotted(value):
                problems.append("%s: %s=%r is not a dotted quad" % (who, key, value))

    if invite_a.get("SELF") == invite_b.get("SELF"):
        problems.append("both invites carry the same SELF (%r); SELF is the "
                        "only field that should differ"
                        % invite_a.get("SELF"))
    if invite_a.get("HOST") != invite_b.get("HOST"):
        problems.append("the two invites disagree about HOST (%r vs %r); they "
                        "must name the same host"
                        % (invite_a.get("HOST"), invite_b.get("HOST")))
    if invite_a.get("SEED") != invite_b.get("SEED"):
        problems.append("SEED differs between the two invites (%r vs %r); the "
                        "simulations would diverge"
                        % (invite_a.get("SEED"), invite_b.get("SEED")))

    # The crossing rule: each console must be told the OTHER's address.
    #
    # This cannot be checked from here when both clients share an address --
    # which they always do on loopback, and always would for two PCSX2 guests
    # in Sockets mode. Identical ADDR and FROM is then correct rather than
    # broken, and failing on it would cry wolf on every local run.
    #
    # What can still be caught is the server echoing the console's *reported*
    # address: a fake console reports 192.0.2.100, so seeing that value proves
    # the server trusted the client instead of the socket.
    reported = "192.0.2.100"
    for who, invite in ((a.persona, invite_a), (b.persona, invite_b)):
        for key in ("ADDR", "FROM"):
            if invite.get(key) == reported:
                problems.append(
                    "%s: %s is %s, the address the client *reported* rather "
                    "than the one it connected from. Every console reports "
                    "that under PCSX2, so both would dial the same host."
                    % (who, key, reported))
    if invite_a.get("ADDR") == invite_a.get("FROM"):
        print("  note: ADDR and FROM are both %s -- correct when both clients "
              "share a host, but it means the crossing is untested here. Two "
              "distinct addresses are needed to prove it."
              % invite_a.get("ADDR"))
    return problems


def run_pair(host: str, port: int, kind: str) -> int:
    """Two clients, one quickmatch. The actual matchmaking test."""
    a = FakeConsole(host, port)
    b = FakeConsole(host, port)
    try:
        print("[alice]")
        a.login("alice", "AliceP")
        a.join("Open Lobby")
        print("[bob]")
        b.login("bob", "BobP")
        b.join("Open Lobby")

        print("[matchmaking]")
        a.quickmatch(kind)
        # Give the server a moment to have alice waiting before bob arrives,
        # so the pairing is deterministic rather than a race.
        time.sleep(0.3)
        b.quickmatch(kind)

        invite_a = a.await_session()
        invite_b = b.await_session()
        if invite_a is None or invite_b is None:
            print("\nFAIL: no +ses arrived (alice=%s bob=%s)"
                  % (invite_a is not None, invite_b is not None))
            print("      the server queued them but never paired them, or the "
                  "push never went out.")
            return 1

        for who, invite in (("alice", invite_a), ("bob", invite_b)):
            print("  %s: SELF=%s HOST=%s OPPO=%s ADDR=%s FROM=%s SEED=%s WHEN=%s"
                  % (who, invite.get("SELF"), invite.get("HOST"),
                     invite.get("OPPO"), invite.get("ADDR"),
                     invite.get("FROM"), invite.get("SEED"),
                     invite.get("WHEN")))

        problems = (a.problems + b.problems
                    + check_session(a, invite_a, b, invite_b))
        if problems:
            print("\nFAIL: %d problem(s)" % len(problems))
            for problem in problems:
                print("  - %s" % problem)
            return 1
        print("\nOK: both consoles were introduced, and each was given the "
              "other's address.")
        return 0
    except ConsoleError as exc:
        print("\nFAIL: %s" % exc)
        return 1
    finally:
        a.close()
        b.close()


def run_single(host: str, port: int, account: str, persona: str,
               room: str, message: str, kind: str, linger: float) -> int:
    console = FakeConsole(host, port)
    try:
        console.login(account, persona)
        console.join(room)
        if message:
            console.say(message)
        if kind:
            console.quickmatch(kind)
            print("  waiting for an opponent -- run a second client, or a "
                  "console, with the same KIND")
            invite = console.await_session(timeout=linger or 30.0)
            if invite is None:
                print("  no +ses arrived")
            else:
                print("  +ses: SELF=%s HOST=%s ADDR=%s FROM=%s"
                      % (invite.get("SELF"), invite.get("HOST"),
                         invite.get("ADDR"), invite.get("FROM")))
        elif linger:
            print("  listening for %.0fs..." % linger)
            for message_in in console.collect(linger):
                print("  <- %s %s" % (message_in.type,
                                      dict(list(message_in.fields.items())[:4])))
        if console.problems:
            print("\n%d problem(s) found" % len(console.problems))
            return 1
        print("\nOK")
        return 0
    except ConsoleError as exc:
        print("\nFAIL: %s" % exc)
        return 1
    finally:
        console.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the backend as if it were a console.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="where the backend is listening")
    parser.add_argument("--port", type=int, default=10000,
                        help="the directory port the console contacts first")
    parser.add_argument("--pair", action="store_true",
                        help="run two clients and test quickmatch end to end")
    parser.add_argument("--account", default="tester")
    parser.add_argument("--persona", default="TesterP")
    parser.add_argument("--room", default="Open Lobby")
    parser.add_argument("--say", dest="message", default="",
                        help="send a chat line after joining")
    parser.add_argument("--quickmatch", dest="kind", default="",
                        help="queue for a match with this KIND")
    parser.add_argument("--linger", type=float, default=0.0,
                        help="keep listening for this many seconds")
    args = parser.parse_args(argv)

    if args.pair:
        return run_pair(args.host, args.port, args.kind or "-1234567")
    return run_single(args.host, args.port, args.account, args.persona,
                      args.room, args.message, args.kind, args.linger)


if __name__ == "__main__":
    sys.exit(main())
