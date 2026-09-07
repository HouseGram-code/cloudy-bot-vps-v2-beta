#!/usr/bin/env bash
set -euo pipefail
cd /opt/cloudy-vps
# Freestyle's documented base snapshot includes Docker and systemd.
# This installer deliberately refuses existing Docker images/containers.
command -v docker >/dev/null || { echo 'Install Docker Engine from its official Ubuntu instructions first.' >&2; exit 1; }
systemctl enable --now docker
bash scripts/prepare-storage.sh --fresh-dedicated-node 100
bash scripts/install.sh
