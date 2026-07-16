"""
ShadyPulse - Health monitoring cog.
Monitors bot status, HTTP services, and cog health.
"""
import discord
from discord import app_commands
import asyncio
import aiohttp
import logging
import traceback
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


# Errors that represent user/usage problems, not a cog "erroring out". We don't
# want these polluting the health signal, so they're never recorded as failures.
IGNORED_PREFIX_ERRORS = (
    commands.CommandNotFound,
    commands.CheckFailure,
    commands.UserInputError,
    commands.DisabledCommand,
    commands.CommandOnCooldown,
    commands.MaxConcurrencyReached,
    commands.NoPrivateMessage,
    commands.PrivateMessageOnly,
)
IGNORED_APP_ERRORS = (
    app_commands.CheckFailure,
    app_commands.CommandOnCooldown,
)


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
            "alert_on_error": True,
            "cog_error_window_seconds": 300,
        }
        self.config.register_global(**default_global)

        self.service_status: Dict[str, Dict] = {}
        self.monitor_task: Optional[asyncio.Task] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.start_time = datetime.now(timezone.utc)

        # In-memory error tracking (resets on reload). Keyed by cog name.
        # Each entry: {"count": int, "last_error": {command, type, message, traceback, timestamp}}
        self.cog_errors: Dict[str, Dict] = {}
        self._last_error_alert: Dict[str, datetime] = {}
        self._orig_tree_error = None

        # In-memory reload throttle. name -> {"attempts": int, "last": datetime}
        self._reload_state: Dict[str, Dict] = {}
        # When a cog was last reloaded, so errors from before the reload don't
        # keep it Degraded (and don't re-trigger another reload immediately).
        self._reloaded_at: Dict[str, datetime] = {}

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        # Hook the app-command (slash) error path. discord.py calls tree.on_error
        # for every app command failure; we wrap it so we can snapshot the error
        # and still chain to Red's handler so the user is notified as normal.
        self._orig_tree_error = self.bot.tree.on_error
        self.bot.tree.on_error = self._on_app_command_error
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        log.info("ShadyPulse loaded")

    async def cog_unload(self) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
        if self._orig_tree_error is not None:
            self.bot.tree.on_error = self._orig_tree_error
        if self.session:
            await self.session.close()

    # ==================== COMMAND ERROR CAPTURE ====================

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        """Capture real exceptions from prefix/hybrid commands in any cog.

        This listener is additive: Red's own error handling still runs, so users
        still get their error messages. We only record genuine failures.
        """
        if isinstance(error, IGNORED_PREFIX_ERRORS):
            return
        cog_name = ctx.cog.qualified_name if ctx.cog else None
        command_name = ctx.command.qualified_name if ctx.command else "unknown"
        await self._record_command_error(cog_name, command_name, error)

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Wrapper around tree.on_error to capture slash-command failures."""
        try:
            if not isinstance(error, IGNORED_APP_ERRORS):
                cog_name = None
                command_name = "unknown"
                cmd = interaction.command
                if cmd is not None:
                    command_name = cmd.qualified_name
                    binding = getattr(cmd, "binding", None)
                    if binding is not None:
                        cog_name = getattr(binding, "qualified_name", type(binding).__name__)
                await self._record_command_error(cog_name, command_name, error)
        except Exception as e:  # never let our capture break the real handler
            log.error(f"ShadyPulse error-capture failed: {e}")
        # Chain to the original handler so Red still notifies the user.
        if self._orig_tree_error is not None:
            await self._orig_tree_error(interaction, error)

    async def _record_command_error(
        self, cog_name: Optional[str], command_name: str, error: Exception
    ) -> None:
        # Unwrap CommandInvokeError-style wrappers to get the real exception.
        original = getattr(error, "original", error)
        tb = "".join(traceback.format_exception(type(original), original, original.__traceback__))
        now = datetime.now(timezone.utc)
        entry = {
            "command": command_name,
            "type": type(original).__name__,
            "message": (str(original)[:500] or "(no message)"),
            "traceback": tb,
            "timestamp": now.isoformat(),
        }
        key = cog_name or "(unknown)"
        rec = self.cog_errors.setdefault(key, {"count": 0, "last_error": None})
        rec["count"] += 1
        rec["last_error"] = entry
        log.warning(f"Captured command error in {key}.{command_name}: {type(original).__name__}: {original}")
        await self._maybe_alert_error(key, entry)

    async def _maybe_alert_error(self, cog_key: str, entry: Dict) -> None:
        if not await self.config.alert_on_error():
            return
        now = datetime.now(timezone.utc)
        cooldown = await self.config.cog_retry_cooldown_seconds()
        last = self._last_error_alert.get(cog_key)
        if last and (now - last).total_seconds() < cooldown:
            return
        self._last_error_alert[cog_key] = now

        embed = discord.Embed(
            title=f"⚠️ Command Error in {cog_key}",
            color=discord.Color.orange(),
            timestamp=now,
        )
        embed.add_field(name="Command", value=f"`{entry['command']}`", inline=True)
        embed.add_field(name="Type", value=f"`{entry['type']}`", inline=True)
        embed.add_field(name="Message", value=f"```{entry['message'][:400]}```", inline=False)
        tb = entry["traceback"]
        if len(tb) > 1000:
            tb = "...\n" + tb[-1000:]
        embed.add_field(name="Traceback", value=f"```py\n{tb}```", inline=False)
        await self._send_alert(embed)

    async def _send_alert(self, embed: discord.Embed) -> None:
        channel_id = await self.config.alert_channel()
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.error(f"Failed to send alert: {e}")

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
        old = self.service_status

        # Bot health
        latency = self.bot.latency * 1000
        status = ServiceStatus.ONLINE if latency < 100 else ServiceStatus.DEGRADED if latency < 500 else ServiceStatus.OFFLINE
        results["bot"] = {"status": status.value, "latency_ms": round(latency, 2)}

        # HTTP services
        for name, cfg in (await self.config.http_services()).items():
            if cfg.get("enabled", True):
                results[f"http:{name}"] = await self._check_http(cfg)

        # Cogs: loaded state + recent command errors form the health signal.
        window = await self.config.cog_error_window_seconds()
        now = datetime.now(timezone.utc)
        for name, cfg in (await self.config.monitored_cogs()).items():
            if not cfg.get("enabled", True):
                continue

            loaded = self.bot.get_cog(name) is not None
            rec = self.cog_errors.get(name)
            error_count = 0
            recent_error = False
            if rec and rec["last_error"]:
                error_count = rec["count"]
                ts = datetime.fromisoformat(rec["last_error"]["timestamp"])
                reloaded = self._reloaded_at.get(name)
                # Only errors *since the last reload* count toward Degraded.
                if reloaded is None or ts > reloaded:
                    recent_error = (now - ts).total_seconds() < window

            if not loaded:
                cog_status = ServiceStatus.OFFLINE
            elif recent_error:
                # Loaded but a command threw recently -> not fully healthy.
                cog_status = ServiceStatus.DEGRADED
            else:
                cog_status = ServiceStatus.ONLINE

            results[f"cog:{name}"] = {
                "status": cog_status.value,
                "error_count": error_count,
                "recent_error": recent_error,
            }

            if cog_status == ServiceStatus.ONLINE:
                # Healthy again -> reset the reload throttle so future failures
                # get a fresh set of attempts.
                self._reload_state.pop(name, None)
            else:
                # Offline (unloaded) or Degraded (erroring) -> try to reload.
                await self._handle_cog_failure(name, cfg, cog_status)

        await self._check_transitions(old, results)
        self.service_status = results

    async def _check_transitions(self, old: Dict, new: Dict) -> None:
        """Alert the configured channel when a service changes up/down state."""
        for key, data in new.items():
            new_status = data.get("status")
            old_status = old.get(key, {}).get("status") if old else None
            if old_status is None or old_status == new_status:
                continue
            if new_status == ServiceStatus.OFFLINE.value:
                await self._send_status_alert(key, old_status, new_status)
            elif new_status == ServiceStatus.ONLINE.value and old_status in ("offline", "degraded"):
                await self._send_status_alert(key, old_status, new_status)

    async def _send_status_alert(self, key: str, old_status: str, new_status: str) -> None:
        recovered = new_status == ServiceStatus.ONLINE.value
        status_enum = ServiceStatus(new_status)
        embed = discord.Embed(
            title=f"{STATUS_EMOJI[status_enum]} {'Recovered' if recovered else 'Down'}: {key}",
            description=f"`{old_status}` → `{new_status}`",
            color=STATUS_COLOR[status_enum],
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_alert(embed)

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

    async def _handle_cog_failure(self, cog_name: str, cfg: Dict, status: ServiceStatus) -> None:
        """Attempt an auto-reload for a cog that is Offline (unloaded) or
        Degraded (loaded but throwing command errors).

        Throttled by an in-memory cooldown + attempt cap. The cap resets once the
        cog is healthy again (see _run_checks), so a flaky cog that recovers keeps
        getting reloaded, while one that stays broken stops after max_retries.
        """
        if not await self.config.cog_auto_reload():
            return

        ext = cfg.get("extension_name")
        if not ext:
            return  # can't reload without an extension path

        now = datetime.now(timezone.utc)
        max_retries = await self.config.cog_max_retries()
        cooldown = await self.config.cog_retry_cooldown_seconds()

        state = self._reload_state.setdefault(cog_name, {"attempts": 0, "last": None})
        if state["last"] and (now - state["last"]).total_seconds() < cooldown:
            return  # still within the reload cooldown
        if state["attempts"] >= max_retries:
            return  # gave up until the cog is healthy again

        state["attempts"] += 1
        state["last"] = now
        attempt = state["attempts"]
        reason = "unloaded" if status == ServiceStatus.OFFLINE else "command errors"

        try:
            await self.bot.reload_extension(ext)
            # Mark the reload time so pre-reload errors stop counting as recent.
            self._reloaded_at[cog_name] = now
            log.info(f"Reloaded {cog_name} ({reason}), attempt {attempt}/{max_retries}")
            await self._send_alert(discord.Embed(
                title=f"🔄 Reloaded {cog_name}",
                description=f"Reason: **{reason}**\nAttempt {attempt}/{max_retries}",
                color=discord.Color.blurple(),
                timestamp=now,
            ))
        except Exception as e:
            log.error(f"Failed to reload {cog_name}: {e}")
            await self._send_alert(discord.Embed(
                title=f"❌ Reload failed: {cog_name}",
                description=f"Reason: **{reason}**\nAttempt {attempt}/{max_retries}\n```{str(e)[:300]}```",
                color=discord.Color.red(),
                timestamp=now,
            ))

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
                line = f"{STATUS_EMOJI[status]} {name[4:]}"
                if data.get("error_count"):
                    line += f" ⚠️ {data['error_count']}"
                cog_lines.append(line)
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

        err_alert = "✅" if config.get("alert_on_error", True) else "❌"
        embed.add_field(
            name="Error Alerts",
            value=f"{err_alert} (window {config.get('cog_error_window_seconds', 300)}s)",
            inline=False,
        )

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

    @shadypulse.command(name="alerterrors")
    @app_commands.describe(enabled="Alert the channel when a command throws an exception")
    async def pulse_alerterrors(self, ctx: commands.Context, enabled: bool):
        """Toggle immediate alerts on captured command errors."""
        await self.config.alert_on_error.set(enabled)
        await ctx.send(f"Command-error alerts: {'Enabled' if enabled else 'Disabled'}")

    @shadypulse.command(name="errors")
    @app_commands.describe(name="Cog name to show the last traceback for (omit for a summary)")
    async def pulse_errors(self, ctx: commands.Context, name: str = None):
        """Show captured command errors and tracebacks."""
        if not self.cog_errors:
            await ctx.send("No command errors captured since last reload. ✅")
            return

        if name:
            rec = self.cog_errors.get(name)
            if not rec or not rec["last_error"]:
                await ctx.send(f"No errors captured for `{name}`.")
                return
            e = rec["last_error"]
            embed = discord.Embed(
                title=f"⚠️ Last error in {name}",
                color=discord.Color.orange(),
                timestamp=datetime.fromisoformat(e["timestamp"]),
            )
            embed.add_field(name="Command", value=f"`{e['command']}`", inline=True)
            embed.add_field(name="Type", value=f"`{e['type']}`", inline=True)
            embed.add_field(name="Total Errors", value=str(rec["count"]), inline=True)
            embed.add_field(name="Message", value=f"```{e['message'][:400]}```", inline=False)
            tb = e["traceback"]
            if len(tb) > 1400:
                tb = "...\n" + tb[-1400:]
            embed.add_field(name="Traceback", value=f"```py\n{tb}```", inline=False)
            await ctx.send(embed=embed)
            return

        lines = []
        for k, rec in self.cog_errors.items():
            le = rec["last_error"]
            if le:
                lines.append(f"• `{k}` — {rec['count']} error(s), last: `{le['type']}` in `{le['command']}`")
        embed = discord.Embed(
            title="⚠️ Captured Command Errors",
            description="\n".join(lines) or "None",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Use [p]shadypulse errors <CogName> for the full traceback")
        await ctx.send(embed=embed)

    @shadypulse.command(name="clearerrors")
    @app_commands.describe(name="Cog name to clear (omit to clear all)")
    async def pulse_clearerrors(self, ctx: commands.Context, name: str = None):
        """Clear captured error history."""
        if name:
            if name in self.cog_errors:
                del self.cog_errors[name]
                self._last_error_alert.pop(name, None)
                await ctx.send(f"✅ Cleared errors for `{name}`.")
            else:
                await ctx.send(f"No errors recorded for `{name}`.")
            return
        self.cog_errors.clear()
        self._last_error_alert.clear()
        await ctx.send("✅ Cleared all captured errors.")

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
