"""Bagimlilik gerektirmeyen minik QR kod ureteci.

Yalnizca bayt modu, ECC seviyesi M, versiyon 1-10 (v10-M ~211 bayt tasir - bir Wi-Fi
baglanti dizesi veya panel URL'si icin fazlasiyla yeterli). ISO/IEC 18004'un herkese acik,
uygulamadan bagimsiz yerlesim/BCH/Reed-Solomon kurallarini uygular (her QR kutuphanesi ayni
kurallari uygular - bunlar standardin kendisidir). SVG'ye cizer; harici paket gerekmez.

Doğruluğu bu depoda, uretilen kod OpenCV'nin QR cozucusuyle onlarca rastgele/sinir-durum
girdi uzerinde carpraz dogrulanarak test edilmistir (bkz. tests/test_qrcode.py).
"""
import re

# ---------------------------------------------------------------- GF(256) / Reed-Solomon
_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(nsym):
    g = [1]
    for i in range(nsym):
        nxt = [0] * (len(g) + 1)
        root = _EXP[i]
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _gmul(c, root)
        g = nxt
    return g


def _rs_encode(data, nsym):
    """LFSR ile Reed-Solomon hata duzeltme kod sozculerini hesaplar (veri boyu kadar kaydirmali bolme)."""
    gen = _rs_generator(nsym)
    buf = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = buf[i]
        if coef:
            for j, g in enumerate(gen):
                buf[i + j] ^= _gmul(g, coef)
    return buf[len(data):]


# ---------------------------------------------------------------- versiyon 1-10 tablolari (ECC seviyesi M)
# (toplam_kod_sozcugu, veri_kod_sozcugu) her blok icin - dogrulanmis ISO/IEC 18004 Tablo 9 degerleri.
_RS_BLOCKS = {
    1: [(26, 16)], 2: [(44, 28)], 3: [(70, 44)], 4: [(50, 32)] * 2, 5: [(67, 43)] * 2,
    6: [(43, 27)] * 4, 7: [(49, 31)] * 4, 8: [(60, 38), (60, 38), (61, 39), (61, 39)],
    9: [(58, 36), (58, 36), (58, 36), (59, 37), (59, 37)],
    10: [(69, 43), (69, 43), (69, 43), (69, 43), (70, 44)],
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
          8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
_EC_LEVEL_M = 0b00               # format bilgisindeki 2 bitlik seviye kodu (ISO Tablo 25: M=00)
_G15, _G15_MASK, _G18 = 0b10100110111, 0b101010000010010, 0b1111100100101


def _bch_bits(v):
    d = 0
    while v:
        d += 1
        v >>= 1
    return d


def _bch_type_info(data):
    d = data << 10
    while _bch_bits(d) - _bch_bits(_G15) >= 0:
        d ^= _G15 << (_bch_bits(d) - _bch_bits(_G15))
    return ((data << 10) | d) ^ _G15_MASK


def _bch_type_number(version):
    d = version << 12
    while _bch_bits(d) - _bch_bits(_G18) >= 0:
        d ^= _G18 << (_bch_bits(d) - _bch_bits(_G18))
    return (version << 12) | d


class _Bits:
    def __init__(self):
        self.v = []

    def put(self, val, n):
        for i in range(n - 1, -1, -1):
            self.v.append((val >> i) & 1)

    def __len__(self):
        return len(self.v)

    def bytes(self):
        out = bytearray((len(self.v) + 7) // 8)
        for i, b in enumerate(self.v):
            if b:
                out[i // 8] |= 0x80 >> (i % 8)
        return bytes(out)


def _pick_version(nbytes):
    for v in range(1, 11):
        cap = sum(d for _, d in _RS_BLOCKS[v])
        count_bits = 8 if v < 10 else 16
        if nbytes * 8 <= cap * 8 - 4 - count_bits:
            return v, cap
    raise ValueError("veri cok uzun (QR versiyon 1-10 / ECC-M kapasitesini asiyor: %d bayt)" % nbytes)


def _codewords(data):
    version, cap = _pick_version(len(data))
    count_bits = 8 if version < 10 else 16
    bits = _Bits()
    bits.put(0b0100, 4)                       # bayt modu
    bits.put(len(data), count_bits)
    for byte in data:
        bits.put(byte, 8)
    bits.put(0, min(4, cap * 8 - len(bits)))  # sonlandirici
    while len(bits) % 8:
        bits.put(0, 1)
    pad = [0xEC, 0x11]
    i = 0
    while len(bits) // 8 < cap:
        bits.put(pad[i % 2], 8)
        i += 1
    data_cw = bits.bytes()

    blocks = _RS_BLOCKS[version]
    dblocks, eblocks = [], []
    pos = 0
    for total, dcount in blocks:
        chunk = data_cw[pos:pos + dcount]
        pos += dcount
        dblocks.append(chunk)
        eblocks.append(_rs_encode(chunk, total - dcount))

    out = bytearray()
    for i in range(max(len(b) for b in dblocks)):
        for b in dblocks:
            if i < len(b):
                out.append(b[i])
    for i in range(len(eblocks[0])):
        for b in eblocks:
            out.append(b[i])
    return version, bytes(out), _REMAINDER_BITS[version]


# ---------------------------------------------------------------- matris insasi
def _finder(mat, size, row, col):
    for r in range(-1, 8):
        if not (0 <= row + r < size):
            continue
        for c in range(-1, 8):
            if not (0 <= col + c < size):
                continue
            dark = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) or (2 <= r <= 4 and 2 <= c <= 4)
            mat[row + r][col + c] = dark


def _alignment(mat, size, version):
    pos = _ALIGN[version]
    for row in pos:
        for col in pos:
            if mat[row][col] is not None:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    mat[row + r][col + c] = r in (-2, 2) or c in (-2, 2) or (r == 0 and c == 0)


def _timing(mat, size):
    for i in range(8, size - 8):
        if mat[i][6] is None:
            mat[i][6] = i % 2 == 0
        if mat[6][i] is None:
            mat[6][i] = i % 2 == 0


def _format_info(mat, size, mask, test):
    bits = 0 if test else _bch_type_info((_EC_LEVEL_M << 3) | mask)
    for i in range(15):
        b = ((bits >> i) & 1) == 1
        r, c = (i, 8) if i < 6 else (i + 1, 8) if i < 8 else (size - 15 + i, 8)
        mat[r][c] = b
        r, c = (8, size - i - 1) if i < 8 else (8, 15 - i) if i < 9 else (8, 14 - i)
        mat[r][c] = b
    mat[size - 8][8] = not test           # sabit koyu modul


def _version_info(mat, size, version, test):
    if version < 7:
        return
    bits = 0 if test else _bch_type_number(version)
    for i in range(18):
        b = ((bits >> i) & 1) == 1
        mat[i // 3][i % 3 + size - 11] = b
        mat[i % 3 + size - 11][i // 3] = b


def _mask(pattern, r, c):
    return [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
            lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
            lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
            lambda r, c: ((r * c) % 3 + (r + c) % 2) % 2 == 0][pattern](r, c)


def _place_data(mat, size, codewords, mask):
    """Standart QR zigzag: sagdan sola ikili sutun seritleri, her seritte dikey yon degisir, sutun 6 atlanir."""
    total_bits = len(codewords) * 8
    bit_i = 0
    row, direction, col = size - 1, -1, size - 1
    while col > 0:
        if col == 6:
            col -= 1
        while True:
            for cc in (col, col - 1):
                if mat[row][cc] is None:
                    dark = bit_i < total_bits and ((codewords[bit_i // 8] >> (7 - bit_i % 8)) & 1) == 1
                    if _mask(mask, row, cc):
                        dark = not dark
                    mat[row][cc] = dark
                    bit_i += 1
            row += direction
            if row < 0 or row >= size:
                row -= direction
                direction = -direction
                break
        col -= 2


def _penalty(mat, size):
    p = 0
    for row in mat:
        run, prev = 1, row[0]
        for v in row[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    p += run - 2
                run, prev = 1, v
        if run >= 5:
            p += run - 2
    for c in range(size):
        run, prev = 1, mat[0][c]
        for r in range(1, size):
            v = mat[r][c]
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    p += run - 2
                run, prev = 1, v
        if run >= 5:
            p += run - 2
    for r in range(size - 1):
        for c in range(size - 1):
            v = mat[r][c]
            if v == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                p += 3
    pat1, pat2 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for row in mat:
        for c in range(size - 10):
            seg = row[c:c + 11]
            if seg == pat1 or seg == pat2:
                p += 40
    for c in range(size):
        col = [mat[r][c] for r in range(size)]
        for r in range(size - 10):
            seg = col[r:r + 11]
            if seg == pat1 or seg == pat2:
                p += 40
    dark = sum(sum(1 for v in row if v) for row in mat)
    p += int(abs(dark * 100.0 / (size * size) - 50) / 5) * 10
    return p


def _build(codewords, remainder_bits, version, mask, test):
    size = version * 4 + 17
    mat = [[None] * size for _ in range(size)]
    _finder(mat, size, 0, 0)
    _finder(mat, size, size - 7, 0)
    _finder(mat, size, 0, size - 7)
    _alignment(mat, size, version)
    _timing(mat, size)
    _format_info(mat, size, mask, test)
    _version_info(mat, size, version, test)
    _place_data(mat, size, codewords, mask)
    return mat


def matrix(text):
    """UTF-8 metni QR modul matrisine (satir listesi, True=koyu) donusturur; en iyi maske otomatik secilir."""
    data = text.encode("utf-8")
    version, codewords, remainder = _codewords(data)
    best_mask, best_score, best_mat = 0, None, None
    for m in range(8):
        mat = _build(codewords, remainder, version, m, test=True)
        score = _penalty(mat, len(mat))
        if best_score is None or score < best_score:
            best_mask, best_score = m, score
    final = _build(codewords, remainder, version, best_mask, test=False)
    return final


def to_svg(text, module_px=6, quiet=4):
    """Uretilen QR'i bagimsiz bir SVG dizesi olarak dondurur (harici kutuphane/font gerekmez)."""
    mat = matrix(text)
    n = len(mat)
    size = (n + quiet * 2) * module_px
    rects = []
    for r, row in enumerate(mat):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append('<rect x="%d" y="%d" width="%d" height="%d"/>' %
                              ((start + quiet) * module_px, (r + quiet) * module_px, (c - start) * module_px, module_px))
            else:
                c += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" shape-rendering="crispEdges">'
            '<rect width="100%%" height="100%%" fill="#fff"/><g fill="#000">%s</g></svg>') % (
        size, size, size, size, "".join(rects))


def _esc_wifi(s):
    return re.sub(r'([\\;,:"])', r"\\\1", s)


def wifi_payload(ssid, psk, security="WPA", hidden=False):
    """QR icin standart 'WIFI:' baglanti dizesi (Android/iOS otomatik baglanma formati)."""
    t = "nopass" if security == "open" else "WPA"
    parts = ["WIFI:T:%s" % t, "S:%s" % _esc_wifi(ssid)]
    if t != "nopass":
        parts.append("P:%s" % _esc_wifi(psk))
    if hidden:
        parts.append("H:true")
    return ";".join(parts) + ";;"
