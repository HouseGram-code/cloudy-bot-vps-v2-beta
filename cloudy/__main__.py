import fcntl
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from .bot import CloudyBot
from .config import Config
from .engine import Engine
from .store import Store

def main():
    os.umask(0o077)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Avoid HTTP debug payloads, terminal output and credential-bearing errors.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("docker").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    lock = (cfg.data_dir / "bot.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another Cloudy bot is already using this data directory.", file=sys.stderr)
        return 2
    store = Store(cfg.database)
    engine = Engine(cfg, store)
    bot = CloudyBot(cfg, store, engine)
    try:
        bot.run(cfg.token, log_handler=None)
    except Exception as exc:
        # Do not print exception payloads that might include a token.
        print(f"Bot stopped: {type(exc).__name__}. Check the token, intents and network.", file=sys.stderr)
        return 1
    finally:
        lock.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
