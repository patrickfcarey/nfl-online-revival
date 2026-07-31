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

#: The client clamps its socket buffer to 8192 bytes (0x00452D90, called from
#: 0x0044759C with 0x8000). It writes a NUL at base+cursor+declared_length-1
#: without bounds-checking, so a length larger than that buffer is an
#: out-of-bounds write in the client -- we must never emit one. The same
#: ceiling is applied to inbound messages, where a nonsense length means the
#: stream has desynchronised.
MAX_MESSAGE_SIZE = 8192

Status = Union[int, str]


#: Sending this as a status tears the session down -- the client routes it to
#: its terminate path (0x00449090). It is never a general-purpose error.
STATUS_TERMINATE = "auth"


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


def percent_escape(text: str) -> str:
    """Protect a value from the client's percent-decoder.

    Every value the client copies goes through 0x0044c9b0, which decodes ``%XX``
    hex escapes and collapses ``%%`` to a literal ``%``. So a raw ``%`` in a
    persona or a chat line is not passed through -- it either eats the next two
    characters or terminates the copy early.

    Only ``%`` needs escaping. The decoder also stops at any byte below 32 and
    strips a matched leading quote, but the encoder already rejects control
    characters and adds quotes deliberately.
    """
    return text.replace("%", "%%")


def percent_unescape(text: str) -> str:
    """Undo the client's escaping on a value it sent us."""
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i] != "%":
            out.append(text[i])
            i += 1
            continue
        if text[i + 1:i + 2] == "%":
            out.append("%")
            i += 2
            continue
        hex_digits = text[i + 1:i + 3]
        if len(hex_digits) == 2:
            try:
                out.append(chr(int(hex_digits, 16)))
                i += 3
                continue
            except ValueError:
                pass
        # A stray '%' the client did not mean as an escape. Keep it rather than
        # dropping data.
        out.append("%")
        i += 1
    return "".join(out)


def parse_fields(payload: bytes) -> Dict[str, str]:
    """Split a ``KEY=VALUE`` payload, dropping the trailing NUL.

    Quotes around a value are stripped -- the client quotes only to protect
    spaces, so they are framing rather than content -- and percent escapes are
    undone, for the same reason.
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
        fields[key.strip()] = percent_unescape(value)
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
        # Before quoting: the quotes are framing the client strips, so they must
        # not themselves be escaped.
        text = percent_escape(text)
        if " " in text and not (text.startswith('"') and text.endswith('"')):
            text = '"%s"' % text
        lines.append("%s=%s" % (key, text))
    payload = (("\n".join(lines) + "\n").encode("latin-1") + b"\x00"
               if lines else b"\x00")
    total = HEADER_SIZE + len(payload)
    if total > MAX_MESSAGE_SIZE:
        raise ProtocolError(
            "message would be %d bytes; the client's buffer is %d and it writes "
            "past a longer one" % (total, MAX_MESSAGE_SIZE))
    return (msg_type.encode("latin-1")
            + struct.pack(">II", encode_status(status), total)
            + payload)


def encode_raw(msg_type: str, status: Status = OK, body: bytes = b"") -> bytes:
    """Build a message whose payload is supplied verbatim.

    Most replies are KEY=VALUE text, which :func:`encode` builds. A few carry a
    structured body instead -- the ``news`` reply wraps its fields in a
    ``new<n>`` sub-block with a binary 12-byte header -- and those bytes must
    reach the wire untouched, so they cannot go through the field encoder.
    """
    if len(msg_type) != 4:
        raise ProtocolError("message type must be exactly 4 characters, got %r"
                            % (msg_type,))
    total = HEADER_SIZE + len(body)
    if total > MAX_MESSAGE_SIZE:
        raise ProtocolError("message would be %d bytes; the client's buffer is %d"
                            % (total, MAX_MESSAGE_SIZE))
    return (msg_type.encode("latin-1")
            + struct.pack(">II", encode_status(status), total) + body)


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
