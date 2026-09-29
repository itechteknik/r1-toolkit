import base64
import os
import secrets

from .. import sysutil
from ..plan import C
from .base import Skill, F, register

IF = "wg-r1"
TABLE = 200


def _genkey():
    rc, priv = sysutil.run(["wg", "genkey"], ignore=True)
    if rc == 0 and priv.strip() and not sysutil.DRY:
        rc, pub = sysutil.run(["wg", "pubkey"], input=priv, ignore=True)
        return priv.strip(), pub.strip()
    return base64.b64encode(secrets.token_bytes(32)).decode(), ""


@register
class WireGuard(Skill):
    name = "wireguard"
    title = "WireGuard VPN (istemci / sunucu)"
    desc = ("Istemci: 'egress=vpn' olan aglarin tum trafigi tunelden cikar (kill-switch dahil: tunel dusunce internet kesilir). "
            "Sunucu: uzaktan eve guvenli erisim.")
    category = "guvenlik"
    needs = ["wg"]
    fields = [
        F("mode", "Mod", "select", "client", ["client", "server"]),
        F("private_key", "Ozel anahtar (otomatik)", "password", ""),
        F("public_key", "Genel anahtar", "text", ""),
        F("address", "Tunel adresi (CIDR)", "text", "10.66.0.2/24"),
        F("listen_port", "Dinleme portu", "int", 51820),
        F("peer_public_key", "[istemci] Sunucu genel anahtari", "text", ""),
        F("peer_psk", "[istemci] On paylasimli anahtar (ops.)", "password", ""),
        F("endpoint", "[istemci] Sunucu host:port", "text", ""),
        F("dns", "[istemci] VPN DNS", "text", ""),
        F("peers", "[sunucu] Eşler (JSON: [{public_key, allowed_ips}])", "json", []),
        F("allow_lan", "[sunucu] VPN istemcileri LAN'a erisebilsin", "bool", True),
    ]

    def prepare(self, store):
        def fn(cfg):
            s = cfg["skills"].setdefault(self.name, {})
            if s.get("enabled") and not s.get("private_key"):
                s["private_key"], s["public_key"] = _genkey()
        store.update(fn)

    def render(self, cfg, s, plan, topo):
        if not s.get("private_key"):
            plan.warnings.append("wireguard: ozel anahtar yok")
            return
        L = ["[Interface]", "PrivateKey = " + s["private_key"]]
        if s["mode"] == "server":
            L.append("ListenPort = %d" % int(s["listen_port"]))
            for p in s.get("peers", []):
                L += ["", "[Peer]", "PublicKey = " + p["public_key"], "AllowedIPs = " + p.get("allowed_ips", "10.66.0.3/32")]
        else:
            if not (s.get("peer_public_key") and s.get("endpoint")):
                plan.warnings.append("wireguard: sunucu anahtari/endpoint eksik")
                return
            L += ["", "[Peer]", "PublicKey = " + s["peer_public_key"], "Endpoint = " + s["endpoint"],
                  "AllowedIPs = 0.0.0.0/0", "PersistentKeepalive = 25"]
            if s.get("peer_psk"):
                L.append("PresharedKey = " + s["peer_psk"])
        conf = plan.file(os.path.join(sysutil.GEN, IF + ".conf"), "\n".join(L) + "\n", 0o600)
        plan.pre += [C("ip", "link", "add", IF, "type", "wireguard", ignore=True),
                     C("wg", "setconf", IF, conf),
                     C("ip", "addr", "replace", s["address"], "dev", IF),
                     C("ip", "link", "set", IF, "up")]
        up = topo.uplink_set()
        if s["mode"] == "client":
            plan.pre.append(C("ip", "route", "replace", "default", "dev", IF, "table", TABLE))
            for _ in range(len(topo.nets) + 1):
                plan.pre.append(C("ip", "rule", "del", "pref", "200", ignore=True))
            for n in topo.nets.values():
                if n.get("egress") == "vpn":
                    plan.pre.append(C("ip", "rule", "add", "pref", "200", "iif", n["br"], "lookup", str(TABLE)))
                    plan.rule("forward", 'iifname "%s" oifname "%s" accept' % (n["br"], IF), 20)
            plan.rule("postrouting", 'oifname "%s" masquerade' % IF, 50)
            if s.get("dns"):
                plan.dnsmasq.append("server=" + s["dns"])
        else:
            if topo.uplinks:
                plan.rule("input", "iifname %s udp dport %d accept" % (up, int(s["listen_port"])), 30)
                plan.rule("forward", 'iifname "%s" oifname %s accept' % (IF, up), 20)
            if s.get("allow_lan"):
                for n in topo.nets.values():
                    if n["iface"]:
                        plan.rule("forward", 'iifname "%s" oifname "%s" accept' % (IF, n["br"]), 21)
            plan.rule("input", 'iifname "%s" udp dport 53 accept' % IF, 30)
            plan.rule("input", 'iifname "%s" tcp dport { 53, 80 } accept' % IF, 30)

    def status(self, cfg, s, topo):
        rc, out = sysutil.run(["wg", "show", IF, "latest-handshakes"], ignore=True)
        return {"up": rc == 0, "handshakes": out.strip()}
