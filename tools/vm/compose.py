"""Run DMA source chains from interpreter memory into the GS emulator, exactly as queued by the
car draw routine (0x227298):  third[s7] -> chain[4+L] -> patch[L] -> chain[L]  (group 1) and
chain[10+L2] -> patch[4+L2] -> chain[8+L2] on top for group 2.

Chains are walked with real DMA tag semantics (CNT/NEXT/REF/REFS/CALL/RET/END) over VM memory;
VIF1 DIRECT/DIRECTHL payloads and PATH3 (no VIF code) payloads are sent to the GIF parser."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gif

TEX_L = [0, 0, 1, 2, 3]
TEX_L2 = [0, 0, 1]


def walk_chain(mem, tadr, limit=100000):
    """Return the GIF byte stream produced by a DMA source chain starting at tadr."""
    out = bytearray()
    asr = []

    def payload(addr, qwc, vif0, vif1):
        data = mem.read_bytes(addr, qwc * 16)
        cmd = (vif1 >> 24) & 0x7f
        if cmd in (0x50, 0x51) or (vif0 == 0 and vif1 == 0):
            out.extend(data)

    for _ in range(limit):
        tag = mem.read(tadr, 8)
        vif0, vif1 = mem.read(tadr + 8, 4), mem.read(tadr + 12, 4)
        qwc = tag & 0xffff
        tid = (tag >> 28) & 7
        addr = (tag >> 32) & 0xffffffff
        if tid == 1:       # cnt
            payload(tadr + 16, qwc, vif0, vif1)
            tadr += 16 + qwc * 16
        elif tid == 2:     # next
            payload(tadr + 16, qwc, vif0, vif1)
            tadr = addr
        elif tid in (3, 4):  # ref / refs
            payload(addr, qwc, vif0, vif1)
            tadr += 16
        elif tid == 0:     # refe
            payload(addr, qwc, vif0, vif1)
            return bytes(out)
        elif tid == 5:     # call
            payload(tadr + 16, qwc, vif0, vif1)
            asr.append(tadr + 16 + qwc * 16)
            tadr = addr
        elif tid == 6:     # ret
            payload(tadr + 16, qwc, vif0, vif1)
            if not asr:
                return bytes(out)
            tadr = asr.pop()
        else:              # end
            payload(tadr + 16, qwc, vif0, vif1)
            return bytes(out)
    raise RuntimeError('chain too long')


def run_stream(gs, stream):
    o = 0
    while o + 16 <= len(stream):
        o = gs.run_gif(stream, o, len(stream))


def section_ptr(mem, pack_addr, k):
    return mem.read(pack_addr + 4 + 4 * k, 4)


def steady_state_passes(m, slot, mode, frames=4):
    """Replay the per-frame texture sequence the car draw builds (verified from the DMA lists
    produced by running 0x227298, mode 0):
        C[8+L2] P[4+L2] -> glass | C[L] P[L] -> body | C[4+L] -> body pass 2, wheel discs |
        T[mode] -> wheel meshes (model section 4) | C[10+L2] -> glass pass 2
    The relative order of the two layer buckets does not change the state seen by any pass.
    Returns {pass_name: GS snapshot} from the last frame (after VRAM has converged)."""
    rec = 0x1de6440 + slot * 16
    model, chains, patch, third = (m.mem.read(rec + 4 * i, 4) for i in range(4))
    L = TEX_L[mode]
    L2 = TEX_L2[mode] if mode < len(TEX_L2) else None
    sec = lambda pk, k: walk_chain(m.mem, section_ptr(m.mem, pk, k))
    streams = {}
    order = []
    if L2 is not None:
        order += [('C8', sec(chains, 8 + L2)), ('P4', sec(patch, 4 + L2)), ('snap', 'glass')]
    order += [('C0', sec(chains, L)), ('P0', sec(patch, L)), ('snap', 'body'),
              ('C4', sec(chains, 4 + L)), ('snap', 'body2_discs')]
    if mode < m.mem.read(third, 4):
        order += [('T', sec(third, mode)), ('snap', 'wheel_meshes')]
    if L2 is not None:
        order += [('C10', sec(chains, 10 + L2)), ('snap', 'glass2')]
    gs = gif.GS()
    snaps = {}
    prev = None
    for f in range(frames):
        for kind, val in order:
            if kind == 'snap':
                g = gif.GS()
                g.mem.mem[:] = gs.mem.mem
                snaps[val] = g
            else:
                run_stream(gs, val)
        cur = gs.mem.mem.copy()
        if prev is not None and (cur == prev).all():
            break
        prev = cur
    snaps['_frames'] = f + 1
    return snaps


def compose_car(m, slot, mode):
    """Return (gs_group1, gs_group2) built from the car record in VM memory."""
    rec = 0x1de6440 + slot * 16
    model, chains, patch, third = (m.mem.read(rec + 4 * i, 4) for i in range(4))
    L = TEX_L[mode]
    g1 = gif.GS()
    seq = []
    if mode < m.mem.read(third, 4):
        seq.append(section_ptr(m.mem, third, mode))
    seq += [section_ptr(m.mem, chains, 4 + L), section_ptr(m.mem, patch, L), section_ptr(m.mem, chains, L)]
    for p in seq:
        run_stream(g1, walk_chain(m.mem, p))
    g2 = None
    if mode < len(TEX_L2):
        g2 = gif.GS()
        g2.mem.mem[:] = g1.mem.mem
        L2 = TEX_L2[mode]
        for p in (section_ptr(m.mem, chains, 10 + L2), section_ptr(m.mem, patch, 4 + L2),
                  section_ptr(m.mem, chains, 8 + L2)):
            run_stream(g2, walk_chain(m.mem, p))
    return g1, g2
