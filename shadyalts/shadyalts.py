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


class ShadyAlts(commands.Cog):
    """Alt account tracking and notifications with named networks."""

    __version__ = "3.0.0"
    __author__ = "ShadyTidus"

    # Pre-defined network names
    NETWORK_GENERAL = "general"      # Manual alt tracking
    NETWORK_SUSPECT = "suspect"      # Flagged by ML (from ShadyFlags)
    NETWORK_CONFIRMED = "confirmed"  # Banned for bot/spam (from ShadyFlags)

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            # Alt links - GUARANTEED alts (manually verified by mods)
            # Forms natural groups based on connections
            # List of {user_id, alt_id, reason, created_at, linked_by}
            "alts": [],

            # User status labels
            # suspect: {user_id: {reason, risk_score, added_at, ...}}
            # confirmed: {user_id: {reason, source, added_at, ...}}
            "suspects": {},      # Flagged by ML, pending mod action
            "confirmed": {},     # Banned for bot/spam - if one alt confirmed, ALL alts confirmed

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

    # ===== ALT LINK METHODS =====

    async def add_alt(
        self,
        guild_id: int,
        user_id: int,
        alt_id: int,
        reason: Optional[str] = None,
        linked_by: Optional[int] = None
    ) -> dict:
        """Add alt relationship (bidirectional). Returns status info.

        If either user is CONFIRMED, the other is auto-confirmed too.
        """
        result = {"added": False, "auto_confirmed": [], "warnings": []}

        async with self.config.guild_from_id(guild_id).alts() as alts:
            # Check if already linked
            exists = any(
                (a["user_id"] == user_id and a["alt_id"] == alt_id) or
                (a["user_id"] == alt_id and a["alt_id"] == user_id)
                for a in alts
            )

            if exists:
                result["warnings"].append("Already linked")
                return result

            entry1 = {
                "user_id": user_id,
                "alt_id": alt_id,
                "reason": reason,
                "linked_by": linked_by,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            entry2 = {
                "user_id": alt_id,
                "alt_id": user_id,
                "reason": reason,
                "linked_by": linked_by,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            alts.append(entry1)
            alts.append(entry2)
            result["added"] = True

        # Check if either user is confirmed → auto-confirm the other
        confirmed = await self.config.guild_from_id(guild_id).confirmed()

        user1_confirmed = str(user_id) in confirmed
        user2_confirmed = str(alt_id) in confirmed

        if user1_confirmed and not user2_confirmed:
            # User1 is confirmed, auto-confirm user2
            await self.add_confirmed(
                guild_id, alt_id,
                reason=f"Alt of confirmed bad actor {user_id}",
                source="alt_auto_confirm"
            )
            result["auto_confirmed"].append(alt_id)
            result["warnings"].append(f"User {alt_id} auto-confirmed (alt of confirmed {user_id})")

        elif user2_confirmed and not user1_confirmed:
            # User2 is confirmed, auto-confirm user1
            await self.add_confirmed(
                guild_id, user_id,
                reason=f"Alt of confirmed bad actor {alt_id}",
                source="alt_auto_confirm"
            )
            result["auto_confirmed"].append(user_id)
            result["warnings"].append(f"User {user_id} auto-confirmed (alt of confirmed {alt_id})")

        return result

    async def remove_alt(self, guild_id: int, user_id: int, alt_id: int) -> bool:
        """Remove alt relationship (bidirectional)."""
        async with self.config.guild_from_id(guild_id).alts() as alts:
            original_len = len(alts)
            alts[:] = [
                a for a in alts
                if not ((a["user_id"] == user_id and a["alt_id"] == alt_id) or
                        (a["user_id"] == alt_id and a["alt_id"] == user_id))
            ]
            return len(alts) < original_len

    async def get_alts(self, guild_id: int, user_id: int) -> List[dict]:
        """Get direct alts for a user."""
        alts = await self.config.guild_from_id(guild_id).alts()
        return [a for a in alts if a["user_id"] == user_id]

    async def get_alt_group(self, guild_id: int, user_id: int) -> List[int]:
        """Get the FULL alt group (all connected users) for a user.

        Uses BFS to find all users connected through alt links.
        E.g., if A↔B and B↔C, get_alt_group(A) returns [A, B, C]
        """
        alts = await self.config.guild_from_id(guild_id).alts()

        # Build adjacency list
        adj = {}
        for a in alts:
            uid, aid = a["user_id"], a["alt_id"]
            if uid not in adj:
                adj[uid] = set()
            adj[uid].add(aid)

        # BFS from user_id
        if user_id not in adj:
            return [user_id]  # No alts, just the user

        visited = set()
        queue = [user_id]
        visited.add(user_id)

        while queue:
            current = queue.pop(0)
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        return list(visited)

    async def is_alt(self, guild_id: int, user_id: int, alt_id: int) -> bool:
        """Check if two users are linked as alts (directly or in same group)."""
        group = await self.get_alt_group(guild_id, user_id)
        return alt_id in group

    # ===== SUSPECT METHODS =====

    async def add_suspect(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        risk_score: float = 0.0,
        **extra_data
    ) -> bool:
        """Add a user to the suspect list."""
        # Don't add if already confirmed
        confirmed = await self.config.guild_from_id(guild_id).confirmed()
        if str(user_id) in confirmed:
            return False

        async with self.config.guild_from_id(guild_id).suspects() as suspects:
            if str(user_id) in suspects:
                return False  # Already suspect

            suspects[str(user_id)] = {
                "user_id": user_id,
                "reason": reason,
                "risk_score": risk_score,
                "added_at": datetime.now(timezone.utc).isoformat(),
                **extra_data
            }
            return True

    async def remove_suspect(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Remove a user from suspects. Returns their data if removed."""
        async with self.config.guild_from_id(guild_id).suspects() as suspects:
            return suspects.pop(str(user_id), None)

    async def get_suspect(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Get suspect data for a user."""
        suspects = await self.config.guild_from_id(guild_id).suspects()
        return suspects.get(str(user_id))

    async def get_all_suspects(self, guild_id: int) -> List[dict]:
        """Get all suspects."""
        suspects = await self.config.guild_from_id(guild_id).suspects()
        return list(suspects.values())

    # ===== CONFIRMED METHODS =====

    async def add_confirmed(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        source: str = "manual",
        **extra_data
    ) -> dict:
        """Add a user to confirmed bad actors.

        Also confirms ALL their alts (guaranteed same person).
        Returns info about who was confirmed.
        """
        result = {"confirmed": [], "already_confirmed": [], "removed_from_suspect": []}

        # Get the full alt group
        alt_group = await self.get_alt_group(guild_id, user_id)

        # Remove from suspects if present
        suspect_data = await self.remove_suspect(guild_id, user_id)
        if suspect_data:
            result["removed_from_suspect"].append(user_id)

        async with self.config.guild_from_id(guild_id).confirmed() as confirmed:
            for uid in alt_group:
                if str(uid) in confirmed:
                    result["already_confirmed"].append(uid)
                    continue

                # Remove from suspects
                sus = await self.remove_suspect(guild_id, uid)
                if sus:
                    result["removed_from_suspect"].append(uid)

                # Add to confirmed
                confirmed[str(uid)] = {
                    "user_id": uid,
                    "reason": reason if uid == user_id else f"Alt of {user_id}: {reason}",
                    "source": source if uid == user_id else "alt_auto_confirm",
                    "confirmed_via": user_id if uid != user_id else None,
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    **extra_data
                }
                result["confirmed"].append(uid)

        return result

    async def remove_confirmed(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Remove a user from confirmed. Does NOT remove their alts."""
        async with self.config.guild_from_id(guild_id).confirmed() as confirmed:
            return confirmed.pop(str(user_id), None)

    async def get_confirmed(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Get confirmed data for a user."""
        confirmed = await self.config.guild_from_id(guild_id).confirmed()
        return confirmed.get(str(user_id))

    async def get_all_confirmed(self, guild_id: int) -> List[dict]:
        """Get all confirmed bad actors."""
        confirmed = await self.config.guild_from_id(guild_id).confirmed()
        return list(confirmed.values())

    async def is_confirmed(self, guild_id: int, user_id: int) -> bool:
        """Check if user is a confirmed bad actor."""
        confirmed = await self.config.guild_from_id(guild_id).confirmed()
        return str(user_id) in confirmed

    async def promote_suspect_to_confirmed(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        source: str = "mod_action"
    ) -> dict:
        """Move a user from suspect to confirmed (and all their alts)."""
        return await self.add_confirmed(guild_id, user_id, reason, source)

    # ===== STATS =====

    async def get_stats(self, guild_id: int) -> dict:
        """Get statistics."""
        alts = await self.config.guild_from_id(guild_id).alts()
        suspects = await self.config.guild_from_id(guild_id).suspects()
        confirmed = await self.config.guild_from_id(guild_id).confirmed()

        # Count unique users in alt links
        alt_users = set()
        for a in alts:
            alt_users.add(a["user_id"])
            alt_users.add(a["alt_id"])

        return {
            "alt_links": len(alts) // 2,  # Bidirectional
            "users_with_alts": len(alt_users),
            "suspects": len(suspects),
            "confirmed": len(confirmed),
        }

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

    # ===== NETWORK VIEW COMMANDS =====

    @app_commands.command(name="altnetwork", description="View suspect or confirmed bad actor networks")
    @app_commands.describe(
        network="Which network to view",
        user_id="Optional: Check specific user ID"
    )
    @app_commands.choices(network=[
        app_commands.Choice(name="View Suspects (ML flagged)", value="suspect"),
        app_commands.Choice(name="View Confirmed (banned bad actors)", value="confirmed"),
        app_commands.Choice(name="View Stats", value="stats"),
    ])
    async def altnetwork_cmd(
        self,
        interaction: discord.Interaction,
        network: str,
        user_id: Optional[str] = None
    ):
        """View suspect or confirmed networks."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        if network == "stats":
            stats = await self.get_stats(interaction.guild.id)

            embed = discord.Embed(
                title="📊 Alt Network Statistics",
                color=discord.Color.blue()
            )
            embed.add_field(name="Alt Links", value=str(stats["alt_links"]), inline=True)
            embed.add_field(name="Users with Alts", value=str(stats["users_with_alts"]), inline=True)
            embed.add_field(name="Suspects", value=str(stats["suspects"]), inline=True)
            embed.add_field(name="Confirmed Bad", value=str(stats["confirmed"]), inline=True)

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if user_id:
            # Check specific user
            try:
                uid = int(user_id.strip())
            except ValueError:
                await interaction.response.send_message("Invalid user ID.", ephemeral=True)
                return

            if network == "suspect":
                data = await self.get_suspect(interaction.guild.id, uid)
                if data:
                    embed = discord.Embed(
                        title="🔍 Suspect Found",
                        description=f"<@{uid}> (`{uid}`) is in the suspect network",
                        color=discord.Color.orange()
                    )
                    embed.add_field(name="Reason", value=data.get("reason", "N/A"), inline=False)
                    embed.add_field(name="Risk Score", value=f"{data.get('risk_score', 0)*100:.0f}%", inline=True)
                    embed.add_field(name="Added", value=f"<t:{int(datetime.fromisoformat(data.get('added_at', datetime.now(timezone.utc).isoformat())).timestamp())}:R>", inline=True)
                else:
                    embed = discord.Embed(
                        title="✅ Not a Suspect",
                        description=f"User `{uid}` is not in the suspect network",
                        color=discord.Color.green()
                    )
            else:  # confirmed
                data = await self.get_confirmed(interaction.guild.id, uid)
                if data:
                    embed = discord.Embed(
                        title="🚨 Confirmed Bad Actor",
                        description=f"<@{uid}> (`{uid}`) is a confirmed bad actor",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Reason", value=data.get("reason", "N/A"), inline=False)
                    embed.add_field(name="Source", value=data.get("source", "N/A"), inline=True)
                    if data.get("confirmed_via"):
                        embed.add_field(name="Confirmed Via", value=f"<@{data['confirmed_via']}>", inline=True)
                else:
                    embed = discord.Embed(
                        title="✅ Not Confirmed",
                        description=f"User `{uid}` is not in the confirmed network",
                        color=discord.Color.green()
                    )

            # Also show their alt group
            alt_group = await self.get_alt_group(interaction.guild.id, uid)
            if len(alt_group) > 1:
                alt_mentions = [f"<@{a}>" for a in alt_group if a != uid][:10]
                embed.add_field(
                    name=f"Alt Group ({len(alt_group)} users)",
                    value=", ".join(alt_mentions) or "None",
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # List all in network
        if network == "suspect":
            members = await self.get_all_suspects(interaction.guild.id)
            title = "🔍 Suspect Network"
            color = discord.Color.orange()
            empty_msg = "No suspects in the network.\nSuspects are added by ShadyFlags ML detection."
        else:  # confirmed
            members = await self.get_all_confirmed(interaction.guild.id)
            title = "🚨 Confirmed Bad Actors"
            color = discord.Color.red()
            empty_msg = "No confirmed bad actors.\nUsers are confirmed when banned for bot/spam."

        if not members:
            await interaction.response.send_message(empty_msg, ephemeral=True)
            return

        # Sort by added_at (most recent first)
        members.sort(key=lambda x: x.get("added_at", ""), reverse=True)

        embed = discord.Embed(title=title, color=color)

        # Show up to 20 members
        for m in members[:20]:
            uid = m.get("user_id")
            reason = m.get("reason", "N/A")[:50]

            # Try to get username
            try:
                user = await self.bot.fetch_user(uid)
                name = f"{user.name} ({uid})"
            except:
                name = f"User {uid}"

            if network == "suspect":
                risk = m.get("risk_score", 0)
                embed.add_field(
                    name=f"🔍 {name}",
                    value=f"Risk: {risk*100:.0f}%\n{reason}",
                    inline=True
                )
            else:
                source = m.get("source", "N/A")
                embed.add_field(
                    name=f"🚨 {name}",
                    value=f"Source: {source}\n{reason}",
                    inline=True
                )

        embed.set_footer(text=f"Total: {len(members)} | Showing {min(len(members), 20)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ===== SETTINGS =====

    @app_commands.command(name="altset", description="Configure alt tracking settings")
    @app_commands.describe(
        setting="Setting to configure",
        role="Role for add/remove role actions",
        channel="Channel for log channel setting (bot-visible channels)"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Set Log Channel", value="channel"),
        app_commands.Choice(name="Toggle Join Notifications", value="joinnotify"),
        app_commands.Choice(name="Toggle Leave Notifications", value="leavenotify"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
    ])
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def altset_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None,
        channel: Optional[str] = None
    ):
        """Configure alt tracking settings."""
        # For role management, require admin (or bot owner)
        if setting in ("addrole", "removerole"):
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
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
            if channel is None:
                await self.config.guild(interaction.guild).mod_log_channel.set(None)
                await interaction.response.send_message("✅ Mod log channel cleared.", ephemeral=True)
                return

            try:
                channel_id = int(channel)
                ch = interaction.guild.get_channel(channel_id)
                if not ch:
                    await interaction.response.send_message("Channel not found.", ephemeral=True)
                    return
                await self.config.guild(interaction.guild).mod_log_channel.set(channel_id)
                await interaction.response.send_message(f"✅ Mod log channel set to {ch.mention}", ephemeral=True)
            except ValueError:
                await interaction.response.send_message("Invalid channel.", ephemeral=True)

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