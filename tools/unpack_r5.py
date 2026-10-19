"""Write every R5.ALL entry (decompressed where needed) to <out>/NNNN.bin plus index.csv."""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r5lz

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
rows = list(csv.DictReader(open(os.path.join(raw, 'index.csv'))))
with open(os.path.join(out, 'index.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['index', 'sector', 'size', 'was_compressed', 'magic_hex', 'magic_ascii'])
    for r in rows:
        i = int(r['index'])
        data = open(os.path.join(raw, '%04d.bin' % i), 'rb').read()
        if r['compressed'] == '1':
            data = r5lz.decompress(data)[0]
        open(os.path.join(out, '%04d.bin' % i), 'wb').write(data)
        m = data[:8]
        w.writerow([i, r['sector'], len(data), r['compressed'], m.hex(), ''.join(chr(c) if 32 <= c < 127 else '.' for c in m)])
