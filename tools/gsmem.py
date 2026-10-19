"""Minimal PS2 GS local-memory model (4 MB) with swizzled read/write for
PSMCT32, PSMCT24, PSMCT16(S), PSMT8, PSMT4, and CLUT/texture decoding to RGBA."""
import numpy as np

BLOCK32 = [[0, 1, 4, 5, 16, 17, 20, 21],
           [2, 3, 6, 7, 18, 19, 22, 23],
           [8, 9, 12, 13, 24, 25, 28, 29],
           [10, 11, 14, 15, 26, 27, 30, 31]]
BLOCK16 = [[0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
           [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31]]
BLOCK16S = [[0, 2, 16, 18], [1, 3, 17, 19], [8, 10, 24, 26], [9, 11, 25, 27],
            [4, 6, 20, 22], [5, 7, 21, 23], [12, 14, 28, 30], [13, 15, 29, 31]]
BLOCK8 = BLOCK32
BLOCK4 = BLOCK16
# word index inside an 8x8 PSMCT32 block
COL32 = np.zeros((8, 8), np.int64)
for _y in range(8):
    for _x in range(8):
        COL32[_y, _x] = (_y >> 1) * 16 + (_y & 1) * 2 + (_x & 1) + ((_x >> 1) * 4)

MEM_WORDS = 1024 * 1024  # 4 MB


def _addr32(bp, bw, x, y):
    page = (y >> 5) * bw + (x >> 6)
    blk = np.array(BLOCK32)[(y >> 3) & 3, (x >> 3) & 7]
    return (bp * 64 + page * 2048 + blk * 64 + COL32[y & 7, x & 7]) % MEM_WORDS


def _addr16(bp, bw, x, y, table):
    """Returns (word address, halfword 0/1)."""
    page = (y >> 6) * bw + (x >> 6)
    blk = np.array(table)[(y >> 3) & 7, (x >> 4) & 3]
    lx, ly = x & 15, y & 7
    w = COL32[ly, lx & 7]
    return (bp * 64 + page * 2048 + blk * 64 + w) % MEM_WORDS, lx >> 3


def _addr8(bp, bw, x, y):
    """Returns (word address, byte 0..3)."""
    page = (y >> 6) * (bw >> 1) + (x >> 7)
    blk = np.array(BLOCK8)[(y >> 4) & 3, (x >> 4) & 7]
    lx, ly = x & 15, y & 15
    swap = (((ly + 2) >> 2) & 1) * 4
    cx = (lx + swap) & 7
    cy = ((ly >> 2) << 1) | (ly & 1)
    byte = ((lx >> 3) << 1) | ((ly >> 1) & 1)
    return (bp * 64 + page * 2048 + blk * 64 + COL32[cy, cx]) % MEM_WORDS, byte


def _addr4(bp, bw, x, y):
    """Returns (word address, nibble 0..7)."""
    page = (y >> 7) * (bw >> 1) + (x >> 7)
    blk = np.array(BLOCK4)[(y >> 4) & 7, (x >> 5) & 3]
    lx, ly = x & 31, y & 15
    swap = (((ly + 2) >> 2) & 1) * 4
    cx = (lx + swap) & 7
    cy = ((ly >> 2) << 1) | (ly & 1)
    byte = (lx >> 3) & 3
    nib = (ly >> 1) & 1
    return (bp * 64 + page * 2048 + blk * 64 + COL32[cy, cx]) % MEM_WORDS, byte * 2 + nib


class GSMem:
    def __init__(self):
        self.mem = np.zeros(MEM_WORDS, np.uint32)

    def _coords(self, w, h, dx=0, dy=0):
        ys, xs = np.mgrid[0:h, 0:w]
        return (xs + dx).astype(np.int64) & 2047, (ys + dy).astype(np.int64) & 2047

    def write(self, bp, bw, psm, w, h, dx, dy, data):
        """Host->local transfer of a w*h rectangle at (dx,dy)."""
        x, y = self._coords(w, h, dx, dy)
        x, y = x.ravel(), y.ravel()
        n = w * h
        m = self.mem
        if psm in (0x00, 0x30):
            px = np.frombuffer(data[:n * 4].ljust(n * 4, b'\0'), np.uint32)
            m[_addr32(bp, bw, x, y)] = px
        elif psm in (0x01, 0x31):
            raw = np.frombuffer(data[:n * 3].ljust(n * 3, b'\0'), np.uint8).reshape(-1, 3).astype(np.uint32)
            a = _addr32(bp, bw, x, y)
            m[a] = (m[a] & 0xff000000) | raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        elif psm in (0x02, 0x0a, 0x32, 0x3a):
            px = np.frombuffer(data[:n * 2].ljust(n * 2, b'\0'), np.uint16).astype(np.uint32)
            table = BLOCK16S if psm in (0x0a, 0x3a) else BLOCK16
            a, hw = _addr16(bp, bw, x, y, table)
            self._put_bits(a, hw * 16, 0xffff, px)
        elif psm in (0x13, 0x1b):
            px = np.frombuffer(data[:n].ljust(n, b'\0'), np.uint8).astype(np.uint32)
            a, b = _addr8(bp, bw, x, y)
            if psm == 0x13:
                self._put_bits(a, b * 8, 0xff, px)
            else:  # PSMT8H: upper 8 bits of a 32-bit pixel
                self._put_bits(_addr32(bp, bw, x, y), np.full(n, 24), 0xff, px)
        elif psm in (0x14, 0x24, 0x2c):
            raw = np.frombuffer(data[:(n + 1) // 2].ljust((n + 1) // 2, b'\0'), np.uint8)
            px = np.empty(raw.size * 2, np.uint32)
            px[0::2] = raw & 15
            px[1::2] = raw >> 4
            px = px[:n]
            if psm == 0x14:
                a, nb = _addr4(bp, bw, x, y)
                self._put_bits(a, nb * 4, 0xf, px)
            else:
                sh = 24 if psm == 0x24 else 28
                self._put_bits(_addr32(bp, bw, x, y), np.full(n, sh), 0xf, px)
        else:
            raise ValueError('unsupported psm %#x' % psm)

    def _put_bits(self, a, shift, mask, val):
        # several sub-word pixels share one 32-bit word, so use unbuffered ufunc.at
        shift = np.broadcast_to(np.asarray(shift, np.uint32), np.shape(a))
        val = np.asarray(val, np.uint32)
        np.bitwise_and.at(self.mem, a, ~(np.uint32(mask) << shift))
        np.bitwise_or.at(self.mem, a, (val & np.uint32(mask)) << shift)

    def read_index(self, bp, bw, psm, w, h):
        x, y = self._coords(w, h)
        if psm == 0x13:
            a, b = _addr8(bp, bw, x, y)
            return (self.mem[a] >> (b * 8).astype(np.uint32)) & 0xff
        if psm == 0x14:
            a, nb = _addr4(bp, bw, x, y)
            return (self.mem[a] >> (nb * 4).astype(np.uint32)) & 0xf
        if psm == 0x1b:
            return self.mem[_addr32(bp, bw, x, y)] >> 24
        if psm == 0x24:
            return (self.mem[_addr32(bp, bw, x, y)] >> 24) & 0xf
        if psm == 0x2c:
            return self.mem[_addr32(bp, bw, x, y)] >> 28
        raise ValueError

    def read_rgba(self, bp, bw, psm, w, h, alpha_x2=True):
        """Direct-colour read, returns HxWx4 uint8."""
        x, y = self._coords(w, h)
        if psm in (0x00, 0x01, 0x30, 0x31):
            v = self.mem[_addr32(bp, bw, x, y)]
            out = np.stack([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, v >> 24], -1).astype(np.uint32)
            if psm in (0x01, 0x31):
                out[..., 3] = 0x80
        elif psm in (0x02, 0x0a, 0x32, 0x3a):
            table = BLOCK16S if psm in (0x0a, 0x3a) else BLOCK16
            a, hw = _addr16(bp, bw, x, y, table)
            v = (self.mem[a] >> (hw * 16).astype(np.uint32)) & 0xffff
            out = np.stack([(v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3,
                            np.where(v & 0x8000, 0x80, 0)], -1).astype(np.uint32)
        else:
            raise ValueError
        if alpha_x2:
            out[..., 3] = np.minimum(out[..., 3] * 2, 255)
        return out.astype(np.uint8)

    def read_clut(self, cbp, cpsm, psm, csa=0):
        """Returns a 256x4 (or 16x4) RGBA palette, CSM1 layout."""
        if psm in (0x13, 0x1b):
            pal = self.read_rgba(cbp, 1, cpsm, 16, 16).reshape(256, 4)
            idx = np.arange(256)
            idx = (idx & 0xe7) | ((idx & 8) << 1) | ((idx & 16) >> 1)
            return pal[idx]
        # 4-bit: 8x2 block, CSA selects one of 16 (CT32) / 32 (CT16) sub-palettes
        if cpsm in (0x00, 0x30):
            pal = self.read_rgba(cbp, 1, cpsm, 16, 16).reshape(256, 4)
            idx = np.arange(256)
            idx = (idx & 0xe7) | ((idx & 8) << 1) | ((idx & 16) >> 1)
            pal = pal[idx]
            return pal[(csa & 15) * 16:(csa & 15) * 16 + 16]
        pal = self.read_rgba(cbp, 1, cpsm, 16, 32).reshape(512, 4)
        idx = np.arange(512)
        idx = (idx & ~0x18) | ((idx & 8) << 1) | ((idx & 16) >> 1)
        pal = pal[idx]
        return pal[csa * 16:csa * 16 + 16]

    def read_texture(self, tbp, tbw, psm, w, h, cbp=0, cpsm=0, csa=0):
        if psm in (0x13, 0x14, 0x1b, 0x24, 0x2c):
            idx = self.read_index(tbp, tbw, psm, w, h)
            pal = self.read_clut(cbp, cpsm, psm, csa)
            return pal[idx]
        return self.read_rgba(tbp, tbw, psm, w, h)
