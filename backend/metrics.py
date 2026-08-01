"""Counters, and a private page that shows them.

The argument for this is narrow and specific: without it, "nobody is playing"
and "everybody is being refused" look identical from outside the process. Every
limit added in this project is a new way for the service to be quietly broken,
and a limit you cannot observe is worse than no limit, because the failure is
invisible and the cause is a number you chose weeks earlier.

It is also what makes the rate limiter's log-only mode useful rather than
merely cautious: `nfl_rate_peak_messages_per_second` is the measurement that
turns the guessed threshold in limits.py into a chosen one.

**Bound to loopback, and deliberately unlabelled.** Two rules, both about not
leaking players:

* The listener refuses any non-loopback address unless explicitly forced. This
  is an operator's view of the process, not a public endpoint, and there is no
  authentication on it because there is not meant to be anything to
  authenticate to.
* No counter carries a source address as a label. Aggregate totals answer every
  operational question here -- how many were refused, not who -- and a metrics
  page that enumerates player IP addresses is a log of who played and when.

The format is Prometheus text exposition, which is plain enough to read with
curl and standard enough to scrape if it ever matters.
"""

from __future__ import annotations

import collections
import http.server
import ipaddress
import threading
from typing import Callable, Dict, List, Optional, Tuple

#: Where the counters live by default. Loopback, and a port with no other
#: claimant in this project.
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 9109

_PREFIX = "nfl_"


class MetricsError(RuntimeError):
    """The metrics endpoint cannot be served as configured."""


class Metrics:
    """Counters the service bumps, plus gauges read from live objects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = collections.Counter()
        self._gauges: List[Tuple[str, str, Callable[[], float]]] = []
        self._help: Dict[str, str] = {}

    def declare(self, name: str, help_text: str) -> None:
        """Give a counter a description and a zero value.

        Declaring up front matters more than it looks: a counter that only
        appears once it is non-zero cannot be distinguished from one that does
        not exist, so an absent `refused` line would read as "nothing was
        refused" when it might mean the code path was never wired up.
        """
        with self._lock:
            self._help[name] = help_text
            self._counters.setdefault(name, 0)

    def gauge(self, name: str, help_text: str,
              read: Callable[[], float]) -> None:
        """Register a value read at scrape time from a live object."""
        with self._lock:
            self._gauges.append((name, help_text, read))

    def bump(self, name: str, count: int = 1) -> None:
        with self._lock:
            self._counters[name] += count

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            out: Dict[str, float] = dict(self._counters)
            gauges = list(self._gauges)
        for name, _help, read in gauges:
            try:
                out[name] = read()
            except Exception:
                # A broken gauge must not take the whole page down; the
                # counters are the part you need when something is wrong.
                continue
        return out

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            helps = dict(self._help)
            gauges = list(self._gauges)
        lines: List[str] = []
        for name in sorted(counters):
            full = _PREFIX + name
            if name in helps:
                lines.append("# HELP %s %s" % (full, helps[name]))
            lines.append("# TYPE %s counter" % full)
            lines.append("%s %d" % (full, counters[name]))
        for name, help_text, read in gauges:
            full = _PREFIX + name
            try:
                value = read()
            except Exception:
                continue
            lines.append("# HELP %s %s" % (full, help_text))
            lines.append("# TYPE %s gauge" % full)
            lines.append("%s %g" % (full, value))
        return "\n".join(lines) + "\n"


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Handler(http.server.BaseHTTPRequestHandler):
    metrics: Optional[Metrics] = None
    on_event = None
    protocol_version = "HTTP/1.0"
    timeout = 10.0

    def do_GET(self) -> None:
        body = self.metrics.render().encode("utf-8") if self.metrics else b""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, fmt, *args) -> None:
        # Silent by default. This is polled, and echoing every scrape would
        # bury the lobby's own output at whatever interval the scraper uses.
        return


class MetricsServer:
    """The private page, on its own thread."""

    def __init__(self, metrics: Metrics, on_event=None) -> None:
        self.metrics = metrics
        self._on_event = on_event
        self._httpd: Optional[http.server.HTTPServer] = None

    def start(self, bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT,
              allow_public: bool = False) -> int:
        if not allow_public and not is_loopback(bind):
            raise MetricsError(
                "refusing to serve metrics on %s: this endpoint has no "
                "authentication and is meant for the operator, not the "
                "internet. Bind it to 127.0.0.1 and reach it over SSH, or "
                "pass --metrics-allow-public if you have a private network "
                "in front of it." % bind)
        handler = type("_BoundMetrics", (_Handler,), {
            "metrics": self.metrics,
            "on_event": staticmethod(self._on_event) if self._on_event else None,
        })
        try:
            self._httpd = http.server.ThreadingHTTPServer((bind, port), handler)
            self._httpd.daemon_threads = True
        except OSError as exc:
            raise MetricsError("cannot serve metrics on %s:%d: %s"
                               % (bind, port, exc))
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self._httpd.server_address[1]

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
