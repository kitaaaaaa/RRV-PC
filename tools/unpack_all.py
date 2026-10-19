"""Split R5.ALL using the file table embedded in SLUS_200.02.

Table entry (16 bytes, LE): sector, sector_count, stored_size, unpacked_size.
"""
import os, struct, sys

TABLE_OFS = 0x10d258  # file offset inside SLUS_200.02
TABLE_COUNT = 1135    # followed by an end marker entry (sector 232861, all zero)

def read_table(elf):
    entries = []
    i = 0
    while True:
        lba, n, size, usize = struct.unpack_from('<4I', elf, TABLE_OFS + i * 16)
        if i == TABLE_COUNT:
            break
        entries.append((lba, n, size, usize))
        i += 1
    return entries

def main(elfpath, allpath, outdir):
    elf = open(elfpath, 'rb').read()
    entries = read_table(elf)
    os.makedirs(outdir, exist_ok=True)
    with open(allpath, 'rb') as f, open(os.path.join(outdir, 'index.csv'), 'w') as idx:
        idx.write('index,sector,sectors,stored_size,unpacked_size,compressed,magic\n')
        for i, (lba, n, size, usize) in enumerate(entries):
            f.seek(lba * 2048)
            data = f.read(size)
            with open(os.path.join(outdir, f'{i:04d}.bin'), 'wb') as o:
                o.write(data)
            magic = data[:4].hex()
            idx.write(f'{i},{lba},{n},{size},{usize},{int(size != usize)},{magic}\n')
    print(len(entries), 'entries')

if __name__ == '__main__':
    main(*sys.argv[1:4])
