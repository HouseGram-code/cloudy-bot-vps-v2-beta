"""All Docker calls stay here. No user-supplied shell commands reach the host."""
import logging
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import docker
import psutil
from docker.errors import DockerException, NotFound
from docker.types import LogConfig, Ulimit

from .capacity import Capacity
from .config import GIB
from .errors import CloudyError
from .models import VPS, cpu_percent, memory_usage, pressure, utc_now
from .security import extract_sshx_url

LOG = logging.getLogger("cloudy.engine")
MANAGED = "io.cloudy.managed"
PREFIX = "io.cloudy."

class Engine:
    def __init__(self, config, store):
        self.cfg, self.store = config, store
        self._client = None
        self.client_lock = threading.Lock()
        self.ready = False
        self.admission = threading.RLock()
        self.locks = {}
        self.lock_guard = threading.Lock()
        self.stats_cache = {}
        self.health_cache = None
        self.health_lock = threading.Lock()
        self.probe_until = 0.0

    @property
    def client(self):
        # Keep Discord online to report a red status even if Docker starts offline.
        with self.client_lock:
            if self._client is None:
                self._client = docker.DockerClient(base_url=self.cfg.docker_socket, version="auto", timeout=35)
            return self._client

    def lock(self, vps_id):
        with self.lock_guard:
            return self.locks.setdefault(vps_id, threading.RLock())

    def _firewall_ok(self):
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-n", self.cfg.firewall_check],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=8, check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _info(self):
        try:
            return self.client.info()
        except DockerException as exc:
            raise CloudyError("The Docker node is unavailable. Please contact an administrator.", "docker_offline") from exc

    def _container(self, row, missing_ok=False):
        try:
            container = self.client.containers.get(row.container_id or row.container_name)
        except NotFound:
            if missing_ok:
                return None
            raise CloudyError("The container is missing. Use Reinstall to create a clean one.", "container_missing") from None
        labels = container.labels or {}
        expected = {
            MANAGED: "true", PREFIX + "id": row.id,
            PREFIX + "owner": str(row.owner_id), PREFIX + "guild": str(row.guild_id),
        }
        if any(labels.get(k) != v for k, v in expected.items()):
            raise CloudyError("Container ownership verification failed. Contact an administrator.", "label_mismatch")
        return container

    def _capacity(self, info):
        rows = self.store.all()
        known = {row.id for row in rows}
        containers = self.client.containers.list(all=True)
        for container in containers:
            labels = container.labels or {}
            if labels.get(MANAGED) != "true" or labels.get(PREFIX + "id") not in known:
                raise CloudyError("Untracked containers exist on this dedicated node. Administrator review is required.", "untracked_container")
        disk = shutil.disk_usage(info["DockerRootDir"])
        memory = psutil.virtual_memory()
        return Capacity(
            total_cpu=int(info["NCPU"]), total_memory=int(info["MemTotal"]),
            total_disk=disk.total, free_memory=memory.available, free_disk=disk.free,
            reserved_cpu=sum(row.cpus for row in rows),
            reserved_memory=sum(row.ram_bytes for row in rows),
            reserved_disk=sum(row.disk_bytes for row in rows), instances=len(rows),
        )

    def preflight(self):
        info = self._info()
        if info.get("Driver") != "overlay2":
            raise CloudyError("Disk quotas are not configured. This node requires overlay2 on XFS with project quotas.", "quota_backend")
        for key in ("MemoryLimit", "CpuCfsQuota", "PidsLimit"):
            if not info.get(key):
                raise CloudyError("Required cgroup resource limits are unavailable on this node.", "missing_cgroups")
        if self.cfg.runtime not in info.get("Runtimes", {}):
            raise CloudyError("The configured container runtime is unavailable.", "runtime_missing")
        if not self._firewall_ok():
            raise CloudyError("The isolation firewall is not ready. New containers are blocked.", "firewall_not_ready")
        try:
            image = self.client.images.get(self.cfg.image)
        except NotFound as exc:
            raise CloudyError("Ubuntu 22.04 is not installed on this node yet.", "image_missing") from exc
        if (image.attrs.get("Config", {}).get("Labels") or {}).get(PREFIX + "os") != "ubuntu:22.04":
            raise CloudyError("The configured image is not the approved Ubuntu 22.04 image.", "wrong_image")
        # A real Docker create probes quota support. Never silently remove storage_opt.
        if time.monotonic() > self.probe_until:
            probe = None
            try:
                probe = self.client.containers.create(
                    self.cfg.image, ["/bin/true"], name="cloudy-quota-check-" + uuid.uuid4().hex[:10],
                    network_mode="none", storage_opt={"size": "1G"},
                    mem_limit=128 * 1024**2, pids_limit=16,
                    cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
                    labels={MANAGED: "quota-probe"},
                )
            except DockerException as exc:
                raise CloudyError("The node cannot enforce a writable disk quota. Deployment was refused.", "quota_probe_failed") from exc
            finally:
                if probe:
                    probe.remove(force=True)
            self.probe_until = time.monotonic() + 300
        return info

    def _create_container(self, row):
        return self.client.containers.create(
            image=self.cfg.image,
            name=row.container_name, hostname="cloudy-" + row.id[:8],
            command=["/bin/sleep", "infinity"], detach=True, init=True, user="0:0",
            network=row.network_name, runtime=self.cfg.runtime,
            mem_limit=row.ram_bytes, memswap_limit=row.ram_bytes,
            cpu_period=100000, cpu_quota=row.cpus * 100000,
            pids_limit=512,
            storage_opt={"size": str(row.disk_bytes // GIB) + "G"},
            cap_drop=["ALL"],
            cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "SETGID", "SETUID", "SETFCAP", "NET_BIND_SERVICE", "SYS_CHROOT"],
            security_opt=["no-new-privileges:true"], privileged=False,
            read_only=False,
            tmpfs={"/run": "rw,nosuid,nodev,noexec,size=16m,mode=0755"},
            shm_size=16 * 1024**2,
            sysctls={"net.ipv6.conf.all.disable_ipv6": "1", "net.ipv6.conf.default.disable_ipv6": "1"},
            dns=["1.1.1.1", "9.9.9.9"],
            restart_policy={"Name": "no"},
            ulimits=[Ulimit(name="nofile", soft=2048, hard=4096)],
            log_config=LogConfig(type="local", config={"max-size": "5m", "max-file": "2"}),
            environment={"TERM": "xterm-256color", "LANG": "C.UTF-8"},
            labels={
                MANAGED: "true", PREFIX + "id": row.id,
                PREFIX + "owner": str(row.owner_id), PREFIX + "guild": str(row.guild_id),
                PREFIX + "expires": str(row.expires_at), PREFIX + "os": "ubuntu:22.04",
                PREFIX + "disk_bytes": str(row.disk_bytes),
            },
        )

    @staticmethod
    def _verify_limits(container, row):
        container.reload()
        host = container.attrs["HostConfig"]
        storage = host.get("StorageOpt") or {}
        if (host.get("Memory") != row.ram_bytes
            or host.get("MemorySwap") != row.ram_bytes
            or host.get("CpuQuota") != row.cpus * 100000
            or host.get("CpuPeriod") != 100000
            or host.get("PidsLimit") != 512
            or str(storage.get("size", "")).lower() != f"{row.disk_bytes // GIB}g"
            or host.get("Privileged")
            or host.get("Binds") or host.get("Devices")):
            raise CloudyError("The container limits did not match the plan. Startup was blocked.", "limit_mismatch")

    def deploy(self, guild_id, owner_id, progress=lambda message: None):
        with self.admission:
            if self.store.for_owner(guild_id, owner_id):
                raise CloudyError("You already have a VPS. Use $manage.", "already_exists")
            if not self.ready:
                raise CloudyError("The node is still reconciling its saved state. Please retry later.", "node_not_ready")
            progress("Checking Docker, firewall and disk quotas")
            info = self.preflight()
            if self._capacity(info).slots(self.cfg) < 1:
                raise CloudyError("No capacity for this plan. No VPS was created; try again when an administrator adds resources.", "capacity_exhausted")
            key, now = uuid.uuid4().hex, utc_now()
            row = VPS(
                key, guild_id, owner_id, None,
                f"cloudy-{owner_id}-{key[:8]}", "cloudy-net-" + key[:12],
                now, now + self.cfg.lease_days * 86400, "PROVISIONING",
                self.cfg.ram_gib * GIB, self.cfg.cpus, self.cfg.disk_gib * GIB,
            )
            self.store.add(row)
            container, network = None, None
            try:
                progress("Reserving resources and creating an isolated network")
                network = self.client.networks.create(
                    row.network_name, driver="bridge", enable_ipv6=False,
                    options={"com.docker.network.bridge.name": "cdy" + key[:10], "com.docker.network.bridge.enable_icc": "false"},
                    labels={MANAGED: "true", PREFIX + "id": key},
                )
                progress("Creating the Ubuntu 22.04 container")
                container = self._create_container(row)
                self.store.update(key, container_id=container.id)
                self._verify_limits(container, row)
                progress("Starting Ubuntu and verifying the running state")
                container.start()
                container.reload()
                if container.status != "running":
                    raise CloudyError("The container did not reach the running state.", "startup_failed")
                self.store.update(key, state="RUNNING")
                self.store.audit(owner_id, key, "deploy")
                self.health_cache = None
                return self.store.get(key)
            except Exception as exc:
                clean = True
                try:
                    if container is None:
                        container = self._container(row, missing_ok=True)
                    if network is None:
                        try:
                            candidate = self.client.networks.get(row.network_name)
                            if (candidate.attrs.get("Labels") or {}).get(PREFIX + "id") == row.id:
                                network = candidate
                            else:
                                clean = False
                        except NotFound:
                            pass
                except Exception:
                    clean = False
                if container:
                    try:
                        container.remove(force=True)
                    except DockerException:
                        clean = False
                if network and clean:
                    try:
                        network.remove()
                    except DockerException:
                        clean = False
                if clean:
                    self.store.delete(key)
                else:
                    self.store.update(key, state="ERROR")
                self.store.audit(owner_id, key, "deploy", getattr(exc, "code", "docker_error"))
                if isinstance(exc, CloudyError):
                    raise
                LOG.error("Deployment failed: %s", type(exc).__name__)
                raise CloudyError("Deployment failed. No success was recorded; an administrator can check the node.", "deploy_failed") from exc

    def _active(self, row):
        if row.expires_at <= utc_now():
            raise CloudyError("This beta lease has expired. The VPS cannot be started or reinstalled.", "expired")

    def operate(self, vps_id, guild_id, owner_id, action):
        if action not in {"start", "stop", "reinstall"}:
            raise ValueError("Unsupported action")
        with self.admission, self.lock(vps_id):
            row = self.store.owned(vps_id, guild_id, owner_id)
            try:
                if action != "stop":
                    if not self.ready:
                        raise CloudyError("The node is still reconciling its saved state.", "node_not_ready")
                    self._active(row)
                container = self._container(row, missing_ok=action == "reinstall")
                if action == "start":
                    self.preflight()
                    self._verify_limits(container, row)
                    if container.status != "running":
                        if psutil.virtual_memory().available < row.ram_bytes + self.cfg.reserve_ram_gib * GIB:
                            raise CloudyError("Not enough currently available RAM to start safely.", "memory_pressure")
                        container.start()
                elif action == "stop":
                    if container.status in {"running", "restarting", "paused"}:
                        if container.status == "paused":
                            container.unpause()
                        container.stop(timeout=10)
                else:
                    self.preflight()
                    reclaimed = 0
                    if container and container.status == "running":
                        reclaimed = memory_usage(container.stats(stream=False)) or 0
                    if psutil.virtual_memory().available + reclaimed < row.ram_bytes + self.cfg.reserve_ram_gib * GIB:
                        raise CloudyError("Not enough available RAM to reinstall safely.", "memory_pressure")
                    self.store.update(row.id, state="REINSTALLING")
                    if container:
                        container.remove(force=True)
                    self.store.update(row.id, container_id=None)
                    # Same network, identity and expiration; all writable data is deleted.
                    try:
                        network = self.client.networks.get(row.network_name)
                        if (network.attrs.get("Labels") or {}).get(PREFIX + "id") != row.id:
                            raise CloudyError("Network ownership verification failed.", "network_mismatch")
                    except NotFound:
                        self.client.networks.create(
                            row.network_name, driver="bridge", enable_ipv6=False,
                            options={"com.docker.network.bridge.name": "cdy" + row.id[:10], "com.docker.network.bridge.enable_icc": "false"},
                            labels={MANAGED: "true", PREFIX + "id": row.id},
                        )
                    container = self._create_container(row)
                    self.store.update(row.id, container_id=container.id)
                    self._verify_limits(container, row)
                    container.start()
                container.reload()
                if action in {"start", "reinstall"} and container.status != "running":
                    raise CloudyError("The container is not running. Check the panel and retry.", "startup_failed")
                state = "EXPIRED" if row.expires_at <= utc_now() else container.status.upper()
                self.store.update(row.id, state=state)
                self.store.audit(owner_id, row.id, action)
                self.stats_cache.pop(row.id, None)
                self.health_cache = None
            except CloudyError:
                raise
            except DockerException as exc:
                self.store.update(row.id, state="ERROR")
                self.store.audit(owner_id, row.id, action, "docker_error")
                raise CloudyError("The operation failed. Refresh the panel before retrying.", "docker_error") from exc

    def snapshot(self, vps_id, guild_id, owner_id):
        with self.lock(vps_id):
            row = self.store.owned(vps_id, guild_id, owner_id)
            now = time.monotonic()
            cached = self.stats_cache.get(vps_id)
            if cached and now - cached[0] < 10:
                return cached[1]
            container = self._container(row, missing_ok=True)
            result = {
                "vps": asdict(row), "sampled_at": utc_now(), "status": "MISSING",
                "cpu_percent": None, "memory_bytes": None, "disk_bytes": None, "uptime_seconds": None,
            }
            if container:
                container.reload()
                result["status"] = container.status.upper()
                if container.status == "running":
                    try:
                        stats = container.stats(stream=False)
                        result["cpu_percent"] = cpu_percent(stats, row.cpus)
                        result["memory_bytes"] = memory_usage(stats)
                    except DockerException:
                        pass
                    started = container.attrs.get("State", {}).get("StartedAt", "")
                    try:
                        # Docker emits nanosecond timestamps; Python accepts microseconds.
                        started = re.sub(r"(\.\d{6})\d+", r"\1", started).replace("Z", "+00:00")
                        result["uptime_seconds"] = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
                    except ValueError:
                        pass
                    try:
                        disk = container.exec_run(["/usr/bin/timeout", "3", "/bin/df", "-B1", "--output=used", "/"], user="0")
                        line = disk.output.decode("utf-8", "replace").splitlines()[-1].strip() if disk.output else ""
                        if disk.exit_code == 0 and line.isdigit():
                            result["disk_bytes"] = int(line)
                    except (DockerException, IndexError):
                        pass
            self.stats_cache[vps_id] = (now, result)
            return result

    def revoke_sshx(self, vps_id, guild_id, owner_id):
        with self.lock(vps_id):
            row = self.store.owned(vps_id, guild_id, owner_id)
            container = self._container(row)
            if container.status == "running":
                # This fixed command only runs inside the verified owner's container.
                container.exec_run(["/bin/sh", "-c", "pkill -TERM -x sshx >/dev/null 2>&1 || true"], user="0")

    def sshx(self, vps_id, guild_id, owner_id):
        with self.lock(vps_id):
            row = self.store.owned(vps_id, guild_id, owner_id)
            self._active(row)
            container = self._container(row)
            if container.status != "running":
                raise CloudyError("Start your VPS before opening sshx.", "not_running")
            if not self._firewall_ok():
                raise CloudyError("The node isolation firewall needs administrator attention.", "firewall_not_ready")
            self.revoke_sshx(vps_id, guild_id, owner_id)
            # The path and duration are generated/validated by us, never from chat.
            path = "/run/cloudy-sshx-" + uuid.uuid4().hex + ".log"
            ttl = max(1, min(self.cfg.sshx_ttl, row.expires_at - utc_now()))
            command = f"umask 077; /usr/bin/timeout --signal=TERM --kill-after=5s {ttl}s /usr/local/bin/sshx >{path} 2>&1 < /dev/null &"
            container.exec_run(["/bin/sh", "-c", command], user="0", workdir="/root")
            try:
                for _ in range(25):
                    # Bound the transcript read and never log terminal output or links.
                    output = container.exec_run(["/usr/bin/head", "-c", "16384", path], user="0")
                    url = extract_sshx_url(output.output.decode("utf-8", "replace"))
                    if url:
                        self.store.audit(owner_id, row.id, "sshx")
                        return url
                    time.sleep(1)
                self.revoke_sshx(vps_id, guild_id, owner_id)
                raise CloudyError("sshx did not create a session in time. Check outbound HTTPS and try again.", "sshx_timeout")
            finally:
                try:
                    container.exec_run(["/bin/rm", "-f", path], user="0")
                except DockerException:
                    pass

    def health(self):
        with self.health_lock:
            if self.health_cache and time.monotonic() - self.health_cache[0] < 10:
                return self.health_cache[1]
            result = {"sampled_at": utc_now(), "node": self.cfg.node_name, "docker": False, "firewall": False, "quota": False, "slots": 0, "instances": len(self.store.all()), "cpu": None, "memory": None, "disk": None, "state": "INCIDENT", "color": "red"}
            try:
                # Serialize with admission so temporary quota probes aren't untracked VPSs.
                with self.admission:
                    info = self._info()
                    result["docker"] = True
                    result["firewall"] = self._firewall_ok()
                    result["quota"] = info.get("Driver") == "overlay2" and time.monotonic() < self.probe_until
                    try:
                        self.preflight()
                        result["quota"] = True
                        result["slots"] = self._capacity(info).slots(self.cfg) if self.ready else 0
                    except CloudyError:
                        result["slots"] = 0
                    result["cpu"] = psutil.cpu_percent(interval=0.2)
                    result["memory"] = psutil.virtual_memory().percent
                    result["disk"] = 100 * (1 - shutil.disk_usage(info["DockerRootDir"]).free / shutil.disk_usage(info["DockerRootDir"]).total)
                    result["state"], result["color"] = pressure(result["cpu"], result["memory"], result["disk"], result["docker"] and result["firewall"] and result["quota"], result["slots"])
            except (CloudyError, DockerException, OSError, KeyError):
                pass
            self.health_cache = (time.monotonic(), result)
            return result

    def reconcile(self):
        """Run at startup and every minute. Never auto-start tenants."""
        with self.admission:
            self.ready = False
            self.client.ping()
            reconciled = True
            for row in self.store.all():
                with self.lock(row.id):
                    try:
                        container = self._container(row, missing_ok=True)
                        if row.expires_at <= utc_now():
                            if container and container.status in {"running", "restarting", "paused"}:
                                if container.status == "paused":
                                    container.unpause()
                                container.stop(timeout=10)
                            self.store.update(row.id, state="EXPIRED")
                            if utc_now() >= row.expires_at + self.cfg.retention_hours * 3600:
                                if container:
                                    container.remove(force=True)
                                try:
                                    network = self.client.networks.get(row.network_name)
                                    if network.attrs.get("Labels", {}).get(PREFIX + "id") != row.id:
                                        raise CloudyError("Network ownership mismatch", "network_mismatch")
                                    network.remove()
                                except NotFound:
                                    pass
                                self.store.audit(row.owner_id, row.id, "purge_expired")
                                self.store.delete(row.id)
                                self.stats_cache.pop(row.id, None)
                        elif container:
                            self.store.update(row.id, container_id=container.id, state=container.status.upper())
                        elif row.state == "PROVISIONING" and utc_now() - row.created_at > 300:
                            try:
                                network = self.client.networks.get(row.network_name)
                                if network.attrs.get("Labels", {}).get(PREFIX + "id") == row.id:
                                    network.remove()
                            except NotFound:
                                pass
                            self.store.delete(row.id)
                        elif row.state != "PROVISIONING":
                            self.store.update(row.id, state="MISSING")
                    except Exception as exc:
                        # Avoid raw Docker/HTTP errors; they can include private payloads.
                        reconciled = False
                        LOG.error("Reconciliation failed for %s: %s", row.id, type(exc).__name__)
            self.ready = reconciled
            self.health_cache = None
