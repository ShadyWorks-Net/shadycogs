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
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
import io

log = logging.getLogger("red.shadycogs.signuptracker")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 1234567893


def _default_active_signup() -> Dict[str, Any]:
    """Factory function for creating a blank active signup structure."""
    return {
        "announcement_id": None,
        "tracker_id": None,
        "reaction_order": [],  # List of user IDs in signup order
        "created_at": None,
        "deadline": None,  # ISO timestamp
        "title": None,  # Custom title for this signup
        "reminder_sent": False,
    }


class SignupTracker(commands.Cog):
    """Track reactions on announcement posts with persistent storage and history."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "enabled": False,
            "announcements_channel": None,
            "log_channel": None,
            "tracker_title": "Signup Tracker",
            "show_numbers": True,
            "default_deadline_hours": None,  # Optional default deadline for new signups
            "active_signup": _default_active_signup(),
            # History of completed signups
            "history": [],  # List of completed signup records
            "max_history": 50,  # Max history entries to keep
        }
        self.config.register_guild(**default_guild)

        # Background task for deadline reminders
        self.deadline_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Start background tasks when cog loads."""
        self.deadline_task = self.bot.loop.create_task(self._deadline_reminder_loop())
        log.info("SignupTracker loaded, deadline reminder task started")

    async def cog_unload(self) -> None:
        """Clean up background tasks."""
        if self.deadline_task:
            self.deadline_task.cancel()

    async def _deadline_reminder_loop(self) -> None:
        """Background task to check for upcoming deadlines and send reminders."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self._check_deadline_reminder(guild)
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in deadline reminder loop: {e}")
                await asyncio.sleep(60)

    async def _check_deadline_reminder(self, guild: discord.Guild) -> None:
        """Check if we should send a deadline reminder for this guild."""
        config = await self.config.guild(guild).all()
        active = config["active_signup"]

        if not active["announcement_id"] or not active["deadline"]:
            return
        if active["reminder_sent"]:
            return

        deadline = datetime.fromisoformat(active["deadline"])
        now = datetime.now(timezone.utc)
        time_left = deadline - now

        # Send reminder 1 hour before deadline
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
                        log.info(f"Sent deadline reminder for guild {guild.id}")
                except Exception as e:
                    log.error(f"Failed to send deadline reminder: {e}")

        # Close signup if deadline passed
        elif time_left <= timedelta(minutes=0):
            await self._close_signup(guild, "Deadline reached")

    async def _close_signup(self, guild: discord.Guild, reason: str = "Manual close") -> None:
        """Archive current signup to history and reset."""
        config = await self.config.guild(guild).all()
        active = config["active_signup"]

        if not active["announcement_id"]:
            return

        # Create history entry
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

        # Add to history (limit to max_history)
        history = config["history"]
        history.insert(0, history_entry)
        if len(history) > config["max_history"]:
            history = history[:config["max_history"]]

        await self.config.guild(guild).history.set(history)

        # Reset active signup
        await self.config.guild(guild).active_signup.set(_default_active_signup())

        # Send close notification
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

    async def format_tracker_message(self, guild_id: int) -> str:
        """Format the tracker message content with current signups."""
        config = await self.config.guild_from_id(guild_id).all()
        active = config["active_signup"]
        title = active.get("title") or config["tracker_title"]
        show_numbers = config["show_numbers"]
        reaction_order = active["reaction_order"]

        lines = [f"**{title}**"]

        # Add deadline info if set
        if active["deadline"]:
            deadline = datetime.fromisoformat(active["deadline"])
            lines.append(f"⏰ Deadline: <t:{int(deadline.timestamp())}:R>")

        lines.append("")  # Empty line

        if not reaction_order:
            lines.append("No signups yet.")
        else:
            if show_numbers:
                lines.extend([f"{idx+1}. <@{uid}>" for idx, uid in enumerate(reaction_order)])
            else:
                lines.extend([f"<@{uid}>" for uid in reaction_order])

        lines.append(f"\n*Total: {len(reaction_order)}*")

        return "\n".join(lines)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Create tracker when new announcement is posted."""
        if not message.guild:
            return
        if message.author.bot:
            return

        config = await self.config.guild(message.guild).all()

        if not config["enabled"]:
            return
        if not config["announcements_channel"]:
            return
        if message.channel.id != config["announcements_channel"]:
            return

        # Close previous signup if active
        if config["active_signup"]["announcement_id"]:
            await self._close_signup(message.guild, "New announcement posted")

        # Calculate deadline if default is set
        deadline = None
        if config["default_deadline_hours"]:
            deadline = datetime.now(timezone.utc) + timedelta(hours=config["default_deadline_hours"])

        # Create new active signup
        active_signup = _default_active_signup()
        active_signup["announcement_id"] = message.id
        active_signup["created_at"] = datetime.now(timezone.utc).isoformat()
        active_signup["deadline"] = deadline.isoformat() if deadline else None
        await self.config.guild(message.guild).active_signup.set(active_signup)

        # Create tracker message in log channel
        log_channel_id = config["log_channel"]
        if log_channel_id:
            try:
                log_channel = self.bot.get_channel(log_channel_id)
                if not log_channel:
                    log_channel = await self.bot.fetch_channel(log_channel_id)

                content = await self.format_tracker_message(message.guild.id)
                tracker_msg = await log_channel.send(content)
                await self.config.guild(message.guild).active_signup.tracker_id.set(tracker_msg.id)
                log.info(f"Created tracker for announcement {message.id} in guild {message.guild.id}")
            except Exception as e:
                log.error(f"Failed to create tracker: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track when user reacts to announcement."""
        if not payload.guild_id:
            return
        if payload.user_id == self.bot.user.id:
            return

        config = await self.config.guild_from_id(payload.guild_id).all()
        if not config["enabled"]:
            return

        active = config["active_signup"]
        if payload.message_id != active["announcement_id"]:
            return

        # Check deadline
        if active["deadline"]:
            deadline = datetime.fromisoformat(active["deadline"])
            if datetime.now(timezone.utc) > deadline:
                return  # Signup closed

        # Already tracked
        if payload.user_id in active["reaction_order"]:
            return

        # Add user to reaction order
        async with self.config.guild_from_id(payload.guild_id).active_signup.reaction_order() as order:
            order.append(payload.user_id)

        # Reload config to get updated tracker_id
        config = await self.config.guild_from_id(payload.guild_id).all()
        await self.update_tracker(payload.guild_id, config["log_channel"], config["active_signup"]["tracker_id"])

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Track when user removes reaction from announcement."""
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

        # Remove user from reaction order
        async with self.config.guild_from_id(payload.guild_id).active_signup.reaction_order() as order:
            if payload.user_id in order:
                order.remove(payload.user_id)

        # Reload config to get updated tracker_id
        config = await self.config.guild_from_id(payload.guild_id).all()
        await self.update_tracker(payload.guild_id, config["log_channel"], config["active_signup"]["tracker_id"])

    async def update_tracker(self, guild_id: int, log_channel_id: int, tracker_id: int) -> None:
        """Update the tracker message with current reaction order."""
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

    # ==================== ADMIN COMMANDS ====================

    @commands.hybrid_group(name="signuptracker", aliases=["st"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def signuptracker(self, ctx: commands.Context):
        """Manage signup tracking for announcements."""
        pass

    @signuptracker.command(name="enable")
    @app_commands.describe(enabled="Enable or disable signup tracking")
    async def signuptracker_enable(self, ctx: commands.Context, enabled: bool):
        """Enable or disable signup tracking."""
        await self.config.guild(ctx.guild).enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Signup tracking {status}.")

    @signuptracker.command(name="announcements")
    @app_commands.describe(channel="Channel to watch for new announcements")
    async def signuptracker_announcements(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the announcements channel to watch for new posts."""
        await self.config.guild(ctx.guild).announcements_channel.set(channel.id)
        await ctx.send(f"✅ Announcements channel set to {channel.mention}.")

    @signuptracker.command(name="log")
    @app_commands.describe(channel="Channel or thread for tracker messages")
    async def signuptracker_log(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        """Set the channel/thread where tracker messages are posted."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.")

    @signuptracker.command(name="title")
    @app_commands.describe(title="Default title for tracker messages")
    async def signuptracker_title(self, ctx: commands.Context, *, title: str):
        """Set the default tracker message title."""
        await self.config.guild(ctx.guild).tracker_title.set(title)
        await ctx.send(f"✅ Default tracker title set to: **{title}**")

    @signuptracker.command(name="numbers")
    @app_commands.describe(show="Show numbered list (true) or plain list (false)")
    async def signuptracker_numbers(self, ctx: commands.Context, show: bool):
        """Toggle numbered list (true) or plain list (false)."""
        await self.config.guild(ctx.guild).show_numbers.set(show)
        if show:
            await ctx.send("✅ Tracker will show numbered list.")
        else:
            await ctx.send("✅ Tracker will show plain list without numbers.")

    @signuptracker.command(name="deadline")
    @app_commands.describe(hours="Default deadline in hours (leave empty to disable)")
    async def signuptracker_deadline(self, ctx: commands.Context, hours: Optional[int] = None):
        """Set default deadline hours for new signups (leave empty to disable)."""
        await self.config.guild(ctx.guild).default_deadline_hours.set(hours)
        if hours:
            await ctx.send(f"✅ New signups will have a {hours} hour deadline by default.")
        else:
            await ctx.send("✅ Default deadline disabled. Signups will have no automatic deadline.")

    @signuptracker.command(name="setdeadline")
    @app_commands.describe(hours="Hours from now for the deadline")
    async def signuptracker_setdeadline(self, ctx: commands.Context, hours: int):
        """Set a deadline on the current active signup (hours from now)."""
        active = await self.config.guild(ctx.guild).active_signup()
        if not active["announcement_id"]:
            await ctx.send("❌ No active signup to set deadline on.")
            return

        deadline = datetime.now(timezone.utc) + timedelta(hours=hours)
        await self.config.guild(ctx.guild).active_signup.deadline.set(deadline.isoformat())
        await self.config.guild(ctx.guild).active_signup.reminder_sent.set(False)

        # Update tracker message
        config = await self.config.guild(ctx.guild).all()
        await self.update_tracker(ctx.guild.id, config["log_channel"], config["active_signup"]["tracker_id"])

        await ctx.send(f"✅ Deadline set to <t:{int(deadline.timestamp())}:F> (<t:{int(deadline.timestamp())}:R>)")

    @signuptracker.command(name="settitle")
    @app_commands.describe(title="Custom title for the current signup")
    async def signuptracker_settitle(self, ctx: commands.Context, *, title: str):
        """Set a custom title for the current active signup."""
        active = await self.config.guild(ctx.guild).active_signup()
        if not active["announcement_id"]:
            await ctx.send("❌ No active signup to set title on.")
            return

        await self.config.guild(ctx.guild).active_signup.title.set(title)

        # Update tracker message
        config = await self.config.guild(ctx.guild).all()
        await self.update_tracker(ctx.guild.id, config["log_channel"], config["active_signup"]["tracker_id"])

        await ctx.send(f"✅ Active signup title set to: **{title}**")

    @signuptracker.command(name="close")
    async def signuptracker_close(self, ctx: commands.Context):
        """Close the current signup and archive it to history."""
        active = await self.config.guild(ctx.guild).active_signup()
        if not active["announcement_id"]:
            await ctx.send("❌ No active signup to close.")
            return

        await self._close_signup(ctx.guild, f"Closed by {ctx.author}")
        await ctx.send("✅ Signup closed and archived to history.")

    @signuptracker.command(name="status")
    async def signuptracker_status(self, ctx: commands.Context):
        """Show current signup tracker configuration and status."""
        config = await self.config.guild(ctx.guild).all()
        active = config["active_signup"]

        embed = discord.Embed(
            title="SignupTracker Configuration",
            color=discord.Color.blue()
        )
        embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
        embed.add_field(name="Show Numbers", value="✅ Yes" if config["show_numbers"] else "❌ No", inline=True)
        embed.add_field(name="Default Title", value=config["tracker_title"], inline=False)

        if config["default_deadline_hours"]:
            embed.add_field(name="Default Deadline", value=f"{config['default_deadline_hours']} hours", inline=True)
        else:
            embed.add_field(name="Default Deadline", value="None", inline=True)

        ann_channel = config["announcements_channel"]
        embed.add_field(
            name="Announcements Channel",
            value=f"<#{ann_channel}>" if ann_channel else "Not set",
            inline=True
        )

        log_channel = config["log_channel"]
        embed.add_field(
            name="Log Channel",
            value=f"<#{log_channel}>" if log_channel else "Not set",
            inline=True
        )

        # Current tracking info
        if active["announcement_id"]:
            tracking_info = f"Announcement ID: `{active['announcement_id']}`\nSignups: **{len(active['reaction_order'])}**"
            if active["deadline"]:
                deadline = datetime.fromisoformat(active["deadline"])
                tracking_info += f"\nDeadline: <t:{int(deadline.timestamp())}:R>"
            if active["title"]:
                tracking_info += f"\nTitle: {active['title']}"
            embed.add_field(name="📋 Active Signup", value=tracking_info, inline=False)
        else:
            embed.add_field(name="📋 Active Signup", value="None - waiting for announcement", inline=False)

        embed.add_field(name="History Entries", value=str(len(config["history"])), inline=True)

        await ctx.send(embed=embed)

    @signuptracker.command(name="reset")
    async def signuptracker_reset(self, ctx: commands.Context) -> None:
        """Reset current tracking without archiving to history."""
        await self.config.guild(ctx.guild).active_signup.set(_default_active_signup())
        await ctx.send("✅ Tracking data reset. Waiting for next announcement.")

    # ==================== HISTORY & STATS ====================

    @signuptracker.command(name="history")
    @app_commands.describe(limit="Number of entries to show (default: 10)")
    async def signuptracker_history(self, ctx: commands.Context, limit: int = 10):
        """View signup history."""
        history = await self.config.guild(ctx.guild).history()

        if not history:
            await ctx.send("📜 No signup history yet.")
            return

        embed = discord.Embed(
            title="📜 Signup History",
            color=discord.Color.blue()
        )

        for entry in history[:limit]:
            created = datetime.fromisoformat(entry["created_at"])
            closed = datetime.fromisoformat(entry["closed_at"])

            value = f"Signups: **{entry['signup_count']}**\n"
            value += f"Created: <t:{int(created.timestamp())}:f>\n"
            value += f"Closed: <t:{int(closed.timestamp())}:f>\n"
            value += f"Reason: {entry['close_reason']}"

            embed.add_field(
                name=entry["title"],
                value=value,
                inline=False
            )

        embed.set_footer(text=f"Showing {min(limit, len(history))} of {len(history)} entries")
        await ctx.send(embed=embed)

    @signuptracker.command(name="stats")
    async def signuptracker_stats(self, ctx: commands.Context):
        """View signup statistics and analytics."""
        history = await self.config.guild(ctx.guild).history()
        active = await self.config.guild(ctx.guild).active_signup()

        if not history and not active["announcement_id"]:
            await ctx.send("📊 No data to analyze yet.")
            return

        total_signups = sum(entry["signup_count"] for entry in history)
        total_events = len(history)
        avg_signups = total_signups / total_events if total_events > 0 else 0

        # Find most active users
        user_counts = {}
        for entry in history:
            for user_id in entry["signups"]:
                user_counts[user_id] = user_counts.get(user_id, 0) + 1

        top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        embed = discord.Embed(
            title="📊 Signup Statistics",
            color=discord.Color.green()
        )
        embed.add_field(name="Total Events", value=str(total_events), inline=True)
        embed.add_field(name="Total Signups", value=str(total_signups), inline=True)
        embed.add_field(name="Average per Event", value=f"{avg_signups:.1f}", inline=True)

        if active["announcement_id"]:
            embed.add_field(
                name="Current Event",
                value=f"{len(active['reaction_order'])} signups",
                inline=True
            )

        if top_users:
            top_list = "\n".join([f"<@{uid}>: {count} events" for uid, count in top_users])
            embed.add_field(name="🏆 Most Active Members", value=top_list, inline=False)

        await ctx.send(embed=embed)

    @signuptracker.command(name="viewsignups")
    @app_commands.describe(history_index="History index (0 = most recent)")
    async def signuptracker_viewsignups(self, ctx: commands.Context, history_index: int = 0):
        """View signups from a historical event (0 = most recent)."""
        history = await self.config.guild(ctx.guild).history()

        if not history:
            await ctx.send("📜 No signup history yet.")
            return

        if history_index < 0 or history_index >= len(history):
            await ctx.send(f"❌ Invalid index. Use 0-{len(history)-1}.")
            return

        entry = history[history_index]

        embed = discord.Embed(
            title=f"📋 {entry['title']}",
            color=discord.Color.blue()
        )

        if entry["signups"]:
            signup_list = "\n".join([f"{idx+1}. <@{uid}>" for idx, uid in enumerate(entry["signups"])])
            for page in pagify(signup_list, page_length=1024):
                embed.add_field(name="Signups", value=page, inline=False)
        else:
            embed.add_field(name="Signups", value="No signups", inline=False)

        created = datetime.fromisoformat(entry["created_at"])
        closed = datetime.fromisoformat(entry["closed_at"])
        embed.set_footer(text=f"Created: {created.strftime('%Y-%m-%d %H:%M')} | Closed: {closed.strftime('%Y-%m-%d %H:%M')}")

        await ctx.send(embed=embed)

    # ==================== EXPORT ====================

    @signuptracker.command(name="export")
    @app_commands.describe(
        format="Export format (json or csv)",
        history_index="Specific history entry index, or leave empty for current/all"
    )
    async def signuptracker_export(self, ctx: commands.Context, format: str = "json", history_index: Optional[int] = None):
        """Export signups to JSON or CSV."""
        format = format.lower()
        if format not in ("json", "csv"):
            await ctx.send("❌ Format must be 'json' or 'csv'.")
            return

        config = await self.config.guild(ctx.guild).all()
        active = config["active_signup"]
        history = config["history"]

        if history_index is not None:
            # Export specific history entry
            if history_index < 0 or history_index >= len(history):
                await ctx.send(f"❌ Invalid index. Use 0-{len(history)-1}.")
                return
            data = [history[history_index]]
            filename = f"signups_{history_index}"
        elif active["announcement_id"]:
            # Export current active signup
            data = [{
                "title": active["title"] or config["tracker_title"],
                "signups": active["reaction_order"],
                "signup_count": len(active["reaction_order"]),
                "created_at": active["created_at"],
                "deadline": active["deadline"],
                "status": "active"
            }]
            filename = "signups_current"
        else:
            # Export all history
            data = history
            filename = "signups_all"

        if format == "json":
            content = json.dumps(data, indent=2)
            file = discord.File(io.BytesIO(content.encode()), filename=f"{filename}.json")
        else:  # csv
            lines = ["title,signup_order,user_id,created_at,closed_at"]
            for entry in data:
                for idx, user_id in enumerate(entry["signups"]):
                    lines.append(f'"{entry["title"]}",{idx+1},{user_id},{entry.get("created_at", "")},{entry.get("closed_at", "")}')
            content = "\n".join(lines)
            file = discord.File(io.BytesIO(content.encode()), filename=f"{filename}.csv")

        await ctx.send(f"📤 Exported {len(data)} signup record(s):", file=file)

    @signuptracker.command(name="clearhistory")
    @app_commands.describe(confirm="Set to true to confirm deletion")
    async def signuptracker_clearhistory(self, ctx: commands.Context, confirm: bool = False):
        """Clear all signup history. Set confirm to true to proceed."""
        if not confirm:
            await ctx.send("⚠️ This will delete all signup history. Run `/signuptracker clearhistory confirm:True` to confirm.")
            return

        await self.config.guild(ctx.guild).history.set([])
        await ctx.send("✅ Signup history cleared.")
