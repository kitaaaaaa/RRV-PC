"""Ridge Racer V R5.ALL LZ decompressor.

Header (10 bytes): u8 offset_bits, u8 flag_bytes, u32 unpacked_size, u32 packed_size.
Stream: groups of `flag_bytes` flag bytes (bits LSB-first, 1 = literal byte,
0 = 2-byte big-endian match: high (16-offset_bits) bits = length, low bits = distance;
a zero field means its maximum, 1 << field_bits).
"""
import struct, sys

def decompress(data):
    obits, fbytes = data[0], data[1]
    usize, csize = struct.unpack_from('<II', data, 2)
    src = data[10:10 + csize]
    out = bytearray()
    p = 0
    dmask = (1 << obits) - 1
    while len(out) < usize and p < len(src):
        flags = int.from_bytes(src[p:p + fbytes], 'little')
        p += fbytes
        for _ in range(fbytes * 8):
            if len(out) >= usize or p >= len(src):
                break
            if flags & 1:
                out.append(src[p]); p += 1
            else:
                w = (src[p] << 8) | src[p + 1]; p += 2
                length = (w >> obits) or (1 << (16 - obits))  # 0 = max length
                dist = (w & dmask) or (1 << obits)  # 0 = full window
                start = len(out) - dist
                if start < 0:
                    raise ValueError('bad distance')
                for i in range(length):
                    out.append(out[start + i])
            flags >>= 1
    return bytes(out), p, usize, csize

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    out, p, usize, csize = decompress(d)
    print(len(out), usize, p, csize)
