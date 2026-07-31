"""EA DirtySDK / FESL-family message framing, as observed on the wire.

Madden NFL 2004 (PS2) opens its game-server session with:

    40 64 69 72  00 00 00 00  00 00 00 57  PROD=MADDEN-PS2-2004\\n...\\0
    \\_________/  \\_________/  \\_________/
       "@dir"       txn id       length

* **type** -- four ASCII bytes naming the message. ``@dir`` is a directory
  lookup: the client asking where to go next, which is why it is the first
  thing sent.
* **transaction id** -- big-endian u32, zero on the opening message. A reply
  is expected to carry the id it answers.
* **length** -- big-endian u32 counting the *whole* message, header included.
  87 on the observed packet: 12 bytes of header plus 75 of payload.
* **payload** -- newline-separated ``KEY=VALUE`` lines, NUL-terminated. Values
  may be double-quoted when they contain spaces.

Only the framing is established fact here. Which keys a *reply* must carry is
not yet known -- it is inferred from the request and confirmed by watching what
the client does next, which is what :func:`directory_reply` exists to try.
"""

from __future__ import annotations

import struct
from typing import Dict, List, NamedTuple, Optional, Tuple

HEADER_SIZE = 12


class EaMessage(NamedTuple):
    """One decoded message."""

    type: str
    txn: int
    fields: Dict[str, str]
    raw_payload: bytes

    @property
    def product(self) -> Optional[str]:
        return self.fields.get("PROD")

    def describe(self) -> str:
        lines = ["  type : %s" % self.type, "  txn  : %d" % self.txn]
        if self.fields:
            width = max(len(k) for k in self.fields)
            for key, value in self.fields.items():
                lines.append("  %-*s = %s" % (width + 2, key, value))
        elif self.raw_payload:
            lines.append("  payload (not key=value): %s"
                         % self.raw_payload[:64].hex())
        return "\n".join(lines)


class EaProtocolError(ValueError):
    """The bytes are not a well-formed message."""


def parse_fields(payload: bytes) -> Dict[str, str]:
    """Split a ``KEY=VALUE`` payload, dropping the trailing NUL.

    Quotes around a value are stripped: the observed ``VERS="PS2/MS5-Jun 17
    2003"`` is quoted only because it contains spaces, and the quotes are not
    part of the value.
    """
    fields: Dict[str, str] = {}
    text = payload.split(b"\x00", 1)[0].decode("latin-1")
    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def decode(data: bytes) -> EaMessage:
    """Decode one complete message. Raises if the framing does not hold."""
    if len(data) < HEADER_SIZE:
        raise EaProtocolError("short message: %d bytes, need at least %d"
                              % (len(data), HEADER_SIZE))
    msg_type = data[:4].decode("latin-1")
    txn, length = struct.unpack_from(">II", data, 4)
    if length < HEADER_SIZE:
        raise EaProtocolError("declared length %d is shorter than the header"
                              % length)
    if length > len(data):
        raise EaProtocolError("declared length %d exceeds the %d bytes present"
                              % (length, len(data)))
    return EaMessage(msg_type, txn, parse_fields(data[HEADER_SIZE:length]),
                     data[HEADER_SIZE:length])


def encode(msg_type: str, txn: int, fields: Dict[str, str]) -> bytes:
    """Build a message. The length is computed, never supplied.

    Values containing a space are quoted, matching how the client sends VERS.
    """
    if len(msg_type) != 4:
        raise EaProtocolError("message type must be exactly 4 characters, got %r"
                              % msg_type)
    lines: List[str] = []
    for key, value in fields.items():
        text = str(value)
        if " " in text and not (text.startswith('"') and text.endswith('"')):
            text = '"%s"' % text
        lines.append("%s=%s" % (key, text))
    payload = ("\n".join(lines) + "\n").encode("latin-1") + b"\x00" if lines else b"\x00"
    return (msg_type.encode("latin-1")
            + struct.pack(">II", txn, HEADER_SIZE + len(payload))
            + payload)


def split_stream(buffer: bytes) -> Tuple[List[EaMessage], bytes]:
    """Pull every complete message out of a byte stream.

    TCP does not preserve message boundaries, so a reader must be prepared for
    a partial trailer; it is returned for the next read rather than discarded.
    """
    messages: List[EaMessage] = []
    while len(buffer) >= HEADER_SIZE:
        length = struct.unpack_from(">I", buffer, 8)[0]
        if length < HEADER_SIZE:
            raise EaProtocolError("declared length %d is shorter than the header"
                                  % length)
        if len(buffer) < length:
            break
        messages.append(decode(buffer[:length]))
        buffer = buffer[length:]
    return messages, buffer


def directory_reply(request: EaMessage, host: str, port: int) -> bytes:
    """A candidate answer to ``@dir``: where the client should go next.

    **This is a hypothesis.** The framing is known from the wire; the key names
    a directory reply must use are not. They are guessed from the shape of the
    request and from how such redirectors conventionally answer, and the test
    of correctness is simply whether the client then connects where it is sent.
    Change the fields freely -- that is the point of having it in one place.
    """
    return encode("@dir", request.txn, {
        "TYPE": "1",
        "ADDR": host,
        "PORT": str(port),
        "NAME": request.fields.get("PROD", "server"),
    })
