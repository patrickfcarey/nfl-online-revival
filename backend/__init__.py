"""A reconstructed EA game backend for ESPN NFL 2K5 / Madden NFL 2004 (PS2).

The original servers shut down long ago. This speaks the protocol recovered
from the client itself -- see ``docs/protocol-notes.md`` for how each part was
established, and ``docs/backend-data-model.md`` for what it persists.

Layers, deliberately separate so one can change without the others:

* :mod:`backend.protocol` -- the wire format, and nothing else.
* :mod:`backend.store`    -- durable state in SQLite.
* :mod:`backend.handlers` -- one function per message type, plus session state.
* :mod:`backend.service`  -- sockets, framing, dispatch, transcript.
"""

__version__ = "0.1.0"
