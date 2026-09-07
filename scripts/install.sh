#!/usr/bin/env bash
# Run only after storage has been prepared and .env has been edited.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ "$ROOT" == '/opt/cloudy-vps' ]] || { echo 'Place the project at /opt/cloudy-vps first.' >&2; exit 1; }
[[ -f "$ROOT/.env" ]] || { echo 'Copy .env.example to .env and configure it first.' >&2; exit 1; }
docker info >/dev/null
[[ "$(docker info --format '{{.Driver}}')" == overlay2 ]] || {
  echo 'Configure overlay2/XFS project quotas first. See README.md.' >&2; exit 1;
}
apt-get update
apt-get install -y python3 python3-venv sudo iptables
id cloudy >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/cloudy-vps --shell /usr/sbin/nologin cloudy
usermod -aG docker cloudy
install -d -o cloudy -g cloudy -m 700 /var/lib/cloudy-vps
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --disable-pip-version-check -r "$ROOT/requirements.txt"
chown -R root:root "$ROOT"
# Give the daemon group read access only to the runtime secrets, not write access to code.
chown root:cloudy "$ROOT/.env"
chmod 640 "$ROOT/.env"
install -d -m 755 /usr/local/libexec
install -o root -g root -m 755 "$ROOT/scripts/firewall-check.sh" /usr/local/libexec/cloudy-firewall-check
install -o root -g root -m 755 "$ROOT/scripts/firewall.sh" /usr/local/libexec/cloudy-firewall
install -o root -g root -m 755 "$ROOT/scripts/enforce-expiry.py" /usr/local/libexec/cloudy-enforce-expiry
printf 'cloudy ALL=(root) NOPASSWD: /usr/local/libexec/cloudy-firewall-check ""\n' > /etc/sudoers.d/cloudy-firewall
chmod 440 /etc/sudoers.d/cloudy-firewall
visudo -cf /etc/sudoers.d/cloudy-firewall
install -m 644 "$ROOT/deploy/cloudy-firewall.service" /etc/systemd/system/
install -m 644 "$ROOT/deploy/cloudy-expiry.service" /etc/systemd/system/
install -m 644 "$ROOT/deploy/cloudy-expiry.timer" /etc/systemd/system/
install -m 644 "$ROOT/deploy/cloudy-vps.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cloudy-firewall.service cloudy-expiry.timer
# A one-time image build, not a per-VPS build. This requires network access.
docker build --pull -t cloudy/ubuntu:22.04-sshx "$ROOT/containers/ubuntu22"
sudo -u cloudy /usr/bin/sudo -n /usr/local/libexec/cloudy-firewall-check
# Validate configuration and real quota support without connecting to Discord.
sudo -u cloudy bash -c 'cd /opt/cloudy-vps && .venv/bin/python scripts/doctor.py'
systemctl enable --now cloudy-vps.service
echo 'Cloudy installed. Check: systemctl status cloudy-vps --no-pager'
