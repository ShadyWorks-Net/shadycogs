"""
ShadyAnnounce - Scheduled announcements with timezone support

Allows staff to schedule posts to channels using their personal timezone.
Features preview/edit workflow before final confirmation.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ui import Button, Modal, Select, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.shadycogs.shadyannounce")

CONFIG_IDENTIFIER = 0x53484144_59414E4E  # SHADYANN

# Common timezones for dropdown
COMMON_TIMEZONES = [
    # US
    ("America/New_York", "US Eastern (ET)"),
    ("America/Chicago", "US Central (CT)"),
    ("America/Denver", "US Mountain (MT)"),
    ("America/Los_Angeles", "US Pacific (PT)"),
    ("America/Anchorage", "US Alaska (AKT)"),
    ("Pacific/Honolulu", "US Hawaii (HT)"),
    # Europe
    ("Europe/London", "UK (GMT/BST)"),
    ("Europe/Paris", "Central Europe (CET)"),
    ("Europe/Berlin", "Germany (CET)"),
    ("Europe/Amsterdam", "Netherlands (CET)"),
    ("Europe/Helsinki", "Finland (EET)"),
    ("Europe/Moscow", "Moscow (MSK)"),
    # Asia/Pacific
    ("Asia/Tokyo", "Japan (JST)"),
    ("Asia/Shanghai", "China (CST)"),
    ("Asia/Singapore", "Singapore (SGT)"),
    ("Australia/Sydney", "Australia Eastern (AEST)"),
    ("Australia/Perth", "Australia Western (AWST)"),
    ("Pacific/Auckland", "New Zealand (NZST)"),
    # Other
    ("UTC", "UTC"),
]


def parse_datetime(text: str, user_tz: ZoneInfo) -> Optional[datetime]:
    """
    Parse user input into a datetime in the user's timezone.
    Supports formats like:
    - 2024-01-15 14:30
    - 01/15/2024 2:30 PM
    - 15-01-2024 14:30
    - Jan 15, 2024 2:30 PM
    - tomorrow 3pm
    - in 2 hours
    """
    text = text.strip().lower()
    now = datetime.now(user_tz)

    # Natural language: "in X hours/minutes/days"
    in_match = re.match(r"in\s+(\d+)\s*(hour|hr|minute|min|day)s?", text, re.IGNORECASE)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2).lower()
        if unit in ("hour", "hr"):
            return now + timedelta(hours=amount)
        elif unit in ("minute", "min"):
            return now + timedelta(minutes=amount)
        elif unit == "day":
            return now + timedelta(days=amount)

    # Natural language: "tomorrow Xpm/am" or "tomorrow X:XX pm/am"
    tomorrow_match = re.match(
        r"tomorrow\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.IGNORECASE
    )
    if tomorrow_match:
        hour = int(tomorrow_match.group(1))
        minute = int(tomorrow_match.group(2)) if tomorrow_match.group(2) else 0
        ampm = tomorrow_match.group(3)
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Natural language: "today Xpm/am" or just "Xpm"
    today_match = re.match(
        r"(?:today\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.IGNORECASE
    )
    if today_match:
        hour = int(today_match.group(1))
        minute = int(today_match.group(2)) if today_match.group(2) else 0
        ampm = today_match.group(3).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # If time already passed today, assume tomorrow
        if result <= now:
            result += timedelta(days=1)
        return result

    # Standard formats
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %I:%M %p",
        "%b %d, %Y %H:%M",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %H:%M",
        "%B %d, %Y %I:%M %p",
        "%b %d %H:%M",
        "%b %d %I:%M %p",
        "%b %d %I%p",
    ]

    original_text = text
    for fmt in formats:
        try:
            dt = datetime.strptime(original_text, fmt)
            # If year not in format, use current year (or next if past)
            if "%Y" not in fmt:
                dt = dt.replace(year=now.year)
                if dt.replace(tzinfo=user_tz) < now:
                    dt = dt.replace(year=now.year + 1)
            return dt.replace(tzinfo=user_tz)
        except ValueError:
            continue
    return None


def parse_time_tags(content: str, user_tz: ZoneInfo) -> str:
    """Convert @time(...) tags to Discord timestamps."""
    pattern = r"@time\(([^)]+)\)"

    def replace_match(match):
        time_str = match.group(1)
        dt = parse_datetime(time_str, user_tz)
        if dt:
            unix_ts = int(dt.timestamp())
            return f"<t:{unix_ts}:F>"
        return match.group(0)  # Leave unchanged if can't parse

    return re.sub(pattern, replace_match, content)


def parse_mentions(content: str, guild: discord.Guild) -> str:
    """Convert @RoleName and @Username to Discord mention format."""
    # Find @SomeWord patterns (not already in <@...> format)
    # This regex matches @word but not <@...> or <@&...>
    pattern = r"(?<!<)@(\w+)"

    def replace_match(match):
        name = match.group(1)
        if name.lower() in ("everyone", "here"):
            return match.group(0)  # Leave @everyone/@here as-is

        # Try to find role by name (case-insensitive)
        role = discord.utils.find(lambda r: r.name.lower() == name.lower(), guild.roles)
        if role:
            return role.mention

        # Try to find member by name/display_name
        member = discord.utils.find(
            lambda m: m.name.lower() == name.lower()
            or m.display_name.lower() == name.lower(),
            guild.members,
        )
        if member:
            return member.mention

        return match.group(0)  # Leave unchanged if not found

    return re.sub(pattern, replace_match, content)


# ==================== UI COMPONENTS ====================


def parse_date_input(text: str, user_tz: ZoneInfo) -> Optional[datetime]:
    """
    Parse date input like '5/25', 'May 25', 'jan 15'.
    Returns a date with year auto-set to current or next year if past.
    """
    text = text.strip()
    now = datetime.now(user_tz)

    # Try m/d format
    md_match = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
    if md_match:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        try:
            dt = datetime(year=now.year, month=month, day=day, tzinfo=user_tz)
            if dt.date() < now.date():
                dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            return None

    # Try "Month day" format (Jan 15, January 15, etc.)
    month_names = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    month_match = re.match(r"^([a-zA-Z]+)\s+(\d{1,2})$", text)
    if month_match:
        month_str = month_match.group(1).lower()
        day = int(month_match.group(2))
        month = month_names.get(month_str)
        if month:
            try:
                dt = datetime(year=now.year, month=month, day=day, tzinfo=user_tz)
                if dt.date() < now.date():
                    dt = dt.replace(year=now.year + 1)
                return dt
            except ValueError:
                return None

    return None


def parse_time_input(text: str) -> Optional[tuple]:
    """
    Parse time input like '2:00 pm', '2pm', '14:30'.
    Returns (hour_24, minute) tuple.
    """
    text = text.strip().lower()

    # Try "H:MM am/pm" or "H:MMam/pm"
    time_match = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        ampm = time_match.group(3)

        if ampm:
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        # If no am/pm and hour <= 12, assume it's already correct (could be 24h)

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
        return None

    # Try "Ham/pm" or "H am/pm" (no minutes)
    time_match2 = re.match(r"^(\d{1,2})\s*(am|pm)$", text)
    if time_match2:
        hour = int(time_match2.group(1))
        ampm = time_match2.group(2)

        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        if 0 <= hour <= 23:
            return (hour, 0)

    return None


class TimezoneSelect(Select):
    """Dropdown for selecting timezone."""

    def __init__(self, cog: "ShadyAnnounce"):
        self.cog = cog
        options = [
            discord.SelectOption(label=label, value=tz_name, description=tz_name)
            for tz_name, label in COMMON_TIMEZONES
        ]
        super().__init__(
            placeholder="Select your timezone...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        tz_name = self.values[0]
        await self.cog.config.member(interaction.user).timezone.set(tz_name)
        # Find display name
        display = next((label for name, label in COMMON_TIMEZONES if name == tz_name), tz_name)
        await interaction.response.send_message(
            f"Your timezone has been set to **{display}** (`{tz_name}`).",
            ephemeral=True,
        )
        self.view.stop()


class TimezoneView(View):
    """View containing timezone selector."""

    def __init__(self, cog: "ShadyAnnounce"):
        super().__init__(timeout=120)
        self.add_item(TimezoneSelect(cog))


class AnnounceModal(Modal):
    """Modal for entering announcement details."""

    def __init__(
        self,
        cog: "ShadyAnnounce",
        channel: Union[discord.TextChannel, discord.Thread],
        user_tz: ZoneInfo,
        prefill_date: str = "",
        prefill_time: str = "",
        prefill_content: str = "",
    ):
        super().__init__(title="Schedule Announcement")
        self.cog = cog
        self.channel = channel
        self.user_tz = user_tz

        self.date_input = TextInput(
            label="Date (in your timezone)",
            placeholder="5/25 or May 25",
            required=True,
            max_length=20,
            default=prefill_date,
        )
        self.time_input = TextInput(
            label="Time (in your timezone)",
            placeholder="2:00 pm or 2pm",
            required=True,
            max_length=20,
            default=prefill_time,
        )
        self.content_input = TextInput(
            label="Announcement Content",
            placeholder="Supports **bold**, @RoleName, @Username, @time(May 25 2pm)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            default=prefill_content,
        )
        self.add_item(self.date_input)
        self.add_item(self.time_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date_str = self.date_input.value
            time_str = self.time_input.value
            content = self.content_input.value

            # Parse the date
            parsed_date = parse_date_input(date_str, self.user_tz)
            if not parsed_date:
                await interaction.response.send_message(
                    "Could not parse the date. Use formats like:\n"
                    "- `5/25` (month/day)\n"
                    "- `May 25` or `Jan 15`",
                    ephemeral=True,
                )
                return

            # Parse the time
            parsed_time = parse_time_input(time_str)
            if not parsed_time:
                await interaction.response.send_message(
                    "Could not parse the time. Use formats like:\n"
                    "- `2:00 pm` or `2:30pm`\n"
                    "- `2pm` or `14:00`",
                    ephemeral=True,
                )
                return

            hour, minute = parsed_time

            # Combine date and time
            scheduled_dt = parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Check if in the future
            now = datetime.now(self.user_tz)
            if scheduled_dt <= now:
                # If same day but time passed, bump to next year
                scheduled_dt = scheduled_dt.replace(year=scheduled_dt.year + 1)
                if scheduled_dt <= now:
                    await interaction.response.send_message(
                        "The scheduled time must be in the future.",
                        ephemeral=True,
                    )
                    return

            # Convert to UTC for storage
            scheduled_utc = scheduled_dt.astimezone(timezone.utc)
            unix_ts = int(scheduled_utc.timestamp())

            # Format combined time string for edit flow
            combined_time_str = f"{date_str} {time_str}"

            # Convert content for preview display
            preview_content = parse_time_tags(content, self.user_tz)
            preview_content = parse_mentions(preview_content, interaction.guild)

            # Show preview (store original content for editing)
            view = PreviewView(
                cog=self.cog,
                channel=self.channel,
                content=content,  # Original for editing
                scheduled_utc=scheduled_utc,
                user_tz=self.user_tz,
                time_str=combined_time_str,
                user=interaction.user,
            )

            await interaction.response.send_message(
                f"**Preview of Scheduled Announcement**\n\n"
                f"**Channel:** {self.channel.mention}\n"
                f"**Scheduled for:** <t:{unix_ts}:F> (<t:{unix_ts}:R>)\n\n"
                f"**Content:**\n{preview_content}",
                view=view,
                ephemeral=True,
            )
        except Exception as e:
            import traceback
            error_msg = f"Error in AnnounceModal.on_submit:\n```\n{traceback.format_exc()}\n```"
            log.exception(f"Error in AnnounceModal.on_submit: {e}")
            # DM the error to the developer
            try:
                dev_user = await self.cog.bot.fetch_user(272585510134743040)
                if dev_user:
                    await dev_user.send(error_msg[:2000])
            except Exception:
                pass
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"An error occurred: {e}",
                    ephemeral=True,
                )


class PreviewView(View):
    """View for preview with Confirm/Edit/Cancel buttons."""

    def __init__(
        self,
        cog: "ShadyAnnounce",
        channel: Union[discord.TextChannel, discord.Thread],
        content: str,
        scheduled_utc: datetime,
        user_tz: ZoneInfo,
        time_str: str,
        user: discord.Member,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.channel = channel
        self.content = content
        self.scheduled_utc = scheduled_utc
        self.user_tz = user_tz
        self.time_str = time_str
        self.user = user

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="\u2705", row=0)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        # Double-check it's still in the future
        if self.scheduled_utc <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "The scheduled time is now in the past. Please reschedule.",
                ephemeral=True,
            )
            return

        # Process @time(...) tags and @mentions before saving
        processed_content = parse_time_tags(self.content, self.user_tz)
        processed_content = parse_mentions(processed_content, interaction.guild)

        # Save to config
        async with self.cog.config.guild(interaction.guild).scheduled() as scheduled:
            # Generate ID
            new_id = max((a["id"] for a in scheduled), default=0) + 1
            announcement = {
                "id": new_id,
                "channel_id": self.channel.id,
                "content": processed_content,
                "scheduled_for": self.scheduled_utc.isoformat(),
                "created_by": interaction.user.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            scheduled.append(announcement)

        unix_ts = int(self.scheduled_utc.timestamp())
        await interaction.response.edit_message(
            content=f"Announcement scheduled for <t:{unix_ts}:F> in {self.channel.mention}. (ID: `{new_id}`)",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="\u270f\ufe0f", row=0)
    async def edit_button(self, interaction: discord.Interaction, button: Button):
        # Try to split time_str into date and time parts
        parts = self.time_str.split(" ", 1) if " " in self.time_str else [self.time_str, ""]
        prefill_date = parts[0] if len(parts) > 0 else ""
        prefill_time = parts[1] if len(parts) > 1 else ""

        modal = AnnounceModal(
            cog=self.cog,
            channel=self.channel,
            user_tz=self.user_tz,
            prefill_date=prefill_date,
            prefill_time=prefill_time,
            prefill_content=self.content,
        )
        await interaction.response.send_modal(modal)
        # Delete the preview message after modal is shown
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="\u274c", row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(
            content="Announcement cancelled.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Add Timestamp", style=discord.ButtonStyle.secondary, emoji="🕐", row=1)
    async def add_timestamp_button(self, interaction: discord.Interaction, button: Button):
        """Open timestamp picker and insert into content."""
        modal = TimestampModal(parent_view=self, user_tz=self.user_tz)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Mention", style=discord.ButtonStyle.secondary, row=1)
    async def add_mention_button(self, interaction: discord.Interaction, button: Button):
        """Open mention search modal."""
        modal = MentionModal(parent_view=self, guild=interaction.guild)
        await interaction.response.send_modal(modal)

    async def refresh_preview(self, interaction: discord.Interaction):
        """Refresh the preview message with updated content."""
        unix_ts = int(self.scheduled_utc.timestamp())
        try:
            # Convert content for preview display
            preview_content = parse_time_tags(self.content, self.user_tz)
            preview_content = parse_mentions(preview_content, interaction.guild)

            # Find the original preview message and update it
            await interaction.message.edit(
                content=f"**Preview of Scheduled Announcement**\n\n"
                f"**Channel:** {self.channel.mention}\n"
                f"**Scheduled for:** <t:{unix_ts}:F> (<t:{unix_ts}:R>)\n\n"
                f"**Content:**\n{preview_content}",
            )
        except Exception:
            pass


class TimestampModal(Modal):
    """Modal for entering a timestamp to insert."""

    def __init__(self, parent_view: "PreviewView", user_tz: ZoneInfo):
        super().__init__(title="Add Timestamp")
        self.parent_view = parent_view
        self.user_tz = user_tz

        self.date_input = TextInput(
            label="Date",
            placeholder="5/25 or May 25",
            required=True,
            max_length=20,
        )
        self.time_input = TextInput(
            label="Time",
            placeholder="2:00 pm or 2pm",
            required=True,
            max_length=20,
        )
        self.add_item(self.date_input)
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Parse date and time
        parsed_date = parse_date_input(self.date_input.value, self.user_tz)
        if not parsed_date:
            await interaction.response.send_message(
                "Could not parse date. Use: `5/25` or `May 25`",
                ephemeral=True,
            )
            return

        parsed_time = parse_time_input(self.time_input.value)
        if not parsed_time:
            await interaction.response.send_message(
                "Could not parse time. Use: `2:00 pm` or `2pm`",
                ephemeral=True,
            )
            return

        hour, minute = parsed_time
        dt = parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If in past, bump to next year
        now = datetime.now(self.user_tz)
        if dt <= now:
            dt = dt.replace(year=dt.year + 1)

        unix_ts = int(dt.timestamp())
        timestamp_code = f"<t:{unix_ts}:F>"

        # Append to parent content
        self.parent_view.content += f" {timestamp_code}"

        await interaction.response.send_message(
            f"Timestamp inserted: {timestamp_code}",
            ephemeral=True,
        )

        # Refresh the parent preview
        await self.parent_view.refresh_preview(interaction)


class MentionModal(Modal):
    """Modal for searching and inserting a role or user mention."""

    def __init__(self, parent_view: "PreviewView", guild: discord.Guild):
        super().__init__(title="Add Mention")
        self.parent_view = parent_view
        self.guild = guild

        self.name_input = TextInput(
            label="Role or Username",
            placeholder="Type a role name or username to search",
            required=True,
            max_length=100,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        search = self.name_input.value.strip().lower()

        # Search roles first (case-insensitive)
        role = discord.utils.find(
            lambda r: r.name.lower() == search and r.name != "@everyone",
            self.guild.roles,
        )
        if role:
            self.parent_view.content += f" {role.mention}"
            await interaction.response.send_message(
                f"Role mention inserted: {role.mention}",
                ephemeral=True,
            )
            await self.parent_view.refresh_preview(interaction)
            return

        # Search members by username or display name
        member = discord.utils.find(
            lambda m: m.name.lower() == search or m.display_name.lower() == search,
            self.guild.members,
        )
        if member:
            self.parent_view.content += f" {member.mention}"
            await interaction.response.send_message(
                f"User mention inserted: {member.mention}",
                ephemeral=True,
            )
            await self.parent_view.refresh_preview(interaction)
            return

        # Partial match - find roles containing the search term
        partial_roles = [
            r for r in self.guild.roles
            if search in r.name.lower() and r.name != "@everyone"
        ][:5]

        # Partial match - find members containing the search term
        partial_members = [
            m for m in self.guild.members
            if search in m.name.lower() or search in m.display_name.lower()
        ][:5]

        if partial_roles or partial_members:
            suggestions = []
            for r in partial_roles:
                suggestions.append(f"Role: `{r.name}`")
            for m in partial_members:
                suggestions.append(f"User: `{m.display_name}` ({m.name})")

            await interaction.response.send_message(
                f"No exact match for `{self.name_input.value}`. Did you mean:\n" +
                "\n".join(suggestions),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"No role or user found matching `{self.name_input.value}`.",
                ephemeral=True,
            )


class AnnounceCancelView(View):
    """View for confirming cancellation of an announcement."""

    def __init__(self, cog: "ShadyAnnounce", announcement_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.announcement_id = announcement_id

    @discord.ui.button(label="Confirm Cancel", style=discord.ButtonStyle.danger)
    async def confirm_cancel(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(interaction.guild).scheduled() as scheduled:
            for i, ann in enumerate(scheduled):
                if ann["id"] == self.announcement_id:
                    del scheduled[i]
                    await interaction.response.edit_message(
                        content=f"Announcement #{self.announcement_id} has been cancelled.",
                        view=None,
                    )
                    self.stop()
                    return

        await interaction.response.edit_message(
            content="Announcement not found (may have already been posted or cancelled).",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Keep", style=discord.ButtonStyle.secondary)
    async def keep_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(
            content="Cancellation aborted. The announcement will remain scheduled.",
            view=None,
        )
        self.stop()


# ==================== MAIN COG ====================


class ShadyAnnounce(commands.Cog):
    """Schedule announcements with timezone support."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "scheduled": [],
            "history": [],
            "max_history": 50,
            "mod_roles": [],
        }
        default_member = {
            "timezone": None,
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.announcement_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self.announcement_task = self.bot.loop.create_task(self._announcement_loop())
        log.info("ShadyAnnounce loaded")

    async def cog_unload(self) -> None:
        if self.announcement_task:
            self.announcement_task.cancel()

    # ==================== HELPERS ====================

    async def _auto_delete(self, interaction: discord.Interaction, delay: float = 10.0):
        """Delete the original response after a delay."""
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass  # Already deleted

    # ==================== AUTHORIZATION ====================

    async def is_authorized(self, ctx_or_interaction) -> bool:
        """Check if user has permission to manage announcements."""
        if isinstance(ctx_or_interaction, discord.Interaction):
            user = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
        else:
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild

        if await self.bot.is_owner(user):
            return True
        if not isinstance(user, discord.Member):
            return False
        if user.guild_permissions.administrator or user == guild.owner:
            return True
        if user.guild_permissions.manage_guild:
            return True
        mod_roles = await self.config.guild(guild).mod_roles()
        return any(role.id in mod_roles for role in user.roles)

    # ==================== BACKGROUND TASK ====================

    async def _announcement_loop(self) -> None:
        """Background loop to post scheduled announcements."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self._process_guild_announcements(guild)
                await asyncio.sleep(180)  # Check every 3 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in announcement loop: {e}")
                await asyncio.sleep(180)

    async def _process_guild_announcements(self, guild: discord.Guild) -> None:
        """Process and post due announcements for a guild."""
        now = datetime.now(timezone.utc)
        async with self.config.guild(guild).scheduled() as scheduled:
            to_remove = []
            for ann in scheduled:
                scheduled_for = datetime.fromisoformat(ann["scheduled_for"])
                if scheduled_for <= now:
                    # Time to post
                    channel = guild.get_channel(ann["channel_id"])
                    if channel:
                        try:
                            await channel.send(ann["content"])
                            log.info(f"Posted announcement #{ann['id']} to {channel.name}")
                        except discord.Forbidden:
                            log.warning(f"No permission to post in {channel.name}")
                        except Exception as e:
                            log.error(f"Failed to post announcement #{ann['id']}: {e}")

                    # Move to history
                    async with self.config.guild(guild).history() as history:
                        history_entry = {**ann, "posted_at": now.isoformat()}
                        history.append(history_entry)
                        # Trim history
                        max_history = await self.config.guild(guild).max_history()
                        while len(history) > max_history:
                            history.pop(0)

                    to_remove.append(ann)

            for ann in to_remove:
                scheduled.remove(ann)

    # ==================== COMMANDS ====================

    @app_commands.command(name="mytime", description="Set your timezone for scheduling announcements")
    @app_commands.guild_only()
    async def mytime(self, interaction: discord.Interaction):
        """Set your personal timezone."""
        current_tz = await self.config.member(interaction.user).timezone()
        if current_tz:
            display = next((label for name, label in COMMON_TIMEZONES if name == current_tz), current_tz)
            content = f"Your current timezone is **{display}** (`{current_tz}`).\nSelect a new one below to change it:"
        else:
            content = "Select your timezone from the dropdown below:"

        view = TimezoneView(self)
        await interaction.response.send_message(content, view=view, ephemeral=True)

    @app_commands.command(name="announce", description="Schedule an announcement to the current channel")
    @app_commands.guild_only()
    async def announce(self, interaction: discord.Interaction):
        """Schedule an announcement to the current channel."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to schedule announcements.",
                ephemeral=True,
            )
            return

        # Check timezone is set
        user_tz_name = await self.config.member(interaction.user).timezone()
        if not user_tz_name:
            await interaction.response.send_message(
                "Please set your timezone first using `/mytime`.",
                ephemeral=True,
            )
            return

        try:
            user_tz = ZoneInfo(user_tz_name)
        except Exception:
            await interaction.response.send_message(
                f"Invalid timezone stored: `{user_tz_name}`. Please run `/mytime` again.",
                ephemeral=True,
            )
            return

        # Check bot permissions in channel - allow TextChannel and Thread
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Announcements can only be scheduled for text channels or threads.",
                ephemeral=True,
            )
            return

        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            await interaction.response.send_message(
                f"I don't have permission to send messages in {channel.mention}.",
                ephemeral=True,
            )
            return

        # Show announcement modal
        modal = AnnounceModal(cog=self, channel=channel, user_tz=user_tz)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="announcelist", description="View pending scheduled announcements")
    @app_commands.guild_only()
    async def announcelist(self, interaction: discord.Interaction):
        """View all pending scheduled announcements."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to view scheduled announcements.",
                ephemeral=True,
            )
            return

        scheduled = await self.config.guild(interaction.guild).scheduled()
        if not scheduled:
            await interaction.response.send_message(
                "No announcements are currently scheduled.",
                ephemeral=True,
            )
            return

        # Check if admin (can see all) or regular mod (own only)
        is_admin = (
            interaction.user.guild_permissions.administrator
            or interaction.user == interaction.guild.owner
            or await self.bot.is_owner(interaction.user)
        )

        lines = []
        for ann in sorted(scheduled, key=lambda a: a["scheduled_for"]):
            # Filter by user if not admin
            if not is_admin and ann["created_by"] != interaction.user.id:
                continue

            scheduled_for = datetime.fromisoformat(ann["scheduled_for"])
            unix_ts = int(scheduled_for.timestamp())
            channel = interaction.guild.get_channel(ann["channel_id"])
            channel_mention = channel.mention if channel else f"<#{ann['channel_id']}>"

            # Truncate content preview
            content_preview = ann["content"][:50]
            if len(ann["content"]) > 50:
                content_preview += "..."

            creator = interaction.guild.get_member(ann["created_by"])
            creator_name = creator.display_name if creator else f"User {ann['created_by']}"

            lines.append(
                f"**#{ann['id']}** \u2022 {channel_mention} \u2022 <t:{unix_ts}:R>\n"
                f"  By: {creator_name}\n"
                f"  `{content_preview}`"
            )

        if not lines:
            await interaction.response.send_message(
                "You have no pending scheduled announcements.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "**Scheduled Announcements**\n\n" + "\n\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="announcecancel", description="Cancel a scheduled announcement")
    @app_commands.describe(announcement_id="The ID of the announcement to cancel")
    @app_commands.guild_only()
    async def announcecancel(self, interaction: discord.Interaction, announcement_id: int):
        """Cancel a scheduled announcement."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to cancel announcements.",
                ephemeral=True,
            )
            return

        scheduled = await self.config.guild(interaction.guild).scheduled()
        announcement = next((a for a in scheduled if a["id"] == announcement_id), None)

        if not announcement:
            await interaction.response.send_message(
                f"Announcement #{announcement_id} not found.",
                ephemeral=True,
            )
            return

        # Check if user owns it or is admin
        is_admin = (
            interaction.user.guild_permissions.administrator
            or interaction.user == interaction.guild.owner
            or await self.bot.is_owner(interaction.user)
        )

        if not is_admin and announcement["created_by"] != interaction.user.id:
            await interaction.response.send_message(
                "You can only cancel your own announcements.",
                ephemeral=True,
            )
            return

        # Show confirmation
        scheduled_for = datetime.fromisoformat(announcement["scheduled_for"])
        unix_ts = int(scheduled_for.timestamp())
        channel = interaction.guild.get_channel(announcement["channel_id"])
        channel_mention = channel.mention if channel else f"<#{announcement['channel_id']}>"

        content_preview = announcement["content"][:200]
        if len(announcement["content"]) > 200:
            content_preview += "..."

        view = AnnounceCancelView(self, announcement_id)
        await interaction.response.send_message(
            f"**Cancel Announcement #{announcement_id}?**\n\n"
            f"**Channel:** {channel_mention}\n"
            f"**Scheduled for:** <t:{unix_ts}:F>\n"
            f"**Content:**\n{content_preview}",
            view=view,
            ephemeral=True,
        )

    # ==================== SETTINGS GROUP ====================

    announceset = app_commands.Group(
        name="announceset",
        description="Configure announcement settings",
        guild_only=True,
    )

    @announceset.command(name="addrole", description="Add a role that can schedule announcements")
    @app_commands.describe(role="The role to add")
    async def announceset_addrole(self, interaction: discord.Interaction, role: discord.Role):
        """Add a role that can schedule announcements."""
        if not interaction.user.guild_permissions.administrator and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only administrators can manage announcement roles.",
                ephemeral=True,
            )
            return

        async with self.config.guild(interaction.guild).mod_roles() as mod_roles:
            if role.id in mod_roles:
                await interaction.response.send_message(
                    f"{role.mention} can already schedule announcements.",
                    ephemeral=True,
                )
                asyncio.create_task(self._auto_delete(interaction))
                return
            mod_roles.append(role.id)

        await interaction.response.send_message(
            f"{role.mention} can now schedule announcements.",
            ephemeral=True,
        )
        asyncio.create_task(self._auto_delete(interaction))

    @announceset.command(name="removerole", description="Remove a role from scheduling announcements")
    @app_commands.describe(role="The role to remove")
    async def announceset_removerole(self, interaction: discord.Interaction, role: discord.Role):
        """Remove a role from scheduling announcements."""
        if not interaction.user.guild_permissions.administrator and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only administrators can manage announcement roles.",
                ephemeral=True,
            )
            return

        async with self.config.guild(interaction.guild).mod_roles() as mod_roles:
            if role.id not in mod_roles:
                await interaction.response.send_message(
                    f"{role.mention} is not in the announcement roles list.",
                    ephemeral=True,
                )
                asyncio.create_task(self._auto_delete(interaction))
                return
            mod_roles.remove(role.id)

        await interaction.response.send_message(
            f"{role.mention} can no longer schedule announcements.",
            ephemeral=True,
        )
        asyncio.create_task(self._auto_delete(interaction))

    @announceset.command(name="view", description="View current announcement settings")
    async def announceset_view(self, interaction: discord.Interaction):
        """View current announcement settings."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to view settings.",
                ephemeral=True,
            )
            return

        config = await self.config.guild(interaction.guild).all()

        # Build role list
        role_mentions = []
        for role_id in config["mod_roles"]:
            role = interaction.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
        roles_str = ", ".join(role_mentions) if role_mentions else "None (admins only)"

        scheduled_count = len(config["scheduled"])
        history_count = len(config["history"])

        await interaction.response.send_message(
            f"**Announcement Settings**\n\n"
            f"**Allowed Roles:** {roles_str}\n"
            f"**Pending Announcements:** {scheduled_count}\n"
            f"**History Entries:** {history_count} / {config['max_history']}",
            ephemeral=True,
        )
        asyncio.create_task(self._auto_delete(interaction))
