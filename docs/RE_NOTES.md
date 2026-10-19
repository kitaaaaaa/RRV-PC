# Reverse-engineering notes (SLUS_200.02)

Traced 2026-10-18 with tools/disasm.py (capstone + custom R5900/VU0 decode). gp = 0x33a670.

- Interpreter: tools/vm (ee.py, vu.py, mem.py, hw.py, machine.py, run_carload.py, run_cardraw.py, compose.py). Runs game code with only libcdvd hooked (0x2c7780 read from BIN, 0x2c8038 sync, 0x2c8488, 0x2c8858). R5.ALL LBA 83743, load heap 0x368500 (game value). DMA needs MADR/SADR write-back and fromSPR destination-chain mode.
- Car loader 0x22c730(car,a1,a2): model=269+10*car, chains=+1+a1, patch=+4+a1, third=+7+a2; record 0x1de6440+slot*16. Menu passes a1/a2 = save bytes 0x1e2d1e7/0x1e2d1e8 for cars<12.
- Colours: 0x22be78(slot,c0..c3) regenerates palettes in patch data (reads R,G,B words only). Default presets = table 0x3055f8 (12 bytes/row: 4 RGB; rows 0-11 standard-car presets, 12-20 special cars). Save struct 0x1e2d160 (+0x90 colour slots, 0x89 slot, 0x8a preset). Race number: 0x22bc28(slot,n), digit tiles in ELF at 0x2f5820 (LOD0, 800 B/digit).
- DMA list insert 0x21f980 pushes to FRONT (lists execute reverse of insertion). Car draw 0x227298 executes: C[8+L2] P[4+L2] glass | C[L] P[L] body | C[4+L] body pass2 + wheel discs | T[mode] wheel meshes (model sec4) | C[10+L2] glass pass2. Steady state converges in 2 frames (compose.steady_state_passes).
- Part draw 0x222eb8 writes ALPHA_2 = gp-0x6ae8 (0x68: Cs*FIX+Cd; 0x58 for modes 1-2) with FIX = byte 7 of part table entry. Multi-pass incl. FBMSK alpha-only pass (0x227838). Exact final colour needs a real GS rasterizer (not built yet).
- Fiaro preset 0 + number 99 reproduces screenshot paint/T-A RACING/NAVIWAY/99 box; stickers (chain 4 = pass 2) not yet composited.
