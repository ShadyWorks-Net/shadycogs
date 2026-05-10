"""
ShadyStatus - Multi-game server status query cog.
Queries game servers via Steam A2S protocol and displays game-specific formatted status.

Supported Games:
- 7 Days to Die
- ARK: Survival Evolved
- Rust
- Valheim
- Project Zomboid
- V Rising
"""

import discord
import logging
import asyncio
import time
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
from enum import Enum

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadystatus")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 1234567895

# Try to import python-a2s
try:
    import a2s
    A2S_AVAILABLE = True
except ImportError:
    A2S_AVAILABLE = False
    log.warning("python-a2s not installed. Install with: pip install python-a2s")


class GameType(Enum):
    """Supported game types."""
    SEVENDTD = "7dtd"
    ARK = "ark"
    RUST = "rust"
    VALHEIM = "valheim"
    PROJECTZOMBOID = "pz"
    VRISING = "vrising"
    GENERIC = "generic"


GAME_DISPLAY_NAMES = {
    GameType.SEVENDTD: "7 Days to Die",
    GameType.ARK: "ARK: Survival Evolved",
    GameType.RUST: "Rust",
    GameType.VALHEIM: "Valheim",
    GameType.PROJECTZOMBOID: "Project Zomboid",
    GameType.VRISING: "V Rising",
    GameType.GENERIC: "Game Server",
}

GAME_EMOJIS = {
    GameType.SEVENDTD: "🧟",
    GameType.ARK: "🦖",
    GameType.RUST: "🔧",
    GameType.VALHEIM: "⚔️",
    GameType.PROJECTZOMBOID: "🧟",
    GameType.VRISING: "🧛",
    GameType.GENERIC: "🎮",
}

GAME_COLORS = {
    GameType.SEVENDTD: discord.Color.dark_red(),
    GameType.ARK: discord.Color.dark_green(),
    GameType.RUST: discord.Color.orange(),
    GameType.VALHEIM: discord.Color.dark_blue(),
    GameType.PROJECTZOMBOID: discord.Color.dark_grey(),
    GameType.VRISING: discord.Color.dark_purple(),
    GameType.GENERIC: discord.Color.blurple(),
}


class ServerQueryError(Exception):
    """Exception raised when server query fails."""
    pass


async def query_server(ip: str, port: int, timeout: float = 5.0) -> Dict[str, Any]:
    """
    Query a game server using A2S protocol.

    Returns server info and rules.
    """
    if not A2S_AVAILABLE:
        raise ServerQueryError("python-a2s library not installed")

    address = (ip, port)

    try:
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()

        # Get server info
        info = await loop.run_in_executor(
            None,
            lambda: a2s.info(address, timeout=timeout)
        )

        # Get rules (may not be available on all servers)
        rules = {}
        try:
            rules_raw = await loop.run_in_executor(
                None,
                lambda: a2s.rules(address, timeout=timeout)
            )
            rules = dict(rules_raw)
        except Exception as e:
            log.debug(f"Could not fetch rules for {ip}:{port}: {e}")

        # Get players (optional)
        players = []
        try:
            players_raw = await loop.run_in_executor(
                None,
                lambda: a2s.players(address, timeout=timeout)
            )
            players = [{"name": p.name, "score": p.score, "duration": p.duration} for p in players_raw]
        except Exception as e:
            log.debug(f"Could not fetch players for {ip}:{port}: {e}")

        return {
            "server_name": info.server_name,
            "map_name": info.map_name,
            "game": info.game,
            "players": info.player_count,
            "max_players": info.max_players,
            "bots": info.bot_count,
            "password_protected": info.password_protected,
            "vac_enabled": info.vac_enabled,
            "version": info.version,
            "ping": info.ping,
            "platform": info.platform,
            "rules": rules,
            "player_list": players,
            "online": True,
        }

    except asyncio.TimeoutError:
        raise ServerQueryError("Server did not respond (timeout)")
    except ConnectionRefusedError:
        raise ServerQueryError("Connection refused")
    except Exception as e:
        raise ServerQueryError(f"Query failed: {e}")


def format_7dtd_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for 7 Days to Die server."""
    rules = data.get("rules", {})

    # Parse 7DTD-specific data
    current_time = int(rules.get("CurrentServerTime", 0))
    day = (current_time // 24000) + 1 if current_time else None
    hour = (current_time % 24000) // 1000 if current_time else None
    minute = (current_time % 1000) * 60 // 1000 if current_time else None

    bm_freq = int(rules.get("BloodMoonFrequency", 7))

    # Blood moon calculation
    days_until = None
    next_bm_day = None
    if day:
        day_in_cycle = day % bm_freq
        if day_in_cycle == 0:
            days_until = 0
            next_bm_day = day
        else:
            days_until = bm_freq - day_in_cycle
            next_bm_day = day + days_until

    # Determine color based on blood moon
    if days_until == 0:
        color = discord.Color.red()
    elif days_until == 1:
        color = discord.Color.orange()
    else:
        color = discord.Color.dark_green()

    embed = discord.Embed(
        title=f"🧟 {data['server_name']}",
        color=color,
    )

    if day:
        embed.add_field(name="📅 In-Game Day", value=f"**Day {day}**", inline=True)
    if hour is not None:
        embed.add_field(name="🕐 In-Game Time", value=f"**{hour:02d}:{minute:02d}**", inline=True)

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )

    if days_until is not None:
        if days_until == 0:
            bm_value = "🩸 **TONIGHT!** Survive the night!"
        elif days_until == 1:
            bm_value = f"⚠️ **Tomorrow** (Day {next_bm_day})"
        else:
            bm_value = f"**{days_until} days** (Day {next_bm_day})"
        embed.add_field(name="🩸 Next Blood Moon", value=bm_value, inline=False)

    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Unknown"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_ark_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for ARK: Survival Evolved server."""
    embed = discord.Embed(
        title=f"🦖 {data['server_name']}",
        color=GAME_COLORS[GameType.ARK],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )
    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Unknown"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    rules = data.get("rules", {})

    # ARK-specific rules
    if "SESSIONISPVE_i" in rules:
        pve = int(rules.get("SESSIONISPVE_i", 0))
        embed.add_field(name="⚔️ Mode", value="PvE" if pve else "PvP", inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_rust_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for Rust server."""
    rules = data.get("rules", {})

    embed = discord.Embed(
        title=f"🔧 {data['server_name']}",
        color=GAME_COLORS[GameType.RUST],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )

    # Rust-specific rules
    if "fps_avg" in rules:
        embed.add_field(name="📊 Server FPS", value=rules["fps_avg"], inline=True)

    if "world.size" in rules:
        size = int(rules.get("world.size", 0))
        embed.add_field(name="🗺️ World Size", value=f"{size}m", inline=True)

    if "world.seed" in rules:
        embed.add_field(name="🌱 Seed", value=rules["world.seed"], inline=True)

    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Procedural Map"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    if "queue" in rules:
        embed.add_field(name="📋 Queue", value=rules["queue"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_valheim_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for Valheim server."""
    embed = discord.Embed(
        title=f"⚔️ {data['server_name']}",
        color=GAME_COLORS[GameType.VALHEIM],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )
    embed.add_field(name="🗺️ World", value=data.get("map_name", "Unknown"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_pz_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for Project Zomboid server."""
    rules = data.get("rules", {})

    embed = discord.Embed(
        title=f"🧟 {data['server_name']}",
        color=GAME_COLORS[GameType.PROJECTZOMBOID],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )
    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Muldraugh, KY"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    # PZ-specific
    if "Open" in rules:
        pvp = rules.get("Open", "false").lower() == "true"
        embed.add_field(name="⚔️ PvP", value="Enabled" if pvp else "Disabled", inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_vrising_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format embed for V Rising server."""
    embed = discord.Embed(
        title=f"🧛 {data['server_name']}",
        color=GAME_COLORS[GameType.VRISING],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )
    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Vardoran"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    return embed


def format_generic_embed(data: Dict[str, Any], server_config: Dict) -> discord.Embed:
    """Format a generic embed for any A2S-compatible server."""
    embed = discord.Embed(
        title=f"🎮 {data['server_name']}",
        color=GAME_COLORS[GameType.GENERIC],
    )

    embed.add_field(
        name="👥 Players",
        value=f"**{data['players']} / {data['max_players']}**",
        inline=True
    )
    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Unknown"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    if data.get("game"):
        embed.add_field(name="🎮 Game", value=data["game"], inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)

    if data.get("password_protected"):
        embed.add_field(name="🔒 Password", value="Yes", inline=True)

    if data.get("vac_enabled"):
        embed.add_field(name="🛡️ VAC", value="Enabled", inline=True)

    return embed


# Map game types to formatters
EMBED_FORMATTERS = {
    GameType.SEVENDTD: format_7dtd_embed,
    GameType.ARK: format_ark_embed,
    GameType.RUST: format_rust_embed,
    GameType.VALHEIM: format_valheim_embed,
    GameType.PROJECTZOMBOID: format_pz_embed,
    GameType.VRISING: format_vrising_embed,
    GameType.GENERIC: format_generic_embed,
}


class ShadyStatus(commands.Cog):
    """Multi-game server status query cog."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "servers": {},  # name -> server config
            "rate_limit_seconds": 300,  # 5 minute default rate limit
            "post_channel": None,  # Channel to post results (optional)
            "mod_roles": [],  # Roles that can manage server configs
        }

        # Server config structure:
        # {
        #     "name": "server name",
        #     "ip": "1.2.3.4",
        #     "port": 27015,
        #     "game": "7dtd",  # GameType value
        #     "display_name": "My 7DTD Server",  # Optional custom display name
        #     "enabled": True,
        # }

        self.config.register_guild(**default_guild)

        # Rate limit tracking: guild_id -> {user_id -> {server_name -> timestamp}}
        self._rate_limits: Dict[int, Dict[int, Dict[str, float]]] = {}

    async def is_authorized(self, ctx: commands.Context) -> bool:
        """Check if user has permission to manage servers."""
        if not isinstance(ctx.author, discord.Member):
            return False

        # Admin/owner always authorized
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
            return True

        # Check manage_guild permission
        if ctx.author.guild_permissions.manage_guild:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(ctx.guild).mod_roles()
        return any(role.id in mod_roles for role in ctx.author.roles)

    async def _check_rate_limit(self, guild_id: int, user_id: int, server_name: str) -> Optional[float]:
        """Check if user is rate limited. Returns seconds remaining or None."""
        rate_limit = await self.config.guild_from_id(guild_id).rate_limit_seconds()

        guild_limits = self._rate_limits.setdefault(guild_id, {})
        user_limits = guild_limits.setdefault(user_id, {})
        last_used = user_limits.get(server_name)

        if last_used is None:
            return None

        remaining = rate_limit - (time.time() - last_used)
        return remaining if remaining > 0 else None

    def _record_use(self, guild_id: int, user_id: int, server_name: str) -> None:
        """Record that a user queried a server."""
        self._rate_limits.setdefault(guild_id, {}).setdefault(user_id, {})[server_name] = time.time()

    async def _get_server_choices(self, guild_id: int) -> List[app_commands.Choice[str]]:
        """Get autocomplete choices for servers."""
        servers = await self.config.guild_from_id(guild_id).servers()
        choices: List[app_commands.Choice[str]] = []
        for name, config in servers.items():
            if config.get("enabled", True):
                display = config.get("display_name", name)
                choices.append(app_commands.Choice(name=display, value=name))
        return choices[:25]  # Discord limit

    async def server_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for server names."""
        servers = await self.config.guild(interaction.guild).servers()
        choices = []
        for name, config in servers.items():
            if not config.get("enabled", True):
                continue
            display = config.get("display_name", name)
            if current.lower() in name.lower() or current.lower() in display.lower():
                choices.append(app_commands.Choice(name=display, value=name))
        return choices[:25]

    @app_commands.command(name="server", description="Check the status of a game server")
    @app_commands.describe(name="The server to query")
    @app_commands.autocomplete(name=server_autocomplete)
    @app_commands.guild_only()
    async def server_status(self, interaction: discord.Interaction, name: str) -> None:
        """Query a game server and display its status."""
        if not A2S_AVAILABLE:
            await interaction.response.send_message(
                "❌ Server query is not available. The `python-a2s` library is not installed.",
                ephemeral=True
            )
            return

        servers = await self.config.guild(interaction.guild).servers()

        if name not in servers:
            await interaction.response.send_message(
                f"❌ Server `{name}` not found. Use `/server` with autocomplete to see available servers.",
                ephemeral=True
            )
            return

        server_config = servers[name]

        if not server_config.get("enabled", True):
            await interaction.response.send_message(
                f"❌ Server `{name}` is currently disabled.",
                ephemeral=True
            )
            return

        # Rate limit check
        remaining = await self._check_rate_limit(
            interaction.guild.id, interaction.user.id, name
        )
        if remaining is not None:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            await interaction.response.send_message(
                f"⏳ You can query `{name}` again in **{mins}m {secs}s**.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            data = await query_server(server_config["ip"], server_config["port"])
        except ServerQueryError as e:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Server Query Failed",
                    description=f"Could not reach **{server_config.get('display_name', name)}**.\n`{e}`",
                    color=discord.Color.red(),
                )
            )
            return

        self._record_use(interaction.guild.id, interaction.user.id, name)

        # Get the appropriate formatter
        game_type = GameType(server_config.get("game", "generic"))
        formatter = EMBED_FORMATTERS.get(game_type, format_generic_embed)
        embed = formatter(data, server_config)

        # Add footer with query info
        embed.set_footer(
            text=f"Queried by {interaction.user.display_name} • {server_config['ip']}:{server_config['port']}"
        )
        embed.timestamp = datetime.now(timezone.utc)

        # Check if we should post to a specific channel
        post_channel_id = await self.config.guild(interaction.guild).post_channel()
        if post_channel_id and post_channel_id != interaction.channel_id:
            channel = interaction.guild.get_channel(post_channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                    await interaction.followup.send(
                        f"✅ Server status posted in <#{post_channel_id}>!",
                        ephemeral=True
                    )
                    return
                except discord.Forbidden:
                    log.warning(f"No permission to post in channel {post_channel_id}")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="servers", description="List all configured game servers")
    @app_commands.guild_only()
    async def list_servers(self, interaction: discord.Interaction) -> None:
        """List all configured servers."""
        servers = await self.config.guild(interaction.guild).servers()

        if not servers:
            await interaction.response.send_message(
                "📋 No servers configured. Admins can add servers with `/shadystatus add`.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎮 Configured Servers",
            color=discord.Color.blue(),
        )

        for name, config in servers.items():
            game_type = GameType(config.get("game", "generic"))
            emoji = GAME_EMOJIS.get(game_type, "🎮")
            game_name = GAME_DISPLAY_NAMES.get(game_type, "Unknown")
            status = "✅" if config.get("enabled", True) else "❌"

            display = config.get("display_name", name)
            value = f"{emoji} {game_name}\n`{config['ip']}:{config['port']}`\nStatus: {status}"
            embed.add_field(name=display, value=value, inline=True)

        await interaction.response.send_message(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @commands.hybrid_group(name="shadystatus", aliases=["ss"])
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def shadystatus(self, ctx: commands.Context):
        """Manage game server status queries."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to manage server status settings.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @shadystatus.command(name="add")
    @app_commands.describe(
        name="Unique identifier for the server",
        ip="Server IP address",
        port="Query port (usually game port or game port + 1)",
        game="Game type (7dtd, ark, rust, valheim, pz, vrising, generic)",
        display_name="Optional display name"
    )
    async def add_server(
        self,
        ctx: commands.Context,
        name: str,
        ip: str,
        port: int,
        game: str,
        *,
        display_name: Optional[str] = None
    ):
        """Add a game server to monitor."""
        # Validate game type
        valid_games = [gt.value for gt in GameType]
        if game.lower() not in valid_games:
            await ctx.send(f"❌ Invalid game type. Valid options: {', '.join(valid_games)}")
            return

        async with self.config.guild(ctx.guild).servers() as servers:
            if name in servers:
                await ctx.send(f"❌ Server `{name}` already exists. Use `remove` first or choose a different name.")
                return

            servers[name] = {
                "name": name,
                "ip": ip,
                "port": port,
                "game": game.lower(),
                "display_name": display_name or name,
                "enabled": True,
            }

        await ctx.send(f"✅ Added server `{name}` ({GAME_DISPLAY_NAMES.get(GameType(game.lower()), game)}) at `{ip}:{port}`.")

    @shadystatus.command(name="remove")
    @app_commands.describe(name="Server identifier to remove")
    async def remove_server(self, ctx: commands.Context, name: str):
        """Remove a game server."""
        async with self.config.guild(ctx.guild).servers() as servers:
            if name not in servers:
                await ctx.send(f"❌ Server `{name}` not found.")
                return

            del servers[name]

        await ctx.send(f"✅ Removed server `{name}`.")

    @shadystatus.command(name="enable")
    @app_commands.describe(name="Server identifier", enabled="Enable or disable")
    async def enable_server(self, ctx: commands.Context, name: str, enabled: bool = True):
        """Enable or disable a server."""
        async with self.config.guild(ctx.guild).servers() as servers:
            if name not in servers:
                await ctx.send(f"❌ Server `{name}` not found.")
                return

            servers[name]["enabled"] = enabled

        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Server `{name}` is now {status}.")

    @shadystatus.command(name="ratelimit")
    @app_commands.describe(seconds="Seconds between queries (0 to disable)")
    async def set_rate_limit(self, ctx: commands.Context, seconds: int):
        """Set the rate limit between server queries (in seconds)."""
        if seconds < 0:
            await ctx.send("❌ Rate limit must be positive.")
            return

        await self.config.guild(ctx.guild).rate_limit_seconds.set(seconds)
        if seconds == 0:
            await ctx.send("✅ Rate limiting disabled.")
        else:
            await ctx.send(f"✅ Rate limit set to {seconds} seconds ({seconds // 60} minutes).")

    @shadystatus.command(name="postchannel")
    @app_commands.describe(channel="Channel for status posts (leave empty to use command channel)")
    async def set_post_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the channel where server status is posted (leave empty to use command channel)."""
        if channel:
            await self.config.guild(ctx.guild).post_channel.set(channel.id)
            await ctx.send(f"✅ Server status will be posted to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).post_channel.set(None)
            await ctx.send("✅ Server status will be posted in the channel where the command is used.")

    @shadystatus.command(name="list")
    async def admin_list_servers(self, ctx: commands.Context):
        """List all configured servers with details."""
        servers = await self.config.guild(ctx.guild).servers()
        rate_limit = await self.config.guild(ctx.guild).rate_limit_seconds()
        post_channel = await self.config.guild(ctx.guild).post_channel()

        embed = discord.Embed(
            title="🎮 ShadyStatus Configuration",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="⏱️ Rate Limit",
            value=f"{rate_limit} seconds" if rate_limit else "Disabled",
            inline=True
        )
        embed.add_field(
            name="📢 Post Channel",
            value=f"<#{post_channel}>" if post_channel else "Same channel",
            inline=True
        )

        if servers:
            for name, config in servers.items():
                game_type = GameType(config.get("game", "generic"))
                emoji = GAME_EMOJIS.get(game_type, "🎮")
                status = "✅ Enabled" if config.get("enabled", True) else "❌ Disabled"

                value = f"{emoji} {GAME_DISPLAY_NAMES.get(game_type, 'Unknown')}\n"
                value += f"Address: `{config['ip']}:{config['port']}`\n"
                value += f"Status: {status}"

                embed.add_field(
                    name=f"{config.get('display_name', name)} (`{name}`)",
                    value=value,
                    inline=False
                )
        else:
            embed.add_field(name="Servers", value="No servers configured.", inline=False)

        await ctx.send(embed=embed)

    @shadystatus.command(name="test")
    @app_commands.describe(name="Server identifier to test")
    async def test_server(self, ctx: commands.Context, name: str):
        """Test querying a server (bypasses rate limit)."""
        if not A2S_AVAILABLE:
            await ctx.send("❌ `python-a2s` library not installed.")
            return

        servers = await self.config.guild(ctx.guild).servers()

        if name not in servers:
            await ctx.send(f"❌ Server `{name}` not found.")
            return

        server_config = servers[name]

        async with ctx.typing():
            try:
                data = await query_server(server_config["ip"], server_config["port"])
                game_type = GameType(server_config.get("game", "generic"))
                formatter = EMBED_FORMATTERS.get(game_type, format_generic_embed)
                embed = formatter(data, server_config)
                embed.set_footer(text=f"Test query • {server_config['ip']}:{server_config['port']}")
                await ctx.send("✅ Query successful!", embed=embed)
            except ServerQueryError as e:
                await ctx.send(f"❌ Query failed: {e}")

    @shadystatus.command(name="addrole")
    @app_commands.describe(role="Role that can manage server status")
    async def add_mod_role(self, ctx: commands.Context, role: discord.Role):
        """Add a role that can manage server status settings."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                await ctx.send(f"{role.mention} is already a mod role.", ephemeral=True)
                return
            roles.append(role.id)

        await ctx.send(f"✅ {role.mention} can now manage server status settings.")

    @shadystatus.command(name="removerole")
    @app_commands.describe(role="Role to remove from server status management")
    async def remove_mod_role(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from server status management."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id not in roles:
                await ctx.send(f"{role.mention} is not a mod role.", ephemeral=True)
                return
            roles.remove(role.id)

        await ctx.send(f"✅ {role.mention} can no longer manage server status settings.")


async def setup(bot: Red):
    """Add the cog to the bot."""
    if not A2S_AVAILABLE:
        log.warning(
            "ShadyStatus: python-a2s not installed. "
            "Install it with: pip install python-a2s"
        )
    await bot.add_cog(ShadyStatus(bot))
