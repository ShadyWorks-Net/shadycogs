"""
ShadyFlags - Temporary warning/flag system with account age auto-flagging.

Features:
- Manual flag creation with expiry dates
- Auto-flag new accounts based on age thresholds
- Flag review queue UI
- Flag statistics/metrics
- Configurable moderator roles
- Mod log channel integration
"""

import discord
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadyflags")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 260288776360820738


class AddFlagModal(discord.ui.Modal, title="Add Flag to User"):
    """Modal for adding flags by user ID."""

    user_id = discord.ui.TextInput(
        label="Discord User ID",
        placeholder="Enter the user's Discord ID...",
        required=True,
        max_length=20
    )

    notes = discord.ui.TextInput(
        label="Reason/Notes",
        placeholder="Why are you flagging this user?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    expiry_days = discord.ui.TextInput(
        label="Expiry (days)",
        placeholder="30",
        required=False,
        default="30",
        max_length=3
    )

    def __init__(self, cog: "ShadyFlags"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        """Handle flag submission."""
        try:
            uid = int(self.user_id.value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid user ID. Please provide a numeric Discord user ID.",
                ephemeral=True
            )
            return

        try:
            days = int(self.expiry_days.value) if self.expiry_days.value else 30
            if days < 1 or days > 365:
                days = 30
        except ValueError:
            days = 30

        flag_id = await self.cog.add_flag(
            interaction.guild.id,
            uid,
            interaction.user.id,
            self.notes.value,
            days
        )

        try:
            user = await self.cog.bot.fetch_user(uid)
            user_display = f"{user.name} ({uid})"
        except Exception:
            user_display = f"User ID: {uid}"

        embed = discord.Embed(
            title="✅ Flag Added",
            description=f"Flag added to {user_display}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Notes", value=self.notes.value, inline=False)
        embed.add_field(name="Flagged By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Expires", value=f"In {days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🚩 **Flag Added** by {interaction.user.mention}\n"
            f"**User:** <@{uid}> ({uid})\n"
            f"**Notes:** {self.notes.value}\n"
            f"**Expires:** {days} days"
        )


class ShadyFlags(commands.Cog):
    """Temporary warning/flag system with account age auto-flagging."""

    __version__ = "2.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "flags": [],
            "mod_log_channel": None,
            "flag_expiry_days": 30,
            "auto_flag_enabled": True,
            "threshold_critical_days": 1,
            "threshold_high_days": 7,
            "threshold_medium_days": 30,
            "flag_expiry_critical_days": 14,
            "flag_expiry_high_days": 7,
            "flag_expiry_medium_days": 3,
            "next_flag_id": 1,
            "mod_roles": [],  # List of role IDs that can manage flags
            # Statistics tracking
            "stats": {
                "total_flags_created": 0,
                "total_flags_cleared": 0,
                "total_auto_flags": 0,
            },
            # Flag decision log for ML training
            "flag_decisions": [],  # List of {flag_id, user_id, action, moderator_id, timestamp}
            "max_decision_history": 1000,
        }
        self.config.register_guild(**default_guild)

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage flags."""
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

        # Check for ban_members permission
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

    # ===== DATABASE METHODS =====

    async def add_flag(self, guild_id: int, user_id: int, moderator_id: int, reason: str, expiry_days: int, priority: str = "manual") -> int:
        """Add a flag to a user. Returns the flag ID."""
        async with self.config.guild_from_id(guild_id).all() as guild_data:
            flag_id = guild_data["next_flag_id"]
            guild_data["next_flag_id"] += 1

            expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat()

            flag = {
                "id": flag_id,
                "user_id": user_id,
                "moderator_id": moderator_id,
                "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at,
                "priority": priority
            }
            guild_data["flags"].append(flag)

            # Update statistics
            if "stats" not in guild_data:
                guild_data["stats"] = {"total_flags_created": 0, "total_flags_cleared": 0, "total_auto_flags": 0}
            guild_data["stats"]["total_flags_created"] += 1
            if priority != "manual":
                guild_data["stats"]["total_auto_flags"] += 1

            return flag_id

    async def record_flag_decision(self, guild_id: int, flag_id: int, user_id: int, action: str, moderator_id: int) -> None:
        """Record a flag decision for ML training data."""
        async with self.config.guild_from_id(guild_id).all() as guild_data:
            if "flag_decisions" not in guild_data:
                guild_data["flag_decisions"] = []

            decision = {
                "flag_id": flag_id,
                "user_id": user_id,
                "action": action,  # "cleared", "expired", "ban", "kick", "false_positive"
                "moderator_id": moderator_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            guild_data["flag_decisions"].append(decision)

            # Trim to max history
            max_history = guild_data.get("max_decision_history", 1000)
            if len(guild_data["flag_decisions"]) > max_history:
                guild_data["flag_decisions"] = guild_data["flag_decisions"][-max_history:]

    async def get_flags(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all active flags for a user."""
        await self._cleanup_expired_flags(guild_id)
        flags = await self.config.guild_from_id(guild_id).flags()
        now = datetime.now(timezone.utc)
        return [f for f in flags if f["user_id"] == user_id and datetime.fromisoformat(f["expires_at"]) > now]

    async def get_all_flagged(self, guild_id: int) -> List[dict]:
        """Get all flagged users with their flag counts."""
        await self._cleanup_expired_flags(guild_id)
        flags = await self.config.guild_from_id(guild_id).flags()
        now = datetime.now(timezone.utc)

        user_flags = {}
        for f in flags:
            if datetime.fromisoformat(f["expires_at"]) > now:
                uid = f["user_id"]
                if uid not in user_flags:
                    user_flags[uid] = {"user_id": uid, "flag_count": 0, "highest_priority": "manual"}
                user_flags[uid]["flag_count"] += 1
                priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
                if priority_order.get(f["priority"], 3) < priority_order.get(user_flags[uid]["highest_priority"], 3):
                    user_flags[uid]["highest_priority"] = f["priority"]

        return list(user_flags.values())

    async def clear_flags(self, guild_id: int, user_id: int) -> None:
        """Clear all flags for a user."""
        async with self.config.guild_from_id(guild_id).flags() as flags:
            flags[:] = [f for f in flags if f["user_id"] != user_id]

    async def remove_flag(self, guild_id: int, flag_id: int) -> Optional[dict]:
        """Remove a specific flag by ID."""
        async with self.config.guild_from_id(guild_id).flags() as flags:
            for i, f in enumerate(flags):
                if f["id"] == flag_id:
                    return flags.pop(i)
        return None

    async def _cleanup_expired_flags(self, guild_id: int) -> None:
        """Remove expired flags."""
        now = datetime.now(timezone.utc)
        async with self.config.guild_from_id(guild_id).flags() as flags:
            flags[:] = [f for f in flags if datetime.fromisoformat(f["expires_at"]) > now]

    def _build_flags_embed(
        self,
        flags: List[dict],
        user_display: str,
        avatar_url: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> discord.Embed:
        """Build an embed showing a user's flags."""
        embed = discord.Embed(
            title=f"🚩 Flags for {user_display}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if user_id:
            embed.add_field(name="User ID", value=str(user_id), inline=False)

        for flag in flags:
            created = datetime.fromisoformat(flag["created_at"])
            expires = datetime.fromisoformat(flag["expires_at"])
            days_left = (expires - datetime.now(timezone.utc)).days
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(flag["priority"], "🚩")

            value = f"**Reason:** {flag['reason']}\n"
            value += f"**Created:** <t:{int(created.timestamp())}:R>\n"
            value += f"**Expires:** <t:{int(expires.timestamp())}:R> ({days_left}d left)\n"
            value += f"**By:** <@{flag['moderator_id']}>"

            embed.add_field(name=f"{priority_emoji} Flag #{flag['id']}", value=value, inline=False)

        return embed

    async def log_to_mod_channel(self, guild: discord.Guild, message: str = None, embed: discord.Embed = None) -> None:
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

    # ===== AUTO-FLAG ON JOIN =====

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Auto-flag new accounts based on account age thresholds."""
        if member.bot:
            return

        enabled = await self.config.guild(member.guild).auto_flag_enabled()
        if not enabled:
            return

        guild_config = await self.config.guild(member.guild).all()
        threshold_critical_days = guild_config.get("threshold_critical_days", 1)
        threshold_high_days = guild_config["threshold_high_days"]
        threshold_medium_days = guild_config["threshold_medium_days"]

        account_age = datetime.now(timezone.utc) - member.created_at.replace(tzinfo=timezone.utc)
        account_age_days = account_age.days
        account_age_hours = account_age.total_seconds() / 3600

        priority = None
        expiry_days = 0

        if account_age_days < threshold_critical_days:
            priority = "critical"
            expiry_days = guild_config["flag_expiry_critical_days"]
            if account_age_hours < 24:
                age_display = f"{int(account_age_hours)} hours" if account_age_hours >= 1 else f"{int(account_age.total_seconds() / 60)} minutes"
            else:
                age_display = f"{account_age_days} days"
        elif account_age_days < threshold_high_days:
            priority = "high"
            expiry_days = guild_config["flag_expiry_high_days"]
            age_display = f"{account_age_days} days"
        elif account_age_days < threshold_medium_days:
            priority = "medium"
            expiry_days = guild_config["flag_expiry_medium_days"]
            age_display = f"{account_age_days} days"

        if not priority:
            return

        reason = f"[AUTO] New account detected - Account age: {age_display}"
        flag_id = await self.add_flag(member.guild.id, member.id, self.bot.user.id, reason, expiry_days, priority)

        priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(priority, "⚪")

        embed = discord.Embed(
            title=f"{priority_emoji} New Account Auto-Flagged",
            description=f"{member.mention} has joined with a very new account",
            color={"critical": discord.Color.red(), "high": discord.Color.orange(), "medium": discord.Color.gold()}.get(priority, discord.Color.greyple()),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="Account Age", value=age_display, inline=True)
        embed.add_field(name="Priority", value=priority.upper(), inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Flag Expires", value=f"In {expiry_days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await self.log_to_mod_channel(member.guild, embed=embed)

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="flag", description="Manage flags for server members")
    @app_commands.describe(
        action="Action to perform",
        user="User to flag/view/clear",
        flag_id="Flag ID number (required for Remove Flag)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Flag", value="add"),
        app_commands.Choice(name="View Flags", value="view"),
        app_commands.Choice(name="Remove Flag", value="remove"),
        app_commands.Choice(name="Clear All Flags", value="clear"),
    ])
    async def flag_cmd(self, interaction: discord.Interaction, action: str, user: discord.Member, flag_id: Optional[int] = None):
        """Flag management for server members."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if action == "add":
            # Open modal for adding flag
            modal = AddFlagMemberModal(self, user)
            await interaction.response.send_modal(modal)

        elif action == "view":
            flags = await self.get_flags(interaction.guild.id, user.id)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user.mention}", ephemeral=True)
                return

            embed = self._build_flags_embed(flags, user.display_name, user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "remove":
            if flag_id is None:
                await interaction.response.send_message(
                    "You must provide a `flag_id` to remove. Use `/flag view` to see flag IDs.",
                    ephemeral=True
                )
                return

            removed = await self.remove_flag(interaction.guild.id, flag_id)
            if not removed:
                await interaction.response.send_message(
                    f"Flag #{flag_id} not found. Use `/flag view` to see active flags.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"✅ Removed flag #{flag_id} from {user.mention}\n**Reason was:** {removed['reason']}",
                ephemeral=True
            )

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flag Removed** by {interaction.user.mention}\n"
                f"**User:** {user.mention}\n"
                f"**Flag ID:** {flag_id}\n"
                f"**Reason was:** {removed['reason']}"
            )

        elif action == "clear":
            flags = await self.get_flags(interaction.guild.id, user.id)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user.mention}", ephemeral=True)
                return

            count = len(flags)
            await self.clear_flags(interaction.guild.id, user.id)
            await interaction.response.send_message(f"✅ Cleared {count} flag(s) from {user.mention}", ephemeral=True)

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flags Cleared** by {interaction.user.mention}\n**User:** {user.mention}\n**Flags Removed:** {count}"
            )

    @app_commands.command(name="flagid", description="Manage flags by user ID (for users not in server)")
    @app_commands.describe(
        action="Action to perform",
        user_id="Discord User ID",
        flag_id="Flag ID number (required for Remove Flag)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Flag", value="add"),
        app_commands.Choice(name="View Flags", value="view"),
        app_commands.Choice(name="Remove Flag", value="remove"),
        app_commands.Choice(name="Clear All Flags", value="clear"),
    ])
    async def flagid_cmd(self, interaction: discord.Interaction, action: str, user_id: str, flag_id: Optional[int] = None):
        """Flag management by user ID."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(uid)
            user_display = user.name
        except Exception:
            user_display = f"User {uid}"

        if action == "add":
            modal = AddFlagModal(self)
            modal.user_id.default = user_id
            await interaction.response.send_modal(modal)

        elif action == "view":
            flags = await self.get_flags(interaction.guild.id, uid)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user_display}", ephemeral=True)
                return

            embed = self._build_flags_embed(flags, user_display, user_id=uid)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "remove":
            if flag_id is None:
                await interaction.response.send_message(
                    "You must provide a `flag_id` to remove. Use `/flagid view` to see flag IDs.",
                    ephemeral=True
                )
                return

            removed = await self.remove_flag(interaction.guild.id, flag_id)
            if not removed:
                await interaction.response.send_message(
                    f"Flag #{flag_id} not found. Use `/flagid view` to see active flags.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"✅ Removed flag #{flag_id} from {user_display}\n**Reason was:** {removed['reason']}",
                ephemeral=True
            )

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flag Removed** by {interaction.user.mention}\n"
                f"**User:** <@{uid}> ({uid})\n"
                f"**Flag ID:** {flag_id}\n"
                f"**Reason was:** {removed['reason']}"
            )

        elif action == "clear":
            flags = await self.get_flags(interaction.guild.id, uid)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user_display}", ephemeral=True)
                return

            count = len(flags)
            await self.clear_flags(interaction.guild.id, uid)
            await interaction.response.send_message(f"✅ Cleared {count} flag(s) from {user_display}", ephemeral=True)

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flags Cleared** by {interaction.user.mention}\n**User:** <@{uid}> ({uid})\n**Flags Removed:** {count}"
            )

    @app_commands.command(name="flagall", description="Show all flagged members")
    async def flagall_cmd(self, interaction: discord.Interaction):
        """Show all flagged members."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        flagged_users = await self.get_all_flagged(interaction.guild.id)

        if not flagged_users:
            await interaction.response.send_message("No members are currently flagged.", ephemeral=True)
            return

        priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
        flagged_users.sort(key=lambda x: (priority_order.get(x["highest_priority"], 3), -x["flag_count"]))

        embed = discord.Embed(title="🚩 Flagged Members", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))

        for user_data in flagged_users[:25]:
            member = interaction.guild.get_member(user_data["user_id"])
            name = member.mention if member else f"<@{user_data['user_id']}>"
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(user_data["highest_priority"], "🚩")
            embed.add_field(name=name, value=f"{priority_emoji} {user_data['flag_count']} flag(s)", inline=True)

        if len(flagged_users) > 25:
            embed.set_footer(text=f"Showing 25/{len(flagged_users)} flagged members")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="flagqueue", description="Review flagged users in a queue")
    async def flagqueue_cmd(self, interaction: discord.Interaction):
        """Show flag review queue with action buttons."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        flagged_users = await self.get_all_flagged(interaction.guild.id)

        if not flagged_users:
            await interaction.response.send_message("No members are currently flagged.", ephemeral=True)
            return

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
        flagged_users.sort(key=lambda x: (priority_order.get(x["highest_priority"], 3), -x["flag_count"]))

        # Show first user in queue
        first_user = flagged_users[0]
        flags = await self.get_flags(interaction.guild.id, first_user["user_id"])

        member = interaction.guild.get_member(first_user["user_id"])
        if member:
            user_display = f"{member.mention} ({member.name})"
            avatar_url = member.display_avatar.url
        else:
            try:
                user = await self.bot.fetch_user(first_user["user_id"])
                user_display = f"<@{first_user['user_id']}> ({user.name})"
                avatar_url = user.display_avatar.url
            except Exception:
                user_display = f"<@{first_user['user_id']}>"
                avatar_url = None

        priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(first_user["highest_priority"], "🚩")

        embed = discord.Embed(
            title=f"{priority_emoji} Flag Review Queue",
            description=f"Reviewing: {user_display}",
            color={"critical": discord.Color.red(), "high": discord.Color.orange(), "medium": discord.Color.gold()}.get(first_user["highest_priority"], discord.Color.blurple()),
            timestamp=datetime.now(timezone.utc)
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.add_field(name="User ID", value=str(first_user["user_id"]), inline=True)
        embed.add_field(name="Flag Count", value=str(first_user["flag_count"]), inline=True)
        embed.add_field(name="Queue Position", value=f"1 of {len(flagged_users)}", inline=True)

        for flag in flags[:5]:
            created = datetime.fromisoformat(flag["created_at"])
            embed.add_field(
                name=f"Flag #{flag['id']} - {flag['priority'].upper()}",
                value=f"{flag['reason']}\n*<t:{int(created.timestamp())}:R>*",
                inline=False
            )

        if len(flags) > 5:
            embed.add_field(name="...", value=f"And {len(flags) - 5} more flags", inline=False)

        view = FlagReviewView(self, first_user["user_id"], len(flagged_users))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="flagstats", description="View flag statistics")
    async def flagstats_cmd(self, interaction: discord.Interaction):
        """View flag statistics and metrics."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        settings = await self.config.guild(interaction.guild).all()
        flags = settings.get("flags", [])
        stats = settings.get("stats", {"total_flags_created": 0, "total_flags_cleared": 0, "total_auto_flags": 0})
        decisions = settings.get("flag_decisions", [])

        # Count active flags by priority
        now = datetime.now(timezone.utc)
        active_by_priority = {"critical": 0, "high": 0, "medium": 0, "manual": 0}
        for f in flags:
            if datetime.fromisoformat(f["expires_at"]) > now:
                priority = f.get("priority", "manual")
                active_by_priority[priority] = active_by_priority.get(priority, 0) + 1

        total_active = sum(active_by_priority.values())

        embed = discord.Embed(
            title="📊 Flag Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        # Current status
        embed.add_field(name="Active Flags", value=str(total_active), inline=True)
        embed.add_field(name="Total Created", value=str(stats.get("total_flags_created", 0)), inline=True)
        embed.add_field(name="Auto-Flags", value=str(stats.get("total_auto_flags", 0)), inline=True)

        # By priority
        priority_breakdown = (
            f"🔴 Critical: {active_by_priority['critical']}\n"
            f"🟠 High: {active_by_priority['high']}\n"
            f"🟡 Medium: {active_by_priority['medium']}\n"
            f"🚩 Manual: {active_by_priority['manual']}"
        )
        embed.add_field(name="By Priority", value=priority_breakdown, inline=False)

        # Decision breakdown
        if decisions:
            action_counts = {}
            for d in decisions:
                action = d.get("action", "unknown")
                action_counts[action] = action_counts.get(action, 0) + 1

            decision_text = "\n".join([f"• {action}: {count}" for action, count in action_counts.items()])
            embed.add_field(name="Decision History", value=decision_text or "No decisions recorded", inline=False)
        else:
            embed.add_field(name="Decision History", value="No decisions recorded yet", inline=False)

        embed.set_footer(text=f"v{self.__version__}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="flagset", description="Configure flag settings")
    @app_commands.describe(
        setting="Setting to configure",
        role="Role for add/remove role actions",
        channel="Channel for log channel setting (bot-visible channels)"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Set Log Channel", value="channel"),
        app_commands.Choice(name="Toggle Auto-Flag", value="autoflag"),
        app_commands.Choice(name="Set Thresholds", value="threshold"),
        app_commands.Choice(name="Set Flag Expiry", value="expiry"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
    ])
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def flagset_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None,
        channel: Optional[str] = None
    ):
        """Configure flag settings."""
        # For role management, require admin (or bot owner)
        if setting in ("addrole", "removerole"):
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Only administrators can manage mod roles.", ephemeral=True
                )
                return
        elif not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if setting == "view":
            settings = await self.config.guild(interaction.guild).all()
            channel = interaction.guild.get_channel(settings["mod_log_channel"]) if settings["mod_log_channel"] else None

            # Get mod roles
            mod_role_mentions = []
            for role_id in settings.get("mod_roles", []):
                r = interaction.guild.get_role(role_id)
                if r:
                    mod_role_mentions.append(r.mention)

            embed = discord.Embed(title="🚩 ShadyFlags Settings", color=discord.Color.blurple())
            embed.add_field(name="Mod Log Channel", value=channel.mention if channel else "Not set", inline=True)
            embed.add_field(name="Auto-Flag Enabled", value="✅ Yes" if settings["auto_flag_enabled"] else "❌ No", inline=True)
            embed.add_field(name="Default Expiry", value=f"{settings['flag_expiry_days']} days", inline=True)
            embed.add_field(
                name="Mod Roles",
                value=", ".join(mod_role_mentions) if mod_role_mentions else "None (admins + mod perms only)",
                inline=False
            )
            embed.add_field(
                name="Thresholds (flag if account younger than)",
                value=f"🔴 Critical: < {settings.get('threshold_critical_days', 1)} days\n"
                      f"🟠 High: < {settings['threshold_high_days']} days\n"
                      f"🟡 Medium: < {settings['threshold_medium_days']} days",
                inline=False
            )
            embed.add_field(
                name="Auto-Flag Expiry",
                value=f"🔴 Critical: {settings['flag_expiry_critical_days']} days\n"
                      f"🟠 High: {settings['flag_expiry_high_days']} days\n"
                      f"🟡 Medium: {settings['flag_expiry_medium_days']} days",
                inline=False
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

        elif setting == "autoflag":
            current = await self.config.guild(interaction.guild).auto_flag_enabled()
            await self.config.guild(interaction.guild).auto_flag_enabled.set(not current)
            status = "enabled" if not current else "disabled"
            await interaction.response.send_message(f"✅ Auto-flagging {status}.", ephemeral=True)

        elif setting == "threshold":
            modal = ThresholdModal(self)
            await interaction.response.send_modal(modal)

        elif setting == "expiry":
            modal = ExpiryModal(self)
            await interaction.response.send_modal(modal)

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
                f"✅ {role.mention} can now manage flags.", ephemeral=True
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
                f"✅ {role.mention} can no longer manage flags.", ephemeral=True
            )


class FlagReviewView(discord.ui.View):
    """View for flag review queue with action buttons."""

    def __init__(self, cog: "ShadyFlags", user_id: int, queue_size: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.queue_size = queue_size

    @discord.ui.button(label="Clear Flags", style=discord.ButtonStyle.success, emoji="✅")
    async def clear_flags(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Clear all flags for this user."""
        await self.cog.clear_flags(interaction.guild.id, self.user_id)
        await self.cog.record_flag_decision(
            interaction.guild.id, 0, self.user_id, "cleared", interaction.user.id
        )

        await interaction.response.send_message(
            f"✅ Cleared all flags for <@{self.user_id}>.",
            ephemeral=True
        )

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"✅ **Flags Cleared** by {interaction.user.mention}\n"
            f"**User:** <@{self.user_id}>\n"
            f"**Decision:** Cleared via review queue"
        )
        self.stop()

    @discord.ui.button(label="Mark False Positive", style=discord.ButtonStyle.secondary, emoji="❎")
    async def false_positive(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Mark as false positive and clear."""
        await self.cog.clear_flags(interaction.guild.id, self.user_id)
        await self.cog.record_flag_decision(
            interaction.guild.id, 0, self.user_id, "false_positive", interaction.user.id
        )

        await interaction.response.send_message(
            f"❎ Marked flags for <@{self.user_id}> as false positive and cleared.",
            ephemeral=True
        )

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"❎ **False Positive** by {interaction.user.mention}\n"
            f"**User:** <@{self.user_id}>\n"
            f"**Decision:** Marked as false positive"
        )
        self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip to next user in queue."""
        await interaction.response.send_message(
            "Skipped. Use `/flagqueue` to continue reviewing.",
            ephemeral=True
        )
        self.stop()


class AddFlagMemberModal(discord.ui.Modal, title="Add Flag"):
    """Modal for adding flag to a member."""

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why are you flagging this user?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    expiry_days = discord.ui.TextInput(
        label="Expiry (days)",
        placeholder="30",
        required=False,
        default="30",
        max_length=3
    )

    def __init__(self, cog: ShadyFlags, member: discord.Member):
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        try:
            days = int(self.expiry_days.value) if self.expiry_days.value else 30
            if days < 1 or days > 365:
                days = 30
        except ValueError:
            days = 30

        flag_id = await self.cog.add_flag(
            interaction.guild.id,
            self.member.id,
            interaction.user.id,
            self.reason.value,
            days
        )

        embed = discord.Embed(
            title="✅ Flag Added",
            description=f"Flag added to {self.member.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(name="Expires", value=f"In {days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🚩 **Flag Added** by {interaction.user.mention}\n**User:** {self.member.mention}\n**Reason:** {self.reason.value}"
        )


class ThresholdModal(discord.ui.Modal, title="Set Auto-Flag Thresholds"):
    """Modal for setting thresholds."""

    critical = discord.ui.TextInput(label="Critical (days)", placeholder="1", required=False, max_length=3)
    high = discord.ui.TextInput(label="High (days)", placeholder="7", required=False, max_length=3)
    medium = discord.ui.TextInput(label="Medium (days)", placeholder="30", required=False, max_length=3)

    def __init__(self, cog: ShadyFlags):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        updates = []

        if self.critical.value:
            try:
                val = int(self.critical.value)
                if 1 <= val <= 7:
                    await self.cog.config.guild(interaction.guild).threshold_critical_days.set(val)
                    updates.append(f"🔴 Critical: {val} days")
            except ValueError:
                pass

        if self.high.value:
            try:
                val = int(self.high.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).threshold_high_days.set(val)
                    updates.append(f"🟠 High: {val} days")
            except ValueError:
                pass

        if self.medium.value:
            try:
                val = int(self.medium.value)
                if 1 <= val <= 365:
                    await self.cog.config.guild(interaction.guild).threshold_medium_days.set(val)
                    updates.append(f"🟡 Medium: {val} days")
            except ValueError:
                pass

        if updates:
            await interaction.response.send_message(f"✅ Updated thresholds:\n" + "\n".join(updates), ephemeral=True)
        else:
            await interaction.response.send_message("No valid thresholds provided.", ephemeral=True)


class ExpiryModal(discord.ui.Modal, title="Set Auto-Flag Expiry"):
    """Modal for setting flag expiry by priority."""

    critical = discord.ui.TextInput(label="Critical flags expire after (days)", placeholder="14", required=False, max_length=3)
    high = discord.ui.TextInput(label="High flags expire after (days)", placeholder="7", required=False, max_length=3)
    medium = discord.ui.TextInput(label="Medium flags expire after (days)", placeholder="3", required=False, max_length=3)

    def __init__(self, cog: ShadyFlags):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        updates = []

        if self.critical.value:
            try:
                val = int(self.critical.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_critical_days.set(val)
                    updates.append(f"🔴 Critical: {val} days")
            except ValueError:
                pass

        if self.high.value:
            try:
                val = int(self.high.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_high_days.set(val)
                    updates.append(f"🟠 High: {val} days")
            except ValueError:
                pass

        if self.medium.value:
            try:
                val = int(self.medium.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_medium_days.set(val)
                    updates.append(f"🟡 Medium: {val} days")
            except ValueError:
                pass

        if updates:
            await interaction.response.send_message(f"✅ Updated expiry:\n" + "\n".join(updates), ephemeral=True)
        else:
            await interaction.response.send_message("No valid expiry values provided.", ephemeral=True)


async def setup(bot: Red) -> None:
    """Load the ShadyFlags cog."""
    await bot.add_cog(ShadyFlags(bot))