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

from . import protocol
from .protocol import Message
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
        #: Subscriptions requested via `sele` ("ROOMS=1 USERS=1 ...").
        self.subscriptions: Dict[str, bool] = {}

    @property
    def authenticated(self) -> bool:
        return self.account is not None

    def describe(self) -> str:
        who = self.account or "-"
        return "%s state=%s account=%s persona=%s" % (
            self.peer, self.state, who, self.persona or "-")


class Context:
    """What a handler is given: the message, its session, and the store."""

    def __init__(self, message: Message, session: Session, store: Store,
                 config: Dict[str, str]) -> None:
        self.message = message
        self.session = session
        self.store = store
        self.config = config

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


@handles("~png")
def ping(ctx: Context) -> List[bytes]:
    """Keepalive. Echoed straight back."""
    return ctx.reply()


def dispatch(ctx: Context) -> List[bytes]:
    """Route a message to its handler, or stay silent if none is registered."""
    handler = handler_for(ctx.message.type)
    if handler is None:
        return []
    return handler(ctx)
