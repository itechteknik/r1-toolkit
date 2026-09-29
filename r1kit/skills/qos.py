from ..plan import C
from .base import Skill, F, register


@register
class QoS(Skill):
    name = "qos"
    title = "Trafik Sekillendirme (CAKE / SQM)"
    desc = "Bufferbloat'u onler: WAN cikisina CAKE, girise IFB uzerinden CAKE. CAKE yoksa fq_codel'e duser."
    category = "performans"
    needs = ["tc"]
    fields = [
        F("down_mbps", "Indirme hizi (Mbit/s)", "int", 90, h="Hat hizinin ~%90'i. R1 ethernet 100Mbit'tir."),
        F("up_mbps", "Yukleme hizi (Mbit/s)", "int", 90),
        F("nat_aware", "NAT farkindaligi", "bool", True),
    ]

    def render(self, cfg, s, plan, topo):
        nat = "nat" if s["nat_aware"] else "nonat"
        for w in topo.uplinks:
            ifb = "ifb-" + w[:10]
            plan.pre += [C("tc", "qdisc", "del", "dev", w, "root", ignore=True),
                         C("tc", "qdisc", "del", "dev", w, "ingress", ignore=True),
                         C("tc", "qdisc", "replace", "dev", w, "root", "cake", "bandwidth", "%dmbit" % int(s["up_mbps"]), nat, "wash",
                           ignore=True, alt=C("tc", "qdisc", "replace", "dev", w, "root", "fq_codel", ignore=True)),
                         C("ip", "link", "add", ifb, "type", "ifb", ignore=True),
                         C("ip", "link", "set", ifb, "up", ignore=True),
                         C("tc", "qdisc", "add", "dev", w, "handle", "ffff:", "ingress", ignore=True),
                         C("tc", "filter", "replace", "dev", w, "parent", "ffff:", "protocol", "all", "u32", "match", "u32", "0", "0",
                           "action", "mirred", "egress", "redirect", "dev", ifb, ignore=True),
                         C("tc", "qdisc", "replace", "dev", ifb, "root", "cake", "bandwidth", "%dmbit" % int(s["down_mbps"]), "ingress", nat,
                           ignore=True, alt=C("tc", "qdisc", "replace", "dev", ifb, "root", "fq_codel", ignore=True))]
