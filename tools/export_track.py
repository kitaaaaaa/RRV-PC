"""Export a Ridge Racer V track entry (R5.ALL 41..60) to OBJ/MTL/PNG.

usage: export_track.py <unpacked_dir> <entry_index> <out_dir>

Coordinates: metres (raw / 256), rotated 180 deg about X to Y-up (right-handed, not mirrored).
Object/material names are p<VU program>_matNNN: p14 = road surface, p18 = scenery,
p12 = special strips, p24 = billboards (exported as two crossed quads).
Vertex colours are written as OBJ 'v x y z r g b' (PS2 0x80 = 1.0, clamped to 1).
"""
import os
import struct
import sys
from collections import defaultdict

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gif
import gsscan
import pack
import r5track
import vif

POS = 1.0 / 16.0    # int16 vertex -> base-offset units
METRES = 1.0 / 16.0  # base-offset units -> metres (1 raw int16 unit = 1/256 m)
UV = 1.0 / 4096.0


def collect_batches(sec, list_ofs):
    """Like r5track.raw_batches but across all REF blocks, also tracking A+D regs from DIRECT."""
    refs = sorted(set(r5track.dma_refs(sec, list_ofs)))
    out = []
    for a, q in refs:
        base = (0.0, 0.0, 0.0)
        regs = {}
        ups = {}
        for e in vif.parse(sec, a, a + q * 16):
            c = e['cmd']
            if c == 'unpack':
                if e['fmt'] == 0x8 and e['num'] == 1 and e['addr'] == 0x39b:
                    base = e['values'][0]
                elif e['fmt'] == 0xc and e['num'] == 1 and e['addr'] == 0x398:
                    lo, hi = struct.unpack('<QQ', e['raw'][:16])
                    regs[hi & 0xff] = lo
                else:
                    ups[e['addr']] = e
            elif c == 'direct':
                o = e['data_ofs']
                lo, hi = struct.unpack_from('<QQ', sec, o)
                nl, nr = lo & 0x7fff, ((lo >> 60) & 0xf) or 16
                if all(((hi >> (4 * k)) & 0xf) == 0xe for k in range(nr)):
                    for k in range(nl * nr):
                        if o + 32 + 16 * k > o + e['size']:
                            break
                        v, ad = struct.unpack_from('<QQ', sec, o + 16 + 16 * k)
                        regs[ad & 0xff] = v
            elif c in ('mscal', 'mscnt'):
                out.append(dict(prog=e['imm'], base=base, regs=dict(regs), unpacks=ups))
                ups = {}
    return out


def tags_of(ev):
    r = ev['raw']
    return [struct.unpack('<QQ', r[k * 16:k * 16 + 16]) for k in range(ev['num'])]


def to_world(base, p):
    # PS2 space is right-handed with Y down; rotate 180 deg about X (not a mirror) for Y-up
    x, y, z = (base[0] + p[0] * POS, base[1] + p[1] * POS, base[2] + p[2] * POS)
    return (x * METRES, -y * METRES, -z * METRES)


def strips_from_batch(b):
    """Return (prim, list of strips) where strip = list of (xyz, st, rgb)."""
    L = r5track.PROGS.get(b['prog'])
    u = b['unpacks']
    if L and 0 in u and L['pos'] in u:
        pos = u[L['pos']]['values']
        uv = u[L['uv']]['values'] if L['uv'] in u else [(0, 0)] * len(pos)
        col = u[L['col']]['values'] if L['col'] and L['col'] in u else [(128, 128, 128)] * len(pos)
        strips, k, prim = [], 0, None
        for lo, hi in tags_of(u[0]):
            n = lo & 0x7fff
            if not n:
                continue
            if prim is None and (lo >> 46) & 1:
                prim = (lo >> 47) & 0x7ff
            s = [(to_world(b['base'], pos[j]), (uv[j][0] * UV, uv[j][1] * UV), col[j])
                 for j in range(k, min(k + n, len(pos)))]
            strips.append(s)
            k += n
        return prim, strips
    if b['prog'] == 24 and all(a in u for a in (0, 1, 5, 9)):
        # billboards: 4-vertex template instanced at each position; emit two crossed quads
        quad = u[1]['values']
        quv = u[5]['values']
        cols = u[0x24]['values'] if 0x24 in u else None
        lo = tags_of(u[0])[0][0]
        prim = (lo >> 47) & 0x7ff
        strips = []
        for j, ip in enumerate(u[9]['values']):
            c = cols[j] if cols and j < len(cols) else (128, 128, 128)
            for axis in (0, 1):
                s = []
                for q, t in zip(quad, quv):
                    off = (q[0], q[1], 0) if axis == 0 else (0, q[1], q[0])
                    p = (ip[0] + off[0], ip[1] + off[1], ip[2] + off[2])
                    s.append((to_world(b['base'], p), (t[0] * UV, t[1] * UV), c))
                strips.append(s)
        return prim, strips
    return None, []


def upload_pairs(chain):
    """(image upload, CLUT upload) pairs in chain order; the CLUT is the next PSMCT32 8x2 (4-bit)
    or 16x16 (8-bit) upload after an indexed image."""
    xs = [e for k, e in gsscan.scan(chain) if k == 'xfer' and e['w'] and e['h']]
    pairs = []
    for n, e in enumerate(xs):
        if e['psm'] not in (0x13, 0x14):
            continue
        want = (8, 2) if e['psm'] == 0x14 else (16, 16)
        for j in range(n + 1, min(n + 4, len(xs))):
            if xs[j]['psm'] == 0 and (xs[j]['w'], xs[j]['h']) == want:
                pairs.append((e, xs[j]))
                break
    return pairs


class TexCache:
    """Reads textures from the emulated VRAM.  The track's texture chain reuses VRAM addresses
    (the game streams textures per area), so the final VRAM state is wrong for some materials;
    for each material the upload pair(s) matching its TEX0 base, CLUT address and UV rectangle
    are re-applied on top of the final state before reading."""

    def __init__(self, gs, pairs):
        self.gs = gs
        self.pairs = pairs

    def image(self, t, rect=None):
        w, h = 1 << t['tw'], 1 << t['th']
        mem = self.gs.mem
        if t['psm'] in (0x13, 0x14) and rect is not None:
            x0, y0, x1, y1 = rect
            cands = []
            for img_x, clut_x in self.pairs:
                if (img_x['dbp'], img_x['dbw'], img_x['psm']) != (t['tbp'], t['tbw'], t['psm'])                         or clut_x['dbp'] != t['cbp']:
                    continue
                ox = max(0, min(x1, img_x['dx'] + img_x['w']) - max(x0, img_x['dx']))
                oy = max(0, min(y1, img_x['dy'] + img_x['h']) - max(y0, img_x['dy']))
                if ox * oy > 0:
                    cands.append((ox * oy, img_x, clut_x))
            if cands:
                saved = mem.mem.copy()
                for _, img_x, clut_x in sorted(cands, key=lambda c: c[0]):
                    for e in (img_x, clut_x):
                        mem.write(e['dbp'], e['dbw'], e['psm'], e['w'], e['h'], e['dx'], e['dy'], e['pixels'])
                pal = mem.read_clut(t['cbp'], t['cpsm'], t['psm'], t['csa'])
                img = pal[mem.read_index(t['tbp'], t['tbw'], t['psm'], w, h)]
                mem.mem[:] = saved
                if not t['tcc']:
                    img = img.copy()
                    img[..., 3] = 255
                return img
        if t['psm'] in (0x13, 0x14, 0x1b, 0x24, 0x2c):
            pal = mem.read_clut(t['cbp'], t['cpsm'], t['psm'], t['csa'])
            img = pal[mem.read_index(t['tbp'], t['tbw'], t['psm'], w, h)]
        else:
            img = self.gs.mem.read_rgba(t['tbp'], t['tbw'], t['psm'], w, h)
        if not t['tcc']:
            img = img.copy()
            img[..., 3] = 255
        return img


def export(unpacked, index, out_dir):
    data = open(os.path.join(unpacked, '%04d.bin' % index), 'rb').read()
    secs = pack.sections(data)
    geo = data[secs[0][0]:secs[0][0] + secs[0][1]]
    list_ofs = struct.unpack_from('<I', geo, 8)[0]
    gs = gif.GS()
    o, sz = secs[2]
    gs.run_chain(data, o, o + sz)
    tc = TexCache(gs, upload_pairs(data[o:o + sz]))

    batches = collect_batches(geo, list_ofs)
    # material key -> list of triangles [(v0,v1,v2)]
    mats = defaultdict(list)
    matinfo = {}
    skipped = defaultdict(int)
    for b in batches:
        prim, strips = strips_from_batch(b)
        if not strips:
            skipped[b['prog']] += 1
            continue
        t0 = b['regs'].get(6)
        cl = b['regs'].get(8, 0)
        # PRIM is not always visible to us (the VU program may write it), so treat any
        # batch with a TEX0 as textured unless the GIFtag explicitly clears TME
        tme = t0 is not None and (prim is None or bool((prim >> 4) & 1) or b['prog'] == 24)
        abe = bool(prim and (prim >> 6) & 1)
        key = (b['prog'], t0 if tme else None, cl & 0xf if tme else 0, abe)
        matinfo[key] = (gsscan.decode_tex0(t0) if tme else None, cl, abe)
        for s in strips:
            for i in range(len(s) - 2):
                a, bb, c = s[i], s[i + 1], s[i + 2]
                # standard strip alternation (rotation keeps the original winding)
                tri = (a, bb, c) if i % 2 == 0 else (a, c, bb)
                mats[key].append(tri)

    os.makedirs(os.path.join(out_dir, 'textures'), exist_ok=True)
    name = 'track_%04d' % index
    obj = open(os.path.join(out_dir, name + '.obj'), 'w')
    mtl = open(os.path.join(out_dir, name + '.mtl'), 'w')
    obj.write('# Ridge Racer V R5.ALL entry %d\nmtllib %s.mtl\n' % (index, name))
    vbase = tbase = 1
    for mi, (key, tris) in enumerate(sorted(mats.items(), key=lambda kv: str(kv[0]))):
        t, cl, abe = matinfo[key]
        mname = 'p%02d_mat%03d' % (key[0], mi)
        mtl.write('newmtl %s\nKd 1 1 1\n' % mname)
        u_off = v_off = 0.0
        u_sc = v_sc = 1.0
        if t is not None:
            sts = np.array([p[1] for tri in tris for p in tri])
            tw_, th_ = 1 << t['tw'], 1 << t['th']
            rect = None
            if len(sts):
                rect = (int(np.floor(sts[:, 0].min() * tw_)), int(np.floor(sts[:, 1].min() * th_)),
                        int(np.ceil(sts[:, 0].max() * tw_)) + 1, int(np.ceil(sts[:, 1].max() * th_)) + 1)
            img = tc.image(t, rect)
            w, h = img.shape[1], img.shape[0]
            clamp = (cl & 3) == 1 and ((cl >> 2) & 3) == 1
            if clamp and len(sts):
                # crop to the used region of the (atlas) texture
                x0 = max(int(np.floor(sts[:, 0].min() * w)) - 1, 0)
                x1 = min(int(np.ceil(sts[:, 0].max() * w)) + 1, w)
                y0 = max(int(np.floor(sts[:, 1].min() * h)) - 1, 0)
                y1 = min(int(np.ceil(sts[:, 1].max() * h)) + 1, h)
                if x1 > x0 and y1 > y0:
                    img = img[y0:y1, x0:x1]
                    u_off, v_off = x0 / w, y0 / h
                    u_sc, v_sc = w / (x1 - x0), h / (y1 - y0)
            tname = '%s.png' % mname
            Image.fromarray(img).save(os.path.join(out_dir, 'textures', tname))
            mtl.write('map_Kd textures/%s\n' % tname)
            if (img[..., 3] < 255).any():
                mtl.write('map_d textures/%s\n' % tname)
            mtl.write('# tex0 tbp=%d tbw=%d psm=%#x %dx%d cbp=%d clamp=%#x abe=%d\n'
                      % (t['tbp'], t['tbw'], t['psm'], 1 << t['tw'], 1 << t['th'], t['cbp'], cl, abe))
        mtl.write('\n')
        obj.write('o %s\nusemtl %s\n' % (mname, mname))
        for tri in tris:
            for (x, y, z), (s_, t_), col in tri:
                r, g, bcol = (min(c / 128.0, 1.0) for c in col[:3])
                obj.write('v %.4f %.4f %.4f %.3f %.3f %.3f\n' % (x, y, z, r, g, bcol))
                obj.write('vt %.6f %.6f\n' % ((s_ - u_off) * u_sc, 1.0 - (t_ - v_off) * v_sc))
        for k in range(len(tris)):
            a = vbase + k * 3
            obj.write('f %d/%d %d/%d %d/%d\n' % (a, a, a + 1, a + 1, a + 2, a + 2))
        vbase += len(tris) * 3
    obj.close()
    mtl.close()
    ntri = sum(len(v) for v in mats.values())
    print('entry %d: %d batches, %d materials, %d triangles, skipped programs %s'
          % (index, len(batches), len(mats), ntri, dict(skipped)))
    if gs.unsupported:
        print('  unsupported GS features:', gs.unsupported)


if __name__ == '__main__':
    export(sys.argv[1], int(sys.argv[2]), sys.argv[3])
