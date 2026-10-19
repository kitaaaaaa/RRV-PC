"""Disassemble SLUS_200.02 (Ridge Racer V, PS2) with capstone and resolve address constants.

Writes <out>/listing.txt (one line per instruction: address, word, text, ; resolved const)
and <out>/xrefs.tsv (const_address <tab> instruction_address) for lui+addiu/ori/load/store
pairs, so data addresses can be cross-referenced.

R5900-specific opcodes capstone does not know are emitted with a best-effort mnemonic
(lq/sq, mmi, cop2 macro) so the listing stays aligned.

usage: disasm.py <elf> <out_dir>
"""
import os
import struct
import sys

import capstone

REGS = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
        's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']


def load_elf(path):
    d = open(path, 'rb').read()
    entry, phoff = struct.unpack_from('<II', d, 0x18)
    phnum = struct.unpack_from('<H', d, 0x2c)[0]
    segs = []
    for i in range(phnum):
        t, off, va, pa, fs, ms, fl, al = struct.unpack_from('<8I', d, phoff + 32 * i)
        if t == 1:
            segs.append((va, d[off:off + fs], ms))
    return entry, segs


BC = 'xyzw'
SPECIAL1 = {0x1c: 'vmulq', 0x1d: 'vmaxi', 0x1e: 'vmuli', 0x1f: 'vminii', 0x20: 'vaddq', 0x21: 'vmaddq',
            0x22: 'vaddi', 0x23: 'vmaddi', 0x24: 'vsubq', 0x25: 'vmsubq', 0x26: 'vsubi', 0x27: 'vmsubi',
            0x28: 'vadd', 0x29: 'vmadd', 0x2a: 'vmul', 0x2b: 'vmax', 0x2c: 'vsub', 0x2d: 'vmsub',
            0x2e: 'vopmsub', 0x2f: 'vmini', 0x30: 'viadd', 0x31: 'visub', 0x32: 'viaddi', 0x34: 'viand',
            0x35: 'vior', 0x38: 'vcallms', 0x39: 'vcallmsr'}
SPECIAL2 = {0x10: 'vitof0', 0x11: 'vitof4', 0x12: 'vitof12', 0x13: 'vitof15', 0x14: 'vftoi0',
            0x15: 'vftoi4', 0x16: 'vftoi12', 0x17: 'vftoi15', 0x1c: 'vmulaq', 0x1d: 'vabs', 0x1e: 'vmulai',
            0x1f: 'vclipw', 0x20: 'vaddaq', 0x21: 'vmaddaq', 0x22: 'vaddai', 0x23: 'vmaddai', 0x24: 'vsubaq',
            0x25: 'vmsubaq', 0x26: 'vsubai', 0x27: 'vmsubai', 0x28: 'vadda', 0x29: 'vmadda', 0x2a: 'vmula',
            0x2c: 'vsuba', 0x2d: 'vmsuba', 0x2e: 'vopmula', 0x2f: 'vnop', 0x30: 'vmove', 0x31: 'vmr32',
            0x34: 'vlqi', 0x35: 'vsqi', 0x36: 'vlqd', 0x37: 'vsqd', 0x38: 'vdiv', 0x39: 'vsqrt',
            0x3a: 'vrsqrt', 0x3b: 'vwaitq', 0x3c: 'vmtir', 0x3d: 'vmfir', 0x3e: 'vilwr', 0x3f: 'viswr',
            0x40: 'vrnext', 0x41: 'vrget', 0x42: 'vrinit', 0x43: 'vrxor'}
SPECIAL2_BC = {0x00: 'vadda', 0x04: 'vsuba', 0x08: 'vmadda', 0x0c: 'vmsuba', 0x18: 'vmula'}
SPECIAL1_BC = {0x00: 'vadd', 0x04: 'vsub', 0x08: 'vmadd', 0x0c: 'vmsub', 0x10: 'vmax', 0x14: 'vmini',
               0x18: 'vmul'}


def vu0_macro(word):
    """Decode a COP2 instruction (VU0 macro mode or register transfer)."""
    rs = (word >> 21) & 31
    ft, fs, fd = (word >> 16) & 31, (word >> 11) & 31, (word >> 6) & 31
    imm = word & 0xffff
    if not (word >> 25) & 1:
        name = {1: 'qmfc2', 2: 'cfc2', 5: 'qmtc2', 6: 'ctc2'}.get(rs)
        if name:
            return name + ('.i' if word & 1 else ''), '%s, vi%d' % (REGS[ft], fs) if name in ('cfc2', 'ctc2') \
                else '%s, vf%d' % (REGS[ft], fs)
        if rs == 8:
            simm = imm - 0x10000 if imm & 0x8000 else imm
            return {0: 'bc2f', 1: 'bc2t', 2: 'bc2fl', 3: 'bc2tl'}.get(ft & 3, 'bc2?'), '%+d' % (simm * 4 + 4)
        return 'cop2?', '%#x' % word
    dest = ''.join(c for i, c in enumerate('xyzw') if (word >> (24 - i)) & 1)
    funct = word & 0x3f
    if funct < 0x3c:
        if funct < 0x1c:
            base = funct & ~3
            return '%s%s.%s' % (SPECIAL1_BC.get(base, 'v?%#x' % base), BC[funct & 3], dest), \
                'vf%d, vf%d, vf%d%s' % (fd, fs, ft, BC[funct & 3])
        name = SPECIAL1.get(funct, 'v?%#x' % funct)
        if name.startswith('vi'):
            if name == 'viaddi':
                v = (word >> 6) & 31
                v = v - 32 if v & 16 else v
                return name, 'vi%d, vi%d, %d' % (ft, fs, v)
            return name, 'vi%d, vi%d, vi%d' % (fd, fs, ft)
        if name.startswith('vcallms'):
            return name, '%#x' % (((word >> 6) & 0x7fff) * 8)
        return '%s.%s' % (name, dest), 'vf%d, vf%d, vf%d' % (fd, fs, ft)
    idx = ((word >> 4) & 0x7c) | (funct & 3)
    if idx < 0x10 or 0x18 <= idx < 0x1c:
        base = idx & ~3
        return '%s%s.%s' % (SPECIAL2_BC.get(base, 'v?%#x' % base), BC[idx & 3], dest), \
            'ACC, vf%d, vf%d%s' % (fs, ft, BC[idx & 3])
    name = SPECIAL2.get(idx, 'v?2_%#x' % idx)
    if name in ('vdiv', 'vsqrt', 'vrsqrt'):
        fsf, ftf = (word >> 21) & 3, (word >> 23) & 3
        return name, 'Q, vf%d%s, vf%d%s' % (fs, BC[fsf], ft, BC[ftf])
    if name in ('vmtir', 'vmfir', 'vilwr', 'viswr', 'vlqi', 'vsqi', 'vlqd', 'vsqd'):
        return '%s.%s' % (name, dest), 'vf/vi %d, %d' % (ft, fs)
    return '%s.%s' % (name, dest), 'vf%d, vf%d' % (ft, fs)


def r5900_fallback(word):
    op = word >> 26
    rs, rt = (word >> 21) & 31, (word >> 16) & 31
    imm = word & 0xffff
    simm = imm - 0x10000 if imm & 0x8000 else imm
    if op == 0x1e:
        return 'lq', '%s, %d(%s)' % (REGS[rt], simm, REGS[rs])
    if op == 0x1f:
        return 'sq', '%s, %d(%s)' % (REGS[rt], simm, REGS[rs])
    if op == 0x1c:
        return 'mmi', 'funct=%#x rs=%s rt=%s rd=%s sa=%#x' % (word & 0x3f, REGS[rs], REGS[rt],
                                                               REGS[(word >> 11) & 31], (word >> 6) & 31)
    if op == 0x12:
        return vu0_macro(word)
    if op == 0x36:
        return 'lqc2', 'vf%d, %d(%s)' % (rt, simm, REGS[rs])
    if op == 0x3e:
        return 'sqc2', 'vf%d, %d(%s)' % (rt, simm, REGS[rs])
    return '.word', '%#010x' % word


def main(elf, out):
    os.makedirs(out, exist_ok=True)
    entry, segs = load_elf(elf)
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_LITTLE_ENDIAN)
    lst = open(os.path.join(out, 'listing.txt'), 'w')
    xr = open(os.path.join(out, 'xrefs.tsv'), 'w')
    lst.write('; entry %#x\n' % entry)
    for va, data, ms in segs:
        hi = {}  # reg -> (value, addr) from lui
        for o in range(0, len(data) - 3, 4):
            addr = va + o
            word = struct.unpack_from('<I', data, o)[0]
            op6 = word >> 26
            # R5900-only opcodes that capstone mis-decodes (lq/sq, MMI, COP2 macro, lqc2/sqc2)
            ins = None if op6 in (0x12, 0x1c, 0x1e, 0x1f, 0x36, 0x3e) else next(md.disasm(data[o:o + 4], addr), None)
            if ins is not None:
                mn, opstr = ins.mnemonic, ins.op_str.replace('$', '')
            else:
                mn, opstr = r5900_fallback(word)
            op = word >> 26
            rs, rt = (word >> 21) & 31, (word >> 16) & 31
            imm = word & 0xffff
            simm = imm - 0x10000 if imm & 0x8000 else imm
            note = ''
            if op == 0x0f:  # lui
                hi[rt] = ((imm << 16) & 0xffffffff, addr)
            elif rs in hi and op in (0x09, 0x0d, 0x23, 0x21, 0x25, 0x20, 0x24, 0x2b, 0x29, 0x28, 0x1e, 0x1f,
                                     0x37, 0x3f, 0x31, 0x39, 0x36, 0x3e, 0x27):
                base = hi[rs][0]
                val = (base | imm) if op == 0x0d else (base + simm) & 0xffffffff
                note = ' ; =%#x' % val
                xr.write('%08x\t%08x\n' % (val, addr))
                if op in (0x09, 0x0d) and rt != rs:
                    pass
            # a write to a register invalidates its lui value (approximate: rt for I-type, rd for R-type)
            if op == 0x00:
                rd = (word >> 11) & 31
                hi.pop(rd, None)
            elif op not in (0x0f, 0x2b, 0x29, 0x28, 0x1f, 0x3f, 0x39, 0x3e, 0x04, 0x05, 0x06, 0x07, 0x01,
                            0x02, 0x03):
                if op in (0x09, 0x0d) and rt == rs:
                    hi.pop(rt, None)
                elif op not in (0x09, 0x0d):
                    hi.pop(rt, None)
            if op in (0x02, 0x03) or (op == 0 and (word & 0x3f) in (8, 9)):
                pass
            lst.write('%08x: %08x  %-8s %s%s\n' % (addr, word, mn, opstr, note))
    lst.close()
    xr.close()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
