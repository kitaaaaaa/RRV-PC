"""PS2 EE memory map for the interpreter.

RDRAM 32 MB (0x00000000, mirrored through KSEG0/KSEG1 and 0x2/0x3 uncached segments),
scratchpad 16 KB at 0x70000000, VU0/VU1 memories at 0x11000000..0x1100ffff,
hardware registers 0x10000000..0x1000ffff routed to a handler object.
Any access outside these ranges raises MemError with the address."""
import struct


class MemError(Exception):
    pass


class Memory:
    def __init__(self):
        self.ram = bytearray(32 * 1024 * 1024)
        self.spr = bytearray(16 * 1024)
        self.vu0_micro = bytearray(4 * 1024)
        self.vu0_data = bytearray(4 * 1024)
        self.vu1_micro = bytearray(16 * 1024)
        self.vu1_data = bytearray(16 * 1024)
        self.hw = None  # object with read(addr, size) / write(addr, size, value)

    def region(self, addr):
        """Return (buffer, offset) for a virtual address, or (None, addr) for hardware."""
        a = addr & 0xffffffff
        seg = a >> 28
        if seg == 0x7 and a < 0x70004000:
            return self.spr, a - 0x70000000
        if seg == 0x1 and (0x10000000 <= a < 0x10010000 or 0x11000000 <= a < 0x11010000 or
                           0x12000000 <= a < 0x12002000):
            seg = 0x0
        if seg in (0x0, 0x2, 0x3, 0x8, 0xa) or seg == 0x1:
            p = a & 0x1fffffff
            if p < 0x02000000:
                return self.ram, p
            if 0x11000000 <= p < 0x11010000:
                o = p - 0x11000000
                if o < 0x1000:
                    return self.vu0_micro, o
                if 0x4000 <= o < 0x5000:
                    return self.vu0_data, o - 0x4000
                if 0x8000 <= o < 0xc000:
                    return self.vu1_micro, o - 0x8000
                if 0xc000 <= o < 0x10000:
                    return self.vu1_data, o - 0xc000
            if 0x10000000 <= p < 0x10010000 or 0x12000000 <= p < 0x12002000:
                return None, p
            if 0x1fc00000 <= p < 0x20000000:  # BIOS: unmapped here
                raise MemError('BIOS access %#010x' % a)
        raise MemError('unmapped access %#010x' % a)

    def read(self, addr, size):
        buf, o = self.region(addr)
        if buf is None:
            if self.hw is None:
                raise MemError('hw read %#010x' % addr)
            return self.hw.read(o, size)
        return int.from_bytes(buf[o:o + size], 'little')

    def write(self, addr, size, value):
        buf, o = self.region(addr)
        if buf is None:
            if self.hw is None:
                raise MemError('hw write %#010x' % addr)
            self.hw.write(o, size, value)
            return
        buf[o:o + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little')

    def read_bytes(self, addr, n):
        buf, o = self.region(addr)
        return bytes(buf[o:o + n])

    def write_bytes(self, addr, data):
        buf, o = self.region(addr)
        buf[o:o + len(data)] = data

    def f32(self, addr):
        return struct.unpack('<f', self.read_bytes(addr, 4))[0]
