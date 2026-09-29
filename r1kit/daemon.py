"""Ana daemon: yapilandirmayi uygular, web sunucusunu, saglik kontrolunu ve metrikleri calistirir."""
import logging
import os
import signal
import threading
import time

from . import sysutil, web
from .mdns import MDNSResponder
from .portaldns import PortalDNS
from .runtime import Runtime

log = logging.getLogger("r1kit")


def loop(rt, interval, fn, name):
    def run():
        while not rt.stop.wait(interval):
            try:
                fn()
            except Exception:
                log.exception("%s hatasi", name)
    threading.Thread(target=run, daemon=True, name=name).start()


def main():
    logging.basicConfig(level=os.environ.get("R1KIT_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    rt = Runtime()
    if rt.first_run_password:
        log.warning("ILK KURULUM: yonetici parolasi %s (yalnizca ilk giriste gecerli, degistirmeniz istenir)", rt.first_run_password)
    if rt.first_run_wifi:
        log.warning("ILK KURULUM %s", rt.first_run_wifi)
    ports = [int(p) for p in os.environ.get("R1KIT_PORTS", "80,8080").split(",")]
    web.serve(rt, ports)
    PortalDNS(rt).start()
    rt.mdns = MDNSResponder(rt)
    rt.mdns.start()
    try:
        rt.apply()
    except Exception:
        log.exception("ilk apply basarisiz; yonetim paneli yine de acik")
    loop(rt, 1, rt.sup.tick, "supervisor")
    loop(rt, rt.store.get()["uplink"]["interval"], rt.uplinks.tick, "uplinks")
    loop(rt, 5, rt.sample, "metrics")
    loop(rt, 15, rt.apply_client_policy, "clientpolicy")
    loop(rt, 3, rt.hotplug_tick, "hotplug")
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: rt.stop.set())
    rt.stop.wait()
    log.info("kapaniyor")
    rt.sup.stop_all()
