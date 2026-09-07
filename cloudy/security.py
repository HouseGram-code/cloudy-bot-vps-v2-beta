import re
import threading
import time
from .errors import CloudyError

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SSHX_URL = re.compile(r"https://sshx\.io/s/[A-Za-z0-9_-]+#[A-Za-z0-9_~+/=%.-]+")

def extract_sshx_url(output: str) -> str | None:
    match = SSHX_URL.search(ANSI.sub("", output))
    return match.group(0) if match else None

class Throttle:
    def __init__(self):
        self.last = {}
        self.lock = threading.Lock()

    def check(self, key, seconds=5):
        with self.lock:
            now = time.monotonic()
            if now - self.last.get(key, -1e20) < seconds:
                raise CloudyError("Please wait a few seconds before trying again.", "rate_limited")
            self.last[key] = now
            if len(self.last) > 10000:
                self.last = {k: t for k, t in self.last.items() if now - t < 3600}
