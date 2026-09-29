"""Donanim kesfi: ethernet/wifi arayuzleri, USB/onboard ayrimi, iw yetenekleri."""
import json
import os
import re

from . import sysutil

SYS = sysutil.P("/sys/class/net")
SKIP_PREFIX = ("lo", "br-", "veth", "docker", "wg", "ifb", "dummy", "bond", "sit", "tun")


def _rd(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _fake():
    """R1KIT_FAKE_HW=1: gelistirme icin orange pi r1 benzeri sahte donanim."""
    def eth(n, bus, drv, speed):
        return {"name": n, "type": "ethernet", "mac": "02:00:00:00:00:0%d" % len(n), "bus": bus, "driver": drv,
                "carrier": n == "end0", "speed": speed, "rx": 123456, "tx": 654321, "addrs": [], "phy": None}
    def wifi(n, phy):
        d = {"name": n, "type": "wifi", "mac": "02:00:00:00:01:0%s" % n[-1], "bus": "usb" if n == "wlan1" else "sdio",
             "driver": "8189es", "carrier": False, "speed": None, "rx": 0, "tx": 0, "addrs": [], "phy": phy,
             "wifi": {"modes": ["managed", "AP", "monitor"], "bands": ["2.4GHz"], "ap_sta_concurrent": False,
                      "max_ap": 1}}
        return d
    return [eth("end0", "platform", "sun8i-emac", 100), eth("eth1", "usb", "r8152", None),
            wifi("wlan0", "phy0"), wifi("wlan1", "phy1")]


def iw_phy_caps(phy):
    rc, out = sysutil.run(["iw", "phy", phy, "info"], ignore=True)
    caps = {"modes": [], "bands": [], "ap_sta_concurrent": False, "max_ap": 0}
    if rc != 0:
        return caps
    m = re.search(r"Supported interface modes:\n((?:\s+\*.*\n)+)", out)
    if m:
        caps["modes"] = [x.strip("* ").strip() for x in m.group(1).splitlines()]
    for b in re.finditer(r"Band (\d+):", out):
        caps["bands"].append("2.4GHz" if b.group(1) == "1" else "5GHz")
    for line in re.findall(r"#\{[^\n]*", out):
        if "AP" in line and "managed" in line:
            caps["ap_sta_concurrent"] = True
        mm = re.search(r"#\{[^}]*AP[^}]*\} <= (\d+)", line)
        if mm:
            caps["max_ap"] = max(caps["max_ap"], int(mm.group(1)))
    return caps


def _addrs(name):
    rc, out = sysutil.run(["ip", "-j", "-4", "addr", "show", "dev", name], ignore=True)
    try:
        return [f"{a['local']}/{a['prefixlen']}" for d in json.loads(out) for a in d.get("addr_info", [])]
    except Exception:
        return []


def list_interfaces():
    if os.environ.get("R1KIT_FAKE_HW") == "1":
        return _fake()
    res = []
    try:
        names = sorted(os.listdir(SYS))
    except OSError:
        return res
    for n in names:
        if n.startswith(SKIP_PREFIX):
            continue
        base = os.path.join(SYS, n)
        wifi = os.path.isdir(os.path.join(base, "wireless")) or os.path.isdir(os.path.join(base, "phy80211"))
        try:
            sub = os.path.basename(os.path.realpath(os.path.join(base, "device", "subsystem")))
        except OSError:
            sub = ""
        drv = os.path.basename(os.path.realpath(os.path.join(base, "device", "driver"))) if os.path.exists(os.path.join(base, "device", "driver")) else ""
        speed = _rd(os.path.join(base, "speed"))
        d = {
            "name": n, "type": "wifi" if wifi else "ethernet", "mac": _rd(os.path.join(base, "address")),
            "bus": sub or "?", "driver": drv, "carrier": _rd(os.path.join(base, "carrier")) == "1",
            "speed": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
            "rx": int(_rd(os.path.join(base, "statistics/rx_bytes"), "0") or 0),
            "tx": int(_rd(os.path.join(base, "statistics/tx_bytes"), "0") or 0),
            "addrs": _addrs(n), "phy": _rd(os.path.join(base, "phy80211/name")) or None,
        }
        if wifi and d["phy"]:
            d["wifi"] = iw_phy_caps(d["phy"])
        res.append(d)
    return res


def suggest_roles(ifaces):
    """Adlandirma: onboard ethernet -> wan, USB ethernet -> lan, wifi0 -> ap, wifi1 -> sta."""
    eth = sorted([i for i in ifaces if i["type"] == "ethernet"], key=lambda i: (i["bus"] == "usb", i["name"]))
    wifi = sorted([i for i in ifaces if i["type"] == "wifi"], key=lambda i: i["name"])
    return {"wired": [i["name"] for i in eth], "wifi": [i["name"] for i in wifi]}
