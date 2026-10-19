"""Emotion Engine (R5900) interpreter: integer ISA, R5900 128-bit GPRs and MMI subset, COP0 stubs,
COP1 (single-precision FPU, PS2 semantics: no NaN/Inf, overflow clamps), COP2 hooks into vu.VU0.

Instructions are decoded once per address and cached.  Unimplemented encodings raise
Unimplemented(addr, word) instead of being guessed."""
import struct

M64 = (1 << 64) - 1
M32 = 0xffffffff
M128 = (1 << 128) - 1


class Unimplemented(Exception):
    pass


class Stop(Exception):
    """Raised when execution reaches the harness return address."""


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def sx64(v32):
    return sx(v32, 32) & M64


def f2b(f):
    """Python float -> PS2 single bits (clamp to max normal, flush denormals)."""
    if f != f:
        f = 0.0
    try:
        b = struct.unpack('<I', struct.pack('<f', f))[0]
    except OverflowError:
        b = 0x7f7fffff | (0x80000000 if f < 0 else 0)
    e = (b >> 23) & 0xff
    if e == 0xff:
        b = (b & 0x80000000) | 0x7f7fffff
    elif e == 0:
        b &= 0x80000000
    return b


def b2f(b):
    e = (b >> 23) & 0xff
    if e == 0:
        return -0.0 if b >> 31 else 0.0
    if e == 0xff:  # PS2 treats exponent 255 as a normal number
        m = 1 + (b & 0x7fffff) / float(1 << 23)
        v = m * 2.0 ** 128
        return -v if b >> 31 else v
    return struct.unpack('<f', struct.pack('<I', b & M32))[0]


class EE:
    def __init__(self, mem, vu0=None):
        self.mem = mem
        self.gpr = [0] * 32          # 128-bit values
        self.hi = self.lo = 0        # 64-bit (HI/LO), plus pipeline 1 (HI1/LO1)
        self.hi1 = self.lo1 = 0
        self.sa = 0
        self.fpr = [0] * 32          # raw 32-bit
        self.acc = 0                 # FPU accumulator raw bits
        self.fcr31 = 0
        self.pc = 0
        self.npc = 4
        self.cache = {}
        self.vu0 = vu0
        self.syscall_handler = None
        self.icount = 0
        self.trace = None

    # ------------------------------------------------------------- helpers
    def r(self, i):
        return self.gpr[i] & M64

    def w64(self, i, v):
        if i:
            self.gpr[i] = (self.gpr[i] & ~M64 & M128) | (v & M64)

    def w128(self, i, v):
        if i:
            self.gpr[i] = v & M128

    def fget(self, i):
        return b2f(self.fpr[i])

    def fset(self, i, v):
        self.fpr[i] = f2b(v)

    # ------------------------------------------------------------- run loop
    def run(self, max_instr=50_000_000):
        mem = self.mem
        cache = self.cache
        for _ in range(max_instr):
            pc = self.pc
            fn = cache.get(pc)
            if fn is None:
                word = mem.read(pc, 4)
                fn = self.decode(pc, word)
                cache[pc] = fn
            self.pc = self.npc
            self.npc = (self.npc + 4) & M32
            if self.trace:
                self.trace(pc)
            fn()
            self.icount += 1
        raise RuntimeError('instruction limit reached at %#x' % self.pc)

    def branch(self, target):
        self.npc = target & M32

    def likely_skip(self):
        # branch-likely not taken: skip delay slot
        self.pc = self.npc
        self.npc = (self.npc + 4) & M32

    # ------------------------------------------------------------- decode
    def decode(self, pc, w):
        op = w >> 26
        rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        sa, fn = (w >> 6) & 31, w & 63
        imm = w & 0xffff
        simm = sx(imm, 16)
        btarget = (pc + 4 + (simm << 2)) & M32
        s = self

        def U():
            raise Unimplemented('%#010x: %08x' % (pc, w))

        if op == 0:
            return self.dec_special(pc, w, rs, rt, rd, sa, fn, U)
        if op == 1:
            cond = rt
            def regimm(likely, link, test):
                def f():
                    v = sx(s.r(rs), 64)
                    if link:
                        s.w64(31, pc + 8)
                    if test(v):
                        s.branch(btarget)
                    elif likely:
                        s.likely_skip()
                return f
            table = {0: (0, 0, lambda v: v < 0), 1: (0, 0, lambda v: v >= 0),
                     2: (1, 0, lambda v: v < 0), 3: (1, 0, lambda v: v >= 0),
                     16: (0, 1, lambda v: v < 0), 17: (0, 1, lambda v: v >= 0),
                     18: (1, 1, lambda v: v < 0), 19: (1, 1, lambda v: v >= 0)}
            if cond in table:
                return regimm(*table[cond])
            if cond == 0x18:  # MTSAB
                return lambda: setattr(s, 'sa', ((s.r(rs) & 15) ^ (imm & 15)) * 8)
            if cond == 0x19:  # MTSAH
                return lambda: setattr(s, 'sa', ((s.r(rs) & 7) ^ (imm & 7)) * 16)
            return U
        if op in (2, 3):
            target = ((pc + 4) & 0xf0000000) | ((w & 0x3ffffff) << 2)
            if op == 3:
                def f():
                    s.w64(31, pc + 8)
                    s.branch(target)
                return f
            return lambda: s.branch(target)
        if op in (4, 5, 6, 7, 20, 21, 22, 23):
            likely = op >= 20
            kind = op & 3
            def f():
                a, b = s.r(rs), s.r(rt)
                if kind == 0:
                    t = a == b
                elif kind == 1:
                    t = a != b
                elif kind == 2:
                    t = sx(a, 64) <= 0
                else:
                    t = sx(a, 64) > 0
                if t:
                    s.branch(btarget)
                elif likely:
                    s.likely_skip()
            return f
        if op in (8, 9):  # addi/addiu (no overflow trap emulated)
            return lambda: s.w64(rt, sx64((s.r(rs) + simm) & M32))
        if op == 10:
            return lambda: s.w64(rt, int(sx(s.r(rs), 64) < simm))
        if op == 11:
            return lambda: s.w64(rt, int(s.r(rs) < (simm & M64)))
        if op == 12:
            return lambda: s.w64(rt, s.r(rs) & imm)
        if op == 13:
            return lambda: s.w64(rt, s.r(rs) | imm)
        if op == 14:
            return lambda: s.w64(rt, s.r(rs) ^ imm)
        if op == 15:
            return lambda: s.w64(rt, sx64(imm << 16))
        if op == 16:
            return self.dec_cop0(pc, w, U)
        if op == 17:
            return self.dec_cop1(pc, w, U)
        if op == 18:
            return self.vu0.dec_cop2(self, pc, w, U) if self.vu0 else U
        if op in (24, 25):  # daddi/daddiu
            return lambda: s.w64(rt, (s.r(rs) + simm) & M64)
        if op in (26, 27):  # ldl / ldr
            left = op == 26
            def f():
                addr = (s.r(rs) + simm) & M32
                al = addr & ~7
                data = s.mem.read(al, 8)
                k = addr & 7
                old = s.r(rt)
                if left:
                    shift = (7 - k) * 8
                    mask = M64 >> ((k + 1) * 8) if k < 7 else 0
                    v = ((data << shift) & M64) | (old & mask)
                else:
                    shift = k * 8
                    mask = (M64 << ((8 - k) * 8)) & M64 if k else 0
                    v = (data >> shift) | (old & mask)
                s.w64(rt, v)
            return f
        if op == 28:
            return self.dec_mmi(pc, w, rs, rt, rd, sa, fn, U)
        if op == 30:  # lq
            return lambda: s.w128(rt, s.mem.read((s.r(rs) + simm) & ~15 & M32, 16))
        if op == 31:  # sq
            return lambda: s.mem.write((s.r(rs) + simm) & ~15 & M32, 16, s.gpr[rt])
        loads = {32: (1, True), 33: (2, True), 35: (4, True), 36: (1, False), 37: (2, False),
                 39: (4, False), 55: (8, False)}
        if op in loads:
            size, signed = loads[op]
            if signed:
                return lambda: s.w64(rt, sx(s.mem.read((s.r(rs) + simm) & M32, size), size * 8) & M64)
            return lambda: s.w64(rt, s.mem.read((s.r(rs) + simm) & M32, size))
        stores = {40: 1, 41: 2, 43: 4, 63: 8}
        if op in stores:
            size = stores[op]
            return lambda: s.mem.write((s.r(rs) + simm) & M32, size, s.r(rt))
        if op in (34, 38):  # lwl / lwr
            left = op == 34
            def f():
                addr = (s.r(rs) + simm) & M32
                data = s.mem.read(addr & ~3, 4)
                k = addr & 3
                old = s.r(rt) & M32
                if left:
                    v = ((data << ((3 - k) * 8)) & M32) | (old & (M32 >> ((k + 1) * 8) if k < 3 else 0))
                else:
                    v = (data >> (k * 8)) | (old & ((M32 << ((4 - k) * 8)) & M32 if k else 0))
                s.w64(rt, sx64(v))
            return f
        if op in (42, 46):  # swl / swr
            left = op == 42
            def f():
                addr = (s.r(rs) + simm) & M32
                al = addr & ~3
                k = addr & 3
                mem = s.mem.read(al, 4)
                v = s.r(rt) & M32
                if left:
                    keep = (M32 << ((k + 1) * 8)) & M32 if k < 3 else 0
                    nv = (mem & keep) | (v >> ((3 - k) * 8))
                else:
                    keep = M32 >> ((4 - k) * 8) if k else 0
                    nv = (mem & keep) | ((v << (k * 8)) & M32)
                s.mem.write(al, 4, nv)
            return f
        if op in (44, 45):  # sdl / sdr
            left = op == 44
            def f():
                addr = (s.r(rs) + simm) & M32
                al = addr & ~7
                k = addr & 7
                mem = s.mem.read(al, 8)
                v = s.r(rt)
                if left:
                    keep = (M64 << ((k + 1) * 8)) & M64 if k < 7 else 0
                    nv = (mem & keep) | (v >> ((7 - k) * 8))
                else:
                    keep = M64 >> ((8 - k) * 8) if k else 0
                    nv = (mem & keep) | ((v << (k * 8)) & M64)
                s.mem.write(al, 8, nv)
            return f
        if op in (47, 51):  # cache / pref
            return lambda: None
        if op == 49:  # lwc1
            def f():
                s.fpr[rt] = s.mem.read((s.r(rs) + simm) & M32, 4)
            return f
        if op == 57:  # swc1
            return lambda: s.mem.write((s.r(rs) + simm) & M32, 4, s.fpr[rt])
        if op == 54:  # lqc2
            return lambda: s.vu0.set_vf_raw(rt, s.mem.read((s.r(rs) + simm) & ~15 & M32, 16))
        if op == 62:  # sqc2
            return lambda: s.mem.write((s.r(rs) + simm) & ~15 & M32, 16, s.vu0.get_vf_raw(rt))
        return U

    # ------------------------------------------------------------- SPECIAL
    def dec_special(self, pc, w, rs, rt, rd, sa, fn, U):
        s = self
        if fn == 0:
            return lambda: s.w64(rd, sx64((s.r(rt) << sa) & M32))
        if fn == 2:
            return lambda: s.w64(rd, sx64((s.r(rt) & M32) >> sa))
        if fn == 3:
            return lambda: s.w64(rd, sx(s.r(rt), 32) >> sa & M64)
        if fn == 4:
            return lambda: s.w64(rd, sx64((s.r(rt) << (s.r(rs) & 31)) & M32))
        if fn == 6:
            return lambda: s.w64(rd, sx64((s.r(rt) & M32) >> (s.r(rs) & 31)))
        if fn == 7:
            return lambda: s.w64(rd, (sx(s.r(rt), 32) >> (s.r(rs) & 31)) & M64)
        if fn == 8:
            return lambda: s.branch(s.r(rs))
        if fn == 9:
            def f():
                t = s.r(rs)
                s.w64(rd, pc + 8)
                s.branch(t)
            return f
        if fn == 10:
            return lambda: s.w64(rd, s.r(rs)) if s.r(rt) == 0 else None
        if fn == 11:
            return lambda: s.w64(rd, s.r(rs)) if s.r(rt) != 0 else None
        if fn == 12:
            def f():
                if s.syscall_handler is None:
                    raise Unimplemented('%#010x: syscall %d' % (pc, s.r(3)))
                s.syscall_handler(s)
            return f
        if fn == 13:
            def f():
                raise Unimplemented('%#010x: break' % pc)
            return f
        if fn == 15:
            return lambda: None
        if fn == 16:
            return lambda: s.w64(rd, s.hi)
        if fn == 17:
            return lambda: setattr(s, 'hi', s.r(rs))
        if fn == 18:
            return lambda: s.w64(rd, s.lo)
        if fn == 19:
            return lambda: setattr(s, 'lo', s.r(rs))
        if fn == 20:
            return lambda: s.w64(rd, (s.r(rt) << (s.r(rs) & 63)) & M64)
        if fn == 22:
            return lambda: s.w64(rd, s.r(rt) >> (s.r(rs) & 63))
        if fn == 23:
            return lambda: s.w64(rd, (sx(s.r(rt), 64) >> (s.r(rs) & 63)) & M64)
        if fn == 24 or fn == 25:  # mult / multu (R5900: also writes rd)
            signed = fn == 24
            def f():
                a, b = s.r(rs) & M32, s.r(rt) & M32
                if signed:
                    a, b = sx(a, 32), sx(b, 32)
                p = (a * b) & M64
                s.lo = sx64(p & M32)
                s.hi = sx64(p >> 32)
                s.w64(rd, s.lo)
            return f
        if fn in (26, 27):
            signed = fn == 26
            def f():
                a, b = s.r(rs) & M32, s.r(rt) & M32
                if signed:
                    a, b = sx(a, 32), sx(b, 32)
                if b == 0:
                    return
                q = int(a / b) if signed else a // b
                r = a - q * b
                s.lo = sx64(q & M32)
                s.hi = sx64(r & M32)
            return f
        if fn in (32, 33):
            return lambda: s.w64(rd, sx64((s.r(rs) + s.r(rt)) & M32))
        if fn in (34, 35):
            return lambda: s.w64(rd, sx64((s.r(rs) - s.r(rt)) & M32))
        if fn == 36:
            return lambda: s.w64(rd, s.r(rs) & s.r(rt))
        if fn == 37:
            return lambda: s.w64(rd, s.r(rs) | s.r(rt))
        if fn == 38:
            return lambda: s.w64(rd, s.r(rs) ^ s.r(rt))
        if fn == 39:
            return lambda: s.w64(rd, ~(s.r(rs) | s.r(rt)) & M64)
        if fn == 40:
            return lambda: s.w64(rd, s.sa)
        if fn == 41:
            return lambda: setattr(s, 'sa', s.r(rs))
        if fn == 42:
            return lambda: s.w64(rd, int(sx(s.r(rs), 64) < sx(s.r(rt), 64)))
        if fn == 43:
            return lambda: s.w64(rd, int(s.r(rs) < s.r(rt)))
        if fn in (44, 45):
            return lambda: s.w64(rd, (s.r(rs) + s.r(rt)) & M64)
        if fn in (46, 47):
            return lambda: s.w64(rd, (s.r(rs) - s.r(rt)) & M64)
        if fn == 56:
            return lambda: s.w64(rd, (s.r(rt) << sa) & M64)
        if fn == 58:
            return lambda: s.w64(rd, s.r(rt) >> sa)
        if fn == 59:
            return lambda: s.w64(rd, (sx(s.r(rt), 64) >> sa) & M64)
        if fn == 60:
            return lambda: s.w64(rd, (s.r(rt) << (sa + 32)) & M64)
        if fn == 62:
            return lambda: s.w64(rd, s.r(rt) >> (sa + 32))
        if fn == 63:
            return lambda: s.w64(rd, (sx(s.r(rt), 64) >> (sa + 32)) & M64)
        return U

    # ------------------------------------------------------------- COP0
    def dec_cop0(self, pc, w, U):
        s = self
        rs, rt = (w >> 21) & 31, (w >> 16) & 31
        if rs == 0:  # mfc0: return 0 (count etc. not modelled)
            return lambda: s.w64(rt, 0)
        if rs == 4:  # mtc0
            return lambda: None
        if rs == 0x10 and (w & 63) in (0x38, 0x39, 0x18):  # ei/di/eret
            return lambda: None
        if rs == 8:  # bc0f/bc0t: CPCOND0 treated as true (DMA idle)
            simm = sx(w & 0xffff, 16)
            target = (pc + 4 + (simm << 2)) & M32
            cond = rt & 1
            likely = (rt >> 1) & 1
            def f():
                if cond:
                    s.branch(target)
                elif likely:
                    s.likely_skip()
            return f
        return U

    # ------------------------------------------------------------- COP1
    def dec_cop1(self, pc, w, U):
        s = self
        fmt = (w >> 21) & 31
        ft, fs, fd = (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31
        fn = w & 63
        if fmt == 0:
            return lambda: s.w64(ft, sx64(s.fpr[fs]))
        if fmt == 2:
            return lambda: s.w64(ft, sx64(s.fcr31 if fs == 31 else 0x2e30))
        if fmt == 4:
            def f():
                s.fpr[fs] = s.r(ft) & M32
            return f
        if fmt == 6:
            def f():
                if fs == 31:
                    s.fcr31 = s.r(ft) & M32
            return f
        if fmt == 8:
            simm = sx(w & 0xffff, 16)
            target = (pc + 4 + (simm << 2)) & M32
            want = ft & 1
            likely = (ft >> 1) & 1
            def f():
                c = (s.fcr31 >> 23) & 1
                if c == want:
                    s.branch(target)
                elif likely:
                    s.likely_skip()
            return f
        if fmt == 16:
            g, st = s.fget, s.fset
            ops = {
                0: lambda: st(fd, g(fs) + g(ft)),
                1: lambda: st(fd, g(fs) - g(ft)),
                2: lambda: st(fd, g(fs) * g(ft)),
                3: lambda: st(fd, g(fs) / g(ft) if g(ft) != 0 else (3.4028234663852886e38 if (g(fs) >= 0) == (s.fpr[ft] >> 31 == 0) else -3.4028234663852886e38)),
                4: lambda: st(fd, abs(g(ft)) ** 0.5),
                5: lambda: s.fpr.__setitem__(fd, s.fpr[fs] & 0x7fffffff),
                6: lambda: s.fpr.__setitem__(fd, s.fpr[fs]),
                7: lambda: s.fpr.__setitem__(fd, s.fpr[fs] ^ 0x80000000),
                22: lambda: st(fd, g(fs) / (abs(g(ft)) ** 0.5) if g(ft) != 0 else 3.4028234663852886e38),
                24: lambda: setattr(s, 'acc', f2b(g(fs) + g(ft))),
                25: lambda: setattr(s, 'acc', f2b(g(fs) - g(ft))),
                26: lambda: setattr(s, 'acc', f2b(g(fs) * g(ft))),
                28: lambda: st(fd, b2f(s.acc) + g(fs) * g(ft)),
                29: lambda: st(fd, b2f(s.acc) - g(fs) * g(ft)),
                30: lambda: setattr(s, 'acc', f2b(b2f(s.acc) + g(fs) * g(ft))),
                31: lambda: setattr(s, 'acc', f2b(b2f(s.acc) - g(fs) * g(ft))),
                36: lambda: s.fpr.__setitem__(fd, int(max(min(g(fs), 2147483647.0), -2147483648.0)) & M32),
                40: lambda: st(fd, max(g(fs), g(ft))),
                41: lambda: st(fd, min(g(fs), g(ft))),
            }
            if fn in ops:
                return ops[fn]
            if fn in (48, 50, 52, 54):  # c.f c.eq c.lt c.le
                def f():
                    a, b = g(fs), g(ft)
                    c = {48: False, 50: a == b, 52: a < b, 54: a <= b}[fn]
                    s.fcr31 = (s.fcr31 | (1 << 23)) if c else (s.fcr31 & ~(1 << 23))
                return f
            return U
        if fmt == 20 and fn == 32:  # cvt.s.w
            return lambda: s.fset(fd, float(sx(s.fpr[fs], 32)))
        return U

    # ------------------------------------------------------------- MMI
    def dec_mmi(self, pc, w, rs, rt, rd, sa, fn, U):
        s = self
        if fn == 0 or fn == 1:  # madd / maddu
            signed = fn == 0
            def f():
                a, b = s.r(rs) & M32, s.r(rt) & M32
                if signed:
                    a, b = sx(a, 32), sx(b, 32)
                acc = ((s.hi & M32) << 32) | (s.lo & M32)
                v = (acc + a * b) & M64
                s.lo = sx64(v & M32)
                s.hi = sx64(v >> 32)
                s.w64(rd, s.lo)
            return f
        if fn == 0x10:
            return lambda: s.w64(rd, s.hi1)
        if fn == 0x11:
            return lambda: setattr(s, 'hi1', s.r(rs))
        if fn == 0x12:
            return lambda: s.w64(rd, s.lo1)
        if fn == 0x13:
            return lambda: setattr(s, 'lo1', s.r(rs))
        if fn in (0x18, 0x19):  # mult1 / multu1
            signed = fn == 0x18
            def f():
                a, b = s.r(rs) & M32, s.r(rt) & M32
                if signed:
                    a, b = sx(a, 32), sx(b, 32)
                p = (a * b) & M64
                s.lo1 = sx64(p & M32)
                s.hi1 = sx64(p >> 32)
                s.w64(rd, s.lo1)
            return f
        if fn in (0x1a, 0x1b):
            signed = fn == 0x1a
            def f():
                a, b = s.r(rs) & M32, s.r(rt) & M32
                if signed:
                    a, b = sx(a, 32), sx(b, 32)
                if b == 0:
                    return
                q = int(a / b) if signed else a // b
                s.lo1 = sx64(q & M32)
                s.hi1 = sx64((a - q * b) & M32)
            return f
        sub = (w >> 6) & 31
        if fn == 0x28 and sub == 0x10:  # PADDUW etc. not needed yet
            return U
        if fn == 0x09:  # MMI2
            if sub == 0x0e:  # pcpyld
                return lambda: s.w128(rd, ((s.gpr[rs] & M64) << 64) | (s.gpr[rt] & M64))
            if sub == 0x12:  # pand
                return lambda: s.w128(rd, s.gpr[rs] & s.gpr[rt])
            if sub == 0x13:  # pxor
                return lambda: s.w128(rd, s.gpr[rs] ^ s.gpr[rt])
            return U
        if fn == 0x29:  # MMI3
            if sub == 0x0e:  # pcpyud
                return lambda: s.w128(rd, (s.gpr[rt] & ~M64 & M128) | (s.gpr[rs] >> 64))
            if sub == 0x12:  # por
                return lambda: s.w128(rd, s.gpr[rs] | s.gpr[rt])
            if sub == 0x13:  # pnor
                return lambda: s.w128(rd, ~(s.gpr[rs] | s.gpr[rt]) & M128)
            if sub == 0x1b:  # pcpyh
                def f():
                    v = s.gpr[rt]
                    a = v & 0xffff
                    b = (v >> 64) & 0xffff
                    lo = a * 0x0001000100010001
                    hi = b * 0x0001000100010001
                    s.w128(rd, (hi << 64) | lo)
                return f
            return U
        if fn == 0x08:  # MMI0
            if sub == 0x12:  # pextlw
                def f():
                    a, b = s.gpr[rs] & M64, s.gpr[rt] & M64
                    v = (b & M32) | ((a & M32) << 32) | ((b >> 32) << 64) | ((a >> 32) << 96)
                    s.w128(rd, v)
                return f
            return U
        return U
