#!/usr/bin/env python3
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from cloudy.config import Config
from cloudy.engine import Engine
from cloudy.store import Store

def main():
    os.umask(0o077)
    load_dotenv(ROOT / ".env")
    try:
        config = Config.from_env()
        engine = Engine(config, Store(config.database))
        with engine.admission:
            info = engine.preflight()
            capacity = engine._capacity(info)
        print("PASS: Docker, cgroups, Ubuntu image, firewall and writable disk quotas")
        print("Available new VPS slots:", capacity.slots(config))
        if capacity.slots(config) < 1:
            print("NOTICE: the current plan cannot be issued until real capacity is available.")
        return 0
    except Exception as exc:
        print("FAIL:", type(exc).__name__, getattr(exc, "code", "configuration_or_host_error"))
        print("Check README.md and the host configuration. No credentials were printed.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
