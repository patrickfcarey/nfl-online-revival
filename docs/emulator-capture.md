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

## PCSX2 network settings (PS2 titles)

Two layers get conflated here, and both must agree:

1. **PCSX2's emulated NIC (DEV9)** — how the virtual PS2 reaches the network.
2. **The game's own network configuration** — PS2 titles store IP/DNS settings
   on the memory card via their in-game network setup. This is what the game
   actually uses.

### DEV9: use Sockets mode

`Settings -> Network & HDD` (values as stored in `inis/PCSX2.ini`, `[DEV9/Eth]`):

| Setting | Value | Why |
|---|---|---|
| `EthEnable` | `true` | the adapter must exist at all |
| `EthApi` | `Sockets` | translates PS2 socket calls to host sockets |
| `InterceptDHCP` | `true` | PCSX2 hands the PS2 its address *and our DNS* |
| `PS2IP` | *(ignored)* | PCSX2 assigns `192.0.2.100/24` from TEST-NET-1 regardless; expect a PTR lookup on `100.2.0.192.in-addr.arpa`, which is the title reverse-resolving its own address, not a server |
| `ModeDNS1` / `ModeDNS2` | `Manual` | otherwise the host resolver is used and we see nothing |
| `DNS1` / `DNS2` | the rig's IP | **both**, so no fallback bypasses the responder |
| `EthLogDNS` / `EthLogDHCP` | `true` | PCSX2's own log, an independent check on ours |

**Why Sockets and not PCAP Bridged.** The PCAP modes put real frames on the
wire, which is more faithful, but they need `cap_net_raw` on the emulator
binary — and capabilities do not survive an AppImage's FUSE mount, which is how
PenguinBox/PCSX2 is installed on this rig. Sockets mode needs no privileges.
The tradeoff: traffic originates from the host's own stack, so capture with
`-i any` (it crosses loopback when the sinkhole is on the same box).

`EthDevice` is only meaningful for the PCAP/TAP modes; leave it empty.

### The game side

In the title's own network setup, choose **automatic / DHCP** so it takes the
address and DNS that `InterceptDHCP` hands out. If a title insists on manual
entry, set its DNS to the rig's IP by hand. The setting is saved to the memory
card, so it only has to be done once per title.

### PCSX2's own log is the best cross-check

`~/.config/PCSX2/logs/emulog.txt` is more informative than the packet capture
once `EthLogDNS`/`EthLogDHCP` are on. It gives the DNS names as DEV9 saw them,
the DHCP handout, connection outcomes (`Recv error: 111` is ECONNREFUSED), and
-- critically -- **which title was running**, via its `Name:` / `Serial:` /
`ELF Loading:` lines. Attribute findings by those boot lines, not by the capture
filename: two titles played back to back land in one capture file.

### Confirming it works

The DNS responder's log is the immediate test. Reach the title's online menu and
watch for query lines; PCSX2's own `EthLogDNS` output is the cross-check. If
PCSX2 logs DNS queries but the responder logs nothing, DNS is being resolved
internally rather than forwarded — set `ModeDNS1` to `Manual` (not `Auto` or
`Internal`) and confirm `DNS1` is the rig. If neither logs anything, the game
never got a network configuration: redo its in-game network setup.

Once hostnames are known, `[DEV9/Eth/Hosts]` can pin them to the sinkhole
inside PCSX2, which removes the DNS server from the loop entirely. That is an
optimisation for later runs, not a substitute for discovery.

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
