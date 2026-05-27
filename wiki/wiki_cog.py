import discord
from redbot.core import commands, Config, checks
from discord import app_commands
from typing import Optional
import logging

log = logging.getLogger("red.shadycogs.wiki")


class Wiki(commands.Cog):
    """Community Wiki Helper - Rules, guides, and server info commands."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        default_guild = {
            "mod_roles": [],
            "admin_roles": [],
        }
        self.config.register_guild(**default_guild)

        # Discord's internal format for the Channels & Roles link
        self.channels_and_roles_link = "<id:customize>"

        # Server rules - hardcoded for consistency
        self.rules = {
            "1": {
                "title": ":Hug: 1) Be Respectful",
                "text": (
                    "No racism, trauma dumping, hateful content, sexual content, extreme vulgarity, "
                    "argumentative behavior, impersonation or harassment. Keep it friendly and adult.\n"
                    "Toxic behavior isn't tolerated, both in-game & in the server!\n"
                    "Undesired DMs & Spamming are considered toxic behaviors and will lead to disciplinary action."
                ),
            },
            "2": {
                "title": "🔞 2) 18+ Only",
                "text": "You must be 18 or older to be a member. No exceptions.",
            },
            "3": {
                "title": ":mmmsphere: 3) Be Civil & Read The Room!",
                "text": (
                    "Avoid political, religious, drug-related conversations in voice channels unless "
                    "all participants in the room are okay with the conversation.\n"
                    "Political, religious, drug-related images/conversations are not allowed in text channels.\n"
                    "We are an international community, even if something is legal in your area, it's not legal everywhere."
                ),
            },
            "4": {
                "title": ":RockBrow: 4) NSFW Content Is Not Allowed",
                "text": "Pornographic & grotesque content sharing will result in an immediate ban.",
            },
            "5": {
                "title": "🗣️ 5) Communication",
                "text": "We primarily speak English here. Do your best to communicate clearly with everyone.",
            },
            "6": {
                "title": ":gandalf_bouncy: 6) Use Our Channels & Roles Properly",
                "text": (
                    "Use our roles & channels correctly by posting in the right places.\n"
                    "Abusing role pings to promote yourself is prohibited.\n"
                    "If you're unsure, double check Channels & Roles and THEN ask staff in <#1036975234202472568> "
                    "first BEFORE posting!"
                ),
            },
            "7": {
                "title": "📹 7) Promoting Content",
                "text": (
                    "Only post content (streaming links & clips) in <#1038166649657893004> or <#933289669561581588>.\n"
                    "Want to collaborate with PA? Apply here! <#693601096467218523>"
                ),
            },
            "8": {
                "title": "🫰 8) Crowdfunding and Solicitation",
                "text": (
                    "No GoFundMe, service offers, or money requests - In public or in DMs.\n"
                    "This includes but is not limited to requests for personal causes, projects, or business ventures.\n"
                    "There are no exceptions to this rule for any reason."
                ),
            },
            "9": {
                "title": "❌ 9) Unauthorized Links are Not Allowed!",
                "text": (
                    "Don't post/advertise or DM Discord server, guild/clan invites, game server links, "
                    "or data collection links (surveys, questionaries, etc.). It's a safety risk.\n"
                    "Game servers are allowed to be posted only after passing our vetting process! <#693601096467218523>"
                ),
            },
            "10": {
                "title": "📝 10) Build-A-VC Channel Names",
                "text": "No swear-words/vulgarity when naming your Build-A-VC channels!",
            },
            "11": {
                "title": ":GES_DiscordStaff: 11) Staff Respect & Jurisdiction",
                "text": (
                    "Staff volunteer their personal time to make this a great place, please treat them with respect!\n"
                    '"Mini-Modding" is prohibited, <#1054444547050053744> if something requires staff attention. '
                    "If you disagree with a staff decision, open a ticket.\n"
                    "Altercations outside of Parental Advisory will not be handled by Parental Advisory Staff. "
                    "We ask you to keep personal matters outside of the server."
                ),
            },
        }

        self.rules_footer = (
            "Most violations will begin with a warning and then actions begin after repeat offenses. "
            "Severe violations will result in immediate action.\n"
            "Admins have the final say in any rules and it is at the discretion of the entire staff to enforce them.\n"
            "Rules are subject to change.\n"
            "All members on my server are assumed to have read and agree to these rules.\n"
            "Questions & comments can be directed to any members of the admin team."
        )

    async def is_authorized(self, ctx_or_interaction) -> bool:
        """Check if user is authorized (mod/admin role or permissions)."""
        if isinstance(ctx_or_interaction, discord.Interaction):
            user = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
        else:
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild

        if not guild or not isinstance(user, discord.Member):
            return False

        # Bot owner always authorized
        if await self.bot.is_owner(user):
            return True

        # Check admin permissions
        if user.guild_permissions.administrator:
            return True

        # Check mod/admin roles from config
        mod_roles = await self.config.guild(guild).mod_roles()
        admin_roles = await self.config.guild(guild).admin_roles()
        user_role_ids = [r.id for r in user.roles]

        for role_id in mod_roles + admin_roles:
            if role_id in user_role_ids:
                return True

        # Check Red's mod/admin
        if await self.bot.is_mod(user) or await self.bot.is_admin(user):
            return True

        return False

    async def delete_and_check(self, ctx) -> bool:
        """Delete the invoking message and return True if the user is authorized."""
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
        return await self.is_authorized(ctx)

    async def send_reply(self, ctx, *args, **kwargs):
        """Reply to the referenced message if available, otherwise send normally."""
        if ctx.message.reference:
            try:
                original_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                return await original_message.reply(*args, **kwargs)
            except Exception:
                pass
        return await ctx.send(*args, **kwargs)

    # ==================== Setup Commands ====================

    @commands.group(name="wikiset")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def wikiset(self, ctx):
        """Configure Wiki cog settings."""
        pass

    @wikiset.command(name="addrole")
    async def wikiset_addrole(self, ctx, role: discord.Role, role_type: str = "mod"):
        """Add a role that can use wiki commands.

        role_type: 'mod' or 'admin' (default: mod)
        """
        role_type = role_type.lower()
        if role_type not in ("mod", "admin"):
            await ctx.send("Role type must be 'mod' or 'admin'.")
            return

        if role_type == "mod":
            async with self.config.guild(ctx.guild).mod_roles() as roles:
                if role.id not in roles:
                    roles.append(role.id)
            await ctx.send(f"✅ Added {role.name} as a mod role for wiki commands.")
        else:
            async with self.config.guild(ctx.guild).admin_roles() as roles:
                if role.id not in roles:
                    roles.append(role.id)
            await ctx.send(f"✅ Added {role.name} as an admin role for wiki commands.")

    @wikiset.command(name="removerole")
    async def wikiset_removerole(self, ctx, role: discord.Role):
        """Remove a role from wiki command access."""
        removed = False
        async with self.config.guild(ctx.guild).mod_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
                removed = True
        async with self.config.guild(ctx.guild).admin_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
                removed = True

        if removed:
            await ctx.send(f"✅ Removed {role.name} from wiki command access.")
        else:
            await ctx.send(f"{role.name} was not in the authorized roles.")

    @wikiset.command(name="listroles")
    async def wikiset_listroles(self, ctx):
        """List roles that can use wiki commands."""
        mod_roles = await self.config.guild(ctx.guild).mod_roles()
        admin_roles = await self.config.guild(ctx.guild).admin_roles()

        embed = discord.Embed(title="Wiki Authorized Roles", color=discord.Color.blue())

        mod_mentions = []
        for role_id in mod_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                mod_mentions.append(role.mention)
        embed.add_field(
            name="Mod Roles",
            value="\n".join(mod_mentions) if mod_mentions else "None",
            inline=False,
        )

        admin_mentions = []
        for role_id in admin_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                admin_mentions.append(role.mention)
        embed.add_field(
            name="Admin Roles",
            value="\n".join(admin_mentions) if admin_mentions else "None",
            inline=False,
        )

        await ctx.send(embed=embed)

    # ==================== Prefix Commands ====================

    @commands.command()
    async def rule(self, ctx, rule_number: int):
        """📘 Show a specific server rule (1-11)."""
        if not await self.delete_and_check(ctx):
            return

        rule_data = self.rules.get(str(rule_number))
        if rule_data:
            embed = discord.Embed(
                title="Full Rules",
                url="https://wiki.parentsthatga.me/rules",
                description=f"**{rule_data['title']}**\n{rule_data['text']}",
                color=discord.Color.orange(),
            )
            await self.send_reply(ctx, embed=embed)
        else:
            await self.send_reply(ctx, "Invalid rule number. Use 1–11.")

    @commands.command()
    async def host(self, ctx):
        """📌 Link to hosting/advertising guidelines."""
        if not await self.delete_and_check(ctx):
            return
        output = (
            "Interested in hosting or promoting something in PA? Check out our guidelines first:\n"
            "📌 [Host/Advertise](https://wiki.parentsthatga.me/servers/hosting)"
        )
        await self.send_reply(ctx, output)

    @commands.command()
    async def hosted(self, ctx):
        """🖥️ Link to community-run servers channel."""
        if not await self.delete_and_check(ctx):
            return
        output = (
            "Want to see which servers our community is running?\n"
            "🖥️ Check out <#1350857736224768192> for a list of all community-run servers!"
        )
        await self.send_reply(ctx, output)

    @commands.command()
    async def colors(self, ctx):
        """🎨 Show server colors/levels and how to earn them."""
        if not await self.delete_and_check(ctx):
            return

        embed = discord.Embed(
            title="Server Colors & Levels",
            description=(
                "**How do I get a color?**\n"
                "Colors are earned automatically through activity! You gain XP by:\n"
                "• Sending messages in text channels\n"
                "• Spending time in voice channels (with at least 1 other person)\n\n"
                "**What do the colors mean?**"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url="https://drop.shadyworks.net/download/colors.png")
        await self.send_reply(ctx, embed=embed)

    @commands.command()
    async def noaccess(self, ctx):
        """🔒 Explain how to access channels with the new genre system."""
        if not await self.delete_and_check(ctx):
            return

        embed = discord.Embed(
            title="Can't See a Channel?",
            description=(
                "We've updated our channel structure to genre-based categories!\n\n"
                "**How it works:**\n"
                "• Select a **genre role** to access its category\n"
                "• Select a **game role** to receive pings for that game\n\n"
                f"**To get access:** Head to {self.channels_and_roles_link} and add the genres you'd like!\n\n"
                "Each game is organized in its own space within genre categories, "
                "keeping content organized and easy to find."
            ),
            color=discord.Color.blue(),
        )
        await self.send_reply(ctx, embed=embed)

    @commands.command()
    async def promote(self, ctx):
        """📢 Explain how to access content promotion channels."""
        if not await self.delete_and_check(ctx):
            return

        embed = discord.Embed(
            title="Promote Your Content",
            description=(
                "We have a channel to promote your content. Here's what to do to access it:\n"
                "• Click on Parental Advisory and find \"Linked Roles\" as can be seen below\n"
                "• Add your socials in the pop-up window\n"
                "• You should now have access to the \"Streaming\" Category and are allowed to post "
                "your content in #promoteyourself"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url="https://drop.shadyworks.net/download/linked.png")
        await self.send_reply(ctx, embed=embed)

    # ==================== Slash Commands ====================

    @app_commands.command(name="rule", description="Show a specific server rule")
    @app_commands.describe(rule_number="The rule number (1-11)")
    async def rule_slash(self, interaction: discord.Interaction, rule_number: int):
        """Show a specific server rule with a link to the full rules page."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        rule_data = self.rules.get(str(rule_number))
        if rule_data:
            embed = discord.Embed(
                title="Full Rules",
                url="https://wiki.parentsthatga.me/rules",
                description=f"**{rule_data['title']}**\n{rule_data['text']}",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Invalid rule number. Use 1–11.", ephemeral=True
            )

    @app_commands.command(name="host", description="Link to hosting/advertising guidelines")
    async def host_slash(self, interaction: discord.Interaction):
        """Link to the hosting/advertising guidelines."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        output = (
            "Interested in hosting or promoting something in PA? Check out our guidelines first:\n"
            "📌 [Host/Advertise](https://wiki.parentsthatga.me/servers/hosting)"
        )
        await interaction.response.send_message(output)

    @app_commands.command(name="hosted", description="Link to community-run servers")
    async def hosted_slash(self, interaction: discord.Interaction):
        """Link to the community-run servers channel."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        output = (
            "Want to see which servers our community is running?\n"
            "🖥️ Check out <#1350857736224768192> for a list of all community-run servers!"
        )
        await interaction.response.send_message(output)

    @app_commands.command(name="colors", description="Show server colors/levels info")
    async def colors_slash(self, interaction: discord.Interaction):
        """Show information about server colors/levels and how to earn them."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Server Colors & Levels",
            description=(
                "**How do I get a color?**\n"
                "Colors are earned automatically through activity! You gain XP by:\n"
                "• Sending messages in text channels\n"
                "• Spending time in voice channels (with at least 1 other person)\n\n"
                "**What do the colors mean?**"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url="https://drop.shadyworks.net/download/colors.png")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="noaccess", description="Explain how to access channels")
    async def noaccess_slash(self, interaction: discord.Interaction):
        """Explain how to access channels with the new genre system."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Can't See a Channel?",
            description=(
                "We've updated our channel structure to genre-based categories!\n\n"
                "**How it works:**\n"
                "• Select a **genre role** to access its category\n"
                "• Select a **game role** to receive pings for that game\n\n"
                f"**To get access:** Head to {self.channels_and_roles_link} and add the genres you'd like!\n\n"
                "Each game is organized in its own space within genre categories, "
                "keeping content organized and easy to find."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="promote", description="Explain how to promote your content")
    async def promote_slash(self, interaction: discord.Interaction):
        """Explain how to access the content promotion channels."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Promote Your Content",
            description=(
                "We have a channel to promote your content. Here's what to do to access it:\n"
                "• Click on Parental Advisory and find \"Linked Roles\" as can be seen below\n"
                "• Add your socials in the pop-up window\n"
                "• You should now have access to the \"Streaming\" Category and are allowed to post "
                "your content in #promoteyourself"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url="https://drop.shadyworks.net/download/linked.png")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    cog = Wiki(bot)
    await bot.add_cog(cog)
