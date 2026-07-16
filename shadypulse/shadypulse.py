"""
ShadyPulse - Health monitoring cog.
Monitors bot status, HTTP services, and cog health (loaded + responding).

Everything is driven from a single interactive panel: `[p]shadypulse`.
"""
import discord
from discord import app_commands
import asyncio
import aiohttp
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, List
from enum import Enum

from redbot.core import commands, Config
from redbot.core.bot import Red

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


def build_panel_embed(config: dict) -> discord.Embed:
    """The status summary shown at the top of the control panel."""
    enabled = config["enabled"]
    e = discord.Embed(
        title="⚙️ ShadyPulse Control Panel",
        color=discord.Color.green() if enabled else discord.Color.red(),
    )
    e.add_field(name="Monitoring", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
    e.add_field(name="Interval", value=f"{config['check_interval_seconds']}s", inline=True)
    e.add_field(
        name="Alert Channel",
        value=f"<#{config['alert_channel']}>" if config["alert_channel"] else "Not set",
        inline=True,
    )
    e.add_field(name="Monitored Cogs", value=str(len(config["monitored_cogs"])), inline=True)
    e.add_field(name="HTTP Services", value=str(len(config["http_services"])), inline=True)
    auto = (
        f"✅ {config['cog_max_retries']}x / {config['cog_retry_cooldown_seconds']}s"
        if config["cog_auto_reload"] else "❌"
    )
    e.add_field(name="Auto-Reload", value=auto, inline=True)
    e.add_field(
        name="Error Alerts",
        value="✅ On" if config.get("alert_on_error", True) else "❌ Off",
        inline=True,
    )
    e.set_footer(text="Use the buttons below to configure — no commands needed.")
    return e


# ==================== UI: MODALS ====================


class SettingsModal(discord.ui.Modal, title="ShadyPulse Settings"):
    interval = discord.ui.TextInput(label="Check Interval (sec, 30-600)", max_length=4)
    max_retries = discord.ui.TextInput(label="Reload Max Retries (1-10)", max_length=2)
    cooldown = discord.ui.TextInput(label="Reload Cooldown (sec, 30-600)", max_length=4)
    error_window = discord.ui.TextInput(label="Error Window (sec, 30-3600)", max_length=4)

    def __init__(self, cog: "ShadyPulse", config: dict):
        super().__init__()
        self.cog = cog
        self.interval.default = str(config.get("check_interval_seconds", 60))
        self.max_retries.default = str(config.get("cog_max_retries", 3))
        self.cooldown.default = str(config.get("cog_retry_cooldown_seconds", 60))
        self.error_window.default = str(config.get("cog_error_window_seconds", 300))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            interval = max(30, min(600, int(self.interval.value)))
            retries = max(1, min(10, int(self.max_retries.value)))
            cooldown = max(30, min(600, int(self.cooldown.value)))
            window = max(30, min(3600, int(self.error_window.value)))
        except ValueError:
            await interaction.response.send_message("❌ All values must be numbers.", ephemeral=True)
            return

        await self.cog.config.check_interval_seconds.set(interval)
        await self.cog.config.cog_max_retries.set(retries)
        await self.cog.config.cog_retry_cooldown_seconds.set(cooldown)
        await self.cog.config.cog_error_window_seconds.set(window)
        await interaction.response.send_message(
            f"✅ Settings saved.\n**Interval:** {interval}s • **Retries:** {retries} • "
            f"**Cooldown:** {cooldown}s • **Error window:** {window}s",
            ephemeral=True,
        )


class AddHttpModal(discord.ui.Modal, title="Add HTTP Service"):
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
            await interaction.response.send_message("❌ Status code and timeout must be numbers.", ephemeral=True)
            return

        async with self.cog.config.http_services() as services:
            services[self.name.value] = {
                "url": self.url.value,
                "expected_status": status_code,
                "timeout": timeout,
                "enabled": True,
            }
        await interaction.response.send_message(f"✅ Added HTTP service `{self.name.value}`.", ephemeral=True)


# ==================== UI: VIEWS ====================


class _OwnerView(discord.ui.View):
    """Base view that restricts every component to the bot owner."""

    def __init__(self, cog: "ShadyPulse", timeout: float = 300):
        super().__init__(timeout=timeout)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message("This panel is owner-only.", ephemeral=True)
        return False


class CogManageView(_OwnerView):
    """Add cogs to monitor from a dropdown of the bot's loaded cogs, and remove
    monitored ones. Extension paths are resolved automatically."""

    def __init__(self, cog: "ShadyPulse"):
        super().__init__(cog)
        self.page = 0
        self.addable: List[str] = []
        self.monitored: Dict[str, dict] = {}

    async def refresh(self):
        self.monitored = await self.cog.config.monitored_cogs()
        loaded = [n for n in sorted(self.cog.bot.cogs.keys()) if n != "ShadyPulse"]
        self.addable = [n for n in loaded if n not in self.monitored]
        pages = max(1, (len(self.addable) + 24) // 25)
        self.page = max(0, min(self.page, pages - 1))
        self._build()

    def _build(self):
        self.clear_items()

        page_opts = self.addable[self.page * 25:(self.page + 1) * 25]
        if page_opts:
            add_sel = discord.ui.Select(
                placeholder=f"➕ Add loaded cogs to monitor (page {self.page + 1})",
                min_values=0,
                max_values=len(page_opts),
                options=[discord.SelectOption(label=n[:100], value=n) for n in page_opts],
            )
            add_sel.callback = self._on_add
            self.add_item(add_sel)

        if self.monitored:
            mon = list(self.monitored.keys())[:25]
            rem_sel = discord.ui.Select(
                placeholder="➖ Remove cogs from monitoring",
                min_values=0,
                max_values=len(mon),
                options=[discord.SelectOption(label=n[:100], value=n) for n in mon],
            )
            rem_sel.callback = self._on_remove
            self.add_item(rem_sel)

        if len(self.addable) > 25:
            prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
            prev.callback = self._prev
            self.add_item(prev)
            nxt = discord.ui.Button(
                label="▶", style=discord.ButtonStyle.secondary,
                disabled=(self.page + 1) * 25 >= len(self.addable),
            )
            nxt.callback = self._next
            self.add_item(nxt)

    def embed(self) -> discord.Embed:
        e = discord.Embed(title="🔌 Manage Monitored Cogs", color=discord.Color.blurple())
        if self.monitored:
            lines = []
            for n, c in self.monitored.items():
                ext = c.get("extension_name")
                lines.append(f"• `{n}` — {'🔄 auto-reload' if ext else '⚠️ no extension (no reload)'}")
            e.add_field(name="Currently monitored", value="\n".join(lines)[:1024], inline=False)
        else:
            e.add_field(name="Currently monitored", value="None yet", inline=False)
        e.set_footer(text=f"{len(self.addable)} loaded cog(s) available to add")
        return e

    async def _on_add(self, interaction: discord.Interaction):
        selected = interaction.data.get("values", [])
        for name in selected:
            ext = self.cog._extension_for_cog(name)
            async with self.cog.config.monitored_cogs() as cogs:
                cogs[name] = {"enabled": True, "extension_name": ext, "reload_attempts": 0}
        await self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _on_remove(self, interaction: discord.Interaction):
        for name in interaction.data.get("values", []):
            async with self.cog.config.monitored_cogs() as cogs:
                cogs.pop(name, None)
            self.cog.cog_errors.pop(name, None)
        await self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class HttpManageView(_OwnerView):
    def __init__(self, cog: "ShadyPulse"):
        super().__init__(cog)
        self.services: Dict[str, dict] = {}

    async def refresh(self):
        self.services = await self.cog.config.http_services()
        self._build()

    def _build(self):
        self.clear_items()
        add_btn = discord.ui.Button(label="Add HTTP Service", style=discord.ButtonStyle.success, emoji="➕")
        add_btn.callback = self._add
        self.add_item(add_btn)
        if self.services:
            names = list(self.services.keys())[:25]
            sel = discord.ui.Select(
                placeholder="➖ Remove HTTP service",
                min_values=0,
                max_values=len(names),
                options=[discord.SelectOption(label=n[:100], value=n) for n in names],
            )
            sel.callback = self._remove
            self.add_item(sel)

    def embed(self) -> discord.Embed:
        e = discord.Embed(title="🌐 Manage HTTP Services", color=discord.Color.blurple())
        if self.services:
            value = "\n".join(f"• `{n}`: {c['url']}" for n, c in self.services.items())
            e.add_field(name="Services", value=value[:1024], inline=False)
        else:
            e.add_field(name="Services", value="None", inline=False)
        return e

    async def _add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddHttpModal(self.cog))

    async def _remove(self, interaction: discord.Interaction):
        for name in interaction.data.get("values", []):
            async with self.cog.config.http_services() as s:
                s.pop(name, None)
        await self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class AlertsView(_OwnerView):
    def __init__(self, cog: "ShadyPulse"):
        super().__init__(cog)
        chan = discord.ui.ChannelSelect(
            placeholder="Set alert channel (deselect to disable)",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
        )
        chan.callback = self._on_channel
        self.add_item(chan)

    async def embed(self) -> discord.Embed:
        c = await self.cog.config.all()
        e = discord.Embed(title="🔔 Alerts", color=discord.Color.blurple())
        e.add_field(
            name="Alert Channel",
            value=f"<#{c['alert_channel']}>" if c["alert_channel"] else "Not set",
            inline=True,
        )
        e.add_field(
            name="Error Alerts",
            value="✅ On" if c.get("alert_on_error", True) else "❌ Off",
            inline=True,
        )
        e.set_footer(text="Status changes and command errors post here.")
        return e

    async def _on_channel(self, interaction: discord.Interaction):
        vals = interaction.data.get("values", [])
        await self.cog.config.alert_channel.set(int(vals[0]) if vals else None)
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @discord.ui.button(label="Toggle Error Alerts", style=discord.ButtonStyle.primary, emoji="⚠️")
    async def toggle_err(self, interaction: discord.Interaction, button: discord.ui.Button):
        new = not await self.cog.config.alert_on_error()
        await self.cog.config.alert_on_error.set(new)
        await interaction.response.edit_message(embed=await self.embed(), view=self)


class ErrorsView(_OwnerView):
    def __init__(self, cog: "ShadyPulse"):
        super().__init__(cog)
        self._build()

    def _build(self):
        self.clear_items()
        keys = list(self.cog.cog_errors.keys())[:25]
        if keys:
            sel = discord.ui.Select(
                placeholder="View traceback for…",
                min_values=0,
                max_values=1,
                options=[discord.SelectOption(label=k[:100], value=k) for k in keys],
            )
            sel.callback = self._view
            self.add_item(sel)
            clr = discord.ui.Button(label="Clear All", style=discord.ButtonStyle.danger, emoji="🗑️")
            clr.callback = self._clear
            self.add_item(clr)

    def embed(self) -> discord.Embed:
        e = discord.Embed(title="⚠️ Captured Command Errors", color=discord.Color.orange())
        if not self.cog.cog_errors:
            e.description = "No command errors captured since last reload. ✅"
            return e
        lines = []
        for k, rec in self.cog.cog_errors.items():
            le = rec.get("last_error")
            if le:
                lines.append(f"• `{k}` — {rec['count']} error(s), last `{le['type']}` in `{le['command']}`")
        e.description = "\n".join(lines)[:4000]
        return e

    async def _view(self, interaction: discord.Interaction):
        vals = interaction.data.get("values", [])
        if not vals:
            await interaction.response.defer()
            return
        name = vals[0]
        rec = self.cog.cog_errors.get(name)
        if not rec or not rec.get("last_error"):
            await interaction.response.send_message(f"No errors for `{name}`.", ephemeral=True)
            return
        e = rec["last_error"]
        emb = discord.Embed(
            title=f"⚠️ {name} — last error",
            color=discord.Color.orange(),
            timestamp=datetime.fromisoformat(e["timestamp"]),
        )
        emb.add_field(name="Command", value=f"`{e['command']}`", inline=True)
        emb.add_field(name="Type", value=f"`{e['type']}`", inline=True)
        emb.add_field(name="Count", value=str(rec["count"]), inline=True)
        emb.add_field(name="Message", value=f"```{e['message'][:400]}```", inline=False)
        tb = e["traceback"]
        if len(tb) > 1400:
            tb = "...\n" + tb[-1400:]
        emb.add_field(name="Traceback", value=f"```py\n{tb}```", inline=False)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    async def _clear(self, interaction: discord.Interaction):
        self.cog.cog_errors.clear()
        self.cog._last_error_alert.clear()
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class PanelView(_OwnerView):
    """The single control panel — every setting lives behind these buttons."""

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.primary, emoji="⚙️", row=0)
    async def settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.all()
        await interaction.response.send_modal(SettingsModal(self.cog, config))

    @discord.ui.button(label="Cogs", style=discord.ButtonStyle.secondary, emoji="🔌", row=0)
    async def cogs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CogManageView(self.cog)
        await view.refresh()
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="HTTP", style=discord.ButtonStyle.secondary, emoji="🌐", row=0)
    async def http_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HttpManageView(self.cog)
        await view.refresh()
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Alerts", style=discord.ButtonStyle.secondary, emoji="🔔", row=0)
    async def alerts_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = AlertsView(self.cog)
        await interaction.response.send_message(embed=await view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Errors", style=discord.ButtonStyle.secondary, emoji="⚠️", row=0)
    async def errors_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ErrorsView(self.cog)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Toggle Monitoring", style=discord.ButtonStyle.success, emoji="▶️", row=1)
    async def toggle_monitoring(self, interaction: discord.Interaction, button: discord.ui.Button):
        new = not await self.cog.config.enabled()
        await self.cog.config.enabled.set(new)
        config = await self.cog.config.all()
        await interaction.response.edit_message(embed=build_panel_embed(config), view=self)

    @discord.ui.button(label="Toggle Auto-Reload", style=discord.ButtonStyle.success, emoji="🔄", row=1)
    async def toggle_autoreload(self, interaction: discord.Interaction, button: discord.ui.Button):
        new = not await self.cog.config.cog_auto_reload()
        await self.cog.config.cog_auto_reload.set(new)
        config = await self.cog.config.all()
        await interaction.response.edit_message(embed=build_panel_embed(config), view=self)


# ==================== MAIN COG ====================


class ShadyPulse(commands.Cog):
    """Health monitoring for bot, services, and cogs."""

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

    def _extension_for_cog(self, cog_name: str) -> Optional[str]:
        """Resolve the extension (load name) a cog was loaded from, so we can
        reload it. Matches the cog's module against the bot's loaded extensions."""
        cog = self.bot.get_cog(cog_name)
        if cog is None:
            return None
        mod = type(cog).__module__ or ""
        for ext_name in self.bot.extensions:
            if mod == ext_name or mod.startswith(ext_name + "."):
                return ext_name
        return mod.split(".")[0] if mod else None

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

    # ==================== MONITOR LOOP ====================

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
                ssl=False,
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
            verb = await self._reload_or_load(ext)
            # Mark the (re)load time so pre-reload errors stop counting as recent.
            self._reloaded_at[cog_name] = now
            log.info(f"{verb} {cog_name} ({reason}), attempt {attempt}/{max_retries}")
            await self._send_alert(discord.Embed(
                title=f"🔄 {verb} {cog_name}",
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

    async def _reload_or_load(self, ext: str) -> str:
        """(Re)load a cog package the way Red's [p]load / [p]reload do.

        Red overrides load_extension to take a ModuleSpec (not a name), so we
        resolve the spec through the cog manager. Returns "Reloaded" if it was
        already loaded, "Loaded" if it was fully unloaded. Raises on failure.
        """
        was_loaded = ext in self.bot.extensions
        if was_loaded:
            await self.bot.unload_extension(ext)

        cog_mgr = getattr(self.bot, "_cog_mgr", None)
        if cog_mgr is not None:
            spec = await cog_mgr.find_cog(ext)
            if spec is None:
                raise ValueError(f"Cog package '{ext}' not found by the cog manager")
            await self.bot.load_extension(spec)
        else:
            # Non-Red fallback (discord.py's string-based loader).
            await self.bot.load_extension(ext)

        return "Reloaded" if was_loaded else "Loaded"

    def _format_uptime(self, seconds: int) -> str:
        d, h, m = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        if m:
            parts.append(f"{m}m")
        return " ".join(parts) or "< 1m"

    async def is_authorized(self, ctx: commands.Context) -> bool:
        """Check if the invoker may view health status (owner / admin / manage-guild)."""
        if await self.bot.is_owner(ctx.author):
            return True
        if not isinstance(ctx.author, discord.Member):
            return False
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
            return True
        if ctx.author.guild_permissions.manage_guild:
            return True
        return False

    # ==================== COMMANDS ====================

    @commands.hybrid_command(name="pulse", description="Health monitoring dashboard")
    @commands.guild_only()
    async def pulse_dashboard(self, ctx: commands.Context):
        """Show the health dashboard (admin / manage-guild / owner)."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to view health status.", ephemeral=True)
            return

        results = self.service_status
        statuses = [r.get("status", "unknown") for r in results.values()]
        if statuses and all(s == "online" for s in statuses):
            overall = ServiceStatus.ONLINE
        elif any(s == "offline" for s in statuses):
            overall = ServiceStatus.OFFLINE
        else:
            overall = ServiceStatus.DEGRADED

        embed = discord.Embed(
            title=f"{STATUS_EMOJI[overall]} System Health",
            color=STATUS_COLOR[overall],
            timestamp=datetime.now(timezone.utc),
        )

        bot_data = results.get("bot", {})
        uptime = self._format_uptime(int((datetime.now(timezone.utc) - self.start_time).total_seconds()))
        embed.add_field(
            name="🤖 Bot",
            value=f"Latency: **{bot_data.get('latency_ms', 0):.0f}ms**\nUptime: **{uptime}**",
            inline=True,
        )

        http_lines = []
        for name, data in results.items():
            if name.startswith("http:"):
                status = ServiceStatus(data.get("status", "unknown"))
                http_lines.append(f"{STATUS_EMOJI[status]} {name[5:]}")
        if http_lines:
            embed.add_field(name="🌐 HTTP", value="\n".join(http_lines), inline=True)

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

        if not results:
            embed.description = "No checks have run yet — give it one interval."

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="shadypulse", aliases=["sp"], description="Open the ShadyPulse control panel")
    @commands.is_owner()
    @commands.guild_only()
    async def shadypulse_panel(self, ctx: commands.Context):
        """Open the interactive control panel (Bot Owner only).

        Everything — settings, cogs, HTTP services, alerts, error history — is
        managed here. No subcommands to remember.
        """
        config = await self.config.all()
        await ctx.send(embed=build_panel_embed(config), view=PanelView(self), ephemeral=True)


async def setup(bot: Red) -> None:
    await bot.add_cog(ShadyPulse(bot))
