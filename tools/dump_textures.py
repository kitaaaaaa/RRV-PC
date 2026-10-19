"""Dump every GS image upload found in R5.ALL entries as PNG.

Indexed (PSMT4/PSMT8) uploads are paired with the nearest following CLUT upload
(PSMCT32 8x2 for 4-bit, 16x16 for 8-bit; falls back to the preceding one, then greyscale).
Uploads that are only used as CLUTs are not written separately.

usage: dump_textures.py <unpacked_dir> <out_dir> [first_index last_index]
"""
import csv
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsscan

TRACKS = set(range(41, 61))


def linear_pixels(e):
    """Decode a transfer's host-order pixel data into an HxW array (indices or RGBA)."""
    w, h, psm, raw = e['w'], e['h'], e['psm'], e['pixels']
    n = w * h
    if psm == 0x14:
        b = np.frombuffer(raw[:(n + 1) // 2].ljust((n + 1) // 2, b'\0'), np.uint8)
        px = np.empty(b.size * 2, np.uint8)
        px[0::2], px[1::2] = b & 15, b >> 4
        return px[:n].reshape(h, w)
    if psm == 0x13:
        return np.frombuffer(raw[:n].ljust(n, b'\0'), np.uint8).reshape(h, w)
    if psm in (0x00, 0x01):
        bpp = 4 if psm == 0 else 3
        a = np.frombuffer(raw[:n * bpp].ljust(n * bpp, b'\0'), np.uint8).reshape(h, w, bpp)
        out = np.full((h, w, 4), 255, np.uint8)
        out[..., :3] = a[..., :3]
        if bpp == 4:
            out[..., 3] = np.minimum(a[..., 3].astype(int) * 2, 255)
        return out
    if psm in (0x02, 0x0a):
        v = np.frombuffer(raw[:n * 2].ljust(n * 2, b'\0'), np.uint16).reshape(h, w).astype(np.uint32)
        out = np.empty((h, w, 4), np.uint8)
        out[..., 0] = (v & 31) << 3
        out[..., 1] = ((v >> 5) & 31) << 3
        out[..., 2] = ((v >> 10) & 31) << 3
        out[..., 3] = np.where(v & 0x8000, 255, 0)
        return out
    return None


def clut_from(e, entries):
    rgba = linear_pixels(e).reshape(-1, 4)
    if entries == 256:
        i = np.arange(256)
        return rgba[(i & 0xe7) | ((i & 8) << 1) | ((i & 16) >> 1)]
    return rgba[:16]


def dump_entry(data, prefix, out_dir):
    xs = [e for k, e in gsscan.scan(data) if k == 'xfer' and e['w'] and e['h']]
    clut_ids = set()
    written = 0
    for n, e in enumerate(xs):
        if e['psm'] not in (0x13, 0x14):
            continue
        want = (8, 2) if e['psm'] == 0x14 else (16, 16)
        cands = [j for j in range(n + 1, len(xs)) if xs[j]['psm'] == 0 and (xs[j]['w'], xs[j]['h']) == want]
        cands = cands[:1] or [j for j in range(n - 1, -1, -1)
                              if xs[j]['psm'] == 0 and (xs[j]['w'], xs[j]['h']) == want][:1]
        idx = linear_pixels(e)
        if cands:
            clut_ids.add(cands[0])
            pal = clut_from(xs[cands[0]], 16 if e['psm'] == 0x14 else 256)
            img = pal[idx]
        else:
            g = (idx.astype(np.uint16) * (17 if e['psm'] == 0x14 else 1)).astype(np.uint8)
            img = np.dstack([g, g, g, np.full_like(g, 255)])
        Image.fromarray(img).save(os.path.join(out_dir, '%s_%03d.png' % (prefix, n)))
        written += 1
    for n, e in enumerate(xs):
        if n in clut_ids or e['psm'] in (0x13, 0x14):
            continue
        img = linear_pixels(e)
        if img is None:
            continue
        if e['psm'] == 0 and (e['w'], e['h']) in ((8, 2), (16, 16), (16, 2)):
            continue  # unpaired CLUT
        Image.fromarray(img).save(os.path.join(out_dir, '%s_%03d.png' % (prefix, n)))
        written += 1
    return written


def main(unpacked, out_dir, first=0, last=10 ** 6):
    labels = {}
    idx_path = os.path.join(unpacked, 'index.csv')
    for r in csv.DictReader(open(idx_path, encoding='utf-8')):
        labels[int(r['index'])] = r.get('label_description', '') or r.get('label_type', '')
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for i in sorted(labels):
        if i < first or i > last or i in TRACKS:
            continue
        data = open(os.path.join(unpacked, '%04d.bin' % i), 'rb').read()
        tag = ''.join(c if c.isalnum() else '_' for c in labels[i])[:40].strip('_')
        prefix = '%04d%s' % (i, ('_' + tag) if tag else '')
        n = dump_entry(data, prefix, out_dir)
        total += n
    print('wrote', total, 'images')


if __name__ == '__main__':
    a = sys.argv
    main(a[1], a[2], *(int(x) for x in a[3:5]))
