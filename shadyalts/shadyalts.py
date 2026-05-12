"""
ShadyAlts - Alt account tracking for moderation.
Slash commands only with modal-based UI.

Features:
- Bidirectional alt linking
- Alt network visualization
- Support for in-server and out-of-server users
- Reason storage when linking
- Join/leave notifications for known alts
- Configurable moderator roles
"""

import discord
import logging
from datetime import datetime, timezone
from typing import Optional, List

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadyalts")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 260288776360820737


class MarkAltModal(discord.ui.Modal, title="Mark Users as Alts"):
    """Modal for marking alt accounts by user ID."""

    user1_id = discord.ui.TextInput(
        label="First User ID",
        placeholder="Enter first user's Discord ID...",
        required=True,
        max_length=20
    )

    user2_id = discord.ui.TextInput(
        label="Second User ID",
        placeholder="Enter second user's Discord ID...",
        required=True,
        max_length=20
    )

    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Why are these accounts linked?",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    def __init__(self, cog: "ShadyAlts"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        """Handle alt marking submission."""
        try:
            uid1 = int(self.user1_id.value)
            uid2 = int(self.user2_id.value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid user IDs. Please provide numeric Discord user IDs.",
                ephemeral=True
            )
            return

        if uid1 == uid2:
            await interaction.response.send_message(
                "Cannot mark a user as their own alt.",
                ephemeral=True
            )
            return

        reason = self.reason.value if self.reason.value else None

        # Check if already linked
        if await self.cog.is_alt(interaction.guild.id, uid1, uid2):
            await interaction.response.send_message(
                "These accounts are already linked as alts.",
                ephemeral=True
            )
            return

        # Add alt relationship (bidirectional)
        await self.cog.add_alt(interaction.guild.id, uid1, uid2, reason)

        # Get user info
        try:
            u1 = await self.cog.bot.fetch_user(uid1)
            u1_display = f"{u1.name} ({uid1})"
        except Exception:
            u1_display = f"User ID: {uid1}"

        try:
            u2 = await self.cog.bot.fetch_user(uid2)
            u2_display = f"{u2.name} ({uid2})"
        except Exception:
            u2_display = f"User ID: {uid2}"

        # Get full network
        alts = await self.cog.get_alts(interaction.guild.id, uid1)

        embed = discord.Embed(
            title="✅ Alts Linked",
            description=f"Linked {u1_display} ↔ {u2_display}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Network Size", value=f"{len(alts) + 1} accounts", inline=True)
        embed.add_field(name="Marked By", value=interaction.user.mention, inline=True)

        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)

        if alts:
            network_list = []
            for alt in alts[:10]:
                try:
                    u = await self.cog.bot.fetch_user(alt["alt_id"])
                    network_list.append(f"• {u.name} ({alt['alt_id']})")
                except Exception:
                    network_list.append(f"• User ID: {alt['alt_id']}")

            if len(alts) > 10:
                network_list.append(f"... and {len(alts) - 10} more")

            embed.add_field(name="Full Network", value="\n".join(network_list), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Log to mod channel
        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🔗 **Alts Linked** by {interaction.user.mention}\n"
            f"**Users:** <@{uid1}> ↔ <@{uid2}>\n"
            f"**Network Size:** {len(alts) + 1} accounts"
        )


class MarkAltMemberModal(discord.ui.Modal, title="Mark as Alt"):
    """Modal for adding alt link with reason when using member select."""

    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Why are these accounts linked?",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    def __init__(self, cog: "ShadyAlts", member1: discord.Member, member2: discord.Member):
        super().__init__()
        self.cog = cog
        self.member1 = member1
        self.member2 = member2

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason.value if self.reason.value else None

        await self.cog.add_alt(interaction.guild.id, self.member1.id, self.member2.id, reason)

        alts = await self.cog.get_alts(interaction.guild.id, self.member1.id)

        embed = discord.Embed(
            title="✅ Alts Linked",
            description=f"Linked {self.member1.mention} ↔ {self.member2.mention}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Network Size", value=f"{len(alts) + 1} accounts", inline=True)
        embed.add_field(name="Marked By", value=interaction.user.mention, inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🔗 **Alts Linked** by {interaction.user.mention}\n"
            f"**Users:** {self.member1.mention} ↔ {self.member2.mention}"
        )


class UnmarkAltIdModal(discord.ui.Modal, title="Unmark Alt by IDs"):
    """Modal for unlinking alt accounts by user ID."""

    user1_id = discord.ui.TextInput(
        label="First User ID",
        placeholder="Enter first user's Discord ID...",
        required=True,
        max_length=20
    )

    user2_id = discord.ui.TextInput(
        label="Second User ID",
        placeholder="Enter second user's Discord ID...",
        required=True,
        max_length=20
    )

    def __init__(self, cog: "ShadyAlts"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            uid1 = int(self.user1_id.value)
            uid2 = int(self.user2_id.value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid user IDs. Please provide numeric Discord user IDs.",
                ephemeral=True
            )
            return

        if not await self.cog.is_alt(interaction.guild.id, uid1, uid2):
            await interaction.response.send_message(
                "These accounts are not linked as alts.",
                ephemeral=True
            )
            return

        await self.cog.remove_alt(interaction.guild.id, uid1, uid2)

        try:
            u1 = await self.cog.bot.fetch_user(uid1)
            u1_display = f"{u1.name} ({uid1})"
        except Exception:
            u1_display = f"User ID: {uid1}"

        try:
            u2 = await self.cog.bot.fetch_user(uid2)
            u2_display = f"{u2.name} ({uid2})"
        except Exception:
            u2_display = f"User ID: {uid2}"

        embed = discord.Embed(
            title="✅ Alts Unlinked",
            description=f"Removed alt link between {u1_display} ↔ {u2_display}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🔓 **Alts Unlinked** by {interaction.user.mention}\n"
            f"**Users:** <@{uid1}> ↔ <@{uid2}>"
        )


class ChannelSelectView(discord.ui.View):
    """View for selecting mod log channel."""

    def __init__(self, cog: "ShadyAlts"):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Select channel...",
        min_values=0,
        max_values=1
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if select.values:
            channel = select.values[0]
            await self.cog.config.guild(interaction.guild).mod_log_channel.set(channel.id)
            await interaction.response.send_message(f"✅ Mod log channel set to {channel.mention}", ephemeral=True)
        else:
            await self.cog.config.guild(interaction.guild).mod_log_channel.set(None)
            await interaction.response.send_message("✅ Mod log channel cleared.", ephemeral=True)
        self.stop()


class ShadyAlts(commands.Cog):
    """Alt account tracking and notifications."""

    __version__ = "2.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "alts": [],  # List of {user_id, alt_id, reason, created_at}
            "mod_log_channel": None,
            "notify_on_join": True,
            "notify_on_leave": True,
            "mod_roles": [],  # List of role IDs that can manage alts
        }
        self.config.register_guild(**default_guild)

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage alts."""
        # Bot owner always authorized
        if await self.bot.is_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/guild owner always authorized
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True

        # Check for moderate_members permission
        if interaction.user.guild_permissions.moderate_members:
            return True

        # Check for ban_members permission (alt management often ties to banning)
        if interaction.user.guild_permissions.ban_members:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(interaction.guild).mod_roles()
        return any(role.id in mod_roles for role in interaction.user.roles)

    # ===== DATABASE METHODS =====

    async def add_alt(self, guild_id: int, user_id: int, alt_id: int, reason: Optional[str] = None) -> None:
        """Add alt relationship (bidirectional)."""
        async with self.config.guild_from_id(guild_id).alts() as alts:
            entry1 = {
                "user_id": user_id,
                "alt_id": alt_id,
                "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            entry2 = {
                "user_id": alt_id,
                "alt_id": user_id,
                "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            exists1 = any(a["user_id"] == user_id and a["alt_id"] == alt_id for a in alts)
            exists2 = any(a["user_id"] == alt_id and a["alt_id"] == user_id for a in alts)

            if not exists1:
                alts.append(entry1)
            if not exists2:
                alts.append(entry2)

    async def remove_alt(self, guild_id: int, user_id: int, alt_id: int) -> None:
        """Remove alt relationship (bidirectional)."""
        async with self.config.guild_from_id(guild_id).alts() as alts:
            alts[:] = [
                a for a in alts
                if not ((a["user_id"] == user_id and a["alt_id"] == alt_id) or
                        (a["user_id"] == alt_id and a["alt_id"] == user_id))
            ]

    async def get_alts(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all alts for a user."""
        alts = await self.config.guild_from_id(guild_id).alts()
        return [a for a in alts if a["user_id"] == user_id]

    async def is_alt(self, guild_id: int, user_id: int, alt_id: int) -> bool:
        """Check if two users are linked as alts."""
        alts = await self.config.guild_from_id(guild_id).alts()
        return any(a["user_id"] == user_id and a["alt_id"] == alt_id for a in alts)

    async def log_to_mod_channel(self, guild: discord.Guild, message: Optional[str] = None, embed: Optional[discord.Embed] = None) -> None:
        """Log message to mod channel."""
        channel_id = await self.config.guild(guild).mod_log_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if channel:
            try:
                if embed:
                    await channel.send(embed=embed)
                elif message:
                    await channel.send(message)
            except discord.Forbidden:
                log.warning(f"Cannot send to mod log channel in {guild.name}")

    # ===== EVENTS =====

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Notify when a user with known alts joins."""
        if member.bot:
            return

        notify = await self.config.guild(member.guild).notify_on_join()
        if not notify:
            return

        alts = await self.get_alts(member.guild.id, member.id)

        if alts:
            embed = discord.Embed(
                title="⚠️ Known Alt Joined",
                description=f"{member.mention} (`{member.id}`) just joined and has {len(alts)} known alt(s)",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)

            alts_text = "\n".join([f"• <@{alt['alt_id']}> (`{alt['alt_id']}`)" for alt in alts[:10]])
            if len(alts) > 10:
                alts_text += f"\n... and {len(alts) - 10} more"

            embed.add_field(name="Known Alts", value=alts_text, inline=False)

            await self.log_to_mod_channel(member.guild, embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Notify when a user with known alts leaves."""
        if member.bot:
            return

        notify = await self.config.guild(member.guild).notify_on_leave()
        if not notify:
            return

        alts = await self.get_alts(member.guild.id, member.id)

        if alts:
            embed = discord.Embed(
                title="ℹ️ Known Alt Left",
                description=f"{member} (`{member.id}`) left the server (had {len(alts)} known alt(s))",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            await self.log_to_mod_channel(member.guild, embed=embed)

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="alt", description="Manage alt accounts for server members")
    @app_commands.describe(
        action="Action to perform",
        member1="Primary user",
        member2="Alt account (required for mark/unmark)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Mark as Alts", value="mark"),
        app_commands.Choice(name="Unmark Alts", value="unmark"),
        app_commands.Choice(name="View Alts", value="view"),
    ])
    async def alt_cmd(
        self,
        interaction: discord.Interaction,
        action: str,
        member1: discord.Member,
        member2: Optional[discord.Member] = None
    ):
        """Alt management for server members."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        if action == "mark":
            if not member2:
                await interaction.response.send_message(
                    "You must specify both `member1` and `member2` to mark alts.",
                    ephemeral=True
                )
                return

            if member1.id == member2.id:
                await interaction.response.send_message(
                    "Cannot mark a user as their own alt.", ephemeral=True
                )
                return

            if await self.is_alt(interaction.guild.id, member1.id, member2.id):
                await interaction.response.send_message(
                    f"{member2.mention} is already marked as an alt of {member1.mention}",
                    ephemeral=True
                )
                return

            modal = MarkAltMemberModal(self, member1, member2)
            await interaction.response.send_modal(modal)

        elif action == "unmark":
            if not member2:
                await interaction.response.send_message(
                    "You must specify both `member1` and `member2` to unmark alts.",
                    ephemeral=True
                )
                return

            if not await self.is_alt(interaction.guild.id, member1.id, member2.id):
                await interaction.response.send_message(
                    "These accounts are not marked as alts.",
                    ephemeral=True
                )
                return

            await self.remove_alt(interaction.guild.id, member1.id, member2.id)

            embed = discord.Embed(
                title="✅ Alts Unlinked",
                description=f"Removed alt link between {member1.mention} and {member2.mention}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            await self.log_to_mod_channel(
                interaction.guild,
                f"🔓 **Alts Unlinked** by {interaction.user.mention}\n"
                f"**Users:** {member1.mention} ↔ {member2.mention}"
            )

        elif action == "view":
            alts = await self.get_alts(interaction.guild.id, member1.id)

            if not alts:
                embed = discord.Embed(
                    title="ℹ️ No Alts",
                    description=f"No known alts for {member1.mention}",
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🔗 Alt Network for {member1.display_name}",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member1.display_avatar.url)
            embed.add_field(
                name="Primary Account",
                value=f"{member1.mention} ({member1.id})",
                inline=False
            )
            embed.add_field(
                name="Network Size",
                value=f"{len(alts) + 1} accounts",
                inline=False
            )

            alt_list = []
            for alt in alts[:20]:
                try:
                    u = await self.bot.fetch_user(alt["alt_id"])
                    alt_list.append(f"• <@{alt['alt_id']}> - {u.name} ({alt['alt_id']})")
                except Exception:
                    alt_list.append(f"• <@{alt['alt_id']}> ({alt['alt_id']})")

            if len(alts) > 20:
                alt_list.append(f"... and {len(alts) - 20} more")

            embed.add_field(
                name="Linked Accounts",
                value="\n".join(alt_list),
                inline=False
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="altid",
        description="Manage alt accounts by user ID (for users not in server)"
    )
    @app_commands.describe(action="Action to perform", user_id="Discord User ID")
    @app_commands.choices(action=[
        app_commands.Choice(name="Mark as Alts (opens form)", value="mark"),
        app_commands.Choice(name="Unmark Alts (opens form)", value="unmark"),
        app_commands.Choice(name="View Alts", value="view"),
    ])
    async def altid_cmd(
        self,
        interaction: discord.Interaction,
        action: str,
        user_id: str = None
    ):
        """Alt management by user ID."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        if action == "mark":
            modal = MarkAltModal(self)
            if user_id:
                modal.user1_id.default = user_id
            await interaction.response.send_modal(modal)

        elif action == "unmark":
            modal = UnmarkAltIdModal(self)
            if user_id:
                modal.user1_id.default = user_id
            await interaction.response.send_modal(modal)

        elif action == "view":
            if not user_id:
                await interaction.response.send_message(
                    "You must provide a user ID to view alts.", ephemeral=True
                )
                return

            try:
                uid = int(user_id)
            except ValueError:
                await interaction.response.send_message(
                    "Invalid user ID.", ephemeral=True
                )
                return

            try:
                user = await self.bot.fetch_user(uid)
                user_display = f"{user.name}"
            except Exception:
                user_display = f"User {uid}"

            alts = await self.get_alts(interaction.guild.id, uid)

            if not alts:
                embed = discord.Embed(
                    title="ℹ️ No Alts",
                    description=f"No known alts for {user_display}",
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🔗 Alt Network for {user_display}",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(
                name="Primary Account",
                value=f"<@{uid}> ({uid})",
                inline=False
            )
            embed.add_field(
                name="Network Size",
                value=f"{len(alts) + 1} accounts",
                inline=False
            )

            alt_list = []
            for alt in alts[:20]:
                try:
                    u = await self.bot.fetch_user(alt["alt_id"])
                    alt_list.append(f"• <@{alt['alt_id']}> - {u.name} ({alt['alt_id']})")
                except Exception:
                    alt_list.append(f"• <@{alt['alt_id']}> ({alt['alt_id']})")

            if len(alts) > 20:
                alt_list.append(f"... and {len(alts) - 20} more")

            embed.add_field(
                name="Linked Accounts",
                value="\n".join(alt_list),
                inline=False
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ===== SETTINGS =====

    @app_commands.command(name="altset", description="Configure alt tracking settings")
    @app_commands.describe(setting="Setting to configure", role="Role for add/remove role actions")
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Set Log Channel", value="channel"),
        app_commands.Choice(name="Toggle Join Notifications", value="joinnotify"),
        app_commands.Choice(name="Toggle Leave Notifications", value="leavenotify"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
    ])
    async def altset_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None
    ):
        """Configure alt tracking settings."""
        # For role management, require admin
        if setting in ("addrole", "removerole"):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Only administrators can manage mod roles.", ephemeral=True
                )
                return
        elif not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        if setting == "view":
            settings = await self.config.guild(interaction.guild).all()
            channel = (
                interaction.guild.get_channel(settings["mod_log_channel"])
                if settings["mod_log_channel"]
                else None
            )

            # Get mod roles
            mod_role_mentions = []
            for role_id in settings.get("mod_roles", []):
                r = interaction.guild.get_role(role_id)
                if r:
                    mod_role_mentions.append(r.mention)

            embed = discord.Embed(
                title="🔗 ShadyAlts Settings",
                color=discord.Color.blurple()
            )
            embed.add_field(
                name="Mod Log Channel",
                value=channel.mention if channel else "Not set",
                inline=True
            )
            embed.add_field(
                name="Notify on Join",
                value="✅ Yes" if settings["notify_on_join"] else "❌ No",
                inline=True
            )
            embed.add_field(
                name="Notify on Leave",
                value="✅ Yes" if settings["notify_on_leave"] else "❌ No",
                inline=True
            )
            embed.add_field(
                name="Mod Roles",
                value=", ".join(mod_role_mentions) if mod_role_mentions else "None (admins + mod perms only)",
                inline=False
            )
            embed.add_field(
                name="Total Alt Records",
                value=str(len(settings.get("alts", []))),
                inline=True
            )

            embed.set_footer(text=f"v{self.__version__}")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif setting == "channel":
            view = ChannelSelectView(self)
            await interaction.response.send_message(
                "Select the mod log channel:", view=view, ephemeral=True
            )

        elif setting == "joinnotify":
            current = await self.config.guild(interaction.guild).notify_on_join()
            await self.config.guild(interaction.guild).notify_on_join.set(not current)
            status = "enabled" if not current else "disabled"
            await interaction.response.send_message(
                f"✅ Join notifications {status}.", ephemeral=True
            )

        elif setting == "leavenotify":
            current = await self.config.guild(interaction.guild).notify_on_leave()
            await self.config.guild(interaction.guild).notify_on_leave.set(not current)
            status = "enabled" if not current else "disabled"
            await interaction.response.send_message(
                f"✅ Leave notifications {status}.", ephemeral=True
            )

        elif setting == "addrole":
            if not role:
                await interaction.response.send_message(
                    "Please specify a role to add.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id in roles:
                    await interaction.response.send_message(
                        f"❌ {role.mention} is already a mod role.", ephemeral=True
                    )
                    return
                roles.append(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can now manage alts.", ephemeral=True
            )

        elif setting == "removerole":
            if not role:
                await interaction.response.send_message(
                    "Please specify a role to remove.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id not in roles:
                    await interaction.response.send_message(
                        f"❌ {role.mention} is not a mod role.", ephemeral=True
                    )
                    return
                roles.remove(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can no longer manage alts.", ephemeral=True
            )


async def setup(bot: Red) -> None:
    await bot.add_cog(ShadyAlts(bot))