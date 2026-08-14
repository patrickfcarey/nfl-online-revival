"""Parse an Xbox executable (XBE) -- header, certificate, sections, libraries.

    python3 recon/xbe.py extract/xbox/default.xbe            # summary
    python3 recon/xbe.py extract/xbox/default.xbe --sections
    python3 recon/xbe.py extract/xbox/default.xbe --va 0x44C2F4

Standard library only, like the rest of `recon/`, so it runs on the rig with no
install step. The XBE format is the Xbox's PE analogue: a fixed header at file
offset 0, a certificate (title name + title id), a section table, and a linked
library-version table. Two fields -- the entry point and the kernel import
thunk -- are stored XOR'd with a build-type-specific key, which is how you tell
a retail image from a debug one: de-XOR with each key and keep whichever result
lands inside the image.

The reason this file exists is the Xbox port of the gameplay work
(`docs/xbox-madden-2004-plan.md`). Every hunt on that side is "which code
references this data address", and x86 uses absolute addressing, so the one
primitive everything needs is a trustworthy VA <-> file-offset map built from
the section table rather than from a remembered constant. `Xbe.off_to_va` /
`Xbe.va_to_off` are that map; `Xbe.find_le32` is the cross-reference sweep
built on top of it.

Verified against the operator's dump of Madden NFL 2004 (USA), Xbox:
title id 0x45410036, retail entry 0x0025A6C6, base 0x00010000, 11 sections,
libraries XAPILIB/D3D8/XGRAPHC/XBOXKRNL/DSOUND/LIBCMT/D3DX8 (no XNET/XONLINE --
the no-online finding in the plan doc).
"""
import struct
import sys

MAGIC = b"XBEH"

#: The entry point and kernel thunk are stored XOR'd. The key identifies the
#: build type, so trying both is also the build-type probe.
ENTRY_XOR = {"retail": 0xA8FC57AB, "debug": 0x94859D4B}
THUNK_XOR = {"retail": 0x5B6D40B6, "debug": 0xEFB1F152}

SECTION_HEADER_SIZE = 0x38
LIBRARY_VERSION_SIZE = 0x10

#: Section flag bits (only the ones worth naming).
SECTION_WRITABLE = 0x00000001
SECTION_PRELOAD = 0x00000002
SECTION_EXECUTABLE = 0x00000004


class XbeError(Exception):
    """The file is not an XBE, or its headers are self-inconsistent."""


class Section:
    """One entry of the XBE section table."""

    def __init__(self, name, flags, vaddr, vsize, raw_off, raw_size):
        self.name = name
        self.flags = flags
        self.vaddr = vaddr
        self.vsize = vsize
        self.raw_off = raw_off
        self.raw_size = raw_size

    @property
    def executable(self):
        return bool(self.flags & SECTION_EXECUTABLE)

    @property
    def writable(self):
        return bool(self.flags & SECTION_WRITABLE)

    def contains_va(self, va):
        """True if `va` falls in this section's *mapped* range.

        Uses the raw size, not the virtual size: the tail of a section whose
        vsize exceeds its raw size is .bss-style zero fill with no bytes in the
        file, and a caller asking for a file offset there must be told no.
        """
        return self.raw_off and self.vaddr <= va < self.vaddr + self.raw_size

    def contains_off(self, off):
        return self.raw_off and self.raw_off <= off < self.raw_off + self.raw_size

    def __repr__(self):
        return "<Section %s vaddr=0x%X raw=0x%X size=0x%X>" % (
            self.name, self.vaddr, self.raw_off, self.raw_size)


class Library:
    """One entry of the linked library-version table."""

    def __init__(self, name, major, minor, build, flags):
        self.name = name
        self.major = major
        self.minor = minor
        self.build = build
        self.flags = flags

    @property
    def version(self):
        return "%d.%d.%d" % (self.major, self.minor, self.build)

    def __repr__(self):
        return "<Library %s %s>" % (self.name, self.version)


class Xbe:
    """A parsed XBE image, with VA <-> file-offset helpers."""

    def __init__(self, data, path=None):
        if len(data) < 0x180 or data[:4] != MAGIC:
            raise XbeError("not an XBE: magic is %r, expected %r"
                           % (data[:4], MAGIC))
        self.data = data
        self.path = path

        u32 = self._u32
        self.base = u32(0x104)
        self.size_of_headers = u32(0x108)
        self.size_of_image = u32(0x10C)
        self.timestamp = u32(0x114)
        self.cert_va = u32(0x118)
        self.section_count = u32(0x11C)
        self.section_headers_va = u32(0x120)
        self.init_flags = u32(0x124)
        self._entry_raw = u32(0x128)
        self.tls_va = u32(0x12C)
        self.pe_base = u32(0x13C)
        self.pe_size_of_image = u32(0x140)
        self.pe_timestamp = u32(0x148)
        self.debug_pathname_va = u32(0x14C)
        self.debug_filename_va = u32(0x150)
        self._thunk_raw = u32(0x158)
        self.library_count = u32(0x160)
        self.library_versions_va = u32(0x164)

        # Header VAs are addressed as if the headers were loaded at `base`,
        # and they are: the header block is the start of the file. So the
        # header-internal VA->offset rule is a plain subtraction, and it must
        # not go through the section map (the section table describes itself).
        self.sections = self._parse_sections()
        self.libraries = self._parse_libraries()
        self.cert = self._parse_cert()
        self.build_type, self.entry, self.kernel_thunk = self._deobfuscate()

    # ---- construction -------------------------------------------------

    @classmethod
    def load(cls, path):
        with open(path, "rb") as handle:
            return cls(handle.read(), path=path)

    def _u32(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def _header_off(self, va):
        """File offset of a VA inside the header block (headers load at base)."""
        off = va - self.base
        if not 0 <= off < len(self.data):
            raise XbeError("header VA 0x%X is outside the file" % va)
        return off

    def _parse_sections(self):
        sections = []
        table = self._header_off(self.section_headers_va)
        for i in range(self.section_count):
            off = table + i * SECTION_HEADER_SIZE
            if off + SECTION_HEADER_SIZE > len(self.data):
                raise XbeError("section table runs past end of file")
            flags, vaddr, vsize, raw_off, raw_size, name_va = struct.unpack_from(
                "<6I", self.data, off)
            sections.append(Section(self._cstring(self._header_off(name_va)),
                                    flags, vaddr, vsize, raw_off, raw_size))
        return sections

    def _parse_libraries(self):
        if not self.library_versions_va or not self.library_count:
            return []
        libraries = []
        table = self._header_off(self.library_versions_va)
        for i in range(self.library_count):
            off = table + i * LIBRARY_VERSION_SIZE
            name = self.data[off:off + 8].rstrip(b"\0").decode("latin-1")
            major, minor, build, flags = struct.unpack_from("<4H", self.data, off + 8)
            libraries.append(Library(name, major, minor, build, flags))
        return libraries

    def _parse_cert(self):
        off = self._header_off(self.cert_va)
        size, timestamp, title_id = struct.unpack_from("<3I", self.data, off)
        name = self.data[off + 0x0C:off + 0x0C + 80].decode("utf-16-le")
        name = name.split("\0", 1)[0]
        media, region, ratings, disk, version = struct.unpack_from(
            "<5I", self.data, off + 0x9C)
        return {
            "size": size, "timestamp": timestamp, "title_id": title_id,
            "title_name": name, "allowed_media": media, "game_region": region,
            "game_ratings": ratings, "disk_number": disk, "version": version,
        }

    def _deobfuscate(self):
        """Recover entry point and kernel thunk, and with them the build type.

        Both fields are XOR'd with a key that differs between retail and debug
        builds. The correct key is the one whose result lands inside the image;
        agreeing on the build type across both fields is the cross-check that
        keeps a coincidence from being read as a decode.
        """
        candidates = []
        for build in ("retail", "debug"):
            entry = self._entry_raw ^ ENTRY_XOR[build]
            thunk = self._thunk_raw ^ THUNK_XOR[build]
            if self.base <= entry < self.base + self.size_of_image:
                candidates.append((build, entry, thunk))
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise XbeError("neither XOR key yields an in-image entry point "
                           "(raw 0x%08X)" % self._entry_raw)
        raise XbeError("both XOR keys yield in-image entry points; ambiguous")

    def _cstring(self, off, limit=256):
        end = self.data.find(b"\0", off, off + limit)
        if end < 0:
            end = off + limit
        return self.data[off:end].decode("latin-1")

    # ---- address translation -------------------------------------------

    def section_by_name(self, name):
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def section_for_va(self, va):
        for section in self.sections:
            if section.contains_va(va):
                return section
        return None

    def section_for_off(self, off):
        for section in self.sections:
            if section.contains_off(off):
                return section
        return None

    def va_to_off(self, va):
        """File offset of a virtual address, or None if it has no bytes."""
        section = self.section_for_va(va)
        if section is None:
            # The header block is mapped at `base` but is not a section.
            if self.base <= va < self.base + self.size_of_headers:
                return va - self.base
            return None
        return section.raw_off + (va - section.vaddr)

    def off_to_va(self, off):
        """Virtual address of a file offset, or None if it maps nowhere."""
        section = self.section_for_off(off)
        if section is None:
            if off < self.size_of_headers:
                return self.base + off
            return None
        return section.vaddr + (off - section.raw_off)

    def read(self, va, length):
        """`length` bytes at a virtual address."""
        off = self.va_to_va_checked(va, length)
        return self.data[off:off + length]

    def va_to_va_checked(self, va, length):
        off = self.va_to_off(va)
        if off is None:
            raise XbeError("VA 0x%X is not backed by file bytes" % va)
        if off + length > len(self.data):
            raise XbeError("read of %d bytes at VA 0x%X runs past end of file"
                           % (length, va))
        return off

    # ---- cross-referencing ---------------------------------------------

    def find_le32(self, value, section=None):
        """Every file offset holding `value` as a 4-byte little-endian word.

        This is the x86 cross-reference primitive: absolute addressing means a
        pointer to data or to a function appears verbatim in the instruction
        stream. Matches are *unaligned* on purpose -- an operand can sit at any
        offset. Note what a hit is and is not: it is a byte pattern, not a
        proven instruction boundary, since x86 instructions are
        variable-length. Corroborate before calling any single hit a reference.
        """
        needle = struct.pack("<I", value)
        blob = self.data
        start, end = 0, len(blob)
        if section is not None:
            sec = self.section_by_name(section) if isinstance(section, str) else section
            if sec is None:
                raise XbeError("no such section: %r" % (section,))
            start, end = sec.raw_off, sec.raw_off + sec.raw_size
        hits, at = [], blob.find(needle, start, end)
        while at >= 0:
            hits.append(at)
            at = blob.find(needle, at + 1, end)
        return hits

    def xrefs(self, va, section=".text"):
        """`find_le32` results expressed as virtual addresses in `section`."""
        return [self.off_to_va(off) for off in self.find_le32(va, section=section)]


# ---- CLI ---------------------------------------------------------------

def _format_summary(xbe):
    lines = []
    cert = xbe.cert
    lines.append("file            %s (%d bytes)" % (xbe.path or "-", len(xbe.data)))
    lines.append("title           %r  id 0x%08X  version 0x%08X"
                 % (cert["title_name"], cert["title_id"], cert["version"]))
    lines.append("build type      %s" % xbe.build_type)
    lines.append("base            0x%08X   size of image 0x%X"
                 % (xbe.base, xbe.size_of_image))
    lines.append("entry           0x%08X  (raw 0x%08X)" % (xbe.entry, xbe._entry_raw))
    lines.append("kernel thunk    0x%08X  (raw 0x%08X)"
                 % (xbe.kernel_thunk, xbe._thunk_raw))
    lines.append("sections        %d" % len(xbe.sections))
    lines.append("libraries       %s"
                 % ", ".join("%s %s" % (lib.name, lib.version) for lib in xbe.libraries))
    return lines


def _format_sections(xbe):
    lines = ["%-12s %-10s %-10s %-10s %-10s %s"
             % ("name", "vaddr", "vsize", "raw off", "raw size", "flags")]
    for section in xbe.sections:
        marks = "".join([
            "X" if section.executable else "-",
            "W" if section.writable else "-",
            "P" if section.flags & SECTION_PRELOAD else "-",
        ])
        lines.append("%-12s 0x%08X 0x%08X 0x%08X 0x%08X %s"
                     % (section.name, section.vaddr, section.vsize,
                        section.raw_off, section.raw_size, marks))
    return lines


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]
    if not args:
        print(__doc__.strip().splitlines()[0])
        print("usage: python3 recon/xbe.py <default.xbe> "
              "[--sections] [--va 0xADDR] [--off 0xOFF] [--xref 0xADDR]")
        return 2
    xbe = Xbe.load(args[0])

    print("\n".join(_format_summary(xbe)))
    if "--sections" in flags:
        print()
        print("\n".join(_format_sections(xbe)))

    # Query flags take the following argv word: `--va 0x44C2F4`.
    for i, flag in enumerate(argv):
        if flag in ("--va", "--off", "--xref") and i + 1 < len(argv):
            value = int(argv[i + 1], 0)
            if flag == "--va":
                off = xbe.va_to_off(value)
                section = xbe.section_for_va(value)
                print("\nVA 0x%08X -> file 0x%X  section %s"
                      % (value, off if off is not None else -1,
                         section.name if section else "(none)"))
            elif flag == "--off":
                va = xbe.off_to_va(value)
                section = xbe.section_for_off(value)
                print("\nfile 0x%X -> VA 0x%08X  section %s"
                      % (value, va if va is not None else -1,
                         section.name if section else "(none)"))
            else:
                hits = xbe.find_le32(value)
                print("\n%d little-endian occurrences of 0x%08X:" % (len(hits), value))
                for off in hits[:64]:
                    va = xbe.off_to_va(off)
                    section = xbe.section_for_off(off)
                    print("  file 0x%06X  VA %s  %s"
                          % (off, "0x%08X" % va if va else "-",
                             section.name if section else "(headers)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
