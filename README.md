# R1 Toolkit — Orange Pi R1 için gelişmiş router / AP / gateway

Orange Pi R1'i (Allwinner H2+, 2× Ethernet, Wi-Fi) tek bir web panelinden yönetilen çok amaçlı ağ cihazına çevirir.
Bağımlılık yok: yalnızca Python 3 stdlib + `hostapd`, `dnsmasq`, `nftables`, `iproute2`, `udhcpc`, `wpa_supplicant`.

> **Yönetim captive portal üzerinden:** cihazın Wi-Fi'sine bağlanın → portal açılır → "Yönetici girişi" → panel.
> (Doğrudan `http://192.168.50.1/` de çalışır.)

## Donanım notları (dürüst kısa liste)
- **Ethernet:** iki port vardır; biri SoC'un dahili 10/100 MAC'i (`end0`/`eth0`), diğeri USB üzerinden RTL8152 (`eth1`). İkisi de **100 Mbit**; USB olan CPU/USB veriyolunu paylaşır.
- **Wi-Fi:** stok R1'de tek çip (RTL8189ETV/ES, 2.4 GHz, 802.11n) bulunur. "İki Wi-Fi" görüyorsanız ikinci bir USB dongle ya da sanal arayüz (AP+STA) vardır; toolkit **N tane Wi-Fi arayüzünü** ve `iw phy` üzerinden eşzamanlı AP+STA desteğini otomatik algılar (`r1kit ifaces`). RTL8189 sürücüsü AP modu/nl80211 konusunda kısıtlı olabilir; `r1kit doctor` bunu söyler. Sorun yaşarsanız nl80211 destekli (ör. MT7601U, RT5370, RTL8812AU/nl80211) bir USB dongle kullanın.
- **Bellek:** 256 MB (bazı revizyonlarda 512 MB). Servis `MemoryMax=96M` ile sınırlıdır; reklam engelleme listesi bu yüzden sınırlıdır.
- Balance modunda akışlar dağıtılır; **tek bir bağlantı iki hattın hızını toplamaz**.

## Kurulum
```sh
git clone <bu-repo> && cd r1-toolkit
sudo sh install.sh          # Armbian/Debian
sudo r1kit ifaces && sudo r1kit doctor
```
İlk açılışta donanıma göre otomatik bir başlangıç ağı kurulur (2 ethernet + Wi-Fi varsa: onboard=WAN, USB ethernet + `R1-Setup`/`R1-Net` Wi-Fi = LAN). **Rastgele** üretilen Wi-Fi parolası `/etc/r1kit/first-run-wifi`, yönetici parolası `/etc/r1kit/first-run-password` dosyasında ve `journalctl -u r1kit`'tedir; ilk girişte parolayı değiştirmeniz istenir (değişene kadar yalnızca parola ekranı açıktır).
Panel: `http://192.168.50.1/` (LAN ağı) — Sistem › **Senaryolar** ile hazır bir kurulum yükleyin, sonra **Kaydet ve Güvenli Uygula**.

**Güvenli uygulama:** değişiklik uygulandıktan sonra 90 sn içinde "Onayla"ya basmazsanız (ör. bağlantınız koptuysa) önceki yapılandırma otomatik geri yüklenir.

## Senaryolar
| Senaryo | Ne yapar |
|---|---|
| `router` | Onboard ethernet=WAN, USB ethernet + Wi-Fi AP = LAN |
| `dual_wan` | İki ethernet WAN (failover veya yük dengeleme), Wi-Fi AP |
| `wifi_repeater` | Wi-Fi[1] mevcut Wi-Fi'a bağlanır (uplink), Wi-Fi[0] kendi AP'niz, ethernet LAN — seyahat router'ı/repeater |
| `wifi_to_ethernet` | Wi-Fi'sız cihazlara (TV, konsol, yazıcı) Wi-Fi'dan internet |
| `guest_portal` | Ana ağ + izole misafir Wi-Fi'si, captive portal (tıkla/kupon/ortak parola) |
| `inline_tap` | İki ethernet adressiz L2 köprü; arada geçen trafiği `Yakalama` ile izleyin |
| `vpn_gateway` | Tüm LAN trafiği WireGuard üzerinden, kill-switch'li |

Arayüz rolleri (`Arayüzler` sekmesi): `wan` · `lan` · `ap` · `sta` (Wi-Fi uplink) · `monitor` · `unused`. Her fiziksel/sanal arayüze istediğiniz rolü verip birleştirebilirsiniz (ör. Wi-Fi'ı WAN, iki ethernet'i LAN yapmak veya bir ethernet + bir Wi-Fi'ı WAN yapıp failover kurmak).

## Skills (eklentiler)
| Skill | İş |
|---|---|
| `captive_portal` | Portal modları: tıkla-kabul, kupon, ortak parola, sadece-yönetici; yönetici girişi; RFC 8910 (`/capport`) |
| `adblock` | dnsmasq ile DNS seviyesinde reklam/izleyici engelleme (boyut sınırlı) |
| `qos` | CAKE/SQM (bufferbloat), CAKE yoksa fq_codel |
| `wireguard` | İstemci (kill-switch) veya sunucu modu, anahtar üretimi otomatik |
| `nettools` | ping, traceroute, DNS, Wake-on-LAN, LAN port tarama (yalnız kendi ağlarınız), uplink hız testi |
| `capture` | Sınırlı süre/boyutlu tcpdump, pcap indirme |
| `wifi_survey` | Çevre AP taraması, en boş kanalı önerme ve AP `auto` kanalına uygulama |

Çekirdek özellikler: çoklu WAN sağlık kontrolü + failover/multipath, istemci başına hız limiti ve sayaç, engelleme/at, statik DHCP, port yönlendirme, ağ izolasyonu (misafir→LAN kapalı), sanal Wi-Fi arayüzü, MAC klonlama, yedekle/geri yükle.

Yeni skill eklemek için `.claude/skills/r1-toolkit/SKILL.md` ve `r1kit/skills/qos.py` örneğine bakın.

## Güvenlik
- Yönetici parolası PBKDF2-SHA256; oturum çerezi `HttpOnly; SameSite=Strict`, değiştiren isteklerde CSRF başlığı, başarısız girişte 5 dk kilit.
- WAN'dan varsayılan olarak hiçbir servis açık değildir (panel için `wan_access` açıkça açılmalıdır). Panel HTTP'dir; yönetimi güvenilir LAN'da yapın veya VPN kullanın.
- Misafir/portal ağlarında SSH kapalıdır; port tarama yalnızca yapılandırılmış yerel alt ağlara izinlidir.
- Kullanıcı girdisi hiçbir zaman shell'e verilmez (argv listesi), SSID/parola satır enjeksiyonuna karşı doğrulanır.
- Yalnızca kendi ağınızda ve izniniz olan cihazlarda kullanın.

## Komut satırı
```
r1kit ifaces | doctor | presets | preset <ad> | passwd | validate
r1kit render nft|dnsmasq|files|cmds     # uygulamadan üretilecekleri göster
```

## Geliştirme
Cihaz olmadan çalışır (`R1KIT_DRY=1 R1KIT_FAKE_HW=1`, bkz. skill dosyası). `python3 -m pytest -q tests` — render, `nft -c` sözdizimi, doğrulama, auth, HTTP akışı, portal DNS ve Wi-Fi tarama ayrıştırıcısını test eder.

> Bu kod, gerçek R1 donanımında test edilmeden önce yazılmıştır: ilk kurulumda `r1kit doctor` çıktısına ve panelin "Güvenli Uygula" akışına güvenin.
