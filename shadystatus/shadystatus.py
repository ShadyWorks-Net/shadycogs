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
    """Modal for adding a server with all settings."""

    name = discord.ui.TextInput(label="Server Name (ID)", placeholder="my-server", max_length=32)
    ip = discord.ui.TextInput(label="IP:Port", placeholder="192.168.1.1:27015", max_length=50)
    display = discord.ui.TextInput(label="Display Name", placeholder="My Awesome Server", max_length=100)
    rate_limit = discord.ui.TextInput(label="Rate Limit (seconds, 0=none)", placeholder="300", max_length=5, required=False)

    def __init__(self, cog: "ShadyStatus", game: str, channel_id: Optional[int] = None):
        super().__init__()
        self.cog = cog
        self.game = game
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        # Parse IP:Port
        ip_port = self.ip.value.strip()
        if ":" not in ip_port:
            await interaction.response.send_message("Invalid format. Use `IP:PORT` (e.g., `192.168.1.1:27015`)", ephemeral=True)
            return

        try:
            ip, port_str = ip_port.rsplit(":", 1)
            port_num = int(port_str)
        except ValueError:
            await interaction.response.send_message("Invalid port number.", ephemeral=True)
            return

        # Parse rate limit
        try:
            rate = max(0, int(self.rate_limit.value or "300"))
        except ValueError:
            rate = 300

        name = self.name.value.lower().replace(" ", "-")

        async with self.cog.config.guild(interaction.guild).servers() as servers:
            if name in servers:
                await interaction.response.send_message(f"Server `{name}` already exists.", ephemeral=True)
                return

            servers[name] = {
                "ip": ip,
                "port": port_num,
                "game": self.game,
                "display_name": self.display.value or name,
                "enabled": True,
                "post_channel": self.channel_id,  # Per-server channel
                "rate_limit": rate,  # Per-server rate limit
            }

        game_name = GAME_NAMES.get(GameType(self.game), self.game)
        channel_str = f"<#{self.channel_id}>" if self.channel_id else "same channel"
        await interaction.response.send_message(
            f"✅ Added **{self.display.value or name}** ({game_name})\n"
            f"📍 `{ip}:{port_num}`\n"
            f"📢 Posts to: {channel_str}\n"
            f"⏱️ Rate limit: {rate}s",
            ephemeral=True
        )


class GameSelect(discord.ui.Select):
    """Select game type for new server."""

    def __init__(self, cog: "ShadyStatus"):
        self.cog = cog
        options = [
            discord.SelectOption(label=GAME_NAMES[gt], value=gt.value, emoji=GAME_EMOJIS[gt])
            for gt in GameType
        ]
        super().__init__(placeholder="1️⃣ Select game type...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        # Store selection and prompt for channel
        self.view.selected_game = self.values[0]
        game_name = GAME_NAMES.get(GameType(self.values[0]), self.values[0])
        await interaction.response.send_message(
            f"Selected **{game_name}**. Now select a post channel below, then click **Continue**.",
            ephemeral=True
        )


class ServerChannelModal(discord.ui.Modal, title="Set Server Post Channel"):
    """Modal for setting the post channel for a new server."""

    channel_name = discord.ui.TextInput(
        label="Channel Name (or ID)",
        placeholder="Leave empty for same channel, or type channel name",
        required=False,
        max_length=100,
    )

    def __init__(self, view: "AddServerView"):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction: discord.Interaction):
        search = self.channel_name.value.strip()

        if not search:
            self.parent_view.selected_channel = None
            await interaction.response.send_message(
                "Posts will go to **same channel** (where command is run). Click **Continue** to enter server details.",
                ephemeral=True
            )
            return

        # Get bot-visible channels
        bot_channels = [
            ch for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).send_messages
            and ch.permissions_for(interaction.guild.me).view_channel
        ]

        # Try ID first
        if search.isdigit():
            channel = interaction.guild.get_channel(int(search))
            if channel and channel in bot_channels:
                self.parent_view.selected_channel = channel.id
                await interaction.response.send_message(
                    f"Posts will go to {channel.mention}. Click **Continue** to enter server details.",
                    ephemeral=True
                )
                return

        # Search by name
        search_lower = search.lower().lstrip('#')
        matches = [ch for ch in bot_channels if search_lower in ch.name.lower()]

        if not matches:
            await interaction.response.send_message(f"❌ No channels found matching `{search}`.", ephemeral=True)
            return

        if len(matches) == 1:
            self.parent_view.selected_channel = matches[0].id
            await interaction.response.send_message(
                f"Posts will go to {matches[0].mention}. Click **Continue** to enter server details.",
                ephemeral=True
            )
            return

        match_list = "\n".join([f"• #{ch.name}" for ch in matches[:10]])
        await interaction.response.send_message(
            f"⚠️ Multiple channels match `{search}`:\n{match_list}\n\nBe more specific or use the channel ID.",
            ephemeral=True
        )


class AddServerView(discord.ui.View):
    """View for adding a server - select game and channel first."""

    def __init__(self, cog: "ShadyStatus", guild: discord.Guild):
        super().__init__(timeout=180)
        self.cog = cog
        self.selected_game = None
        self.selected_channel = None

        # Add game select
        self.add_item(GameSelect(cog))

    @discord.ui.button(label="Set Post Channel", style=discord.ButtonStyle.secondary, emoji="📢", row=1)
    async def channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServerChannelModal(self))

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.green, emoji="➡️", row=2)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_game:
            await interaction.response.send_message("Please select a game type first.", ephemeral=True)
            return

        await interaction.response.send_modal(
            AddServerModal(self.cog, self.selected_game, self.selected_channel)
        )


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


class DefaultChannelModal(discord.ui.Modal, title="Set Default Post Channel"):
    """Modal for setting the default post channel."""

    channel_name = discord.ui.TextInput(
        label="Channel Name (or ID)",
        placeholder="Leave empty for same channel, or type channel name",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "ShadyStatus"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        search = self.channel_name.value.strip()

        if not search:
            await self.cog.config.guild(interaction.guild).post_channel.set(None)
            await interaction.response.send_message("✅ Default post channel cleared (posts to same channel).", ephemeral=True)
            return

        bot_channels = [
            ch for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).send_messages
            and ch.permissions_for(interaction.guild.me).view_channel
        ]

        if search.isdigit():
            channel = interaction.guild.get_channel(int(search))
            if channel and channel in bot_channels:
                await self.cog.config.guild(interaction.guild).post_channel.set(channel.id)
                await interaction.response.send_message(f"✅ Default post channel set to {channel.mention}", ephemeral=True)
                return

        search_lower = search.lower().lstrip('#')
        matches = [ch for ch in bot_channels if search_lower in ch.name.lower()]

        if not matches:
            await interaction.response.send_message(f"❌ No channels found matching `{search}`.", ephemeral=True)
            return

        if len(matches) == 1:
            await self.cog.config.guild(interaction.guild).post_channel.set(matches[0].id)
            await interaction.response.send_message(f"✅ Default post channel set to {matches[0].mention}", ephemeral=True)
            return

        match_list = "\n".join([f"• #{ch.name}" for ch in matches[:10]])
        await interaction.response.send_message(
            f"⚠️ Multiple channels match `{search}`:\n{match_list}\n\nBe more specific or use the channel ID.",
            ephemeral=True
        )


class SetupView(discord.ui.View):
    """Interactive setup view."""

    def __init__(self, cog: "ShadyStatus", guild: discord.Guild, config: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config

    @discord.ui.button(label="Set Default Channel", style=discord.ButtonStyle.secondary, emoji="📢", row=0)
    async def channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DefaultChannelModal(self.cog))

    @discord.ui.button(label="Set Rate Limit", style=discord.ButtonStyle.secondary, emoji="⏱️", row=0)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.guild(interaction.guild).all()
        await interaction.response.send_modal(SettingsModal(self.cog, config["rate_limit_seconds"]))

    @discord.ui.button(label="Add Server", style=discord.ButtonStyle.success, emoji="➕", row=1)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="➕ Add Game Server",
            description="1️⃣ Select a game type\n2️⃣ Set post channel (optional)\n3️⃣ Click Continue to enter details",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(
            embed=embed,
            view=AddServerView(self.cog, interaction.guild),
            ephemeral=True
        )


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

        # Use per-server rate limit, fall back to global
        rate_limit = cfg.get("rate_limit")
        if rate_limit is None:
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

        # Use per-server post channel, fall back to global
        post_channel = cfg.get("post_channel")
        if post_channel is None:
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
    @app_commands.describe()
    async def shadystatus_setup(self, ctx: commands.Context):
        """View and configure server status settings."""
        config = await self.config.guild(ctx.guild).all()
        servers = config.get("servers", {})

        embed = discord.Embed(title="🎮 ShadyStatus Setup", color=discord.Color.blue())

        # Show global settings
        default_channel = f"<#{config['post_channel']}>" if config.get("post_channel") else "Same channel"
        embed.add_field(name="Default Channel", value=default_channel, inline=True)
        embed.add_field(name="Default Rate Limit", value=f"{config.get('rate_limit_seconds', 300)}s", inline=True)
        embed.add_field(name="Servers", value=str(len(servers)), inline=True)

        if servers:
            server_list = []
            for name, cfg in list(servers.items())[:5]:
                game = GAME_NAMES.get(GameType(cfg.get("game", "generic")), cfg.get("game", "?"))
                status = "✅" if cfg.get("enabled", True) else "❌"
                server_list.append(f"{status} **{cfg.get('display_name', name)}** ({game})")

            if len(servers) > 5:
                server_list.append(f"... and {len(servers) - 5} more")

            embed.add_field(name="Configured Servers", value="\n".join(server_list), inline=False)
        else:
            embed.add_field(name="Configured Servers", value="None - use buttons below to add", inline=False)

        view = SetupView(self, ctx.guild, config)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @shadystatus.command(name="add")
    @app_commands.describe()
    async def shadystatus_add(self, ctx: commands.Context):
        """Add a game server."""
        embed = discord.Embed(
            title="🎮 Add Game Server",
            description=(
                "**Step 1:** Select the game type\n"
                "**Step 2:** Select where to post status\n"
                "**Step 3:** Click Continue and fill in server details"
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed, view=AddServerView(self, ctx.guild), ephemeral=True)

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

    @shadystatus.command(name="listroles")
    @app_commands.describe()
    async def shadystatus_listroles(self, ctx: commands.Context):
        """List all roles that can manage servers."""
        mod_roles = await self.config.guild(ctx.guild).mod_roles()

        if not mod_roles:
            await ctx.send("No mod roles configured. Admins only.")
            return

        role_mentions = []
        for role_id in mod_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
            else:
                role_mentions.append(f"Unknown ({role_id})")

        embed = discord.Embed(
            title="🎮 ShadyStatus Mod Roles",
            description="\n".join(role_mentions),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Admins can always manage servers")
        await ctx.send(embed=embed)

    @shadystatus.command(name="channel")
    @app_commands.describe(channel="Default post channel (bot-visible channels)")
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def shadystatus_channel(self, ctx: commands.Context, channel: str = None):
        """Set the default post channel for server status."""
        if channel is None:
            await self.config.guild(ctx.guild).post_channel.set(None)
            await ctx.send("✅ Default post channel cleared (will post in same channel).")
            return

        try:
            channel_id = int(channel)
            ch = ctx.guild.get_channel(channel_id)
            if not ch:
                await ctx.send("Channel not found.", ephemeral=True)
                return
            await self.config.guild(ctx.guild).post_channel.set(channel_id)
            await ctx.send(f"✅ Default post channel set to {ch.mention}")
        except ValueError:
            await ctx.send("Invalid channel.", ephemeral=True)


async def setup(bot: Red):
    if not A2S_AVAILABLE:
        log.warning("ShadyStatus: python-a2s not installed")
    await bot.add_cog(ShadyStatus(bot))
