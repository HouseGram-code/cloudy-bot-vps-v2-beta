#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
command -v iptables-restore >/dev/null
iptables -w 5 -nL DOCKER-USER >/dev/null 2>&1 || {
  echo 'Docker must use its iptables backend; DOCKER-USER is missing.' >&2; exit 1;
}
# The custom chain is replaced atomically, never temporarily emptied live.
iptables-restore --wait 5 --noflush <<'RULES'
*filter
:CLOUDY-EGRESS - [0:0]
-F CLOUDY-EGRESS
-A CLOUDY-EGRESS -d 0.0.0.0/8 -j DROP
-A CLOUDY-EGRESS -d 10.0.0.0/8 -j DROP
-A CLOUDY-EGRESS -d 100.64.0.0/10 -j DROP
-A CLOUDY-EGRESS -d 127.0.0.0/8 -j DROP
-A CLOUDY-EGRESS -d 169.254.0.0/16 -j DROP
-A CLOUDY-EGRESS -d 172.16.0.0/12 -j DROP
-A CLOUDY-EGRESS -d 192.168.0.0/16 -j DROP
-A CLOUDY-EGRESS -d 198.18.0.0/15 -j DROP
-A CLOUDY-EGRESS -d 224.0.0.0/4 -j DROP
-A CLOUDY-EGRESS -d 240.0.0.0/4 -j DROP
-A CLOUDY-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A CLOUDY-EGRESS -p udp -d 1.1.1.1 --dport 53 -j ACCEPT
-A CLOUDY-EGRESS -p udp -d 9.9.9.9 --dport 53 -j ACCEPT
-A CLOUDY-EGRESS -p tcp -d 1.1.1.1 --dport 53 -j ACCEPT
-A CLOUDY-EGRESS -p tcp -d 9.9.9.9 --dport 53 -j ACCEPT
-A CLOUDY-EGRESS -p tcp -m multiport --dports 80,443 -j ACCEPT
-A CLOUDY-EGRESS -j DROP
COMMIT
RULES
ensure() { iptables -w 5 -C "$@" 2>/dev/null || iptables -w 5 -I "$1" 1 "${@:2}"; }
# Install in reverse order: outbound policy comes before inbound acceptance.
ensure DOCKER-USER -o cdy+ -j DROP
ensure DOCKER-USER -o cdy+ -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ensure DOCKER-USER -i cdy+ -j CLOUDY-EGRESS
ensure INPUT -i cdy+ -j DROP
# Networks and guests disable IPv6. Also block all bridge IPv6 at the host.
for chain in INPUT FORWARD; do
  ip6tables -w 5 -C "$chain" -i cdy+ -j DROP 2>/dev/null || ip6tables -w 5 -I "$chain" 1 -i cdy+ -j DROP
done
echo 'Cloudy isolation rules installed. Only public HTTP(S) and approved DNS are allowed.'
