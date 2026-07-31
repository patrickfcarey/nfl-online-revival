"""EA DirtySDK / FESL-family message framing.

    40 64 69 72  00 00 00 00  00 00 00 57  PROD=MADDEN-PS2-2004\\n...\\0
    \\_________/  \\_________/  \\_________/
       "@dir"       status       length

* **type** -- four ASCII bytes naming the message.
* **status** -- four bytes. Zero means success. Non-zero is *four ASCII
  characters*, not a number: an error tag the client looks up in a table to
  choose an error screen (`dupl`, `mail`, `pass`, `tooy`, ...). Treating this
  field as an integer works only by accident, because success is zero.
* **length** -- big-endian u32 counting the *whole* message, header included.
* **payload** -- ``KEY=VALUE`` lines separated by ``\\n``, NUL-terminated.
  Values are quoted when they contain spaces.

This module is the single definition of the wire format. The recon harness and
the server both use it, so a correction lands in one place.
"""

from __future__ import annotations

import struct
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple, Union

HEADER_SIZE = 12

#: Success. Any other status is a four-character tag.
OK = 0

#: Refuse to buffer more than this for one message. A desynchronised stream
#: yields a nonsense length -- 4 GB is representable in the header -- and
#: without a cap the reader waits for bytes that will never come while the
#: buffer grows. Real messages are a few hundred bytes; the largest field the
#: client reads is 4097.
MAX_MESSAGE_SIZE = 65536

Status = Union[int, str]


class ProtocolError(ValueError):
    """The bytes are not a well-formed message."""


def encode_status(status: Status) -> int:
    """Turn a status into its 32-bit wire form.

    Accepts 0 (or "") for success and a four-character tag otherwise. Rejects
    anything else rather than silently truncating -- a wrong status shows up on
    the client as the wrong error screen, which is very hard to trace back.
    """
    if isinstance(status, int):
        if not 0 <= status <= 0xFFFFFFFF:
            raise ProtocolError(
                "status %d does not fit in 32 bits" % status)
        return status
    if status == "":
        return OK
    if len(status) != 4:
        raise ProtocolError(
            "status must be 0 or a 4-character tag, got %r" % (status,))
    return struct.unpack(">I", status.encode("latin-1"))[0]


def decode_status(value: int) -> str:
    """Render a wire status as its tag, or "" for success."""
    if value == OK:
        return ""
    raw = struct.pack(">I", value & 0xFFFFFFFF)
    if all(32 <= byte < 127 for byte in raw):
        return raw.decode("latin-1")
    return "0x%08x" % value


class Message(NamedTuple):
    """One decoded message.

    ``raw`` is the exact bytes this was decoded from. Keeping them means a
    transcript records what actually arrived rather than what we made of it --
    the two differ precisely when our parsing is wrong, which is the case worth
    being able to see.
    """

    type: str
    status: int
    fields: Dict[str, str]
    raw_payload: bytes = b""
    raw: bytes = b""

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def status_tag(self) -> str:
        return decode_status(self.status)

    def get(self, key: str, default: str = "") -> str:
        return self.fields.get(key, default)

    def describe(self) -> str:
        head = "  type : %s" % self.type
        if not self.ok:
            head += "\n  status: %s" % self.status_tag
        lines = [head]
        if self.fields:
            width = max(len(k) for k in self.fields)
            for key, value in self.fields.items():
                lines.append("  %-*s = %s" % (width + 2, key, value))
        elif self.raw_payload not in (b"", b"\x00"):
            lines.append("  payload (not key=value): %s"
                         % self.raw_payload[:64].hex())
        return "\n".join(lines)


def parse_fields(payload: bytes) -> Dict[str, str]:
    """Split a ``KEY=VALUE`` payload, dropping the trailing NUL.

    Quotes around a value are stripped -- the client quotes only to protect
    spaces, so they are framing rather than content.
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


def decode(data: bytes) -> Message:
    """Decode one complete message."""
    if len(data) < HEADER_SIZE:
        raise ProtocolError("short message: %d bytes, need %d"
                            % (len(data), HEADER_SIZE))
    msg_type = data[:4].decode("latin-1")
    status, length = struct.unpack_from(">II", data, 4)
    if length < HEADER_SIZE:
        raise ProtocolError("declared length %d is shorter than the header" % length)
    if length > len(data):
        raise ProtocolError("declared length %d exceeds the %d bytes present"
                            % (length, len(data)))
    if length > MAX_MESSAGE_SIZE:
        raise ProtocolError("declared length %d exceeds the %d-byte limit"
                            % (length, MAX_MESSAGE_SIZE))
    payload = data[HEADER_SIZE:length]
    return Message(msg_type, status, parse_fields(payload), payload,
                   bytes(data[:length]))


def _check_field(key: str, value: str) -> None:
    """Refuse anything that could forge the payload's own framing.

    Fields are newline-separated and NUL-terminated, so a value containing
    either does not merely corrupt the message -- it *injects*. A persona named
    ``eve\nADMIN=1`` would arrive at the client as two fields. Handlers already
    validate the names they accept, but the encoder is the last place this can
    be caught for every field, including ones read back out of the database.
    """
    if "\n" in key or "\x00" in key or "=" in key:
        raise ProtocolError(
            "field name %r contains a character that would break framing" % key)
    if "\n" in value or "\x00" in value:
        raise ProtocolError(
            "value of %s contains a newline or NUL, which would inject a field"
            % key)


def encode(msg_type: str, status: Status = OK,
           fields: Optional[Dict[str, str]] = None) -> bytes:
    """Build a message. The length is computed, never supplied by a caller."""
    if len(msg_type) != 4:
        raise ProtocolError("message type must be exactly 4 characters, got %r"
                            % (msg_type,))
    lines: List[str] = []
    for key, value in (fields or {}).items():
        text = str(value)
        _check_field(str(key), text)
        if " " in text and not (text.startswith('"') and text.endswith('"')):
            text = '"%s"' % text
        lines.append("%s=%s" % (key, text))
    payload = (("\n".join(lines) + "\n").encode("latin-1") + b"\x00"
               if lines else b"\x00")
    return (msg_type.encode("latin-1")
            + struct.pack(">II", encode_status(status), HEADER_SIZE + len(payload))
            + payload)


def split_stream(buffer: bytes) -> Tuple[List[Message], bytes]:
    """Pull every complete message out of a stream, keeping a partial trailer.

    TCP does not preserve message boundaries, so the remainder is returned for
    the next read rather than discarded.
    """
    messages: List[Message] = []
    while len(buffer) >= HEADER_SIZE:
        length = struct.unpack_from(">I", buffer, 8)[0]
        if length < HEADER_SIZE:
            raise ProtocolError("declared length %d is shorter than the header"
                                % length)
        if length > MAX_MESSAGE_SIZE:
            raise ProtocolError("declared length %d exceeds the %d-byte limit; "
                                "the stream is out of step"
                                % (length, MAX_MESSAGE_SIZE))
        if len(buffer) < length:
            break
        messages.append(decode(buffer[:length]))
        buffer = buffer[length:]
    return messages, buffer


def iter_tokens(value: str, separator: str = ",") -> Iterator[str]:
    """Split a packed list value such as ``PERSONAS=a,b,c``."""
    for token in value.split(separator):
        token = token.strip()
        if token:
            yield token
