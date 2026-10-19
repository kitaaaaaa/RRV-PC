"""Load a car with the game's code, apply colours/number with the game's routines, run the
car draw routine 0x227298 and record which model parts were drawn into which packet."""
import struct
import sys

sys.path.insert(0, 'J:/RIDGERACERV/tools/vm')
from run_carload import load_car
from machine import GP

PACKET_BUF = 0x01e60000
HEADS = 0x01e5f000
CAR_OBJ = 0x01e5e000
CTX = 0x01e5d000
COLOURS = 0x01fe0000
IDENT = struct.pack('<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def preset_colours(row_index):
    d = open('J:/RIDGERACERV/extracted/SLUS_200.02', 'rb').read()
    o = 0x3055f8 - 0x200000 + 0x1000 + 12 * row_index
    return d[o:o + 12]


def setup(car=0, a1=0, a2=0, preset=0, number=99):
    m = load_car(car, a1, a2, verbose=False)
    row = preset_colours(preset)
    m.mem.write_bytes(COLOURS, b''.join(struct.pack('<4I', row[3 * k], row[3 * k + 1], row[3 * k + 2], 0x80)
                                        for k in range(4)))
    m.call(0x22be78, 0, COLOURS, COLOURS + 16, COLOURS + 32, COLOURS + 48)
    if number is not None:
        m.call(0x22bc28, 0, number)
    return m


def run_draw(m, mode=0):
    mem = m.mem
    mem.write(0x347080 + 0x20, 4, PACKET_BUF)
    mem.write(0x347080 + 0x14, 4, PACKET_BUF + 0x80000)
    for h in range(4):
        mem.write_bytes(HEADS + 16 * h, struct.pack('<QQ', 0x70000000, 0))
    mem.write(GP - 0x58b4, 4, HEADS)
    mem.write(GP - 0x5874, 4, HEADS + 0x20)
    mem.write_bytes(CTX, bytes(0x800))
    obj = bytearray(0x400)
    struct.pack_into('<I', obj, 0, 0)          # slot
    struct.pack_into('<f', obj, 4, 1.0)
    for off in range(0x10, 0x2d0, 0x40):
        obj[off:off + 64] = IDENT
    mem.write_bytes(CAR_OBJ, bytes(obj))
    m.call(0x2225b0)                             # game's VU0 microcode / VU setup
    parts = []
    ee = m.ee

    def tr(pc):
        if pc == 0x222eb8:
            table = mem.read(GP - 0x5b24, 4)
            parts.append((mem.read(GP - 0x5b20, 4), table, ee.r(4)))
    ee.trace = tr
    m.call(0x227298, CTX, CAR_OBJ, mode, 0)
    ee.trace = None
    return parts


if __name__ == '__main__':
    import traceback
    m = setup()
    try:
        parts = run_draw(m)
        print('part draws (packet, table, n):', [(hex(a), hex(b), c) for a, b, c in parts])
    except Exception:
        traceback.print_exc()
        print('pc', hex(m.ee.pc))
    print('instr', m.ee.icount, 'syscalls', m.syscalls[-5:], 'captured dma', len(m.hw.captured))
