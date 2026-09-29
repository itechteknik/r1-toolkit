"""Render ciktisi: dosyalar, komutlar, servisler ve nftables kurallari (yan etkisiz, test edilebilir)."""
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Cmd:
    argv: list
    ignore: bool = False           # hata onemsiz
    alt: Optional["Cmd"] = None    # basarisizsa denenecek alternatif


def C(*argv, ignore=False, alt=None):
    return Cmd([str(a) for a in argv], ignore, alt)


@dataclass
class Proc:
    argv: list
    watch: list = field(default_factory=list)   # icerigi degisince yeniden baslat
    signature: str = ""

    def sig(self):
        h = hashlib.sha1(" ".join(map(str, self.argv)).encode())
        for w in self.watch:
            h.update(w.encode())
        return h.hexdigest()


NFT_SECTIONS = ("sets", "input", "forward", "prerouting", "postrouting", "chains")


@dataclass
class Plan:
    files: dict = field(default_factory=dict)      # yol -> (icerik, mod)
    pre: list = field(default_factory=list)        # servislerden once calisacak komutlar
    post: list = field(default_factory=list)       # servislerden sonra
    procs: dict = field(default_factory=dict)      # ad -> Proc (eklenme sirasi = baslama sirasi)
    nft: dict = field(default_factory=lambda: {k: [] for k in NFT_SECTIONS})
    dnsmasq: list = field(default_factory=list)
    sysctl: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def file(self, path, content, mode=0o644):
        self.files[path] = (content, mode)
        return path

    def rule(self, section, text, prio=50):
        self.nft[section].append((prio, len(self.nft[section]), text))

    def proc(self, name, argv, watch=()):
        contents = [self.files[w][0] for w in watch if w in self.files]
        p = Proc([str(a) for a in argv], contents)
        self.procs[name] = p

    def nft_text(self):
        def body(sec):
            return "\n".join("    " + r[2] for r in sorted(self.nft[sec], key=lambda r: (r[0], r[1])))
        chains = self.nft["chains"]
        out = ["table inet r1kit", "delete table inet r1kit", "table inet r1kit {", body("sets")]
        out += ["  " + c[2] for c in chains]
        out += [
            "  chain input {\n    type filter hook input priority filter; policy drop;\n" + body("input") + "\n  }",
            "  chain forward {\n    type filter hook forward priority filter; policy drop;\n" + body("forward") + "\n  }",
            "  chain prerouting {\n    type nat hook prerouting priority dstnat; policy accept;\n" + body("prerouting") + "\n  }",
            "  chain postrouting {\n    type nat hook postrouting priority srcnat; policy accept;\n" + body("postrouting") + "\n  }",
            "}", ""]
        return "\n".join(out)
