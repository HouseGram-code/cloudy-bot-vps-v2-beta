#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ "${1:-}" == '--fresh-dedicated-node' ]] || {
  echo 'This changes Docker storage on a FRESH dedicated VM only.' >&2
  echo 'Usage: sudo bash scripts/prepare-storage.sh --fresh-dedicated-node [size-GiB=100]' >&2
  exit 2
}
SIZE="${2:-100}"
[[ "$SIZE" =~ ^[0-9]+$ ]] && (( SIZE >= 20 && SIZE <= 100000 )) || exit 2
IMAGE=/var/lib/cloudy-storage.img
TARGET=/srv/cloudy-docker
[[ ! -e "$IMAGE" && ! -e /etc/systemd/system/docker.service.d/cloudy-storage.conf ]] || {
  echo 'Cloudy storage already exists. Do not rerun; inspect the existing setup.' >&2; exit 1;
}
docker info >/dev/null
[[ -z "$(docker ps -aq)" && -z "$(docker image ls -q)" ]] || {
  echo 'Refusing to switch storage: Docker already has containers or images. Use a fresh VM.' >&2; exit 1;
}
if [[ -e /etc/docker/daemon.json ]]; then
  python3 - <<'PY'
import json
with open('/etc/docker/daemon.json') as f:
    c = json.load(f)
if c.get('data-root') or c.get('storage-driver'):
    raise SystemExit('Existing custom Docker storage found. Configure manually instead.')
PY
fi
apt-get update
apt-get install -y xfsprogs util-linux
mkdir -p "$TARGET" /etc/docker /etc/systemd/system/docker.service.d
[[ -z "$(ls -A "$TARGET")" ]] || { echo 'Mount target is not empty.' >&2; exit 1; }
python3 - "$SIZE" <<'PY'
import shutil, sys
wanted = int(sys.argv[1]) * 1024**3
free = shutil.disk_usage('/var/lib').free
buffer_needed = 5 * 1024**3  # 5 GiB buffer for operations
if free < wanted + buffer_needed:
    free_gb = free / (1024**3)
    wanted_gb = wanted / (1024**3)
    buffer_gb = buffer_needed / (1024**3)
    print(f'Free: {free_gb:.1f} GiB, Needed: {wanted_gb:.1f} GiB + {buffer_gb:.1f} GiB buffer')
    raise SystemExit('Not enough real free disk. A sparse-file fallback is intentionally forbidden.')
print(f'Storage check OK: {free / (1024**3):.1f} GiB free, allocating {wanted_gb:.1f} GiB')
PY
# Fully preallocate the backing file: no sparse disks, overbooking or fake capacity.
( set -o noclobber; : > "$IMAGE" )
chmod 600 "$IMAGE"
fallocate -l "${SIZE}G" "$IMAGE"
mkfs.xfs -f "$IMAGE"
mount -t xfs -o loop,pquota "$IMAGE" "$TARGET"
xfs_quota -x -c state "$TARGET"
cp -a /etc/fstab "/etc/fstab.cloudy-backup.$(date +%s)"
printf '%s %s xfs loop,pquota 0 0\n' "$IMAGE" "$TARGET" >> /etc/fstab
if [[ -f /etc/docker/daemon.json ]]; then
  cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.cloudy-backup.$(date +%s)"
fi
systemctl stop docker.service docker.socket
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/docker/daemon.json')
config = json.loads(p.read_text()) if p.exists() else {}
config.update({'data-root': '/srv/cloudy-docker', 'storage-driver': 'overlay2'})
config.setdefault('features', {})['containerd-snapshotter'] = False
config['live-restore'] = False
p.write_text(json.dumps(config, indent=2) + '\n')
PY
cat > /etc/systemd/system/docker.service.d/cloudy-storage.conf <<'UNIT'
[Unit]
RequiresMountsFor=/srv/cloudy-docker
ConditionPathIsMountPoint=/srv/cloudy-docker
UNIT
systemctl daemon-reload
systemctl enable --now docker.service
[[ "$(docker info --format '{{.Driver}}')" == 'overlay2' ]]
echo 'Storage ready. Writable-layer quotas can now be enforced.'
