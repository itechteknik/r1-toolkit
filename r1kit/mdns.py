"""Minik mDNS yanitlayici: cihaza IP ezberlemeden 'http://<hostname>.local/' ile ulasilmasini saglar.

Tam bir mDNS/Bonjour yigini degildir; yalnizca kendi ana bilgisayar adimiza gelen A sorgularini
yanitlar (Avahi/Bonjour istemcileri bunu tanir). Zaten calisan portaldns.py'nin port 5353'u degil
5390'i kullanmasinin nedeni budur - 5353 gercek mDNS grup adresi icin ayrilmistir.
"""
import ipaddress
import logging
import socket
import struct
import threading

from .portaldns import build_reply

log = logging.getLogger("r1kit.mdns")
GROUP = "224.0.0.251"
PORT = 5353


class MDNSResponder(threading.Thread):
    def __init__(self, rt):
        super().__init__(daemon=True, name="mdns")
        self.rt = rt
        self.sock = None
        self.joined = set()          # uzerinde IP_ADD_MEMBERSHIP yapilmis bridge adresleri

    def _open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            s.bind(("0.0.0.0", PORT))
        except OSError as e:
            log.error("mDNS baglanamadi (%s); 'http://<ip>/' ile erisin", e)
            return None
        return s

    def rejoin(self):
        """Her apply() sonrasi cagrilir: yeni/degisen bridge'lerde multicast uyeligini tazeler."""
        if not self.sock:
            return
        want = {str(n["iface"].ip) for n in self.rt.topo().nets.values() if n["iface"]}
        for ip in want - self.joined:
            try:
                mreq = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(ip))
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.joined.add(ip)
                log.info("mDNS: %s uzerinde gruba katildi", ip)
            except OSError as e:
                log.debug("mDNS uyeligi basarisiz (%s): %s", ip, e)

    def _names(self):
        cfg = self.rt.store.get()
        h = cfg["system"]["hostname"].lower()
        return {h + ".local.", h + ".local"}

    def _answer_ip(self, src_ip):
        try:
            c = ipaddress.ip_address(src_ip)
        except ValueError:
            return None
        for n in self.rt.topo().nets.values():
            if n["iface"] and c in n["iface"].network:
                return str(n["iface"].ip)
        return next((str(n["iface"].ip) for n in self.rt.topo().nets.values() if n["iface"]), None)

    def _qname(self, data):
        try:
            i, labels = 12, []
            while data[i] != 0:
                n = data[i]
                labels.append(data[i + 1:i + 1 + n].decode("ascii", "ignore"))
                i += n + 1
            return ".".join(labels).lower() + "."
        except (IndexError, UnicodeDecodeError):
            return ""

    def run(self):
        self.sock = self._open()
        if not self.sock:
            return
        self.rejoin()
        while not self.rt.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                continue
            if self._qname(data) not in self._names():
                continue
            ip = self._answer_ip(addr[0])
            if not ip:
                continue
            reply = build_reply(data, ip, ttl=120)
            if reply:
                try:
                    self.sock.sendto(reply, addr)
                except OSError:
                    pass
