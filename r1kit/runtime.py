"""Calisma zamani cekirdegi: apply, portal yetkileri, istemciler, metrikler, guvenli uygulama."""
import collections
import copy
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time

from . import auth, config, netdev, presets, sysutil, __version__
from .plan import Cmd
from .render import Topology, render
from .skills import REGISTRY
from .supervisor import Supervisor
from .uplink import UplinkManager

log = logging.getLogger("r1kit")


class Runtime:
    def __init__(self, store=None):
        sysutil.ensure_dirs()
        self.store = store or config.Store()
        self.store.load()
        self.sessions = auth.Sessions(self.store.get()["admin"]["session_ttl"])
        self.sup = Supervisor()
        self.uplinks = UplinkManager(self.store)
        self.apply_lock = threading.Lock()
        self.authed = {}                  # mac -> {"exp","ip","how"}
        self.pending = None               # guvenli uygulama geri alma
        self.samples = collections.deque(maxlen=120)
        self._last_bytes = {}
        self._last_cpu = None
        self.client_sig = None
        self.started = time.time()
        self.last_apply = None
        self.stop = threading.Event()
        self.first_run_password = self._ensure_admin()
        self.first_run_wifi = self._ensure_defaults()

    # ------------------------------------------------------------------ yonetici
    def _ensure_admin(self):
        cfg = self.store.get()
        if cfg["admin"].get("password_hash"):
            return None
        pw = secrets.token_urlsafe(9)
        def fn(c):
            c["admin"]["password_hash"] = auth.hash_password(pw)
            c["admin"]["must_change"] = True
        self.store.update(fn)
        sysutil.write_file(os.path.join(sysutil.ETC, "first-run-password"), pw + "\n", 0o600)
        return pw

    def _ensure_defaults(self):
        """Hic arayuz yapilandirilmamissa donanima gore baslangic ag kur (panele ulasilabilsin)."""
        cfg = self.store.get()
        if cfg["interfaces"]:
            return None
        hw = netdev.suggest_roles(netdev.list_interfaces())
        new = presets.default_for(hw["wired"], hw["wifi"])
        new["admin"], new["system"] = cfg["admin"], cfg["system"]
        if not new["interfaces"]:
            return None
        self.store.set(new)
        ap = next(((n, s) for n, s in new["interfaces"].items() if s.get("role") == "ap"), None)
        if ap:
            note = "Wi-Fi SSID: %s  parola: %s\n" % (ap[1]["ssid"], ap[1]["psk"])
            sysutil.write_file(os.path.join(sysutil.ETC, "first-run-wifi"), note, 0o600)
            return note.strip()
        return None

    def set_password(self, new):
        if len(new) < 8:
            raise ValueError("parola en az 8 karakter olmali")
        def fn(c):
            c["admin"]["password_hash"] = auth.hash_password(new)
            c["admin"]["must_change"] = False
        self.store.update(fn)
        for f in ("first-run-password", "first-run-wifi"):
            try:
                os.unlink(os.path.join(sysutil.ETC, f))
            except OSError:
                pass
        self.sessions.drop_all()

    # -------------------------------------------------------------------- yardimcilar
    def topo(self):
        return Topology(self.store.get())

    def run_cmd(self, c: Cmd):
        rc, _ = sysutil.run(c.argv, ignore=c.ignore or c.alt is not None)
        if rc != 0 and c.alt:
            self.run_cmd(c.alt)

    # -------------------------------------------------------------------- apply
    def apply(self, safe=False, confirm_s=90):
        with self.apply_lock:
            for sk in REGISTRY.values():
                if sk.enabled(self.store.get()):
                    sk.prepare(self.store)
            cfg = self.store.get()
            prev = self._load_applied()
            plan, topo = render(cfg)
            for k, v in plan.sysctl.items():
                sysutil.run(["sysctl", "-qw", "%s=%s" % (k, v)], ignore=True)
            for path, (content, mode) in plan.files.items():
                sysutil.write_file(path, content, mode)
            self.sup.stop_stale(plan.procs)
            self._remove_stale(topo, cfg)
            for c in plan.pre:
                self.run_cmd(c)
            nft = os.path.join(sysutil.GEN, "r1kit.nft")
            sysutil.write_file(nft, plan.nft_text(), 0o600)
            rc, out = sysutil.run(["nft", "-f", nft])
            if rc != 0:
                raise RuntimeError("nftables kurallari yuklenemedi: " + out.strip()[:400])
            self.sup.start_all(plan.procs)
            for c in plan.post:
                self.run_cmd(c)
            self._restore_authed()
            self.uplinks.applied_sig = None
            self.client_sig = None
            self._save_applied(cfg, topo)
            self.last_apply = time.time()
            if safe and prev is not None:
                self._arm_rollback(prev, confirm_s)
            return {"ok": True, "procs": list(plan.procs), "warnings": plan.warnings,
                    "safe": bool(safe and prev is not None), "confirm_s": confirm_s if safe else 0}

    def _load_applied(self):
        try:
            with open(os.path.join(sysutil.VAR, "applied.json")) as f:
                return json.load(f)["config"]
        except (OSError, ValueError, KeyError):
            return None

    def _save_applied(self, cfg, topo):
        virt = [n for n, s in cfg["interfaces"].items() if s.get("parent")]
        sysutil.write_file(os.path.join(sysutil.VAR, "applied.json"), json.dumps(
            {"config": cfg, "bridges": [n["br"] for n in topo.nets.values()], "virtual": virt}), 0o600)

    def _remove_stale(self, topo, cfg):
        try:
            with open(os.path.join(sysutil.VAR, "applied.json")) as f:
                old = json.load(f)
        except (OSError, ValueError):
            return
        keep = {n["br"] for n in topo.nets.values()}
        for br in old.get("bridges", []):
            if br not in keep:
                sysutil.run(["ip", "link", "set", br, "down"], ignore=True)
                sysutil.run(["ip", "link", "del", br], ignore=True)
        for v in old.get("virtual", []):
            if v not in cfg["interfaces"]:
                sysutil.run(["iw", "dev", v, "del"], ignore=True)

    def _arm_rollback(self, prev, secs):
        self.cancel_pending()
        t = threading.Timer(secs, self._rollback, args=(prev,))
        t.daemon = True
        self.pending = {"timer": t, "deadline": time.time() + secs}
        t.start()

    def _rollback(self, prev):
        log.warning("guvenli uygulama onaylanmadi: onceki yapilandirmaya donuluyor")
        self.pending = None
        try:
            self.store.set(prev, backup=False)
            self.apply(safe=False)
        except Exception:
            log.exception("geri alma basarisiz")

    def cancel_pending(self):
        if self.pending:
            self.pending["timer"].cancel()
            self.pending = None
            return True
        return False

    # -------------------------------------------------------------------- portal yetkisi
    def mac_for_ip(self, ip):
        for line in sysutil.read_file(sysutil.P("/proc/net/arp")).splitlines()[1:]:
            f = line.split()
            if len(f) >= 4 and f[0] == ip and f[3] != "00:00:00:00:00:00":
                return f[3].lower()
        rc, out = sysutil.run(["ip", "neigh", "show", ip], ignore=True)
        m = re.search(r"lladdr ([0-9a-f:]{17})", out)
        return m.group(1) if m else None

    def authorize(self, mac, minutes, ip="", how="guest"):
        secs = max(60, int(minutes) * 60)
        self.authed[mac] = {"exp": time.time() + secs, "ip": ip, "how": how}
        sysutil.run(["nft", "add", "element", "inet", "r1kit", "authed", "{", mac, "timeout", "%ds" % secs, "}"])

    def revoke(self, mac):
        self.authed.pop(mac, None)
        sysutil.run(["nft", "delete", "element", "inet", "r1kit", "authed", "{", mac, "}"], ignore=True)

    def _restore_authed(self):
        now = time.time()
        for mac, a in list(self.authed.items()):
            if a["exp"] <= now:
                del self.authed[mac]
            else:
                sysutil.run(["nft", "add", "element", "inet", "r1kit", "authed", "{", mac, "timeout", "%ds" % int(a["exp"] - now), "}"], ignore=True)

    # -------------------------------------------------------------------- kuponlar
    def _vfile(self):
        return os.path.join(sysutil.VAR, "vouchers.json")

    def vouchers(self):
        try:
            with open(self._vfile()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_vouchers(self, v):
        sysutil.write_file(self._vfile(), json.dumps(v), 0o600)

    def create_vouchers(self, count, minutes, max_devices=1):
        v = self.vouchers()
        made = []
        for _ in range(min(int(count), 100)):
            code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
            v[code] = {"minutes": int(minutes), "max_devices": int(max_devices), "used_by": [], "created": int(time.time())}
            made.append(code)
        self.save_vouchers(v)
        return made

    def redeem(self, code, mac, ip):
        v = self.vouchers()
        e = v.get(code.strip().upper())
        if not e:
            return None
        if mac not in e["used_by"]:
            if len(e["used_by"]) >= e["max_devices"]:
                return None
            e["used_by"].append(mac)
            self.save_vouchers(v)
        return e["minutes"]

    # -------------------------------------------------------------------- istemciler
    def clients(self):
        cfg = self.store.get()
        topo = Topology(cfg)
        leases = {}
        for line in sysutil.read_file(os.path.join(sysutil.VAR, "dnsmasq.leases")).splitlines():
            f = line.split()
            if len(f) >= 4:
                leases[f[1].lower()] = {"ip": f[2], "name": "" if f[3] == "*" else f[3], "lease_exp": int(f[0])}
        stations = {}
        for ap in topo.aps:
            rc, out = sysutil.run(["iw", "dev", ap, "station", "dump"], ignore=True)
            for blk in re.split(r"(?m)^Station ", out)[1:]:
                m = re.match(r"([0-9a-f:]{17})", blk)
                if m:
                    sig = re.search(r"signal:\s+(-?\d+)", blk)
                    rate = re.search(r"tx bitrate:\s+([\d.]+)", blk)
                    stations[m.group(1)] = {"wifi": ap, "signal": int(sig.group(1)) if sig else None,
                                            "rate": float(rate.group(1)) if rate else None}
        for line in sysutil.read_file(sysutil.P("/proc/net/arp")).splitlines()[1:]:
            f = line.split()
            if len(f) >= 6 and f[2] != "0x0" and f[3] != "00:00:00:00:00:00" and f[5].startswith("br-"):
                leases.setdefault(f[3].lower(), {"ip": f[0], "name": "", "lease_exp": 0})
        counters = self._read_counters()
        out = []
        for mac, l in leases.items():
            net = next((n["name"] for n in topo.nets.values() if n["iface"] and l["ip"] and
                        ipaddress.ip_address(l["ip"]) in n["iface"].network), "")
            c = counters.get(mac, {})
            out.append(dict(l, mac=mac, network=net, authed=mac in self.authed or not (net and topo.nets[net].get("portal")),
                            blocked=mac in cfg["blocked_macs"], limit=cfg["client_limits"].get(mac), up=c.get("u", 0),
                            down=c.get("d", 0), **stations.get(mac, {})))
        return sorted(out, key=lambda c: c["ip"])

    def kick(self, mac):
        for ap in self.topo().aps:
            sysutil.run(["hostapd_cli", "-p", os.path.join(sysutil.RUN, "hostapd"), "-i", ap, "deauthenticate", mac], ignore=True)
        self.revoke(mac)

    def apply_client_policy(self):
        """'clients' zincirini bastan kurar: istemci basina hiz siniri ve sayaclar."""
        cfg = self.store.get()
        L = ["flush chain inet r1kit clients"]
        for c in self.clients():
            m, ip = c["mac"], c["ip"]
            lim = cfg["client_limits"].get(m)
            if lim:
                if lim.get("up_mbps"):
                    L.append("add rule inet r1kit clients ether saddr %s limit rate over %d bytes/second drop" % (m, int(float(lim["up_mbps"]) * 125000)))
                if lim.get("down_mbps"):
                    L.append("add rule inet r1kit clients ip daddr %s limit rate over %d bytes/second drop" % (ip, int(float(lim["down_mbps"]) * 125000)))
            L.append('add rule inet r1kit clients ether saddr %s counter comment "u:%s"' % (m, m))
            L.append('add rule inet r1kit clients ip daddr %s counter comment "d:%s"' % (ip, m))
        path = os.path.join(sysutil.GEN, "clients.nft")
        sysutil.write_file(path, "\n".join(L) + "\n", 0o600)
        sysutil.run(["nft", "-f", path], ignore=True)

    def _read_counters(self):
        rc, out = sysutil.run(["nft", "-j", "list", "chain", "inet", "r1kit", "clients"], ignore=True)
        res = {}
        try:
            for item in json.loads(out or "{}").get("nftables", []):
                r = item.get("rule")
                if not r or not r.get("comment"):
                    continue
                kind, mac = r["comment"].split(":", 1)
                for e in r["expr"]:
                    if "counter" in e:
                        res.setdefault(mac, {})[kind] = e["counter"]["bytes"]
        except (ValueError, KeyError):
            pass
        return res

    # -------------------------------------------------------------------- metrikler
    def sample(self):
        now = time.time()
        cpu = None
        f = sysutil.read_file(sysutil.P("/proc/stat")).split("\n")[0].split()[1:8]
        if f:
            v = list(map(int, f))
            tot, idle = sum(v), v[3] + v[4]
            if self._last_cpu:
                dt, di = tot - self._last_cpu[0], idle - self._last_cpu[1]
                cpu = round(100 * (1 - di / dt), 1) if dt else 0
            self._last_cpu = (tot, idle)
        mem = {}
        for l in sysutil.read_file(sysutil.P("/proc/meminfo")).splitlines():
            k, _, v = l.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(v.split()[0]) // 1024
        try:
            temp = int(sysutil.read_file(sysutil.P("/sys/class/thermal/thermal_zone0/temp"), "0")) / 1000
        except ValueError:
            temp = None
        rates = {}
        for i in netdev.list_interfaces():
            last = self._last_bytes.get(i["name"])
            if last and now > last[0]:
                rates[i["name"]] = {"rx": max(0, (i["rx"] - last[1]) * 8 / (now - last[0])),
                                    "tx": max(0, (i["tx"] - last[2]) * 8 / (now - last[0]))}
            self._last_bytes[i["name"]] = (now, i["rx"], i["tx"])
        load = sysutil.read_file(sysutil.P("/proc/loadavg"), "0 0 0").split()[0]
        self.samples.append({"t": int(now), "cpu": cpu, "mem_total": mem.get("MemTotal"), "mem_avail": mem.get("MemAvailable"),
                             "temp": temp, "load": float(load), "rates": rates})

    def status(self):
        cfg = self.store.get()
        return {"version": __version__, "uptime": int(time.time() - self.started), "hostname": cfg["system"]["hostname"],
                "procs": self.sup.status(), "pending_confirm": int(self.pending["deadline"] - time.time()) if self.pending else 0,
                "last_apply": self.last_apply, "last": self.samples[-1] if self.samples else None,
                "uplinks": self.uplinks.status(), "authed": len(self.authed), "dry": sysutil.DRY}

    # -------------------------------------------------------------------- doctor
    def doctor(self):
        checks = []
        def add(name, ok, hint=""):
            checks.append({"name": name, "ok": bool(ok), "hint": hint})
        for b in ("ip", "nft", "hostapd", "dnsmasq", "udhcpc", "wpa_supplicant", "iw", "tc", "wg", "tcpdump", "ping", "traceroute", "hostapd_cli"):
            add("binary: " + b, sysutil.have(b), "apt install ile kurun (bkz. install.sh)")
        for m in ("sch_cake", "wireguard", "nf_tables", "bridge", "ifb"):
            found = os.path.isdir(sysutil.P("/sys/module/" + m)) or os.path.exists(sysutil.P("/sys/module/%s" % m))
            add("kernel modul: " + m, found, "modprobe %s (kernel destegi gerekebilir)" % m)
        rc, out = sysutil.run(["systemctl", "is-active", "NetworkManager"], ignore=True)
        add("NetworkManager kapali/unmanaged", out.strip() != "active", "install.sh unmanaged yapar; yine de cakisirsa: systemctl disable --now NetworkManager")
        for i in netdev.list_interfaces():
            if i["type"] == "wifi":
                w = i.get("wifi", {})
                add("%s: AP modu" % i["name"], "AP" in w.get("modes", []), "surucu AP modunu desteklemiyor: hostapd calismaz")
        return checks
