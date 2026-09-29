"""HTTP sunucusu: captive portal (misafir) + yonetici paneli/API. Yalnizca stdlib."""
import json
import logging
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import auth, config, netdev, presets, qrcode, sysutil
from .skills import REGISTRY

log = logging.getLogger("r1kit.web")
STATIC = os.path.join(os.path.dirname(__file__), "static")
CAPTIVE_PATHS = {"/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html", "/ncsi.txt",
                 "/connecttest.txt", "/redirect", "/success.txt", "/canonical.html", "/kindle-wifi/wifistub.html",
                 "/check_network_status.txt"}
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")
ADMIN_EDITABLE = ("session_ttl", "wan_access", "ssh_from_lan")


class ApiError(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def make_handler(rt):
    class H(BaseHTTPRequestHandler):
        server_version = "r1kit"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            log.debug("%s " + fmt, self.client_address[0], *a)

        # ---- yardimcilar -----------------------------------------------------
        def _send(self, code, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _read_raw(self):
            """Govdeyi HER ZAMAN tuket (erken hata donse bile keep-alive akisi bozulmasin)."""
            n = int(self.headers.get("Content-Length") or 0)
            if n > 262144:
                self.close_connection = True
                raise ApiError(413, "istek cok buyuk")
            self._raw = self.rfile.read(n) if n else b""

        def _body(self):
            if not self._raw:
                return {}
            try:
                d = json.loads(self._raw)
            except ValueError:
                raise ApiError(400, "gecersiz JSON")
            if not isinstance(d, dict):
                raise ApiError(400, "JSON nesnesi bekleniyor")
            return d

        def _cookie(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "r1sid":
                    return v
            return None

        def _session(self):
            t = self._cookie()
            return rt.sessions.get(t) if t else None

        def _need_admin(self):
            s = self._session()
            if not s:
                raise ApiError(401, "oturum gerekli")
            if self.command not in ("GET", "HEAD") and self.headers.get("X-CSRF") != s["csrf"]:
                raise ApiError(403, "CSRF dogrulamasi basarisiz")
            return s

        def _own_names(self):
            cfg = rt.store.get()
            names = {cfg["system"]["hostname"], "%s.%s" % (cfg["system"]["hostname"], cfg["system"]["domain"]), "localhost"}
            for n in rt.topo().nets.values():
                if n["iface"]:
                    names.add(str(n["iface"].ip))
            return names

        def _client_net(self):
            import ipaddress
            ip = self.client_address[0]
            for n in rt.topo().nets.values():
                if n["iface"] and ipaddress.ip_address(ip) in n["iface"].network:
                    return n
            return None

        def _is_unauth(self):
            n = self._client_net()
            if not n or not n.get("portal"):
                return False
            mac = rt.mac_for_ip(self.client_address[0])
            return mac not in rt.authed

        def _portal_url(self):
            n = self._client_net() or next((x for x in rt.topo().nets.values() if x["iface"]), None)
            return "http://%s/" % (n["iface"].ip if n else "192.168.50.1")

        # ---- yonlendirme -----------------------------------------------------
        def do_HEAD(self):
            self.route()

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD

        def route(self):
            try:
                self._read_raw()
                u = urlparse(self.path)
                host = (self.headers.get("Host") or "").split(":")[0]
                if host not in self._own_names() and not u.path.startswith("/api/"):
                    if self._is_unauth():
                        return self._send(302, b"", "text/plain", {"Location": self._portal_url()})
                if u.path in CAPTIVE_PATHS:
                    if self._is_unauth():
                        return self._send(302, b"", "text/plain", {"Location": self._portal_url()})
                    return self._send(204 if u.path in ("/generate_204", "/gen_204") else 200, b"Success", "text/plain")
                if u.path == "/favicon.ico":
                    return self._send(204, b"", "image/x-icon")
                if u.path == "/capport":
                    return self._send(200, {"captive": self._is_unauth(), "user-portal-url": self._portal_url()}, "application/captive+json")
                if u.path == "/api/events" and self.command == "GET":
                    return self.sse_events()
                if u.path.startswith("/api/"):
                    return self._send(*self.api(u))
                return self.static(u.path)
            except ApiError as e:
                self._send(e.code, {"error": e.msg})
            except ValueError as e:
                self._send(400, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("istek hatasi")
                self._send(500, {"error": "sunucu hatasi"})

        def static(self, path):
            if path in ("/", ""):
                name = "portal.html" if self._is_unauth() else "app.html"
            elif path == "/admin":
                name = "app.html"
            elif path == "/portal":
                name = "portal.html"
            else:
                name = path.lstrip("/")
            if not SAFE_FILE.match(name) or not os.path.isfile(os.path.join(STATIC, name)):
                return self._send(404, b"not found", "text/plain")
            with open(os.path.join(STATIC, name), "rb") as f:
                self._send(200, f.read(), MIME.get(os.path.splitext(name)[1], "application/octet-stream"))

        def sse_events(self):
            """Canli durum akisi (Server-Sent Events): panel 5sn'lik yoklama yerine bunu dinler.
            Her ~2sn 'status', ve olustukca 'event' (hotplug/uplink/apply) mesaji gonderir."""
            if not self._session():
                raise ApiError(401, "oturum gerekli")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            hdr = self.headers.get("Last-Event-ID")
            q = (parse_qs(urlparse(self.path).query).get("last_id") or ["0"])[0]
            last_id = int(hdr or q or 0)
            try:
                for _ in range(150):                          # ~5 dk sonra istemci yeniden baglanir
                    if rt.stop.is_set():
                        break
                    for e in rt.events_since(last_id):
                        last_id = e["id"]
                        self.wfile.write(("id: %d\nevent: hotplug\ndata: %s\n\n" % (e["id"], json.dumps(e))).encode())
                    self.wfile.write(("event: status\ndata: %s\n\n" % json.dumps(rt.status())).encode())
                    self.wfile.flush()
                    self.close_connection = rt.stop.wait(2)
                    if self.close_connection:
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        # ---- API -------------------------------------------------------------
        def api(self, u):
            p = u.path[5:].strip("/")
            m = self.command
            ip = self.client_address[0]
            # -- herkese acik portal uclari
            if p == "portal/info" and m == "GET":
                s = REGISTRY["captive_portal"].scfg(rt.store.get())
                mac = rt.mac_for_ip(ip)
                return 200, {"title": s["title"], "terms": s["terms"], "mode": s["mode"], "authed": mac in rt.authed,
                             "minutes": s["session_minutes"], "must_change": False}
            if p.startswith("portal/") and m == "POST":
                return 200, self.portal_action(p[7:], self._body(), ip)
            if p == "login" and m == "POST":
                return self.login(self._body(), ip)
            if p == "logout" and m == "POST":
                rt.sessions.drop(self._cookie() or "")
                return 200, {"ok": True}, "application/json", {"Set-Cookie": "r1sid=; Path=/; Max-Age=0"}
            # -- yonetici uclari
            if p == "session" and m == "GET":
                s = self._session()
                return 200, {"logged_in": bool(s), "csrf": s["csrf"] if s else None,
                             "must_change": rt.store.get()["admin"]["must_change"] if s else False}
            sess = self._need_admin()
            if p not in ("password", "state", "config", "schema") and rt.store.get()["admin"]["must_change"]:
                raise ApiError(403, "once yonetici parolasini degistirmelisiniz")
            body = self._body() if m in ("POST", "PUT") else {}
            return self.admin_api(p, m, body, parse_qs(u.query), sess)

        def login(self, b, ip):
            if rt.sessions.locked(ip):
                raise ApiError(429, "cok fazla basarisiz deneme, 5 dk bekleyin")
            cfg = rt.store.get()
            if not auth.verify_password(str(b.get("password", "")), cfg["admin"]["password_hash"]):
                rt.sessions.fail(ip)
                time.sleep(0.5)
                raise ApiError(401, "parola hatali")
            mac = rt.mac_for_ip(ip)
            tok, csrf = rt.sessions.create(ip, mac)
            if mac and self._client_net() and self._client_net().get("portal"):
                rt.authorize(mac, REGISTRY["captive_portal"].scfg(cfg)["admin_session_minutes"], ip, "admin")
            ck = "r1sid=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (tok, cfg["admin"]["session_ttl"])
            return 200, {"ok": True, "csrf": csrf, "must_change": cfg["admin"]["must_change"]}, "application/json", {"Set-Cookie": ck}

        def portal_action(self, act, b, ip):
            cfg = rt.store.get()
            s = REGISTRY["captive_portal"].scfg(cfg)
            mac = rt.mac_for_ip(ip)
            if not mac:
                raise ApiError(400, "cihaz MAC adresi belirlenemedi")
            if mac in cfg["blocked_macs"]:
                raise ApiError(403, "erisim engellendi")
            if act == "accept" and s["mode"] == "click":
                if not b.get("agree"):
                    raise ApiError(400, "sartlari kabul etmelisiniz")
                minutes = s["session_minutes"]
            elif act == "voucher" and s["mode"] == "voucher":
                minutes = rt.redeem(str(b.get("code", "")), mac, ip)
                if not minutes:
                    rt.sessions.fail(ip)
                    raise ApiError(403, "kupon gecersiz veya kullanim hakki dolmus")
            elif act == "password" and s["mode"] == "password":
                if rt.sessions.locked(ip) or not s["shared_password"] or str(b.get("password", "")) != s["shared_password"]:
                    rt.sessions.fail(ip)
                    raise ApiError(403, "parola hatali")
                minutes = s["session_minutes"]
            else:
                raise ApiError(400, "bu giris yontemi etkin degil")
            rt.authorize(mac, minutes, ip, "guest")
            return {"ok": True, "minutes": minutes}

        def admin_api(self, p, m, b, q, sess):
            cfg = rt.store.get()
            if p == "state" and m == "GET":
                return 200, {"status": rt.status(), "hw": netdev.list_interfaces(), "config": config.public_view(cfg)}
            if p == "config" and m == "GET":
                return 200, config.public_view(cfg)
            if p == "config" and m == "PUT":
                new = self.merge_config(cfg, b)
                rt.store.set(new)
                return 200, {"ok": True}
            if p == "schema" and m == "GET":
                return 200, {"skills": [{"name": s.name, "title": s.title, "desc": s.desc, "category": s.category,
                                          "fields": s.fields, "enabled": s.enabled(cfg), "actions": list(s.actions()),
                                          "status": s.status(cfg, s.scfg(cfg), rt.topo()) if s.enabled(cfg) else {},
                                          "defaults": s.defaults()} for s in REGISTRY.values()],
                             "presets": [{"name": k, "title": v["title"], "desc": v["desc"]} for k, v in presets.PRESETS.items()],
                             "roles": list(config.ROLES)}
            if p == "hw" and m == "GET":
                return 200, netdev.list_interfaces()
            if p == "preset" and m == "POST":
                hw = netdev.suggest_roles(netdev.list_interfaces())
                wired = b.get("wired") or hw["wired"]
                wifi = b.get("wifi") or hw["wifi"]
                if b.get("name") not in presets.PRESETS:
                    raise ApiError(400, "bilinmeyen senaryo")
                new = presets.build(b["name"], wired, wifi)
                for k in ("admin", "system"):
                    new[k] = cfg[k]
                new["skills"] = config.deep_merge(cfg["skills"], new["skills"])
                rt.store.set(new)
                return 200, {"ok": True, "config": config.public_view(new)}
            if p == "apply" and m == "POST":
                return 200, rt.apply(safe=bool(b.get("safe", True)), confirm_s=min(int(b.get("confirm_s", 90)), 600))
            if p == "confirm" and m == "POST":
                return 200, {"ok": rt.cancel_pending()}
            if p == "clients" and m == "GET":
                return 200, {"clients": rt.clients()}
            if p == "clients/action" and m == "POST":
                return 200, self.client_action(b)
            if p == "uplinks" and m == "GET":
                return 200, rt.uplinks.status()
            if p == "metrics" and m == "GET":
                return 200, {"samples": list(rt.samples)}
            if p == "vouchers" and m == "GET":
                return 200, rt.vouchers()
            if p == "vouchers" and m == "POST":
                return 200, {"codes": rt.create_vouchers(b.get("count", 1), b.get("minutes", 60), b.get("max_devices", 1))}
            if p == "vouchers" and m == "DELETE":
                code = (q.get("code") or [""])[0]
                if not code:
                    raise ApiError(400, "code parametresi gerekli")
                v = rt.vouchers()
                v.pop(code, None)
                rt.save_vouchers(v)
                return 200, {"ok": True}
            if p == "password" and m == "POST":
                if not auth.verify_password(str(b.get("old", "")), cfg["admin"]["password_hash"]):
                    raise ApiError(403, "mevcut parola hatali")
                rt.set_password(str(b.get("new", "")))
                return 200, {"ok": True}
            if p == "doctor" and m == "GET":
                return 200, {"checks": rt.doctor()}
            mm = re.fullmatch(r"qr/wifi/([A-Za-z0-9_.:-]{1,15})\.svg", p)
            if mm and m == "GET":
                s = cfg["interfaces"].get(mm.group(1))
                if not s or s.get("role") != "ap":
                    raise ApiError(404, "boyle bir Wi-Fi AP arayuzu yok")
                payload = qrcode.wifi_payload(s["ssid"], s.get("psk", ""), s.get("security", "wpa2"), s.get("hidden", False))
                return 200, qrcode.to_svg(payload), "image/svg+xml"
            if p == "qr/panel.svg" and m == "GET":
                net = next((n for n in rt.topo().nets.values() if n["iface"]), None)
                url = "http://%s.local/" % cfg["system"]["hostname"] if rt.mdns else "http://%s/" % (net["iface"].ip if net else cfg["system"]["hostname"])
                return 200, qrcode.to_svg(url), "image/svg+xml"
            if p == "logs" and m == "GET":
                rc, out = sysutil.run(["journalctl", "-u", "r1kit", "-n", "200", "--no-pager", "-o", "short"], ignore=True)
                return 200, {"log": out}
            if p == "cmdlog" and m == "GET":
                return 200, {"cmds": [" ".join(c) for c in list(sysutil.CMDLOG)[-100:]]}
            if p == "backup" and m == "GET":
                return 200, cfg, "application/json", {"Content-Disposition": 'attachment; filename="r1kit-config.json"'}
            if p == "restore" and m == "POST":
                rt.store.set(config.deep_merge(config.DEFAULT, b))
                return 200, {"ok": True}
            if p == "system/reboot" and m == "POST":
                threading.Timer(1.0, lambda: sysutil.run(["systemctl", "reboot"])).start()
                return 200, {"ok": True}
            mm = re.fullmatch(r"skill/([a-z_]+)/([a-z_]+)", p)
            if mm and m == "POST":
                sk = REGISTRY.get(mm.group(1))
                act = sk.actions().get(mm.group(2)) if sk else None
                if not act:
                    raise ApiError(404, "bilinmeyen eylem")
                if not sk.enabled(cfg):
                    raise ApiError(409, "skill etkin degil")
                return 200, act(rt, b)
            mm = re.fullmatch(r"capture/([A-Za-z0-9._-]+\.pcap)", p)
            if mm and m == "GET":
                path = os.path.join(sysutil.VAR, "captures", mm.group(1))
                if not os.path.isfile(path):
                    raise ApiError(404, "yok")
                with open(path, "rb") as f:
                    return 200, f.read(), "application/vnd.tcpdump.pcap", {"Content-Disposition": 'attachment; filename="%s"' % mm.group(1)}
            raise ApiError(404, "bilinmeyen uc nokta")

        def merge_config(self, old, new):
            if not isinstance(new, dict):
                raise ApiError(400, "gecersiz yapilandirma")
            out = config.deep_merge(config.DEFAULT, new)
            out["admin"] = dict(old["admin"], **{k: new.get("admin", {}).get(k, old["admin"][k]) for k in ADMIN_EDITABLE})
            for name, s in out.get("skills", {}).items():
                for k, v in list(s.items()):
                    if v == "***":
                        s[k] = old["skills"].get(name, {}).get(k, "")
            return out

        def client_action(self, b):
            mac = str(b.get("mac", "")).lower()
            if not config.MAC_RE.match(mac):
                raise ApiError(400, "gecersiz MAC")
            act = b.get("action")
            cfg = rt.store.get()
            if act == "kick":
                rt.kick(mac)
            elif act == "authorize":
                rt.authorize(mac, int(b.get("minutes", 60)), "", "admin")
            elif act == "revoke":
                rt.revoke(mac)
            elif act in ("block", "unblock"):
                def fn(c):
                    s = set(c["blocked_macs"])
                    (s.add if act == "block" else s.discard)(mac)
                    c["blocked_macs"] = sorted(s)
                rt.store.update(fn)
                verb = "add" if act == "block" else "delete"
                sysutil.run(["nft", verb, "element", "inet", "r1kit", "blocked", "{", mac, "}"], ignore=True)
                if act == "block":
                    rt.kick(mac)
            elif act == "limit":
                def fn(c):
                    d, u = b.get("down_mbps"), b.get("up_mbps")
                    if d or u:
                        c["client_limits"][mac] = {"down_mbps": float(d or 0), "up_mbps": float(u or 0)}
                    else:
                        c["client_limits"].pop(mac, None)
                rt.store.update(fn)
                rt.apply_client_policy()
            else:
                raise ApiError(400, "bilinmeyen eylem")
            return {"ok": True}

    return H


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


def serve(rt, ports):
    handler = make_handler(rt)
    servers = []
    for port in ports:
        try:
            srv = Server(("0.0.0.0", port), handler)
        except OSError as e:
            log.error("port %s dinlenemiyor: %s", port, e)
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        log.info("HTTP dinleniyor: %d", port)
    return servers
