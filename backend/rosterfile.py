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

#: How many downloads may be in flight at once.
#:
#: This server used to be a plain ``HTTPServer``, which handles exactly one
#: request at a time. Two consoles joining together therefore serialised their
#: 253 KB downloads, and a single client that opened a socket without ever
#: sending a request line blocked every other download indefinitely.
#:
#: That is worse than it sounds. The install path wipes the league database
#: *before* it validates anything (0x004c9ee8), so a console left waiting
#: behind a stalled transfer sits on an empty database until it is rebooted.
#: Serving concurrently is a correctness fix, not only a throughput one.
#:
#: The cap exists because the opposite failure is just as real: unbounded
#: threads each holding a 253 KB payload is a cheap way to exhaust the host.
MAX_CONCURRENT_DOWNLOADS = 16

#: A console finishes 253 KB in seconds even on a poor link. Anything holding a
#: connection open longer than this is not downloading a roster, and without a
#: timeout it would hold its slot forever.
REQUEST_TIMEOUT = 30.0


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
    slots = None            # a BoundedSemaphore, installed by RosterServer

    # The console is not a browser; HTTP/1.0 with an explicit length and a
    # close is the least that can go wrong.
    protocol_version = "HTTP/1.0"

    # socketserver applies this to the connection in setup(), so a client that
    # connects and then says nothing is dropped instead of holding a slot.
    timeout = REQUEST_TIMEOUT

    def handle(self) -> None:
        """Take a download slot, or hang up rather than queue.

        Refusing immediately is deliberate. The console retries a failed
        transfer, but it has no useful behaviour for a connection that is
        accepted and then ignored -- it simply waits, which is the failure mode
        this whole file exists to avoid.
        """
        if self.slots is not None and not self.slots.acquire(blocking=False):
            self._announce("refused %s: %d downloads already in flight"
                           % (self.client_address[0], MAX_CONCURRENT_DOWNLOADS))
            self.close_connection = True
            return
        try:
            super().handle()
        finally:
            if self.slots is not None:
                self.slots.release()

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
            "slots": threading.BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS),
        })
        try:
            # Threading, so two consoles joining together do not serialise --
            # see MAX_CONCURRENT_DOWNLOADS. daemon_threads keeps shutdown()
            # from blocking on a transfer that is still running.
            self._httpd = http.server.ThreadingHTTPServer((bind, port), handler)
            self._httpd.daemon_threads = True
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
