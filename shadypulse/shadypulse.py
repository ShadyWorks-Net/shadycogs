"""
ShadyPulse - Comprehensive health monitoring cog.
Monitors bot status, external services, game servers, and other cogs.

Features:
- Bot health (latency, shard health)
- External API monitoring (HTTP endpoints)
- Game server integration (with ShadyStatus)
- Cog health monitoring with auto-reload
- Dashboard command
- Uptime statistics
- Alert channels
"""

import discord
from discord import app_commands
import asyncio
import aiohttp
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from enum import Enum

from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.shadycogs.shadypulse")


class ServiceStatus(Enum):
    """Service status states."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class CheckType(Enum):
    """Types of health checks."""
    HTTP = "http"
    TCP = "tcp"
    COG = "cog"
    GAME_SERVER = "game_server"


STATUS_EMOJIS = {
    ServiceStatus.ONLINE: "🟢",
    ServiceStatus.OFFLINE: "🔴",
    ServiceStatus.DEGRADED: "🟡",
    ServiceStatus.UNKNOWN: "⚪",
}

STATUS_COLORS = {
    ServiceStatus.ONLINE: discord.Color.green(),
    ServiceStatus.OFFLINE: discord.Color.red(),
    ServiceStatus.DEGRADED: discord.Color.orange(),
    ServiceStatus.UNKNOWN: discord.Color.light_grey(),
}


class ShadyPulse(commands.Cog):
    """Comprehensive health monitoring for bot, services, and cogs."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567896, force_registration=True)

        default_global = {
            "enabled": True,
            "check_interval_seconds": 60,
            "alert_channel": None,
            "admin_dm_alerts": False,
            "admin_user_id": None,
            # HTTP endpoints to monitor
            "http_services": {},
            # Cogs to monitor
            "monitored_cogs": {},
            # Cog auto-reload settings
            "cog_auto_reload": False,
            "cog_max_retries": 3,
            "cog_retry_cooldown_seconds": 60,
            # History/stats
            "uptime_history": [],  # List of status snapshots
            "max_history_entries": 1440,  # 24 hours at 1-minute intervals
        }

        # Service config structure:
        # http_services: {
        #     "name": {
        #         "url": "https://...",
        #         "method": "GET",
        #         "expected_status": 200,
        #         "timeout": 10,
        #         "headers": {},
        #         "enabled": True,
        #     }
        # }

        # monitored_cogs: {
        #     "CogName": {
        #         "enabled": True,
        #         "extension_name": "mycogs.cogname",  # Required for auto-reload
        #         "last_status": "online",
        #         "consecutive_failures": 0,
        #         "reload_attempts": 0,
        #         "last_reload_attempt": None,
        #     }
        # }

        self.config.register_global(**default_global)

        # Runtime status tracking
        self.service_status: Dict[str, Dict[str, Any]] = {}
        self.monitor_task: Optional[asyncio.Task] = None
        self.session: Optional[aiohttp.ClientSession] = None

        # Bot start time for uptime calculation
        self.start_time = datetime.now(timezone.utc)

    async def cog_load(self) -> None:
        """Start monitoring tasks."""
        self.session = aiohttp.ClientSession()
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        log.info("ShadyPulse loaded, monitoring started")

    async def cog_unload(self) -> None:
        """Cleanup on unload."""
        if self.monitor_task:
            self.monitor_task.cancel()
        if self.session:
            await self.session.close()

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        await self.bot.wait_until_ready()

        while True:
            try:
                if await self.config.enabled():
                    await self._run_all_checks()

                interval = await self.config.check_interval_seconds()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(60)

    async def _run_all_checks(self) -> None:
        """Run all configured health checks."""
        results = {}

        # Check bot health
        results["bot"] = await self._check_bot_health()

        # Check HTTP services
        http_services = await self.config.http_services()
        for name, config in http_services.items():
            if config.get("enabled", True):
                results[f"http:{name}"] = await self._check_http_service(name, config)

        # Check cogs
        monitored_cogs = await self.config.monitored_cogs()
        for cog_name, config in monitored_cogs.items():
            if config.get("enabled", True):
                result = await self._check_cog(cog_name, config)
                results[f"cog:{cog_name}"] = result

                # Handle auto-reload if needed
                if result["status"] == ServiceStatus.OFFLINE.value:
                    await self._handle_cog_failure(cog_name, config)
                else:
                    # Reset failure tracking on success
                    async with self.config.monitored_cogs() as cogs:
                        if cog_name in cogs:
                            cogs[cog_name]["consecutive_failures"] = 0
                            cogs[cog_name]["reload_attempts"] = 0

        # Store status snapshot
        await self._record_status_snapshot(results)

        # Check for alerts
        await self._check_for_alerts(results)

        self.service_status = results

    async def _check_bot_health(self) -> Dict[str, Any]:
        """Check bot's own health."""
        latency = self.bot.latency * 1000  # Convert to ms

        # Determine status based on latency
        if latency < 100:
            status = ServiceStatus.ONLINE
        elif latency < 500:
            status = ServiceStatus.DEGRADED
        else:
            status = ServiceStatus.OFFLINE

        uptime = datetime.now(timezone.utc) - self.start_time

        return {
            "status": status.value,
            "latency_ms": round(latency, 2),
            "uptime_seconds": int(uptime.total_seconds()),
            "guilds": len(self.bot.guilds),
            "users": sum(g.member_count or 0 for g in self.bot.guilds),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _check_http_service(self, name: str, config: Dict) -> Dict[str, Any]:
        """Check an HTTP endpoint."""
        url = config.get("url")
        method = config.get("method", "GET").upper()
        expected_status = config.get("expected_status", 200)
        timeout = config.get("timeout", 10)
        headers = config.get("headers", {})

        start_time = time.time()

        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                ssl=False  # Allow self-signed certs
            ) as response:
                elapsed = (time.time() - start_time) * 1000

                if response.status == expected_status:
                    status = ServiceStatus.ONLINE
                else:
                    status = ServiceStatus.DEGRADED

                return {
                    "status": status.value,
                    "response_time_ms": round(elapsed, 2),
                    "status_code": response.status,
                    "expected_status": expected_status,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }

        except asyncio.TimeoutError:
            return {
                "status": ServiceStatus.OFFLINE.value,
                "error": "Timeout",
                "timeout": timeout,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {
                "status": ServiceStatus.OFFLINE.value,
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def _check_cog(self, cog_name: str, config: Dict) -> Dict[str, Any]:
        """Check if a cog is loaded."""
        cog = self.bot.get_cog(cog_name)

        if cog is not None:
            return {
                "status": ServiceStatus.ONLINE.value,
                "loaded": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return {
                "status": ServiceStatus.OFFLINE.value,
                "loaded": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    def _find_extension_for_cog(self, cog_name: str) -> Optional[str]:
        """Find the extension name that provides a given cog.

        Searches through loaded extensions to find which one registered
        the cog with the given name.
        """
        for ext_name, ext_module in self.bot.extensions.items():
            # Check if this extension has the cog
            if hasattr(ext_module, "setup"):
                # The cog might be registered under this extension
                # We can check by looking at the cog's module
                cog = self.bot.get_cog(cog_name)
                if cog is not None:
                    cog_module = type(cog).__module__
                    # Extension names often match module paths
                    if ext_name in cog_module or cog_module.startswith(ext_name.rsplit(".", 1)[0]):
                        return ext_name
        return None

    async def _handle_cog_failure(self, cog_name: str, config: Dict) -> None:
        """Handle a cog that failed the health check.

        Tracks consecutive failures and attempts auto-reload if enabled.
        """
        auto_reload = await self.config.cog_auto_reload()
        max_retries = await self.config.cog_max_retries()
        cooldown = await self.config.cog_retry_cooldown_seconds()

        async with self.config.monitored_cogs() as cogs:
            if cog_name not in cogs:
                return

            cog_data = cogs[cog_name]
            cog_data["consecutive_failures"] = cog_data.get("consecutive_failures", 0) + 1
            failures = cog_data["consecutive_failures"]

            # If auto-reload is disabled, just alert
            if not auto_reload:
                if failures == 1 or failures % 5 == 0:
                    await self._send_alert(
                        f"⚠️ **Cog Offline**\n"
                        f"Cog `{cog_name}` is not loaded.\n"
                        f"Consecutive failures: {failures}\n"
                        f"Auto-reload is disabled."
                    )
                    log.warning(f"Cog {cog_name} not loaded (failure #{failures})")
                cogs[cog_name] = cog_data
                return

            # Get extension name from config or try to find it
            extension_name = cog_data.get("extension_name")
            if not extension_name:
                log.warning(f"No extension_name configured for cog {cog_name}, cannot auto-reload")
                if failures == 1:
                    await self._send_alert(
                        f"⚠️ **Cog Offline**\n"
                        f"Cog `{cog_name}` is not loaded.\n"
                        f"No extension name configured - cannot auto-reload.\n"
                        f"Use `/shadypulse cog setextension` to enable auto-reload."
                    )
                cogs[cog_name] = cog_data
                return

            reload_attempts = cog_data.get("reload_attempts", 0)
            last_attempt = cog_data.get("last_reload_attempt")

            # Check cooldown
            if last_attempt:
                try:
                    last_time = datetime.fromisoformat(last_attempt)
                    if datetime.now(timezone.utc) - last_time < timedelta(seconds=cooldown):
                        cogs[cog_name] = cog_data
                        return
                except ValueError:
                    pass  # Invalid timestamp, proceed with reload

            # Check max retries
            if reload_attempts >= max_retries:
                if failures == reload_attempts + 1:  # Alert once when retries exhausted
                    await self._send_alert(
                        f"🔴 **Cog Reload Failed**\n"
                        f"Cog `{cog_name}` failed to reload after {max_retries} attempts.\n"
                        f"Manual intervention required.\n"
                        f"Use `[p]load {extension_name}` or bot restart to manually reload."
                    )
                cogs[cog_name] = cog_data
                return

            # Attempt reload
            cog_data["reload_attempts"] = reload_attempts + 1
            cog_data["last_reload_attempt"] = datetime.now(timezone.utc).isoformat()

            try:
                log.info(f"Attempting to reload cog {cog_name} via extension {extension_name} (attempt {reload_attempts + 1}/{max_retries})")

                # Try to reload the extension
                try:
                    await self.bot.reload_extension(extension_name)
                    reload_success = True
                except Exception:
                    # If reload fails, try unload then load
                    try:
                        await self.bot.unload_extension(extension_name)
                    except Exception:
                        pass  # May already be unloaded
                    await self.bot.load_extension(extension_name)
                    reload_success = True

                # Check if cog is now loaded
                if self.bot.get_cog(cog_name) is not None:
                    await self._send_alert(
                        f"🟢 **Cog Reloaded**\n"
                        f"Successfully reloaded `{cog_name}` (attempt {reload_attempts + 1}/{max_retries})"
                    )
                    log.info(f"Successfully reloaded cog {cog_name}")
                    # Reset counters on success
                    cog_data["consecutive_failures"] = 0
                    cog_data["reload_attempts"] = 0
                else:
                    await self._send_alert(
                        f"🟡 **Cog Reload Partial**\n"
                        f"Extension `{extension_name}` reloaded but cog `{cog_name}` not found.\n"
                        f"Attempt {reload_attempts + 1}/{max_retries}"
                    )

            except Exception as e:
                log.error(f"Failed to reload cog {cog_name}: {e}")
                await self._send_alert(
                    f"🔴 **Cog Reload Error**\n"
                    f"Failed to reload `{cog_name}`: {str(e)[:100]}\n"
                    f"Attempt {reload_attempts + 1}/{max_retries}"
                )

            cogs[cog_name] = cog_data

    async def _record_status_snapshot(self, results: Dict[str, Any]) -> None:
        """Record a status snapshot for history."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": {k: v.get("status", "unknown") for k, v in results.items()},
        }

        async with self.config.uptime_history() as history:
            history.append(snapshot)
            max_entries = await self.config.max_history_entries()
            if len(history) > max_entries:
                # Remove oldest entries
                history[:] = history[-max_entries:]

    async def _check_for_alerts(self, results: Dict[str, Any]) -> None:
        """Check results and send alerts for state changes."""
        for service_name, result in results.items():
            previous = self.service_status.get(service_name, {})
            previous_status = previous.get("status", "unknown")
            current_status = result.get("status", "unknown")

            # Alert on status change
            if previous_status != current_status:
                if current_status == ServiceStatus.OFFLINE.value:
                    await self._send_alert(
                        f"🔴 **Service Offline**\n"
                        f"Service `{service_name}` is now offline.\n"
                        f"Error: {result.get('error', 'Unknown')}"
                    )
                elif current_status == ServiceStatus.ONLINE.value and previous_status == ServiceStatus.OFFLINE.value:
                    await self._send_alert(
                        f"🟢 **Service Online**\n"
                        f"Service `{service_name}` is back online."
                    )

    async def _send_alert(self, message: str) -> None:
        """Send an alert to configured channels/users."""
        # Send to alert channel
        channel_id = await self.config.alert_channel()
        if channel_id:
            try:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(message)
            except Exception as e:
                log.error(f"Failed to send alert to channel: {e}")

        # DM admin if configured
        if await self.config.admin_dm_alerts():
            admin_id = await self.config.admin_user_id()
            if admin_id:
                try:
                    user = await self.bot.fetch_user(admin_id)
                    await user.send(message)
                except Exception as e:
                    log.error(f"Failed to DM admin: {e}")

    def _format_uptime(self, seconds: int) -> str:
        """Format seconds into human-readable uptime."""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")

        return " ".join(parts) or "< 1m"

    # ==================== USER COMMANDS ====================

    @commands.hybrid_command(name="pulse")
    @commands.guild_only()
    @app_commands.describe()
    async def pulse_dashboard(self, ctx: commands.Context):
        """Show the health monitoring dashboard."""
        results = self.service_status

        # Determine overall status
        statuses = [r.get("status", "unknown") for r in results.values()]
        if all(s == ServiceStatus.ONLINE.value for s in statuses):
            overall = ServiceStatus.ONLINE
        elif any(s == ServiceStatus.OFFLINE.value for s in statuses):
            overall = ServiceStatus.OFFLINE
        else:
            overall = ServiceStatus.DEGRADED

        embed = discord.Embed(
            title=f"{STATUS_EMOJIS[overall]} System Health Dashboard",
            color=STATUS_COLORS[overall],
            timestamp=datetime.now(timezone.utc),
        )

        # Bot status
        bot_status = results.get("bot", {})
        if bot_status:
            latency = bot_status.get("latency_ms", 0)
            uptime = self._format_uptime(bot_status.get("uptime_seconds", 0))
            guilds = bot_status.get("guilds", 0)

            embed.add_field(
                name="🤖 Bot Status",
                value=(
                    f"Latency: **{latency:.0f}ms**\n"
                    f"Uptime: **{uptime}**\n"
                    f"Guilds: **{guilds}**"
                ),
                inline=True,
            )

        # HTTP Services
        http_status_lines = []
        for name, result in results.items():
            if name.startswith("http:"):
                service_name = name[5:]
                status = ServiceStatus(result.get("status", "unknown"))
                emoji = STATUS_EMOJIS[status]
                response_time = result.get("response_time_ms", "N/A")
                if isinstance(response_time, (int, float)):
                    http_status_lines.append(f"{emoji} {service_name}: {response_time:.0f}ms")
                else:
                    http_status_lines.append(f"{emoji} {service_name}: {result.get('error', 'Error')}")

        if http_status_lines:
            embed.add_field(
                name="🌐 HTTP Services",
                value="\n".join(http_status_lines) or "None configured",
                inline=True,
            )

        # Cog status
        cog_status_lines = []
        for name, result in results.items():
            if name.startswith("cog:"):
                cog_name = name[4:]
                status = ServiceStatus(result.get("status", "unknown"))
                emoji = STATUS_EMOJIS[status]
                cog_status_lines.append(f"{emoji} {cog_name}")

        if cog_status_lines:
            embed.add_field(
                name="🔌 Cog Health",
                value="\n".join(cog_status_lines) or "None monitored",
                inline=True,
            )

        # Calculate uptime percentage from history
        history = await self.config.uptime_history()
        if history:
            total_checks = len(history)
            online_checks = sum(
                1 for h in history
                if all(s == "online" for s in h.get("services", {}).values())
            )
            uptime_pct = (online_checks / total_checks) * 100 if total_checks > 0 else 100
            embed.set_footer(text=f"Overall Uptime: {uptime_pct:.1f}% (last {total_checks} checks)")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="uptime")
    @commands.guild_only()
    async def show_uptime(self, ctx: commands.Context):
        """Show bot uptime and statistics."""
        bot_status = self.service_status.get("bot", {})
        uptime_seconds = bot_status.get("uptime_seconds", 0)

        if uptime_seconds == 0:
            uptime_seconds = int((datetime.now(timezone.utc) - self.start_time).total_seconds())

        uptime_str = self._format_uptime(uptime_seconds)

        embed = discord.Embed(
            title="🕐 Bot Uptime",
            color=discord.Color.green(),
        )
        embed.add_field(name="Current Uptime", value=f"**{uptime_str}**", inline=False)
        embed.add_field(
            name="Started",
            value=f"<t:{int(self.start_time.timestamp())}:F>",
            inline=True
        )
        embed.add_field(
            name="Latency",
            value=f"**{self.bot.latency * 1000:.0f}ms**",
            inline=True
        )

        await ctx.send(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @commands.hybrid_group(name="shadypulse", aliases=["sp"])
    @commands.is_owner()
    @app_commands.default_permissions(administrator=True)
    async def shadypulse(self, ctx: commands.Context):
        """Manage health monitoring."""
        pass

    @shadypulse.command(name="enable")
    @app_commands.describe(enabled="Enable or disable monitoring")
    async def pulse_enable(self, ctx: commands.Context, enabled: bool = True):
        """Enable or disable monitoring."""
        await self.config.enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Health monitoring {status}.")

    @shadypulse.command(name="interval")
    @app_commands.describe(seconds="Check interval in seconds (30-600)")
    async def pulse_interval(self, ctx: commands.Context, seconds: int):
        """Set the check interval in seconds (30-600)."""
        if seconds < 30 or seconds > 600:
            await ctx.send("❌ Interval must be between 30 and 600 seconds.")
            return

        await self.config.check_interval_seconds.set(seconds)
        await ctx.send(f"✅ Check interval set to {seconds} seconds.")

    @shadypulse.command(name="alertchannel")
    @app_commands.describe(channel="Channel for alerts (leave empty to disable)")
    async def pulse_alert_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the alert channel (leave empty to disable)."""
        if channel:
            await self.config.alert_channel.set(channel.id)
            await ctx.send(f"✅ Alerts will be sent to {channel.mention}.")
        else:
            await self.config.alert_channel.set(None)
            await ctx.send("✅ Alert channel disabled.")

    @shadypulse.command(name="admin")
    @app_commands.describe(user="User to DM alerts to (leave empty to disable)")
    async def pulse_admin(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Set the admin user for DM alerts (leave empty to disable)."""
        if user:
            await self.config.admin_user_id.set(user.id)
            await self.config.admin_dm_alerts.set(True)
            await ctx.send(f"✅ DM alerts will be sent to {user.mention}.")
        else:
            await self.config.admin_user_id.set(None)
            await self.config.admin_dm_alerts.set(False)
            await ctx.send("✅ Admin DM alerts disabled.")

    # HTTP service management
    @shadypulse.group(name="http")
    async def pulse_http(self, ctx: commands.Context):
        """Manage HTTP service monitoring."""
        pass

    @pulse_http.command(name="add")
    @app_commands.describe(
        name="Friendly name for this service",
        url="URL to monitor",
        expected_status="Expected HTTP status code (default: 200)",
        timeout="Request timeout in seconds (default: 10)"
    )
    async def http_add(
        self,
        ctx: commands.Context,
        name: str,
        url: str,
        expected_status: int = 200,
        timeout: int = 10
    ):
        """Add an HTTP endpoint to monitor."""
        async with self.config.http_services() as services:
            services[name] = {
                "url": url,
                "method": "GET",
                "expected_status": expected_status,
                "timeout": timeout,
                "headers": {},
                "enabled": True,
            }

        await ctx.send(f"✅ Added HTTP service `{name}` monitoring `{url}`.")

    @pulse_http.command(name="remove")
    @app_commands.describe(name="Name of the service to remove")
    async def http_remove(self, ctx: commands.Context, name: str):
        """Remove an HTTP endpoint from monitoring."""
        async with self.config.http_services() as services:
            if name in services:
                del services[name]
                await ctx.send(f"✅ Removed HTTP service `{name}`.")
            else:
                await ctx.send(f"❌ Service `{name}` not found.")

    @pulse_http.command(name="list")
    async def http_list(self, ctx: commands.Context):
        """List monitored HTTP endpoints."""
        services = await self.config.http_services()

        if not services:
            await ctx.send("📋 No HTTP services configured.")
            return

        embed = discord.Embed(title="🌐 Monitored HTTP Services", color=discord.Color.blue())

        for name, config in services.items():
            status = "✅ Enabled" if config.get("enabled", True) else "❌ Disabled"
            embed.add_field(
                name=name,
                value=f"URL: `{config['url']}`\nExpected: {config['expected_status']}\nTimeout: {config['timeout']}s\n{status}",
                inline=False
            )

        await ctx.send(embed=embed)

    # Cog monitoring
    @shadypulse.group(name="cog")
    async def pulse_cog(self, ctx: commands.Context):
        """Manage cog health monitoring."""
        pass

    @pulse_cog.command(name="add")
    @app_commands.describe(
        cog_name="Cog class name (e.g., 'LevelUp', 'ShadyStatus')",
        extension_name="Extension path for auto-reload (e.g., 'mycogs.levelup')"
    )
    async def cog_add(self, ctx: commands.Context, cog_name: str, extension_name: Optional[str] = None):
        """Add a cog to monitor."""
        # Try to auto-detect extension name if not provided and cog is loaded
        if extension_name is None and self.bot.get_cog(cog_name):
            extension_name = self._find_extension_for_cog(cog_name)
            if extension_name:
                await ctx.send(f"ℹ️ Auto-detected extension: `{extension_name}`")

        async with self.config.monitored_cogs() as cogs:
            cogs[cog_name] = {
                "enabled": True,
                "extension_name": extension_name,
                "last_status": "unknown",
                "consecutive_failures": 0,
                "reload_attempts": 0,
                "last_reload_attempt": None,
            }

        if extension_name:
            await ctx.send(f"✅ Now monitoring cog `{cog_name}` (extension: `{extension_name}`).")
        else:
            await ctx.send(
                f"✅ Now monitoring cog `{cog_name}`.\n"
                f"⚠️ No extension name provided - auto-reload will not work.\n"
                f"Use `/shadypulse cog setextension` to enable auto-reload."
            )

    @pulse_cog.command(name="remove")
    @app_commands.describe(cog_name="Name of the cog to stop monitoring")
    async def cog_remove(self, ctx: commands.Context, cog_name: str):
        """Remove a cog from monitoring."""
        async with self.config.monitored_cogs() as cogs:
            if cog_name in cogs:
                del cogs[cog_name]
                await ctx.send(f"✅ Stopped monitoring cog `{cog_name}`.")
            else:
                await ctx.send(f"❌ Cog `{cog_name}` not monitored.")

    @pulse_cog.command(name="setextension")
    @app_commands.describe(
        cog_name="Cog class name being monitored",
        extension_name="Extension path (e.g., 'mycogs.levelup')"
    )
    async def cog_set_extension(self, ctx: commands.Context, cog_name: str, extension_name: str):
        """Set the extension name for a monitored cog (required for auto-reload)."""
        async with self.config.monitored_cogs() as cogs:
            if cog_name not in cogs:
                await ctx.send(f"❌ Cog `{cog_name}` is not being monitored.")
                return

            cogs[cog_name]["extension_name"] = extension_name
            await ctx.send(f"✅ Set extension for `{cog_name}` to `{extension_name}`.")

    @pulse_cog.command(name="reset")
    @app_commands.describe(cog_name="Cog to reset failure counters for")
    async def cog_reset(self, ctx: commands.Context, cog_name: str):
        """Reset failure counters for a monitored cog after manual intervention."""
        async with self.config.monitored_cogs() as cogs:
            if cog_name not in cogs:
                await ctx.send(f"❌ Cog `{cog_name}` is not being monitored.")
                return

            cogs[cog_name]["consecutive_failures"] = 0
            cogs[cog_name]["reload_attempts"] = 0
            cogs[cog_name]["last_reload_attempt"] = None
            await ctx.send(f"✅ Reset failure counters for `{cog_name}`.")

    @pulse_cog.command(name="autoreload")
    @app_commands.describe(
        enabled="Enable or disable auto-reload",
        max_retries="Maximum reload attempts before giving up (1-10)",
        cooldown="Seconds between reload attempts (30-600)"
    )
    async def cog_autoreload(self, ctx: commands.Context, enabled: bool, max_retries: int = 3, cooldown: int = 60):
        """Configure auto-reload for failed cogs."""
        if max_retries < 1 or max_retries > 10:
            await ctx.send("❌ Max retries must be between 1 and 10.")
            return
        if cooldown < 30 or cooldown > 600:
            await ctx.send("❌ Cooldown must be between 30 and 600 seconds.")
            return

        await self.config.cog_auto_reload.set(enabled)
        await self.config.cog_max_retries.set(max_retries)
        await self.config.cog_retry_cooldown_seconds.set(cooldown)

        if enabled:
            await ctx.send(
                f"✅ Cog auto-reload **enabled**.\n"
                f"Max retries: {max_retries}\n"
                f"Cooldown: {cooldown}s between attempts\n\n"
                f"Note: Cogs need an `extension_name` configured to be auto-reloaded."
            )
        else:
            await ctx.send("✅ Cog auto-reload **disabled**.")

    @pulse_cog.command(name="list")
    async def cog_list(self, ctx: commands.Context):
        """List monitored cogs."""
        cogs = await self.config.monitored_cogs()
        auto_reload = await self.config.cog_auto_reload()

        if not cogs:
            await ctx.send("📋 No cogs being monitored.")
            return

        embed = discord.Embed(
            title="🔌 Monitored Cogs",
            description=f"Auto-reload: {'✅ Enabled' if auto_reload else '❌ Disabled'}",
            color=discord.Color.blue()
        )

        for name, config in cogs.items():
            loaded = self.bot.get_cog(name) is not None
            status_emoji = "🟢" if loaded else "🔴"
            enabled = "✅" if config.get("enabled", True) else "❌"
            extension = config.get("extension_name", "Not set")
            failures = config.get("consecutive_failures", 0)
            reloads = config.get("reload_attempts", 0)

            value_lines = [
                f"Loaded: {'Yes' if loaded else 'No'}",
                f"Extension: `{extension}`" if extension != "Not set" else "Extension: ⚠️ Not set",
            ]
            if failures > 0:
                value_lines.append(f"Failures: {failures}")
            if reloads > 0:
                value_lines.append(f"Reload attempts: {reloads}")

            embed.add_field(
                name=f"{status_emoji} {name} {enabled}",
                value="\n".join(value_lines),
                inline=True
            )

        await ctx.send(embed=embed)

    @shadypulse.command(name="status")
    async def pulse_status(self, ctx: commands.Context):
        """Show detailed monitoring configuration."""
        enabled = await self.config.enabled()
        interval = await self.config.check_interval_seconds()
        alert_channel = await self.config.alert_channel()
        http_services = await self.config.http_services()
        monitored_cogs = await self.config.monitored_cogs()
        auto_reload = await self.config.cog_auto_reload()
        max_retries = await self.config.cog_max_retries()
        cooldown = await self.config.cog_retry_cooldown_seconds()

        embed = discord.Embed(
            title="⚙️ ShadyPulse Configuration",
            color=discord.Color.green() if enabled else discord.Color.red()
        )

        embed.add_field(name="Monitoring", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Check Interval", value=f"{interval}s", inline=True)
        embed.add_field(
            name="Alert Channel",
            value=f"<#{alert_channel}>" if alert_channel else "Not set",
            inline=True
        )
        embed.add_field(name="HTTP Services", value=str(len(http_services)), inline=True)
        embed.add_field(name="Monitored Cogs", value=str(len(monitored_cogs)), inline=True)

        if auto_reload:
            embed.add_field(
                name="Cog Auto-Reload",
                value=f"✅ Enabled\nRetries: {max_retries}\nCooldown: {cooldown}s",
                inline=True
            )
        else:
            embed.add_field(name="Cog Auto-Reload", value="❌ Disabled", inline=True)

        await ctx.send(embed=embed)

    @shadypulse.command(name="history")
    @app_commands.describe(entries="Number of history entries to show (default: 10)")
    async def pulse_history(self, ctx: commands.Context, entries: int = 10):
        """Show recent status history."""
        history = await self.config.uptime_history()

        if not history:
            await ctx.send("📊 No history recorded yet.")
            return

        recent = history[-entries:]

        embed = discord.Embed(title="📊 Status History", color=discord.Color.blue())

        for snapshot in reversed(recent):
            timestamp = datetime.fromisoformat(snapshot["timestamp"])
            services = snapshot.get("services", {})

            all_online = all(s == "online" for s in services.values())
            emoji = "🟢" if all_online else "🔴"

            offline_services = [k for k, v in services.items() if v != "online"]
            if offline_services:
                status_str = f"Offline: {', '.join(offline_services)}"
            else:
                status_str = "All services online"

            embed.add_field(
                name=f"{emoji} <t:{int(timestamp.timestamp())}:t>",
                value=status_str,
                inline=False
            )

        await ctx.send(embed=embed)


async def setup(bot: Red) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(ShadyPulse(bot))
