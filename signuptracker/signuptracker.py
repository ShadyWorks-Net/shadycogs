"""
SignupTracker Cog - Track reactions on announcement posts.
Creates an ordered list of users who react to announcements.

Features:
- Persistent storage (survives bot restarts)
- Signup history with statistics
- Export to CSV/JSON
- Signup deadlines with reminders
- Reaction order tracking (first-come-first-served)
"""
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify
from discord import app_commands
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
import io

log = logging.getLogger("red.shadycogs.signuptracker")

CONFIG_IDENTIFIER = 1234567893


def _default_active_signup() -> Dict[str, Any]:
    """Factory function for creating a blank active signup structure."""
    return {
        "announcement_id": None,
        "tracker_id": None,
        "reaction_order": [],
        "created_at": None,
        "deadline": None,
        "title": None,
        "reminder_sent": False,
    }


# ==================== UI COMPONENTS ====================


class SettingsModal(discord.ui.Modal, title="Tracker Settings"):
    """Modal for text-based settings."""

    tracker_title = discord.ui.TextInput(
        label="Default Title",
        placeholder="Signup Tracker",
        required=False,
        max_length=100,
    )

    deadline_hours = discord.ui.TextInput(
        label="Default Deadline (hours)",
        placeholder="Leave empty for no deadline",
        required=False,
        max_length=4,
    )

    def __init__(self, cog: "SignupTracker", current_title: str, current_deadline: Optional[int]):
        super().__init__()
        self.cog = cog
        self.tracker_title.default = current_title or "Signup Tracker"
        if current_deadline:
            self.deadline_hours.default = str(current_deadline)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.tracker_title.value.strip() or "Signup Tracker"
        await self.cog.config.guild(interaction.guild).tracker_title.set(title)

        deadline = None
        if self.deadline_hours.value.strip():
            try:
                deadline = int(self.deadline_hours.value.strip())
                if deadline <= 0:
                    deadline = None
            except ValueError:
                pass
        await self.cog.config.guild(interaction.guild).default_deadline_hours.set(deadline)

        await interaction.response.send_message(
            f"✅ Settings updated!\n**Title:** {title}\n**Default Deadline:** {f'{deadline} hours' if deadline else 'None'}",
            ephemeral=True
        )


class ChannelSearchModal(discord.ui.Modal, title="Set Channel"):
    """Modal for searching and setting a channel by name."""

    channel_name = discord.ui.TextInput(
        label="Channel Name (or ID)",
        placeholder="Type channel name to search (leave empty to clear)",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "SignupTracker", setting: str):
        super().__init__()
        self.cog = cog
        self.setting = setting  # "announcements" or "log"
        self.title = f"Set {setting.title()} Channel"

    async def on_submit(self, interaction: discord.Interaction):
        search = self.channel_name.value.strip()

        # Clear if empty
        if not search:
            if self.setting == "announcements":
                await self.cog.config.guild(interaction.guild).announcements_channel.set(None)
            else:
                await self.cog.config.guild(interaction.guild).log_channel.set(None)
            await interaction.response.send_message(f"✅ {self.setting.title()} channel cleared.", ephemeral=True)
            return

        # Get bot-visible channels
        bot_channels = [
            ch for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).send_messages
            and ch.permissions_for(interaction.guild.me).view_channel
        ]

        # Try to match by ID first
        if search.isdigit():
            channel = interaction.guild.get_channel(int(search))
            if channel and channel in bot_channels:
                if self.setting == "announcements":
                    await self.cog.config.guild(interaction.guild).announcements_channel.set(channel.id)
                else:
                    await self.cog.config.guild(interaction.guild).log_channel.set(channel.id)
                await interaction.response.send_message(f"✅ {self.setting.title()} channel set to {channel.mention}", ephemeral=True)
                return

        # Search by name (case-insensitive, partial match)
        search_lower = search.lower().lstrip('#')
        matches = [ch for ch in bot_channels if search_lower in ch.name.lower()]

        if not matches:
            await interaction.response.send_message(
                f"❌ No bot-accessible channels found matching `{search}`.\n"
                f"Make sure the bot has access to the channel.",
                ephemeral=True
            )
            return

        if len(matches) == 1:
            channel = matches[0]
            if self.setting == "announcements":
                await self.cog.config.guild(interaction.guild).announcements_channel.set(channel.id)
            else:
                await self.cog.config.guild(interaction.guild).log_channel.set(channel.id)
            await interaction.response.send_message(f"✅ {self.setting.title()} channel set to {channel.mention}", ephemeral=True)
            return

        # Multiple matches - show list
        match_list = "\n".join([f"• #{ch.name} ({ch.category.name if ch.category else 'No category'})" for ch in matches[:10]])
        if len(matches) > 10:
            match_list += f"\n... and {len(matches) - 10} more"

        await interaction.response.send_message(
            f"⚠️ Multiple channels match `{search}`:\n{match_list}\n\nPlease be more specific or use the channel ID.",
            ephemeral=True
        )


class SetupView(discord.ui.View):
    """Interactive setup view with channel search buttons and settings."""

    def __init__(self, cog: "SignupTracker", guild: discord.Guild, config: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config

    @discord.ui.button(label="Set Announcements Channel", style=discord.ButtonStyle.secondary, emoji="📢", row=0)
    async def announcements_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelSearchModal(self.cog, "announcements"))

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.secondary, emoji="📋", row=0)
    async def log_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelSearchModal(self.cog, "log"))

    @discord.ui.button(label="Edit Title & Deadline", style=discord.ButtonStyle.primary, emoji="⚙️", row=2)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.guild(interaction.guild).all()
        modal = SettingsModal(
            self.cog,
            config["tracker_title"],
            config["default_deadline_hours"]
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def enable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.guild(interaction.guild).enabled.set(True)
        await interaction.response.send_message("✅ Signup tracking **enabled**.", ephemeral=True)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, emoji="❌", row=2)
    async def disable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.guild(interaction.guild).enabled.set(False)
        await interaction.response.send_message("❌ Signup tracking **disabled**.", ephemeral=True)


# ==================== MAIN COG ====================


class SignupTracker(commands.Cog):
    """Track reactions on announcement posts with persistent storage and history."""

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "enabled": False,
            "announcements_channel": None,
            "log_channel": None,
            "tracker_title": "Signup Tracker",
            "default_deadline_hours": None,
            "active_signup": _default_active_signup(),
            "history": [],
            "max_history": 50,
            "mod_roles": [],
        }
        self.config.register_guild(**default_guild)
        self.deadline_task: Optional[asyncio.Task] = None

    async def is_authorized(self, ctx: commands.Context) -> bool:
        """Check if user has permission to manage signups."""
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
                # Match on channel name
                if current.lower() in channel.name.lower():
                    label = f"#{channel.name}"
                    if channel.category:
                        label = f"#{channel.name} ({channel.category.name})"
                    choices.append(app_commands.Choice(name=label[:100], value=str(channel.id)))

        # Sort by channel position and limit to 25
        choices.sort(key=lambda c: int(c.value))
        return choices[:25]

    async def cog_load(self) -> None:
        self.deadline_task = self.bot.loop.create_task(self._deadline_reminder_loop())
        log.info("SignupTracker loaded")

    async def cog_unload(self) -> None:
        if self.deadline_task:
            self.deadline_task.cancel()

    # ==================== BACKGROUND TASKS ====================

    async def _deadline_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self._check_deadline_reminder(guild)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in deadline reminder loop: {e}")
                await asyncio.sleep(60)

    async def _check_deadline_reminder(self, guild: discord.Guild) -> None:
        config = await self.config.guild(guild).all()
        active = config["active_signup"]

        if not active["announcement_id"] or not active["deadline"]:
            return
        if active["reminder_sent"]:
            return

        deadline = datetime.fromisoformat(active["deadline"])
        now = datetime.now(timezone.utc)
        time_left = deadline - now

        if timedelta(minutes=0) < time_left <= timedelta(hours=1):
            log_channel_id = config["log_channel"]
            if log_channel_id:
                try:
                    channel = guild.get_channel(log_channel_id)
                    if channel:
                        signup_count = len(active["reaction_order"])
                        minutes_left = int(time_left.total_seconds() // 60)
                        embed = discord.Embed(
                            title="⏰ Signup Deadline Reminder",
                            description=f"**{active['title'] or config['tracker_title']}** closes in {minutes_left} minutes!\n\nCurrent signups: **{signup_count}**",
                            color=discord.Color.orange()
                        )
                        await channel.send(embed=embed)
                        await self.config.guild(guild).active_signup.reminder_sent.set(True)
                except Exception as e:
                    log.error(f"Failed to send deadline reminder: {e}")

        elif time_left <= timedelta(minutes=0):
            await self._close_signup(guild, "Deadline reached")

    async def _close_signup(self, guild: discord.Guild, reason: str = "Manual close") -> None:
        config = await self.config.guild(guild).all()
        active = config["active_signup"]

        if not active["announcement_id"]:
            return

        history_entry = {
            "announcement_id": active["announcement_id"],
            "title": active["title"] or config["tracker_title"],
            "signups": active["reaction_order"].copy(),
            "signup_count": len(active["reaction_order"]),
            "created_at": active["created_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "deadline": active["deadline"],
            "close_reason": reason,
        }

        history = config["history"]
        history.insert(0, history_entry)
        if len(history) > config["max_history"]:
            history = history[:config["max_history"]]

        await self.config.guild(guild).history.set(history)
        await self.config.guild(guild).active_signup.set(_default_active_signup())

        log_channel_id = config["log_channel"]
        if log_channel_id:
            try:
                channel = guild.get_channel(log_channel_id)
                if channel:
                    embed = discord.Embed(
                        title="📋 Signup Closed",
                        description=f"**{history_entry['title']}**\n\nTotal signups: **{history_entry['signup_count']}**\nReason: {reason}",
                        color=discord.Color.green()
                    )
                    await channel.send(embed=embed)
            except Exception as e:
                log.error(f"Failed to send close notification: {e}")

        log.info(f"Closed signup for guild {guild.id}: {reason}")

    # ==================== HELPERS ====================

    async def format_tracker_message(self, guild_id: int) -> str:
        config = await self.config.guild_from_id(guild_id).all()
        active = config["active_signup"]
        title = active.get("title") or config["tracker_title"]
        reaction_order = active["reaction_order"]

        lines = [f"**{title}**"]

        if active["deadline"]:
            deadline = datetime.fromisoformat(active["deadline"])
            lines.append(f"⏰ Deadline: <t:{int(deadline.timestamp())}:R>")

        lines.append("")

        if not reaction_order:
            lines.append("No signups yet.")
        else:
            lines.extend([f"{idx+1}. <@{uid}>" for idx, uid in enumerate(reaction_order)])

        lines.append(f"\n*Total: {len(reaction_order)}*")
        return "\n".join(lines)

    async def update_tracker(self, guild_id: int, log_channel_id: int, tracker_id: int) -> None:
        if not log_channel_id or not tracker_id:
            return
        try:
            log_channel = self.bot.get_channel(log_channel_id)
            if not log_channel:
                log_channel = await self.bot.fetch_channel(log_channel_id)
            message = await log_channel.fetch_message(tracker_id)
            content = await self.format_tracker_message(guild_id)
            await message.edit(content=content)
        except Exception as e:
            log.error(f"Failed to update tracker: {e}")

    # ==================== LISTENERS ====================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        config = await self.config.guild(message.guild).all()

        if not config["enabled"] or not config["announcements_channel"]:
            return
        if message.channel.id != config["announcements_channel"]:
            return

        if config["active_signup"]["announcement_id"]:
            await self._close_signup(message.guild, "New announcement posted")

        deadline = None
        if config["default_deadline_hours"]:
            deadline = datetime.now(timezone.utc) + timedelta(hours=config["default_deadline_hours"])

        active_signup = _default_active_signup()
        active_signup["announcement_id"] = message.id
        active_signup["created_at"] = datetime.now(timezone.utc).isoformat()
        active_signup["deadline"] = deadline.isoformat() if deadline else None
        await self.config.guild(message.guild).active_signup.set(active_signup)

        log_channel_id = config["log_channel"]
        if log_channel_id:
            try:
                log_channel = self.bot.get_channel(log_channel_id)
                if not log_channel:
                    log_channel = await self.bot.fetch_channel(log_channel_id)

                content = await self.format_tracker_message(message.guild.id)
                tracker_msg = await log_channel.send(content)
                await self.config.guild(message.guild).active_signup.tracker_id.set(tracker_msg.id)
            except Exception as e:
                log.error(f"Failed to create tracker: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return

        config = await self.config.guild_from_id(payload.guild_id).all()
        if not config["enabled"]:
            return

        active = config["active_signup"]
        if payload.message_id != active["announcement_id"]:
            return

        if active["deadline"]:
            deadline = datetime.fromisoformat(active["deadline"])
            if datetime.now(timezone.utc) > deadline:
                return

        if payload.user_id in active["reaction_order"]:
            return

        async with self.config.guild_from_id(payload.guild_id).active_signup.reaction_order() as order:
            order.append(payload.user_id)

        config = await self.config.guild_from_id(payload.guild_id).all()
        await self.update_tracker(payload.guild_id, config["log_channel"], config["active_signup"]["tracker_id"])

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        config = await self.config.guild_from_id(payload.guild_id).all()
        if not config["enabled"]:
            return

        active = config["active_signup"]
        if payload.message_id != active["announcement_id"]:
            return

        if payload.user_id not in active["reaction_order"]:
            return

        async with self.config.guild_from_id(payload.guild_id).active_signup.reaction_order() as order:
            if payload.user_id in order:
                order.remove(payload.user_id)

        config = await self.config.guild_from_id(payload.guild_id).all()
        await self.update_tracker(payload.guild_id, config["log_channel"], config["active_signup"]["tracker_id"])

    # ==================== COMMANDS ====================

    @commands.hybrid_group(name="signuptracker", aliases=["st"])
    @commands.guild_only()
    async def signuptracker(self, ctx: commands.Context):
        """Manage signup tracking for announcements."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to manage signup tracker.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @signuptracker.command(name="setup")
    async def signuptracker_setup(self, ctx: commands.Context):
        """Interactive setup for signup tracker."""
        config = await self.config.guild(ctx.guild).all()

        embed = discord.Embed(
            title="📋 SignupTracker Setup",
            description="Configure signup tracking using the controls below.",
            color=discord.Color.blue()
        )

        status = "✅ Enabled" if config["enabled"] else "❌ Disabled"
        ann_ch = f"<#{config['announcements_channel']}>" if config["announcements_channel"] else "Not set"
        log_ch = f"<#{config['log_channel']}>" if config["log_channel"] else "Not set"
        deadline = f"{config['default_deadline_hours']} hours" if config["default_deadline_hours"] else "None"

        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Announcements", value=ann_ch, inline=True)
        embed.add_field(name="Log Channel", value=log_ch, inline=True)
        embed.add_field(name="Title", value=config["tracker_title"], inline=True)
        embed.add_field(name="Default Deadline", value=deadline, inline=True)

        view = SetupView(self, ctx.guild, config)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @signuptracker.command(name="status")
    async def signuptracker_status(self, ctx: commands.Context):
        """Show current signup tracker status."""
        config = await self.config.guild(ctx.guild).all()
        active = config["active_signup"]

        embed = discord.Embed(title="📋 SignupTracker Status", color=discord.Color.blue())
        embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
        embed.add_field(name="Title", value=config["tracker_title"], inline=True)

        if config["default_deadline_hours"]:
            embed.add_field(name="Default Deadline", value=f"{config['default_deadline_hours']} hours", inline=True)

        ann_channel = config["announcements_channel"]
        embed.add_field(name="Announcements", value=f"<#{ann_channel}>" if ann_channel else "Not set", inline=True)

        log_channel = config["log_channel"]
        embed.add_field(name="Log Channel", value=f"<#{log_channel}>" if log_channel else "Not set", inline=True)

        if active["announcement_id"]:
            tracking_info = f"Signups: **{len(active['reaction_order'])}**"
            if active["deadline"]:
                deadline = datetime.fromisoformat(active["deadline"])
                tracking_info += f"\nDeadline: <t:{int(deadline.timestamp())}:R>"
            embed.add_field(name="📋 Active Signup", value=tracking_info, inline=False)
        else:
            embed.add_field(name="📋 Active Signup", value="None - waiting for announcement", inline=False)

        embed.add_field(name="History", value=f"{len(config['history'])} entries", inline=True)

        await ctx.send(embed=embed)

    @signuptracker.command(name="history")
    @app_commands.describe(limit="Number of entries to show (default: 10)")
    async def signuptracker_history(self, ctx: commands.Context, limit: int = 10):
        """View signup history."""
        history = await self.config.guild(ctx.guild).history()

        if not history:
            await ctx.send("📜 No signup history yet.")
            return

        embed = discord.Embed(title="📜 Signup History", color=discord.Color.blue())

        for entry in history[:limit]:
            created = datetime.fromisoformat(entry["created_at"])
            closed = datetime.fromisoformat(entry["closed_at"])

            value = f"Signups: **{entry['signup_count']}**\n"
            value += f"Created: <t:{int(created.timestamp())}:f>\n"
            value += f"Closed: <t:{int(closed.timestamp())}:f>"

            embed.add_field(name=entry["title"], value=value, inline=False)

        embed.set_footer(text=f"Showing {min(limit, len(history))} of {len(history)} entries")
        await ctx.send(embed=embed)

    @signuptracker.command(name="stats")
    async def signuptracker_stats(self, ctx: commands.Context):
        """View signup statistics."""
        history = await self.config.guild(ctx.guild).history()
        active = await self.config.guild(ctx.guild).active_signup()

        if not history and not active["announcement_id"]:
            await ctx.send("📊 No data to analyze yet.")
            return

        total_signups = sum(entry["signup_count"] for entry in history)
        total_events = len(history)
        avg_signups = total_signups / total_events if total_events > 0 else 0

        user_counts = {}
        for entry in history:
            for user_id in entry["signups"]:
                user_counts[user_id] = user_counts.get(user_id, 0) + 1

        top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        embed = discord.Embed(title="📊 Signup Statistics", color=discord.Color.green())
        embed.add_field(name="Total Events", value=str(total_events), inline=True)
        embed.add_field(name="Total Signups", value=str(total_signups), inline=True)
        embed.add_field(name="Average", value=f"{avg_signups:.1f}", inline=True)

        if active["announcement_id"]:
            embed.add_field(name="Current", value=f"{len(active['reaction_order'])} signups", inline=True)

        if top_users:
            top_list = "\n".join([f"<@{uid}>: {count}" for uid, count in top_users])
            embed.add_field(name="🏆 Most Active", value=top_list, inline=False)

        await ctx.send(embed=embed)

    @signuptracker.command(name="export")
    @app_commands.describe(format="Export format (json or csv)")
    async def signuptracker_export(self, ctx: commands.Context, format: str = "json"):
        """Export signups to JSON or CSV."""
        format = format.lower()
        if format not in ("json", "csv"):
            await ctx.send("❌ Format must be 'json' or 'csv'.")
            return

        config = await self.config.guild(ctx.guild).all()
        active = config["active_signup"]
        history = config["history"]

        if active["announcement_id"]:
            data = [{
                "title": active["title"] or config["tracker_title"],
                "signups": active["reaction_order"],
                "signup_count": len(active["reaction_order"]),
                "created_at": active["created_at"],
                "deadline": active["deadline"],
                "status": "active"
            }] + history
            filename = "signups_all"
        else:
            data = history
            filename = "signups_history"

        if not data:
            await ctx.send("❌ No data to export.")
            return

        if format == "json":
            content = json.dumps(data, indent=2)
            file = discord.File(io.BytesIO(content.encode()), filename=f"{filename}.json")
        else:
            lines = ["title,signup_order,user_id,created_at,closed_at"]
            for entry in data:
                for idx, user_id in enumerate(entry["signups"]):
                    lines.append(f'"{entry["title"]}",{idx+1},{user_id},{entry.get("created_at", "")},{entry.get("closed_at", "")}')
            content = "\n".join(lines)
            file = discord.File(io.BytesIO(content.encode()), filename=f"{filename}.csv")

        await ctx.send(f"📤 Exported {len(data)} record(s):", file=file)

    @signuptracker.command(name="addrole")
    @app_commands.describe(role="Role that can manage signup tracker")
    async def signuptracker_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role that can manage signup tracker."""
        if not ctx.author.guild_permissions.administrator:
            if not await self.bot.is_owner(ctx.author):
                await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
                return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                await ctx.send(f"{role.mention} is already a mod role.", ephemeral=True)
                return
            roles.append(role.id)

        await ctx.send(f"✅ {role.mention} can now manage signup tracker.")

    @signuptracker.command(name="removerole")
    @app_commands.describe(role="Role to remove from management")
    async def signuptracker_removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from signup tracker management."""
        if not ctx.author.guild_permissions.administrator:
            if not await self.bot.is_owner(ctx.author):
                await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
                return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id not in roles:
                await ctx.send(f"{role.mention} is not a mod role.", ephemeral=True)
                return
            roles.remove(role.id)

        await ctx.send(f"✅ {role.mention} removed from management.")

    @signuptracker.command(name="listroles")
    @app_commands.describe()
    async def signuptracker_listroles(self, ctx: commands.Context):
        """List all roles that can manage signup tracker."""
        mod_roles = await self.config.guild(ctx.guild).mod_roles()

        if not mod_roles:
            await ctx.send("No mod roles configured. Admins and users with `manage_guild` permission only.")
            return

        role_mentions = []
        for role_id in mod_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
            else:
                role_mentions.append(f"Unknown ({role_id})")

        embed = discord.Embed(
            title="📋 SignupTracker Mod Roles",
            description="\n".join(role_mentions),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Admins and manage_guild permission can always manage signups")
        await ctx.send(embed=embed)

    @signuptracker.command(name="announcements")
    @app_commands.describe(channel="Channel where announcements are posted (bot-visible channels)")
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def signuptracker_announcements(self, ctx: commands.Context, channel: str = None):
        """Set the announcements channel (where signup posts are made)."""
        if channel is None:
            await self.config.guild(ctx.guild).announcements_channel.set(None)
            await ctx.send("✅ Announcements channel cleared.")
            return

        try:
            channel_id = int(channel)
            ch = ctx.guild.get_channel(channel_id)
            if not ch:
                await ctx.send("Channel not found.", ephemeral=True)
                return
            await self.config.guild(ctx.guild).announcements_channel.set(channel_id)
            await ctx.send(f"✅ Announcements channel set to {ch.mention}")
        except ValueError:
            await ctx.send("Invalid channel.", ephemeral=True)

    @signuptracker.command(name="logchannel")
    @app_commands.describe(channel="Channel for signup tracker messages (bot-visible channels)")
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def signuptracker_logchannel(self, ctx: commands.Context, channel: str = None):
        """Set the log channel (where tracker messages are posted)."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("✅ Log channel cleared.")
            return

        try:
            channel_id = int(channel)
            ch = ctx.guild.get_channel(channel_id)
            if not ch:
                await ctx.send("Channel not found.", ephemeral=True)
                return
            await self.config.guild(ctx.guild).log_channel.set(channel_id)
            await ctx.send(f"✅ Log channel set to {ch.mention}")
        except ValueError:
            await ctx.send("Invalid channel.", ephemeral=True)
