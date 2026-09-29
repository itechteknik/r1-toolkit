"""Sistem yardimcilari: komut calistirma (dry-run destekli), atomik dosya yazimi, yollar."""
import collections
import logging
import os
import shutil
import subprocess
import tempfile
import threading

log = logging.getLogger("r1kit")

ROOT = os.environ.get("R1KIT_ROOT", "")          # test/gelistirme icin onek
DRY = os.environ.get("R1KIT_DRY") == "1"         # gercek komut calistirma


def P(path):
    return ROOT + path


ETC = P("/etc/r1kit")
RUN = P("/run/r1kit")
VAR = P("/var/lib/r1kit")
GEN = os.path.join(RUN, "gen")                   # uretilen (turetilmis) yapilandirmalar

CMDLOG = collections.deque(maxlen=500)
_lock = threading.Lock()


def ensure_dirs():
    for d in (ETC, RUN, VAR, GEN, os.path.join(RUN, "uplinks"), os.path.join(VAR, "captures")):
        os.makedirs(d, exist_ok=True)
    try:
        os.chmod(ETC, 0o700)
    except OSError:
        pass


def have(binary):
    return shutil.which(binary) is not None


def run(argv, timeout=20, input=None, ignore=False):
    """argv listesi calistirir (shell yok). (rc, cikti) doner. DRY modda kaydeder ve 0 doner."""
    argv = [str(a) for a in argv]
    if DRY:
        with _lock:
            CMDLOG.append(argv)
        return 0, ""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0 and not ignore:
            log.warning("komut basarisiz (%s): %s -> %s", p.returncode, " ".join(argv), out.strip()[:300])
        return p.returncode, out
    except FileNotFoundError:
        return 127, "%s bulunamadi" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "zaman asimi"


def write_file(path, content, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default
