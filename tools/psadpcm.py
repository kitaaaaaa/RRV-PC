"""Sony PS-ADPCM (VAG) decoder, numba-accelerated."""
import numpy as np
from numba import njit

COEF = np.array([[0.0, 0.0], [60.0, 0.0], [115.0, -52.0], [98.0, -55.0], [122.0, -60.0]]) / 64.0


@njit(cache=True)
def _decode(frames, coef):
    n = frames.shape[0]
    out = np.zeros(n * 28, np.int16)
    h1 = 0.0
    h2 = 0.0
    for f in range(n):
        hdr = frames[f, 0]
        shift = hdr & 15
        filt = (hdr >> 4) & 7
        if filt > 4:
            filt = 0
        if shift > 12:
            shift = 9
        c1 = coef[filt, 0]
        c2 = coef[filt, 1]
        for k in range(28):
            b = np.int64(frames[f, 2 + (k >> 1)])
            nib = (b >> 4) if (k & 1) else (b & 15)
            s = nib - 16 if nib >= 8 else nib
            raw = (s << 12) >> shift
            v = raw + h1 * c1 + h2 * c2
            h2 = h1
            h1 = v
            iv = int(round(v))
            if iv > 32767:
                iv = 32767
            elif iv < -32768:
                iv = -32768
            out[f * 28 + k] = iv
    return out


def decode(data):
    """Decode a mono PS-ADPCM byte stream (multiple of 16 bytes) to int16 samples."""
    a = np.frombuffer(data[:len(data) // 16 * 16], np.uint8).reshape(-1, 16)
    return _decode(a, COEF)


def deinterleave(data, channels, block):
    a = np.frombuffer(data[:len(data) // (block * channels) * block * channels], np.uint8)
    a = a.reshape(-1, channels, block)
    return [a[:, c, :].tobytes() for c in range(channels)]
