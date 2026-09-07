from dataclasses import dataclass

@dataclass(frozen=True)
class Capacity:
    total_cpu: int
    total_memory: int
    total_disk: int
    free_memory: int
    free_disk: int
    reserved_cpu: int
    reserved_memory: int
    reserved_disk: int
    instances: int

    def slots(self, config) -> int:
        from .config import GIB
        bounds = [
            config.max_vps - self.instances,
            (self.total_cpu - config.reserve_cpus - self.reserved_cpu) // config.cpus,
            (self.total_memory - config.reserve_ram_gib * GIB - self.reserved_memory) // (config.ram_gib * GIB),
            (self.total_disk - config.reserve_disk_gib * GIB - self.reserved_disk) // (config.disk_gib * GIB),
            (self.free_memory - config.reserve_ram_gib * GIB) // (config.ram_gib * GIB),
            (self.free_disk - config.reserve_disk_gib * GIB) // (config.disk_gib * GIB),
        ]
        return max(0, min(bounds))
