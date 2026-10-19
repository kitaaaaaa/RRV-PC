"""VU0 for the EE interpreter: COP2 macro instructions and micro-mode execution.

Floating point uses PS2 single semantics via ee.f2b/b2f (no NaN/Inf, clamped).  FMAC results
are treated as immediately available (the hardware interlocks on them); the Q register follows
DIV/SQRT/RSQRT latency (7/7/13 cycles) because reading Q early is not interlocked.
Unimplemented encodings raise ee.Unimplemented with the address."""
import struct

from ee import Unimplemented, f2b, b2f, M32, sx

XYZW = (8, 4, 2, 1)  # dest bit for x, y, z, w


def fr(v):
    """Round a Python float to PS2 single precision."""
    return b2f(f2b(v))


class VU0:
    def __init__(self, mem):
        self.mem = mem
        self.vf = [[0.0, 0.0, 0.0, 0.0] for _ in range(32)]
        self.vf[0] = [0.0, 0.0, 0.0, 1.0]
        self.vi = [0] * 16
        self.acc = [0.0] * 4
        self.q = 0.0
        self.q_pending = None  # (ready_cycle, value)
        self.p = 0.0
        self.i = 0.0
        self.r = 0x3f800000
        self.clip = 0
        self.mac = 0
        self.status = 0
        self.cycle = 0
        self.micro_cache = {}
        self.trace = None

    # ---------------------------------------------------------- register io
    def set_vf_raw(self, n, v128):
        if n == 0:
            return
        self.vf[n] = [b2f((v128 >> (32 * k)) & M32) for k in range(4)]

    def get_vf_raw(self, n):
        v = 0
        for k in range(4):
            v |= f2b(self.vf[n][k]) << (32 * k)
        return v

    def get_q(self):
        if self.q_pending and self.cycle >= self.q_pending[0]:
            self.q = self.q_pending[1]
            self.q_pending = None
        return self.q

    def waitq(self):
        if self.q_pending:
            self.q = self.q_pending[1]
            self.cycle = max(self.cycle, self.q_pending[0])
            self.q_pending = None

    # ---------------------------------------------------------- shared ops
    def write_vf(self, n, dest, vals):
        if n == 0:
            return
        reg = self.vf[n]
        mac = 0
        for k in range(4):
            if dest & XYZW[k]:
                v = fr(vals[k])
                reg[k] = v
                if v == 0:
                    mac |= 1 << (3 - k)
                if v < 0:
                    mac |= 1 << (7 - k)
        self.mac = mac

    def write_acc(self, dest, vals):
        for k in range(4):
            if dest & XYZW[k]:
                self.acc[k] = fr(vals[k])

    def upper(self, name, dest, fd, fs, ft, bc):
        """Execute an FMAC/upper operation. bc: 0-3 broadcast index, 'q', 'i', or None."""
        A = self.vf[fs]
        if bc is None:
            B = self.vf[ft]
        elif bc == 'q':
            q = self.get_q()
            B = [q] * 4
        elif bc == 'i':
            B = [self.i] * 4
        else:
            B = [self.vf[ft][bc]] * 4
        acc = self.acc
        if name in ('add', 'sub', 'mul', 'max', 'mini', 'madd', 'msub'):
            if name == 'add':
                r = [A[k] + B[k] for k in range(4)]
            elif name == 'sub':
                r = [A[k] - B[k] for k in range(4)]
            elif name == 'mul':
                r = [A[k] * B[k] for k in range(4)]
            elif name == 'max':
                r = [max(A[k], B[k]) for k in range(4)]
            elif name == 'mini':
                r = [min(A[k], B[k]) for k in range(4)]
            elif name == 'madd':
                r = [acc[k] + A[k] * B[k] for k in range(4)]
            else:
                r = [acc[k] - A[k] * B[k] for k in range(4)]
            self.write_vf(fd, dest, r)
            return
        if name in ('adda', 'suba', 'mula', 'madda', 'msuba'):
            if name == 'adda':
                r = [A[k] + B[k] for k in range(4)]
            elif name == 'suba':
                r = [A[k] - B[k] for k in range(4)]
            elif name == 'mula':
                r = [A[k] * B[k] for k in range(4)]
            elif name == 'madda':
                r = [acc[k] + A[k] * B[k] for k in range(4)]
            else:
                r = [acc[k] - A[k] * B[k] for k in range(4)]
            self.write_acc(dest, r)
            return
        if name == 'opmula':  # ACC.xyz = fs.yzx * ft.zxy
            r = [A[1] * B[2], A[2] * B[0], A[0] * B[1], 0.0]
            self.write_acc(0b1110, r)
            return
        if name == 'opmsub':
            r = [acc[0] - A[1] * B[2], acc[1] - A[2] * B[0], acc[2] - A[0] * B[1], 0.0]
            self.write_vf(fd, 0b1110, r)
            return
        if name == 'abs':
            self.write_vf(ft, dest, [abs(x) for x in A])
            return
        if name.startswith('itof'):
            sh = int(name[4:])
            raw = [f2b(x) for x in A]  # registers hold raw ints when used as ints
            vals = [sx(self._raw(fs, k), 32) / float(1 << sh) for k in range(4)]
            self.write_vf(ft, dest, vals)
            return
        if name.startswith('ftoi'):
            sh = int(name[4:])
            if ft == 0:
                return
            for k in range(4):
                if dest & XYZW[k]:
                    v = int(A[k] * (1 << sh))
                    v = max(min(v, 0x7fffffff), -0x80000000)
                    self._set_raw(ft, k, v & M32)
            return
        if name == 'clip':
            w = abs(B[3])
            flags = 0
            for k, (pos, neg) in enumerate(((0, 1), (2, 3), (4, 5))):
                if A[k] > w:
                    flags |= 1 << pos
                if A[k] < -w:
                    flags |= 1 << neg
            self.clip = ((self.clip << 6) | flags) & 0xffffff
            return
        if name == 'nop':
            return
        raise Unimplemented('VU upper %s' % name)

    # raw-int view of vf registers (for itof/ftoi and integer-packed data)
    def _raw(self, n, k):
        return f2b(self.vf[n][k]) if not hasattr(self, '_rawregs') else self._rawregs.get((n, k), f2b(self.vf[n][k]))

    def _set_raw(self, n, k, v):
        # store an integer bit pattern; b2f keeps it bit-exact for normal patterns,
        # and _rawregs keeps exact bits for patterns b2f/f2b would alter
        if not hasattr(self, '_rawregs'):
            self._rawregs = {}
        self.vf[n][k] = b2f(v)
        if f2b(self.vf[n][k]) != v:
            self._rawregs[(n, k)] = v
        else:
            self._rawregs.pop((n, k), None)

    # ---------------------------------------------------------- COP2 decode (macro mode)
    def dec_cop2(self, ee, pc, w, U):
        s = self
        rs = (w >> 21) & 31
        ft, fs, fd = (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31
        if not (w >> 25) & 1:
            if rs == 1:  # qmfc2
                return lambda: ee.w128(ft, s.get_vf_raw(fs))
            if rs == 5:  # qmtc2
                return lambda: s.set_vf_raw(fs, ee.gpr[ft])
            if rs == 2:  # cfc2
                return lambda: ee.w64(ft, s.read_ctrl(fs) & 0xffffffff if s.read_ctrl(fs) >= 0 else 0)
            if rs == 6:  # ctc2
                return lambda: s.write_ctrl(fs, ee.r(ft) & M32, ee)
            if rs == 8:  # bc2f/t: VU0 never busy here (microprograms run synchronously)
                simm = sx(w & 0xffff, 16)
                target = (pc + 4 + (simm << 2)) & M32
                want = ft & 1
                likely = (ft >> 1) & 1
                def f():
                    if want == 0:
                        ee.branch(target)
                    elif likely:
                        ee.likely_skip()
                return f
            return U
        op = self.decode_upper_like(w, macro=True)
        if op is None:
            return U
        kind = op[0]
        if kind == 'upper':
            _, name, dest, fd_, fs_, ft_, bc = op
            return lambda: s.upper(name, dest, fd_, fs_, ft_, bc)
        if kind == 'callms':
            addr = op[1]
            return lambda: s.run_micro(addr, ee)
        if kind == 'lower':
            fn = op[1]
            return lambda: fn(s, ee)
        return U

    def read_ctrl(self, n):
        if n < 16:
            return self.vi[n] & 0xffff
        if n == 16:
            return self.status
        if n == 17:
            return self.mac
        if n == 18:
            return self.clip
        if n == 20:
            return self.r
        if n == 21:
            return f2b(self.i)
        if n == 22:
            return f2b(self.get_q())
        if n == 23:
            return f2b(self.p)
        return 0

    def write_ctrl(self, n, v, ee):
        if n < 16:
            if n:
                self.vi[n] = v & 0xffff
        elif n == 18:
            self.clip = v & 0xffffff
        elif n == 20:
            self.r = v
        elif n == 21:
            self.i = b2f(v)
        elif n == 22:
            self.q = b2f(v)
        elif n == 27:  # CMSAR0
            self.cmsar0 = v & 0xffff
        elif n == 28:  # FBRST: reset/ force break - ignored
            pass
        elif n == 31:  # CMSAR1: start VU1 (not modelled)
            raise Unimplemented('CMSAR1 (VU1 start) write')

    # ---------------------------------------------------------- decoding helpers
    SP1 = {0x1c: ('mul', 'q'), 0x1d: ('max', 'i'), 0x1e: ('mul', 'i'), 0x1f: ('mini', 'i'),
           0x20: ('add', 'q'), 0x21: ('madd', 'q'), 0x22: ('add', 'i'), 0x23: ('madd', 'i'),
           0x24: ('sub', 'q'), 0x25: ('msub', 'q'), 0x26: ('sub', 'i'), 0x27: ('msub', 'i'),
           0x28: ('add', None), 0x29: ('madd', None), 0x2a: ('mul', None), 0x2b: ('max', None),
           0x2c: ('sub', None), 0x2d: ('msub', None), 0x2e: ('opmsub', None), 0x2f: ('mini', None)}
    SP1BC = {0x00: 'add', 0x04: 'sub', 0x08: 'madd', 0x0c: 'msub', 0x10: 'max', 0x14: 'mini', 0x18: 'mul'}
    SP2BC = {0x00: 'adda', 0x04: 'suba', 0x08: 'madda', 0x0c: 'msuba', 0x18: 'mula'}
    SP2 = {0x10: 'itof0', 0x11: 'itof4', 0x12: 'itof12', 0x13: 'itof15', 0x14: 'ftoi0', 0x15: 'ftoi4',
           0x16: 'ftoi12', 0x17: 'ftoi15', 0x1c: ('mula', 'q'), 0x1d: 'abs', 0x1e: ('mula', 'i'),
           0x1f: 'clip', 0x20: ('adda', 'q'), 0x21: ('madda', 'q'), 0x22: ('adda', 'i'),
           0x23: ('madda', 'i'), 0x24: ('suba', 'q'), 0x25: ('msuba', 'q'), 0x26: ('suba', 'i'),
           0x27: ('msuba', 'i'), 0x28: ('adda', None), 0x29: ('madda', None), 0x2a: ('mula', None),
           0x2c: ('suba', None), 0x2d: ('msuba', None), 0x2e: ('opmula', None), 0x2f: 'nop'}

    def decode_upper_like(self, w, macro):
        """Decode the FMAC part shared by COP2 macro and VU upper instructions."""
        dest = (w >> 21) & 15
        ft, fs, fd = (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31
        funct = w & 0x3f
        if funct < 0x3c:
            if funct < 0x1c:
                return ('upper', self.SP1BC[funct & ~3], dest, fd, fs, ft, funct & 3) if (funct & ~3) in self.SP1BC else None
            if funct in self.SP1:
                name, bc = self.SP1[funct]
                return ('upper', name, dest, fd, fs, ft, bc)
            if macro:
                return self.decode_macro_lower(w, funct)
            return None
        idx = ((w >> 4) & 0x7c) | (funct & 3)
        if idx < 0x10 or 0x18 <= idx < 0x1c:
            base = idx & ~3
            if base in self.SP2BC:
                return ('upper', self.SP2BC[base], dest, 0, fs, ft, idx & 3)
            return None
        e = self.SP2.get(idx)
        if e is None:
            return self.decode_macro_lower(w, None, idx) if macro else None
        if isinstance(e, tuple):
            return ('upper', e[0], dest, 0, fs, ft, e[1])
        if e in ('abs',) or e.startswith('itof') or e.startswith('ftoi'):
            return ('upper', e, dest, 0, fs, ft, None)
        if e == 'clip':
            return ('upper', 'clip', dest, 0, fs, ft, None)
        return ('upper', e, dest, 0, fs, ft, None)

    def decode_macro_lower(self, w, funct, idx=None):
        """COP2 macro-mode versions of integer / move / div instructions."""
        it, is_, id_ = (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31
        dest = (w >> 21) & 15
        if funct is not None:
            if funct == 0x38:  # vcallms
                return ('callms', ((w >> 6) & 0x7fff) * 8)
            if funct == 0x39:  # vcallmsr
                return ('lower', lambda s, ee: s.run_micro(getattr(s, 'cmsar0', 0) * 8, ee))
            if funct == 0x30:
                return ('lower', lambda s, ee: s.set_vi(id_, s.vi[is_] + s.vi[it]))
            if funct == 0x31:
                return ('lower', lambda s, ee: s.set_vi(id_, s.vi[is_] - s.vi[it]))
            if funct == 0x32:
                imm = sx(id_, 5)
                return ('lower', lambda s, ee: s.set_vi(it, s.vi[is_] + imm))
            if funct == 0x34:
                return ('lower', lambda s, ee: s.set_vi(id_, s.vi[is_] & s.vi[it]))
            if funct == 0x35:
                return ('lower', lambda s, ee: s.set_vi(id_, s.vi[is_] | s.vi[it]))
            return None
        lw = self.lower_special2(idx, dest, it, is_, w, macro=True)
        return ('lower', lw) if lw else None

    def set_vi(self, n, v):
        if n:
            self.vi[n] = v & 0xffff

    def lower_special2(self, idx, dest, ft, fs, w, macro=False):
        """MOVE/MR32/LQI/SQI/LQD/SQD/DIV/SQRT/RSQRT/WAITQ/MTIR/MFIR/ILWR/ISWR/R*."""
        fsf, ftf = (w >> 21) & 3, (w >> 23) & 3
        it, is_ = ft, fs

        def vmem_addr(s, base):
            return (base & 0xff) * 16  # VU0 data memory: 4 KB = 256 qwords

        if idx == 0x30:
            return lambda s, ee=None: s.write_vf_exact(ft, dest, list(s.vf[fs]))
        if idx == 0x31:
            return lambda s, ee=None: s.write_vf_exact(ft, dest, [s.vf[fs][1], s.vf[fs][2], s.vf[fs][3], s.vf[fs][0]])
        if idx in (0x34, 0x36):  # lqi / lqd
            post = idx == 0x34
            def f(s, ee=None):
                if not post:
                    s.set_vi(is_, s.vi[is_] - 1)
                a = vmem_addr(s, s.vi[is_])
                v = int.from_bytes(s.mem.vu0_data[a:a + 16], 'little')
                s.load_vf_masked(ft, dest, v)
                if post:
                    s.set_vi(is_, s.vi[is_] + 1)
            return f
        if idx in (0x35, 0x37):  # sqi / sqd  (fs = source vf, ft field = it base)
            post = idx == 0x35
            def f(s, ee=None):
                if not post:
                    s.set_vi(it, s.vi[it] - 1)
                a = vmem_addr(s, s.vi[it])
                s.store_vf_masked(fs, dest, a)
                if post:
                    s.set_vi(it, s.vi[it] + 1)
            return f
        if idx in (0x38, 0x39, 0x3a):
            def f(s, ee=None):
                a = s.vf[fs][fsf]
                b = s.vf[ft][ftf]
                if idx == 0x38:
                    v = a / b if b != 0 else (3.4028234663852886e38 if (a >= 0) == (b >= 0) else -3.4028234663852886e38)
                    lat = 7
                elif idx == 0x39:
                    v = abs(b) ** 0.5
                    lat = 7
                else:
                    v = a / (abs(b) ** 0.5) if b != 0 else 3.4028234663852886e38
                    lat = 13
                s.q_pending = (s.cycle + lat, fr(v))
                if macro:
                    s.waitq()
            return f
        if idx == 0x3b:
            return lambda s, ee=None: s.waitq()
        if idx == 0x3c:  # mtir it, fs[fsf]
            return lambda s, ee=None: s.set_vi(it, s.raw_word(fs, fsf) & 0xffff)
        if idx == 0x3d:  # mfir ft, is
            def f(s, ee=None):
                v = sx(s.vi[is_], 16) & M32
                for k in range(4):
                    if dest & XYZW[k] and ft:
                        s._set_raw(ft, k, v)
            return f
        if idx in (0x3e, 0x3f):  # ilwr / iswr
            load = idx == 0x3e
            def f(s, ee=None):
                a = vmem_addr(s, s.vi[is_])
                for k in range(4):
                    if dest & XYZW[k]:
                        o = a + 4 * k
                        if load:
                            s.set_vi(it, int.from_bytes(s.mem.vu0_data[o:o + 2], 'little'))
                        else:
                            s.mem.vu0_data[o:o + 4] = (s.vi[it] & 0xffff).to_bytes(4, 'little')
            return f
        if idx == 0x42:  # rinit
            return lambda s, ee=None: setattr(s, 'r', 0x3f800000 | (s.raw_word(fs, fsf) & 0x7fffff))
        if idx == 0x40 or idx == 0x41:  # rnext / rget
            def f(s, ee=None):
                if idx == 0x40:
                    x = s.r & 0x7fffff
                    bit = ((x >> 4) ^ (x >> 22)) & 1
                    s.r = 0x3f800000 | (((x << 1) | bit) & 0x7fffff)
                v = s.r
                for k in range(4):
                    if dest & XYZW[k] and ft:
                        s._set_raw(ft, k, v)
            return f
        if idx == 0x43:  # rxor
            return lambda s, ee=None: setattr(s, 'r', 0x3f800000 | ((s.r ^ s.raw_word(fs, fsf)) & 0x7fffff))
        return None

    def raw_word(self, n, k):
        if hasattr(self, '_rawregs') and (n, k) in self._rawregs:
            return self._rawregs[(n, k)]
        return f2b(self.vf[n][k])

    def write_vf_exact(self, n, dest, vals):
        if n == 0:
            return
        for k in range(4):
            if dest & XYZW[k]:
                raw = self.raw_word_from_val(vals[k])
                self.vf[n][k] = vals[k]
                if hasattr(self, '_rawregs'):
                    self._rawregs.pop((n, k), None)

    def raw_word_from_val(self, v):
        return f2b(v)

    def load_vf_masked(self, n, dest, v128):
        if n == 0:
            return
        for k in range(4):
            if dest & XYZW[k]:
                self._set_raw(n, k, (v128 >> (32 * k)) & M32)

    def store_vf_masked(self, n, dest, addr):
        for k in range(4):
            if dest & XYZW[k]:
                o = addr + 4 * k
                self.mem.vu0_data[o:o + 4] = self.raw_word(n, k).to_bytes(4, 'little')

    # ---------------------------------------------------------- micro mode
    def run_micro(self, start, ee=None, limit=100000):
        pc = start & 0xfff
        delay_target = None
        ending = False
        for _ in range(limit):
            ops = self.micro_cache.get(pc)
            if ops is None:
                lo = int.from_bytes(self.mem.vu0_micro[pc:pc + 4], 'little')
                hi = int.from_bytes(self.mem.vu0_micro[pc + 4:pc + 8], 'little')
                ops = self.decode_micro(pc, lo, hi)
                self.micro_cache[pc] = ops
            upper_fn, lower_fn, ebit, is_branch, ibit_val = ops
            if self.trace:
                self.trace(pc)
            npc = (pc + 8) & 0xfff
            if delay_target is not None:
                npc, delay_target = delay_target, None
            # lower reads must see pre-upper values: snapshot vf when both touch registers
            snap = [list(r) for r in self.vf]
            if upper_fn:
                upper_fn(self)
            if ibit_val is not None:
                self.i = ibit_val
            elif lower_fn:
                cur = self.vf
                self.vf = snap
                res = lower_fn(self)
                lower_vf = self.vf
                self.vf = cur
                # merge: registers written by lower take lower's result
                for n in range(1, 32):
                    if lower_vf[n] != snap[n]:
                        cur[n] = lower_vf[n]
                if isinstance(res, int):
                    delay_target = res & 0xfff
            self.cycle += 1
            if ending:
                return
            if ebit:
                ending = True
            pc = npc
        raise RuntimeError('VU0 micro limit at %#x' % pc)

    def decode_micro(self, pc, lo, hi):
        ibit = (hi >> 31) & 1
        ebit = (hi >> 30) & 1
        up = self.decode_upper_like(hi & 0x01ffffff | (hi & 0x01e00000), macro=False)
        if up is None:
            raise Unimplemented('VU0 upper %#x: %08x' % (pc, hi))
        _, name, dest, fd, fs, ft, bc = up
        upper_fn = (lambda s: s.upper(name, dest, fd, fs, ft, bc)) if name != 'nop' else None
        if ibit:
            return (upper_fn, None, ebit, False, b2f(lo))
        lower_fn = self.decode_lower(pc, lo)
        return (upper_fn, lower_fn, ebit, False, None)

    def decode_lower(self, pc, w):
        op = w >> 25
        dest = (w >> 21) & 15
        it, is_ = (w >> 16) & 31, (w >> 11) & 31
        imm11 = sx(w & 0x7ff, 11)
        imm15 = (w & 0x7ff) | ((w >> 10) & 0x7800)
        btarget = (pc + 8 + imm11 * 8) & 0xfff

        def U(s):
            raise Unimplemented('VU0 lower %#x: %08x' % (pc, w))

        if op == 0x40:
            funct = w & 0x3f
            if funct < 0x3c:
                if funct == 0x30:
                    id_ = (w >> 6) & 31
                    return lambda s: s.set_vi(id_, s.vi[is_] + s.vi[it])
                if funct == 0x31:
                    id_ = (w >> 6) & 31
                    return lambda s: s.set_vi(id_, s.vi[is_] - s.vi[it])
                if funct == 0x32:
                    imm5 = sx((w >> 6) & 31, 5)
                    return lambda s: s.set_vi(it, s.vi[is_] + imm5)
                if funct == 0x34:
                    id_ = (w >> 6) & 31
                    return lambda s: s.set_vi(id_, s.vi[is_] & s.vi[it])
                if funct == 0x35:
                    id_ = (w >> 6) & 31
                    return lambda s: s.set_vi(id_, s.vi[is_] | s.vi[it])
                return U
            idx = ((w >> 4) & 0x7c) | (funct & 3)
            if idx == 0x2f or idx == 0x3b and False:
                return None
            f = self.lower_special2(idx, dest, it, is_, w)
            if f:
                return f
            if idx == 0x6c:  # xgkick: path1 transfer not used by VU0
                return U
            return U
        if op == 0x7f or w == 0x8000033c:  # NOP encodings
            return None
        if op in (0x00, 0x01):  # lq / sq
            load = op == 0
            def f(s):
                base = s.vi[is_] if load else s.vi[it]
                a = ((base + imm11) & 0xff) * 16
                if load:
                    v = int.from_bytes(s.mem.vu0_data[a:a + 16], 'little')
                    s.load_vf_masked(it, dest, v)
                else:
                    s.store_vf_masked(is_, dest, a)
            return f
        if op in (0x04, 0x05):  # ilw / isw
            load = op == 4
            def f(s):
                a = ((s.vi[is_] + imm11) & 0xff) * 16
                for k in range(4):
                    if dest & XYZW[k]:
                        o = a + 4 * k
                        if load:
                            s.set_vi(it, int.from_bytes(s.mem.vu0_data[o:o + 2], 'little'))
                        else:
                            s.mem.vu0_data[o:o + 4] = (s.vi[it] & 0xffff).to_bytes(4, 'little')
            return f
        if op == 0x08:
            return lambda s: s.set_vi(it, s.vi[is_] + imm15)
        if op == 0x09:
            return lambda s: s.set_vi(it, s.vi[is_] - imm15)
        if op == 0x10:  # fceq
            v = w & 0xffffff
            return lambda s: s.set_vi(1, int((s.clip & 0xffffff) == v))
        if op == 0x11:  # fcset
            v = w & 0xffffff
            return lambda s: setattr(s, 'clip', v)
        if op == 0x12:  # fcand
            v = w & 0xffffff
            return lambda s: s.set_vi(1, int((s.clip & v) != 0))
        if op == 0x13:  # fcor
            v = w & 0xffffff
            return lambda s: s.set_vi(1, int(((s.clip | v) & 0xffffff) == 0xffffff))
        if op == 0x1c:  # fcget
            return lambda s: s.set_vi(it, s.clip & 0xfff)
        if op == 0x1a:  # fmand
            return lambda s: s.set_vi(it, s.vi[is_] & s.mac)
        if op == 0x16:  # fsand
            v = (w & 0x7ff) | ((w >> 10) & 0x800)
            return lambda s: s.set_vi(it, s.status & v)
        if op == 0x20:  # b
            return lambda s: btarget
        if op == 0x21:  # bal
            def f(s):
                s.set_vi(it, (pc + 16) // 8)
                return btarget
            return f
        if op == 0x24:  # jr
            return lambda s: (s.vi[is_] * 8) & 0xfff
        if op == 0x25:  # jalr
            def f(s):
                t = (s.vi[is_] * 8) & 0xfff
                s.set_vi(it, (pc + 16) // 8)
                return t
            return f
        conds = {0x28: lambda a, b: a == b, 0x29: lambda a, b: a != b}
        if op in conds:
            c = conds[op]
            return lambda s: btarget if c(s.vi[it], s.vi[is_]) else None
        conds1 = {0x2c: lambda a: a < 0, 0x2d: lambda a: a > 0, 0x2e: lambda a: a <= 0, 0x2f: lambda a: a >= 0}
        if op in conds1:
            c = conds1[op]
            return lambda s: btarget if c(sx(s.vi[is_], 16)) else None
        return U
