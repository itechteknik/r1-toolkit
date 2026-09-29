"""Alt surec yoneticisi: hostapd, wpa_supplicant, udhcpc, dnsmasq. Degisen yapilandirmada yeniden baslatir."""
import logging
import os
import signal
import subprocess
import threading
import time

from . import sysutil

log = logging.getLogger("r1kit.sup")


class Supervisor:
    def __init__(self):
        self.procs = {}      # ad -> {"argv","sig","p","fails","next"}
        self.lock = threading.Lock()

    def _stop(self, name):
        e = self.procs.pop(name, None)
        if not e:
            return
        p = e["p"]
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(4)
            except subprocess.TimeoutExpired:
                p.kill()

    def stop_stale(self, desired):
        """Yapilandirmasi degisen veya artik gerekmeyen surecleri durdur."""
        with self.lock:
            for name in list(self.procs):
                d = desired.get(name)
                if d is None or d.sig() != self.procs[name]["sig"]:
                    log.info("durduruluyor: %s", name)
                    self._stop(name)

    def _start(self, name, proc):
        argv = proc.argv
        if not sysutil.DRY and not sysutil.have(argv[0]):
            log.error("%s bulunamadi, %s baslatilamadi", argv[0], name)
            self.procs[name] = {"argv": argv, "sig": proc.sig(), "p": None, "fails": 99, "next": 1e18, "missing": True}
            return
        p = None
        if not sysutil.DRY:
            p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            sysutil.CMDLOG.append(["<proc>"] + argv)
        self.procs[name] = {"argv": argv, "sig": proc.sig(), "p": p, "fails": 0, "next": 0}
        log.info("basladi: %s", name)

    def start_all(self, desired):
        with self.lock:
            for name, proc in desired.items():
                if name not in self.procs:
                    self._start(name, proc)

    def signal(self, name, sig):
        e = self.procs.get(name)
        if e and e["p"] and e["p"].poll() is None:
            e["p"].send_signal(sig)

    def tick(self):
        """Olen surecleri ustel geri cekilme ile yeniden baslat."""
        now = time.time()
        with self.lock:
            for name, e in list(self.procs.items()):
                p = e["p"]
                if p is not None and p.poll() is not None and now >= e["next"]:
                    e["fails"] += 1
                    e["next"] = now + min(60, 2 ** e["fails"])
                    try:
                        e["p"] = subprocess.Popen(e["argv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                        log.warning("yeniden baslatildi: %s (deneme %d)", name, e["fails"])
                    except OSError as ex:
                        log.error("%s: %s", name, ex)

    def status(self):
        with self.lock:
            return {n: ("missing" if e.get("missing") else "running" if e["p"] is None or e["p"].poll() is None else "dead")
                    for n, e in self.procs.items()}

    def stop_all(self):
        with self.lock:
            for n in list(self.procs):
                self._stop(n)
