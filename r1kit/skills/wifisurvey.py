"""Wi-Fi ortam taramasi ve kanal optimizasyonu."""
import json
import os
import re

from .. import sysutil
from ..config import IFACE_RE
from .base import Skill, register


def parse_scan(text):
    aps = []
    for block in re.split(r"(?m)^BSS ", text)[1:]:
        m = re.match(r"([0-9a-f:]{17})", block)
        if not m:
            continue
        freq = re.search(r"freq: (\d+)", block)
        sig = re.search(r"signal: (-?[\d.]+)", block)
        ssid = re.search(r"SSID: (.*)", block)
        f = int(freq.group(1)) if freq else 0
        ch = (f - 2407) // 5 if f < 3000 else (f - 5000) // 5
        aps.append({"bssid": m.group(1), "ssid": ssid.group(1).strip() if ssid else "", "freq": f, "channel": ch,
                    "signal": float(sig.group(1)) if sig else None,
                    "secure": "RSN:" in block or "WPA:" in block, "band": "2.4" if f < 3000 else "5"})
    return aps


def best_channels(aps):
    """Her kanal icin girisim skoru (guclu AP'ler agirlikli); 2.4GHz'de 1/6/11 arasindan secim."""
    score = {c: 0.0 for c in (1, 6, 11)}
    for a in aps:
        if a["band"] != "2.4" or a["signal"] is None:
            continue
        w = 10 ** ((a["signal"] + 100) / 20)
        for c in score:
            if abs(a["channel"] - c) < 5:
                score[c] += w * (1.0 if a["channel"] == c else 0.5)
    return min(score, key=score.get), score


@register
class WifiSurvey(Skill):
    name = "wifi_survey"
    title = "Wi-Fi Tarama ve Kanal Optimizasyonu"
    desc = "Cevredeki AP'leri listeler, en bos 2.4GHz kanalini onerir ve AP'lerde 'auto' kanal icin kaydeder."
    category = "wifi"
    default_enabled = True
    needs = ["iw"]

    def actions(self):
        return {"scan": self.scan, "optimize": self.optimize}

    def scan(self, rt, p):
        iface = p.get("iface", "")
        if not IFACE_RE.match(iface):
            raise ValueError("gecersiz arayuz")
        sysutil.run(["ip", "link", "set", iface, "up"], ignore=True)
        rc, out = sysutil.run(["iw", "dev", iface, "scan"], timeout=30, ignore=True)
        if rc != 0:
            return {"ok": False, "error": out.strip()[:300] +
                    " (Aktif AP arayuzunde tarama mumkun olmayabilir; ikinci wifi veya sanal arayuz kullanin)"}
        aps = sorted(parse_scan(out), key=lambda a: -(a["signal"] or -999))
        best, score = best_channels(aps)
        return {"ok": True, "aps": aps, "best_2g": best, "scores": score}

    def optimize(self, rt, p):
        r = self.scan(rt, p)
        if not r["ok"]:
            return r
        path = os.path.join(sysutil.VAR, "survey.json")
        sysutil.write_file(path, json.dumps({"best": {"2.4": r["best_2g"]}}))
        return {"ok": True, "best_2g": r["best_2g"], "note": "Uygulamak icin yapilandirmayi 'Uygula' yapin (AP kanali 'auto' olmali)."}
