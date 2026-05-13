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


class ChannelSelect(discord.ui.Select):
    """Select for choosing feedback channel."""

    def __init__(self, cog: "AnonMail", channels: List[discord.TextChannel], current_id: Optional[int]):
        self.cog = cog
        options = [discord.SelectOption(label="None (Clear)", value="none", emoji="🚫")]
        for ch in channels[:24]:
            options.append(discord.SelectOption(
                label=f"#{ch.name}"[:100],
                value=str(ch.id),
                description=ch.category.name[:50] if ch.category else None,
                default=ch.id == current_id
            ))
        super().__init__(placeholder="Select feedback channel...", options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        channel_id = None if value == "none" else int(value)
        await self.cog.config.guild(interaction.guild).feedback_channel.set(channel_id)
        display = f"<#{channel_id}>" if channel_id else "None"
        await interaction.response.send_message(f"Feedback channel set to {display}", ephemeral=True)


class RoleSelect(discord.ui.Select):
    """Select for choosing recipient/sender role."""

    def __init__(self, cog: "AnonMail", roles: List[discord.Role], setting: str, current_id: Optional[int]):
        self.cog = cog
        self.setting = setting
        options = [discord.SelectOption(label="None (Anyone)" if setting == "sender" else "None", value="none", emoji="🚫")]
        for r in roles[:24]:
            if not r.is_default() and not r.managed:
                options.append(discord.SelectOption(
                    label=r.name[:100],
                    value=str(r.id),
                    default=r.id == current_id
                ))
        super().__init__(placeholder=f"Select {setting} role...", options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        role_id = None if value == "none" else int(value)
        if self.setting == "recipient":
            await self.cog.config.guild(interaction.guild).recipient_role.set(role_id)
        else:
            await self.cog.config.guild(interaction.guild).sender_role.set(role_id)
        display = f"<@&{role_id}>" if role_id else "None"
        await interaction.response.send_message(f"{self.setting.title()} role set to {display}", ephemeral=True)


class SetupView(discord.ui.View):
    """Interactive setup view."""

    def __init__(self, cog: "AnonMail", guild: discord.Guild, config: dict):
        super().__init__(timeout=300)
        self.cog = cog

        channels = [ch for ch in guild.text_channels
                    if ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).view_channel]
        channels.sort(key=lambda c: (c.category.position if c.category else -1, c.position))

        roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)

        self.add_item(ChannelSelect(cog, channels, config["feedback_channel"]))
        self.add_item(RoleSelect(cog, roles, "recipient", config["recipient_role"]))
        self.add_item(RoleSelect(cog, roles, "sender", config["sender_role"]))

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
            "recipient_role": None,
            "sender_role": None,
            "feedback_channel": None,
            "thread_prefix": "Feedback - ",
            "cooldown_minutes": 60,
            "banned_users": [],
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

    # ==================== USER COMMANDS ====================

    @app_commands.command(name="feedback", description="Submit anonymous feedback")
    @app_commands.guild_only()
    async def feedback_slash(self, interaction: discord.Interaction):
        """Submit anonymous feedback to a recipient."""
        config = await self.config.guild(interaction.guild).all()

        if not config["enabled"]:
            await interaction.response.send_message("Anonymous feedback is not enabled.", ephemeral=True)
            return

        if interaction.user.id in config["banned_users"]:
            await interaction.response.send_message("You are banned from using feedback.", ephemeral=True)
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

        if not config["recipient_role"]:
            await interaction.response.send_message("Recipient role not configured.", ephemeral=True)
            return

        role = interaction.guild.get_role(config["recipient_role"])
        if not role:
            await interaction.response.send_message("Recipient role not found.", ephemeral=True)
            return

        recipients = [m for m in role.members if not m.bot]
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

        embed = discord.Embed(title="📬 AnonMail Setup", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if config["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=f"<#{config['feedback_channel']}>" if config["feedback_channel"] else "Not set", inline=True)
        embed.add_field(name="Recipient Role", value=f"<@&{config['recipient_role']}>" if config["recipient_role"] else "Not set", inline=True)
        embed.add_field(name="Sender Role", value=f"<@&{config['sender_role']}>" if config["sender_role"] else "Anyone", inline=True)
        embed.add_field(name="Cooldown", value=f"{config['cooldown_minutes']} min", inline=True)
        embed.add_field(name="Prefix", value=f"`{config['thread_prefix']}`", inline=True)

        await ctx.send(embed=embed, view=SetupView(self, ctx.guild, config))

    @anonmail.command(name="status")
    async def anonmail_status(self, ctx: commands.Context):
        """Show current AnonMail configuration."""
        config = await self.config.guild(ctx.guild).all()

        embed = discord.Embed(title="📬 AnonMail Status", color=discord.Color.blue())
        embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
        embed.add_field(name="Channel", value=f"<#{config['feedback_channel']}>" if config["feedback_channel"] else "Not set", inline=True)
        embed.add_field(name="Recipient Role", value=f"<@&{config['recipient_role']}>" if config["recipient_role"] else "Not set", inline=True)
        embed.add_field(name="Sender Role", value=f"<@&{config['sender_role']}>" if config["sender_role"] else "Anyone", inline=True)
        embed.add_field(name="Cooldown", value=f"{config['cooldown_minutes']} min", inline=True)
        embed.add_field(name="Banned Users", value=str(len(config["banned_users"])), inline=True)

        await ctx.send(embed=embed)

    @anonmail.command(name="ban")
    @app_commands.describe(user="User to ban from feedback")
    async def anonmail_ban(self, ctx: commands.Context, user: discord.User):
        """Ban a user from using feedback."""
        async with self.config.guild(ctx.guild).banned_users() as banned:
            if user.id in banned:
                await ctx.send(f"{user.mention} is already banned.")
                return
            banned.append(user.id)
        await ctx.send(f"✅ {user.mention} banned from feedback.")

    @anonmail.command(name="unban")
    @app_commands.describe(user="User to unban")
    async def anonmail_unban(self, ctx: commands.Context, user: discord.User):
        """Unban a user from feedback."""
        async with self.config.guild(ctx.guild).banned_users() as banned:
            if user.id not in banned:
                await ctx.send(f"{user.mention} is not banned.")
                return
            banned.remove(user.id)
        await ctx.send(f"✅ {user.mention} unbanned.")

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


async def setup(bot: Red):
    await bot.add_cog(AnonMail(bot))
