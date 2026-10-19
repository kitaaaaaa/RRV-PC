import csv, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r5lz
d = sys.argv[1]
bad = 0
for r in csv.DictReader(open(os.path.join(d, 'index.csv'))):
    if r['compressed'] != '1': continue
    data = open(os.path.join(d, '%04d.bin' % int(r['index'])), 'rb').read()
    try:
        out, p, usize, csize = r5lz.decompress(data)
        ok = len(out) == usize == int(r['unpacked_size']) and p == csize
    except Exception as e:
        ok = False; p = str(e); out = b''
    if not ok:
        bad += 1; print('FAIL', r['index'], len(out), r['unpacked_size'], p, len(data) - 10)
print('failures:', bad)
