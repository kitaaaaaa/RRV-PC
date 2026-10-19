"""Generic container used throughout R5.ALL: u32 count, u32 offsets[count], padded to 16;
section i runs to the next offset, the last one to end of data."""
import struct

def sections(data):
    """Return list of (offset, size), or None if data does not look like a pack."""
    if len(data) < 16:
        return None
    n = struct.unpack_from('<I', data)[0]
    if not (0 < n < 8192) or 4 + n * 4 > len(data):
        return None
    offs = list(struct.unpack_from('<%dI' % n, data, 4))
    hdr = (4 + n * 4 + 15) & ~15
    if offs[0] < hdr or offs[0] > hdr + 16 or offs[-1] > len(data):
        return None
    if any(b < a for a, b in zip(offs, offs[1:])):
        return None
    ends = offs[1:] + [len(data)]
    return [(a, b - a) for a, b in zip(offs, ends)]
