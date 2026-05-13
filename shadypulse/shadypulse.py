"""
ShadyPulse - Health monitoring cog.
Monitors bot status, HTTP services, and cog health.
"""
import discord
from discord import app_commands
import asyncio
import aiohttp
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from enum import Enum

from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import List

log = logging.getLogger("red.shadycogs.shadypulse")


class ServiceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


STATUS_EMOJI = {ServiceStatus.ONLINE: "🟢", ServiceStatus.OFFLINE: "🔴", ServiceStatus.DEGRADED: "🟡"}
STATUS_COLOR = {
    ServiceStatus.ONLINE: discord.Color.green(),
    ServiceStatus.OFFLINE: discord.Color.red(),
    ServiceStatus.DEGRADED: discord.Color.orange(),
}


# ==================== UI COMPONENTS ====================


class SettingsModal(discord.ui.Modal, title="Pulse Settings"):
    """Modal for main settings."""

    interval = discord.ui.TextInput(label="Check Interval (seconds)", placeholder="60", max_length=4)
    max_retries = discord.ui.TextInput(label="Cog Reload Max Retries", placeholder="3", max_length=2)
    cooldown = discord.ui.TextInput(label="Cog Reload Cooldown (seconds)", placeholder="60", max_length=4)

    def __init__(self, cog: "ShadyPulse", config: dict):
        super().__init__()
        self.cog = cog
        self.interval.default = str(config.get("check_interval_seconds", 60))
        self.max_retries.default = str(config.get("cog_max_retries", 3))
        self.cooldown.default = str(config.get("cog_retry_cooldown_seconds", 60))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            interval = max(30, min(600, int(self.interval.value)))
            retries = max(1, min(10, int(self.max_retries.value)))
            cooldown = max(30, min(600, int(self.cooldown.value)))
        except ValueError:
            await interaction.response.send_message("Invalid values.", ephemeral=True)
            return

        await self.cog.config.check_interval_seconds.set(interval)
        await self.cog.config.cog_max_retries.set(retries)
        await self.cog.config.cog_retry_cooldown_seconds.set(cooldown)

        await interaction.response.send_message(
            f"Settings updated!\n**Interval:** {interval}s\n**Retries:** {retries}\n**Cooldown:** {cooldown}s",
            ephemeral=True
        )


class AddHttpModal(discord.ui.Modal, title="Add HTTP Service"):
    """Modal for adding HTTP service."""

    name = discord.ui.TextInput(label="Service Name", placeholder="my-api", max_length=32)
    url = discord.ui.TextInput(label="URL", placeholder="https://api.example.com/health")
    expected = discord.ui.TextInput(label="Expected Status Code", placeholder="200", max_length=3)
    timeout = discord.ui.TextInput(label="Timeout (seconds)", placeholder="10", max_length=3)

    def __init__(self, cog: "ShadyPulse"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            status_code = int(self.expected.value)
            timeout = int(self.timeout.value)
        except ValueError:
            await interaction.response.send_message("Invalid numbers.", ephemeral=True)
            return

        async with self.cog.config.http_services() as services:
            services[self.name.value] = {
                "url": self.url.value,
                "expected_status": status_code,
                "timeout": timeout,
                "enabled": True,
            }

        await interaction.response.send_message(f"✅ Added HTTP service `{self.name.value}`")


class AddCogModal(discord.ui.Modal, title="Add Cog Monitor"):
    """Modal for adding cog monitor."""

    cog_name = discord.ui.TextInput(label="Cog Class Name", placeholder="LevelUp")
    extension = discord.ui.TextInput(label="Extension Path (for auto-reload)", placeholder="mycogs.levelup", required=False)

    def __init__(self, cog: "ShadyPulse"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        async with self.cog.config.monitored_cogs() as cogs:
            cogs[self.cog_name.value] = {
                "enabled": True,
                "extension_name": self.extension.value or None,
                "consecutive_failures": 0,
                "reload_attempts": 0,
            }

        ext_msg = f" (extension: `{self.extension.value}`)" if self.extension.value else " (no auto-reload)"
        await interaction.response.send_message(f"✅ Monitoring `{self.cog_name.value}`{ext_msg}")


class SetupView(discord.ui.View):
    """Setup view for ShadyPulse."""

    def __init__(self, cog: "ShadyPulse", config: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.config = config

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.all()
        await interaction.response.send_modal(SettingsModal(self.cog, config))

    @discord.ui.button(label="Add HTTP", style=discord.ButtonStyle.secondary, emoji="🌐")
    async def add_http_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddHttpModal(self.cog))

    @discord.ui.button(label="Add Cog", style=discord.ButtonStyle.secondary, emoji="🔌")
    async def add_cog_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCogModal(self.cog))

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success, emoji="✅")
    async def enable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.enabled.set(True)
        await interaction.response.send_message("Monitoring **enabled**.", ephemeral=True)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, emoji="❌")
    async def disable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.enabled.set(False)
        await interaction.response.send_message("Monitoring **disabled**.", ephemeral=True)


# ==================== MAIN COG ====================


class ShadyPulse(commands.Cog):
    """Health monitoring for bot and services."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567896, force_registration=True)

        default_global = {
            "enabled": True,
            "check_interval_seconds": 60,
            "alert_channel": None,
            "http_services": {},
            "monitored_cogs": {},
            "cog_auto_reload": False,
            "cog_max_retries": 3,
            "cog_retry_cooldown_seconds": 60,
        }
        self.config.register_global(**default_global)

        self.service_status: Dict[str, Dict] = {}
        self.monitor_task: Optional[asyncio.Task] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.start_time = datetime.now(timezone.utc)

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        log.info("ShadyPulse loaded")

    async def cog_unload(self) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
        if self.session:
            await self.session.close()

    async def _monitor_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                if await self.config.enabled():
                    await self._run_checks()
                await asyncio.sleep(await self.config.check_interval_seconds())
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Monitor error: {e}")
                await asyncio.sleep(60)

    async def _run_checks(self) -> None:
        results = {}

        # Bot health
        latency = self.bot.latency * 1000
        status = ServiceStatus.ONLINE if latency < 100 else ServiceStatus.DEGRADED if latency < 500 else ServiceStatus.OFFLINE
        results["bot"] = {"status": status.value, "latency_ms": round(latency, 2)}

        # HTTP services
        for name, cfg in (await self.config.http_services()).items():
            if cfg.get("enabled", True):
                results[f"http:{name}"] = await self._check_http(cfg)

        # Cogs
        for name, cfg in (await self.config.monitored_cogs()).items():
            if cfg.get("enabled", True):
                loaded = self.bot.get_cog(name) is not None
                results[f"cog:{name}"] = {"status": ServiceStatus.ONLINE.value if loaded else ServiceStatus.OFFLINE.value}

                if not loaded:
                    await self._handle_cog_failure(name, cfg)

        self.service_status = results

    async def _check_http(self, cfg: Dict) -> Dict:
        try:
            async with self.session.get(
                cfg["url"],
                timeout=aiohttp.ClientTimeout(total=cfg.get("timeout", 10)),
                ssl=False
            ) as resp:
                status = ServiceStatus.ONLINE if resp.status == cfg.get("expected_status", 200) else ServiceStatus.DEGRADED
                return {"status": status.value, "code": resp.status}
        except Exception as e:
            return {"status": ServiceStatus.OFFLINE.value, "error": str(e)[:50]}

    async def _handle_cog_failure(self, cog_name: str, cfg: Dict) -> None:
        if not await self.config.cog_auto_reload():
            return

        ext = cfg.get("extension_name")
        if not ext:
            return

        max_retries = await self.config.cog_max_retries()
        async with self.config.monitored_cogs() as cogs:
            if cog_name not in cogs:
                return
            cogs[cog_name]["reload_attempts"] = cogs[cog_name].get("reload_attempts", 0) + 1
            if cogs[cog_name]["reload_attempts"] > max_retries:
                return

        try:
            await self.bot.reload_extension(ext)
            log.info(f"Reloaded {cog_name}")
        except Exception as e:
            log.error(f"Failed to reload {cog_name}: {e}")

    def _format_uptime(self, seconds: int) -> str:
        d, h, m = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        return " ".join(parts) or "< 1m"

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to view health status."""
        # Bot owner always authorized
        if await self.bot.is_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/guild owner always authorized
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True

        # Check manage_guild permission
        if interaction.user.guild_permissions.manage_guild:
            return True

        return False

    async def bot_channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for channels the bot can see and send messages to."""
        if not interaction.guild:
            return []

        choices = []
        for channel in interaction.guild.text_channels:
            perms = channel.permissions_for(interaction.guild.me)
            if perms.view_channel and perms.send_messages:
                if current.lower() in channel.name.lower():
                    label = f"#{channel.name}"
                    if channel.category:
                        label = f"#{channel.name} ({channel.category.name})"
                    choices.append(app_commands.Choice(name=label[:100], value=str(channel.id)))

        choices.sort(key=lambda c: int(c.value))
        return choices[:25]

    # ==================== STAFF COMMANDS ====================

    @app_commands.command(name="pulse", description="Health monitoring dashboard")
    @app_commands.guild_only()
    async def pulse_dashboard(self, interaction: discord.Interaction):
        """Show health dashboard (mod-only)."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to view health status.", ephemeral=True)
            return

        results = self.service_status

        statuses = [r.get("status", "unknown") for r in results.values()]
        if all(s == "online" for s in statuses):
            overall = ServiceStatus.ONLINE
        elif any(s == "offline" for s in statuses):
            overall = ServiceStatus.OFFLINE
        else:
            overall = ServiceStatus.DEGRADED

        embed = discord.Embed(
            title=f"{STATUS_EMOJI[overall]} System Health",
            color=STATUS_COLOR[overall],
            timestamp=datetime.now(timezone.utc)
        )

        # Bot
        bot_data = results.get("bot", {})
        uptime = self._format_uptime(int((datetime.now(timezone.utc) - self.start_time).total_seconds()))
        embed.add_field(
            name="🤖 Bot",
            value=f"Latency: **{bot_data.get('latency_ms', 0):.0f}ms**\nUptime: **{uptime}**",
            inline=True
        )

        # HTTP
        http_lines = []
        for name, data in results.items():
            if name.startswith("http:"):
                status = ServiceStatus(data.get("status", "unknown"))
                http_lines.append(f"{STATUS_EMOJI[status]} {name[5:]}")
        if http_lines:
            embed.add_field(name="🌐 HTTP", value="\n".join(http_lines), inline=True)

        # Cogs
        cog_lines = []
        for name, data in results.items():
            if name.startswith("cog:"):
                status = ServiceStatus(data.get("status", "unknown"))
                cog_lines.append(f"{STATUS_EMOJI[status]} {name[4:]}")
        if cog_lines:
            embed.add_field(name="🔌 Cogs", value="\n".join(cog_lines), inline=True)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="uptime", description="Show bot uptime")
    @app_commands.guild_only()
    async def show_uptime(self, interaction: discord.Interaction):
        """Show uptime (mod-only)."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to view uptime.", ephemeral=True)
            return

        uptime = self._format_uptime(int((datetime.now(timezone.utc) - self.start_time).total_seconds()))
        embed = discord.Embed(title="🕐 Bot Uptime", color=discord.Color.green())
        embed.add_field(name="Uptime", value=f"**{uptime}**", inline=True)
        embed.add_field(name="Started", value=f"<t:{int(self.start_time.timestamp())}:F>", inline=True)
        embed.add_field(name="Latency", value=f"**{self.bot.latency * 1000:.0f}ms**", inline=True)
        await interaction.response.send_message(embed=embed)

    # ==================== OWNER COMMANDS ====================

    @commands.hybrid_group(name="shadypulse", aliases=["sp"])
    @commands.is_owner()
    async def shadypulse(self, ctx: commands.Context):
        """Manage health monitoring (Bot Owner only)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @shadypulse.command(name="setup")
    async def pulse_setup(self, ctx: commands.Context):
        """Interactive setup."""
        config = await self.config.all()

        embed = discord.Embed(title="⚙️ ShadyPulse Setup", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if config["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Interval", value=f"{config['check_interval_seconds']}s", inline=True)
        embed.add_field(name="HTTP Services", value=str(len(config["http_services"])), inline=True)
        embed.add_field(name="Monitored Cogs", value=str(len(config["monitored_cogs"])), inline=True)
        embed.add_field(name="Auto-Reload", value="✅" if config["cog_auto_reload"] else "❌", inline=True)

        await ctx.send(embed=embed, view=SetupView(self, config), ephemeral=True)

    @shadypulse.command(name="status")
    async def pulse_status(self, ctx: commands.Context):
        """Show detailed configuration."""
        config = await self.config.all()

        embed = discord.Embed(title="⚙️ ShadyPulse Configuration", color=discord.Color.blue())
        embed.add_field(name="Monitoring", value="✅ Enabled" if config["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Interval", value=f"{config['check_interval_seconds']}s", inline=True)
        embed.add_field(name="Alert Channel", value=f"<#{config['alert_channel']}>" if config["alert_channel"] else "Not set", inline=True)
        embed.add_field(name="HTTP Services", value=str(len(config["http_services"])), inline=True)
        embed.add_field(name="Monitored Cogs", value=str(len(config["monitored_cogs"])), inline=True)

        auto = f"✅ ({config['cog_max_retries']} retries, {config['cog_retry_cooldown_seconds']}s cooldown)" if config["cog_auto_reload"] else "❌"
        embed.add_field(name="Auto-Reload", value=auto, inline=False)

        await ctx.send(embed=embed)

    @shadypulse.command(name="alert")
    @app_commands.describe(channel="Alert channel (bot-visible channels, leave empty to disable)")
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def pulse_alert(self, ctx: commands.Context, channel: str = None):
        """Set alert channel for downtime notifications."""
        if channel is None:
            await self.config.alert_channel.set(None)
            await ctx.send("✅ Alert channel disabled.")
            return

        try:
            channel_id = int(channel)
            ch = ctx.guild.get_channel(channel_id) if ctx.guild else None
            if not ch:
                await ctx.send("Channel not found.", ephemeral=True)
                return
            await self.config.alert_channel.set(channel_id)
            await ctx.send(f"✅ Alert channel set to {ch.mention}")
        except ValueError:
            await ctx.send("Invalid channel.", ephemeral=True)

    @shadypulse.command(name="autoreload")
    @app_commands.describe(enabled="Enable or disable cog auto-reload")
    async def pulse_autoreload(self, ctx: commands.Context, enabled: bool):
        """Toggle cog auto-reload."""
        await self.config.cog_auto_reload.set(enabled)
        await ctx.send(f"Cog auto-reload: {'Enabled' if enabled else 'Disabled'}")

    @shadypulse.command(name="removehttp")
    @app_commands.describe(name="HTTP service name to remove")
    async def pulse_removehttp(self, ctx: commands.Context, name: str):
        """Remove an HTTP service."""
        async with self.config.http_services() as services:
            if name not in services:
                await ctx.send(f"Service `{name}` not found.")
                return
            del services[name]
        await ctx.send(f"✅ Removed `{name}`")

    @shadypulse.command(name="removecog")
    @app_commands.describe(name="Cog name to stop monitoring")
    async def pulse_removecog(self, ctx: commands.Context, name: str):
        """Remove a cog from monitoring."""
        async with self.config.monitored_cogs() as cogs:
            if name not in cogs:
                await ctx.send(f"Cog `{name}` not monitored.")
                return
            del cogs[name]
        await ctx.send(f"✅ Stopped monitoring `{name}`")

    @shadypulse.command(name="list")
    async def pulse_list(self, ctx: commands.Context):
        """List all monitored services."""
        config = await self.config.all()

        embed = discord.Embed(title="📋 Monitored Services", color=discord.Color.blue())

        if config["http_services"]:
            http_list = "\n".join([f"• `{n}`: {c['url']}" for n, c in config["http_services"].items()])
            embed.add_field(name="🌐 HTTP Services", value=http_list, inline=False)
        else:
            embed.add_field(name="🌐 HTTP Services", value="None", inline=False)

        if config["monitored_cogs"]:
            cog_list = "\n".join([
                f"• `{n}` {'(auto-reload)' if c.get('extension_name') else ''}"
                for n, c in config["monitored_cogs"].items()
            ])
            embed.add_field(name="🔌 Monitored Cogs", value=cog_list, inline=False)
        else:
            embed.add_field(name="🔌 Monitored Cogs", value="None", inline=False)

        await ctx.send(embed=embed)


async def setup(bot: Red) -> None:
    await bot.add_cog(ShadyPulse(bot))
