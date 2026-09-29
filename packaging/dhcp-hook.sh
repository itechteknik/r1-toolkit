#!/bin/sh
# udhcpc hook'u: yalnizca adresi ayarlar ve durum dosyasi yazar; rotalari r1kit uplink yoneticisi kurar.
D="${R1KIT_RUN:-/run/r1kit}/uplinks"
mkdir -p "$D"
case "$1" in
  bound|renew)
    ip addr replace "$ip/${mask:-24}" dev "$interface" ${broadcast:+broadcast "$broadcast"}
    gw="${router%% *}"
    printf '{"iface":"%s","ip":"%s","prefix":%s,"gw":"%s","dns":"%s","proto":"dhcp"}\n' \
      "$interface" "$ip" "${mask:-24}" "$gw" "$dns" > "$D/.$interface.tmp" && mv "$D/.$interface.tmp" "$D/$interface.json"
    ;;
  deconfig)
    ip addr flush dev "$interface"
    rm -f "$D/$interface.json"
    ;;
esac
exit 0
