"""Tiny z-buffered, textured software renderer for checking exported OBJ files.

usage: render_obj.py file.obj out.png eye_x eye_y eye_z look_x look_y look_z [w h fov]
       render_obj.py file.obj out.png top          (orthographic top-down view)
"""
import os
import sys
import numpy as np
from PIL import Image


def load(path):
    V, VT, F, M = [], [], [], []
    mtl, cur = {}, None
    only = os.environ.get('ONLY')  # comma-separated object names to keep
    keep = True
    base = os.path.dirname(path)
    for line in open(path):
        if line.startswith('v '):
            p = line.split()
            V.append([float(x) for x in p[1:4]])
        elif line.startswith('vt '):
            VT.append([float(x) for x in line.split()[1:3]])
        elif line.startswith('o '):
            keep = not only or line.split()[1] in only.split(',')
        elif line.startswith('f ') and keep:
            idx = [w.split('/') for w in line.split()[1:4]]
            F.append([int(a[0]) - 1 for a in idx] + [int(a[1]) - 1 for a in idx])
            M.append(cur)
        elif line.startswith('usemtl'):
            cur = line.split()[1]
        elif line.startswith('mtllib'):
            name = None
            for l2 in open(os.path.join(base, line.split()[1])):
                if l2.startswith('newmtl'):
                    name = l2.split()[1]
                elif l2.startswith('map_Kd'):
                    img = Image.open(os.path.join(base, l2.split()[1])).convert('RGBA')
                    mtl[name] = np.asarray(img)
    return np.array(V), np.array(VT), np.array(F), M, mtl


def render(path, out, cam, size=(960, 540), fov=70):
    V, VT, F, M, mtl = load(path)
    W, H = size
    if cam == 'top':
        mn, mx = V.min(0), V.max(0)
        s = min(W / (mx[0] - mn[0]), H / (mx[2] - mn[2]))
        sx = (V[:, 0] - mn[0]) * s
        sy = (V[:, 2] - mn[2]) * s
        depth = -V[:, 1]
        valid = np.ones(len(V), bool)
    else:
        eye, look = np.array(cam[:3]), np.array(cam[3:6])
        f = look - eye
        f /= np.linalg.norm(f)
        r = np.cross(f, [0, 1, 0])
        r /= np.linalg.norm(r)
        u = np.cross(r, f)
        rel = V - eye
        x, y, z = rel @ r, rel @ u, rel @ f
        k = (W / 2) / np.tan(np.radians(fov / 2))
        valid = z > 0.5
        zz = np.where(valid, z, 1)
        sx = W / 2 + x / zz * k
        sy = H / 2 - y / zz * k
        depth = z
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (90, 120, 160)
    zb = np.full((H, W), np.inf)
    for fi, m in zip(F, M):
        a, b, c = fi[:3]
        if not (valid[a] and valid[b] and valid[c]):
            continue
        xs = sx[[a, b, c]]
        ys = sy[[a, b, c]]
        x0, x1 = int(max(np.floor(xs.min()), 0)), int(min(np.ceil(xs.max()), W - 1))
        y0, y1 = int(max(np.floor(ys.min()), 0)), int(min(np.ceil(ys.max()), H - 1))
        if x1 < x0 or y1 < y0 or (x1 - x0) * (y1 - y0) > W * H:
            continue
        py, px = np.mgrid[y0:y1 + 1, x0:x1 + 1] + 0.5
        d = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
        if abs(d) < 1e-9:
            continue
        w1 = ((px - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (py - ys[0])) / d
        w2 = ((xs[1] - xs[0]) * (py - ys[0]) - (px - xs[0]) * (ys[1] - ys[0])) / d
        w0 = 1 - w1 - w2
        ins = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not ins.any():
            continue
        dz = w0 * depth[a] + w1 * depth[b] + w2 * depth[c]
        yy, xx = (py[ins] - 0.5).astype(int), (px[ins] - 0.5).astype(int)
        dz = dz[ins]
        closer = dz < zb[yy, xx]
        if not closer.any():
            continue
        yy, xx, dz = yy[closer], xx[closer], dz[closer]
        tex = mtl.get(m)
        if tex is not None:
            ta, tb, tc = fi[3:]
            uu = (w0 * VT[ta, 0] + w1 * VT[tb, 0] + w2 * VT[tc, 0])[ins][closer]
            vv = (w0 * VT[ta, 1] + w1 * VT[tb, 1] + w2 * VT[tc, 1])[ins][closer]
            th, tw = tex.shape[:2]
            tx = (np.mod(uu, 1) * tw).astype(int) % tw
            ty = (np.mod(1 - vv, 1) * th).astype(int) % th
            col = tex[ty, tx]
            keep = col[:, 3] > 40
            yy, xx, dz, col = yy[keep], xx[keep], dz[keep], col[keep]
            img[yy, xx] = col[:, :3]
        else:
            img[yy, xx] = (200, 200, 200)
        zb[yy, xx] = dz
    Image.fromarray(img).save(out)


if __name__ == '__main__':
    a = sys.argv
    if a[3] == 'top':
        render(a[1], a[2], 'top', size=(1400, 1400))
    else:
        cam = [float(x) for x in a[3:9]]
        size = (int(a[9]), int(a[10])) if len(a) > 10 else (960, 540)
        render(a[1], a[2], cam, size, float(a[11]) if len(a) > 11 else 70)
