"""Skill (yetenek) altyapisi. Her skill yapilandirmadan plana kural/servis ekler ve isteyene araclar sunar."""
REGISTRY = {}


def F(k, label, t="text", default=None, opts=None, h=""):
    d = {"k": k, "label": label, "t": t, "h": h}
    if default is not None:
        d["default"] = default
    if opts:
        d["opts"] = opts
    return d


class Skill:
    name = ""
    title = ""
    desc = ""
    category = "network"
    fields = []
    needs = []            # gereken ikili dosyalar (doctor icin)
    default_enabled = False

    def defaults(self):
        return {f["k"]: f["default"] for f in self.fields if "default" in f}

    def scfg(self, cfg):
        c = self.defaults()
        c.update(cfg.get("skills", {}).get(self.name, {}))
        return c

    def enabled(self, cfg):
        return bool(cfg.get("skills", {}).get(self.name, {}).get("enabled", self.default_enabled))

    def prepare(self, store):
        """Render oncesi kalici hazirlik (orn. anahtar uretimi). Store uzerinden yapilandirmayi gunceller."""

    def render(self, cfg, s, plan, topo):
        """Etkin skill icin plana katkida bulun."""

    def status(self, cfg, s, topo):
        return {}

    def actions(self):
        """{'ad': fonksiyon(ctx, params) -> dict}. ctx = Runtime (daemon)."""
        return {}


def register(cls):
    REGISTRY[cls.name] = cls()
    return cls
