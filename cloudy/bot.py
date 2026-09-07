import asyncio
import logging
import math
from contextlib import suppress
import discord
from discord.ext import commands, tasks
from . import ui
from .errors import CloudyError
from .security import Throttle

LOG = logging.getLogger("cloudy.bot")

class OwnerView(discord.ui.View):
    def __init__(self, bot, owner_id, *, timeout=180):
        super().__init__(timeout=timeout)
        self.bot, self.owner_id = bot, owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.bot.cfg.guild_id:
            await interaction.response.send_message("Only the VPS owner can use these controls.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error, item):
        LOG.error("UI action failed: %s", type(error).__name__)
        message = str(error) if isinstance(error, CloudyError) else "The request failed. Refresh your panel and try again."
        if interaction.response.is_done():
            await interaction.followup.send(embed=ui.error(message), ephemeral=True)
        else:
            await interaction.response.send_message(embed=ui.error(message), ephemeral=True)

class DeploymentView(OwnerView):
    def __init__(self, bot, owner_id):
        super().__init__(bot, owner_id, timeout=180)
        self.selected, self.launched, self.message = False, False, None
        self.selector = discord.ui.Select(
            placeholder="Choose an operating system",
            options=[discord.SelectOption(label="Ubuntu 22.04 LTS", value="ubuntu22", description="Jammy Jellyfish · x86_64 / ARM64", emoji="🐧")],
        )
        self.selector.callback = self.select_os
        self.launch = discord.ui.Button(label="Launch VPS", style=discord.ButtonStyle.success, emoji="▶️", disabled=True)
        self.launch.callback = self.launch_vps
        self.add_item(self.selector)
        self.add_item(self.launch)

    async def select_os(self, interaction):
        self.selected = True
        self.launch.disabled = False
        self.selector.options[0].default = True
        await interaction.response.edit_message(view=self)

    async def launch_vps(self, interaction):
        self.bot.check_beta(interaction.user)
        if not self.selected or self.launched:
            raise CloudyError("This deployment request is already in use.")
        self.bot.throttle.check((self.owner_id, "deploy"), 30)
        self.launched = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=ui.deployment("Queued for provisioning", 0), view=self)
        self.stop()
        await self.bot.provision(interaction.message, interaction.guild_id, self.owner_id)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message and not self.launched:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)

class ConfirmationView(OwnerView):
    def __init__(self, bot, row):
        super().__init__(bot, row.owner_id, timeout=60)
        self.row, self.used = row, False

    @discord.ui.button(label="Delete files & reinstall", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(view=None, content="Reinstall requested. Please wait…")
        try:
            await self.bot.call(self.bot.engine.operate, self.row.id, interaction.guild_id, interaction.user.id, "reinstall")
            snapshot = await self.bot.call(self.bot.engine.snapshot, self.row.id, interaction.guild_id, interaction.user.id)
            await interaction.edit_original_response(content=None, embed=ui.management(snapshot, self.bot.cfg.node_name), view=ManagementView(self.bot, self.row))
        except Exception as exc:
            message = str(exc) if isinstance(exc, CloudyError) else "Reinstall failed. Use $manage to inspect the current state."
            await interaction.edit_original_response(content=None, embed=ui.error(message), view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content="Reinstall cancelled. Your files were not changed.", embed=None, view=None)
        self.stop()

class ManagementView(OwnerView):
    def __init__(self, bot, row):
        super().__init__(bot, row.owner_id, timeout=None)
        self.row = row
        specs = [
            ("start", "Start", "▶️", discord.ButtonStyle.success, 0),
            ("stop", "Stop", "⏹️", discord.ButtonStyle.secondary, 0),
            ("sshx", "sshx · DM", "🔑", discord.ButtonStyle.primary, 0),
            ("stats", "Stats", "📊", discord.ButtonStyle.secondary, 0),
            ("refresh", "Refresh", "↻", discord.ButtonStyle.secondary, 1),
            ("reinstall", "Reinstall", "♻️", discord.ButtonStyle.danger, 1),
        ]
        for action, label, emoji, style, row_number in specs:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row_number, custom_id=f"cloudy:v1:{row.id}:{action}")
            async def callback(interaction, selected=action):
                await self.handle(interaction, selected)
            button.callback = callback
            self.add_item(button)

    async def handle(self, interaction, action):
        self.bot.throttle.check((interaction.user.id, "manage"), 5)
        row = self.bot.store.owned(self.row.id, interaction.guild_id, interaction.user.id)
        if action == "reinstall":
            text = f"**All files in `{row.container_name}` will be permanently deleted.**\nA clean Ubuntu 22.04 container will replace it. The lease expiration will not change.\nBack up your files before confirming."
            await interaction.response.send_message(embed=ui.embed("Reinstall Ubuntu 22.04?", text, "red"), view=ConfirmationView(self.bot, row), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if action == "sshx":
            await self.bot.deliver_sshx(interaction, row)
            return
        if action in {"start", "stop"}:
            await self.bot.call(self.bot.engine.operate, row.id, interaction.guild_id, interaction.user.id, action)
        snapshot = await self.bot.call(self.bot.engine.snapshot, row.id, interaction.guild_id, interaction.user.id)
        panel = ui.management(snapshot, self.bot.cfg.node_name)
        if action != "stats":
            with suppress(discord.HTTPException):
                await interaction.message.edit(embed=panel, view=self)
            await interaction.edit_original_response(content="Panel refreshed." if action == "refresh" else f"{action.title()} completed.")
        else:
            await interaction.edit_original_response(embed=panel)

class StatusView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Refresh status", emoji="↻", style=discord.ButtonStyle.secondary, custom_id="cloudy:v1:status")
    async def refresh(self, interaction, button):
        if interaction.guild_id != self.bot.cfg.guild_id:
            await interaction.response.send_message("This server is not configured for Cloudy.", ephemeral=True)
            return
        try:
            self.bot.throttle.check((interaction.user.id, "status"), 10)
        except CloudyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer()
        data = await self.bot.call(self.bot.engine.health)
        latency = self.bot.latency * 1000 if math.isfinite(self.bot.latency) else 0
        await interaction.edit_original_response(embed=ui.status(data, latency), view=self)

class CloudyBot(commands.Bot):
    def __init__(self, cfg, store, engine):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="$", intents=intents, help_command=None, allowed_mentions=discord.AllowedMentions.none(), max_messages=200)
        self.cfg, self.store, self.engine = cfg, store, engine
        self.throttle = Throttle()
        self.worker_slots = asyncio.Semaphore(4)
        self.provision_slot = asyncio.Semaphore(1)
        self.deploying = set()
        self.register_commands()

    async def call(self, function, *args):
        async with self.worker_slots:
            return await asyncio.to_thread(function, *args)

    def check_beta(self, member):
        if getattr(getattr(member, "guild", None), "id", None) != self.cfg.guild_id:
            raise CloudyError("This server is not configured for Cloudy.")
        if not self.cfg.open_beta and not any(role.id == self.cfg.required_role_id for role in getattr(member, "roles", [])):
            raise CloudyError("You need the approved beta tester role to deploy a VPS.", "role_required")

    async def setup_hook(self):
        self.add_view(StatusView(self))
        for row in self.store.all():
            self.add_view(ManagementView(self, row))
        try:
            await self.call(self.engine.reconcile)
        except Exception as exc:
            LOG.error("Initial node check failed: %s", type(exc).__name__)
        self.maintenance.start()

    async def on_ready(self):
        LOG.info("Cloudy connected to Discord")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="$deploy · $manage · $status"))

    @tasks.loop(seconds=60)
    async def maintenance(self):
        try:
            await self.call(self.engine.reconcile)
        except Exception as exc:
            LOG.error("Maintenance failed: %s", type(exc).__name__)

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.wait_until_ready()

    async def close(self):
        self.maintenance.cancel()
        await super().close()

    async def provision(self, message, guild_id, owner_id):
        if owner_id in self.deploying:
            await message.edit(embed=ui.error("A deployment is already running for you."), view=None)
            return
        self.deploying.add(owner_id)
        stage, frame = ["Waiting for the deployment worker"], 0
        loop = asyncio.get_running_loop()
        def progress(text):
            loop.call_soon_threadsafe(stage.__setitem__, 0, text)
        async def work():
            async with self.provision_slot:
                return await self.call(self.engine.deploy, guild_id, owner_id, progress)
        task = asyncio.create_task(work())
        try:
            while not task.done():
                # Cosmetic spinner only. Milestones come from the actual Docker operation.
                with suppress(discord.HTTPException):
                    await message.edit(embed=ui.deployment(stage[0], frame), view=None)
                frame += 1
                await asyncio.wait({task}, timeout=2)
            row = await task
            view = ManagementView(self, row)
            self.add_view(view)
            try:
                snapshot = await self.call(self.engine.snapshot, row.id, guild_id, owner_id)
                await message.edit(embed=ui.management(snapshot, self.cfg.node_name), view=view)
            except Exception:
                with suppress(discord.HTTPException):
                    await message.edit(embed=ui.embed("VPS created", "Your VPS was created. Use **$manage** to load the latest panel.", "green"), view=view)
        except Exception as exc:
            LOG.error("Provision request failed: %s", type(exc).__name__)
            text = str(exc) if isinstance(exc, CloudyError) else "The request failed. Use $manage to check whether a VPS exists before retrying."
            with suppress(discord.HTTPException):
                await message.edit(embed=ui.error(text), view=None)
        finally:
            self.deploying.discard(owner_id)

    async def deliver_sshx(self, interaction, row):
        private_message = None
        created = False
        try:
            # Prove DM delivery works BEFORE starting a private terminal session.
            private_message = await interaction.user.send(embed=ui.embed("Preparing private sshx session", "Your private terminal is being prepared. The link will appear **only in this message**."))
            url = await self.call(self.engine.sshx, row.id, interaction.guild_id, interaction.user.id)
            created = True
            # No fallback sends this embed, the URL or relay logs to a guild channel.
            await private_message.edit(embed=ui.session(url, self.cfg.sshx_ttl, row.expires_at))
            await interaction.edit_original_response(content="Your sshx link was sent by DM only. Keep it private.")
        except Exception as exc:
            if created:
                with suppress(Exception):
                    await self.call(self.engine.revoke_sshx, row.id, interaction.guild_id, interaction.user.id)
            text = str(exc) if isinstance(exc, CloudyError) else "I could not deliver the private session. Enable DMs from server members and try again. No link was posted here."
            if private_message:
                with suppress(discord.HTTPException):
                    await private_message.edit(embed=ui.error(text))
            await interaction.edit_original_response(embed=ui.error(text))
            LOG.warning("Private session request failed: %s", type(exc).__name__)

    def register_commands(self):
        @self.check
        async def configured_guild(ctx):
            return bool(ctx.guild and ctx.guild.id == self.cfg.guild_id)

        @self.command(name="deploy")
        @commands.cooldown(1, 15, commands.BucketType.user)
        async def deploy(ctx):
            self.check_beta(ctx.author)
            if ctx.channel.id != self.cfg.deploy_channel_id:
                await ctx.send(f"Please use **$deploy** in <#{self.cfg.deploy_channel_id}>.")
                return
            if self.store.for_owner(ctx.guild.id, ctx.author.id):
                await ctx.send("You already have a VPS. Use **$manage**.")
                return
            view = DeploymentView(self, ctx.author.id)
            view.message = await ctx.send(embed=ui.plan(self.cfg), view=view)

        @self.command(name="manage")
        @commands.cooldown(1, 10, commands.BucketType.user)
        async def manage(ctx):
            row = self.store.for_owner(ctx.guild.id, ctx.author.id)
            if not row:
                await ctx.send(f"You do not have a VPS yet. Use **$deploy** in <#{self.cfg.deploy_channel_id}>.")
                return
            snapshot = await self.call(self.engine.snapshot, row.id, ctx.guild.id, ctx.author.id)
            await ctx.send(embed=ui.management(snapshot, self.cfg.node_name), view=ManagementView(self, row))

        @self.command(name="status")
        @commands.cooldown(1, 10, commands.BucketType.user)
        async def status(ctx):
            data = await self.call(self.engine.health)
            latency = self.latency * 1000 if math.isfinite(self.latency) else 0
            await ctx.send(embed=ui.status(data, latency), view=StatusView(self))

        @self.command(name="help")
        @commands.cooldown(1, 10, commands.BucketType.user)
        async def help_command(ctx):
            out = ui.embed("Cloudy — Command Guide", "**$deploy** — choose Ubuntu 22.04 and launch in the deploy channel.\n**$manage** — open your VPS management panel.\n**$status** — view real node health and available capacity.\n**$help** — show this guide.")
            out.add_field(name="Management buttons", value="**Start · Stop · sshx · Stats · Refresh · Reinstall**\nsshx links are DM-only. Reinstall deletes all files. No public inbound ports are provided.", inline=False)
            await ctx.send(embed=out)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait **{error.retry_after:.0f}s** before using that command again.")
            return
        if isinstance(error, commands.CheckFailure):
            return
        original = getattr(error, "original", error)
        text = str(original) if isinstance(original, CloudyError) else "The request failed. Please retry or contact an administrator."
        LOG.error("Command failed: %s", type(original).__name__)
        with suppress(discord.HTTPException):
            await ctx.send(embed=ui.error(text))
