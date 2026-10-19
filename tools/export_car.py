"""Export Ridge Racer V car models (R5.ALL '3DModel' entries 269, 279, ... 439) to OBJ/MTL/PNG.

Each model entry M is followed by texture entries:
  M+1+v (v = 0..2 = livery variants): pack of 12 GIF chains.  Each chain redraws the shared
        512x512 PSMT8 page at TBP 6784 (sprites + patch uploads, some also draw CLUTs); the game
        redraws the page between groups of strips.  Observed layout: 4 LODs x 3 groups
        (chain 4+L = body of LOD L, 8+L = glass, L = wheels/misc).
  M+4+v: raw patch uploads (lights etc.) applied on top of every chain.
Every strip is assigned the chain whose drawn area best covers its UVs (biased towards the
chain that best covers its whole part).  One texture is written per (chain, CLUT) pair, which
is exactly how the GS samples it, so no texels are shared between materials.
CLUT entries the game fills at runtime with the chosen body colour (the leading run of empty
entries) get a neutral grey ramp so the paint can be tinted in AC.

Output (<out>/<name>/): <name>.obj/.mtl using textures/ (= variant 0),
skins/v0..v2/ with the same file names for each livery variant.
Objects: part00 = body LOD0, part01 = glass LOD0, part02/03 = LOD1 body/glass, part04 = LOD2,
part05 = LOD3, part06-13 = wheel disc quads (at origin, not yet placed), last = untextured.
Units: metres (1 raw unit = 1/256 m).  Axes: rotated 180 deg about X to Y-up (not mirrored).

usage: export_car.py <unpacked_dir> <model_index> <out_dir> [name]
"""
import os
import shutil
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gif
import gsscan
import pack
import r5car

SCALE = 1.0 / 256.0
UV = 1.0 / 4096.0
TBP, TBW = 6784, 8
PART_BIAS = 0.15
# part -> chain: body LOD L uses chain 4+L, glass LOD0/1 uses 10/11, wheel discs use 0/1
FIXED_CHAIN = {0: 4, 1: 10, 2: 5, 3: 11, 4: 6, 5: 7, 6: 0, 7: 0, 8: 0, 9: 0, 10: 1, 11: 1, 12: 1, 13: 1}


def xf(p):
    return (p[0] * SCALE, -p[1] * SCALE, -p[2] * SCALE)


def load(unpacked, i):
    return open(os.path.join(unpacked, '%04d.bin' % i), 'rb').read()


def uv_mask(strips):
    im = Image.new('1', (512, 512))
    dr = ImageDraw.Draw(im)
    for s in strips:
        uv = [(v['uv'][0] / 8.0, v['uv'][1] / 8.0) for v in s['verts']]
        for i in range(len(uv) - 2):
            dr.polygon(uv[i:i + 3], fill=1)
    return np.asarray(im)


def paint_fix(pal):
    pal = pal.copy()
    nz = np.nonzero(pal[:, :3].any(1))[0]
    n0 = int(nz[0]) if len(nz) else len(pal)
    if n0 >= 16:
        ramp = np.linspace(230, 90, n0).astype(np.uint8)
        pal[:n0, 0] = pal[:n0, 1] = pal[:n0, 2] = ramp
        pal[:n0, 3] = 255
    return pal


def run_section(gs, data, sec):
    """Execute one pack section: a DMA chain if it starts with a DMA tag, else raw transfers."""
    o, s = sec
    t = int.from_bytes(data[o:o + 8], 'little')
    if (t >> 28) & 7 in (1, 2, 3, 5, 6, 7) and (t & 0xffff) > 0:
        gs.run_chain(data, o, o + s)
    else:
        for k, e in gsscan.scan(data[o:o + s]):
            if k == 'xfer':
                gs.mem.write(e['dbp'], e['dbw'], e['psm'], e['w'], e['h'], e['dx'], e['dy'], e['pixels'])


# Draw-mode tables read from SLUS_200.02 (gp = 0x33a670), indexed by draw mode s7:
BODY_PART = [0, 0, 2, 4, 5]     # gp-0x6ad8: geometry entry for the body
GLASS_PART = [1, 1, 3]          # gp-0x6ac8: geometry entry drawn with texture group 2
TEX_L = [0, 0, 1, 2, 3]         # gp-0x6ad0: group-1 index
TEX_L2 = [0, 0, 1]              # gp-0x6ac0: group-2 index
DISC_PARTS = [6, 6, 10]         # gp-0x6ab0: first of 4 wheel-disc geometry entries


def verified_states(unpacked, model, a1, a2, mode):
    """Texture states as queued by the car draw routine at 0x227298:
    third set section [mode] (0x227a48), then group 1: chain[4+L], patch[L], chain[L]
    (0x227f90..0x2280b0); group 2 on top: chain[10+L2], patch[4+L2], chain[8+L2]
    (0x228140..0x228228)."""
    C = load(unpacked, model + 1 + a1)
    Pf = load(unpacked, model + 4 + a1)
    T = load(unpacked, model + 7 + a2)
    CS, PS, TS = pack.sections(C), pack.sections(Pf), pack.sections(T)
    g1 = gif.GS()
    if mode < len(TS):
        run_section(g1, T, TS[mode])
    L = TEX_L[mode]
    for data, secs, k in ((C, CS, 4 + L), (Pf, PS, L), (C, CS, L)):
        run_section(g1, data, secs[k])
    g2 = None
    if mode < len(TEX_L2):
        g2 = gif.GS()
        g2.mem.mem[:] = g1.mem.mem
        L2 = TEX_L2[mode]
        for data, secs, k in ((C, CS, 10 + L2), (Pf, PS, 4 + L2), (C, CS, 8 + L2)):
            if k < len(secs):
                run_section(g2, data, secs[k])
    return g1, g2


def chain_states(unpacked, model, v):
    """Return list of GS states (one per chain) for livery variant v."""
    chains = load(unpacked, model + 1 + v)
    patches = [e for k, e in gsscan.scan(load(unpacked, model + 4 + v)) if k == 'xfer']
    states = []
    for o, s in pack.sections(chains) or []:
        gs = gif.GS()
        gs.run_chain(chains, o, o + s)
        for e in patches:
            gs.mem.write(e['dbp'], e['dbw'], e['psm'], e['w'], e['h'], e['dx'], e['dy'], e['pixels'])
        states.append(gs)
    return states


def export(unpacked, model, out_dir, name=None):
    name = name or 'car_%04d' % model
    root = os.path.join(out_dir, name)
    os.makedirs(root, exist_ok=True)
    d = load(unpacked, model)
    o0, sz = pack.sections(d)[1]
    sec = d[o0:o0 + sz]
    parts = [r5car.parse_part(sec, a, b)[0] for a, b in r5car.parts(sec)]

    # chain assignment using variant 0 drawn-area masks
    states0 = chain_states(unpacked, model, 0)
    drawn = [gs.mem.read_index(TBP, TBW, 0x13, 512, 512) != 0 for gs in states0]

    def best_chain(mask, bias_k=None):
        area = mask.sum()
        if not area:
            return bias_k if bias_k is not None else 0
        sc = [(m & mask).sum() / area + (PART_BIAS if k == bias_k else 0) for k, m in enumerate(drawn)]
        return int(np.argmax(sc))

    assign = {}
    for pi, strips in enumerate(parts):
        tex = [s for s in strips if s['kind'] == 'tex' and s['tex0']]
        if not tex:
            continue
        pk = FIXED_CHAIN.get(pi)
        if pk is None or pk >= len(drawn):
            pk = best_chain(uv_mask(tex))
        for si, s in enumerate(strips):
            if s['kind'] == 'tex' and s['tex0']:
                assign[(pi, si)] = pk

    mats = sorted({(assign[(pi, si)], s['tex0']['cbp'])
                   for pi, strips in enumerate(parts) for si, s in enumerate(strips) if (pi, si) in assign})
    fname = {m: 'k%02d_c%d.png' % m for m in mats}

    for v in range(3):
        states = states0 if v == 0 else chain_states(unpacked, model, v)
        vdir = os.path.join(root, 'skins', 'v%d' % v)
        os.makedirs(vdir, exist_ok=True)
        for k, c in mats:
            if k >= len(states):
                continue
            gs = states[k]
            idx = gs.mem.read_index(TBP, TBW, 0x13, 512, 512)
            img = paint_fix(gs.mem.read_clut(c, 0, 0x13))[idx]
            Image.fromarray(img).save(os.path.join(vdir, fname[(k, c)]))
    tdir = os.path.join(root, 'textures')
    if os.path.isdir(tdir):
        shutil.rmtree(tdir)
    shutil.copytree(os.path.join(root, 'skins', 'v0'), tdir)
    for stale in ('skin.png',):
        if os.path.exists(os.path.join(root, stale)):
            os.remove(os.path.join(root, stale))

    colors = sorted({tuple(s['verts'][0]['rgba']) for p in parts for s in p if s['kind'] == 'color'})
    with open(os.path.join(root, name + '.mtl'), 'w') as m:
        for k, c in mats:
            m.write('newmtl k%02d_c%d\nKd 1 1 1\nmap_Kd textures/%s\nmap_d textures/%s\n\n'
                    % (k, c, fname[(k, c)], fname[(k, c)]))
        for col in colors:
            m.write('newmtl shade_%02x%02x%02x%02x\nKd %.3f %.3f %.3f\nd %.3f\n\n'
                    % (col + tuple(x / 128 for x in col[:3]) + (min(col[3] / 128, 1),)))

    with open(os.path.join(root, name + '.obj'), 'w') as f:
        f.write('# Ridge Racer V R5.ALL entry %d\nmtllib %s.mtl\n' % (model, name))
        vi = 1
        for pi, strips in enumerate(parts):
            if not strips:
                continue
            f.write('o part%02d\n' % pi)
            groups = defaultdict(list)
            for si, s in enumerate(strips):
                if (pi, si) in assign:
                    key = 'k%02d_c%d' % (assign[(pi, si)], s['tex0']['cbp'])
                elif s['kind'] == 'color':
                    key = 'shade_%02x%02x%02x%02x' % tuple(s['verts'][0]['rgba'])
                else:
                    continue
                groups[key].append(s)
            for key, ss in groups.items():
                f.write('usemtl %s\n' % key)
                for s in ss:
                    vs = s['verts']
                    base = vi
                    for v in vs:
                        x, y, z = xf(v['pos'])
                        f.write('v %.5f %.5f %.5f\n' % (x, y, z))
                        u = v['uv'] or (0, 0)
                        f.write('vt %.6f %.6f\n' % (u[0] * UV, 1 - u[1] * UV))
                        nx, ny, nz = v['nrm'] or (0.0, -1.0, 0.0)
                        f.write('vn %.4f %.4f %.4f\n' % (nx, -ny, -nz))
                    for i in range(len(vs) - 2):
                        a, b, c = base + i, base + i + 1, base + i + 2
                        if i % 2:
                            b, c = c, b
                        f.write('f %d/%d/%d %d/%d/%d %d/%d/%d\n' % (a, a, a, b, b, b, c, c, c))
                    vi += len(vs)
    used = sorted({k for k, c in mats})
    print('%s: %d parts, %d materials, chains used %s' % (name, sum(1 for p in parts if p), len(mats), used))


if __name__ == '__main__':
    a = sys.argv
    export(a[1], int(a[2]), a[3], a[4] if len(a) > 4 else None)
