"""Phase 1 recon harness for the NFL online-revival project.

Everything here is standard-library only, so it drops onto the emulator rig
with no install step. The harness does not revive anything on its own -- it
watches a game client that still tries to phone home, and turns "what does it
send, to whom, in what order" into a written protocol map. That map is what a
compatible server gets reconstructed from.

Subcommands (see ``python -m recon --help``):

* ``dns``      -- answer the game's DNS lookups with a box you control
* ``sink``     -- accept every connection the redirected client opens and log it
* ``classify`` -- fingerprint a capture (GameSpy / EA / DNAS / TLS / plaintext)
* ``pcap``     -- dump flows from a classic ``.pcap`` (emulator NIC capture)
"""

__version__ = "0.1.0"
