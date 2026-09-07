from datetime import datetime, timezone
import discord
from .config import GIB
from .models import days_left, duration, utc_now

COLORS = {"blue": 0x2783DE, "green": 0x46A171, "yellow": 0xD5A13B, "red": 0xE56458}
BRAND = "Cloudy VPS • v1.0-dev"

def embed(title, description=None, color="blue", timestamp=None):
    out = discord.Embed(
        title=title, description=description, color=COLORS[color],
        timestamp=datetime.fromtimestamp(timestamp or utc_now(), timezone.utc),
    )
    out.set_author(name=BRAND)
    out.set_footer(text="FREE BETA 2026 • UTC • Docker containers, not full VMs")
    return out

def bar(value):
    if value is None:
        return "Not available"
    blocks = max(0, min(10, round(value / 10)))
    return f"`{'▰' * blocks}{'▱' * (10 - blocks)}` **{value:.1f}%**"

def management(snapshot, node_name):
    row = snapshot["vps"]
    active = row["expires_at"] > utc_now()
    running = snapshot["status"] == "RUNNING"
    color = "green" if active and running else "yellow" if active else "red"
    icon = "🟢" if active and running else "🟡" if active else "🔴"
    out = embed(
        "VPS Management — VPS 1",
        f"{icon} **{snapshot['status']}**\nManaging container: `{row['container_name']}`\nNode: **{discord.utils.escape_markdown(node_name)}**",
        color, snapshot["sampled_at"],
    )
    out.add_field(name="➤ Allocated Resources", value=f"**{row['ram_bytes'] // GIB} GiB RAM / {row['cpus']} vCPU / {row['disk_bytes'] // GIB} GiB disk**\nUbuntu **22.04 LTS** · writable-disk quota\nCPU is a quota, not dedicated physical cores.", inline=False)
    out.add_field(name="Uptime", value=f"`{duration(snapshot['uptime_seconds'])}`" if snapshot["uptime_seconds"] is not None else "Not running / unavailable", inline=True)
    out.add_field(name="OS", value="`ubuntu:22.04`", inline=True)
    out.add_field(name="➤ Expiration", value=f"**{'ACTIVE' if active else 'EXPIRED'}** · {days_left(row['expires_at'])} days left\nExpires: <t:{row['expires_at']}:F>\n<t:{row['expires_at']}:R>", inline=False)
    cpu = snapshot["cpu_percent"]
    memory = snapshot["memory_bytes"]
    disk = snapshot["disk_bytes"]
    memory_text = "Not available" if memory is None else f"{bar(memory / row['ram_bytes'] * 100)}\n{memory / 1024**2:,.0f} / {row['ram_bytes'] // 1024**2:,} MiB"
    disk_text = "Not available" if disk is None else f"{bar(disk / row['disk_bytes'] * 100)}\n{disk / GIB:.2f} / {row['disk_bytes'] // GIB} GiB"
    out.add_field(name="➤ CPU · quota usage", value=bar(cpu), inline=False)
    out.add_field(name="Memory · working set", value=memory_text, inline=True)
    out.add_field(name="Disk · root filesystem", value=disk_text, inline=True)
    out.add_field(name="➤ Controls", value="Start or stop your VPS below. **sshx is delivered by DM only.**\nReinstall permanently deletes all VPS files. Stats refresh on request (10s cache).", inline=False)
    return out

def status(data, latency):
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    out = embed("Cloudy — Server Status", f"{icons[data['color']]} **{data['state']}**\nNode: **{discord.utils.escape_markdown(data['node'])}**", data["color"], data["sampled_at"])
    out.add_field(name="Services", value=f"Discord: **ONLINE** · {latency:.0f} ms\nDocker: **{'ONLINE' if data['docker'] else 'OFFLINE'}**\nIsolation firewall: **{'READY' if data['firewall'] else 'NOT READY'}**\nDisk quota check: **{'PASSED' if data['quota'] else 'NOT VERIFIED'}**", inline=False)
    out.add_field(name="Host CPU", value=bar(data["cpu"]), inline=False)
    out.add_field(name="Host memory", value=bar(data["memory"]), inline=True)
    out.add_field(name="Docker storage", value=bar(data["disk"]), inline=True)
    out.add_field(name="Beta Capacity", value=f"**{data['instances']}** reserved VPS · **{data['slots']}** new slots available\nStopped VPSs retain their reservations until deletion.", inline=False)
    out.add_field(name="Status guide", value="🟢 Operational  ·  🟡 High load / no capacity  ·  🔴 Incident\nOn-demand snapshot, not an external uptime monitor.", inline=False)
    return out

def error(message):
    return embed("Request could not be completed", message, "red")

def plan(config):
    out = embed("Deploy your free beta VPS", "Select an operating system, then press **Launch VPS**.\nAvailable now: **Ubuntu 22.04 LTS**.")
    out.add_field(name="Beta plan", value=f"**{config.ram_gib} GiB RAM · {config.cpus} vCPU · {config.disk_gib} GiB disk**\n{config.lease_days}-day lease · one VPS per Discord member", inline=False)
    out.add_field(name="Before you launch", value="For approved beta testers on an authorized host. No mining, scanning, spam or abusive traffic. No public inbound ports.\nResources must actually be available. **This is a Docker container, not a full virtual machine.**", inline=False)
    return out

def deployment(stage, frame):
    frames = ["◐", "◓", "◑", "◒"]
    return embed("Deploying Ubuntu 22.04", f"**{frames[frame % len(frames)]} {stage}**\nPlease wait. This panel follows the real provisioning operation.\nNo fake completion percentage is shown.")

def session(url, ttl, expires_at):
    out = embed("Your private sshx session", "**This link grants root shell access to your container.**\nDo not forward, screenshot or post it.", "green")
    out.add_field(name="Open terminal", value=f"[Open your encrypted terminal]({url})", inline=False)
    out.add_field(name="Session lifetime", value=f"Up to **{ttl // 60} minutes**, no later than <t:{expires_at}:F>.\nClicking sshx again revokes the previous bot-created session.", inline=False)
    out.add_field(name="Privacy", value="Sent only to your DMs. sshx is an external end-to-end-encrypted relay; anyone with the full URL can join.", inline=False)
    return out
