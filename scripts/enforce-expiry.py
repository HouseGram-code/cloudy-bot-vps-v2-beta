#!/usr/bin/env python3
"""Independent host timer: stop expired containers even if the Discord bot is down."""
import json
import subprocess
import time

def run(*args):
    return subprocess.run(["/usr/bin/docker", *args], capture_output=True, text=True, timeout=30, check=True).stdout

def main():
    ids = run("ps", "-q", "--filter", "label=io.cloudy.managed=true").split()
    for container_id in ids:
        try:
            data = json.loads(run("inspect", container_id))[0]
            labels = data["Config"].get("Labels") or {}
            if labels.get("io.cloudy.managed") != "true":
                continue
            deadline = int(labels["io.cloudy.expires"])
            if deadline <= int(time.time()):
                if data.get("State", {}).get("Paused"):
                    run("unpause", container_id)
                run("stop", "--time", "10", container_id)
                print("Stopped expired Cloudy container", container_id[:12])
        except (ValueError, KeyError, subprocess.SubprocessError):
            # This log contains no shell-session output, user tokens or API keys.
            print("Could not verify/stop Cloudy container", container_id[:12])

if __name__ == "__main__":
    main()
