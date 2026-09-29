"""Ag araclari: ping, traceroute, DNS, WoL, LAN port tarama (yalnizca ozel aglar), hiz testi."""
import concurrent.futures
import ipaddress
import re
import socket
import time

from .. import sysutil
from ..config import HOST_RE, IFACE_RE, MAC_RE
from .base import Skill, register

COMMON = [21, 22, 23, 25, 53, 80, 110, 139, 143, 443, 445, 554, 631, 1883, 3306, 3389, 5900, 8000, 8080, 8443, 9100]


def _host(p):
    h = str(p.get("host", "")).strip()
    if not HOST_RE.match(h) or h.startswith("-"):
        raise ValueError("gecersiz host")
    return h


def _iface(p):
    i = p.get("iface")
    if i and not IFACE_RE.match(i):
        raise ValueError("gecersiz arayuz")
    return i


@register
class NetTools(Skill):
    name = "nettools"
    title = "Ag Araclari"
    desc = "Ping, traceroute, DNS sorgusu, Wake-on-LAN, LAN cihaz/port kesfi (sadece ozel IP araliklari), hiz testi."
    category = "arac"
    default_enabled = True
    needs = ["ping", "traceroute"]

    def actions(self):
        return {"ping": self.ping, "traceroute": self.trace, "dns": self.dns, "wol": self.wol,
                "portscan": self.portscan, "speedtest": self.speedtest}

    def ping(self, rt, p):
        cmd = ["ping", "-n", "-c", str(min(int(p.get("count", 4)), 10)), "-W", "2"]
        if _iface(p):
            cmd += ["-I", p["iface"]]
        rc, out = sysutil.run(cmd + [_host(p)], timeout=40, ignore=True)
        return {"ok": rc == 0, "output": out}

    def trace(self, rt, p):
        cmd = ["traceroute", "-n", "-m", "15", "-w", "1"]
        if _iface(p):
            cmd += ["-i", p["iface"]]
        rc, out = sysutil.run(cmd + [_host(p)], timeout=60, ignore=True)
        return {"ok": rc == 0, "output": out}

    def dns(self, rt, p):
        h = _host(p)
        t = time.time()
        try:
            r = sorted({a[4][0] for a in socket.getaddrinfo(h, None)})
            return {"ok": True, "addresses": r, "ms": round((time.time() - t) * 1000, 1)}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def wol(self, rt, p):
        mac = str(p.get("mac", "")).lower()
        if not MAC_RE.match(mac):
            raise ValueError("gecersiz MAC")
        pkt = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        sent = 0
        for net in rt.topo().nets.values():
            if net["iface"]:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                try:
                    s.bind((str(net["iface"].ip), 0))
                    s.sendto(pkt, (str(net["iface"].network.broadcast_address), 9))
                    sent += 1
                except OSError:
                    pass
                finally:
                    s.close()
        return {"ok": sent > 0, "sent_on": sent}

    def portscan(self, rt, p):
        """Yalnizca yapilandirilmis yerel aglarin icindeki adresler taranabilir."""
        target = ipaddress.ip_address(_host(p))
        allowed = [n["iface"].network for n in rt.topo().nets.values() if n["iface"]]
        if not any(target in a for a in allowed):
            raise ValueError("sadece yerel aglardaki cihazlar taranabilir")
        ports = [int(x) for x in p.get("ports", COMMON)][:200]

        def probe(port):
            s = socket.socket()
            s.settimeout(0.6)
            try:
                return port if s.connect_ex((str(target), port)) == 0 else None
            finally:
                s.close()
        with concurrent.futures.ThreadPoolExecutor(16) as ex:
            open_ports = sorted(x for x in ex.map(probe, ports) if x)
        return {"ok": True, "host": str(target), "open": open_ports}

    def speedtest(self, rt, p):
        """Belirli uplink uzerinden HTTP indirme hizi (curl --interface)."""
        url = str(p.get("url") or "http://speed.hetzner.de/100MB.bin")
        if not re.match(r"^https?://[A-Za-z0-9./_:-]+$", url):
            raise ValueError("gecersiz URL")
        cmd = ["curl", "-s", "-o", "/dev/null", "-m", "12", "-w", "%{speed_download}"]
        if _iface(p):
            cmd += ["--interface", p["iface"]]
        rc, out = sysutil.run(cmd + [url], timeout=20, ignore=True)
        try:
            return {"ok": True, "mbit": round(float(out.strip()) * 8 / 1e6, 2)}
        except ValueError:
            return {"ok": False, "error": out[:200]}
