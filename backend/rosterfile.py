"""Serving a roster over HTTP, which is how the console actually fetches one.

The lobby connection never carries roster bytes. What it carries is a `new2`
manifest naming a URL, and the console then makes an ordinary HTTP GET through
its own client ('http'/'get ' at 0x004468f4, DirtySDK ProtoHttp on the IOP).

Three constraints come from the client and none of them are negotiable:

**The CRC seed is 0.** On completion (0x003527c0) the client runs
``0x0039d7e8(buf, size, 0)`` over the downloaded bytes and compares against the
manifest's ``CRC``. That is plain :func:`zlib.crc32`. It is deliberately *not*
the roster checksum in ``CSUM``, which seeds from the row count instead -- two
different numbers over two different things, and swapping them silently fails
the download with the same error as a corrupt transfer.

**The length must match the console's own league database, exactly.** Before
starting, the client asks 0x003b6cf0 for the size of the member backing LEAG --
253,044 bytes on retail -- and uses that one number for both the receive
allocation and the CRC range. A body of any other length cannot verify: short
and the uninitialised tail is hashed, long and it is refused on the
Content-Length alone at 0x00305f94, before any body is read.

Note that our own ``sendall`` returning proves nothing about acceptance: it
returns when the kernel takes the bytes, so a refused transfer can still look
like a clean 200 in this server's log. Only the console's own state says
whether a payload landed.

**The console wipes before it validates.** The install path deletes every table
in ``LEAG`` (0x004c9ee8-0x004c9f14) *before* the stream is opened and before the
file's magic is read, and there is no rollback. A truncated transfer or a
malformed payload therefore leaves the league database empty or half-built,
recoverable only by rebooting the game. That is a reason to be careful about
what is served, not merely about whether it verifies.

**It is served to a console, not a browser.** Keep the response minimal and
always send ``Content-Length``; the client reads the body length from the
header ('body' selector) rather than by reading to EOF.
"""

from __future__ import annotations

import http.server
import socket
import threading
import zlib
from typing import Optional, Tuple

#: The path the manifest URL points at. Nothing requires this exact spelling --
#: the client fetches whatever URL we name -- but a fixed one keeps logs
#: readable.
ROSTER_PATH = "/roster.dat"


class RosterFileError(Exception):
    """The roster payload cannot be served as configured."""


def load(path: str) -> Tuple[bytes, int]:
    """Read the payload and compute the CRC the client will check it against."""
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        raise RosterFileError("cannot read roster payload %s: %s" % (path, exc))
    if not payload:
        raise RosterFileError("roster payload %s is empty" % path)
    # Seed 0 -- see the module docstring. This is NOT the CSUM algorithm.
    return payload, zlib.crc32(payload) & 0xFFFFFFFF


class _Handler(http.server.BaseHTTPRequestHandler):
    # Set by the server instance.
    payload = b""
    on_event = None

    # The console is not a browser; HTTP/1.0 with an explicit length and a
    # close is the least that can go wrong.
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        self._announce("GET %s" % self.path)
        # Serve the payload for any path. The client fetches exactly the URL we
        # published, and being strict here turns a typo in the manifest into a
        # silent 404 that looks identical to a network failure on the console.
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        try:
            self.wfile.write(self.payload)
        except OSError:
            # The console hung up mid-transfer; it will retry.
            self._announce("transfer interrupted")

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()

    def log_message(self, fmt, *args) -> None:
        # BaseHTTPRequestHandler logs to stderr by default, which would
        # interleave with the lobby's own output.
        self._announce(fmt % args)

    def _announce(self, text: str) -> None:
        if self.on_event is not None:
            self.on_event("[http] %s" % text)


class RosterServer:
    """A one-file HTTP server, alive for as long as the lobby is."""

    def __init__(self, payload: bytes, on_event=None) -> None:
        self.payload = payload
        self.crc = zlib.crc32(payload) & 0xFFFFFFFF
        self._on_event = on_event
        self._httpd: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, bind: str, port: int) -> int:
        """Begin serving. Returns the port actually bound."""
        handler = type("_BoundHandler", (_Handler,), {
            "payload": self.payload,
            "on_event": staticmethod(self._on_event) if self._on_event else None,
        })
        try:
            self._httpd = http.server.HTTPServer((bind, port), handler)
        except OSError as exc:
            raise RosterFileError("cannot serve roster on %s:%d: %s"
                                  % (bind, port, exc))
        bound = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()
        return bound

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def manifest_url(host: str, port: int, path: str = ROSTER_PATH) -> str:
    """The absolute URL to publish in the ``new2`` manifest.

    Absolute and numeric on purpose. The console has no useful DNS beyond what
    we already redirect, and a relative URL has nothing to resolve against.
    """
    return "http://%s:%d%s" % (host, port, path)


def local_addresses() -> Tuple[str, ...]:
    """Best-effort list of addresses this host answers on, for diagnostics."""
    try:
        _name, _aliases, addresses = socket.gethostbyname_ex(
            socket.gethostname())
        return tuple(addresses)
    except OSError:
        return ()
