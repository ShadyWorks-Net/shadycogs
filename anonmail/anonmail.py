"""
AnonMail Cog - Anonymous feedback system.
Users can submit anonymous feedback to members with a specific role.
Feedback is posted in dedicated threads.

Features:
- Role-based recipient selection
- Anonymous modal submission
- Per-recipient thread management
- Cooldown per user (abuse prevention)
- Ban list for repeat abusers
- Auto-deletes command for privacy
"""
import discord
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.anonmail")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 1234567891


class FeedbackSelectView(discord.ui.View):
    """View for selecting a feedback recipient."""

    def __init__(self, cog, recipients: list[discord.Member]):
        super().__init__(timeout=180)
        self.cog = cog
        self.recipients = recipients

        # Create select options
        options = [
            discord.SelectOption(label=member.display_name, value=str(member.id))
            for member in recipients[:25]  # Discord limit
        ]

        select = discord.ui.Select(
            placeholder="Select a recipient",
            min_values=1,
            max_values=1,
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        """Handle recipient selection."""
        selected_id = int(interaction.data['values'][0])
        selected_member = discord.utils.get(self.recipients, id=selected_id)

        if selected_member:
            modal = FeedbackModal(self.cog, selected_member)
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.send_message(
                "Error: Could not find selected recipient.",
                ephemeral=True
            )


class FeedbackModal(discord.ui.Modal):
    """Modal for submitting feedback."""

    def __init__(self, cog, recipient: discord.Member):
        super().__init__(title=f"Feedback for {recipient.display_name}")
        self.cog = cog
        self.recipient = recipient

        self.feedback = discord.ui.TextInput(
            label="Your Feedback (Anonymous)",
            style=discord.TextStyle.paragraph,
            placeholder="Your feedback will be posted anonymously...",
            required=True,
            max_length=2000
        )
        self.add_item(self.feedback)

    async def on_submit(self, interaction: discord.Interaction):
        """Process the feedback submission."""
        try:
            guild_config = self.cog.config.guild(interaction.guild)
            channel_id = await guild_config.feedback_channel()

            if not channel_id:
                await interaction.response.send_message(
                    "Error: Feedback channel not configured. Contact an admin.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.response.send_message(
                    "Error: Feedback channel not found. Contact an admin.",
                    ephemeral=True
                )
                return

            # Find or create thread for this recipient
            thread = await self.find_or_create_thread(channel, self.recipient)

            # Post anonymous feedback
            embed = discord.Embed(
                title="Anonymous Feedback",
                description=self.feedback.value,
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            await thread.send(embed=embed)

            # Record cooldown
            await self.cog.record_feedback_use(interaction.guild.id, interaction.user.id)

            await interaction.response.send_message(
                f"Your anonymous feedback for {self.recipient.display_name} has been submitted!",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to post feedback. Contact an admin.",
                ephemeral=True
            )
        except Exception as e:
            log.error(f"Error submitting feedback: {e}")
            await interaction.response.send_message(
                "An error occurred. Please try again or contact an admin.",
                ephemeral=True
            )

    async def find_or_create_thread(
        self, channel: discord.TextChannel, recipient: discord.Member
    ) -> discord.Thread:
        """Find existing thread or create new one for recipient."""
        guild_config = self.cog.config.guild(channel.guild)
        thread_prefix = await guild_config.thread_prefix()
        thread_name = f"{thread_prefix}{recipient.display_name}"

        # Search existing threads
        for thread in channel.threads:
            if thread.name == thread_name:
                return thread

        # Check archived threads
        async for thread in channel.archived_threads(limit=100):
            if thread.name == thread_name:
                # Unarchive it
                await thread.edit(archived=False)
                return thread

        # Create new thread
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=f"Feedback thread for {recipient.display_name}"
        )
        return thread


class AnonMail(commands.Cog):
    """Anonymous feedback system - users can submit feedback to role members."""

    __version__ = "2.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "enabled": False,
            "recipient_role": None,  # Role ID whose members can receive feedback
            "sender_role": None,  # Role ID required to send feedback (None = anyone)
            "feedback_channel": None,  # Channel ID for feedback threads
            "thread_prefix": "Feedback - ",
            "cooldown_minutes": 60,  # Cooldown between feedback submissions
            "banned_users": [],  # List of user IDs banned from sending feedback
            "mod_roles": [],  # List of role IDs that can manage settings
        }
        self.config.register_guild(**default_guild)

        # In-memory cooldown tracking: {guild_id: {user_id: timestamp}}
        self._cooldowns: dict[int, dict[int, float]] = {}

    async def record_feedback_use(self, guild_id: int, user_id: int) -> None:
        """Record that a user submitted feedback."""
        if guild_id not in self._cooldowns:
            self._cooldowns[guild_id] = {}
        self._cooldowns[guild_id][user_id] = time.time()

    async def check_cooldown(self, guild_id: int, user_id: int) -> Optional[float]:
        """Check if user is on cooldown. Returns seconds remaining or None."""
        cooldown_minutes = await self.config.guild_from_id(guild_id).cooldown_minutes()

        if cooldown_minutes <= 0:
            return None

        guild_cooldowns = self._cooldowns.get(guild_id, {})
        last_use = guild_cooldowns.get(user_id)

        if last_use is None:
            return None

        cooldown_seconds = cooldown_minutes * 60
        elapsed = time.time() - last_use
        remaining = cooldown_seconds - elapsed

        return remaining if remaining > 0 else None

    async def is_banned(self, guild_id: int, user_id: int) -> bool:
        """Check if user is banned from sending feedback."""
        banned_users = await self.config.guild_from_id(guild_id).banned_users()
        return user_id in banned_users

    async def is_authorized(self, ctx: commands.Context) -> bool:
        """Check if user has permission to manage settings."""
        # Bot owner always authorized
        if await self.bot.is_owner(ctx.author):
            return True

        if not isinstance(ctx.author, discord.Member):
            return False

        # Admin/guild owner always authorized
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(ctx.guild).mod_roles()
        return any(role.id in mod_roles for role in ctx.author.roles)

    @commands.hybrid_command(name="feedback")
    @commands.guild_only()
    async def feedback_command(self, ctx: commands.Context):
        """Submit anonymous feedback to a recipient."""
        guild_config = self.config.guild(ctx.guild)

        # Delete command message immediately for privacy
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        # Check if enabled
        if not await guild_config.enabled():
            await ctx.send("Anonymous feedback is not enabled on this server.", delete_after=10)
            return

        # Check if user is banned
        if await self.is_banned(ctx.guild.id, ctx.author.id):
            await ctx.send(
                "You are banned from using the feedback system.",
                delete_after=10
            )
            return

        # Check cooldown
        remaining = await self.check_cooldown(ctx.guild.id, ctx.author.id)
        if remaining is not None:
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            await ctx.send(
                f"Please wait **{minutes}m {seconds}s** before submitting more feedback.",
                delete_after=10
            )
            return

        # Check sender role if configured
        sender_role_id = await guild_config.sender_role()
        if sender_role_id:
            sender_role = ctx.guild.get_role(sender_role_id)
            if sender_role and sender_role not in ctx.author.roles:
                await ctx.send(
                    f"You need the `{sender_role.name}` role to submit feedback.",
                    delete_after=10
                )
                return

        # Get recipients
        recipient_role_id = await guild_config.recipient_role()
        if not recipient_role_id:
            await ctx.send("Recipient role not configured. Contact an admin.", delete_after=10)
            return

        recipient_role = ctx.guild.get_role(recipient_role_id)
        if not recipient_role:
            await ctx.send("Recipient role not found. Contact an admin.", delete_after=10)
            return

        recipients = [m for m in recipient_role.members if not m.bot]
        if not recipients:
            await ctx.send("No recipients available.", delete_after=10)
            return

        # Show selection view
        view = FeedbackSelectView(self, recipients)
        await ctx.send(
            "Select a recipient to submit anonymous feedback:",
            view=view,
            delete_after=180
        )

    @commands.hybrid_group(name="anonmailset")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def anonmailset(self, ctx: commands.Context):
        """Configure anonymous feedback settings."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to manage AnonMail settings.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @anonmailset.command(name="enable")
    @app_commands.describe(enabled="Enable or disable anonymous feedback")
    async def anonmailset_enable(self, ctx: commands.Context, enabled: bool):
        """Enable or disable anonymous feedback."""
        await self.config.guild(ctx.guild).enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Anonymous feedback {status}.")

    @anonmailset.command(name="recipientrole")
    @app_commands.describe(role="Role whose members can receive feedback")
    async def anonmailset_recipientrole(self, ctx: commands.Context, role: discord.Role):
        """Set the role whose members can receive feedback."""
        await self.config.guild(ctx.guild).recipient_role.set(role.id)
        await ctx.send(f"✅ Recipient role set to `{role.name}`.")

    @anonmailset.command(name="senderrole")
    @app_commands.describe(role="Role required to send feedback (leave empty for anyone)")
    async def anonmailset_senderrole(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the role required to send feedback (leave empty for anyone)."""
        if role:
            await self.config.guild(ctx.guild).sender_role.set(role.id)
            await ctx.send(f"✅ Sender role set to `{role.name}`.")
        else:
            await self.config.guild(ctx.guild).sender_role.set(None)
            await ctx.send("✅ Sender role cleared. Anyone can submit feedback.")

    @anonmailset.command(name="channel")
    @app_commands.describe(channel="Channel where feedback threads will be created")
    async def anonmailset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel for feedback threads."""
        await self.config.guild(ctx.guild).feedback_channel.set(channel.id)
        await ctx.send(f"✅ Feedback channel set to {channel.mention}.")

    @anonmailset.command(name="prefix")
    @app_commands.describe(prefix="Thread name prefix (e.g., 'Feedback - ')")
    async def anonmailset_prefix(self, ctx: commands.Context, *, prefix: str):
        """Set the thread name prefix."""
        await self.config.guild(ctx.guild).thread_prefix.set(prefix)
        await ctx.send(f"✅ Thread prefix set to: `{prefix}`")

    @anonmailset.command(name="cooldown")
    @app_commands.describe(minutes="Minutes between submissions (0 to disable)")
    async def anonmailset_cooldown(self, ctx: commands.Context, minutes: int):
        """Set the cooldown between feedback submissions (0 to disable)."""
        if minutes < 0:
            await ctx.send("❌ Cooldown must be 0 or positive.")
            return

        await self.config.guild(ctx.guild).cooldown_minutes.set(minutes)
        if minutes == 0:
            await ctx.send("✅ Cooldown disabled. Users can submit feedback anytime.")
        else:
            await ctx.send(f"✅ Cooldown set to {minutes} minutes between submissions.")

    @anonmailset.command(name="ban")
    @app_commands.describe(user="User to ban from feedback system")
    async def anonmailset_ban(self, ctx: commands.Context, user: discord.User):
        """Ban a user from using the feedback system."""
        async with self.config.guild(ctx.guild).banned_users() as banned:
            if user.id in banned:
                await ctx.send(f"❌ {user.mention} is already banned.")
                return
            banned.append(user.id)

        await ctx.send(f"✅ {user.mention} is now banned from using feedback.")

    @anonmailset.command(name="unban")
    @app_commands.describe(user="User to unban from feedback system")
    async def anonmailset_unban(self, ctx: commands.Context, user: discord.User):
        """Unban a user from the feedback system."""
        async with self.config.guild(ctx.guild).banned_users() as banned:
            if user.id not in banned:
                await ctx.send(f"❌ {user.mention} is not banned.")
                return
            banned.remove(user.id)

        await ctx.send(f"✅ {user.mention} can now use feedback again.")

    @anonmailset.command(name="banned")
    async def anonmailset_banned(self, ctx: commands.Context):
        """List all banned users."""
        banned_ids = await self.config.guild(ctx.guild).banned_users()

        if not banned_ids:
            await ctx.send("No users are banned from feedback.")
            return

        user_mentions = []
        for user_id in banned_ids:
            user = self.bot.get_user(user_id)
            if user:
                user_mentions.append(f"{user.mention} ({user.id})")
            else:
                user_mentions.append(f"Unknown ({user_id})")

        embed = discord.Embed(
            title="🚫 Banned Users",
            description="\n".join(user_mentions),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

    @anonmailset.command(name="addrole")
    @app_commands.describe(role="Role that can manage AnonMail settings")
    async def anonmailset_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role that can manage AnonMail settings."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                await ctx.send(f"❌ {role.mention} is already a mod role.", ephemeral=True)
                return
            roles.append(role.id)

        await ctx.send(f"✅ {role.mention} can now manage AnonMail settings.")

    @anonmailset.command(name="removerole")
    @app_commands.describe(role="Role to remove from AnonMail management")
    async def anonmailset_removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from AnonMail management."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id not in roles:
                await ctx.send(f"❌ {role.mention} is not a mod role.", ephemeral=True)
                return
            roles.remove(role.id)

        await ctx.send(f"✅ {role.mention} can no longer manage AnonMail settings.")

    @anonmailset.command(name="show")
    async def anonmailset_show(self, ctx: commands.Context):
        """Show current settings."""
        guild_config = self.config.guild(ctx.guild)

        enabled = await guild_config.enabled()
        recipient_role_id = await guild_config.recipient_role()
        sender_role_id = await guild_config.sender_role()
        channel_id = await guild_config.feedback_channel()
        prefix = await guild_config.thread_prefix()
        cooldown = await guild_config.cooldown_minutes()
        banned = await guild_config.banned_users()
        mod_role_ids = await guild_config.mod_roles()

        recipient_role = ctx.guild.get_role(recipient_role_id) if recipient_role_id else None
        sender_role = ctx.guild.get_role(sender_role_id) if sender_role_id else None
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        mod_role_mentions = []
        for role_id in mod_role_ids:
            r = ctx.guild.get_role(role_id)
            if r:
                mod_role_mentions.append(r.mention)

        embed = discord.Embed(
            title="📬 AnonMail Settings",
            color=discord.Color.blue()
        )
        embed.add_field(name="Enabled", value="✅ Yes" if enabled else "❌ No", inline=True)
        embed.add_field(
            name="Recipient Role",
            value=recipient_role.mention if recipient_role else "Not set",
            inline=True
        )
        embed.add_field(
            name="Sender Role",
            value=sender_role.mention if sender_role else "Anyone",
            inline=True
        )
        embed.add_field(
            name="Feedback Channel",
            value=channel.mention if channel else "Not set",
            inline=True
        )
        embed.add_field(name="Thread Prefix", value=f"`{prefix}`", inline=True)
        embed.add_field(
            name="Cooldown",
            value=f"{cooldown} minutes" if cooldown > 0 else "Disabled",
            inline=True
        )
        embed.add_field(name="Banned Users", value=str(len(banned)), inline=True)
        embed.add_field(
            name="Mod Roles",
            value=", ".join(mod_role_mentions) if mod_role_mentions else "Admins only",
            inline=True
        )

        embed.set_footer(text=f"v{self.__version__}")

        await ctx.send(embed=embed)
