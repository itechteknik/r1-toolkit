"""Cekirdek render: yapilandirma -> Plan (kopruler, hostapd, wpa_supplicant, dnsmasq, nftables)."""
import hashlib
import ipaddress
import json
import os

from . import sysutil
from .plan import C, Plan
from .skills import base as skillbase

HOOK = "/usr/local/lib/r1kit/dhcp-hook.sh"
PORTAL_DNS_PORT = 5353


class Topology:
    def __init__(self, cfg):
        self.cfg = cfg
        self.nets = {}
        for n, spec in cfg["networks"].items():
            i = ipaddress.ip_interface(spec["address"]) if spec.get("address") else None
            self.nets[n] = dict(spec, name=n, br="br-" + n, iface=i, members=[])
        cp = skillbase.REGISTRY.get("captive_portal")
        portal_on = bool(cp and cp.enabled(cfg))
        for net in self.nets.values():
            net["portal"] = bool(net.get("portal")) and portal_on
        self.wans, self.stas, self.aps, self.mons = [], [], [], []
        for name, s in cfg["interfaces"].items():
            role = s.get("role", "unused")
            if role in ("lan", "ap") and s.get("network") in self.nets:
                self.nets[s["network"]]["members"].append(name)
            if role == "wan":
                self.wans.append(name)
            elif role == "sta":
                self.stas.append(name)
            elif role == "ap":
                self.aps.append(name)
            elif role == "monitor":
                self.mons.append(name)

    @property
    def uplinks(self):
        return self.wans + self.stas

    def uplink_set(self):
        return "{ " + ", ".join('"%s"' % w for w in self.uplinks) + " }"

    def portal_nets(self):
        return [n for n in self.nets.values() if n.get("portal") and n["iface"]]

    def auto_channel(self, band):
        try:
            with open(os.path.join(sysutil.VAR, "survey.json")) as f:
                v = json.load(f).get("best", {}).get(band)
                if v:
                    return v
        except (OSError, ValueError):
            pass
        return 6 if band == "2.4" else 36


def psk_hex(ssid, psk):
    if len(psk) == 64:
        return psk.lower()
    return hashlib.pbkdf2_hmac("sha1", psk.encode(), ssid.encode(), 4096, 32).hex()


def hostapd_conf(name, s, cfg, topo, blocked_file):
    band = str(s.get("band", "2.4"))
    ch = s.get("channel", "auto")
    ch = topo.auto_channel(band) if ch in ("auto", 0, "0", None) else int(ch)
    net = topo.nets[s["network"]]
    sec = s.get("security", "wpa2")
    L = ["interface=" + name, "driver=" + s.get("driver", "nl80211"), "bridge=" + net["br"],
         "ssid=" + s["ssid"], "utf8_ssid=1", "country_code=" + cfg["system"]["country"], "ieee80211d=1",
         "hw_mode=%s" % ("a" if band == "5" else "g"), "channel=%d" % ch, "ieee80211n=1", "wmm_enabled=1",
         "ctrl_interface=" + os.path.join(sysutil.RUN, "hostapd"),
         "ignore_broadcast_ssid=%d" % (1 if s.get("hidden") else 0),
         "ap_isolate=%d" % (1 if s.get("isolate") or net.get("isolate") else 0),
         "max_num_sta=%d" % int(s.get("max_clients", 32)),
         "macaddr_acl=0", "deny_mac_file=" + blocked_file,
         "ht_capab=" + ("[HT40+][SHORT-GI-20][SHORT-GI-40]" if str(s.get("width", "20")) == "40" else "[SHORT-GI-20]")]
    if band == "5":
        L += ["ieee80211h=1", "ieee80211ac=1"] if s.get("vht") else ["ieee80211h=1"]
    if sec == "open":
        L += ["auth_algs=1", "wpa=0"]
    else:
        L += ["auth_algs=1", "wpa=2", "rsn_pairwise=CCMP", "wpa_passphrase=" + s["psk"]]
        if sec == "wpa2/3":
            L += ["wpa_key_mgmt=WPA-PSK SAE", "ieee80211w=1", "sae_require_mfp=1"]
        else:
            L += ["wpa_key_mgmt=WPA-PSK", "ieee80211w=0"]
    return "\n".join(L) + "\n"


def wpa_conf(name, s, cfg):
    L = ["ctrl_interface=DIR=%s GROUP=netdev" % os.path.join(sysutil.RUN, "wpa"), "update_config=0",
         "country=" + cfg["system"]["country"], "ap_scan=1"]
    saved = list(s.get("saved", []))
    if s.get("ssid"):
        saved.insert(0, {"ssid": s["ssid"], "psk": s.get("psk", "")})
    for prio, sv in enumerate(reversed(saved)):
        ssid = sv["ssid"]
        blk = ["network={", "  ssid=" + ssid.encode().hex(), "  scan_ssid=1", "  priority=%d" % prio]
        if sv.get("psk"):
            blk += ["  psk=" + psk_hex(ssid, sv["psk"]), "  key_mgmt=WPA-PSK"]
        else:
            blk += ["  key_mgmt=NONE"]
        blk.append("}")
        L += blk
    return "\n".join(L) + "\n"


def render(cfg):
    topo = Topology(cfg)
    plan = Plan()
    G = sysutil.GEN
    ifs = cfg["interfaces"]
    plan.sysctl.update({"net.ipv4.ip_forward": "1", "net.ipv4.fib_multipath_hash_policy": "1",
                        "net.ipv4.conf.all.rp_filter": "2", "net.ipv4.conf.default.rp_filter": "2",
                        "net.ipv6.conf.all.forwarding": "0", "net.bridge.bridge-nf-call-iptables": "0"})
    blocked_file = plan.file(os.path.join(G, "hostapd.deny"), "".join(m + "\n" for m in cfg["blocked_macs"]))

    # -- koprular (her ag icin) ------------------------------------------------
    for n, net in topo.nets.items():
        br = net["br"]
        plan.pre.append(C("ip", "link", "add", br, "type", "bridge", ignore=True))
        plan.pre.append(C("ip", "addr", "flush", "dev", br))
        if net["iface"]:
            plan.pre.append(C("ip", "addr", "add", str(net["iface"]), "dev", br))
        plan.pre.append(C("ip", "link", "set", br, "up"))

    # -- arayuzler --------------------------------------------------------------
    for name, s in ifs.items():
        role = s.get("role", "unused")
        parent = s.get("parent")
        if parent and role in ("ap", "sta", "monitor"):
            typ = {"ap": "__ap", "sta": "managed", "monitor": "monitor"}[role]
            plan.pre.append(C("iw", "dev", parent, "interface", "add", name, "type", typ, ignore=True))
        if role == "unused":
            continue
        if s.get("mtu"):
            plan.pre.append(C("ip", "link", "set", name, "mtu", s["mtu"], ignore=True))
        if s.get("mac_clone"):
            plan.pre.append(C("ip", "link", "set", name, "down"))
            plan.pre.append(C("ip", "link", "set", name, "address", s["mac_clone"], ignore=True))
        plan.pre.append(C("ip", "link", "set", name, "nomaster", ignore=True))
        plan.pre.append(C("ip", "addr", "flush", "dev", name, ignore=True))

        if role == "lan":
            plan.pre += [C("ip", "link", "set", name, "master", topo.nets[s["network"]]["br"]),
                         C("ip", "link", "set", name, "up")]
            if topo.nets[s["network"]].get("isolate"):
                plan.pre.append(C("bridge", "link", "set", "dev", name, "isolated", "on", ignore=True))
        elif role == "wan":
            plan.pre.append(C("ip", "link", "set", name, "up"))
            if s.get("proto", "dhcp") == "static":
                plan.pre.append(C("ip", "addr", "add", s["address"], "dev", name))
                st = {"iface": name, "ip": s["address"].split("/")[0], "prefix": int(s["address"].split("/")[1]),
                      "gw": s["gateway"], "dns": " ".join(s.get("dns", [])), "proto": "static"}
                plan.file(os.path.join(sysutil.RUN, "uplinks", name + ".json"), json.dumps(st))
            else:
                plan.proc("udhcpc-" + name, ["udhcpc", "-f", "-i", name, "-s", HOOK, "-t", "5", "-T", "3", "-A", "10", "-S"])
        elif role == "ap":
            conf = plan.file(os.path.join(G, "hostapd-%s.conf" % name), hostapd_conf(name, s, cfg, topo, blocked_file), 0o600)
            plan.pre.append(C("ip", "link", "set", name, "down", ignore=True))
            plan.proc("hostapd-" + name, ["hostapd", conf], watch=[conf, blocked_file])
        elif role == "sta":
            plan.pre += [C("ip", "link", "set", name, "down", ignore=True),
                         C("iw", "dev", name, "set", "type", "managed", ignore=True),
                         C("ip", "link", "set", name, "up")]
            conf = plan.file(os.path.join(G, "wpa-%s.conf" % name), wpa_conf(name, s, cfg), 0o600)
            plan.proc("wpa-" + name, ["wpa_supplicant", "-i", name, "-c", conf, "-D", s.get("driver", "nl80211")], watch=[conf])
            plan.proc("udhcpc-" + name, ["udhcpc", "-f", "-i", name, "-s", HOOK, "-t", "5", "-T", "3", "-A", "10", "-S"])
        elif role == "monitor":
            plan.pre += [C("ip", "link", "set", name, "down"), C("iw", "dev", name, "set", "type", "monitor", ignore=True),
                         C("ip", "link", "set", name, "up")]
            if s.get("channel"):
                plan.pre.append(C("iw", "dev", name, "set", "channel", s["channel"], ignore=True))

    # -- dnsmasq ---------------------------------------------------------------
    upstream = os.path.join(sysutil.RUN, "resolv.upstream")
    d = ["# r1kit tarafindan uretildi", "bind-dynamic", "except-interface=lo", "domain-needed", "bogus-priv",
         "resolv-file=" + upstream, "cache-size=1000", "domain=" + cfg["system"]["domain"], "local=/%s/" % cfg["system"]["domain"],
         "expand-hosts", "dhcp-authoritative", "dhcp-leasefile=" + os.path.join(sysutil.VAR, "dnsmasq.leases"),
         "pid-file=" + os.path.join(sysutil.RUN, "dnsmasq.pid"), "dhcp-broadcast"]
    first = True
    for n, net in topo.nets.items():
        if not net["iface"]:
            continue
        ip = net["iface"]
        d.append("interface=" + net["br"])
        if first:
            d.append("host-record=%s.%s,%s" % (cfg["system"]["hostname"], cfg["system"]["domain"], ip.ip))
            first = False
        if net.get("dhcp"):
            base = int(ip.network.network_address)
            d.append("dhcp-range=set:%s,%s,%s,%s,%s" % (n, ipaddress.ip_address(base + int(net["dhcp_start"])),
                     ipaddress.ip_address(base + int(net["dhcp_end"])), ip.network.netmask, net.get("lease", "12h")))
            d.append("dhcp-option=tag:%s,option:router,%s" % (n, ip.ip))
            d.append("dhcp-option=tag:%s,option:dns-server,%s" % (n, ip.ip))
            if net.get("portal"):
                d.append("dhcp-option=tag:%s,114,http://%s/capport" % (n, ip.ip))
    for l in cfg["static_leases"]:
        d.append("dhcp-host=%s,%s,%s" % (l["mac"].lower(), l["ip"], l.get("name", "")) if l.get("name") else "dhcp-host=%s,%s" % (l["mac"].lower(), l["ip"]))
    for m in cfg["blocked_macs"]:
        d.append("dhcp-host=%s,ignore" % m)

    # -- skill'ler -------------------------------------------------------------
    for sk in skillbase.REGISTRY.values():
        if sk.enabled(cfg):
            sk.render(cfg, sk.scfg(cfg), plan, topo)
    d += plan.dnsmasq
    dconf = plan.file(os.path.join(G, "dnsmasq.conf"), "\n".join(d) + "\n")
    plan.proc("dnsmasq", ["dnsmasq", "-k", "-C", dconf], watch=[dconf])

    _firewall(cfg, topo, plan)
    return plan, topo


def _firewall(cfg, topo, plan):
    up = topo.uplink_set() if topo.uplinks else None
    plan.rule("sets", "set authed { type ether_addr; flags timeout; }")
    blocked = ", ".join(cfg["blocked_macs"])
    plan.rule("sets", "set blocked { type ether_addr; %s}" % ("elements = { %s } ; " % blocked if blocked else ""))
    plan.rule("chains", "chain clients { }")

    R = plan.rule
    R("input", "ct state established,related accept", 0)
    R("input", "ct state invalid drop", 0)
    R("input", 'iifname "lo" accept', 0)
    R("input", "ether saddr @blocked drop", 1)
    R("input", "ip protocol icmp icmp type { echo-request, destination-unreachable, time-exceeded } limit rate 20/second accept", 2)
    R("input", "meta l4proto ipv6-icmp accept", 2)
    for net in topo.nets.values():
        if not net["iface"]:
            continue
        br = net["br"]
        if net.get("portal"):
            R("input", 'iifname "%s" ether saddr != @authed udp dport { 53, 67, %d } accept' % (br, PORTAL_DNS_PORT), 10)
            R("input", 'iifname "%s" ether saddr != @authed tcp dport { 53, 80, %d } accept' % (br, PORTAL_DNS_PORT), 10)
            R("input", 'iifname "%s" ether saddr != @authed drop' % br, 11)
        if cfg["admin"].get("ssh_from_lan", True) and not net.get("portal") and not net.get("isolate"):
            R("input", 'iifname "%s" tcp dport 22 accept' % br, 20)
        R("input", 'iifname "%s" udp dport { 53, 67 } accept' % br, 20)
        R("input", 'iifname "%s" tcp dport { 53, 80, 8080 } accept' % br, 20)
        R("input", 'iifname "%s" ip protocol { icmp, igmp } accept' % br, 21)
    if up and cfg["admin"].get("wan_access"):
        R("input", 'iifname %s tcp dport { 80, 8080 } accept' % up, 30)

    R("forward", "ct state established,related accept", 0)
    R("forward", "ct state invalid drop", 0)
    R("forward", "ether saddr @blocked drop", 1)
    R("forward", "tcp flags syn tcp option maxseg size set rt mtu", 2)
    R("forward", "jump clients", 3)
    for net in topo.portal_nets():
        R("forward", 'iifname "%s" ether saddr != @authed meta l4proto tcp reject with tcp reset' % net["br"], 5)
        R("forward", 'iifname "%s" ether saddr != @authed reject' % net["br"], 5)
        R("prerouting", 'iifname "%s" ether saddr != @authed udp dport 53 redirect to :%d' % (net["br"], PORTAL_DNS_PORT), 5)
        R("prerouting", 'iifname "%s" ether saddr != @authed tcp dport 53 redirect to :%d' % (net["br"], PORTAL_DNS_PORT), 5)
        R("prerouting", 'iifname "%s" ether saddr != @authed tcp dport 80 redirect to :80' % net["br"], 5)
    for n, net in topo.nets.items():
        if up and net.get("egress", "default") == "default" and net["iface"]:
            R("forward", 'iifname "%s" oifname %s accept' % (net["br"], up), 20)
        for other in net.get("allow_to", []):
            if other in topo.nets:
                R("forward", 'iifname "%s" oifname "%s" accept' % (net["br"], topo.nets[other]["br"]), 21)
    if up:
        for pf in cfg["port_forwards"]:
            c = pf.get("comment", "").replace('"', "")[:40]
            R("prerouting", 'iifname %s %s dport %d dnat ip to %s:%d comment "%s"' % (up, pf["proto"], int(pf["wan_port"]), pf["to_ip"], int(pf["to_port"]), c), 20)
        if cfg["port_forwards"]:
            R("forward", "ct status dnat accept", 25)
        R("postrouting", "oifname %s masquerade" % up, 50)
