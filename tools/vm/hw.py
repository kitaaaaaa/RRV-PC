"""EE hardware registers (0x10000000-0x1000ffff, 0x12000000 GS privileged) for the interpreter.

DMA transfers run synchronously when a channel's CHCR.STR bit is written:
  ch0 VIF0  : VIF codes processed into VU0 (UNPACK, MPG, MSCAL/MSCNT, STCYCL, STMOD, STROW, ITOP...)
  ch1 VIF1  : captured (list of (mode, bytes)) for the GS-side emulator
  ch2 GIF   : captured
  ch8 fromSPR, ch9 toSPR : memory copies
Normal and source-chain modes are supported.  Unknown register accesses are logged."""
import struct

DMA_BASE = {0x8000: 0, 0x9000: 1, 0xa000: 2, 0xb000: 3, 0xb400: 4, 0xc000: 5, 0xc400: 6, 0xc800: 7,
            0xd000: 8, 0xd400: 9}


class Hardware:
    def __init__(self, mem, vu0):
        self.mem = mem
        self.vu0 = vu0
        self.regs = {}
        self.captured = []   # (channel, bytes)
        self.log = []
        self.timer = 0
        self.vif0 = {'cycle_cl': 1, 'cycle_wl': 1, 'mode': 0, 'row': [0, 0, 0, 0], 'top': 0, 'itop': 0}
        self.ee = None

    def read(self, addr, size):
        if 0x10000000 <= addr < 0x10002000:  # timers
            self.timer = (self.timer + 64) & 0xffff
            if (addr & 0x7ff) == 0:
                return self.timer
        if (addr & 0xffff0000) == 0x10000000 and (addr & 0xff00) in DMA_BASE.keys() | {0}:
            pass
        v = self.regs.get(addr, 0)
        off = addr & 0xffff
        if (off & ~0xff) in DMA_BASE and (off & 0xff) == 0:
            v &= ~0x100  # transfers complete synchronously
        return v

    def write(self, addr, size, value):
        self.regs[addr] = value
        off = addr & 0xffff
        base = off & ~0xff
        if 0x10000000 <= addr < 0x10010000 and base in DMA_BASE and (off & 0xff) == 0 and value & 0x100:
            self.run_dma(DMA_BASE[base], 0x10000000 | base, value)
            self.regs[addr] = value & ~0x100

    # ------------------------------------------------------------ DMA
    def chreg(self, base, o):
        return self.regs.get(base | o, 0)

    def run_dma(self, ch, base, chcr):
        self._run_dma(ch, base, chcr)
        if ch == 0 and getattr(self, '_vif0_buf', None):
            buf, self._vif0_buf = bytes(self._vif0_buf), None
            self.vif0_process(buf)

    def _run_dma(self, ch, base, chcr):
        mode = (chcr >> 2) & 3
        if ch == 8:
            return self.from_spr(base, mode)
        if ch == 9 and mode == 0:
            sadr = self.chreg(base, 0x80) & 0x3ff0
            madr = self.chreg(base, 0x10)
            qwc = self.chreg(base, 0x20) & 0xffff
            data = self.mem.read_bytes(madr, qwc * 16)
            for i in range(qwc):  # SPR address wraps at 16 KB
                a = (sadr + 16 * i) & 0x3ff0
                self.mem.spr[a:a + 16] = data[16 * i:16 * i + 16]
            # hardware write-back: MADR/SADR advance, QWC reaches 0
            self.regs[base | 0x10] = madr + qwc * 16
            self.regs[base | 0x80] = (sadr + qwc * 16) & 0x3ff0
            self.regs[base | 0x20] = 0
            return
        madr = self.chreg(base, 0x10)
        qwc = self.chreg(base, 0x20) & 0xffff
        sink = self.sink(ch, base)
        if mode == 0:  # normal
            sink(self.mem.read_bytes(madr, qwc * 16), madr)
            self.regs[base | 0x10] = madr + qwc * 16
            self.regs[base | 0x20] = 0
            return
        if mode == 1:  # source chain
            tadr = self.chreg(base, 0x30)
            asr = []
            tte = (chcr >> 6) & 1
            if qwc:
                sink(self.mem.read_bytes(madr, qwc * 16), madr)
            for _ in range(1_000_000):
                tag = self.mem.read(tadr, 8)
                tq = tag & 0xffff
                tid = (tag >> 28) & 7
                taddr = (tag >> 32) & 0x7ffffff0 | ((tag >> 63) << 31)
                taddr = (tag >> 32) & 0xffffffff
                if tte:
                    sink(self.mem.read_bytes(tadr + 8, 8), None, tag_only=True)
                if tid == 0:      # refe
                    sink(self.mem.read_bytes(taddr, tq * 16), taddr)
                    return
                if tid == 1:      # cnt
                    sink(self.mem.read_bytes(tadr + 16, tq * 16), tadr + 16)
                    tadr = tadr + 16 + tq * 16
                elif tid == 2:    # next
                    sink(self.mem.read_bytes(tadr + 16, tq * 16), tadr + 16)
                    tadr = taddr
                elif tid in (3, 4):  # ref / refs
                    sink(self.mem.read_bytes(taddr, tq * 16), taddr)
                    tadr += 16
                elif tid == 5:    # call
                    sink(self.mem.read_bytes(tadr + 16, tq * 16), tadr + 16)
                    asr.append(tadr + 16 + tq * 16)
                    tadr = taddr
                elif tid == 6:    # ret
                    sink(self.mem.read_bytes(tadr + 16, tq * 16), tadr + 16)
                    if not asr:
                        return
                    tadr = asr.pop()
                else:             # end
                    sink(self.mem.read_bytes(tadr + 16, tq * 16), tadr + 16)
                    return
            raise RuntimeError('DMA chain too long')
        raise NotImplementedError('DMA mode %d on ch%d' % (mode, ch))

    def sink(self, ch, base, ):
        if ch == 9:  # toSPR
            sadr = self.chreg(base, 0x80) & 0x3ff0
            state = {'s': sadr}
            def f(data, src, tag_only=False):
                if tag_only:
                    return
                self.mem.spr[state['s']:state['s'] + len(data)] = data
                state['s'] = (state['s'] + len(data)) & 0x3fff
            return f
        if ch == 8:  # fromSPR: normal mode copies SPR -> RAM at MADR
            sadr = self.chreg(base, 0x80) & 0x3ff0
            madr = self.chreg(base, 0x10)
            qwc = self.chreg(base, 0x20) & 0xffff
            def f(data, src, tag_only=False):
                pass
            # perform directly (fromSPR uses MADR as destination)
            self.mem.write_bytes(madr, bytes(self.mem.spr[sadr:sadr + qwc * 16]))
            return lambda *a, **k: None
        if ch == 0:
            # VIF0: accumulate the whole transfer (tag VIF-code halves + payloads) into one stream
            self._vif0_buf = bytearray()
            def f(data, src, tag_only=False):
                self._vif0_buf.extend(data)
            return f
        return lambda data, src, tag_only=False: self.captured.append((ch, bytes(data)))

    def from_spr(self, base, mode):
        sadr = self.chreg(base, 0x80) & 0x3ff0
        spr = self.mem.spr
        if mode == 0:
            madr = self.chreg(base, 0x10)
            qwc = self.chreg(base, 0x20) & 0xffff
            out = b''.join(bytes(spr[(sadr + 16 * i) & 0x3ff0:((sadr + 16 * i) & 0x3ff0) + 16]) for i in range(qwc))
            self.mem.write_bytes(madr, out)
            self.regs[base | 0x10] = madr + qwc * 16
            self.regs[base | 0x80] = (sadr + qwc * 16) & 0x3ff0
            self.regs[base | 0x20] = 0
            return
        if mode != 1:
            raise NotImplementedError('fromSPR mode %d' % mode)
        # destination chain: tags are read from SPR; each tag's ADDR is the RAM destination
        for _ in range(100000):
            tag = int.from_bytes(spr[sadr:sadr + 8], 'little')
            qwc = tag & 0xffff
            tid = (tag >> 28) & 7
            addr = (tag >> 32) & 0x7ffffff0
            sadr = (sadr + 16) & 0x3ff0
            out = bytearray()
            for i in range(qwc):
                out += spr[sadr:sadr + 16]
                sadr = (sadr + 16) & 0x3ff0
            if qwc:
                self.mem.write_bytes(addr, bytes(out))
            if tid == 7:
                return
            if tid not in (0, 1):
                raise NotImplementedError('fromSPR dest-chain tag id %d' % tid)
        raise RuntimeError('fromSPR chain too long')

    # ------------------------------------------------------------ VIF0
    def vif0_process(self, data):
        v = self.vif0
        o = 0
        n = len(data)
        while o + 4 <= n:
            code = int.from_bytes(data[o:o + 4], 'little')
            cmd = (code >> 24) & 0x7f
            imm = code & 0xffff
            num = (code >> 16) & 0xff
            o += 4
            if cmd == 0x00 or cmd in (0x10, 0x11, 0x13, 0x06, 0x07):
                continue
            if cmd == 0x01:
                v['cycle_cl'], v['cycle_wl'] = imm & 0xff, (imm >> 8) & 0xff
            elif cmd == 0x04:
                v['itop'] = imm & 0x3ff
            elif cmd == 0x05:
                v['mode'] = imm & 3
            elif cmd == 0x20:
                o += 4
            elif cmd in (0x30, 0x31):
                if cmd == 0x30:
                    v['row'] = list(struct.unpack_from('<4I', data, o))
                o += 16
            elif cmd == 0x4a:  # MPG
                cnt = (num or 256) * 8
                addr = imm * 8
                self.mem.vu0_micro[addr:addr + cnt] = data[o:o + cnt]
                self.vu0.micro_cache.clear()
                o += cnt
            elif cmd in (0x14, 0x15, 0x17):  # MSCAL / MSCALF / MSCNT
                start = imm * 8 if cmd != 0x17 else getattr(self.vu0, 'last_pc', 0)
                self.vu0.vi[15] = v['itop']
                self.vu0.run_micro(start)
            elif cmd & 0x60 == 0x60:
                o += self.vif0_unpack(code, data, o)
            elif cmd in (0x50, 0x51):
                raise NotImplementedError('DIRECT on VIF0')
            else:
                raise NotImplementedError('VIF0 cmd %#x' % cmd)

    def vif0_unpack(self, code, data, o):
        v = self.vif0
        cmd = (code >> 24) & 0x7f
        imm = code & 0xffff
        num = ((code >> 16) & 0xff) or 256
        fmt = cmd & 0xf
        usn = (imm >> 14) & 1
        addr = imm & 0x3ff
        if (imm >> 15) & 1:
            addr += v.get('tops', 0)
        vn, vl = (fmt >> 2) & 3, fmt & 3
        comps = vn + 1
        bits = (32, 16, 8, 5)[vl]
        if bits == 5:
            size = num * 2
        else:
            size = num * comps * bits // 8
        size = (size + 3) & ~3
        raw = data[o:o + size]
        cl, wl = v['cycle_cl'] or 1, v['cycle_wl'] or 1
        dst = addr
        src = 0
        for k in range(num):
            if bits == 5:
                p = int.from_bytes(raw[k * 2:k * 2 + 2], 'little')
                vals = [(p & 31) << 3, ((p >> 5) & 31) << 3, ((p >> 10) & 31) << 3, (p >> 15) << 7]
            else:
                step = bits // 8
                vals = []
                for c in range(comps):
                    b = raw[(k * comps + c) * step:(k * comps + c + 1) * step]
                    x = int.from_bytes(b, 'little')
                    if not usn and bits < 32 and x >> (bits - 1):
                        x -= 1 << bits
                    vals.append(x & 0xffffffff)
                if comps == 1:
                    vals = vals * 4
                elif comps in (2, 3):
                    # lanes beyond the unpacked components are not specified here; leave memory
                    # untouched and record that this path ran so results depending on it can be checked
                    self.log.append(('vif0_partial_unpack', fmt, num))
                    vals = vals + [None] * (4 - comps)
            a = (dst & 0xff) * 16
            for c in range(4):
                val = vals[c] if c < len(vals) else None
                if val is None:
                    continue
                if v['mode'] == 1:
                    val = (val + v['row'][c]) & 0xffffffff
                elif v['mode'] == 2:
                    val = (val + v['row'][c]) & 0xffffffff
                    v['row'][c] = val
                self.mem.vu0_data[a + 4 * c:a + 4 * c + 4] = val.to_bytes(4, 'little')
            dst += 1
            if cl > wl:
                pass
            elif wl > cl and (k + 1) % wl == 0:
                pass
            if cl > wl and (k + 1) % wl == 0:
                dst += cl - wl
        return size
