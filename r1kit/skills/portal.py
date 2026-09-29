from .base import Skill, F, register


@register
class CaptivePortal(Skill):
    name = "captive_portal"
    title = "Captive Portal"
    desc = ("Portal etkin aglarda (Aglar > 'portal') yetkisiz istemcileri splash sayfasina yonlendirir. "
            "Yonetici, portal sayfasindaki 'Yonetici girisi' ile yonetim paneline girer.")
    category = "erisim"
    default_enabled = True
    fields = [
        F("mode", "Misafir giris modu", "select", "click", ["click", "voucher", "password", "admin_only"],
          "click: sartlari kabul; voucher: kupon kodu; password: ortak parola; admin_only: sadece yonetici"),
        F("title", "Baslik", "text", "Orange Pi R1 Ag Gecidi"),
        F("terms", "Kullanim sartlari metni", "text", "Bu agi yasal amaclarla kullanacagimi kabul ediyorum."),
        F("shared_password", "Ortak parola (password modu)", "password", ""),
        F("session_minutes", "Misafir oturum suresi (dk)", "int", 240),
        F("admin_session_minutes", "Yonetici internet suresi (dk)", "int", 720),
    ]

    def status(self, cfg, s, topo):
        return {"portal_networks": [n["name"] for n in topo.portal_nets()], "mode": s["mode"]}
