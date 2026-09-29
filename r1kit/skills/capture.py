"""Kendi agin uzerinde paket yakalama (tcpdump) - sinirli sure/boyut, pcap indirme."""
import os
import re
import subprocess
import time

from .. import sysutil
from ..config import IFACE_RE
from .base import Skill, register

FILTER_RE = re.compile(r"^[A-Za-z0-9 .:/_()!&|=<>-]{0,120}$")


@register
class Capture(Skill):
    name = "capture"
    title = "Paket Yakalama (tcpdump)"
    desc = "Herhangi bir arayuzde sinirli sureli pcap yakalar; Wireshark ile incelemek icin indirilebilir."
    category = "arac"
    needs = ["tcpdump"]
    default_enabled = True

    def __init__(self):
        self.proc = None

    def actions(self):
        return {"start": self.start, "stop": self.stop, "list": self.list}

    def _dir(self):
        return os.path.join(sysutil.VAR, "captures")

    def start(self, rt, p):
        iface = p.get("iface", "any")
        flt = p.get("filter", "")
        if iface != "any" and not IFACE_RE.match(iface):
            raise ValueError("gecersiz arayuz")
        if not FILTER_RE.match(flt):
            raise ValueError("gecersiz filtre")
        secs = min(int(p.get("seconds", 30)), 300)
        mb = min(int(p.get("max_mb", 20)), 50)
        os.makedirs(self._dir(), exist_ok=True)
        name = "cap-%s-%d.pcap" % (iface, int(time.time()))
        argv = ["timeout", str(secs), "tcpdump", "-n", "-i", iface, "-s", "256", "-C", str(mb), "-W", "1", "-w",
                os.path.join(self._dir(), name)] + flt.split()
        if not sysutil.DRY:
            self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # eski yakalamalari temizle (en fazla 10 dosya)
        files = sorted(os.listdir(self._dir()))
        for old in files[:-10]:
            try:
                os.unlink(os.path.join(self._dir(), old))
            except OSError:
                pass
        return {"ok": True, "file": name, "seconds": secs}

    def stop(self, rt, p):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        return {"ok": True}

    def list(self, rt, p):
        d = self._dir()
        out = []
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                out.append({"name": f, "size": os.path.getsize(os.path.join(d, f))})
        return {"ok": True, "files": out, "running": bool(self.proc and self.proc.poll() is None)}
