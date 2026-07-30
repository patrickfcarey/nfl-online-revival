# Capturing a title's online traffic on the rig

This is the Phase 1 runbook: get a game client to talk to a box you control,
and record what it says. Everything here runs on the emulator rig.

## Before anything launches: the headset check

The rig shares one VR headset across three emulators. Run this as its own
command and read the output before starting any emulator:

```bash
pgrep -x pcsx2-qt; pgrep -x mupen64plus; pgrep -f "qemu-system-i38[6]"
```

Any hit means someone may be in the headset — stop and ask. Never chain an
emulator launch behind the check in one command. Read-only file access over SSH
is always fine.

Note the brackets in `qemu-system-i38[6]`. `pgrep -f` matches whole command
lines, so if the literal string `qemu-system-i386` appears in the shell command
running the check — which it does when sent over SSH as `bash -c '...'` — pgrep
matches *itself* and reports a phantom emulator. The bracket form cannot match
its own text. A false positive here is not harmless: it stops real work, and if
you learn to wave it off you have broken the check that protects the headset.

## The one thing to understand first: which layer is plaintext

Where you tap decides whether you get readable bytes or ciphertext.

- **Original Xbox, Xbox Live traffic** is IPsec/ESP encrypted at the IP layer.
  A capture at the emulated NIC is **post-encryption ciphertext** — useful for
  hosts, ports and timing, useless for payloads. For plaintext you hook the
  emulator's **socket boundary** (below), above the crypto.
- **Original Xbox, system-link traffic** and **all PS2 traffic** are generally
  readable at the NIC. PS2 game servers mostly spoke plain TCP/UDP (with at most
  an app-layer token such as DNAS), so a NIC capture there is usually already
  the real payload.

So: PS2 and system-link → NIC capture is enough. Xbox Live payloads → socket
hook.

## Step 1 — point the console's DNS at your capture box

Find the rig's LAN IP (call it `RIG_IP`), then run the responder there:

```bash
sudo python3 -m recon dns --ip RIG_IP           # sink every hostname to you
# or, once you know the names, redirect only those and NXDOMAIN the rest:
python3 -m recon dns --map easo.ea.com=RIG_IP --map nfl2k5.example=RIG_IP
```

Port 53 needs privilege: run under `sudo`, or grant the interpreter the
capability once with
`sudo setcap cap_net_bind_service=+ep $(readlink -f $(which python3))`.

Then set the emulated console's DNS to `RIG_IP`:

- **xemu** (Xbox): Settings → Network — enable the NIC, and set the DNS server
  the guest uses to `RIG_IP` (or hand it out over the guest's DHCP if bridged).
- **PCSX2** (PS2): configure the DEV9/network adapter, and set the in-game
  network configuration's DNS to `RIG_IP`.

Boot the game and enter its online menu. Even before any sinkhole, the `dns`
log now lists every hostname the title resolves — the first half of the map.

## Step 2 — capture at the NIC (hosts, ports, and PS2/system-link payloads)

On the rig, capture the emulator's traffic with tcpdump. Write **classic pcap**
(tcpdump's default) — `recon pcap` / `recon classify` do not read Wireshark's
pcapng.

```bash
sudo tcpdump -i any -w captures/title.pcap host RIG_IP
# then (both read the link type from the file; `-i any` on libpcap >= 1.10
# writes LINUX_SLL2, which the reader handles):
python3 -m recon pcap captures/title.pcap
python3 -m recon classify captures/title.pcap
```

This gives you the ports the client dials and, for PS2/system-link, the actual
payloads and their stack.

If `recon pcap` prints `(0 flow record(s))`, that is a real signal, not a
mystery: the reader raises on a link type it cannot decode and warns when a
capture yields nothing, so zero records means the filter matched nothing rather
than that parsing failed silently. Check the `host` filter and that the
emulator actually has its NIC enabled.

## Step 3 — sinkhole the ports and probe

Once you know the ports, accept the connections and log every byte; optionally
answer with canned bytes to see how far the client will advance:

```bash
python3 -m recon sink --tcp 80,443,18300 --udp 27900,28900 --out captures/title.jsonl
python3 -m recon classify --transcript captures/title.jsonl
```

For a true catch-all when ports are still unknown, a Linux `iptables` REDIRECT
of all guest TCP to one sink port also works (recovering the intended port via
`SO_ORIGINAL_DST`); that is a later addition, not needed for the first pass.

## Step 4 — Xbox Live plaintext (when the NIC capture is ciphertext)

If Step 2 shows high-entropy payloads on an Xbox Live title, the bytes are
under IPsec and you need the socket boundary instead. xemu is open source
(QEMU-based); the plan is to patch its emulation of the Xbox network kernel
calls to log the buffers the title passes in/out **before** they hit the crypto.
That patch is built against the rig's own xemu checkout and is Phase-2 work; the
design lives with that checkout, not in this repo.

Rule of thumb: hook the socket/kernel call, not the emulated wire. The wire is
where the encryption already happened.

## Where output goes

Everything lands in `captures/` (gitignored): `*.pcap` from tcpdump, `*.jsonl`
from the sinkhole. Record findings as you go in `docs/protocol-notes.md`.
