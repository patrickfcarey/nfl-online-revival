"""EA "LZH1" (codec id 5) decompressor + TERF container reader.

Reversed from Madden NFL 2004 PS2 ELF (SLUS_207.52):
  codec descriptor table entry 5 @ 0x00574770  name 'LZH1', flags 3
  init                     0x004ff188  (32 KiB window alloc, state = 0x004ffe30)
  block-header state       0x004ffe30
  block-body/decode state  0x00500008
  getbits(n)               0x00500c68   (MSB-first)
  read 4-bit code lengths  0x00501d88
  canonical code assign    0x00501e70
  length base   table      0x005fc940 (deflate)
  length extra  table      0x005fc920
  dist   base   table      0x005fc9a0 (deflate)
  dist   extra  table      0x005fc980

Format: deflate's symbol grammar with MSB-first bit packing and a trivially
stored code-length table (no code-length-code layer):

  loop:
    1 bit  -- 1 => end of stream (followed by 32 bits, ignored); 0 => block
    285 x 4 bits : code lengths, literal/length alphabet (0..284)
     30 x 4 bits : code lengths, distance alphabet (0..29)
    symbols until 256 (end of block), then loop
"""
import struct

LEN_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43,
            51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 228)
LEN_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3,
             4, 4, 4, 4, 5, 5, 5, 0, 0)
DIST_BASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257,
             385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289,
             16385, 24577)
DIST_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8,
              9, 9, 10, 10, 11, 11, 12, 12, 13, 13)


class Bits:
    def __init__(self, data):
        self.d = data
        self.p = 0
        self.buf = 0
        self.n = 0

    def get(self, k):
        if k == 0:
            return 0
        while self.n < k:
            b = self.d[self.p] if self.p < len(self.d) else 0
            self.p += 1
            self.buf = (self.buf << 8) | b
            self.n += 8
        self.n -= k
        v = self.buf >> self.n
        self.buf &= (1 << self.n) - 1
        return v


def build(lengths):
    """Canonical Huffman, exactly as 0x00501e70. -> dict {(len,code): sym}."""
    count = [0] * 17
    for l in lengths:
        if l:
            count[l] += 1
    code = 0
    next_code = [0] * 17
    for l in range(1, 17):
        code = (code + count[l - 1]) << 1
        next_code[l] = code
    table = {}
    for sym, l in enumerate(lengths):
        if l:
            table[(l, next_code[l])] = sym
            next_code[l] += 1
    return table


def decode_sym(bs, table):
    code = 0
    for l in range(1, 17):
        code = (code << 1) | bs.get(1)
        s = table.get((l, code))
        if s is not None:
            return s
    raise ValueError("bad huffman code")


def lzh1_decompress(src, out_size=None):
    bs = Bits(src)
    win = bytearray(0x8000)
    wp = 0
    out = bytearray()
    while True:
        if bs.get(1):
            bs.get(32)
            break
        lit = build([bs.get(4) for _ in range(285)])
        dist = build([bs.get(4) for _ in range(30)])
        while True:
            s = decode_sym(bs, lit)
            if s < 256:
                win[wp] = s
                out.append(s)
                wp = (wp + 1) & 0x7FFF
                continue
            if s == 256:
                break
            i = s - 257
            if i < 8:
                length = i + 3
            elif LEN_EXTRA[i] == 0:
                length = 227
            else:
                length = LEN_BASE[i] + bs.get(LEN_EXTRA[i])
            ds = decode_sym(bs, dist)
            d = ds + 1 if ds < 4 else DIST_BASE[ds] + bs.get(DIST_EXTRA[ds])
            sp = (wp - d) & 0x7FFF
            for _ in range(length):
                b = win[sp]
                win[wp] = b
                out.append(b)
                wp = (wp + 1) & 0x7FFF
                sp = (sp + 1) & 0x7FFF
        if out_size is not None and len(out) >= out_size:
            break
    return bytes(out)


def read_terf(path):
    """-> list of (index, decompressed bytes)."""
    d = open(path, 'rb').read()
    chunks = {}
    p = 0
    while p + 8 <= len(d):
        tag = d[p:p + 4]
        sz = struct.unpack('<I', d[p + 4:p + 8])[0]
        if sz < 8:
            break
        chunks[tag] = (p, sz)
        p += sz
    hp, hs = chunks[b'TERF']
    # TERF payload: u8 ver, u8 ver, u8, u8 intsize(=5), u16 capacity, u16 count
    n_entries = struct.unpack('<H', d[hp + 8 + 6:hp + 8 + 8])[0]
    dp, ds = chunks[b'DIR1']
    dirw = struct.unpack('<%dI' % ((ds - 8) // 4), d[dp + 8:dp + ds])
    comp = None
    if b'COMP' in chunks:
        cp, cs = chunks[b'COMP']
        comp = struct.unpack('<%dI' % ((cs - 8) // 4), d[cp + 8:cp + cs])
    base = chunks[b'DATA'][0]
    out = []
    for i in range(n_entries):
        off, csz = dirw[2 * i], dirw[2 * i + 1]
        blob = d[base + off:base + off + csz]
        if comp is None:
            out.append((i, blob))
            continue
        ctype, usz = comp[2 * i], comp[2 * i + 1]
        if ctype == 5:
            r = lzh1_decompress(blob, usz)
            assert len(r) == usz, (i, len(r), usz)
        elif ctype == 0:
            r = blob
        else:
            raise NotImplementedError('codec %d' % ctype)
        out.append((i, r))
    return out


if __name__ == '__main__':
    import sys
    for i, b in read_terf(sys.argv[1]):
        open('%s.%02d.bin' % (sys.argv[2], i), 'wb').write(b)
        print(i, len(b))
