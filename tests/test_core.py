import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cloudy.capacity import Capacity
from cloudy.config import Config, GIB
from cloudy.errors import CloudyError
from cloudy.models import VPS, cpu_percent, days_left, duration, memory_usage, pressure
from cloudy.security import Throttle, extract_sshx_url
from cloudy.store import Store

def config(**changes):
    return replace(Config(token="unit-test", guild_id=100, deploy_channel_id=200, required_role_id=300), **changes)

def vps(key="a" * 32, owner=123, expires=9999999999):
    return VPS(key, 100, owner, None, "cloudy-" + key, "cloudy-net-" + key, 1000, expires, "PROVISIONING", 15 * GIB, 3, 75 * GIB)

class ConfigTests(unittest.TestCase):
    def env(self):
        return {"DISCORD_TOKEN": "unit-test", "GUILD_ID": "100", "DEPLOY_CHANNEL_ID": "200", "REQUIRED_ROLE_ID": "300"}

    def test_defaults_are_real_requested_limits(self):
        with patch.dict(os.environ, self.env(), clear=True):
            c = Config.from_env()
            self.assertEqual((c.ram_gib, c.cpus, c.disk_gib), (15, 3, 75))
            self.assertFalse(c.open_beta)

    def test_token_required(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_placeholder_rejected(self):
        env = self.env() | {"DISCORD_TOKEN": "REPLACE_WITH_NEW_TOKEN"}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_zero_guild_rejected(self):
        with patch.dict(os.environ, self.env() | {"GUILD_ID": "0"}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_role_required_by_default(self):
        with patch.dict(os.environ, self.env() | {"REQUIRED_ROLE_ID": "0"}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_open_beta_requires_explicit_true(self):
        with patch.dict(os.environ, self.env() | {"REQUIRED_ROLE_ID": "0", "OPEN_BETA": "true"}, clear=True):
            self.assertTrue(Config.from_env().open_beta)

    def test_boolean_typos_fail_closed(self):
        with patch.dict(os.environ, self.env() | {"OPEN_BETA": "yes"}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_remote_docker_refused(self):
        with patch.dict(os.environ, self.env() | {"DOCKER_HOST": "tcp://example.invalid:2375"}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_non_numeric_limit_refused(self):
        with patch.dict(os.environ, self.env() | {"VPS_CPUS": "three"}, clear=True), self.assertRaises(ValueError):
            Config.from_env()

class CapacityTests(unittest.TestCase):
    def host(self):
        return Capacity(4, 20*GIB, 100*GIB, 19*GIB, 95*GIB, 0, 0, 0, 0)

    def test_one_plan_fits(self):
        self.assertEqual(self.host().slots(config()), 1)

    def test_free_sized_node_cannot_fake_big_plan(self):
        h = replace(self.host(), total_memory=8*GIB, total_disk=32*GIB, free_memory=7*GIB, free_disk=30*GIB)
        self.assertEqual(h.slots(config()), 0)

    def test_stopped_reservations_still_count(self):
        h = replace(self.host(), reserved_cpu=3, reserved_memory=15*GIB, reserved_disk=75*GIB, instances=1)
        self.assertEqual(h.slots(config(max_vps=10)), 0)

    def test_memory_pressure_blocks_admission(self):
        self.assertEqual(replace(self.host(), free_memory=10*GIB).slots(config()), 0)

    def test_disk_pressure_blocks_admission(self):
        self.assertEqual(replace(self.host(), free_disk=80*GIB).slots(config()), 0)

    def test_no_host_cpu_overbooking(self):
        self.assertEqual(replace(self.host(), total_cpu=3).slots(config()), 0)

class MetricTests(unittest.TestCase):
    def test_cpu_is_percent_of_quota(self):
        s = {"cpu_stats": {"cpu_usage": {"total_usage": 250}, "system_cpu_usage": 1200, "online_cpus": 4}, "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}}
        self.assertEqual(cpu_percent(s, 3), 100)

    def test_missing_cpu_data_is_not_fake_zero(self):
        self.assertIsNone(cpu_percent({}, 3))

    def test_zero_system_delta(self):
        s = {"cpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 50, "online_cpus": 4}, "precpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 50}}
        self.assertIsNone(cpu_percent(s, 3))

    def test_working_set_subtracts_cache(self):
        self.assertEqual(memory_usage({"memory_stats": {"usage": 100, "stats": {"inactive_file": 25}}}), 75)

    def test_missing_memory_is_unknown(self):
        self.assertIsNone(memory_usage({}))

    def test_expiration_boundaries(self):
        self.assertEqual(days_left(100, 100), 0)
        self.assertEqual(days_left(101, 100), 1)
        self.assertEqual(days_left(86401, 0), 2)

    def test_duration_uses_container_elapsed_time(self):
        self.assertEqual(duration(90061), "1d 01:01:01")
        self.assertEqual(duration(-1), "00:00:00")

    def test_health_semantics(self):
        self.assertEqual(pressure(5, 10, 10, True, 1)[1], "green")
        self.assertEqual(pressure(85, 10, 10, True, 1)[1], "yellow")
        self.assertEqual(pressure(5, 10, 10, True, 0)[1], "yellow")
        self.assertEqual(pressure(5, 96, 10, True, 1)[1], "red")
        self.assertEqual(pressure(5, 10, 10, False, 1)[1], "red")

class SecurityTests(unittest.TestCase):
    def test_accepts_official_link_with_ansi(self):
        url = "https://sshx.io/s/demo#test-session-not-real"
        self.assertEqual(extract_sshx_url("\x1b[32m" + url + "\x1b[0m"), url)

    def test_other_domains_and_missing_key_refused(self):
        self.assertIsNone(extract_sshx_url("https://sshx.io.evil.invalid/s/demo#secret"))
        self.assertIsNone(extract_sshx_url("https://sshx.io/s/demo"))
        self.assertIsNone(extract_sshx_url("http://sshx.io/s/demo#secret"))

    def test_throttle_and_independent_owners(self):
        throttle = Throttle()
        throttle.check((1, "start"))
        with self.assertRaises(CloudyError):
            throttle.check((1, "start"))
        throttle.check((2, "start"))

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_restart(self):
        row = vps()
        self.store.add(row)
        self.assertEqual(Store(self.store.path).get(row.id), row)

    def test_non_owner_and_cross_guild_rejected(self):
        row = vps()
        self.store.add(row)
        for guild, owner in [(101, 123), (100, 124)]:
            with self.assertRaises(CloudyError):
                self.store.owned(row.id, guild, owner)

    def test_duplicate_owner_rejected(self):
        self.store.add(vps())
        with self.assertRaises(CloudyError):
            self.store.add(vps(key="b"*32))

    def test_concurrent_duplicate_deploy_only_one_reserves(self):
        gate = threading.Barrier(4)
        def add(i):
            gate.wait()
            try:
                self.store.add(vps(key=str(i)*32))
                return True
            except CloudyError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(add, range(4))), 1)
        self.assertEqual(len(self.store.all()), 1)

    def test_update_cannot_change_owner_or_expiry(self):
        self.store.add(vps())
        with self.assertRaises(ValueError):
            self.store.update(vps().id, owner_id=999)
        with self.assertRaises(ValueError):
            self.store.update(vps().id, expires_at=1)

    def test_sql_injection_is_data(self):
        self.assertIsNone(self.store.get("' OR 1=1 --"))

    def test_delete_releases_owner_slot(self):
        row = vps()
        self.store.add(row)
        self.store.delete(row.id)
        self.store.add(vps(key="b"*32))
        self.assertEqual(len(self.store.all()), 1)

    def test_audit_stores_metadata_only(self):
        self.store.audit(123, "vps", "start")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT action,outcome FROM audit").fetchone()[0], "start")

if __name__ == "__main__":
    unittest.main()
