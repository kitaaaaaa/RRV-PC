"""Extract ISO9660 filesystem directly from a MODE2/2352 BIN image."""
import os, struct, sys

SECT, HDR, DATA = 2352, 24, 2048  # MODE2 Form1: 16 sync/header + 8 subheader

def read_sectors(f, lba, count):
    out = bytearray()
    for i in range(count):
        f.seek((lba + i) * SECT + HDR)
        out += f.read(DATA)
    return bytes(out)

def walk(f, lba, size, path, outdir, listing):
    data = read_sectors(f, lba, (size + DATA - 1) // DATA)
    pos = 0
    while pos < len(data):
        rlen = data[pos]
        if rlen == 0:
            pos = (pos // DATA + 1) * DATA  # skip to next sector
            continue
        rec = data[pos:pos + rlen]
        elba = struct.unpack_from('<I', rec, 2)[0]
        esize = struct.unpack_from('<I', rec, 10)[0]
        flags = rec[25]
        nlen = rec[32]
        name = rec[33:33 + nlen]
        pos += rlen
        if name in (b'\x00', b'\x01'):
            continue
        name = name.decode('ascii', 'replace').split(';')[0]
        rel = os.path.join(path, name)
        if flags & 2:
            os.makedirs(os.path.join(outdir, rel), exist_ok=True)
            walk(f, elba, esize, rel, outdir, listing)
        else:
            listing.append((rel, elba, esize))
            with open(os.path.join(outdir, rel), 'wb') as o:
                left, cur = esize, elba
                while left > 0:
                    chunk = read_sectors(f, cur, min(512, (left + DATA - 1) // DATA))
                    o.write(chunk[:left])
                    left -= len(chunk)
                    cur += 512

def main(binpath, outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(binpath, 'rb') as f:
        pvd = read_sectors(f, 16, 1)
        assert pvd[1:6] == b'CD001', 'no ISO9660 PVD'
        root = pvd[156:156 + 34]
        rlba = struct.unpack_from('<I', root, 2)[0]
        rsize = struct.unpack_from('<I', root, 10)[0]
        listing = []
        walk(f, rlba, rsize, '', outdir, listing)
    for rel, lba, size in listing:
        print(f'{lba:8d} {size:12d}  {rel}')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
