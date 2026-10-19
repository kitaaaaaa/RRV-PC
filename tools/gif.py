"""Tiny GIF/GS interpreter: executes GIF packets (PACKED / REGLIST / IMAGE) from DMA chains
into a gsmem.GSMem, rasterising untextured SPRITE / TRIANGLE / TRISTRIP / TRIFAN primitives
into PSMCT32 frame buffers.  Enough to rebuild textures the game composes at load time."""
import struct
import numpy as np
import gsmem

PRIM, RGBAQ, ST, UV, XYZF2, XYZ2, TEX0_1, TEX0_2 = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07
XYZF3, XYZ3 = 0x0c, 0x0d
XYOFFSET_1, XYOFFSET_2, PRMODECONT, PRMODE = 0x18, 0x19, 0x1a, 0x1b
SCISSOR_1, SCISSOR_2, FRAME_1, FRAME_2 = 0x40, 0x41, 0x4c, 0x4d
BITBLTBUF, TRXPOS, TRXREG, TRXDIR = 0x50, 0x51, 0x52, 0x53


class GS:
    def __init__(self, mem=None):
        self.mem = mem or gsmem.GSMem()
        self.r = {}
        self.prim = 0
        self.prmode = 0
        self.prmodecont = 1
        self.rgba = 0x80808080
        self.verts = []
        self.xfer = None
        self.unsupported = set()

    # -- register writes -------------------------------------------------
    def write(self, addr, val):
        self.r[addr] = val
        if addr == PRIM:
            self.prim = val
            self.verts = []
        elif addr == PRMODE:
            self.prmode = val
        elif addr == PRMODECONT:
            self.prmodecont = val & 1
        elif addr == RGBAQ:
            self.rgba = val & 0xffffffff
        elif addr in (XYZ2, XYZF2, XYZ3, XYZF3):
            x = (val & 0xffff) / 16.0
            y = ((val >> 16) & 0xffff) / 16.0
            self.vertex(x, y, kick=addr in (XYZ2, XYZF2))
        elif addr == TRXDIR:
            b, p, r = self.r.get(BITBLTBUF, 0), self.r.get(TRXPOS, 0), self.r.get(TRXREG, 0)
            self.xfer = dict(dbp=(b >> 32) & 0x3fff, dbw=(b >> 48) & 0x3f, psm=(b >> 56) & 0x3f,
                             dx=(p >> 32) & 0x7ff, dy=(p >> 48) & 0x7ff, w=r & 0xfff, h=(r >> 32) & 0xfff,
                             dir=val & 3)

    def image(self, data):
        x = self.xfer
        if x and x['dir'] == 0 and x['w'] and x['h']:
            self.mem.write(x['dbp'], x['dbw'], x['psm'], x['w'], x['h'], x['dx'], x['dy'], data)

    # -- drawing ---------------------------------------------------------
    def ctx(self):
        attr = self.prim if self.prmodecont else self.prmode
        return (attr >> 9) & 1

    def vertex(self, x, y, kick=True):
        ptype = self.prim & 7
        self.verts.append((x, y, self.rgba))
        need = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3, 5: 3, 6: 2}.get(ptype, 99)
        if len(self.verts) < need:
            return
        v = self.verts[-need:]
        if kick:
            if ptype == 6:
                self.sprite(v[0], v[1])
            elif ptype in (3, 4, 5):
                if ptype == 5:
                    v = [self.verts[0]] + self.verts[-2:]
                self.triangle(v)
        if ptype in (6, 3, 1):
            self.verts = []
        elif ptype == 5:
            pass
        else:
            self.verts = self.verts[-(need - 1):] if need > 1 else []
        if (self.prim >> 4) & 1:
            self.unsupported.add('textured prim %d' % ptype)

    def _target(self):
        c = self.ctx()
        fr = self.r.get(FRAME_2 if c else FRAME_1, 0)
        xo = self.r.get(XYOFFSET_2 if c else XYOFFSET_1, 0)
        sc = self.r.get(SCISSOR_2 if c else SCISSOR_1, 0)
        fbp, fbw, psm = (fr & 0x1ff) * 32, (fr >> 16) & 0x3f, (fr >> 24) & 0x3f
        ox, oy = (xo & 0xffff) / 16.0, ((xo >> 32) & 0xffff) / 16.0
        s = (sc & 0x7ff, (sc >> 16) & 0x7ff, (sc >> 32) & 0x7ff, (sc >> 48) & 0x7ff)
        return fbp, fbw, psm, ox, oy, s

    def _fill(self, xs, ys, colors):
        fbp, fbw, psm, _, _, (sx0, sx1, sy0, sy1) = self._target()
        keep = (xs >= sx0) & (xs <= sx1) & (ys >= sy0) & (ys <= sy1) & (xs >= 0) & (ys >= 0)
        xs, ys = xs[keep], ys[keep]
        colors = colors[keep] if np.ndim(colors) else colors
        if xs.size == 0:
            return
        if psm in (0x00, 0x30):
            self.mem.mem[gsmem._addr32(fbp, fbw, xs, ys)] = colors
        elif psm in (0x01, 0x31):
            a = gsmem._addr32(fbp, fbw, xs, ys)
            self.mem.mem[a] = (self.mem.mem[a] & 0xff000000) | (np.uint32(colors) & 0xffffff)
        else:
            self.unsupported.add('frame psm %#x' % psm)

    def sprite(self, a, b):
        fbp, fbw, psm, ox, oy, _ = self._target()
        x0, x1 = sorted((a[0] - ox, b[0] - ox))
        y0, y1 = sorted((a[1] - oy, b[1] - oy))
        ix0, ix1 = int(np.ceil(x0)), int(np.ceil(x1))
        iy0, iy1 = int(np.ceil(y0)), int(np.ceil(y1))
        if ix1 <= ix0 or iy1 <= iy0:
            return
        ys, xs = np.mgrid[iy0:iy1, ix0:ix1]
        self._fill(xs.ravel(), ys.ravel(), np.uint32(b[2]))

    def triangle(self, v):
        fbp, fbw, psm, ox, oy, _ = self._target()
        p = [(x - ox, y - oy, c) for x, y, c in v]
        xs_ = [q[0] for q in p]; ys_ = [q[1] for q in p]
        ix0, ix1 = int(np.floor(min(xs_))), int(np.ceil(max(xs_)))
        iy0, iy1 = int(np.floor(min(ys_))), int(np.ceil(max(ys_)))
        if ix1 <= ix0 or iy1 <= iy0:
            return
        ys, xs = np.mgrid[iy0:iy1, ix0:ix1]
        px, py = xs + 0.0, ys + 0.0
        (x0, y0, c0), (x1, y1, c1), (x2, y2, c2) = p
        d = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if d == 0:
            return
        w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / d
        w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / d
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if (self.prim >> 3) & 1:  # gouraud
            cs = []
            for sh in (0, 8, 16, 24):
                ch = w0 * ((c0 >> sh) & 255) + w1 * ((c1 >> sh) & 255) + w2 * ((c2 >> sh) & 255)
                cs.append(np.clip(ch, 0, 255).astype(np.uint32) << sh)
            col = (cs[0] | cs[1] | cs[2] | cs[3])[inside]
        else:
            col = np.uint32(c2)
        self._fill(xs[inside], ys[inside], col)

    # -- packet parsing --------------------------------------------------
    def run_gif(self, data, o, end):
        """Execute GIF packets starting at o until EOP or end. Returns new offset."""
        while o + 16 <= end:
            lo, hi = struct.unpack_from('<QQ', data, o)
            o += 16
            nloop = lo & 0x7fff
            eop = (lo >> 15) & 1
            pre = (lo >> 46) & 1
            flg = (lo >> 58) & 3
            nreg = ((lo >> 60) & 0xf) or 16
            regs = [(hi >> (4 * k)) & 0xf for k in range(nreg)]
            if pre and flg != 2:
                self.write(PRIM, (lo >> 47) & 0x7ff)
            if flg == 0:
                for _ in range(nloop):
                    for rd in regs:
                        if o + 16 > end:
                            return end
                        a, b = struct.unpack_from('<QQ', data, o)
                        o += 16
                        self.packed(rd, a, b)
            elif flg == 1:
                n = nloop * nreg
                for k in range(n):
                    if o + 8 > end:
                        return end
                    v = struct.unpack_from('<Q', data, o)[0]
                    o += 8
                    rd = regs[k % nreg]
                    if rd != 0xe and rd != 0xf:
                        self.write(rd, v)
                if n & 1:
                    o += 8
            else:
                self.image(data[o:o + nloop * 16])
                o += nloop * 16
            if eop:
                return o
        return o

    def packed(self, rd, a, b):
        if rd == 0xe:
            self.write(b & 0xff, a)
        elif rd == 0x1:  # RGBAQ packed
            r, g = a & 0xff, (a >> 32) & 0xff
            bl, al = b & 0xff, (b >> 32) & 0xff
            self.write(RGBAQ, r | (g << 8) | (bl << 16) | (al << 24))
        elif rd in (0x4, 0x5):  # XYZF2 / XYZ2 packed
            x, y = a & 0xffff, (a >> 32) & 0xffff
            z = b & 0xffffffff
            adc = (b >> 47) & 1
            v = x | (y << 16) | (z << 32)
            self.write(XYZ3 if adc else XYZ2, v)
        elif rd == 0x0:
            self.write(PRIM, a & 0x7ff)
        elif rd == 0xf:
            pass
        else:
            self.write(rd, a)

    def run_chain(self, data, o=0, end=None):
        """Walk a DMA chain of CNT/END tags with VIF DIRECT/DIRECTHL transfers."""
        end = len(data) if end is None else end
        while o + 16 <= end:
            tag = struct.unpack_from('<Q', data, o)[0]
            qwc = tag & 0xffff
            tid = (tag >> 28) & 7
            vif0, vif1 = struct.unpack_from('<II', data, o + 8)
            body = o + 16
            bend = min(body + qwc * 16, end)
            # DIRECT/DIRECTHL via VIF1, or a plain GIF-channel (PATH3) chain with no VIF codes
            if (vif1 >> 24) & 0x7f in (0x50, 0x51) or (vif0 == 0 and vif1 == 0):
                p = body
                while p < bend:
                    p = self.run_gif(data, p, bend)
            o = bend
            if tid in (0, 7):  # REFE / END
                break
        return o
