# Anim lane 3: the clip inventory — id→data mechanism, root motion, and the driving-clip candidates

Recorded 2026-08-11. Static only, against `extract/SLUS_207.52`
(vaddr = file offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`, and
against `extract/ee_inplay.bin` (32 MB EE image, vaddr == offset; pre-snap,
22 players) for live data structures. No rig, no network, no emulator, no
commits. Everything quoted was read this pass; **UNVERIFIED** marks inference.

Mission context: Route C is confirmed (`docs/on-skates-requirements.md`) —
during kinds 5/6 the animation's root motion owns both players' transforms,
so driving a defender backwards means SELECTING a different clip. This lane
answers: what is a clip id, where is its data, what carries displacement, and
which ids are the drive/pancake candidates.

---

## 1. What an "animation id" is, and how it reaches clip data (VERIFIED chain)

One global u16 id namespace covers singles (28, 74, 85, 86, 91, 144…), the
shed-contest outcomes (62-65, 118-131) and the two-man block family
(146-173). Resolution is per **character class** ("ctx"): every on-field
player reads ctx from `[player+0x308]+2` (u16) — **all 22 players read ctx 1
in ee_inplay.bin**.

The registry (all offsets read from the disassembly and verified against the
in-play dump):

```
count   = [0x00608098]              (gp+10664; 81 slots, 20 loaded in-play)
table   = [0x006080b0]              (gp+10688; 0x00f848f0 in-play)
entry g = table + 28*g:             28-byte group row
   +0x00  u32  animdata.dat member number this group was loaded from
   +0x0C  ptr  group data (the loaded member, resident in EE RAM)
   +0x12  u8   type (1 = id-serving group)
   +0x14  u16  ctx served (0x8000/0xFFFF = empty)
```

Loader: at boot `0x0047ebf8("animdata.dat", 44)` opens the container (handle
stored at `0x00600b30`; `pladata.dat` handle at `0x00600b34` — filename table
at va `0x0054b0a0`); `0x0047f480(handle, member, 1)` loads a member and
`0x003b3110` registers it as a group. **Clip data lives in ANIMDATA.DAT
(not in extract/), but the loaded members ARE resident in the in-play dump**
(0x008xxxxx-0x00dxxxxx), so they were read from there.

Group data layout (id index):

```
gd+0x08 -> index block: u32 count, then 16-byte entries
   entry+0x04  u16  animation id        (lookup: 0x003a9328, linear scan)
   entry+0x06  u16  member tag
   entry+0x08  ptr  variant list: u16 count; 8-byte items
                    { u16 tag @ +4+8k, ptr record @ +8+8k }
   entry+0x0C  ptr  sequence/component object
gd+0x0C/0x10/0x14 -> per-sequence sections (see §3a)
```

Lookup call chain (all read this pass): `0x003ad530(ctx, id)` →
`0x003aa630` → `0x003aa398(id, ctx, buf, 32)` scans the group table and
builds descriptor entries `{group, tag, record*}` into the fixed scratch
buffer **`0x00627418`**; the pair-anim selector `0x0018d998` consumes them.

**Two-level ids for two-man animations:** a pair id's variant tags carry bit
15 (e.g. 0xa1b6); `tag & 0x7FFF` is ITSELF an id (0x21b6 = 8630) resolved
through the same index (`0x0018daa4: jal 0x003aa790` with `andi a0,a0,0x7fff`).
That component id's entry+0x0C is the **component record**:

```
+0x00  u16 seq#, u16 frame count (0x2C = 44 frames for pair 146 variant 0)
+0x04  u32 participant count (2)
+0x08  per-participant rows (20 bytes): { u16 role seq#, u16 flags,
        BAM24 relative heading, float rel-x, float rel-z }
```

Pair 146 variant 0 (component 8630, read at 0x00cefd2c in-play): roles
seq# 0x56/0x57, alignment = **BAM24 0x82bfa1 ≈ 184° (face-to-face) at
(1.83, −0.09) yd** — the blocker/defender chest-to-chest attach geometry the
capture's 20° tolerance (`0x000E38E3` at request+0x34) is tested against.

No name table exists for animations: the only "Pancake" string in the ELF
(va 0x00602568) is a stats-screen label. Clip semantics below come from code
that special-cases ids, not from strings.

## 2. Root motion: how displacement is represented and who writes the body (VERIFIED code, data pending)

**Representation:** not per-frame keyframed root translation. A per-role
spec block holds a **total displacement vector, a duration, and a final
heading**; the engine converts them ONCE at start into per-frame deltas and
applies those linearly. That is exactly the measured "14-30-frame rigid
two-body translation" of block-cycle.md.

The conversion — `0x0018f9e0(player, motion_block, spec)`:

```
spec+0x08   displacement vector (rotated into world by 0x004ada50)
spec+0x14   second vector (anchor/offset)
spec+0x20   duration (float; the divisor)
spec+0x38   final heading term (BAM24 math via atan2 0x00469e78 /
            angle-diff 0x00469fc8)
writes:  motion_block { +0 dx/frame, +4 dz/frame, +8 dheading/frame (BAM24),
                        +0xC frames remaining }
and sets player flag +0xC |= 0x200 while interaction root motion owns him.
```

**The applier — `0x0018f980(player, motion_block)`**, called from the
participant handler `0x0018fbe8` (phase 2 = advance) at `0x0018fce8`:

```
0018f990  if --[block+0xC] <= 0: stop
0018f9a0  f0 = [player+0x190] + [block+0]   ; pos_x += dx
0018f9bc  swc1 f0, 0x190(player)
0018f9a8  [player+0x1A8] = ([player+0x1A8] & 0x00FFFFFF) + [block+8]
                                            ; heading += dheading (BAM24)
0018f9c8  [player+0x194] += [block+4]       ; pos_z += dz
```

Guard at `0x0018fcd4`: the deltas are applied **only while
`0x003ad410([player+0x304]) == id`** — the slot must still be playing the
clip. This is the code that "drives a body" during an interaction; it was
previously unlocated. `0x0018fbe8` is registered as an interaction-service
callback at `0x00180efc` (`[svc+0x2f10+0x98]`).

A second applier exists inside `0x0018c7c0` (callback, registered
`0x00168c8c`): at `0x0018cc20-0x0018cc3c` it steps pos_x/pos_z by
`[ctl+0x1C]/[ctl+0x20]` per frame — displacement **divided by a frame count**
at `0x0018cbc4-0x0018cbd0` — i.e. the approach/alignment glide that walks the
two participants onto the attach anchor. It is **killed by a global byte:
`0x0060111d` == 0 zeroes both per-frame steps** (`0x0018cbdc`), a shipped
lever over interaction glide worth knowing about (writer not traced —
UNVERIFIED what sets it).

**Where per-role motion enters:** phase 0 of `0x0018fbe8` reads the runtime
row `[interaction_obj + p*8 + 8]` as the spec — the launcher `0x0018e378`
builds that object when it pushes both players into AI state 32 (commands via
`0x001b0170`/`0x001afb50`), stamping on each player:

```
player+0x3DC  u16  participant/sequence word (from the selected variant)
player+0x3DE  u16  THE PAIR ANIMATION ID     (bytes 0x3DE/0x3DF)
```

— two bytes before engagement kind +0x3E0. This stamp is the cleanest live
hook for "which clip did this engagement select" (§4).

State 32's think `0x001e8088` (read in full) matches block-cycle.md: mutual
no-collide = `0x00213628(a,b)` both directions at `0x001e8124-0x001e8134`;
segment-end flag = player flag `+0xC & 4`; promote 5→6 at `0x001e81ec`; exit
re-arms locomotion with speed 0.46 from `0x005fef3c` (`0x001e8224`).

## 3. The inventory — id families and the driving-clip candidates

### 3a. Where each id lives (walked from the live registry in ee_inplay.bin)

| ids | group (member) | family | evidence |
|---|---|---|---|
| 146-151 | g18 (member 0x0A) | two-man block, DT-gate **yes-set** | table 0x00583360 yes-arm exactly = g18's 146-155 range members |
| 168-170, 173 | g18 (member 0x0A) | same family, later additions | yes-set; 12-16 variants each (146-151 have 6) |
| 158, 161 | g19 (member 0x0B) | the capture/neutral pair | 158 = the id `0x001f7c98` hard-codes; NO-set |
| 152-157, 159-160, 164-167, 171-172 | g1/g2/g12/g14/g16 | other interaction families | scattered; NO-set |
| 162, 163 | **not loaded in-play** | — | absent from every resident group |
| 118-131 | g17 (member 6) | shed-contest outcome pairs | selected by `0x001a7070` |
| 62-65 | g15 (member 4) | shed win (non-contact finishes) | pass-table fallback chain members |

### 3b. Ids with PROVEN backward-drive semantics (the shed outcome tables)

`0x001a7070(win, moveType)` — tables re-read as data this pass; entries are
24 bytes = win chain +0, lose chain +12; chains are u32 ids, 0xFFFF-ends:

```
RUN  0x00526668:  move 0..6 win = 62, 63, 122, 123, 126, 127, 130
                  move 0..6 lose = 120, 121, 124, 125, 128, 129, 131
PASS 0x00526710:  win = [118,62], [119,63], 122, 123, [126,64], [127,65], 130
                  lose = 120, 121, 124, 125, 128, 129, 131
fallback 0x00526704 = 131
```

Per cpu-dt-animations.md (adversarially verified doc): the lose set is the
defender **"driven back / pancaked"** family, win = shed/swim/rip/club, and
move 6 is the bull rush — so **id 130 = a blocker being driven backwards**
(bull-rush win) and **ids 120/121/124/125/128/129/131 = the defender being
driven back or pancaked**. These are two-man clips in the same interaction
machinery (paired variants with bit-15 tags, verified for 120/121/130/131 in
the dump). **Dramatic-displacement pair clips therefore demonstrably exist in
the shipped data, exactly as the operator's 256x pancake observation
implied** — and they are selected today by the shed contest, not by the
block-cycle capture (which hard-codes 158).

### 3c. The yes-set displacement question (data decode — see §5 status)

The per-sequence sections of a group (gd+0x0C: counted pointer table to
variable-length rows; gd+0x10: **1511 pointers = one per sequence**, matching
the group header's 0x05E7, to uniform 84-byte records; gd+0x14: counted
pointer table) hold the per-role sequence headers. The 84-byte records at
`[gd+0x10][seq#]` are the prime candidate for the `{vector, duration,
heading}` spec consumed by `0x0018f9e0`. Decode status per id: §5.

Sibling intel (lane 1, `0-partials-rescued.md`): the two-man selector rolls
on the +0x41C contest margin and pushes **id 168** with facing sub-cases at
85° — so the engine already picks yes-set clips by margin, and 168 is a live
margin-driven selection. 168 has 16 variants (146-151 have 6), consistent
with a richer outcome family.

## 4. THE LIVE PROBE — sample "which clip is he playing" (ready to run)

Player base: `[0x00600E48]` descriptor → base 0x00661B90, stride 0x14C0,
side*11+index (addresses.yaml). Three complementary words per player:

1. **Current animation id** — follow `animptr = [player+0x304]` (points at
   player+0x954, verified in-play); four slots of 0x64 bytes at
   `animptr + 0x64*k`:
   `status = u16[slot+6]` (3 = active), `id = u16[slot+4]`.
   The engine getter `0x003ad410` returns the **lowest** active slot's id
   (scan k = 3..0, last write wins), 0xFFFF if none. Verified against
   ee_inplay.bin: pre-snap stances read 85/86/91/28/74/144 in slot 0.
2. **Selected pair-anim id** — `u16[player+0x3DE]`, stamped by the launcher
   `0x0018e378` at interaction start (with the participant word at +0x3DC).
   Read together with kind `+0x3E0` (5/6) and AI state 32.
3. **Instance timing** — `[player+0x30C]` → up to 10 records of 0x7C
   (player+0x47C, verified); `float[rec+0x54]` = remaining time (state 32's
   own exit math reads it via `0x003ac3d0(rec+0x4C)` = `[+8]`).

Recipe for the pancake hunt (one play, no patch): sample per frame for the
engaged pair — `+0x3DE` u16, `+0x3E0` kind, anim slots (1), positions
+0x190/+0x194. When the operator sees the pancake, the frame where kind
enters 5 names the clip in +0x3DE, and the slot scan confirms which id was
playing while the displacement happened. Addresses above are all absolute
player-record offsets; no code hook is needed.

## 5. Status of the static displacement decode (updated in place)

Decoded so far: id → variants → component → role seq#s → the per-sequence
pointer tables. IN PROGRESS at cutoff: reading the 84-byte per-sequence
records for the yes-set roles and 158 to extract `{vector, duration}` and
rank the drivers statically. This section is updated below as records are
read.

## 6. Could not establish

1. The byte-level keyframe stream (joint curves) — streamed via the 200 KB
   ring set up at `0x0012c088` (`0x004816b8`); not needed for Route C.
2. What writes the glide kill-byte `0x0060111d`.
3. Ids 162/163: absent from every in-play group; whether another .DAT member
   carries them is unknown.
4. The semantic meaning of each yes-set id (which is "drive", which
   "stalemate") from static data alone — pending §5 or the §4 probe.
5. The runtime interaction object's full layout (only rows +4/+6/+8 read).
