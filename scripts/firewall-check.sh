#!/usr/bin/env bash
# Installed root-owned at a fixed path. The bot may run this exact check via sudo.
set -euo pipefail
[[ $# -eq 0 && $EUID -eq 0 ]] || exit 1
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
iptables -w 2 -C DOCKER-USER -i cdy+ -j CLOUDY-EGRESS
iptables -w 2 -C DOCKER-USER -o cdy+ -j DROP
iptables -w 2 -C DOCKER-USER -o cdy+ -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -w 2 -C INPUT -i cdy+ -j DROP
for subnet in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
  iptables -w 2 -C CLOUDY-EGRESS -d "$subnet" -j DROP
done
iptables -w 2 -C CLOUDY-EGRESS -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -w 2 -C CLOUDY-EGRESS -j DROP
ip6tables -w 2 -C INPUT -i cdy+ -j DROP
ip6tables -w 2 -C FORWARD -i cdy+ -j DROP
