"""Harness: build a PS2 EE machine from SLUS_200.02, place data in RAM and call game functions.

    m = Machine('J:/RIDGERACERV/extracted/SLUS_200.02')
    addr = m.alloc(data)                # copy bytes into free RAM (16-byte aligned)
    ret = m.call(0x22a600, addr)        # run function with a0.. args until it returns
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mem import Memory
from ee import EE, Stop, M64, sx64
from vu import VU0
from hw import Hardware

GP = 0x33a670          # set by the entry code at 0x20005c
STACK_TOP = 0x01ff0000
RETURN_TRAP = 0x01ffff00
HEAP_START = 0x01200000  # above the program's bss end (0x1e49c2c)


class Machine:
    def __init__(self, elf_path):
        self.mem = Memory()
        self.vu0 = VU0(self.mem)
        self.hw = Hardware(self.mem, self.vu0)
        self.mem.hw = self.hw
        self.ee = EE(self.mem, self.vu0)
        self.hw.ee = self.ee
        self.ee.syscall_handler = self.syscall
        self.syscalls = []
        self.heap = 0x01e50000
        self.load_elf(elf_path)

    def load_elf(self, path):
        d = open(path, 'rb').read()
        entry, phoff = struct.unpack_from('<II', d, 0x18)
        phnum = struct.unpack_from('<H', d, 0x2c)[0]
        for i in range(phnum):
            t, off, va, pa, fs, ms, fl, al = struct.unpack_from('<8I', d, phoff + 32 * i)
            if t == 1:
                self.mem.ram[va:va + fs] = d[off:off + fs]
                self.mem.ram[va + fs:va + ms] = bytes(ms - fs)
        self.entry = entry

    def alloc(self, data, align=64):
        self.heap = (self.heap + align - 1) & ~(align - 1)
        addr = self.heap
        if addr + len(data) > 0x01fe0000:
            raise MemoryError('VM heap exhausted')
        self.mem.ram[addr:addr + len(data)] = data
        self.heap += len(data)
        return addr

    def syscall(self, ee):
        num = ee.r(3) & 0xffffffff
        num = num - (1 << 32) if num >> 31 else num
        self.syscalls.append(num)
        if num in (100, -100, 0x64):  # FlushCache
            ee.w64(2, 0)
            return
        ee.w64(2, 0)

    # ------------------------------------------------------------ HLE hooks
    def hook(self, addr, fn):
        """Replace the function at addr: fn(machine) returns v0; execution returns to ra."""
        ee = self.ee
        def h():
            v = fn(self)
            if v is not None:
                ee.w64(2, v & M64)
            ee.pc = ee.r(31)
            ee.npc = ee.pc + 4
        ee.cache[addr] = h

    def attach_disc(self, bin_path, r5all_lba=83743):
        """Serve libcdvd reads from a MODE2/2352 BIN image (only the CD layer is replaced)."""
        self.disc = open(bin_path, 'rb')
        self.cd_log = []

        def sector(lsn):
            self.disc.seek(lsn * 2352 + 24)
            return self.disc.read(2048)

        def cd_read(m):  # sceCdRead(lsn, sectors, buf, mode)
            e = m.ee
            lsn, n, buf = e.r(4) & 0xffffffff, e.r(5) & 0xffffffff, e.r(6) & 0xffffffff
            data = b''.join(sector(lsn + k) for k in range(n))
            m.mem.write_bytes(buf, data)
            m.cd_log.append((lsn, n, buf))
            return 1
        self.hook(0x2c7780, cd_read)
        self.hook(0x2c8038, lambda m: 0)   # sceCdSync: transfer finished
        self.hook(0x2c8488, lambda m: 2)   # drive status (anything but 6 = ready)
        self.hook(0x2c8858, lambda m: 0)   # sceCdGetError: SCECdErNO
        self.mem.write(GP - 0x5a84, 4, r5all_lba)

    def call(self, func, *args, max_instr=50_000_000, fargs=()):
        ee = self.ee
        for i, a in enumerate(args):
            ee.w64(4 + i, a & M64)
        for i, a in enumerate(fargs):
            ee.fset(12 + i, a)
        ee.w64(28, GP)
        ee.w64(29, STACK_TOP)
        ee.w64(31, RETURN_TRAP)
        ee.pc = func
        ee.npc = func + 4

        def trap():
            raise Stop()
        ee.cache[RETURN_TRAP] = trap
        try:
            ee.run(max_instr)
        except Stop:
            pass
        return ee.r(2)
