"""Coklu uplink yoneticisi: saglik kontrolu, failover, yuk dengeleme (multipath), uplink basina yonlendirme tablosu."""
import collections
import json
import os
import re
import threading
import time

from . import sysutil

BASE_TABLE = 100
BASE_PREF = 100
MAIN_METRIC = "10"


class UplinkManager:
    def __init__(self, store):
        self.store = store
        self.state = {}
        self.applied_sig = None
        self.active = []
        self.events = collections.deque(maxlen=100)
        self.lock = threading.Lock()

    def ev(self, msg):
        self.events.appendleft({"t": int(time.time()), "msg": msg})

    def info(self, iface):
        try:
            with open(os.path.join(sysutil.RUN, "uplinks", iface + ".json")) as f:
                d = json.load(f)
            return d if d.get("gw") and d.get("ip") else None
        except (OSError, ValueError):
            return None

    def probe(self, iface, targets):
        for t in targets:
            rc, out = sysutil.run(["ping", "-n", "-q", "-c", "1", "-W", "2", "-I", iface, t], timeout=6, ignore=True)
            if rc == 0:
                m = re.search(r"= [\d.]+/([\d.]+)/", out)
                return True, float(m.group(1)) if m else None
        return False, None

    def uplinks(self, cfg):
        return [n for n, s in cfg["interfaces"].items() if s.get("role") in ("wan", "sta")]

    def tick(self):
        cfg = self.store.get()
        u = cfg["uplink"]
        names = self.uplinks(cfg)
        with self.lock:
            for n in list(self.state):
                if n not in names:
                    del self.state[n]
            for n in names:
                st = self.state.setdefault(n, {"up": False, "fails": 0, "oks": 0, "rtt": None})
                info = self.info(n)
                st["info"] = info
                if not info:
                    if st["up"]:
                        self.ev("%s: baglanti/adres kayboldu" % n)
                    st.update(up=False, oks=0, fails=0, rtt=None)
                    continue
                ok, rtt = self.probe(n, u["targets"])
                st["rtt"] = rtt
                if ok:
                    st["oks"] += 1
                    st["fails"] = 0
                    if not st["up"] and st["oks"] >= (1 if st.get("new", True) else u["recover_after"]):
                        st["up"] = True
                        self.ev("%s: UP (%s ms)" % (n, rtt))
                else:
                    st["fails"] += 1
                    st["oks"] = 0
                    if st["up"] and st["fails"] >= u["fail_after"]:
                        st["up"] = False
                        self.ev("%s: DOWN (saglik kontrolu basarisiz)" % n)
                st["new"] = False
            self._reconcile(cfg, names)

    # -- karar & uygulama ------------------------------------------------------
    def compute(self, cfg, names):
        ifs, u = cfg["interfaces"], cfg["uplink"]
        prio = lambda n: (int(ifs[n].get("priority", 100)), n)
        healthy = sorted([n for n in names if self.state.get(n, {}).get("up")], key=prio)
        mode = u["mode"]
        if mode == "single":
            p = u.get("primary")
            chosen = [p] if p in names and self.state.get(p, {}).get("info") else healthy[:1]
        elif mode == "balance":
            chosen = healthy
        else:
            chosen = healthy[:1]
        return [(n, self.state[n]["info"]["gw"], int(ifs[n].get("weight", 1))) for n in chosen if self.state.get(n, {}).get("info")]

    def _reconcile(self, cfg, names):
        chosen = self.compute(cfg, names)
        tables = [(n, self.state[n]["info"]["ip"], self.state[n]["info"]["gw"]) for n in sorted(names) if self.state.get(n, {}).get("info")]
        dns = []
        for n, _, _ in chosen:
            dns += [d for d in self.state[n]["info"].get("dns", "").split() if d not in dns]
        sig = json.dumps([chosen, tables, dns, cfg["system"]["dns_servers"]])
        if sig == self.applied_sig:
            return
        self.applied_sig = sig
        self.active = [c[0] for c in chosen]
        self.ev("aktif uplink: %s" % (", ".join(self.active) or "yok"))
        for i, (n, ip, gw) in enumerate(tables):
            t = str(BASE_TABLE + sorted(names).index(n))
            pref = str(BASE_PREF + sorted(names).index(n))
            sysutil.run(["ip", "route", "replace", "default", "via", gw, "dev", n, "table", t], ignore=True)
            sysutil.run(["ip", "rule", "del", "pref", pref], ignore=True)
            sysutil.run(["ip", "rule", "add", "pref", pref, "from", ip, "lookup", t], ignore=True)
        self._set_main(chosen)
        servers = dns + [d for d in cfg["system"]["dns_servers"] if d not in dns]
        sysutil.write_file(os.path.join(sysutil.RUN, "resolv.upstream"), "".join("nameserver %s\n" % d for d in servers))

    def _set_main(self, chosen):
        rc, out = sysutil.run(["ip", "-j", "route", "show", "default"], ignore=True)
        try:
            for r in json.loads(out or "[]"):
                if str(r.get("metric")) != MAIN_METRIC:
                    args = ["ip", "route", "del", "default"]
                    if r.get("dev"):
                        args += ["dev", r["dev"]]
                    if r.get("metric") is not None:
                        args += ["metric", str(r["metric"])]
                    sysutil.run(args, ignore=True)
        except ValueError:
            pass
        if not chosen:
            return
        if len(chosen) == 1:
            n, gw, _ = chosen[0]
            sysutil.run(["ip", "route", "replace", "default", "via", gw, "dev", n, "metric", MAIN_METRIC], ignore=True)
        else:
            cmd = ["ip", "route", "replace", "default", "metric", MAIN_METRIC, "scope", "global"]
            for n, gw, w in chosen:
                cmd += ["nexthop", "via", gw, "dev", n, "weight", str(max(1, w))]
            sysutil.run(cmd, ignore=True)

    def status(self):
        with self.lock:
            out = []
            for n, st in sorted(self.state.items()):
                i = st.get("info") or {}
                out.append({"iface": n, "up": st["up"], "rtt": st["rtt"], "ip": i.get("ip"), "gw": i.get("gw"),
                            "proto": i.get("proto"), "active": n in self.active, "fails": st["fails"]})
            return {"uplinks": out, "events": list(self.events)[:30]}
