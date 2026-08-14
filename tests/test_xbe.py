"""Tests for the XBE parser that underwrites the Xbox hook map.

Two layers, for two different failure modes.

The synthetic layer builds a complete little XBE in memory and parses it back.
It covers the parts that are pure format-reading -- the certificate, the
section table, the library list, the XOR de-obfuscation -- and it is the layer
that still runs when nobody has a disc dump on the machine.

The image-backed layer pins the facts `docs/xbox-hook-map.md` is written from.
It matters because of how this project has been wrong before: not with a crash,
but with a plausible listing read at the wrong address. The X1 sweep recorded
the ptrk weight tables at "vaddr 0x44C2F4"; that number is the *file offset*,
and the virtual address is 0x0045AF14. Every x86 cross-reference is a search
for a 4-byte absolute VA, so a 0xE320 error in the base would have found
nothing and the miss would have read as "the Xbox build doesn't have it".
`test_recency_table_va` is that specific mistake, nailed down.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon.xbe import ENTRY_XOR, THUNK_XOR, Xbe, XbeError   # noqa: E402

IMAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "extract", "xbox", "default.xbe")

BASE = 0x00010000


def build_xbe(build="retail", sections=((".text", 0x1000, 0x400),)):
    """A minimal but structurally real XBE, laid out like the retail one.

    Header at file 0, then the certificate, the section-name pool, the section
    headers and the library table -- all inside the header block, which loads
    at `base`, which is why their addresses are plain `base + offset`.
    """
    cert_off, names_off, sect_off, libs_off = 0x180, 0x280, 0x300, 0x400
    headers_size = 0x500
    blob = bytearray(headers_size)

    # --- certificate
    title = "Test Title"
    struct.pack_into("<3I", blob, cert_off, 0x1D0, 0x3F000000, 0x45410036)
    blob[cert_off + 0x0C:cert_off + 0x0C + len(title) * 2] = title.encode("utf-16-le")
    struct.pack_into("<5I", blob, cert_off + 0x9C, 1, 2, 3, 4, 7)

    # --- section name pool + section headers
    name_ptr, body = names_off, headers_size
    placed = []
    for name, vaddr, size in sections:
        blob[name_ptr:name_ptr + len(name)] = name.encode("latin-1")
        placed.append((name_ptr, vaddr, size, body))
        name_ptr += len(name) + 1
        body += size
    for i, (npos, vaddr, size, raw) in enumerate(placed):
        struct.pack_into("<6I", blob, sect_off + i * 0x38,
                         0x04, vaddr, size, raw, size, BASE + npos)

    # --- linked libraries
    for i, lib in enumerate((b"XAPILIB", b"LIBCMT")):
        off = libs_off + i * 0x10
        blob[off:off + len(lib)] = lib
        struct.pack_into("<4H", blob, off + 8, 1, 0, 5455, 0)

    # --- header
    entry, thunk = 0x0002A6C6, 0x003E4240
    blob[0:4] = b"XBEH"
    struct.pack_into("<I", blob, 0x104, BASE)
    struct.pack_into("<I", blob, 0x108, headers_size)
    struct.pack_into("<I", blob, 0x10C, 0x100000)
    struct.pack_into("<I", blob, 0x118, BASE + cert_off)
    struct.pack_into("<I", blob, 0x11C, len(sections))
    struct.pack_into("<I", blob, 0x120, BASE + sect_off)
    struct.pack_into("<I", blob, 0x128, entry ^ ENTRY_XOR[build])
    struct.pack_into("<I", blob, 0x158, thunk ^ THUNK_XOR[build])
    struct.pack_into("<I", blob, 0x160, 2)
    struct.pack_into("<I", blob, 0x164, BASE + libs_off)

    for _, _, size, _ in placed:
        blob += bytes(size)
    return bytes(blob), entry, thunk


class SyntheticXbeTest(unittest.TestCase):
    """The format reader, on an image this file built itself."""

    def test_rejects_non_xbe(self):
        with self.assertRaises(XbeError):
            Xbe(b"MZ" + bytes(0x400))

    def test_certificate(self):
        xbe = Xbe(build_xbe()[0])
        self.assertEqual(xbe.cert["title_name"], "Test Title")
        self.assertEqual(xbe.cert["title_id"], 0x45410036)
        self.assertEqual(xbe.cert["version"], 7)

    def test_entry_and_thunk_deobfuscate(self):
        for build in ("retail", "debug"):
            data, entry, thunk = build_xbe(build=build)
            xbe = Xbe(data)
            self.assertEqual(xbe.build_type, build)
            self.assertEqual(xbe.entry, entry)
            self.assertEqual(xbe.kernel_thunk, thunk)

    def test_libraries(self):
        xbe = Xbe(build_xbe()[0])
        self.assertEqual([lib.name for lib in xbe.libraries], ["XAPILIB", "LIBCMT"])
        self.assertEqual(xbe.libraries[0].version, "1.0.5455")

    def test_va_offset_round_trip(self):
        data, _, _ = build_xbe(sections=(("A", 0x20000, 0x100), ("B", 0x30000, 0x100)))
        xbe = Xbe(data)
        for section in xbe.sections:
            for delta in (0, 1, section.raw_size - 1):
                va = section.vaddr + delta
                self.assertEqual(xbe.va_to_off(va), section.raw_off + delta)
                self.assertEqual(xbe.off_to_va(section.raw_off + delta), va)

    def test_unmapped_va_is_none_not_a_guess(self):
        """A VA with no bytes must return None; a plausible offset is worse."""
        data, _, _ = build_xbe(sections=(("A", 0x20000, 0x100),))
        xbe = Xbe(data)
        self.assertIsNone(xbe.va_to_off(0x20100))
        self.assertIsNone(xbe.va_to_off(0x900000))

    def test_find_le32_is_unaligned(self):
        """x86 operands sit at any offset, so the search must not step by 4."""
        data, _, _ = build_xbe(sections=(("A", 0x20000, 0x100),))
        data = bytearray(data)
        section = Xbe(bytes(data)).sections[0]
        struct.pack_into("<I", data, section.raw_off + 3, 0xDEADBEEF)
        xbe = Xbe(bytes(data))
        self.assertEqual(xbe.find_le32(0xDEADBEEF), [section.raw_off + 3])
        self.assertEqual(xbe.xrefs(0xDEADBEEF, section="A"), [0x20003])


@unittest.skipUnless(os.path.exists(IMAGE), "extract/xbox/default.xbe not present")
class RetailImageTest(unittest.TestCase):
    """Regressions on the operator's dump -- the numbers the hook map cites."""

    @classmethod
    def setUpClass(cls):
        cls.xbe = Xbe.load(IMAGE)

    def test_identity(self):
        self.assertEqual(self.xbe.cert["title_name"], "Madden NFL 2004")
        self.assertEqual(self.xbe.cert["title_id"], 0x45410036)
        self.assertEqual(self.xbe.build_type, "retail")
        self.assertEqual(self.xbe.entry, 0x0025A6C6)
        self.assertEqual(self.xbe.base, 0x00010000)

    def test_no_online_libraries(self):
        """The gameplay-only scope rests on this: no XNET, no XONLINE."""
        names = {lib.name for lib in self.xbe.libraries}
        self.assertEqual(names, {"XAPILIB", "D3D8", "XGRAPHC", "XBOXKRNL",
                                 "DSOUND", "LIBCMT", "D3DX8"})

    def test_section_layout(self):
        text = self.xbe.section_by_name(".text")
        self.assertEqual((text.vaddr, text.raw_off, text.raw_size),
                         (0x00011000, 0x1000, 0x360F4C))
        self.assertTrue(text.executable)
        # The .text rule the hook map quotes: file_offset = vaddr - 0x10000.
        self.assertEqual(self.xbe.va_to_off(0x0012C210), 0x0011C210)

    def test_kernel_thunk_points_at_rdata(self):
        """Cross-check on the XOR key: the thunk table opens .rdata."""
        self.assertEqual(self.xbe.kernel_thunk,
                         self.xbe.section_by_name(".rdata").vaddr)

    def test_recency_table_va(self):
        """The X1 'vaddr 0x44C2F4' was a file offset. The VA is 0x0045AF14."""
        off = self.xbe.va_to_off(0x0045AF14)
        self.assertEqual(off, 0x44C2F4)
        weights = struct.unpack("<4f", self.xbe.read(0x0045AF14, 16))
        for got, want in zip(weights, (1 / 24, 1 / 48, 1 / 96, 1 / 192)):
            self.assertAlmostEqual(got, want, places=7)

    def test_recency_table_has_exactly_one_referencing_instruction(self):
        """The xref that identifies the RepetitionFactor twin at 0x0012C9E0.

        One hit in .text, and it is the displacement of the `fadd` at
        0x0012CA24 (`d8 04 85 14 af 45 00`), three bytes into the instruction.
        """
        self.assertEqual(self.xbe.xrefs(0x0045AF14, section=".text"), [0x0012CA27])
        self.assertEqual(self.xbe.read(0x0012CA24, 7).hex(" "),
                         "d8 04 85 14 af 45 00")

    def test_ptrk_registration_immediate(self):
        """`push 'ptrk'` then `push 1556` -- the ctor, byte for byte."""
        self.assertEqual(self.xbe.read(0x0012C214, 12).hex(" "),
                         "68 6b 72 74 70 6a 00 68 14 06 00 00")


if __name__ == "__main__":
    unittest.main()
