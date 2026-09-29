"""r1kit.qrcode (bagimsiz QR ureteci) icin dogruluk testleri.

cv2 varsa (gelistirme ortaminda) uretilen kodlar gercekten bir QR cozucuyle okunarak
uctan uca dogrulanir; yoksa yapisal testlerle (boyut, tasma hatasi, kacis) yetinilir.
"""
import random
import re
import string

import pytest

from r1kit import qrcode as q

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


def render_bitmap(text, scale=8, quiet=4):
    mat = q.matrix(text)
    n = len(mat)
    size = (n + quiet * 2) * scale
    img = np.full((size, size), 255, dtype=np.uint8)
    for r, row in enumerate(mat):
        for c, v in enumerate(row):
            if v:
                y, x = (r + quiet) * scale, (c + quiet) * scale
                img[y:y + scale, x:x + scale] = 0
    return img


def decodes_to(text):
    det = cv2.QRCodeDetector()
    for scale in (8, 10, 12, 16):            # cv2'nin kendi dedektoru bazen olcege duyarli
        data, _, _ = det.detectAndDecode(render_bitmap(text, scale=scale))
        if data == text:
            return True
    return False


@pytest.mark.skipif(not HAVE_CV2, reason="cv2 yok")
@pytest.mark.parametrize("text", [
    "hello world", "a", "1234567890", "http://r1.local/", "https://192.168.50.1/",
    "R1 Orange Pi Toolkit", "türkçe şğıöçü test", "!" * 13, "A" * 14, "B" * 15,
    "x" * 210,
])
def test_roundtrip_fixed(text):
    assert decodes_to(text)


@pytest.mark.skipif(not HAVE_CV2, reason="cv2 yok")
def test_roundtrip_random_sweep():
    random.seed(7)
    alphabet = string.ascii_letters + string.digits + " -_."
    bad = []
    for n in (1, 5, 14, 15, 30, 60, 100, 150, 200):
        t = "".join(random.choice(alphabet) for _ in range(n))
        if not decodes_to(t):
            bad.append((n, t))
    assert not bad, bad


@pytest.mark.skipif(not HAVE_CV2, reason="cv2 yok")
def test_roundtrip_wifi_payload_with_special_chars():
    payload = q.wifi_payload('Weird"SSID\\, name', 'p@ss;word:1\\', "WPA")
    assert decodes_to(payload)
    open_payload = q.wifi_payload("Acik Misafir Agi", "", "open")
    assert "T:nopass" in open_payload and decodes_to(open_payload)


def test_wifi_payload_escaping_is_reversible_by_spec():
    # Ozel karakterler ( \ ; , : " ) ters egik cizgi ile kacilir (QR WIFI: standardi)
    p = q.wifi_payload('a;b,c:d"e\\f', "pw", "WPA")
    assert p.startswith("WIFI:T:WPA;S:a\\;b\\,c\\:d\\\"e\\\\f;P:pw;;")


def test_version_grows_with_payload_and_capacity_error():
    assert len(q.matrix("x")) == 21                     # versiyon 1: 4*1+17
    assert len(q.matrix("x" * 200)) > 21
    with pytest.raises(ValueError):
        q.matrix("x" * 300)                              # v10-M kapasitesinin (~211 bayt) uzerinde


def test_svg_well_formed():
    svg = q.to_svg("http://r1.local/")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert re.search(r'viewBox="0 0 (\d+) \1"', svg)      # kare tuval


def test_empty_and_boundary_lengths_do_not_crash():
    for n in (0, 14, 15, 26, 27, 211):
        q.matrix("x" * n)
