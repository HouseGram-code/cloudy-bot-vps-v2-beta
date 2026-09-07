"""These tests run in CI/installed environments, and are skipped if discord.py is unavailable."""
import importlib.util
import unittest
from dataclasses import asdict
from unittest.mock import MagicMock
from cloudy.config import Config
from cloudy.models import VPS

HAS_DISCORD = importlib.util.find_spec("discord") is not None

@unittest.skipUnless(HAS_DISCORD, "discord.py is not installed in this offline build environment")
class DiscordUITests(unittest.IsolatedAsyncioTestCase):
    async def test_management_embed_limits_and_persistent_buttons(self):
        from cloudy import ui
        from cloudy.bot import ManagementView
        row = VPS("a"*32, 100, 123, "cid", "cloudy-test", "cloudy-net-test", 1, 9999999999, "RUNNING", 15*1024**3, 3, 75*1024**3)
        data = {"vps": asdict(row), "sampled_at": 1, "status": "RUNNING", "cpu_percent": 4.8, "memory_bytes": 69*1024**2, "disk_bytes": 1000, "uptime_seconds": 500}
        out = ui.management(data, "Local Node")
        self.assertLess(len(out), 6000)
        self.assertLessEqual(len(out.fields), 25)
        self.assertTrue(all(len(field.value) <= 1024 for field in out.fields))
        bot = MagicMock(cfg=Config(token="unit-test", guild_id=100, deploy_channel_id=200))
        view = ManagementView(bot, row)
        self.assertTrue(view.is_persistent())
        self.assertEqual(len(view.children), 6)
        self.assertEqual(len({c.custom_id for c in view.children}), 6)
        self.assertTrue(all(len(c.custom_id) <= 100 for c in view.children))

    async def test_commands_register_without_connecting(self):
        from cloudy.bot import CloudyBot
        cfg = Config(token="unit-test", guild_id=100, deploy_channel_id=200)
        bot = CloudyBot(cfg, MagicMock(), MagicMock())
        self.assertEqual({cmd.name for cmd in bot.commands}, {"deploy", "manage", "status", "help"})
        await bot.close()

if __name__ == "__main__":
    unittest.main()
