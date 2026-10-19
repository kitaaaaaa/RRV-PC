"""VIF1 stream parser for Ridge Racer V geometry packets.

Yields per-command events so callers can rebuild meshes from UNPACK data."""
import struct

# vn/vl -> (components, bits)
UNPACK_FMT = {0x0: (1, 32), 0x1: (1, 16), 0x2: (1, 8), 0x4: (2, 32), 0x5: (2, 16), 0x6: (2, 8),
              0x8: (3, 32), 0x9: (3, 16), 0xa: (3, 8), 0xc: (4, 32), 0xd: (4, 16), 0xe: (4, 8),
              0xf: (4, 5)}


def parse(data, o, end):
    """Yield dicts: {'cmd', 'ofs', ...}. UNPACK events carry 'fmt', 'num', 'addr', 'usn',
    'flg', 'values' (list of tuples)."""
    while o + 4 <= end:
        code = struct.unpack_from('<I', data, o)[0]
        cmd = (code >> 24) & 0x7f
        imm = code & 0xffff
        num = (code >> 16) & 0xff
        start = o
        o += 4
        if cmd & 0x60 == 0x60:  # UNPACK
            fmt = cmd & 0xf
            comps, bits = UNPACK_FMT.get(fmt, (0, 0))
            n = num or 256
            if bits == 5:
                size = n * 2
            else:
                size = n * comps * bits // 8
            size = (size + 3) & ~3
            raw = data[o:o + size]
            usn = (imm >> 14) & 1
            vals = []
            if bits == 32:
                vals = [struct.unpack_from('<%d%s' % (comps, 'f'), raw, k * comps * 4) for k in range(n)]
                ivals = [struct.unpack_from('<%dI' % comps, raw, k * comps * 4) for k in range(n)]
            elif bits == 16:
                ch = 'H' if usn else 'h'
                vals = [struct.unpack_from('<%d%s' % (comps, ch), raw, k * comps * 2) for k in range(n)]
                ivals = vals
            elif bits == 8:
                ch = 'B' if usn else 'b'
                vals = [struct.unpack_from('<%d%s' % (comps, ch), raw, k * comps) for k in range(n)]
                ivals = vals
            else:
                ivals = vals = [struct.unpack_from('<H', raw, k * 2) for k in range(n)]
            yield dict(cmd='unpack', ofs=start, fmt=fmt, num=n, addr=imm & 0x3ff, usn=usn,
                       flg=(imm >> 15) & 1, values=vals, ivalues=ivals, raw=raw)
            o += size
        elif cmd in (0x50, 0x51):  # DIRECT / DIRECTHL
            q = (imm or 65536) * 16
            yield dict(cmd='direct', ofs=start, data_ofs=o, size=q)
            o += q
        elif cmd == 0x20:  # STMASK
            o += 4
            yield dict(cmd='stmask', ofs=start)
        elif cmd == 0x30 or cmd == 0x31:  # STROW / STCOL
            o += 16
            yield dict(cmd='strow' if cmd == 0x30 else 'stcol', ofs=start)
        elif cmd == 0x4a:  # MPG
            o += (num or 256) * 8
            yield dict(cmd='mpg', ofs=start)
        else:
            yield dict(cmd={0x00: 'nop', 0x01: 'stcycl', 0x02: 'offset', 0x03: 'base', 0x04: 'itop',
                            0x05: 'stmod', 0x06: 'mskpath3', 0x07: 'mark', 0x10: 'flushe',
                            0x11: 'flush', 0x13: 'flusha', 0x14: 'mscal', 0x15: 'mscalf',
                            0x17: 'mscnt'}.get(cmd, 'unk%02x' % cmd), ofs=start, imm=imm, num=num)
