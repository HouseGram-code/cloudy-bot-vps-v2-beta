import os
from dataclasses import dataclass
from pathlib import Path

GIB = 1024 ** 3

@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    deploy_channel_id: int
    required_role_id: int = 0
    open_beta: bool = False
    node_name: str = "Local Node"
    image: str = "cloudy/ubuntu:22.04-sshx"
    data_dir: Path = Path("/var/lib/cloudy-vps")
    docker_socket: str = "unix:///var/run/docker.sock"
    ram_gib: int = 15
    cpus: int = 3
    disk_gib: int = 75
    reserve_ram_gib: int = 2
    reserve_cpus: int = 1
    reserve_disk_gib: int = 10
    max_vps: int = 1
    # Small disk mode: can be overridden via env vars
    small_disk_mode: bool = False
    lease_days: int = 30
    retention_hours: int = 72
    sshx_ttl: int = 900
    firewall_check: str = "/usr/local/libexec/cloudy-firewall-check"
    runtime: str = "runc"

    @property
    def database(self):
        return self.data_dir / "state.sqlite3"

    @classmethod
    def from_env(cls):
        def integer(name, default, minimum=1, maximum=100000):
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        def boolean(name, default="false"):
            value = os.getenv(name, default).lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token.lower().startswith(("replace", "your_", "paste_")):
            raise ValueError("Set a NEW Discord token in the private .env file")
        role = integer("REQUIRED_ROLE_ID", 0, 0, 2**64 - 1)
        opened = boolean("OPEN_BETA")
        if not opened and not role:
            raise ValueError("Set REQUIRED_ROLE_ID, or explicitly set OPEN_BETA=true")
        socket = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        if not socket.startswith("unix:///"):
            raise ValueError("Only a local Unix Docker socket is supported")
        
        # Small disk mode for testing/development on limited resources
        small_disk = boolean("SMALL_DISK_MODE", "false")
        return cls(
            token=token,
            guild_id=integer("GUILD_ID", 0, 1, 2**64 - 1),
            deploy_channel_id=integer("DEPLOY_CHANNEL_ID", 0, 1, 2**64 - 1),
            required_role_id=role, open_beta=opened,
            node_name=os.getenv("NODE_NAME", "Local Node")[:64],
            image=os.getenv("VPS_IMAGE", "cloudy/ubuntu:22.04-sshx"),
            data_dir=Path(os.getenv("DATA_DIR", "/var/lib/cloudy-vps")).resolve(),
            docker_socket=socket,
            # Reduce resources for small disk mode (testing/dev)
            ram_gib=integer("VPS_RAM_GIB", 6 if small_disk else 15),
            cpus=integer("VPS_CPUS", 1 if small_disk else 3, 1, 1024),
            disk_gib=integer("VPS_DISK_GIB", 10 if small_disk else 75),
            reserve_ram_gib=integer("HOST_RESERVE_RAM_GIB", 1 if small_disk else 2),
            reserve_cpus=integer("HOST_RESERVE_CPUS", 1, 1, 1024),
            reserve_disk_gib=integer("HOST_RESERVE_DISK_GIB", 3 if small_disk else 10),
            max_vps=integer("MAX_VPS_TOTAL", 1, 1, 1000),
            lease_days=integer("LEASE_DAYS", 30, 1, 365),
            retention_hours=integer("EXPIRED_RETENTION_HOURS", 72, 1, 720),
            sshx_ttl=integer("SSHX_TTL_SECONDS", 900, 60, 3600),
            runtime=os.getenv("DOCKER_RUNTIME", "runc"),
            small_disk_mode=small_disk,
        )
