"""A QR code for a WireGuard config, with nothing but Python: a phone joins the VPN by
scanning it, and a desktop without `qrencode` should still be able to show one.

Byte mode, error correction level L, versions 1 to 20 (858 bytes: a config has about 300).
ISO/IEC 18004; tests/ compares every module with what qrencode makes of the same text.
"""
import struct
import zlib

from .util import KiwiError

# version -> (EC codewords per block, [(blocks, data codewords per block), ...]) at level L
_BLOCKS = {
    1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]), 5: (26, [(1, 108)]),
    6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]), 9: (30, [(2, 116)]),
    10: (18, [(2, 68), (2, 69)]), 11: (20, [(4, 81)]), 12: (24, [(2, 92), (2, 93)]), 13: (26, [(4, 107)]),
    14: (30, [(3, 115), (1, 116)]), 15: (22, [(5, 87), (1, 88)]), 16: (24, [(5, 98), (1, 99)]),
    17: (28, [(1, 107), (5, 108)]), 18: (30, [(5, 120), (1, 121)]), 19: (28, [(3, 113), (4, 114)]),
    20: (28, [(3, 107), (5, 108)]),
}
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
    9: [6, 26, 46], 10: [6, 28, 50], 11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
    15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78], 18: [6, 30, 56, 82], 19: [6, 30, 58, 86],
    20: [6, 34, 62, 90],
}

# GF(256) with the polynomial x^8 + x^4 + x^3 + x^2 + 1
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _rs(data, n):
    """The n Reed-Solomon error correction codewords of a block."""
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _EXP[_LOG[g] + i] if g else 0
        gen = nxt
    rem = [0] * n
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for j in range(n):
                if gen[j + 1]:
                    rem[j] ^= _EXP[_LOG[gen[j + 1]] + _LOG[factor]]
    return rem


def _capacity(version):
    ec, groups = _BLOCKS[version]
    return sum(b * d for b, d in groups)


def version_for(length):
    for v in sorted(_BLOCKS):
        if length + (2 if v < 10 else 3) <= _capacity(v):
            return v
    raise KiwiError("too long for a QR code here (%d bytes)" % length)


def _codewords(data, version):
    """The data with its header and padding, split in blocks, each with its error
    correction, interleaved: the final sequence of codewords."""
    bits = [0, 1, 0, 0]
    n = len(data)
    for i in range(7 if version < 10 else 15, -1, -1):
        bits.append((n >> i) & 1)
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    total = _capacity(version) * 8
    bits += [0] * min(4, total - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    words += [pad[i % 2] for i in range(_capacity(version) - len(words))]
    ec, groups = _BLOCKS[version]
    blocks, at = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[at:at + size])
            at += size
    eccs = [_rs(b, ec) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec):
        out += [e[i] for e in eccs]
    return out


def _bch(value, bits, gen, gen_bits):
    rem = value << (gen_bits - 1)
    for i in range(bits - 1, -1, -1):
        if rem & (1 << (i + gen_bits - 1)):
            rem ^= gen << i
    return (value << (gen_bits - 1)) | rem


def _frame(version):
    """(modules, reserved): the function patterns of an empty symbol."""
    size = 17 + 4 * version
    m = [[0] * size for _ in range(size)]
    res = [[False] * size for _ in range(size)]

    def put(r, c, v):
        if 0 <= r < size and 0 <= c < size:
            m[r][c], res[r][c] = v, True
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):          # finders with their separators
        for r in range(-1, 8):
            for c in range(-1, 8):
                inside = 0 <= r <= 6 and 0 <= c <= 6
                dark = inside and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                put(r0 + r, c0 + c, 1 if dark else 0)
    pos = _ALIGN[version]
    for r in pos:                                                   # alignment patterns
        for c in pos:
            if res[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(r + dr, c + dc, 1 if max(abs(dr), abs(dc)) != 1 else 0)
    for i in range(8, size - 8):                                    # timing
        if not res[6][i]:
            put(6, i, 1 - i % 2)
        if not res[i][6]:
            put(i, 6, 1 - i % 2)
    put(size - 8, 8, 1)                                             # the dark module
    for i in range(9):                                              # format areas
        if not res[8][i]:
            put(8, i, 0)
        if not res[i][8]:
            put(i, 8, 0)
    for i in range(8):
        put(8, size - 1 - i, 0)
    for i in range(7):                                              # (the eighth from the bottom is the dark module)
        put(size - 1 - i, 8, 0)
    if version >= 7:                                                # version information
        info = _bch(version, 6, 0x1F25, 13)
        for i in range(18):
            bit = (info >> i) & 1
            put(i // 3, size - 11 + i % 3, bit)
            put(size - 11 + i % 3, i // 3, bit)
    return m, res


_MASKS = (
    lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _format(m, mask):
    size = len(m)
    info = _bch((0b01 << 3) | mask, 5, 0x537, 11) ^ 0x5412          # level L is 01
    for i in range(15):
        bit = (info >> i) & 1
        # around the top left finder
        if i < 6:
            m[i][8] = bit
        elif i < 8:
            m[i + 1][8] = bit
        elif i == 8:
            m[8][7] = bit
        else:
            m[8][14 - i] = bit
        # split over the other two
        if i < 8:
            m[8][size - 1 - i] = bit
        else:
            m[size - 15 + i][8] = bit


def _penalty(m):
    size = len(m)
    score = 0
    for lines in (m, list(zip(*m))):
        for line in lines:
            run, last = 0, None
            for v in line:                                           # runs of five or more
                if v == last:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run, last = 1, v
            s = "".join(map(str, line))                              # finder-like patterns
            score += 40 * (_count(s, "10111010000") + _count(s, "00001011101"))
    for r in range(size - 1):                                        # 2x2 blocks
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(map(sum, m)) * 100 // (size * size)                   # balance
    score += 10 * (abs(dark - 50) // 5)
    return score


def _count(s, pattern):
    n, at = 0, s.find(pattern)
    while at >= 0:
        n, at = n + 1, s.find(pattern, at + 1)
    return n


def matrix(text, mask=None):
    """The symbol as rows of 0 and 1 (1 is dark). `mask`: one of the eight, else the best."""
    data = text.encode() if isinstance(text, str) else bytes(text)
    version = version_for(len(data))
    frame, res = _frame(version)
    size = len(frame)
    bits = []
    for word in _codewords(data, version):
        bits += [(word >> i) & 1 for i in range(7, -1, -1)]
    cells, col, up = [], size - 1, True                             # the zigzag from the bottom right
    while col > 0:
        if col == 6:
            col -= 1
        for r in (range(size - 1, -1, -1) if up else range(size)):
            for c in (col, col - 1):
                if not res[r][c]:
                    cells.append((r, c))
        col, up = col - 2, not up
    bits += [0] * (len(cells) - len(bits))                          # the remainder bits
    best = None
    for k in (range(8) if mask is None else [mask]):
        m = [row[:] for row in frame]
        for (r, c), bit in zip(cells, bits):
            m[r][c] = bit ^ (1 if _MASKS[k](r, c) else 0)
        _format(m, k)
        p = _penalty(m)
        if best is None or p < best[0]:
            best = (p, m)
    return best[1]


def svg(text, border=2):
    """The symbol as an SVG image."""
    m = matrix(text)
    n = len(m) + 2 * border
    path = "".join("M%d,%dh1v1h-1z" % (c + border, r + border)
                   for r, row in enumerate(m) for c, v in enumerate(row) if v)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="%d" height="%d" fill="#fff"/><path d="%s" fill="#000"/></svg>' % (n, n, n, n, path))


def text_blocks(text, border=2):
    """The symbol for a terminal, two rows of modules per line."""
    m = matrix(text)
    size = len(m) + 2 * border
    grid = [[0] * size for _ in range(size)]
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            grid[r + border][c + border] = v
    if size % 2:
        grid.append([0] * size)
    # white blocks on black, said in colours, so that it reads the same on a light and a dark terminal
    chars = {(0, 0): "\u2588", (0, 1): "\u2580", (1, 0): "\u2584", (1, 1): " "}
    return "\n".join("\033[40;97m" + "".join(chars[(grid[r][c], grid[r + 1][c])] for c in range(size)) + "\033[0m"
                     for r in range(0, len(grid), 2))


def png(text, scale=8, border=2):
    """The symbol as a PNG image (grayscale), `scale` pixels per module."""
    m = matrix(text)
    size = (len(m) + 2 * border) * scale
    rows = []
    blank = b"\xff" * size
    for _ in range(border * scale):
        rows.append(blank)
    for row in m:
        line = b"\xff" * (border * scale) + b"".join((b"\x00" if v else b"\xff") * scale for v in row) + b"\xff" * (border * scale)
        rows += [line] * scale
    for _ in range(border * scale):
        rows.append(blank)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
