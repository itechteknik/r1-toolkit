#!/bin/sh
# Orange Pi R1 (Armbian/Debian) icin kurulum. root olarak calistirin: sh install.sh
set -e
[ "$(id -u)" = 0 ] || { echo "root gerekli"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "==> Paketler"
apt-get update -qq
apt-get install -y --no-install-recommends python3 hostapd dnsmasq-base udhcpc wpasupplicant iw iproute2 nftables \
    wireguard-tools tcpdump traceroute iputils-ping curl ca-certificates kmod || true
apt-get install -y --no-install-recommends iperf3 >/dev/null 2>&1 || true

echo "==> Dosyalar"
mkdir -p /opt/r1kit /usr/local/lib/r1kit /etc/r1kit
rm -rf /opt/r1kit/r1kit && cp -r "$SRC/r1kit" /opt/r1kit/
install -m 755 "$SRC/packaging/dhcp-hook.sh" /usr/local/lib/r1kit/dhcp-hook.sh
install -m 644 "$SRC/packaging/r1kit.service" /etc/systemd/system/r1kit.service
printf '#!/bin/sh\nPYTHONPATH=/opt/r1kit exec python3 -m r1kit "$@"\n' > /usr/local/bin/r1kit && chmod +x /usr/local/bin/r1kit

echo "==> Cakisan servisleri devre disi birak (r1kit hostapd/dnsmasq/wpa_supplicant'i kendisi yonetir)"
for s in hostapd dnsmasq wpa_supplicant systemd-resolved; do
    systemctl disable --now "$s" >/dev/null 2>&1 || true
done
systemctl mask hostapd >/dev/null 2>&1 || true
if [ -d /etc/NetworkManager ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '[keyfile]\nunmanaged-devices=*\n' > /etc/NetworkManager/conf.d/99-r1kit.conf
    systemctl restart NetworkManager >/dev/null 2>&1 || true
fi
# systemd-resolved kapaliysa /etc/resolv.conf'u router'in kendi DNS'ine bagla
if [ -L /etc/resolv.conf ]; then rm -f /etc/resolv.conf; fi
[ -s /etc/resolv.conf ] || printf 'nameserver 127.0.0.1\nnameserver 1.1.1.1\n' > /etc/resolv.conf

echo "==> Kernel modulleri"
for m in sch_cake ifb wireguard bridge nf_tables; do modprobe "$m" 2>/dev/null || echo "   uyari: $m yuklenemedi (ilgili ozellik calismayabilir)"; done
printf 'sch_cake\nifb\nwireguard\nbridge\n' > /etc/modules-load.d/r1kit.conf

systemctl daemon-reload
systemctl enable --now r1kit

sleep 3
echo
echo "==> Kurulum tamam."
echo "    Panel:   http://<router-ip>/  (varsayilan LAN: http://192.168.50.1/)"
[ -f /etc/r1kit/first-run-wifi ] && echo "    Kurulum Wi-Fi'si: $(cat /etc/r1kit/first-run-wifi)"
[ -f /etc/r1kit/first-run-password ] && echo "    Ilk parola: $(cat /etc/r1kit/first-run-password)   (ilk giriste degistirin)"
echo "    Sonraki adim: 'r1kit ifaces' ve 'r1kit doctor', ardindan panelden Sistem > Senaryolar."
echo "    UYARI: Ilk uygulamada SSH/ag baglantiniz degisebilir; Panel 'Guvenli Uygula' 90sn icinde onaylanmazsa otomatik geri alir."
