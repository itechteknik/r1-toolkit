import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = tempfile.mkdtemp(prefix="r1t-")
os.environ.update(R1KIT_ROOT=ROOT, R1KIT_DRY="1", R1KIT_FAKE_HW="1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from r1kit import auth, config, netdev, portaldns, presets, render, sysutil  # noqa: E402
from r1kit.skills.wifisurvey import best_channels, parse_scan  # noqa: E402

HW = netdev.suggest_roles(netdev.list_interfaces())


def cfg_for(name, **skills):
    c = presets.build(name, HW["wired"], HW["wifi"])
    for k, v in skills.items():
        c["skills"][k] = v
    return c


@pytest.mark.parametrize("name", list(presets.PRESETS))
def test_presets_valid_and_render(name):
    c = cfg_for(name)
    assert config.validate(c) == []
    plan, _ = render.render(c)
    assert "table inet r1kit" in plan.nft_text()
    assert "dnsmasq" in plan.procs


@pytest.mark.skipif(not shutil.which("nft"), reason="nft yok")
@pytest.mark.parametrize("name", list(presets.PRESETS))
def test_nft_syntax(name, tmp_path):
    plan, _ = render.render(cfg_for(name, adblock={"enabled": True}, qos={"enabled": True}))
    f = tmp_path / "r.nft"
    f.write_text(plan.nft_text())
    r = subprocess.run(["nft", "-c", "-f", str(f)], capture_output=True, text=True)
    if "Operation not permitted" in r.stderr:
        pytest.skip("nft -c icin yetki yok")
    assert r.returncode == 0, r.stderr


def test_dual_wan_and_wifi_roles():
    plan, topo = render.render(cfg_for("dual_wan"))
    assert set(topo.wans) == {"end0", "eth1"}
    assert "hostapd-wlan0" in plan.procs and "udhcpc-end0" in plan.procs and "udhcpc-eth1" in plan.procs
    assert 'oifname { "end0", "eth1" } masquerade' in plan.nft_text()


def test_repeater_uses_both_radios():
    plan, topo = render.render(cfg_for("wifi_repeater"))
    assert topo.stas == ["wlan1"] and topo.aps == ["wlan0"]
    assert "wpa-wlan1" in plan.procs and "hostapd-wlan0" in plan.procs
    conf = plan.files[[k for k in plan.files if k.endswith("wpa-wlan1.conf")][0]][0]
    assert "psk=" in conf and "upstream-password" not in conf       # parola turetilmis PSK olarak yazilir


def test_portal_rules_only_when_enabled():
    c = cfg_for("guest_portal")
    nft = render.render(c)[0].nft_text()
    assert 'iifname "br-guest" ether saddr != @authed tcp dport 80 redirect to :80' in nft
    assert "reject with tcp reset" in nft
    c["skills"]["captive_portal"]["enabled"] = False
    assert "redirect to :80" not in render.render(c)[0].nft_text()


def test_guest_isolated_from_lan_and_no_ssh():
    nft = render.render(cfg_for("guest_portal"))[0].nft_text()
    assert 'iifname "br-guest" oifname "br-lan"' not in nft
    assert 'iifname "br-guest" tcp dport 22' not in nft


def test_vpn_killswitch_has_no_direct_egress():
    c = cfg_for("vpn_gateway")
    c["skills"]["wireguard"].update(private_key="k" * 43 + "=", peer_public_key="p" * 43 + "=", endpoint="1.2.3.4:51820")
    plan, _ = render.render(c)
    nft = plan.nft_text()
    assert 'iifname "br-lan" oifname "wg-r1" accept' in nft
    assert 'iifname "br-lan" oifname { "end0" } accept' not in nft


def test_tap_has_no_ip():
    plan, topo = render.render(cfg_for("inline_tap"))
    assert topo.nets["tap"]["iface"] is None
    assert ["ip", "link", "set", "end0", "master", "br-tap"] in [c.argv for c in plan.pre]


@pytest.mark.parametrize("bad", [
    lambda c: c["interfaces"]["wlan0"].update(ssid='a"b\nwpa=0'),
    lambda c: c["interfaces"]["wlan0"].update(psk="short"),
    lambda c: c["networks"]["lan"].update(address="10.0.0.1/31"),
    lambda c: c["port_forwards"].append({"proto": "tcp", "wan_port": 1, "to_ip": "x", "to_port": 1}),
    lambda c: c["blocked_macs"].append("zz"),
])
def test_validation_rejects(bad):
    c = cfg_for("router")
    bad(c)
    assert config.validate(c)


def test_password_hash():
    h = auth.hash_password("s3cret-pass")
    assert auth.verify_password("s3cret-pass", h) and not auth.verify_password("x", h)


def test_login_lockout():
    s = auth.Sessions()
    for _ in range(5):
        s.fail("1.1.1.1")
    assert s.locked("1.1.1.1") and not s.locked("2.2.2.2")


def test_portal_dns_reply():
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"
    r = portaldns.build_reply(q, "192.168.60.1")
    assert r[:2] == b"\x12\x34" and r.endswith(bytes([192, 168, 60, 1]))
    q6 = q[:-4] + b"\x00\x1c\x00\x01"
    assert portaldns.build_reply(q6, "192.168.60.1")[6:8] == b"\x00\x00"       # AAAA -> bos cevap


def test_wifi_scan_parse_and_channel_choice():
    text = ("BSS aa:bb:cc:dd:ee:01(on wlan1)\n\tfreq: 2437\n\tsignal: -40.00 dBm\n\tSSID: Strong\n\tRSN: * Version: 1\n"
            "BSS aa:bb:cc:dd:ee:02(on wlan1)\n\tfreq: 2412\n\tsignal: -80.00 dBm\n\tSSID: Weak\n")
    aps = parse_scan(text)
    assert [a["channel"] for a in aps] == [6, 1] and aps[0]["secure"]
    best, score = best_channels(aps)
    assert best == 11 and score[6] > score[11]


def test_apply_and_safe_rollback():
    from r1kit.runtime import Runtime
    rt = Runtime()
    rt.store.set(cfg_for("router"))
    r = rt.apply()
    assert r["ok"] and "hostapd-wlan0" in r["procs"]
    c = rt.store.get()
    c["interfaces"]["wlan0"]["ssid"] = "Changed"
    rt.store.set(c)
    r = rt.apply(safe=True, confirm_s=60)
    assert r["safe"] and rt.pending
    assert rt.cancel_pending() and not rt.pending
    rt.sup.stop_all()


def test_http_auth_flow():
    import http.client
    import threading
    from r1kit import web
    from r1kit.runtime import Runtime
    rt = Runtime()
    srv = web.Server(("127.0.0.1", 0), web.make_handler(rt))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("localhost", port)
        c.request(method, path, json.dumps(body) if body is not None else None, dict({"Host": "localhost"}, **(headers or {})))
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Set-Cookie")

    assert call("GET", "/api/state")[0] == 401
    pw = open(os.path.join(sysutil.ETC, "first-run-password")).read().strip() if rt.first_run_password else None
    if pw:
        assert call("POST", "/api/login", {"password": "wrong"})[0] == 401
        st, body, ck = call("POST", "/api/login", {"password": pw})
        assert st == 200 and "HttpOnly" in ck
        csrf = json.loads(body)["csrf"]
        cookie = ck.split(";")[0]
        assert call("GET", "/api/config", headers={"Cookie": cookie})[0] == 200
        assert call("PUT", "/api/config", {}, {"Cookie": cookie})[0] == 403            # CSRF yok
        assert call("POST", "/api/skill/nettools/ping", {"host": "1.1.1.1"}, {"Cookie": cookie, "X-CSRF": csrf})[0] == 403  # sifre degistirilmeli
    st, body, _ = call("GET", "/generate_204")
    assert st in (204, 302)
    srv.shutdown()


def test_first_run_defaults_reachable_and_no_static_psk():
    from r1kit.runtime import Runtime
    rt = Runtime()
    c = rt.store.get()
    roles = {n: s["role"] for n, s in c["interfaces"].items()}
    assert "lan" in roles.values() and "ap" in roles.values()          # panele ulasilabilir
    assert all(s.get("psk") != "changeme-r1kit" for s in c["interfaces"].values())
    for name in presets.PRESETS:
        assert all(s.get("psk") != "changeme-r1kit" for s in cfg_for(name)["interfaces"].values())


def test_default_for_minimal_hardware():
    c = presets.default_for(["eth0"], [])
    assert c["interfaces"] == {"eth0": {"role": "lan", "network": "lan"}}
    assert config.validate(c) == []


def test_hotplug_detects_new_and_removed_interface(monkeypatch):
    from r1kit import netdev as netdev_mod
    from r1kit.runtime import Runtime
    rt = Runtime()
    base = netdev_mod.list_interfaces()
    monkeypatch.setattr(netdev_mod, "list_interfaces", lambda: base)
    rt.known_ifaces = {i["name"] for i in base}
    extra = base + [{"name": "eth9", "type": "ethernet", "bus": "usb", "driver": "r8152", "mac": "02:00:00:00:09:09",
                     "carrier": False, "speed": None, "rx": 0, "tx": 0, "addrs": [], "phy": None}]
    monkeypatch.setattr(netdev_mod, "list_interfaces", lambda: extra)
    rt.hotplug_tick()
    assert rt.store.get()["interfaces"]["eth9"] == {"role": "unused"}
    assert any("eth9" in e["msg"] and e["kind"] == "hotplug" for e in rt.events)

    monkeypatch.setattr(netdev_mod, "list_interfaces", lambda: base)
    rt.hotplug_tick()
    assert any("cikarildi" in e["msg"] and "eth9" in e["msg"] for e in rt.events)
    assert rt.store.get()["interfaces"]["eth9"]["role"] == "unused"      # yapilandirma korunur


def test_events_since_is_monotonic():
    from r1kit.runtime import Runtime
    rt = Runtime()
    rt.event("a")
    rt.event("b")
    ids = [e["id"] for e in rt.events]
    assert ids == sorted(ids)
    assert len(rt.events_since(ids[0])) == 1


def test_qr_endpoints_serve_svg_from_saved_config():
    import http.client
    import threading
    from r1kit import web
    from r1kit.runtime import Runtime
    rt = Runtime()
    preset = cfg_for("router")
    preset["admin"], preset["system"] = rt.store.get()["admin"], rt.store.get()["system"]
    rt.store.set(preset)
    rt.set_password("qr-test-pass-1")

    srv = web.Server(("127.0.0.1", 0), web.make_handler(rt))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, headers=None):
        c = http.client.HTTPConnection("localhost", port)
        c.request(method, path, None, dict({"Host": "localhost"}, **(headers or {})))
        r = c.getresponse()
        return r.status, r.read()

    c = http.client.HTTPConnection("localhost", port)
    c.request("POST", "/api/login", json.dumps({"password": "qr-test-pass-1"}), {"Host": "localhost"})
    ck = c.getresponse().getheader("Set-Cookie").split(";")[0]

    st, body = call("GET", "/api/qr/wifi/wlan0.svg", {"Cookie": ck})
    assert st == 200 and body.startswith(b"<svg")
    st, body = call("GET", "/api/qr/panel.svg", {"Cookie": ck})
    assert st == 200 and body.startswith(b"<svg")
    st, _ = call("GET", "/api/qr/wifi/does-not-exist.svg", {"Cookie": ck})
    assert st == 404
    srv.shutdown()


def test_sse_events_stream_sends_status_promptly():
    import socket
    import threading
    from r1kit import web
    from r1kit.runtime import Runtime
    rt = Runtime()
    preset = cfg_for("router")
    preset["admin"], preset["system"] = rt.store.get()["admin"], rt.store.get()["system"]
    rt.store.set(preset)
    rt.mdns = None
    rt.set_password("sse-test-pass-1")

    srv = web.Server(("127.0.0.1", 0), web.make_handler(rt))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    import http.client
    c = http.client.HTTPConnection("localhost", port)
    c.request("POST", "/api/login", json.dumps({"password": "sse-test-pass-1"}), {"Host": "localhost", "Content-Type": "application/json"})
    ck = c.getresponse().getheader("Set-Cookie").split(";")[0]

    s = socket.create_connection(("localhost", port), timeout=5)
    s.sendall(("GET /api/events HTTP/1.1\r\nHost: localhost\r\nCookie: %s\r\nConnection: close\r\n\r\n" % ck).encode())
    s.settimeout(5)
    chunk = s.recv(4096).decode(errors="ignore")
    assert "200" in chunk.splitlines()[0]
    assert "text/event-stream" in chunk
    assert "event: status" in chunk
    s.close()
    rt.stop.set()
    srv.shutdown()
