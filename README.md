# RRV-PC

Tools for extracting, decoding and converting **Ridge Racer V** (PS2, US `SLUS_200.02` v1.10) data, for Assetto Corsa conversion. The repo also contains an EE/VU0 interpreter that runs the game's own code to reproduce its car textures exactly.

**No game data is included.** You need your own disc image. All paths in the scripts point at a local working folder (`J:\RIDGERACERV`); adjust them for your setup.

Python 3.10+ with `numpy`, `Pillow`, `numba` and `capstone`.

## Pipeline

| step | script |
|---|---|
| BIN (MODE2/2352) → ISO files | `tools/bin2files.py` |
| `R5.ALL` → 1135 entries, using the file table in the ELF at file offset `0x10D258` | `tools/unpack_all.py`, `tools/unpack_r5.py` |
| Entry decompression (custom LZSS: 8 flag bytes LSB first; 16‑bit match = `len<<obits \| dist`; zero means max) | `tools/r5lz.py` |
| Generic pack container (`u32 count, u32 offsets[]`) | `tools/pack.py` |
| GS local memory model (swizzled PSMCT32/16, T8, T4, CLUTs) | `tools/gsmem.py` |
| GIF/GS packet interpreter (sprites, uploads, flat triangles) | `tools/gif.py` |
| VIF parser | `tools/vif.py` |
| Tracks → OBJ/MTL/PNG (metres, Y‑up, vertex colours) | `tools/export_track.py`, `tools/r5track.py` |
| Cars → OBJ/MTL/PNG | `tools/export_car.py`, `tools/r5car.py` |
| Standalone texture uploads → PNG | `tools/dump_textures.py` |
| Audio → WAV (music streams, VAB banks, clips) | `tools/export_audio.py`, `tools/psadpcm.py` |
| R5900 + VU0 disassembler (capstone plus custom decode) | `tools/disasm.py` |
| Preview renderer | `tools/render_obj.py` |

## EE/VU0 interpreter (`tools/vm`)

The interpreter runs the game's own code, with only the libcdvd CD layer replaced by reads from the BIN image.

- `ee.py`: R5900 integer ISA, FPU, MMI subset and COP2 macro mode.
- `vu.py`: VU0 micro‑mode interpreter.
- `mem.py`, `hw.py`: memory map, plus DMA for scratchpad (incl. destination chain), VIF0 and GIF capture.
- `machine.py`: loads the ELF and calls game functions.
- `run_carload.py`: the game's car loader.
- `run_cardraw.py`: the game's colour, race‑number and car‑draw routines.
- `compose.py`: replays the texture DMA chains in the exact order the game queues them.

See `docs/RE_NOTES.md` for the reverse‑engineering facts, with code addresses.

## Status

**Done**
- Archive extraction
- Tracks (geometry and textures)
- UI textures and audio
- Car geometry
- Car base livery, palettes and race numbers, reproduced by the game's code

**In progress**
- GS rasterizer to composite the car's multi‑pass rendering (sticker/reflection pass)

**Not done**
- Track collision/AI data
- R5.STR
- Text
