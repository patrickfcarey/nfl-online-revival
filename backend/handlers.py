"""One handler per message type, plus the session state they operate on.

Handlers are registered by type, so supporting a new message is additive: write
a function, decorate it, done. Nothing dispatches on a chain of ifs that has to
be edited in two places.

Every handler returns a list of messages to send, so a handler that must both
answer and push can, and one that should stay silent returns nothing. Silence
is meaningful in this protocol -- the client moves past some messages without
waiting -- so it is expressed directly rather than as a special case.

Error tags are the client's own, recovered from its tables:
``0x00573c20`` for accounts (``dupl`` ``inam`` ``mail`` ``pass`` ``tooy`` ...)
and ``0x0056d4dc`` for tournaments (``full`` ``dupl`` ``strt`` ...). Returning
the right tag makes the client show the right screen; returning a wrong one
sends the user to a misleading error, which is worse than a generic failure.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable, Dict, List, Optional

from . import lobby, matchmaking, protocol
from .protocol import Message, ProtocolError
from .store import MAX_PERSONA_LEN, MAX_PERSONAS, Store

# --- error tags the client understands -------------------------------------
ERR_DUPLICATE = "dupl"       # name already taken
ERR_BAD_NAME = "inam"        # invalid name
ERR_NAME_LENGTH = "elen"     # name length
ERR_BAD_MAIL = "mail"        # invalid address
ERR_BAD_PASS = "pass"        # wrong password
ERR_TOS = "tosa"             # terms not accepted
ERR_BORN = "born"            # bad date of birth
ERR_GEND = "gend"            # bad gender
ERR_TOO_YOUNG = "tooy"       # under the age limit
ERR_TOO_MANY = "many"        # limit reached
ERR_MISSING = "miss"         # not found
ERR_INTERNAL = "misc"        # generic failure, from the same table

#: States in which the client's own request wrapper (0x00448050) will send.
#: Mirroring the gate means a message that arrives before the session is
#: established is refused rather than acted on, which would leave the two sides
#: disagreeing about where they are.
OPEN_STATES = frozenset(("idle", "auth", "acct", "skey"))

#: Names the client will accept back. Kept deliberately conservative: the field
#: is echoed into UI and into other players' screens.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,31}$")
_PERSONA_RE = re.compile(r"^[A-Za-z0-9_.-]{1,%d}$" % MAX_PERSONA_LEN)
_MAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Registration sends MINAGE as 13 or 18 depending on a checkbox; a birth date
#: below it is what the `tooy` screen exists for.
DEFAULT_MIN_AGE = 13


class Session:
    """Per-connection state. Ephemeral by design -- never written to the store.

    Mirrors the client's own state machine so a handler can refuse a message
    that arrives out of order, rather than acting on it and leaving the two
    sides disagreeing about where they are.
    """

    def __init__(self, peer: str, listen_port: int = 0) -> None:
        self.peer = peer
        self.listen_port = listen_port
        self.state = "conn"
        self.session_key: bytes = b""
        self.account: Optional[str] = None
        self.persona: Optional[str] = None
        self.client_addr: str = ""
        self.client_port: int = 0
        self.product: str = ""
        self.opened = time.time()
        self.last_seen = self.opened
        #: Subscriptions requested via `sele` ("ROOMS=1 USERS=1 ...").
        #: Until these exist the client silently drops +rom/+usr/+pop.
        self.subscriptions: Dict[str, bool] = {}
        #: Current room name, or None. Rooms are joined by NAME, not by id.
        self.room: Optional[str] = None
        #: Numeric room id the client believes it is in (its conn+440).
        self.room_id: int = 0
        #: Stable id for this occupant in other clients' user lists. Assigned
        #: on connect; ids must be positive or the client discards the record.
        self.user_id: int = 0
        #: Favourite team, from `cusr` SETFAV. Session-only for now.
        self.favourite: str = ""
        #: A pairing waiting to be announced once this session's reply has
        #: gone out. The +ses push must never precede the reply that the
        #: client is still inside.
        self.pending_pair = None

    @property
    def authenticated(self) -> bool:
        return self.account is not None

    @property
    def observed_addr(self) -> str:
        """The address we actually see this client connecting from.

        Deliberately not ``client_addr``. That is what the console *reports*
        in its ``addr`` message, sampled from the pre-redirect socket, and it is
        the console's own local address -- under PCSX2 in Sockets mode every
        guest is 192.0.2.100. Putting it in a ``+ses`` makes each console dial
        itself. What the peer needs is the address we observed on the TCP
        connection.
        """
        return self.peer.rsplit(":", 1)[0] if ":" in self.peer else self.peer

    def describe(self) -> str:
        who = self.account or "-"
        return "%s state=%s account=%s persona=%s room=%s" % (
            self.peer, self.state, who, self.persona or "-", self.room or "-")


class Context:
    """What a handler is given: the message, its session, and the store."""

    def __init__(self, message: Message, session: Session, store: Store,
                 config: Dict[str, str], hub=None, connection=None) -> None:
        self.message = message
        self.session = session
        self.store = store
        self.config = config
        #: Present when a handler needs to reach clients other than its own.
        #: Optional so unit tests can dispatch without a live socket.
        self.hub = hub
        self.connection = connection

    # Convenience so handlers read cleanly.
    def reply(self, **fields) -> List[bytes]:
        return [protocol.encode(self.message.type, protocol.OK, fields)]

    def fail(self, tag: str) -> List[bytes]:
        return [protocol.encode(self.message.type, tag, {})]

    def silent(self) -> List[bytes]:
        return []


Handler = Callable[[Context], List[bytes]]
_HANDLERS: Dict[str, Handler] = {}


def handles(msg_type: str) -> Callable[[Handler], Handler]:
    """Register a handler for one message type."""
    def register(func: Handler) -> Handler:
        if len(msg_type) != 4:
            raise ValueError("message type must be 4 characters: %r" % msg_type)
        _HANDLERS[msg_type] = func
        return func
    return register


def handler_for(msg_type: str) -> Optional[Handler]:
    return _HANDLERS.get(msg_type)


def known_types() -> List[str]:
    return sorted(_HANDLERS)


# --------------------------------------------------------------------------
# session establishment
# --------------------------------------------------------------------------

@handles("@dir")
def directory(ctx: Context) -> List[bytes]:
    """Redirect the client to the game service.

    ``ADDR`` must be a dotted quad: the client parses it with a routine that
    splits on '.', so an integer here is read as a single octet and the
    redirect silently goes nowhere.
    """
    ctx.session.product = ctx.message.get("PROD")
    return ctx.reply(
        ADDR=ctx.config["advertise_host"],
        PORT=ctx.config["advertise_port"],
        SESS="1",
        MASK=ctx.config.get("mask", "GS"),
    )


@handles("addr")
def client_address(ctx: Context) -> List[bytes]:
    """Record where the client says it is. It does not wait for an answer."""
    ctx.session.client_addr = ctx.message.get("ADDR")
    try:
        ctx.session.client_port = int(ctx.message.get("PORT", "0"))
    except ValueError:
        ctx.session.client_port = 0
    return ctx.silent()


@handles("skey")
def session_key(ctx: Context) -> List[bytes]:
    """Complete the key exchange and open the session.

    The value is ``$`` followed by hex. A zero key is deliberate: with it the
    client masks passwords against ``MASK`` instead of encrypting them under a
    cipher we have not reversed, which keeps what arrives here inspectable.
    """
    ctx.session.state = "idle"
    return ctx.reply(SKEY="$" + "00" * 16)


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def _birth_year(born: str) -> Optional[int]:
    """BORN arrives as YYYYMMDD."""
    if len(born) != 8 or not born.isdigit():
        return None
    return int(born[:4])


@handles("acct")
def create_account(ctx: Context) -> List[bytes]:
    """New member registration."""
    if ctx.session.state not in OPEN_STATES:
        return ctx.fail(ERR_INTERNAL)
    message = ctx.message
    name = message.get("NAME").strip()
    if not name:
        return ctx.fail(ERR_BAD_NAME)
    if not _NAME_RE.match(name):
        return ctx.fail(ERR_BAD_NAME if len(name) <= 31 else ERR_NAME_LENGTH)

    mail = message.get("MAIL").strip()
    if mail and not _MAIL_RE.match(mail):
        return ctx.fail(ERR_BAD_MAIL)
    if message.get("TOS", "0") in ("", "0"):
        return ctx.fail(ERR_TOS)

    born = message.get("BORN")
    year = _birth_year(born) if born else None
    if born and year is None:
        return ctx.fail(ERR_BORN)
    if year is not None:
        try:
            minimum = int(message.get("MINAGE", str(DEFAULT_MIN_AGE)))
        except ValueError:
            minimum = DEFAULT_MIN_AGE
        if time.gmtime().tm_year - year < minimum:
            return ctx.fail(ERR_TOO_YOUNG)

    gender = message.get("GEND")
    if gender and gender not in ("M", "F"):
        return ctx.fail(ERR_GEND)

    try:
        ctx.store.create_account(
            name,
            PASS=message.get("PASS"),
            MAIL=mail or None,
            PMAIL=message.get("PMAIL") or None,
            BORN=born or None,
            GEND=gender or None,
            SPAM=message.get("SPAM") or None,
            TOS=1,
            ALTS=int(message.get("ALTS", MAX_PERSONAS) or MAX_PERSONAS),
            CDEV=message.get("CDEV") or None,
        )
    except sqlite3.IntegrityError:
        # The database owns uniqueness, so two simultaneous registrations
        # cannot both win; whichever loses gets the client's own tag.
        return ctx.fail(ERR_DUPLICATE)
    except (ValueError, sqlite3.Error):
        return ctx.fail(ERR_BAD_NAME)

    ctx.session.account = name
    ctx.session.state = "acct"
    return ctx.reply()


@handles("auth")
def login(ctx: Context) -> List[bytes]:
    """Log in and hand back the persona list.

    ``PASS`` is compared as presented. The client encrypts or masks it before
    sending and we cannot reverse that, so this checks that the same client
    with the same key produces the same value -- see the note in store.py.
    """
    if ctx.session.state not in OPEN_STATES:
        return ctx.fail(ERR_INTERNAL)
    name = ctx.message.get("NAME").strip()
    row = ctx.store.account(name) if name else None
    if row is None:
        return ctx.fail(ERR_MISSING)
    # Compare unconditionally. Skipping the check when the stored value is
    # empty would let an account created without a password accept any password
    # at all, which is the opposite of what an empty password means.
    presented = ctx.message.get("PASS")
    if presented != (row["PASS"] or ""):
        return ctx.fail(ERR_BAD_PASS)

    ctx.session.account = name
    ctx.session.state = "auth"
    personas = ctx.store.personas(name)
    return ctx.reply(
        NAME=name,
        ADDR=ctx.config["advertise_host"],
        PERSONAS=",".join(personas),
        OPTS=row["OPTS"] or "",
    )


@handles("pers")
def select_persona(ctx: Context) -> List[bytes]:
    """Choose which persona this session plays as."""
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)
    persona = ctx.message.get("PERS").strip()
    if persona not in ctx.store.personas(ctx.session.account):
        return ctx.fail(ERR_MISSING)
    ctx.session.persona = persona
    return ctx.reply(PERS=persona)


@handles("cper")
def create_persona(ctx: Context) -> List[bytes]:
    """Create a persona, refusing the cases the client has screens for."""
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)
    persona = ctx.message.get("PERS").strip()
    if not persona or not _PERSONA_RE.match(persona):
        return ctx.fail(ERR_BAD_NAME)
    existing = ctx.store.personas(ctx.session.account)
    if len(existing) >= MAX_PERSONAS:
        return ctx.fail(ERR_TOO_MANY)
    try:
        ctx.store.create_persona(ctx.session.account, persona)
    except sqlite3.IntegrityError:
        return ctx.fail(ERR_DUPLICATE)
    personas = ctx.store.personas(ctx.session.account)
    return ctx.reply(PERS=persona, PERSONAS=",".join(personas))


@handles("dper")
def delete_persona(ctx: Context) -> List[bytes]:
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)
    persona = ctx.message.get("PERS").strip()
    if not ctx.store.delete_persona(ctx.session.account, persona):
        return ctx.fail(ERR_MISSING)
    if ctx.session.persona == persona:
        ctx.session.persona = None
    return ctx.reply(PERSONAS=",".join(ctx.store.personas(ctx.session.account)))


@handles("pass")
def change_password(ctx: Context) -> List[bytes]:
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)
    ctx.store.update_account(ctx.session.account, PASS=ctx.message.get("PASS"))
    return ctx.reply()


# --------------------------------------------------------------------------
# service
# --------------------------------------------------------------------------

@handles("sele")
def subscribe(ctx: Context) -> List[bytes]:
    """Register push subscriptions: ``ROOMS=1 USERS=1 RANKS=1 MESGS=1``.

    The client sends these space-separated in the body rather than one per
    line, so the standard field split sees a single key. Both shapes are
    accepted because only one of them has been observed.
    """
    body = ctx.message.raw_payload.split(b"\x00", 1)[0].decode("latin-1")
    for token in body.replace("\n", " ").split():
        if "=" in token:
            key, value = token.split("=", 1)
            ctx.session.subscriptions[key.strip()] = value.strip() not in ("", "0")
    return ctx.reply(MORE="0", SLOTS=str(MAX_PERSONAS))


def after_subscribe(ctx: Context) -> None:
    """Send the room list, now that the slots to hold it exist.

    Before the `sele` reply the client has no subscription slots and drops
    +rom/+usr/+pop silently, so this cannot be done any earlier.
    """
    if ctx.session.subscriptions.get("ROOMS", True):
        _push_room_list(ctx)


@handles("~png")
def ping(ctx: Context) -> List[bytes]:
    """A keepalive coming back to us -- acknowledge it by staying quiet.

    The client never originates a ping. It intercepts any `~png` it receives
    and echoes it verbatim before anything else (0x00448C58), so this message
    is the echo of one *we* sent. Replying would make it echo again, and the
    two of us would ping each other forever.

    The direction that matters is the other one: the client's receive deadline
    is reset to now + 60 s by every message that arrives, so the server has to
    send something within that window or the session is torn down. See the
    keepalive in service.py.
    """
    ctx.session.last_seen = time.time()
    return ctx.silent()


def dispatch(ctx: Context) -> List[bytes]:
    """Route a message to its handler, or stay silent if none is registered."""
    handler = handler_for(ctx.message.type)
    if handler is None:
        return []
    return handler(ctx)


# --------------------------------------------------------------------------
# lobby
# --------------------------------------------------------------------------

ERR_BAD_PASSWORD = "pass"    # wrong room password
ERR_FULL = "full"            # room at capacity

#: The peer port both consoles connect on is hardcoded in the client
#: (0x004e2c1c). The server only introduces the two players.
PEER_PORT = 3658

#: TWRP is the one `news` field whose converter carries a non-zero default
#: (240, at 0x0034f078). Matching it means sending the field changes nothing
#: unless we mean it to.
TWRP_DEFAULT = 240

# --- roster version -------------------------------------------------------
#
# The console computes the checksum of its own roster and compares it against
# the CSUM we announce; a difference means "yours is stale".
#
# The algorithm is now known and implemented in tools/roster_checksum.py, so a
# real value can be computed from the roster being served -- run the server with
# `--roster-db DB_TEAMS.DAT` and it announces a checksum the console agrees
# with. The fallback below is still a placeholder and still will not match.
#
# Two things follow from serving a real value. The pnach line that zeroes the
# comparison (0x0012A978) becomes unnecessary and should be removed, because
# leaving it in would suppress a legitimate update once rosters actually differ.
# And the mismatch path becomes meaningful: announce a checksum that does not
# match and the console asks to download, which is the hook a roster update
# would hang off.
#: The roster version has **two** independent staleness tests, and satisfying
#: only one of them is not enough. This was the harder of the two to see,
#: because the failure looks identical either way.
#:
#: 0x00350960 is not the getter it appears to be -- its answer is in the delay
#: slot::
#:
#:     00350960  lw   v0, 2452(gp)     ; the DATE we announced
#:     00350964  jr   ra
#:     00350968  sltu v0, a0, v0       ; return (local_date < server_DATE)
#:
#: So the script hands it the console's own roster date and asks whether ours is
#: newer. Announcing a real date therefore tells every console with a lower one
#: -- which, on a disc that has never taken an update, means essentially all of
#: them -- that an update exists, however well the checksum matches.
#:
#: Zero is the honest default: it claims nothing is newer than what the console
#: already holds, and the comparison is unsigned, so no local date can be below
#: it. Announce a real date only when actually serving a newer roster.
DEFAULT_ROSTER_DATE = "0"

#: Not derived from any roster. Serve one with --roster-db instead; the console
#: computes its own and compares (0x0012a960), and a wrong value here is the
#: other half of the staleness test.
PLACEHOLDER_ROSTER_CSUM = "0"


def _occupancy(ctx: Context, room_name: str) -> int:
    return len(ctx.hub.in_room(room_name)) if ctx.hub else 0


def _push_room_list(ctx: Context) -> None:
    """Send the whole room list to this client."""
    if ctx.hub is None or ctx.connection is None:
        return
    for blob in lobby.room_listing(ctx.store.rooms(),
                                   lambda name: _occupancy(ctx, name)):
        ctx.hub.push(ctx.connection, blob)


def _push_occupants(ctx: Context, room_name: str) -> None:
    """Re-send every occupant of *room_name* to this client.

    Required after any room change: the client drains and frees its entire user
    list when the room id changes, so anyone not re-sent simply vanishes.
    """
    if ctx.hub is None or ctx.connection is None:
        return
    count = _occupancy(ctx, room_name)
    for other in ctx.hub.in_room(room_name):
        ctx.hub.push(ctx.connection, lobby.user_record(
            other.session.user_id,
            other.session.persona or other.session.account or "player",
            addr=other.session.client_addr or "0.0.0.0",
            room_occupants=count,
            is_self=other is ctx.connection))


def _announce(ctx: Context, room_name: str, blob: bytes) -> None:
    """Tell everyone else in a room about a change."""
    if ctx.hub is None or not room_name:
        return
    ctx.hub.broadcast([blob], room=room_name, exclude=ctx.connection)


@handles("move")
def move_room(ctx: Context) -> List[bytes]:
    """Join a room, or leave with an empty NAME.

    Rooms are joined by NAME, not by id -- the id only appears in the reply and
    in pushes. The reply carries both halves: LIDENT/LCOUNT describe the room
    being left, IDENT/COUNT/NAME the one being entered.
    """
    session = ctx.session

    def refuse(tag: str) -> List[bytes]:
        """Fail a move WITHOUT destroying the client's lobby.

        A bare error reply is not safe here. The client's status gate
        (0x0044a2ac) only skips the LIDENT/LCOUNT half; the second half is gated
        on the message *type*, so it reads on regardless, takes IDENT's default
        of 0 (0x0044a32c), adopts that as its current room, and then drains its
        whole user list at 0x0044a3d8. A wrong password would blank the lobby
        while we still believed the player was where they were.

        Echoing the session's current room makes the `beq s0, v0` at 0x0044a35c
        match, so the client sees no change and keeps its list.
        """
        room = session.room
        return [protocol.encode("move", tag, {
            "LIDENT": str(session.room_id or -1),
            "LCOUNT": str(_occupancy(ctx, room) if room else 0),
            "IDENT": str(session.room_id or -1),
            "COUNT": str(_occupancy(ctx, room) if room else 0),
            "NAME": room or "",
            "FLAGS": "",
        })]

    if not session.authenticated:
        return refuse(ERR_MISSING)

    target = ctx.message.get("NAME").strip()
    previous = session.room
    previous_id = session.room_id
    previous_count = max(0, _occupancy(ctx, previous) - 1) if previous else 0

    if not target:                                   # leaving
        session.room, session.room_id = None, 0
        if previous:
            _announce(ctx, previous, lobby.user_removal(
                session.user_id, _occupancy(ctx, previous)))
        return [protocol.encode("move", protocol.OK, {
            "LIDENT": str(previous_id or -1),
            "LCOUNT": str(previous_count),
            "IDENT": "-1",
            "COUNT": "0",
            "NAME": "",
            "FLAGS": "",
        })]

    row = ctx.store.room(target)
    if row is None:
        return refuse(ERR_MISSING)
    if row["PASS"] and ctx.message.get("PASS") != row["PASS"]:
        # `pass` is the one tag the client special-cases (0x00356ae4), so it
        # gets a password prompt rather than a generic failure.
        return refuse(ERR_BAD_PASSWORD)
    limit = row["MAX"] or 0
    if limit and _occupancy(ctx, target) >= limit:
        return refuse(ERR_FULL)

    if previous and previous != target:
        _announce(ctx, previous, lobby.user_removal(
            session.user_id, _occupancy(ctx, previous)))
    session.room, session.room_id = target, row["ID"]

    reply = protocol.encode("move", protocol.OK, {
        "LIDENT": str(previous_id or -1),
        "LCOUNT": str(previous_count),
        "IDENT": str(row["ID"]),
        "COUNT": str(_occupancy(ctx, target)),
        "NAME": target,
        "FLAGS": "",
    })
    return [reply]


def after_move(ctx: Context) -> None:
    """Re-populate the client's lists once the move reply has gone.

    Kept separate from the handler because ordering matters: the reply must
    reach the client first, since it is what sets the room id that makes the
    following pushes meaningful.
    """
    if not ctx.session.room:
        return
    _push_occupants(ctx, ctx.session.room)
    _announce(ctx, ctx.session.room, lobby.user_record(
        ctx.session.user_id,
        ctx.session.persona or ctx.session.account or "player",
        addr=ctx.session.client_addr or "0.0.0.0",
        room_occupants=_occupancy(ctx, ctx.session.room)))


#: Work that has to run once a reply has been written, keyed by message type.
#: Ordering is the whole point: these pushes are meaningless until the reply
#: that establishes the client's new state has arrived.
# --------------------------------------------------------------------------
# matchmaking
# --------------------------------------------------------------------------

#: Field names, from the client's own builders.
QUICK_KIND = "KIND"          # 0x0011c4b8, signed decimal
CHALLENGE_PERSONA = "PERS"   # 0x004e1ee0
CHALLENGE_HOST = "HOST"      # 0x004e1ee0, "0" or "1"


def _matchmaker(ctx: Context):
    """The server's waiting room, or None when dispatching without a hub."""
    return getattr(ctx.hub, "matchmaker", None) if ctx.hub else None


def _pair(ctx: Context, pairing) -> None:
    """Introduce two paired connections, if both are still there."""
    if pairing is None:
        return
    host_conn, guest_conn = pairing
    start_match(ctx.hub, host_conn, guest_conn)


@handles("quik")
def quickmatch(ctx: Context) -> List[bytes]:
    """Join or leave the quickmatch queue.

    Always replies. An unanswered request sits at the head of the client's
    pending queue forever and blocks every later reply on the connection, so
    even the paths with nothing to say answer with an empty body.
    """
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)

    kind = ctx.message.get(QUICK_KIND).strip()
    maker = _matchmaker(ctx)
    if maker is None:
        return ctx.reply()

    if kind == matchmaking.CANCEL or not kind:
        maker.withdraw(ctx.connection)
        return ctx.reply()

    pairing = maker.enqueue(ctx.connection, kind)
    # The reply goes out before the +ses push: the client is still inside its
    # request when this returns, and a session record delivered first would be
    # consumed against a state it has not reached.
    reply = ctx.reply()
    if pairing is not None:
        ctx.session.pending_pair = pairing
    return reply


def after_quickmatch(ctx: Context) -> None:
    """Push +ses once the quik reply has gone."""
    pairing = getattr(ctx.session, "pending_pair", None)
    if pairing is not None:
        ctx.session.pending_pair = None
        _pair(ctx, pairing)


@handles("chal")
def challenge(ctx: Context) -> List[bytes]:
    """A direct challenge against a named persona.

    Both players send this and disagree on ``HOST`` by one bit, which is how
    the server learns who hosts. The reply must be status 0 and must arrive
    within 30 seconds (0x004e1e40 aborts on a timeout or any non-zero status).
    """
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)

    opponent = ctx.message.get(CHALLENGE_PERSONA).strip()
    me = ctx.session.persona or ctx.session.account or ""
    maker = _matchmaker(ctx)
    if maker is None:
        return ctx.reply()

    if opponent == matchmaking.CANCEL or not opponent:
        maker.cancel_challenge(me)
        return ctx.reply()

    hosts = ctx.message.get(CHALLENGE_HOST, "0") not in ("", "0")
    maker.offer(me, ctx.connection, hosts)
    pairing = maker.resolve(me, opponent)
    reply = ctx.reply()
    if pairing is not None:
        ctx.session.pending_pair = pairing
    return reply


# --------------------------------------------------------------------------
# verbs the client can send that we do not implement yet
# --------------------------------------------------------------------------

#: Every remaining type from the 27 call sites of the client's request sender
#: (0x004df3d8). These are acknowledged rather than ignored because an
#: unanswered request wedges the connection: the reply queue is matched head
#: first (0x00446ce0) and nothing pops it on a timeout, so one silent verb
#: blocks every later reply.
#:
#: ``onln`` matters most -- it goes through the *modal* path (0x0034f6c8), so
#: the client blocks on it rather than carrying on.
UNIMPLEMENTED_VERBS = ("rank", "snap", "peek", "flag", "lost", "edit",
                       "user", "onln")


def _acknowledge(ctx: Context) -> List[bytes]:
    """Answer with success and an empty body -- the least surprising thing to
    tell a client whose request we have not implemented."""
    return ctx.reply()


for _verb in UNIMPLEMENTED_VERBS:
    handles(_verb)(_acknowledge)


AFTER_REPLY = {
    "move": after_move,
    "sele": after_subscribe,
    # +ses must follow the quik/chal reply, never precede it.
    "quik": after_quickmatch,
    "chal": after_quickmatch,
}


@handles("room")
def create_room(ctx: Context) -> List[bytes]:
    """Create a room. This is **not** a status query.

    The client's send site (0x00119cf0) writes ``NAME=<generated>`` plus
    ``IGNEXIST=1``, where the name generator at 0x00119cc8 emits ``X``, ``.``
    and a suffix -- so client-made rooms are ``X.<something>``, the same
    one-letter-prefix convention as ``Z.Quickmatch``.

    Answering OK without creating anything, as this once did, is worse than
    failing: the client immediately tries to ``move`` into a room that does not
    exist, and a failed move used to wipe its lobby.

    ``IGNEXIST`` means "an existing room of this name is not an error", which is
    what the client relies on when several players pick the same generated name.
    """
    if not ctx.session.authenticated:
        return ctx.fail(ERR_MISSING)

    name = ctx.message.get("NAME").strip()
    if not name or not _NAME_RE.match(name):
        return ctx.fail(ERR_BAD_NAME)

    ignore_existing = ctx.message.get("IGNEXIST", "0") not in ("", "0")
    existing = ctx.store.room(name)
    if existing is not None and not ignore_existing:
        return ctx.fail(ERR_DUPLICATE)

    if existing is None:
        password = ctx.message.get("PASS")
        try:
            limit = int(ctx.message.get("MAX", "0")) or 32
        except ValueError:
            limit = 32
        ctx.store.ensure_room(name, desc="", max_users=limit,
                              password=password)
        existing = ctx.store.room(name)
        if existing is None:
            return ctx.fail(ERR_INTERNAL)

    return [protocol.encode("room", protocol.OK, {
        "IDENT": str(existing["ID"]),
        "NAME": name,
        "COUNT": str(_occupancy(ctx, name)),
        "LIDENT": str(ctx.session.room_id or -1),
        "LCOUNT": str(_occupancy(ctx, ctx.session.room) if ctx.session.room else 0),
    })]


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

#: The client speaks with ``mesg``, proven at its send site 0x0034e6b0. ``chat``
#: was registered alongside it as a guess and is NOT a real type -- keeping it
#: would invite someone to build on a verb the client never sends.
CHAT_REQUEST_TYPE = "mesg"

#: Keys on an outgoing ``mesg``, from the same send site: the body is ``TEXT``
#: (0x00605f80) and a private recipient is ``PRIV`` (0x00605f88). Reading
#: ``USER`` -- as this once did -- means no message is ever seen as private, so
#: every private line is broadcast to the whole room instead.
CHAT_BODY_KEY = "TEXT"
CHAT_PRIVATE_KEY = "PRIV"

#: What the client reads back off a ``+msg`` push. The handler (0x00449488)
#: itself reads only ``F``, which it turns into a message class; the consumer
#: behind it reads ``N`` (sender, 64 bytes) and ``T`` (text, 256 bytes).
#: ``PERS``/``USER``/``BODY``/``TYPE``/``TIME`` -- which this used to send --
#: are read by nothing at all, so chat was invisible.
MAX_SPEAKER = 63
MAX_CHAT = 255

#: ``F`` letters, through the same bitmask codec as elsewhere: bit 2 is ``B``
#: and marks a broadcast, bit 16 is ``P`` and marks a private message.
CHAT_BROADCAST = "B"
CHAT_PRIVATE = "P"


def _chat_push(speaker: str, text: str, private: bool) -> bytes:
    """One ``+msg``, in the shape the client actually parses."""
    return protocol.encode("+msg", protocol.OK, {
        "N": speaker[:MAX_SPEAKER],
        "T": text[:MAX_CHAT],
        "F": CHAT_PRIVATE if private else CHAT_BROADCAST,
    })


@handles(CHAT_REQUEST_TYPE)
def _say(ctx: Context) -> List[bytes]:
    """Relay a line of chat to the speaker's room, or to one player."""
    if not ctx.session.authenticated or not ctx.session.room:
        return ctx.fail(ERR_MISSING)
    body = ctx.message.get(CHAT_BODY_KEY)
    if not body:
        return ctx.reply()
    speaker = ctx.session.persona or ctx.session.account or "player"
    private_to = ctx.message.get(CHAT_PRIVATE_KEY).strip()

    if ctx.hub is not None:
        if private_to:
            target = ctx.hub.by_persona(private_to)
            if target is None:
                return ctx.fail(ERR_MISSING)
            ctx.hub.push(target, _chat_push(speaker, body, private=True))
        else:
            ctx.hub.broadcast([_chat_push(speaker, body, private=False)],
                              room=ctx.session.room)
    return ctx.reply()


# --------------------------------------------------------------------------
# matchmaking
# --------------------------------------------------------------------------

def session_invite(recipient, host_persona: str, guest_persona: str,
                   host_addr: str, guest_addr: str, seed: int,
                   when: int, name: str = "Quickmatch") -> bytes:
    """Build the ``+ses`` that starts a match for one of the two players.

    The client decides its own role by comparing SELF against HOST: the host
    dials OPPO at ADDR, the guest dials HOST at FROM, both on a port hardcoded
    in the client. The server introduces and then has nothing further to do --
    gameplay never comes back through here.

    WHEN must be non-zero: the record is only delivered while it is set, and is
    cleared once consumed.
    """
    if not when:
        raise ValueError("WHEN must be non-zero or the client discards the "
                         "invite without acting on it")
    if not host_persona or not recipient:
        # Both are strcmp'd at 0x004e2cbc. Two empty strings compare equal, so
        # every client would decide it was the host and nobody would dial.
        raise ValueError("SELF and HOST must both be set; empty strings compare "
                         "equal and both consoles would take the host branch")
    return protocol.encode("+ses", protocol.OK, {
        "NAME": lobby._clip(name, lobby.MAX_NAME),
        "SELF": lobby._clip(recipient, lobby.MAX_NAME),
        "HOST": lobby._clip(host_persona, lobby.MAX_NAME),
        "OPPO": lobby._clip(guest_persona, lobby.MAX_NAME),
        # 16-byte slots, unlike the 32 above -- clip explicitly rather than
        # letting the client truncate.
        "P1": lobby._clip(host_persona, lobby.MAX_SLOT),
        "P2": lobby._clip(guest_persona, lobby.MAX_SLOT),
        "P3": "",
        "P4": "",
        "AUTH": "1",
        # ADDR is dialled by the HOST, so it is the guest's address; FROM is
        # dialled by the guest, so it is the host's. Both are parsed by the
        # dotted-quad reader at 0x0044c628 -- anything else silently yields
        # garbage rather than an error.
        "ADDR": guest_addr,
        "FROM": host_addr,
        "SEED": str(seed),
        "WHEN": str(when),
    })


def start_match(hub, host_conn, guest_conn, seed: Optional[int] = None) -> bool:
    """Introduce two players to each other. Returns False if either has gone.

    Both sides are told the same story from their own point of view, which is
    what lets each work out whether to dial or be dialled.
    """
    host = host_conn.session.persona or host_conn.session.account or "host"
    guest = guest_conn.session.persona or guest_conn.session.account or "guest"
    # Observed, not self-reported -- see Session.observed_addr.
    host_addr = host_conn.session.observed_addr or "0.0.0.0"
    guest_addr = guest_conn.session.observed_addr or "0.0.0.0"
    when = int(time.time())
    seed = when & 0x7FFFFFFF if seed is None else seed

    for conn, who in ((host_conn, host), (guest_conn, guest)):
        blob = session_invite(who, host, guest, host_addr, guest_addr,
                              seed, when)
        if not hub.push(conn, blob):
            return False
    return True


# --------------------------------------------------------------------------
# service news -- the message that also carries the buddy address
# --------------------------------------------------------------------------

#: ``news`` is parameterised by ``NAME``, and **the reply is not tagged
#: ``news``**. The client builds the tag it will accept as ``'new0' + NAME``
#: (0x0034f4e0-0x0034f504), then keeps the body only if the reply's type word
#: equals it -- via a ``movz``, so a mismatch silently yields NULL rather than
#: an error.
#:
#: We answered ``news`` for a long time, so every reply was discarded. That is
#: invisible from the server: the request looks answered, the client neither
#: complains nor retries. Two symptoms were downstream of it, and both were
#: misread at the time -- ``CSUM`` never landed, so the console always believed
#: its rosters were stale; and ``BUDDY_URL``/``BUDDY_PORT`` never landed, which
#: is why no console ever connected to the buddy port we were advertising.
NEWS_REPLY_BASE = "new0"

#: The categories the client asks for.
NEWS_CONFIG = 0      # service config: buddy address, roster DATE/CSUM
NEWS_HEADLINES = 1   # the news list it displays
NEWS_ROSTERS = 2     # the roster update manifest


def _news_reply_type(kind: int) -> str:
    """``new0``, ``new1``, ``new2`` -- the tag for this category."""
    if not 0 <= kind <= 9:
        # The client forms the tag by integer addition on the last byte, so
        # anything outside one digit would produce a type we cannot name.
        raise ProtocolError("news category %d is out of range" % kind)
    return NEWS_REPLY_BASE[:3] + str(kind)


@handles("news")
def service_news(ctx: Context) -> List[bytes]:
    """Answer the client's news fetch.

    Three categories, selected by ``NAME``, each with its own reply tag:

    * ``NAME=0`` -> ``new0``: the service configuration. ``BUDDY_URL`` and
      ``BUDDY_PORT`` (read at 0x0034ef44/0x0034ef6c) go straight to the buddy
      manager, then ``DATE`` and ``CSUM``, the roster version.
    * ``NAME=1`` -> ``new1``: the headline list the client displays.
    * ``NAME=2`` -> ``new2``: the roster update manifest -- see
      :func:`_roster_manifest`.

    Leaving this unanswered is what made the client sit on "logging into
    server": it had asked and was waiting.
    """
    try:
        kind = int(ctx.message.get("NAME", "0"))
    except ValueError:
        kind = NEWS_CONFIG

    if kind == NEWS_ROSTERS:
        return _roster_manifest(ctx)
    if kind == NEWS_HEADLINES:
        return [protocol.encode(_news_reply_type(kind), protocol.OK,
                                {"NAME": str(kind)})]

    fields = {
        "NAME": str(kind),
        "BUDDY_URL": ctx.config.get("buddy_host", ctx.config["advertise_host"]),
        "BUDDY_PORT": str(ctx.config.get("buddy_port", 0)),
        # The parser reads these four unconditionally after DATE/CSUM. Sending
        # them explicitly beats letting each fall back to a default we have not
        # reasoned about -- TWRP's is 240, and the others are zero.
        "FPLY": str(ctx.config.get("fply", 0)),
        "BGNR": str(ctx.config.get("bgnr", 0)),
        "ELIT": str(ctx.config.get("elit", 0)),
        "TWRP": str(ctx.config.get("twrp", TWRP_DEFAULT)),
    }
    # Always sent, so the field is populated and the path is exercised. The
    # default checksum is a placeholder and will not match the console's --
    # see the note above PLACEHOLDER_ROSTER_CSUM.
    fields["DATE"] = str(ctx.config.get("roster_date", DEFAULT_ROSTER_DATE))
    fields["CSUM"] = _pick_csum(ctx)
    return [protocol.encode(_news_reply_type(NEWS_CONFIG), protocol.OK, fields)]


def _pick_csum(ctx: Context) -> str:
    """The checksum to announce, honouring a sweep if one is configured.

    Testing a candidate checksum normally costs a whole boot, because the
    console asks for its roster version once per login. A sweep serves the next
    candidate on each successive request instead, so backing out to the menu and
    going online again tests the next one -- turning a morning of reboots into a
    few minutes of menu navigation.

    Which candidate was served is printed, because the console cannot tell us:
    the only signal is whether the "rosters are out of date" prompt appears.
    """
    sweep = ctx.config.get("roster_csum_sweep")
    if not sweep:
        return str(ctx.config.get("roster_csum", PLACEHOLDER_ROSTER_CSUM))
    index = int(ctx.config.get("_sweep_index", 0))
    value = sweep[index % len(sweep)]
    ctx.config["_sweep_index"] = index + 1
    label = ctx.config.get("roster_csum_labels", {}).get(value, "")
    print("[roster] serving CSUM candidate %d/%d: %s%s"
          % (index % len(sweep) + 1, len(sweep), value,
             "  (%s)" % label if label else ""), flush=True)
    return value


def _roster_manifest(ctx: Context) -> List[bytes]:
    """``new2`` -- where to fetch a roster, and what it should hash to.

    This is how a roster is actually delivered, and it is not a message on this
    connection at all. Each record carries:

    * ``URL``  -- an absolute HTTP URL. Read at 0x003525b0 into a 256-byte
      buffer, whose default (0x00606158) is the empty string. An absent or
      empty ``URL`` is exactly the failure seen on hardware: the client's HTTP
      component ('http'/'get ', 0x004468f4) rejects it *before resolving
      anything*, which is why no new connection and no DNS lookup were
      observed.
    * ``CRC``  -- decimal CRC-32 of the body. Checked at 0x003527c0 with
      ``0x0039d7e8(buf, size, 0)``. Note the seed is **0** here, unlike the
      roster checksum in ``CSUM``, which seeds from the row count.
    * ``NAME`` -- the label shown in the update list.

    On a CRC match the payload is handed to 0x003b6e38, which loads it into the
    record store as database ``GAEL`` -- ``LEAG`` reversed. That call site is
    the only one, so an HTTP download is the sole route by which a league
    database is ever replaced.

    With no roster configured we return an empty manifest rather than a bad
    record: zero entries leaves the client with nothing to fetch, which is
    honest, where a record with an unreachable URL would look like a broken
    download.
    """
    url = ctx.config.get("roster_url")
    crc = ctx.config.get("roster_file_crc")
    if not url or crc is None:
        return [protocol.encode(_news_reply_type(NEWS_ROSTERS), protocol.OK,
                                {"NAME": str(NEWS_ROSTERS)})]
    return [protocol.encode(_news_reply_type(NEWS_ROSTERS), protocol.OK, {
        "NAME": str(NEWS_ROSTERS),
        "URL": str(url),
        "CRC": str(crc),
    })]


@handles("cusr")
def change_user(ctx: Context) -> List[bytes]:
    """Persona preferences -- currently a favourite team (``SETFAV``).

    Acknowledged and stored on the session. There is nowhere in the schema for
    it yet, and inventing a column before the leaderboard work says what shape
    that data takes would be guessing.
    """
    favourite = ctx.message.get("SETFAV")
    if favourite:
        ctx.session.favourite = favourite
    return ctx.reply()
