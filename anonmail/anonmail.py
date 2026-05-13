"""
AnonMail Cog - Anonymous feedback system.
Users can submit anonymous feedback to members with a specific role.
"""
import discord
import logging
import time
from typing import Optional, List

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands
from typing import Union

log = logging.getLogger("red.shadycogs.anonmail")
CONFIG_IDENTIFIER = 1234567891


# ==================== UI COMPONENTS ====================


class FeedbackModal(discord.ui.Modal):
    """Modal for submitting feedback."""

    feedback = discord.ui.TextInput(
        label="Your Feedback (Anonymous)",
        style=discord.TextStyle.paragraph,
        placeholder="Your feedback will be posted anonymously...",
        required=True,
        max_length=2000
    )

    def __init__(self, cog: "AnonMail", recipient: discord.Member):
        super().__init__(title=f"Feedback for {recipient.display_name}"[:45])
        self.cog = cog
        self.recipient = recipient

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = await self.cog.config.guild(interaction.guild).feedback_channel()
            if not channel_id:
                await interaction.response.send_message("Feedback channel not configured.", ephemeral=True)
                return

            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.response.send_message("Feedback channel not found.", ephemeral=True)
                return

            thread = await self._find_or_create_thread(channel)

            embed = discord.Embed(
                title="Anonymous Feedback",
                description=self.feedback.value,
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            await thread.send(embed=embed)
            await self.cog._record_use(interaction.guild.id, interaction.user.id)

            await interaction.response.send_message(
                f"Your anonymous feedback for {self.recipient.display_name} has been submitted!",
                ephemeral=True
            )
        except Exception as e:
            log.error(f"Error submitting feedback: {e}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)

    async def _find_or_create_thread(self, channel: discord.TextChannel) -> discord.Thread:
        prefix = await self.cog.config.guild(channel.guild).thread_prefix()
        thread_name = f"{prefix}{self.recipient.display_name}"

        for thread in channel.threads:
            if thread.name == thread_name:
                return thread

        async for thread in channel.archived_threads(limit=100):
            if thread.name == thread_name:
                await thread.edit(archived=False)
                return thread

        return await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=f"Feedback thread for {self.recipient.display_name}"
        )


class RecipientSelect(discord.ui.Select):
    """Select menu for choosing feedback recipient."""

    def __init__(self, cog: "AnonMail", recipients: List[discord.Member]):
        self.cog = cog
        options = [
            discord.SelectOption(label=m.display_name[:100], value=str(m.id))
            for m in recipients[:25]
        ]
        super().__init__(placeholder="Select a recipient...", options=options)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0]))
        if member:
            await interaction.response.send_modal(FeedbackModal(self.cog, member))
        else:
            await interaction.response.send_message("Recipient not found.", ephemeral=True)


class FeedbackView(discord.ui.View):
    """View for selecting feedback recipient."""

    def __init__(self, cog: "AnonMail", recipients: List[discord.Member]):
        super().__init__(timeout=180)
        self.add_item(RecipientSelect(cog, recipients))


class SettingsModal(discord.ui.Modal, title="AnonMail Settings"):
    """Modal for text-based settings."""

    thread_prefix = discord.ui.TextInput(
        label="Thread Prefix",
        placeholder="Feedback - ",
        required=False,
        max_length=50,
    )
    cooldown = discord.ui.TextInput(
        label="Cooldown (minutes, 0 to disable)",
        placeholder="60",
        required=False,
        max_length=4,
    )

    def __init__(self, cog: "AnonMail", current_prefix: str, current_cooldown: int):
        super().__init__()
        self.cog = cog
        self.thread_prefix.default = current_prefix
        self.cooldown.default = str(current_cooldown)

    async def on_submit(self, interaction: discord.Interaction):
        prefix = self.thread_prefix.value.strip() or "Feedback - "
        await self.cog.config.guild(interaction.guild).thread_prefix.set(prefix)

        cooldown = 60
        if self.cooldown.value.strip():
            try:
                cooldown = max(0, int(self.cooldown.value.strip()))
            except ValueError:
                pass
        await self.cog.config.guild(interaction.guild).cooldown_minutes.set(cooldown)

        await interaction.response.send_message(
            f"Settings updated!\n**Prefix:** `{prefix}`\n**Cooldown:** {cooldown} min",
            ephemeral=True
        )


class ChannelSearchModal(discord.ui.Modal, title="Set Feedback Channel"):
    """Modal for searching and setting the feedback channel by name."""

    channel_name = discord.ui.TextInput(
        label="Channel Name (or ID)",
        placeholder="Type channel name to search (leave empty to clear)",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "AnonMail"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        search = self.channel_name.value.strip()

        if not search:
            await self.cog.config.guild(interaction.guild).feedback_channel.set(None)
            await interaction.response.send_message("✅ Feedback channel cleared.", ephemeral=True)
            return

        bot_channels = [
            ch for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).send_messages
            and ch.permissions_for(interaction.guild.me).view_channel
        ]

        if search.isdigit():
            channel = interaction.guild.get_channel(int(search))
            if channel and channel in bot_channels:
                await self.cog.config.guild(interaction.guild).feedback_channel.set(channel.id)
                await interaction.response.send_message(f"✅ Feedback channel set to {channel.mention}", ephemeral=True)
                return

        search_lower = search.lower().lstrip('#')
        matches = [ch for ch in bot_channels if search_lower in ch.name.lower()]

        if not matches:
            await interaction.response.send_message(
                f"❌ No bot-accessible channels found matching `{search}`.",
                ephemeral=True
            )
            return

        if len(matches) == 1:
            await self.cog.config.guild(interaction.guild).feedback_channel.set(matches[0].id)
            await interaction.response.send_message(f"✅ Feedback channel set to {matches[0].mention}", ephemeral=True)
            return

        match_list = "\n".join([f"• #{ch.name} ({ch.category.name if ch.category else 'No category'})" for ch in matches[:10]])
        if len(matches) > 10:
            match_list += f"\n... and {len(matches) - 10} more"

        await interaction.response.send_message(
            f"⚠️ Multiple channels match `{search}`:\n{match_list}\n\nPlease be more specific or use the channel ID.",
            ephemeral=True
        )


class RecipientRolesSelect(discord.ui.Select):
    """Select for choosing multiple recipient roles."""

    def __init__(self, cog: "AnonMail", roles: List[discord.Role], current_ids: List[int]):
        self.cog = cog
        options = []
        for r in roles[:25]:
            if not r.is_default() and not r.managed:
                options.append(discord.SelectOption(
                    label=r.name[:100],
                    value=str(r.id),
                    default=r.id in current_ids
                ))
        if not options:
            options.append(discord.SelectOption(label="No roles available", value="none"))
        super().__init__(
            placeholder="Select recipient role(s)...",
            options=options,
            min_values=0,
            max_values=min(len(options), 10)
        )

    async def callback(self, interaction: discord.Interaction):
        role_ids = [int(v) for v in self.values if v != "none"]
        await self.cog.config.guild(interaction.guild).recipient_roles.set(role_ids)
        if role_ids:
            display = ", ".join(f"<@&{rid}>" for rid in role_ids)
        else:
            display = "None (cleared)"
        await interaction.response.send_message(f"Recipient roles set to: {display}", ephemeral=True)


class SenderRoleSelect(discord.ui.Select):
    """Select for choosing sender role."""

    def __init__(self, cog: "AnonMail", roles: List[discord.Role], current_id: Optional[int]):
        self.cog = cog
        options = [discord.SelectOption(label="None (Anyone)", value="none", emoji="🚫")]
        for r in roles[:24]:
            if not r.is_default() and not r.managed:
                options.append(discord.SelectOption(
                    label=r.name[:100],
                    value=str(r.id),
                    default=r.id == current_id
                ))
        super().__init__(placeholder="Select sender role...", options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        role_id = None if value == "none" else int(value)
        await self.cog.config.guild(interaction.guild).sender_role.set(role_id)
        display = f"<@&{role_id}>" if role_id else "None (Anyone)"
        await interaction.response.send_message(f"Sender role set to {display}", ephemeral=True)


class SetupView(discord.ui.View):
    """Interactive setup view."""

    def __init__(self, cog: "AnonMail", guild: discord.Guild, config: dict):
        super().__init__(timeout=300)
        self.cog = cog

        roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)

        self.add_item(RecipientRolesSelect(cog, roles, config.get("recipient_roles", [])))
        self.add_item(SenderRoleSelect(cog, roles, config["sender_role"]))

    @discord.ui.button(label="Set Feedback Channel", style=discord.ButtonStyle.secondary, emoji="📢", row=2)
    async def channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelSearchModal(self.cog))

    @discord.ui.button(label="More Settings", style=discord.ButtonStyle.primary, emoji="⚙️", row=3)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.guild(interaction.guild).all()
        await interaction.response.send_modal(SettingsModal(
            self.cog, config["thread_prefix"], config["cooldown_minutes"]
        ))

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success, emoji="✅", row=3)
    async def enable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.guild(interaction.guild).enabled.set(True)
        await interaction.response.send_message("AnonMail **enabled**.", ephemeral=True)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, emoji="❌", row=3)
    async def disable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.guild(interaction.guild).enabled.set(False)
        await interaction.response.send_message("AnonMail **disabled**.", ephemeral=True)


# ==================== MAIN COG ====================


class AnonMail(commands.Cog):
    """Anonymous feedback system."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "enabled": False,
            "recipient_roles": [],  # Changed from recipient_role (single) to recipient_roles (list)
            "sender_role": None,
            "feedback_channel": None,
            "thread_prefix": "Feedback - ",
            "cooldown_minutes": 60,
            "mod_roles": [],
        }
        self.config.register_guild(**default_guild)
        self._cooldowns: dict[int, dict[int, float]] = {}

    async def _record_use(self, guild_id: int, user_id: int) -> None:
        self._cooldowns.setdefault(guild_id, {})[user_id] = time.time()

    async def _check_cooldown(self, guild_id: int, user_id: int) -> Optional[float]:
        cooldown_minutes = await self.config.guild_from_id(guild_id).cooldown_minutes()
        if cooldown_minutes <= 0:
            return None
        last_use = self._cooldowns.get(guild_id, {}).get(user_id)
        if last_use is None:
            return None
        remaining = (cooldown_minutes * 60) - (time.time() - last_use)
        return remaining if remaining > 0 else None

    async def is_authorized(self, ctx: commands.Context) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True
        if not isinstance(ctx.author, discord.Member):
            return False
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
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

    # ==================== USER COMMANDS ====================

    @app_commands.command(name="feedback", description="Submit anonymous feedback")
    @app_commands.guild_only()
    async def feedback_slash(self, interaction: discord.Interaction):
        """Submit anonymous feedback to a recipient."""
        config = await self.config.guild(interaction.guild).all()

        if not config["enabled"]:
            await interaction.response.send_message("Anonymous feedback is not enabled.", ephemeral=True)
            return

        remaining = await self._check_cooldown(interaction.guild.id, interaction.user.id)
        if remaining:
            await interaction.response.send_message(
                f"Please wait **{int(remaining // 60)}m {int(remaining % 60)}s**.",
                ephemeral=True
            )
            return

        if config["sender_role"]:
            role = interaction.guild.get_role(config["sender_role"])
            if role and role not in interaction.user.roles:
                await interaction.response.send_message(f"You need the `{role.name}` role.", ephemeral=True)
                return

        # Support both old single recipient_role and new recipient_roles list
        recipient_role_ids = config.get("recipient_roles", [])
        if not recipient_role_ids:
            # Backwards compatibility: check old single role config
            old_role = config.get("recipient_role")
            if old_role:
                recipient_role_ids = [old_role]

        if not recipient_role_ids:
            await interaction.response.send_message("Recipient role(s) not configured.", ephemeral=True)
            return

        # Collect recipients from all configured roles
        recipients = []
        seen_ids = set()
        for role_id in recipient_role_ids:
            role = interaction.guild.get_role(role_id)
            if role:
                for m in role.members:
                    if not m.bot and m.id not in seen_ids:
                        recipients.append(m)
                        seen_ids.add(m.id)

        if not recipients:
            await interaction.response.send_message("No recipients available.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Select a recipient:",
            view=FeedbackView(self, recipients),
            ephemeral=True
        )

    # ==================== ADMIN COMMANDS ====================

    @commands.hybrid_group(name="anonmail")
    @commands.guild_only()
    async def anonmail(self, ctx: commands.Context):
        """Manage anonymous feedback settings."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @anonmail.command(name="setup")
    async def anonmail_setup(self, ctx: commands.Context):
        """Interactive setup for AnonMail."""
        config = await self.config.guild(ctx.guild).all()

        # Get recipient roles display
        recipient_role_ids = config.get("recipient_roles", [])
        if recipient_role_ids:
            recipient_display = ", ".join(f"<@&{rid}>" for rid in recipient_role_ids)
        else:
            recipient_display = "Not set"

        embed = discord.Embed(title="📬 AnonMail Setup", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if config["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=f"<#{config['feedback_channel']}>" if config["feedback_channel"] else "Not set", inline=True)
        embed.add_field(name="Recipient Roles", value=recipient_display, inline=True)
        embed.add_field(name="Sender Role", value=f"<@&{config['sender_role']}>" if config["sender_role"] else "Anyone", inline=True)
        embed.add_field(name="Cooldown", value=f"{config['cooldown_minutes']} min", inline=True)
        embed.add_field(name="Prefix", value=f"`{config['thread_prefix']}`", inline=True)

        await ctx.send(embed=embed, view=SetupView(self, ctx.guild, config), ephemeral=True)

    @anonmail.command(name="status")
    async def anonmail_status(self, ctx: commands.Context):
        """Show current AnonMail configuration."""
        config = await self.config.guild(ctx.guild).all()

        # Get recipient roles display
        recipient_role_ids = config.get("recipient_roles", [])
        if recipient_role_ids:
            recipient_display = ", ".join(f"<@&{rid}>" for rid in recipient_role_ids)
        else:
            recipient_display = "Not set"

        embed = discord.Embed(title="📬 AnonMail Status", color=discord.Color.blue())
        embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
        embed.add_field(name="Channel", value=f"<#{config['feedback_channel']}>" if config["feedback_channel"] else "Not set", inline=True)
        embed.add_field(name="Recipient Roles", value=recipient_display, inline=True)
        embed.add_field(name="Sender Role", value=f"<@&{config['sender_role']}>" if config["sender_role"] else "Anyone", inline=True)
        embed.add_field(name="Cooldown", value=f"{config['cooldown_minutes']} min", inline=True)

        await ctx.send(embed=embed)

    @anonmail.command(name="addrole")
    @app_commands.describe(role="Role that can manage AnonMail")
    async def anonmail_addrole(self, ctx: commands.Context, role: discord.Role):
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
        await ctx.send(f"✅ {role.mention} can now manage AnonMail.")

    @anonmail.command(name="removerole")
    @app_commands.describe(role="Role to remove")
    async def anonmail_removerole(self, ctx: commands.Context, role: discord.Role):
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

    @anonmail.command(name="listroles")
    @app_commands.describe()
    async def anonmail_listroles(self, ctx: commands.Context):
        """List all roles that can manage AnonMail."""
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
            title="📬 AnonMail Mod Roles",
            description="\n".join(role_mentions),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Admins can always manage AnonMail")
        await ctx.send(embed=embed)

    @anonmail.command(name="listrecipients")
    @app_commands.describe()
    async def anonmail_listrecipients(self, ctx: commands.Context):
        """List all recipient roles that can receive feedback."""
        config = await self.config.guild(ctx.guild).all()
        recipient_role_ids = config.get("recipient_roles", [])

        if not recipient_role_ids:
            await ctx.send("No recipient roles configured.")
            return

        role_info = []
        total_recipients = 0
        seen_ids = set()

        for role_id in recipient_role_ids:
            role = ctx.guild.get_role(role_id)
            if role:
                # Count unique non-bot members
                role_members = [m for m in role.members if not m.bot and m.id not in seen_ids]
                for m in role_members:
                    seen_ids.add(m.id)
                total_recipients += len(role_members)
                role_info.append(f"{role.mention} ({len(role.members)} members)")
            else:
                role_info.append(f"Unknown role ({role_id})")

        embed = discord.Embed(
            title="📬 AnonMail Recipient Roles",
            description="\n".join(role_info),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Total Recipients",
            value=f"{total_recipients} unique members can receive feedback",
            inline=False
        )
        await ctx.send(embed=embed)

    @anonmail.command(name="channel")
    @app_commands.describe(channel="Feedback channel where threads are created (bot-visible channels)")
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def anonmail_channel(self, ctx: commands.Context, channel: str = None):
        """Set the feedback channel (where feedback threads are created)."""
        if channel is None:
            await self.config.guild(ctx.guild).feedback_channel.set(None)
            await ctx.send("✅ Feedback channel cleared.")
            return

        try:
            channel_id = int(channel)
            ch = ctx.guild.get_channel(channel_id)
            if not ch:
                await ctx.send("Channel not found.", ephemeral=True)
                return
            await self.config.guild(ctx.guild).feedback_channel.set(channel_id)
            await ctx.send(f"✅ Feedback channel set to {ch.mention}")
        except ValueError:
            await ctx.send("Invalid channel.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(AnonMail(bot))
