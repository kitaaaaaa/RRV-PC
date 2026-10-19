# Ridge Racer V (US, SLUS_200.02 v1.10): converted data

Everything here was produced by the scripts in `..\tools` from `..\R5_ALL_unpacked`.
Run them with Python 3.10 (`python -I`) (that Python has numpy, Pillow and numba).

## Conventions
- **Units:** metres. 1 raw game unit = 1/256 m (checked against car dimensions and road widths).
- **Axes:** Y‑up, right‑handed. The PS2 data is right‑handed with Y down, so it is rotated 180° about X, not mirrored. Orientation matches the in‑game course maps.
- **Textures:** PNG with alpha. PS2 alpha 0x80 is mapped to 255.

## tracks/track_00NN
One OBJ/MTL plus `textures/` per track entry. Vertex colours are written as `v x y z r g b`. Object/material names are `p<VU program>_matNNN`:

| prefix | meaning |
|---|---|
| p14 | road‑type surfaces (includes highway/overpass scenery on roads not driven on this course) |
| p18 | general scenery |
| p12 | special strips |
| p24 | billboards (trees, crowds), exported as two crossed quads |

Entry groups (variants of one layout) are listed below. The names assume menu order and are **tentative**:

| entries | course |
|---|---|
| 41, 42, 43 | Sunny Beach |
| 44, 45 | Green Field |
| 47, 48, 49 | Park Town |
| 50, 51 | Outer Pass |
| 53, 55 | Above the City |
| 57, 58 | Bayside Line |
| 60 | Airport Oval |

What the variants differ by (e.g. reverse or time of day) is not confirmed.

Not exported yet:
- VU programs 20 and 4: alpha effects (glows/lights) and a re‑draw pass. Their batch counts are printed by the exporter.
- A few program‑14/12 batches in some variants that reuse earlier uploaded data.
- Collision, AI lines and start positions: likely entries 246‑252 and 1089‑1095, not decoded yet.

## cars/<Name>
`<Name>.obj/.mtl` plus `textures/` (livery variant 0) and `skins/v0`, `v1`, `v2` (the three livery variants, same file names, so they map onto AC skin folders).

The game redraws one shared 8‑bit texture page between parts, so each car has 12 drawings (4 LODs × 3 groups). Each part is mapped to its drawing:
- body LOD0‑3: chains 4‑7
- glass LOD0/1: chains 10/11
- wheel discs: chains 0/1

The other chains (8/9 and the rest) are not base textures and are not used.

One texture is written per (drawing, palette) pair. The paint shading baked into the body indices is kept, with a neutral grey ramp standing in for the runtime paint colour.

The notes below about `skin.png` / `l00`‑`l11` liveries are outdated.

- **Objects:**
  - part00: body, high LOD
  - part01: glass, high LOD
  - part02/03: mid LOD body/glass
  - part04: low LOD
  - part05: low‑poly hull (probably the reflection pass)
  - part06‑13: wheel disc quads, still at the origin
  - last part: untextured pieces
- **Liveries:** each car has 12 livery decal sets (`l00`‑`l11`) × 3 "alt" variants (`v0`‑`v2`). The game picks one at runtime, probably per team or opponent.
- **Body paint:** the game fills the paint colour in at runtime. Those palette entries are empty in the data, so they are replaced with a neutral grey ramp. This is an approximation meant for tinting or re‑skinning in AC.
- **Not done yet:**
  - wheel placement
  - tyres
  - engine/trophy models (entries 542‑672, different container)

## textures_raw
Every standalone GS image upload in non‑track entries (UI, logos, maps, fonts, menu art). Files are named `<entry>_<label>_<upload#>.png`, and each indexed image is paired with its CLUT. Known bad: entry 682 (course‑select screenshot), which uses a different encoding.

## audio
- `music/stream_00NN.wav`: entries 0‑40, stereo, PS‑ADPCM with 0x100‑byte interleave.
- `banks/bank_NNNN/`: VAB banks (909‑1088). One WAV per sample, plus `bank.json` (program/tone mapping, ADSR, root notes).
- `clips/`: mono ADPCM clips (1096, 1097‑1130, 1131).
- **Sample rate:** 44100 Hz is an assumption (the rate isn't stored). If the pitch sounds off, re‑run `tools\export_audio.py ... --rate 48000`.

## Still to do
- Text/scripts
- R5.STR (170 MB stream file)
- Course data (246‑252, 1089‑1095)
- Engine/trophy models
- GIM files (510, 743)
