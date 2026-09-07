import math
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class VPS:
    id: str
    guild_id: int
    owner_id: int
    container_id: str | None
    container_name: str
    network_name: str
    created_at: int
    expires_at: int
    state: str
    ram_bytes: int
    cpus: int
    disk_bytes: int

def utc_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def days_left(expires_at: int, now: int | None = None) -> int:
    return max(0, math.ceil((expires_at - (utc_now() if now is None else now)) / 86400))

def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    clock = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{days}d {clock}" if days else clock

def cpu_percent(stats: dict, allocated_cpus: int) -> float | None:
    """Percent of this container's CPU quota, not of one host core."""
    current, previous = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    current_usage, old_usage = current.get("cpu_usage", {}), previous.get("cpu_usage", {})
    total = current_usage.get("total_usage")
    prior = old_usage.get("total_usage")
    system, old_system = current.get("system_cpu_usage"), previous.get("system_cpu_usage")
    online = current.get("online_cpus") or len(current_usage.get("percpu_usage", []))
    if None in (total, prior, system, old_system) or not online or allocated_cpus <= 0:
        return None
    delta, system_delta = total - prior, system - old_system
    if system_delta <= 0 or delta < 0:
        return None
    return max(0.0, 100.0 * delta / system_delta * online / allocated_cpus)

def memory_usage(stats: dict) -> int | None:
    memory = stats.get("memory_stats", {})
    usage = memory.get("usage")
    if usage is None:
        return None
    details = memory.get("stats", {})
    cache = details.get("total_inactive_file", details.get("inactive_file", 0))
    return max(0, usage - cache)

def pressure(cpu: float | None, memory: float, disk: float, healthy: bool, slots: int):
    if not healthy or max(memory, disk) >= 95:
        return "INCIDENT", "red"
    if slots < 1 or max(cpu or 0, memory, disk) >= 80:
        return "HIGH LOAD / LIMITED CAPACITY", "yellow"
    return "OPERATIONAL", "green"
