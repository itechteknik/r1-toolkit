"""Yonetici kimlik dogrulama: PBKDF2, oturumlar, CSRF, kaba kuvvet korumasi."""
import hashlib
import hmac
import secrets
import threading
import time

ITER = 100_000


def hash_password(pw, iters=ITER):
    salt = secrets.token_hex(12)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), iters).hex()
    return "pbkdf2$%d$%s$%s" % (iters, salt, dk)


def verify_password(pw, stored):
    try:
        _, iters, salt, dk = stored.split("$")
        cand = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(cand, dk)
    except Exception:
        return False


class Sessions:
    def __init__(self, ttl=1800):
        self.ttl = ttl
        self.s = {}
        self.fails = {}           # ip -> [zaman damgalari]
        self.lock = threading.Lock()

    def locked(self, ip, now=None):
        now = now or time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < 300]
            self.fails[ip] = f
            return len(f) >= 5

    def fail(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def create(self, ip, mac=None):
        tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
        with self.lock:
            self.s[tok] = {"exp": time.time() + self.ttl, "csrf": csrf, "ip": ip, "mac": mac}
            self.fails.pop(ip, None)
        return tok, csrf

    def get(self, tok):
        with self.lock:
            s = self.s.get(tok)
            if not s:
                return None
            if s["exp"] < time.time():
                del self.s[tok]
                return None
            s["exp"] = time.time() + self.ttl      # kayan sure
            return dict(s)

    def drop(self, tok):
        with self.lock:
            self.s.pop(tok, None)

    def drop_all(self):
        with self.lock:
            self.s.clear()
