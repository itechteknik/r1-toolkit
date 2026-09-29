---
name: r1-toolkit
description: Orange Pi R1 toolkit'ini (r1kit) gelistirirken veya cihaz uzerinde sorun giderirken kullan. Yeni skill ekleme, render/nftables degisiklikleri, hostapd/dnsmasq/multi-WAN hata ayiklama, test calistirma icin rehber.
---

# r1kit ile calisma rehberi

## Mimari (kisa)
`config.json` -> `render.render(cfg)` -> **Plan** (dosyalar + komutlar + surecler + nft kurallari) -> `Runtime.apply()` uygular.
Render **saf**dir (yan etkisiz); tum degisiklikler `tests/` ile cihaz olmadan test edilir.
- `render.py` cekirdek: koprüler, hostapd, wpa_supplicant, dnsmasq, nftables temeli.
- `skills/*.py` eklentiler: `Skill` alt sinifi + `@register`; `render()` plana kural/surec ekler, `actions()` API'den cagrilan araclar sunar.
- `uplink.py` cok-WAN (saglik, failover, multipath). `runtime.py` apply/guvenli-geri-alma/portal yetkisi/istemciler.
- `web.py` portal + yonetici API (CSRF + oturum), `static/` panel (innerHTML KULLANMA, `h()` yardimcisi).

## Yeni skill ekleme
1. `r1kit/skills/<ad>.py`: `@register class X(Skill)`; `fields=[F(...)]` UI formunu otomatik uretir.
2. `skills/__init__.py` icine import et.
3. nft kurali: `plan.rule("forward"|"input"|"prerouting"|"postrouting", "kural", prio)`; dnsmasq: `plan.dnsmasq.append`; komut: `plan.pre.append(C(...))`; surec: `plan.proc(ad, argv, watch=[dosya])`.
4. Kullanici girdisi shell'e ASLA gitmez (argv listesi); ag/dosya adlarini `config.IFACE_RE/HOST_RE/MAC_RE` ile dogrula.
5. `tests/test_toolkit.py` icine render + nft sozdizimi testi ekle.

## Gelistirme ortami (cihaz yok)
```
export R1KIT_ROOT=/tmp/r1t R1KIT_DRY=1 R1KIT_FAKE_HW=1 R1KIT_PORTS=18080
python3 -m r1kit daemon      # komutlar calistirilmaz, /api/cmdlog'da listelenir
python3 -m pytest -q tests
```
Panel testinde cookie'nin Host'a bagli oldugunu unutma: `localhost` kullan.

## Sorun giderme (cihazda)
- `r1kit doctor` -> eksik ikili/kernel modulu/AP destegi.
- `r1kit render nft|dnsmasq|files|cmds` -> uygulanacaklari uygulamadan gor.
- `journalctl -u r1kit -f`; `nft list ruleset`; `ip rule; ip route show table 100`; `bridge link`.
- Wi-Fi AP acilmiyor: surucu AP modu/`nl80211` destegi (RTL8189ES/ETV genelde sinirlidir; USB dongle onerilir). `iw phy` -> "valid interface combinations".
- Portal tetiklenmiyor: `nft list set inet r1kit authed`, portal aginda `portal` isaretli mi, skill etkin mi.
