"""Hazir senaryolar. Fiziksel arayuzler soyut yuvalara eslenir: wired[0]=onboard, wired[1]=USB, wifi[0], wifi[1]."""
import copy
import secrets

from .config import DEFAULT, deep_merge

PRESETS = {
    "router": {
        "title": "Ev/Ofis Router",
        "desc": "Onboard ethernet=WAN, USB ethernet+Wi-Fi AP = LAN. Klasik NAT router + AP.",
        "build": lambda w, f: {
            "interfaces": {w[0]: {"role": "wan"}, w[1]: {"role": "lan", "network": "lan"},
                            f[0]: {"role": "ap", "network": "lan", "ssid": "R1-Net", "psk": "changeme-r1kit", "band": "2.4", "channel": "auto"}}},
    },
    "dual_wan": {
        "title": "Cift WAN (failover / yuk dengeleme)",
        "desc": "Iki ethernet WAN (or. iki modem/ISP), Wi-Fi AP LAN. Saglik kontrolu ile otomatik gecis.",
        "build": lambda w, f: {
            "interfaces": {w[0]: {"role": "wan", "priority": 10}, w[1]: {"role": "wan", "priority": 20},
                            f[0]: {"role": "ap", "network": "lan", "ssid": "R1-DualWAN", "psk": "changeme-r1kit", "channel": "auto"}},
            "uplink": {"mode": "failover"}},
    },
    "wifi_repeater": {
        "title": "Wi-Fi Tekrarlayici / Seyahat Router",
        "desc": "wifi[1] mevcut Wi-Fi'a baglanir (uplink), wifi[0] kendi AP'nizi yayar, ethernet portu LAN.",
        "build": lambda w, f: {
            "interfaces": {f[-1]: {"role": "sta", "ssid": "UPSTREAM_SSID", "psk": "upstream-password"},
                            f[0]: {"role": "ap", "network": "lan", "ssid": "R1-Travel", "psk": "changeme-r1kit", "channel": "auto"},
                            w[0]: {"role": "lan", "network": "lan"}}},
    },
    "wifi_to_ethernet": {
        "title": "Wi-Fi -> Ethernet Koprusu",
        "desc": "Wi-Fi'siz cihaza (TV, yazici, konsol) Wi-Fi uzerinden internet verin: wifi[0] uplink, iki ethernet LAN.",
        "build": lambda w, f: {
            "interfaces": {f[0]: {"role": "sta", "ssid": "UPSTREAM_SSID", "psk": "upstream-password"},
                            w[0]: {"role": "lan", "network": "lan"}, w[1]: {"role": "lan", "network": "lan"}}},
    },
    "guest_portal": {
        "title": "Misafir Wi-Fi + Captive Portal",
        "desc": "Ana ag (ethernet) ve izole misafir agi (Wi-Fi + portal). Misafirler yerel aga erisemez.",
        "build": lambda w, f: {
            "networks": {"lan": {"address": "192.168.50.1/24"},
                         "guest": {"address": "192.168.60.1/24", "portal": True, "isolate": True, "dhcp": True,
                                   "dhcp_start": 20, "dhcp_end": 230, "lease": "4h", "egress": "default", "allow_to": []}},
            "interfaces": {w[0]: {"role": "wan"}, w[1]: {"role": "lan", "network": "lan"},
                            f[0]: {"role": "ap", "network": "guest", "ssid": "R1-Guest", "security": "open", "channel": "auto"}},
            "skills": {"captive_portal": {"enabled": True}}},
    },
    "inline_tap": {
        "title": "Seffaf Kopru / Inline Tap",
        "desc": "Iki ethernet portu adressiz L2 kopru olur (arada gecen trafik izlenebilir: 'Yakalama' skill'i). Yonetim Wi-Fi AP uzerinden.",
        "build": lambda w, f: {
            "networks": {"lan": {"address": "192.168.50.1/24"}, "tap": {"address": "", "dhcp": False}},
            "interfaces": {w[0]: {"role": "lan", "network": "tap"}, w[1]: {"role": "lan", "network": "tap"},
                            f[0]: {"role": "ap", "network": "lan", "ssid": "R1-Mgmt", "psk": "changeme-r1kit", "channel": "auto"}}},
    },
    "vpn_gateway": {
        "title": "VPN Gecidi",
        "desc": "Router + tum LAN trafigi WireGuard uzerinden (kill-switch). Sunucu bilgilerini Skills > WireGuard'a girin.",
        "build": lambda w, f: {
            "networks": {"lan": {"egress": "vpn"}},
            "interfaces": {w[0]: {"role": "wan"}, w[1]: {"role": "lan", "network": "lan"},
                            f[0]: {"role": "ap", "network": "lan", "ssid": "R1-VPN", "psk": "changeme-r1kit", "channel": "auto"}},
            "skills": {"wireguard": {"enabled": True, "mode": "client"}}},
    },
}


NEEDS = {"router": (2, 1), "dual_wan": (2, 1), "wifi_repeater": (1, 2), "wifi_to_ethernet": (2, 1),
         "guest_portal": (2, 1), "inline_tap": (2, 1), "vpn_gateway": (2, 1)}


def build(name, wired, wifi):
    """wired/wifi: arayuz adlari (onboard once). Yeterli arayuz yoksa ValueError."""
    nw, nf = NEEDS[name]
    if len(wired) < nw or len(wifi) < nf:
        raise ValueError("'%s' icin en az %d ethernet ve %d wifi gerekli (bulunan: %d/%d)" % (name, nw, nf, len(wired), len(wifi)))
    patch = PRESETS[name]["build"](wired, wifi)
    cfg = copy.deepcopy(DEFAULT)
    cfg["networks"] = deep_merge({"lan": DEFAULT["networks"]["lan"]}, patch.pop("networks", {}))
    cfg = deep_merge(cfg, patch)
    for spec in cfg["interfaces"].values():          # bilinen varsayilan parola birakma
        if spec.get("psk") == "changeme-r1kit":
            spec["psk"] = secrets.token_urlsafe(9)
    return cfg


def default_for(wired, wifi):
    """Ilk acilista panele ulasilabilsin diye donanima gore makul bir baslangic yapilandirmasi."""
    if len(wired) >= 2 and wifi:
        return build("router", wired, wifi)
    cfg = copy.deepcopy(DEFAULT)
    if len(wired) >= 2:
        cfg["interfaces"] = {wired[0]: {"role": "wan"}, wired[1]: {"role": "lan", "network": "lan"}}
    else:                                            # tek ethernet: yonetim icin LAN yap
        cfg["interfaces"] = {n: {"role": "lan", "network": "lan"} for n in wired[:1]}
    if wifi:
        cfg["interfaces"][wifi[0]] = {"role": "ap", "network": "lan", "ssid": "R1-Setup", "psk": secrets.token_urlsafe(9), "channel": "auto"}
    return cfg
