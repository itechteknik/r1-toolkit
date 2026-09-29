"""r1kit komut satiri."""
import argparse
import getpass
import json
import sys

from . import netdev, presets, sysutil
from .config import Store, validate
from .render import render


def main(argv=None):
    ap = argparse.ArgumentParser(prog="r1kit", description="Orange Pi R1 toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="servisi calistir")
    sub.add_parser("ifaces", help="algilanan arayuzler")
    sub.add_parser("doctor", help="bagimlilik/kernel/surucu kontrolu")
    sub.add_parser("passwd", help="yonetici parolasini degistir")
    sub.add_parser("presets", help="senaryolari listele")
    p = sub.add_parser("preset", help="senaryo yukle (sonra 'apply' veya servisi yeniden baslat)")
    p.add_argument("name")
    r = sub.add_parser("render", help="uretilecek yapilandirmayi yazdir (uygulamaz)")
    r.add_argument("what", nargs="?", default="nft", choices=["nft", "dnsmasq", "files", "cmds"])
    sub.add_parser("validate", help="config.json dogrula")
    a = ap.parse_args(argv)

    if a.cmd == "daemon":
        from .daemon import main as dm
        dm()
    elif a.cmd == "ifaces":
        for i in netdev.list_interfaces():
            extra = ""
            if i["type"] == "wifi":
                w = i.get("wifi", {})
                extra = " modlar=%s AP+STA=%s" % (",".join(w.get("modes", [])), w.get("ap_sta_concurrent"))
            print("%-8s %-8s bus=%-8s drv=%-10s carrier=%s speed=%s%s" % (i["name"], i["type"], i["bus"], i["driver"], i["carrier"], i["speed"], extra))
    elif a.cmd == "doctor":
        from .runtime import Runtime
        for c in Runtime().doctor():
            print("[%s] %s %s" % ("OK " if c["ok"] else "!!!", c["name"], "" if c["ok"] else "-> " + c["hint"]))
    elif a.cmd == "passwd":
        from .runtime import Runtime
        pw = getpass.getpass("Yeni parola: ")
        if pw != getpass.getpass("Tekrar: "):
            print("parolalar eslesmiyor")
            return 1
        Runtime().set_password(pw)
        print("tamam")
    elif a.cmd == "presets":
        for k, v in presets.PRESETS.items():
            print("%-18s %s\n%18s %s" % (k, v["title"], "", v["desc"]))
    elif a.cmd == "preset":
        hw = netdev.suggest_roles(netdev.list_interfaces())
        st = Store()
        old = st.load()
        new = presets.build(a.name, hw["wired"], hw["wifi"])
        new["admin"], new["system"] = old["admin"], old["system"]
        st.set(new)
        print("senaryo yuklendi: %s (SSID/parola icin panelden duzenleyin)" % a.name)
    elif a.cmd == "validate":
        errs = validate(Store().load())
        print("\n".join(errs) or "gecerli")
        return 1 if errs else 0
    elif a.cmd == "render":
        cfg = Store().load()
        plan, _ = render(cfg)
        if a.what == "nft":
            print(plan.nft_text())
        elif a.what == "dnsmasq":
            print(next(v[0] for k, v in plan.files.items() if k.endswith("dnsmasq.conf")))
        elif a.what == "files":
            for k, (c, _) in plan.files.items():
                print("### %s\n%s" % (k, c))
        else:
            for c in plan.pre:
                print(" ".join(c.argv))
            for n, pr in plan.procs.items():
                print("[proc %s] %s" % (n, " ".join(pr.argv)))
    return 0
