"""
ShadyStatus - Multi-game server status query cog.
Queries game servers via Steam A2S protocol.
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
CONFIG_IDENTIFIER = 1234567895

try:
    import a2s
    A2S_AVAILABLE = True
except ImportError:
    A2S_AVAILABLE = False
    log.warning("python-a2s not installed")


class GameType(Enum):
    SEVENDTD = "7dtd"
    ARK = "ark"
    RUST = "rust"
    VALHEIM = "valheim"
    PROJECTZOMBOID = "pz"
    VRISING = "vrising"
    GENERIC = "generic"


GAME_NAMES = {
    GameType.SEVENDTD: "7 Days to Die",
    GameType.ARK: "ARK: Survival Evolved",
    GameType.RUST: "Rust",
    GameType.VALHEIM: "Valheim",
    GameType.PROJECTZOMBOID: "Project Zomboid",
    GameType.VRISING: "V Rising",
    GameType.GENERIC: "Game Server",
}

GAME_EMOJIS = {
    GameType.SEVENDTD: "🧟", GameType.ARK: "🦖", GameType.RUST: "🔧",
    GameType.VALHEIM: "⚔️", GameType.PROJECTZOMBOID: "🧟",
    GameType.VRISING: "🧛", GameType.GENERIC: "🎮",
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
    pass


async def query_server(ip: str, port: int, timeout: float = 5.0) -> Dict[str, Any]:
    if not A2S_AVAILABLE:
        raise ServerQueryError("python-a2s not installed")

    address = (ip, port)
    loop = asyncio.get_event_loop()

    try:
        info = await loop.run_in_executor(None, lambda: a2s.info(address, timeout=timeout))
        rules = {}
        try:
            rules = dict(await loop.run_in_executor(None, lambda: a2s.rules(address, timeout=timeout)))
        except Exception:
            pass

        return {
            "server_name": info.server_name,
            "map_name": info.map_name,
            "players": info.player_count,
            "max_players": info.max_players,
            "password_protected": info.password_protected,
            "version": info.version,
            "ping": info.ping,
            "rules": rules,
            "game": info.game,
        }
    except asyncio.TimeoutError:
        raise ServerQueryError("Server timeout")
    except Exception as e:
        raise ServerQueryError(str(e))


def format_embed(data: Dict, config: Dict, game_type: GameType) -> discord.Embed:
    """Format server status embed."""
    emoji = GAME_EMOJIS.get(game_type, "🎮")
    color = GAME_COLORS.get(game_type, discord.Color.blurple())
    rules = data.get("rules", {})

    embed = discord.Embed(title=f"{emoji} {data['server_name']}", color=color)
    embed.add_field(name="👥 Players", value=f"**{data['players']} / {data['max_players']}**", inline=True)
    embed.add_field(name="🗺️ Map", value=data.get("map_name", "Unknown"), inline=True)
    embed.add_field(name="📡 Ping", value=f"{int(data.get('ping', 0) * 1000)}ms", inline=True)

    # Game-specific fields
    if game_type == GameType.SEVENDTD:
        current_time = int(rules.get("CurrentServerTime", 0))
        if current_time:
            day = (current_time // 24000) + 1
            hour = (current_time % 24000) // 1000
            embed.add_field(name="📅 Day", value=f"**{day}**", inline=True)
            embed.add_field(name="🕐 Time", value=f"**{hour:02d}:00**", inline=True)

    if data.get("version"):
        embed.add_field(name="📋 Version", value=data["version"], inline=True)
    if data.get("password_protected"):
        embed.add_field(name="🔒", value="Password", inline=True)

    return embed


# ==================== UI COMPONENTS ====================


class AddServerModal(discord.ui.Modal, title="Add Game Server"):
    """Modal for adding a server."""

    name = discord.ui.TextInput(label="Server Name (ID)", placeholder="my-server", max_length=32)
    ip = discord.ui.TextInput(label="IP Address", placeholder="192.168.1.1")
    port = discord.ui.TextInput(label="Query Port", placeholder="27015", max_length=5)
    display = discord.ui.TextInput(label="Display Name (optional)", required=False, max_length=100)

    def __init__(self, cog: "ShadyStatus", game: str):
        super().__init__()
        self.cog = cog
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        try:
            port_num = int(self.port.value)
        except ValueError:
            await interaction.response.send_message("Invalid port number.", ephemeral=True)
            return

        name = self.name.value.lower().replace(" ", "-")

        async with self.cog.config.guild(interaction.guild).servers() as servers:
            if name in servers:
                await interaction.response.send_message(f"Server `{name}` already exists.", ephemeral=True)
                return

            servers[name] = {
                "ip": self.ip.value,
                "port": port_num,
                "game": self.game,
                "display_name": self.display.value or name,
                "enabled": True,
            }

        game_name = GAME_NAMES.get(GameType(self.game), self.game)
        await interaction.response.send_message(
            f"✅ Added **{self.display.value or name}** ({game_name}) at `{self.ip.value}:{port_num}`"
        )


class GameSelect(discord.ui.Select):
    """Select game type for new server."""

    def __init__(self, cog: "ShadyStatus"):
        self.cog = cog
        options = [
            discord.SelectOption(label=GAME_NAMES[gt], value=gt.value, emoji=GAME_EMOJIS[gt])
            for gt in GameType
        ]
        super().__init__(placeholder="Select game type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddServerModal(self.cog, self.values[0]))


class AddServerView(discord.ui.View):
    """View for adding a server."""

    def __init__(self, cog: "ShadyStatus"):
        super().__init__(timeout=120)
        self.add_item(GameSelect(cog))


class SettingsModal(discord.ui.Modal, title="ShadyStatus Settings"):
    """Modal for settings."""

    rate_limit = discord.ui.TextInput(
        label="Rate Limit (seconds, 0 to disable)",
        placeholder="300",
        max_length=4,
    )

    def __init__(self, cog: "ShadyStatus", current_rate: int):
        super().__init__()
        self.cog = cog
        self.rate_limit.default = str(current_rate)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rate = max(0, int(self.rate_limit.value))
        except ValueError:
            rate = 300

        await self.cog.config.guild(interaction.guild).rate_limit_seconds.set(rate)
        await interaction.response.send_message(f"Rate limit set to **{rate}** seconds.", ephemeral=True)


class ChannelSelect(discord.ui.Select):
    """Select for post channel."""

    def __init__(self, cog: "ShadyStatus", channels: List[discord.TextChannel], current_id: Optional[int]):
        self.cog = cog
        options = [discord.SelectOption(label="Same Channel (default)", value="none", emoji="💬")]
        for ch in channels[:24]:
            options.append(discord.SelectOption(
                label=f"#{ch.name}"[:100],
                value=str(ch.id),
                default=ch.id == current_id
            ))
        super().__init__(placeholder="Post results to...", options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        channel_id = None if value == "none" else int(value)
        await self.cog.config.guild(interaction.guild).post_channel.set(channel_id)
        display = f"<#{channel_id}>" if channel_id else "Same channel"
        await interaction.response.send_message(f"Results will post to {display}", ephemeral=True)


class SetupView(discord.ui.View):
    """Interactive setup view."""

    def __init__(self, cog: "ShadyStatus", guild: discord.Guild, config: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.config = config

        channels = [ch for ch in guild.text_channels
                    if ch.permissions_for(guild.me).send_messages]
        channels.sort(key=lambda c: (c.category.position if c.category else -1, c.position))

        self.add_item(ChannelSelect(cog, channels, config["post_channel"]))

    @discord.ui.button(label="Rate Limit", style=discord.ButtonStyle.primary, emoji="⏱️", row=1)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.guild(interaction.guild).all()
        await interaction.response.send_modal(SettingsModal(self.cog, config["rate_limit_seconds"]))


# ==================== MAIN COG ====================


class ShadyStatus(commands.Cog):
    """Multi-game server status queries."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "servers": {},
            "rate_limit_seconds": 300,
            "post_channel": None,
            "mod_roles": [],
        }
        self.config.register_guild(**default_guild)
        self._rate_limits: Dict[int, Dict[int, Dict[str, float]]] = {}

    async def is_authorized(self, ctx: commands.Context) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True
        if not isinstance(ctx.author, discord.Member):
            return False
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
            return True
        if ctx.author.guild_permissions.manage_guild:
            return True
        mod_roles = await self.config.guild(ctx.guild).mod_roles()
        return any(role.id in mod_roles for role in ctx.author.roles)

    def _check_rate_limit(self, guild_id: int, user_id: int, server: str, rate: int) -> Optional[float]:
        if rate <= 0:
            return None
        last = self._rate_limits.get(guild_id, {}).get(user_id, {}).get(server)
        if last is None:
            return None
        remaining = rate - (time.time() - last)
        return remaining if remaining > 0 else None

    def _record_use(self, guild_id: int, user_id: int, server: str):
        self._rate_limits.setdefault(guild_id, {}).setdefault(user_id, {})[server] = time.time()

    async def server_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        servers = await self.config.guild(interaction.guild).servers()
        choices = []
        for name, cfg in servers.items():
            if not cfg.get("enabled", True):
                continue
            display = cfg.get("display_name", name)
            if current.lower() in name.lower() or current.lower() in display.lower():
                choices.append(app_commands.Choice(name=display, value=name))
        return choices[:25]

    # ==================== USER COMMANDS ====================

    @app_commands.command(name="server", description="Check game server status")
    @app_commands.describe(name="Server to query")
    @app_commands.autocomplete(name=server_autocomplete)
    @app_commands.guild_only()
    async def server_status(self, interaction: discord.Interaction, name: str):
        """Query a game server."""
        if not A2S_AVAILABLE:
            await interaction.response.send_message("Server queries unavailable (missing python-a2s).", ephemeral=True)
            return

        servers = await self.config.guild(interaction.guild).servers()
        if name not in servers:
            await interaction.response.send_message(f"Server `{name}` not found.", ephemeral=True)
            return

        cfg = servers[name]
        if not cfg.get("enabled", True):
            await interaction.response.send_message(f"Server `{name}` is disabled.", ephemeral=True)
            return

        rate_limit = await self.config.guild(interaction.guild).rate_limit_seconds()
        remaining = self._check_rate_limit(interaction.guild.id, interaction.user.id, name, rate_limit)
        if remaining:
            await interaction.response.send_message(
                f"Wait **{int(remaining // 60)}m {int(remaining % 60)}s** to query again.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            data = await query_server(cfg["ip"], cfg["port"])
        except ServerQueryError as e:
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Query Failed",
                description=f"**{cfg.get('display_name', name)}**\n`{e}`",
                color=discord.Color.red()
            ))
            return

        self._record_use(interaction.guild.id, interaction.user.id, name)

        game_type = GameType(cfg.get("game", "generic"))
        embed = format_embed(data, cfg, game_type)
        embed.set_footer(text=f"{interaction.user.display_name} • {cfg['ip']}:{cfg['port']}")
        embed.timestamp = datetime.now(timezone.utc)

        post_channel = await self.config.guild(interaction.guild).post_channel()
        if post_channel and post_channel != interaction.channel_id:
            channel = interaction.guild.get_channel(post_channel)
            if channel:
                await channel.send(embed=embed)
                await interaction.followup.send(f"Posted in <#{post_channel}>!", ephemeral=True)
                return

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="servers", description="List configured servers")
    @app_commands.guild_only()
    async def list_servers(self, interaction: discord.Interaction):
        """List all servers."""
        servers = await self.config.guild(interaction.guild).servers()

        if not servers:
            await interaction.response.send_message("No servers configured.", ephemeral=True)
            return

        embed = discord.Embed(title="🎮 Game Servers", color=discord.Color.blue())

        for name, cfg in servers.items():
            game_type = GameType(cfg.get("game", "generic"))
            emoji = GAME_EMOJIS.get(game_type, "🎮")
            status = "✅" if cfg.get("enabled", True) else "❌"
            embed.add_field(
                name=cfg.get("display_name", name),
                value=f"{emoji} {GAME_NAMES.get(game_type, 'Unknown')}\n{status} `{cfg['ip']}:{cfg['port']}`",
                inline=True
            )

        await interaction.response.send_message(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @commands.hybrid_group(name="shadystatus", aliases=["ss"])
    @commands.guild_only()
    async def shadystatus(self, ctx: commands.Context):
        """Manage server status settings."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @shadystatus.command(name="setup")
    async def shadystatus_setup(self, ctx: commands.Context):
        """Interactive setup."""
        config = await self.config.guild(ctx.guild).all()

        embed = discord.Embed(title="🎮 ShadyStatus Setup", color=discord.Color.blue())
        embed.add_field(name="Servers", value=str(len(config["servers"])), inline=True)
        embed.add_field(name="Rate Limit", value=f"{config['rate_limit_seconds']}s", inline=True)
        embed.add_field(name="Post Channel", value=f"<#{config['post_channel']}>" if config["post_channel"] else "Same channel", inline=True)

        await ctx.send(embed=embed, view=SetupView(self, ctx.guild, config))

    @shadystatus.command(name="add")
    async def shadystatus_add(self, ctx: commands.Context):
        """Add a game server."""
        await ctx.send("Select game type:", view=AddServerView(self))

    @shadystatus.command(name="remove")
    @app_commands.describe(name="Server ID to remove")
    async def shadystatus_remove(self, ctx: commands.Context, name: str):
        """Remove a server."""
        async with self.config.guild(ctx.guild).servers() as servers:
            if name not in servers:
                await ctx.send(f"Server `{name}` not found.")
                return
            del servers[name]
        await ctx.send(f"✅ Removed `{name}`.")

    @shadystatus.command(name="toggle")
    @app_commands.describe(name="Server ID", enabled="Enable or disable")
    async def shadystatus_toggle(self, ctx: commands.Context, name: str, enabled: bool = True):
        """Enable or disable a server."""
        async with self.config.guild(ctx.guild).servers() as servers:
            if name not in servers:
                await ctx.send(f"Server `{name}` not found.")
                return
            servers[name]["enabled"] = enabled
        await ctx.send(f"✅ Server `{name}` {'enabled' if enabled else 'disabled'}.")

    @shadystatus.command(name="test")
    @app_commands.describe(name="Server ID to test")
    async def shadystatus_test(self, ctx: commands.Context, name: str):
        """Test query a server (bypasses rate limit)."""
        if not A2S_AVAILABLE:
            await ctx.send("python-a2s not installed.")
            return

        servers = await self.config.guild(ctx.guild).servers()
        if name not in servers:
            await ctx.send(f"Server `{name}` not found.")
            return

        cfg = servers[name]

        async with ctx.typing():
            try:
                data = await query_server(cfg["ip"], cfg["port"])
                game_type = GameType(cfg.get("game", "generic"))
                embed = format_embed(data, cfg, game_type)
                embed.set_footer(text=f"Test • {cfg['ip']}:{cfg['port']}")
                await ctx.send("✅ Query successful!", embed=embed)
            except ServerQueryError as e:
                await ctx.send(f"❌ Query failed: {e}")

    @shadystatus.command(name="addrole")
    @app_commands.describe(role="Role that can manage servers")
    async def shadystatus_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a management role."""
        if not ctx.author.guild_permissions.administrator:
            if not await self.bot.is_owner(ctx.author):
                await ctx.send("Only administrators can manage roles.", ephemeral=True)
                return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                await ctx.send(f"{role.mention} is already a mod role.")
                return
            roles.append(role.id)
        await ctx.send(f"✅ {role.mention} can now manage servers.")

    @shadystatus.command(name="removerole")
    @app_commands.describe(role="Role to remove")
    async def shadystatus_removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a management role."""
        if not ctx.author.guild_permissions.administrator:
            if not await self.bot.is_owner(ctx.author):
                await ctx.send("Only administrators can manage roles.", ephemeral=True)
                return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id not in roles:
                await ctx.send(f"{role.mention} is not a mod role.")
                return
            roles.remove(role.id)
        await ctx.send(f"✅ {role.mention} removed.")


async def setup(bot: Red):
    if not A2S_AVAILABLE:
        log.warning("ShadyStatus: python-a2s not installed")
    await bot.add_cog(ShadyStatus(bot))
