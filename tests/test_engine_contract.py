"""Offline control-plane tests. Fake Docker responses are NOT a live Docker test."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cloudy.config import Config, GIB
from cloudy.errors import CloudyError
from cloudy.models import VPS
from cloudy.store import Store

class FakeDockerException(Exception):
    pass

class FakeNotFound(FakeDockerException):
    pass

def load_engine():
    # Isolate optional external dependencies so safety/ownership tests run offline.
    fake_docker = types.ModuleType("docker")
    fake_docker.DockerClient = MagicMock()
    fake_errors = types.ModuleType("docker.errors")
    fake_errors.DockerException = FakeDockerException
    fake_errors.NotFound = FakeNotFound
    fake_types = types.ModuleType("docker.types")
    fake_types.LogConfig = lambda **kw: kw
    fake_types.Ulimit = lambda **kw: kw
    fake_psutil = types.ModuleType("psutil")
    fake_psutil.virtual_memory = MagicMock(return_value=types.SimpleNamespace(available=19*GIB, percent=10))
    fake_psutil.cpu_percent = MagicMock(return_value=5)
    spec = importlib.util.spec_from_file_location("cloudy.offline_engine", Path(__file__).resolve().parents[1] / "cloudy/engine.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_errors, "docker.types": fake_types, "psutil": fake_psutil}):
        spec.loader.exec_module(module)
    return module

M = load_engine()

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(token="unit-test", guild_id=100, deploy_channel_id=200)
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        self.engine = M.Engine(self.cfg, self.store)
        self.engine._client = MagicMock()
        self.engine.ready = True
        self.row = VPS("a"*32, 100, 123, "container-test", "cloudy-test", "cloudy-net-test", 100, 9999999999, "RUNNING", 15*GIB, 3, 75*GIB)
        self.store.add(self.row)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wrong_owner_cannot_start(self):
        with self.assertRaises(CloudyError):
            self.engine.operate(self.row.id, 100, 999, "start")
        self.engine.client.containers.get.assert_not_called()

    def test_wrong_owner_cannot_request_sshx(self):
        with self.assertRaises(CloudyError):
            self.engine.sshx(self.row.id, 100, 999)
        self.engine.client.containers.get.assert_not_called()

    def test_guest_settings_include_enforced_quota_and_no_secrets(self):
        self.engine._create_container(self.row)
        kw = self.engine.client.containers.create.call_args.kwargs
        self.assertEqual(kw["mem_limit"], 15*GIB)
        self.assertEqual(kw["memswap_limit"], 15*GIB)
        self.assertEqual(kw["cpu_quota"], 300000)
        self.assertEqual(kw["storage_opt"], {"size": "75G"})
        self.assertEqual(kw["pids_limit"], 512)
        self.assertFalse(kw["privileged"])
        self.assertEqual(kw["restart_policy"]["Name"], "no")
        self.assertEqual(set(kw["environment"]), {"TERM", "LANG"})
        for field in ("volumes", "ports", "devices", "pid_mode", "ipc_mode"):
            self.assertNotIn(field, kw)
        self.assertNotIn("SYS_ADMIN", kw["cap_add"])
        self.assertNotIn("NET_RAW", kw["cap_add"])
        self.assertEqual(kw["security_opt"], ["no-new-privileges:true"])

    def test_mismatched_docker_limits_fail_closed(self):
        container = MagicMock(attrs={"HostConfig": {"Memory": 0}})
        with self.assertRaises(CloudyError):
            self.engine._verify_limits(container, self.row)

    def test_wrong_labels_refused(self):
        self.engine.client.containers.get.return_value.labels = {"io.cloudy.managed": "true", "io.cloudy.owner": "999"}
        with self.assertRaises(CloudyError):
            self.engine._container(self.row)

    def test_unsupported_disk_backend_refused(self):
        self.engine.client.info.return_value = {"Driver": "overlayfs"}
        with self.assertRaises(CloudyError) as error:
            self.engine.preflight()
        self.assertEqual(error.exception.code, "quota_backend")
        self.engine.client.containers.create.assert_not_called()

    def test_duplicate_deploy_does_not_touch_docker(self):
        with self.assertRaises(CloudyError):
            self.engine.deploy(100, 123)
        self.engine.client.info.assert_not_called()

    def test_not_reconciled_node_refuses_start(self):
        self.engine.ready = False
        with self.assertRaises(CloudyError) as error:
            self.engine.operate(self.row.id, 100, 123, "start")
        self.assertEqual(error.exception.code, "node_not_ready")

    def test_expired_lease_refuses_start(self):
        with patch.object(M, "utc_now", return_value=10000000000), self.assertRaises(CloudyError) as error:
            self.engine.operate(self.row.id, 100, 123, "start")
        self.assertEqual(error.exception.code, "expired")

    def test_unknown_action_refused(self):
        with self.assertRaises(ValueError):
            self.engine.operate(self.row.id, 100, 123, "exec arbitrary host command")

    def test_missing_container_has_unknown_not_fake_stats(self):
        self.engine.client.containers.get.side_effect = FakeNotFound()
        data = self.engine.snapshot(self.row.id, 100, 123)
        self.assertEqual(data["status"], "MISSING")
        self.assertIsNone(data["cpu_percent"])
        self.assertIsNone(data["disk_bytes"])

    def test_docker_offline_produces_red_status(self):
        self.engine.client.info.side_effect = FakeDockerException()
        data = self.engine.health()
        self.assertEqual(data["color"], "red")
        self.assertFalse(data["docker"])

if __name__ == "__main__":
    unittest.main()
