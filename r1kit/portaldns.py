"""Minik yakalayici DNS: yetkisiz portal istemcilerinin tum A sorgularini router IP'sine cevaplar (UDP 5353)."""
import ipaddress
import logging
import socket
import struct
import threading

log = logging.getLogger("r1kit.dns")


def build_reply(data, ip):
    if len(data) < 12:
        return None
    tid = data[:2]
    i = 12
    while i < len(data) and data[i] != 0:
        i += data[i] + 1
    q = data[12:i + 5]
    if len(q) < 5:
        return None
    qtype = struct.unpack("!H", q[-4:-2])[0]
    hdr = tid + b"\x81\x80" + b"\x00\x01" + (b"\x00\x01" if qtype == 1 else b"\x00\x00") + b"\x00\x00\x00\x00"
    ans = b""
    if qtype == 1:
        ans = b"\xc0\x0c\x00\x01\x00\x01" + struct.pack("!I", 10) + b"\x00\x04" + socket.inet_aton(ip)
    return hdr + q + ans


class PortalDNS(threading.Thread):
    def __init__(self, rt, port=5353):
        super().__init__(daemon=True)
        self.rt, self.port = rt, port

    def ip_for(self, client_ip):
        nets = self.rt.topo().nets.values()
        try:
            c = ipaddress.ip_address(client_ip)
        except ValueError:
            return None
        for n in nets:
            if n["iface"] and c in n["iface"].network:
                return str(n["iface"].ip)
        for n in nets:
            if n["iface"]:
                return str(n["iface"].ip)

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", self.port))
        except OSError as e:
            log.error("portal DNS baglanamadi: %s", e)
            return
        while not self.rt.stop.is_set():
            try:
                data, addr = s.recvfrom(512)
                ip = self.ip_for(addr[0])
                r = build_reply(data, ip) if ip else None
                if r:
                    s.sendto(r, addr)
            except OSError:
                continue
