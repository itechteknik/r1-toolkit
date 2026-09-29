"""Yapilandirma modeli, varsayilanlar, dogrulama ve kalici depo."""
import copy
import ipaddress
import json
import os
import re
import threading
import time

from . import sysutil

ROLES = ("wan", "lan", "ap", "sta", "monitor", "unused")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,9}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.:-]{1,253}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f\"\\]")

DEFAULT = {
    "version": 1,
    "system": {"hostname": "r1", "country": "TR", "domain": "lan", "dns_servers": ["1.1.1.1", "9.9.9.9"]},
    "admin": {"password_hash": "", "must_change": True, "session_ttl": 1800,
              "wan_access": False, "ssh_from_lan": True},
    "interfaces": {},
    "networks": {
        "lan": {"address": "192.168.50.1/24", "dhcp": True, "dhcp_start": 20, "dhcp_end": 230,
                "lease": "12h", "isolate": False, "portal": False, "vpn": False,
                "allow_to": [], "egress": "default"},
    },
    "uplink": {"mode": "failover", "primary": "", "targets": ["1.1.1.1", "8.8.8.8"],
               "interval": 5, "fail_after": 3, "recover_after": 2},
    "port_forwards": [],       # {proto,wan_port,to_ip,to_port,comment}
    "static_leases": [],       # {mac,ip,name}
    "blocked_macs": [],
    "client_limits": {},       # mac -> {"down_mbps":n,"up_mbps":n}
    "skills": {},
}


def deep_merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _ip(s):
    try:
        return ipaddress.ip_interface(s)
    except ValueError:
        return None


def validate(cfg):
    """Hata mesajlari listesi doner (bos = gecerli)."""
    e = []
    nets = cfg.get("networks", {})
    ifs = cfg.get("interfaces", {})
    for n, spec in nets.items():
        if not NAME_RE.match(n):
            e.append("ag adi gecersiz: %r (kucuk harf/rakam/_, en fazla 10)" % n)
        addr = spec.get("address")
        if addr:
            i = _ip(addr)
            if not i or i.version != 4 or i.network.prefixlen > 29:
                e.append("%s: adres gecersiz (IPv4/CIDR, /29 veya daha genis olmali)" % n)
            elif spec.get("dhcp") and not (1 <= int(spec.get("dhcp_start", 0)) < int(spec.get("dhcp_end", 0)) < i.network.num_addresses - 1):
                e.append("%s: dhcp araligi ag boyutunun disinda" % n)
    seen = []
    for n, spec in nets.items():
        i = _ip(spec.get("address") or "")
        if i:
            for o in seen:
                if i.network.overlaps(o.network):
                    e.append("%s: baska bir agla cakisiyor" % n)
            seen.append(i)
    for name, s in ifs.items():
        if not IFACE_RE.match(name):
            e.append("arayuz adi gecersiz: %r" % name)
            continue
        role = s.get("role", "unused")
        if role not in ROLES:
            e.append("%s: gecersiz rol %r" % (name, role))
        if role in ("lan", "ap") and s.get("network") not in nets:
            e.append("%s: 'network' mevcut bir aga isaret etmeli" % name)
        if role == "wan" and s.get("proto", "dhcp") == "static":
            if not _ip(s.get("address", "")) or not s.get("gateway"):
                e.append("%s: statik WAN icin address ve gateway gerekli" % name)
        if role == "ap":
            ssid, psk = s.get("ssid", ""), s.get("psk", "")
            if not 1 <= len(ssid.encode()) <= 32 or CTRL_RE.search(ssid):
                e.append("%s: SSID 1-32 bayt olmali, tirnak/ters egik cizgi/kontrol karakteri olamaz" % name)
            if s.get("security", "wpa2") != "open" and not (8 <= len(psk) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", psk or "")) :
                e.append("%s: WPA parolasi 8-63 karakter olmali" % name)
            if CTRL_RE.search(psk or ""):
                e.append("%s: parolada gecersiz karakter" % name)
        if role == "sta":
            for sv in s.get("saved", []):
                if not 1 <= len(sv.get("ssid", "").encode()) <= 32:
                    e.append("%s: kayitli SSID gecersiz" % name)
                if sv.get("psk") and not 8 <= len(sv["psk"]) <= 63:
                    e.append("%s: kayitli parola 8-63 karakter olmali" % name)
        if s.get("parent") and s["parent"] not in ifs and not IFACE_RE.match(s["parent"]):
            e.append("%s: parent gecersiz" % name)
    for r in cfg.get("port_forwards", []):
        try:
            assert r["proto"] in ("tcp", "udp")
            assert 1 <= int(r["wan_port"]) <= 65535 and 1 <= int(r["to_port"]) <= 65535
            ipaddress.IPv4Address(r["to_ip"])
        except Exception:
            e.append("port yonlendirme kurali gecersiz: %r" % (r,))
    for l in cfg.get("static_leases", []):
        try:
            assert MAC_RE.match(l["mac"].lower())
            ipaddress.IPv4Address(l["ip"])
            assert not CTRL_RE.search(l.get("name", "")) and "," not in l.get("name", "")
        except Exception:
            e.append("statik kira gecersiz: %r" % (l,))
    for m in cfg.get("blocked_macs", []):
        if not MAC_RE.match(m):
            e.append("engelli MAC gecersiz: %r" % m)
    if cfg.get("uplink", {}).get("mode") not in ("failover", "balance", "single"):
        e.append("uplink.mode failover|balance|single olmali")
    cc = cfg.get("system", {}).get("country", "")
    if not re.fullmatch(r"[A-Z]{2}", cc):
        e.append("system.country iki harfli buyuk harf olmali (orn. TR)")
    return e


class Store:
    """Thread-safe JSON yapilandirma deposu."""

    def __init__(self, path=None):
        self.path = path or os.path.join(sysutil.ETC, "config.json")
        self.lock = threading.RLock()
        self._cfg = None

    def load(self):
        with self.lock:
            try:
                with open(self.path) as f:
                    self._cfg = deep_merge(DEFAULT, json.load(f))
            except FileNotFoundError:
                self._cfg = copy.deepcopy(DEFAULT)
            return copy.deepcopy(self._cfg)

    def get(self):
        with self.lock:
            if self._cfg is None:
                return self.load()
            return copy.deepcopy(self._cfg)

    def set(self, cfg, backup=True):
        errs = validate(cfg)
        if errs:
            raise ValueError("; ".join(errs))
        with self.lock:
            if backup and os.path.exists(self.path):
                try:
                    os.replace(self.path, self.path + ".bak")
                except OSError:
                    pass
            sysutil.write_file(self.path, json.dumps(cfg, indent=2, sort_keys=True), 0o600)
            self._cfg = copy.deepcopy(cfg)

    def update(self, fn):
        with self.lock:
            cfg = self.get()
            fn(cfg)
            self.set(cfg)
            return cfg


def public_view(cfg):
    """API'ye giden kopya: parola hash'i ve VPN ozel anahtari gizlenir."""
    c = copy.deepcopy(cfg)
    c["admin"].pop("password_hash", None)
    for s in c.get("skills", {}).values():
        for k in ("private_key",):
            if k in s:
                s[k] = "***" if s[k] else ""
    return c
