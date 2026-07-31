"""Building the lobby's pushed records.

``+rom`` and ``+usr`` are ordinary ``KEY=VALUE`` messages, **one record each** --
not the concatenated record buffers used elsewhere in this protocol. Each is
built here as a pure function so the exact bytes can be tested without a socket.

Three details are easy to get wrong and each produces a wrong lobby rather than
an error:

* **A record with no ``N`` is a deletion.** That is how a room or user is
  removed from the client's list, and it is why every builder here is explicit
  about whether it is sending or removing.
* **``F`` must always be present in a ``+rom``**, even empty. Its converter
  defaults to ``-1`` when the key is absent, which sets every bit including the
  ``P`` flag, and the client then shows the room as password-protected.
* **A negative or missing ``I`` discards the record silently.** Ids start at 1.

``F`` is a letter bitmask, not a number: the converter maps ``@`` to bit 0 and
then ``A``-``Z`` to bits 1-26, case-insensitively, with ``0``-``3`` covering
bits 27-30. Only two letters have known meaning, so only those are named.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from . import protocol

#: Room flag: the room requires a password. Bit 0x10000 -> letter 'P'.
ROOM_PRIVATE = "P"
#: User flag: "this record is you". Bit 0x200000 -> letter 'U'.
USER_SELF = "U"

#: The client reads room and user names through 32-byte buffers.
MAX_NAME = 31
#: ``S`` and ``X`` on a user record are 128-byte buffers.
MAX_STATUS = 127
#: ``P`` on a user record is an 8-byte buffer.
MAX_TAG = 7


def _clip(text: str, limit: int) -> str:
    """Trim to what the client's buffer holds, leaving room for the NUL."""
    return (text or "")[:limit]


def room_record(room_id: int, name: str, occupants: int = 0, limit: int = 0,
                host: str = "", addr: str = "0.0.0.0",
                ping: Optional[int] = None, private: bool = False) -> bytes:
    """One room, for the client's room list.

    ``ping`` distinguishes three states the client renders differently: absent
    shows blank, zero shows ``---``, and a positive value shows ``~Nms``.
    """
    if room_id < 1:
        raise ValueError("room id must be positive; the client discards id < 0")
    fields = {
        "I": str(room_id),
        # Always present, even empty -- absent means -1, which reads as private.
        "F": ROOM_PRIVATE if private else "",
        "N": _clip(name, MAX_NAME),
        "H": _clip(host, MAX_NAME),
        "T": str(occupants),
        "L": str(limit),
        "A": addr,
    }
    if ping is not None:
        fields["P"] = str(ping)
    return protocol.encode("+rom", protocol.OK, fields)


def room_removal(room_id: int) -> bytes:
    """Remove a room from the client's list, by omitting ``N``."""
    return protocol.encode("+rom", protocol.OK, {"I": str(room_id), "F": ""})


def user_record(user_id: int, name: str, addr: str = "0.0.0.0",
                rank: int = 0, tag: str = "", status: str = "",
                extra: str = "", room_occupants: Optional[int] = None,
                is_self: bool = False) -> bytes:
    """One occupant, for the client's user list.

    ``is_self`` sets the flag that tells the client which row is its own; it
    stores that separately and the lobby highlights it.
    """
    if user_id < 1:
        raise ValueError("user id must be positive; the client discards id < 0")
    fields = {
        "I": str(user_id),
        "F": USER_SELF if is_self else "",
        "N": _clip(name, MAX_NAME),
        "P": _clip(tag, MAX_TAG),
        "A": addr,
        "R": str(rank),
        "S": _clip(status, MAX_STATUS),
        "X": _clip(extra, MAX_STATUS),
    }
    if room_occupants is not None:
        fields["T"] = str(room_occupants)
    return protocol.encode("+usr", protocol.OK, fields)


def user_removal(user_id: int) -> bytes:
    """Remove an occupant from the client's list, by omitting ``N``."""
    return protocol.encode("+usr", protocol.OK, {"I": str(user_id), "F": ""})


def population(counts: Sequence[Tuple[int, int]]) -> bytes:
    """Occupancy for several rooms at once, as ``+pop``.

    The client parses one key, ``Z``, as decimal ``id,count`` pairs with any
    non-digit acting as a separator, stopping at a NUL or at an id of zero. So
    no id may be 0, and the value must stay inside the 512-byte buffer it is
    read into.
    """
    parts: List[str] = []
    for room_id, count in counts:
        if room_id < 1:
            raise ValueError("room id 0 terminates the list; ids start at 1")
        parts.append("%d,%d" % (room_id, count))
    value = " ".join(parts)
    if len(value) > 511:
        raise ValueError("population list is %d bytes; the client reads 512"
                         % len(value))
    return protocol.encode("+pop", protocol.OK, {"Z": value})


def whereabouts(persona: str, room_id: int, room_name: str,
                occupants: int = 0) -> bytes:
    """``+who`` -- tell the client which room it is in, unsolicited.

    Same effect as a ``move`` reply but server-initiated, so it can be used to
    place or relocate a client without it having asked. Changing the room id
    makes the client discard its entire user list, so every occupant must be
    re-sent afterwards.
    """
    return protocol.encode("+who", protocol.OK, {
        "N": _clip(persona, MAX_NAME),
        "F": "",
        "RI": str(room_id),
        "R": _clip(room_name, MAX_NAME),
        "RF": "",
        "RT": str(occupants),
    })


def room_listing(rooms: Iterable, occupancy) -> List[bytes]:
    """Every room, in one pass. ``occupancy`` maps a room name to a count."""
    out = []
    for row in rooms:
        name = row["NAME"]
        out.append(room_record(
            row["ID"], name,
            occupants=occupancy(name),
            limit=row["MAX"] or 0,
            host=row["DESC"] or "",
            private=bool(row["PASS"]),
        ))
    return out
