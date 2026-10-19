"""Export Ridge Racer V audio from decompressed R5.ALL entries to WAV.

  0..40      stereo PS-ADPCM streams, 0x100-byte interleave (music)
  909..1088  VAB sound banks (pBAV v7): one WAV per VAG + bank.json with program/tone data
  1096, 1131 packs of mono PS-ADPCM clips
  1097..1130 single mono PS-ADPCM clips

The sample rate is not stored for streams/clips; RATE below is an assumption (44100 Hz).
If pitch sounds wrong, re-export with --rate 48000.

usage: export_audio.py <unpacked_dir> <out_dir> [--rate N]
"""
import json
import os
import struct
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack
import psadpcm

RATE = 44100


def write_wav(path, chans, rate):
    n = min(len(c) for c in chans)
    inter = np.stack([c[:n] for c in chans], 1).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(len(chans))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(inter.tobytes())


def trim_vag(raw):
    """Stop a mono ADPCM stream at the first frame with the end flag (bit 0)."""
    for o in range(0, len(raw) - 15, 16):
        if raw[o + 1] & 1 and o > 0:
            return raw[:o + 16]
    return raw


def parse_vab(vh, vb):
    magic, ver, vid, total = struct.unpack_from('<4sIII', vh, 0)
    _, nprog, ntone, nvag, mvol, mpan, a1, a2 = struct.unpack_from('<HHHHBBBB', vh, 16)
    progs = []
    o = 32
    for p in range(128):
        tones, vol, prior, mode, pan = struct.unpack_from('<BBBBB', vh, o)
        progs.append(dict(tones=tones, vol=vol, priority=prior, mode=mode, pan=pan))
        o += 16
    tone_attr = []
    nused = sum(1 for p in progs if p['tones'])
    for k in range(nused * 16):
        t = struct.unpack_from('<16BHHHH', vh, o + k * 32)
        if t[19] == 0:
            continue  # unused tone slot
        tone_attr.append(dict(priority=t[0], mode=t[1], vol=t[2], pan=t[3], center=t[4], shift=t[5],
                              min_note=t[6], max_note=t[7], vib_w=t[8], vib_t=t[9], por_w=t[10],
                              por_t=t[11], pbmin=t[12], pbmax=t[13], adsr1=t[16], adsr2=t[17],
                              program=t[18], vag=t[19]))
    o += nused * 16 * 32
    sizes = struct.unpack_from('<256H', vh, o)
    vags = []
    pos = 0
    for k in range(1, nvag + 1):
        sz = sizes[k] * 8
        vags.append(vb[pos:pos + sz])
        pos += sz
    return dict(version=ver, id=vid, programs=nprog, tones=ntone, vags=nvag,
                master_vol=mvol, master_pan=mpan), progs, tone_attr, vags


def main(unpacked, out, rate=RATE):
    def entry(i):
        return open(os.path.join(unpacked, '%04d.bin' % i), 'rb').read()

    os.makedirs(os.path.join(out, 'music'), exist_ok=True)
    for i in range(0, 41):
        d = entry(i)
        if len(d) < 64:
            continue
        L, R = psadpcm.deinterleave(d, 2, 0x100)
        write_wav(os.path.join(out, 'music', 'stream_%04d.wav' % i), [psadpcm.decode(L), psadpcm.decode(R)], rate)

    vdir = os.path.join(out, 'banks')
    os.makedirs(vdir, exist_ok=True)
    nb = 0
    for i in range(909, 1089):
        d = entry(i)
        secs = pack.sections(d)
        if not secs or d[secs[0][0]:secs[0][0] + 4] != b'pBAV':
            continue
        vh = d[secs[0][0]:secs[0][0] + secs[0][1]]
        vb = d[secs[-1][0]:secs[-1][0] + secs[-1][1]]
        hdr, progs, tones, vags = parse_vab(vh, vb)
        bdir = os.path.join(vdir, 'bank_%04d' % i)
        os.makedirs(bdir, exist_ok=True)
        for k, v in enumerate(vags):
            if len(v) >= 32:
                write_wav(os.path.join(bdir, 'vag_%03d.wav' % (k + 1)), [psadpcm.decode(trim_vag(v))], rate)
        extra = [s for s in secs[1:-1]]
        json.dump(dict(header=hdr, programs=[dict(index=n, **p) for n, p in enumerate(progs) if p['tones']],
                       tones=tones,
                       extra_sections=[d[o:o + s].hex() for o, s in extra]),
                  open(os.path.join(bdir, 'bank.json'), 'w'), indent=1)
        nb += 1

    cdir = os.path.join(out, 'clips')
    os.makedirs(cdir, exist_ok=True)
    for i in (1096, 1131):
        d = entry(i)
        for k, (o, s) in enumerate(pack.sections(d)):
            write_wav(os.path.join(cdir, 'clip_%04d_%02d.wav' % (i, k)), [psadpcm.decode(d[o:o + s])], rate)
    for i in range(1097, 1131):
        d = entry(i)
        if len(d) >= 32:
            write_wav(os.path.join(cdir, 'clip_%04d.wav' % i), [psadpcm.decode(d)], rate)
    print('banks:', nb)


if __name__ == '__main__':
    a = sys.argv[1:]
    r = RATE
    if '--rate' in a:
        r = int(a[a.index('--rate') + 1])
        a = a[:a.index('--rate')]
    main(a[0], a[1], r)
