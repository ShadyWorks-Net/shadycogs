"""
FAFO Cog - Find Around Find Out moderation button.
Posts a warning message with a button that times out users who click it.
"""
import discord
import logging
from datetime import timedelta

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.fafo")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 1234567890


class FafoView(discord.ui.View):
    """View containing the FAFO button."""

    def __init__(self, timeout_minutes: int = 5):
        super().__init__(timeout=180)  # View expires after 3 minutes
        self.message = None
        self.timeout_minutes = timeout_minutes

    async def on_timeout(self):
        """Delete the message when the view times out."""
        if self.message:
            try:
                await self.message.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                log.warning(f"Failed to delete message on timeout: {e}")

    @discord.ui.button(label="FAFO", style=discord.ButtonStyle.danger)
    async def fafo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the FAFO button click - timeout the user."""
        try:
            await interaction.response.defer(ephemeral=True)

            duration = timedelta(minutes=self.timeout_minutes)
            until_time = discord.utils.utcnow() + duration
            member = interaction.guild.get_member(interaction.user.id)

            if member is None:
                await interaction.followup.send("Member not found.", ephemeral=True)
                return

            await member.timeout(until_time, reason="FAFO button clicked.")
            await interaction.followup.send(
                f"You have been timed out for {self.timeout_minutes} minutes.",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to timeout you. Please check my role position and permissions.",
                ephemeral=True
            )
        except discord.HTTPException as http_err:
            log.error(f"HTTP error during timeout: {http_err}")
            await interaction.followup.send(f"An error occurred: {http_err}", ephemeral=True)
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            await interaction.followup.send(
                "An unexpected error occurred while processing FAFO.",
                ephemeral=True
            )


class Fafo(commands.Cog):
    """FAFO moderation cog - posts warning messages with timeout buttons."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "timeout_minutes": 5,
            "warning_message": (
                "__**Warning:**__\n"
                "If you cannot abide by the rules from previous responses,\n"
                "**Click Below To FAFO**"
            ),
            "mod_roles": [],  # Roles that can use FAFO
        }
        self.config.register_guild(**default_guild)

    async def is_authorized(self, ctx: commands.Context) -> bool:
        """Check if user has permission to use FAFO commands."""
        # Bot owner always authorized
        if await self.bot.is_owner(ctx.author):
            return True

        if not isinstance(ctx.author, discord.Member):
            return False

        # Admin/guild owner always authorized
        if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
            return True

        # Check moderate_members permission
        if ctx.author.guild_permissions.moderate_members:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(ctx.guild).mod_roles()
        return any(role.id in mod_roles for role in ctx.author.roles)

    @commands.hybrid_command(name="fafo")
    @commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def fafo_command(self, ctx: commands.Context):
        """Post a warning message with a FAFO button that times out clickers."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to use FAFO.", ephemeral=True)
            return

        timeout_minutes = await self.config.guild(ctx.guild).timeout_minutes()
        warning_message = await self.config.guild(ctx.guild).warning_message()

        view = FafoView(timeout_minutes=timeout_minutes)
        msg = await ctx.send(warning_message, view=view)
        view.message = msg

        # Delete the command message
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

    @commands.hybrid_group(name="fafoset")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def fafoset(self, ctx: commands.Context):
        """Configure FAFO settings."""
        if not await self.is_authorized(ctx):
            await ctx.send("You don't have permission to manage FAFO settings.", ephemeral=True)
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @fafoset.command(name="timeout")
    @app_commands.describe(minutes="Timeout duration in minutes (1-1440)")
    async def fafoset_timeout(self, ctx: commands.Context, minutes: int):
        """Set the timeout duration in minutes (1-1440)."""
        if minutes < 1 or minutes > 1440:
            await ctx.send("Timeout must be between 1 and 1440 minutes (24 hours).")
            return

        await self.config.guild(ctx.guild).timeout_minutes.set(minutes)
        await ctx.send(f"FAFO timeout set to {minutes} minute(s).")

    @fafoset.command(name="message")
    @app_commands.describe(message="Warning message to display above the button")
    async def fafoset_message(self, ctx: commands.Context, *, message: str):
        """Set the warning message displayed above the FAFO button."""
        await self.config.guild(ctx.guild).warning_message.set(message)
        await ctx.send("Warning message updated.")

    @fafoset.command(name="addrole")
    @app_commands.describe(role="Role that can use FAFO commands")
    async def fafoset_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role that can use FAFO commands."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                await ctx.send(f"{role.mention} is already a mod role.", ephemeral=True)
                return
            roles.append(role.id)

        await ctx.send(f"{role.mention} can now use FAFO commands.")

    @fafoset.command(name="removerole")
    @app_commands.describe(role="Role to remove from FAFO access")
    async def fafoset_removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from FAFO access."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can manage mod roles.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id not in roles:
                await ctx.send(f"{role.mention} is not a mod role.", ephemeral=True)
                return
            roles.remove(role.id)

        await ctx.send(f"{role.mention} can no longer use FAFO commands.")

    @fafoset.command(name="show")
    async def fafoset_show(self, ctx: commands.Context):
        """Show current FAFO settings."""
        timeout = await self.config.guild(ctx.guild).timeout_minutes()
        message = await self.config.guild(ctx.guild).warning_message()
        mod_role_ids = await self.config.guild(ctx.guild).mod_roles()

        mod_role_mentions = []
        for role_id in mod_role_ids:
            r = ctx.guild.get_role(role_id)
            if r:
                mod_role_mentions.append(r.mention)

        embed = discord.Embed(title="FAFO Settings", color=discord.Color.red())
        embed.add_field(name="Timeout Duration", value=f"{timeout} minutes", inline=False)
        embed.add_field(name="Warning Message", value=message[:1024], inline=False)
        embed.add_field(
            name="Mod Roles",
            value=", ".join(mod_role_mentions) if mod_role_mentions else "Admins + moderate_members only",
            inline=False
        )
        await ctx.send(embed=embed)


async def setup(bot: Red) -> None:
    """Load the FAFO cog."""
    await bot.add_cog(Fafo(bot))
