"""Ridge Racer V track geometry extraction (R5.ALL entries 41..60).

Track file = pack[ geometry, ?, texture DMA chain ].
Geometry section: u16, s16, u32 count, u32 dma_list_offset, ...; the DMA list is a run of
REF tags pointing at VIF1 streams.  Each MSCAL runs a VU1 program on the data unpacked
since the previous one.  Per program (MSCAL address) the VU memory layout is:

  prog 18: GIFtags@0, pos V3-16@0x0c, uv V2-16@0x52, rgb V3-8@0x98
  prog 14: GIFtags@0, pos V3-16@0x08, uv V2-16@0x25, rgb V3-8@0x42
  prog 12: GIFtags@0, pos V3-16@0x13, uv V2-16@0x5b, per-strip V1-16@0xa3
  prog 24: billboard: GIFtag@0, quad V3-16@1, quad uv V2-16@5, instance pos V3-16@9,
           rgb V3-8@0x24 and @0x3f
Shared: base xyz V3-32@0x39b (float), TEX0 as A+D qword V4-32@0x398.
"""
import struct
import vif
import gsscan

PROGS = {18: dict(pos=0x0c, uv=0x52, col=0x98),
         14: dict(pos=0x08, uv=0x25, col=0x42),
         12: dict(pos=0x13, uv=0x5b, col=None)}


def dma_refs(sec, list_ofs):
    refs = []
    o = list_ofs
    while o + 16 <= len(sec):
        t = struct.unpack_from('<Q', sec, o)[0]
        tid, qwc, addr = (t >> 28) & 7, t & 0xffff, (t >> 32) & 0x7fffffff
        if tid != 3:
            break
        refs.append((addr, qwc))
        o += 16
    return refs


def raw_batches(sec, start, end):
    """Yield dict(prog, base, tex0, unpacks={addr: event}, tags=[(lo,hi)]) per MSCAL."""
    base = (0.0, 0.0, 0.0)
    tex0 = None
    ups = {}
    for e in vif.parse(sec, start, end):
        c = e['cmd']
        if c == 'unpack':
            a, f = e['addr'], e['fmt']
            if f == 0x8 and e['num'] == 1 and a == 0x39b:
                base = e['values'][0]
            elif f == 0xc and e['num'] == 1 and a == 0x398:
                lo, hi = struct.unpack('<QQ', e['raw'][:16])
                if (hi & 0xff) in (0x06, 0x07):
                    tex0 = gsscan.decode_tex0(lo)
                    tex0['raw'] = lo
            else:
                ups[a] = e
        elif c in ('mscal', 'mscnt'):
            tags = []
            if 0 in ups and ups[0]['fmt'] == 0xc:
                r = ups[0]['raw']
                tags = [struct.unpack('<QQ', r[k * 16:k * 16 + 16]) for k in range(ups[0]['num'])]
            yield dict(prog=e['imm'], base=base, tex0=tex0, unpacks=ups, tags=tags)
            ups = {}
