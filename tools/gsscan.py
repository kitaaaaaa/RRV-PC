"""Scan files for GS image transfers (A+D BITBLTBUF/TRXPOS/TRXREG/TRXDIR + IMAGE GIFtag)
and for TEX0 register writes made with A+D."""
import struct

PSM_BPP = {0x00: 32, 0x01: 24, 0x02: 16, 0x0a: 16, 0x13: 8, 0x14: 4, 0x1b: 8, 0x24: 4, 0x2c: 4,
           0x30: 32, 0x31: 24, 0x32: 16, 0x3a: 16}

def giftag(q):
    lo, hi = struct.unpack('<QQ', q)
    return {'nloop': lo & 0x7fff, 'eop': (lo >> 15) & 1, 'pre': (lo >> 46) & 1,
            'prim': (lo >> 47) & 0x7ff, 'flg': (lo >> 58) & 3, 'nreg': ((lo >> 60) & 0xf) or 16,
            'regs': hi}

def scan(data):
    """Yield ('xfer', dict) and ('tex0', dict) events in file order."""
    n = len(data) & ~15
    o = 0
    while o + 16 <= n:
        t = giftag(data[o:o + 16])
        nreg = t['nreg']
        allad = all(((t['regs'] >> (4 * k)) & 0xf) == 0xe for k in range(nreg))
        count = t['nloop'] * nreg
        if t['flg'] == 0 and allad and 0 < count <= 64 and o + 16 + count * 16 <= n:
            regs = {}
            ok = True
            for k in range(count):
                val, addr = struct.unpack_from('<QQ', data, o + 16 + k * 16)
                if addr > 0x7f:
                    ok = False
                    break
                regs.setdefault(addr, []).append(val)
            if not ok:
                o += 16
                continue
            end = o + 16 + count * 16
            for key in (0x06, 0x07):
                for val in regs.get(key, []):
                    yield 'tex0', dict(ofs=o, **decode_tex0(val))
            if 0x50 in regs and 0x52 in regs and 0x53 in regs and end + 16 <= n:
                b, p, r = regs[0x50][-1], regs.get(0x51, [0])[-1], regs[0x52][-1]
                it = giftag(data[end:end + 16])
                if it['flg'] == 2:
                    dbp, dbw, dpsm = (b >> 32) & 0x3fff, (b >> 48) & 0x3f, (b >> 56) & 0x3f
                    w, h = r & 0xfff, (r >> 32) & 0xfff
                    dx, dy = (p >> 32) & 0x7ff, (p >> 48) & 0x7ff
                    qwc = it['nloop']
                    pix = data[end + 16:end + 16 + qwc * 16]
                    yield 'xfer', dict(ofs=o, dbp=dbp, dbw=dbw, psm=dpsm, w=w, h=h, dx=dx, dy=dy,
                                       pixels=pix, data_ofs=end + 16)
                    o = end + 16 + qwc * 16
                    continue
            o = end
            continue
        o += 16

def decode_tex0(v):
    return dict(tbp=v & 0x3fff, tbw=(v >> 14) & 0x3f, psm=(v >> 20) & 0x3f,
                tw=(v >> 26) & 0xf, th=(v >> 30) & 0xf, tcc=(v >> 34) & 1, tfx=(v >> 35) & 3,
                cbp=(v >> 37) & 0x3fff, cpsm=(v >> 51) & 0xf, csm=(v >> 55) & 1,
                csa=(v >> 56) & 0x1f, cld=(v >> 61) & 7)
