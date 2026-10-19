"""Ridge Racer V car / object model parser (e.g. R5.ALL 269, 279, ...: '3DModel').

Model section (pack section 1): u32 count, u32 header_size(0x80), then count x (u32 flags|n, u32 offset)
part entries; part data runs from 0x80 / each offset to the next.  Part data is a list of
GIF packets (PACKED):
  A+D tag (regs 0xE):           register writes, TEX0_2 selects texture page/CLUT
  strip tag regs ST,RGBAQ,XYZF2: nloop x 16-byte vertex  s16 pos[3], s16 normal[3] (4.12), u16 uv[2]
                                 or 10-byte vertex s16 pos[3], u16 uv[2] (unlit, e.g. lights/glass)
                                 (size chosen by checking the following packet; padded to 16)
  strip tag regs RGBAQ,XYZF2:    nloop x 10-byte vertex  s16 pos[3], u8 rgba[4]  (padded to 16)
"""
import struct
import gsscan


def parts(sec):
    n = struct.unpack_from('<I', sec, 0)[0]
    offs = sorted({0x80} | {struct.unpack_from('<II', sec, 8 + 8 * k)[1] for k in range(n)} - {0})
    return [(a, b) for a, b in zip(offs, offs[1:] + [len(sec)])]


def _valid_next(sec, o, end):
    if o >= end or o + 16 > len(sec):
        return True
    lo, hi = struct.unpack_from('<QQ', sec, o)
    if lo == 0 and hi == 0:
        return True
    nl, nr, flg = lo & 0x7fff, ((lo >> 60) & 0xf) or 16, (lo >> 58) & 3
    regs = hi & ((1 << (4 * nr)) - 1)
    return flg == 0 and nl > 0 and (regs == 0xe or (nr == 3 and regs == 0x412) or (nr == 2 and regs == 0x41))


def parse_part(sec, o, end):
    """Return (strips, unknown_count); strip = dict(tex0, prim, kind, verts)."""
    strips, tex, unk, extra = [], None, 0, None
    while o + 16 <= end:
        lo, hi = struct.unpack_from('<QQ', sec, o)
        nl = lo & 0x7fff
        nr = ((lo >> 60) & 0xf) or 16
        flg = (lo >> 58) & 3
        regs = hi & ((1 << (4 * nr)) - 1)
        prim = (lo >> 47) & 0x7ff
        if flg == 0 and regs == 0xe and nl:
            for k in range(nl):
                v, a = struct.unpack_from('<QQ', sec, o + 16 + 16 * k)
                if (a & 0xff) in (6, 7):
                    tex = gsscan.decode_tex0(v)
            extra = hi >> 48  # game-specific tag in the unused top bits of the A+D GIFtag
            o += 16 + 16 * nl
        elif flg == 0 and nr == 3 and regs == 0x412 and nl:
            size16 = 16 + 16 * nl
            size10 = 16 + ((10 * nl + 15) & ~15)
            vs = []
            # the 16-byte layout carries unit normals (4.12); use that to tell the layouts apart
            nrm_ok = all(abs(sum(x * x for x in struct.unpack_from('<3h', sec, o + 22 + 16 * k)) - 4096 ** 2)
                         < 0.06 * 4096 ** 2 for k in range(nl)) if o + size16 <= len(sec) else False
            if nrm_ok or not _valid_next(sec, o + size10, end):
                for k in range(nl):
                    p = struct.unpack_from('<3h3h2H', sec, o + 16 + 16 * k)
                    vs.append(dict(pos=p[0:3], nrm=tuple(x / 4096.0 for x in p[3:6]), uv=p[6:8], rgba=None))
                o += size16
            else:
                for k in range(nl):
                    p = struct.unpack_from('<3h2H', sec, o + 16 + 10 * k)
                    vs.append(dict(pos=p[0:3], nrm=None, uv=p[3:5], rgba=None))
                o += size10
            strips.append(dict(tex0=tex, prim=prim, kind='tex', verts=vs, extra=extra))
        elif flg == 0 and nr == 2 and regs == 0x41 and nl:
            vs = []
            for k in range(nl):
                p = struct.unpack_from('<3h4B', sec, o + 16 + 10 * k)
                vs.append(dict(pos=p[0:3], nrm=None, uv=None, rgba=p[3:7]))
            strips.append(dict(tex0=None, prim=prim, kind='color', verts=vs, extra=extra))
            o += 16 + ((10 * nl + 15) & ~15)
        elif lo == 0 and hi == 0:
            break  # end of part
        else:
            unk += 1
            o += 16
    return strips, unk
