import os
import re
import urllib.request

from .. import sysutil
from .base import Skill, F, register

DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
HOSTS = lambda: os.path.join(sysutil.VAR, "adblock.hosts")


@register
class AdBlock(Skill):
    name = "adblock"
    title = "Reklam / Izleyici Engelleme (DNS)"
    desc = "dnsmasq uzerinden DNS seviyesinde engelleme. 256MB RAM icin liste boyutu sinirlidir."
    category = "guvenlik"
    fields = [
        F("lists", "Liste URL'leri (satir basina)", "textarea",
          "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"),
        F("custom", "Ek engellenen alan adlari (satir basina)", "textarea", ""),
        F("max_entries", "En fazla kayit", "int", 40000, h="R1 icin 40000 civari guvenli"),
    ]

    def render(self, cfg, s, plan, topo):
        path = HOSTS()
        plan.file(path, sysutil.read_file(path, "# bos\n"))
        plan.dnsmasq.append("addn-hosts=" + path)
        for d in s["custom"].split():
            if DOMAIN.match(d.lower()):
                plan.dnsmasq.append("address=/%s/0.0.0.0" % d.lower())

    def status(self, cfg, s, topo):
        n = sum(1 for l in sysutil.read_file(HOSTS()).splitlines() if l.startswith("0.0.0.0"))
        return {"blocked_domains": n}

    def actions(self):
        return {"update": self.update}

    def update(self, rt, params):
        cfg = rt.store.get()
        s = self.scfg(cfg)
        seen, lines = set(), []
        limit = int(s["max_entries"])
        for url in s["lists"].split():
            if not url.startswith("https://"):
                continue
            try:
                with urllib.request.urlopen(url, timeout=25) as r:
                    text = r.read(20_000_000).decode("utf-8", "ignore")
            except Exception as ex:
                return {"ok": False, "error": "%s: %s" % (url, ex)}
            for l in text.splitlines():
                parts = l.split("#")[0].split()
                if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1") and DOMAIN.match(parts[1].lower()):
                    d = parts[1].lower()
                    if d not in seen and len(seen) < limit:
                        seen.add(d)
                        lines.append("0.0.0.0 " + d)
        sysutil.write_file(HOSTS(), "\n".join(lines) + "\n")
        rt.sup.signal("dnsmasq", 1)           # SIGHUP: addn-hosts yeniden okunur
        return {"ok": True, "count": len(lines)}
