import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.shadycogs.shadysuggest")

CONFIG_IDENTIFIER = 260288776360820740

# status -> (embed color, display label)
STATUS_META = {
    "open": (discord.Color.blurple(), "🟦 Open"),
    "approved": (discord.Color.green(), "✅ Approved"),
    "denied": (discord.Color.red(), "⛔ Denied"),
    "implemented": (discord.Color.gold(), "🌟 Implemented"),
}

_TRUTHY = {"y", "yes", "true", "1", "anon", "anonymous", "on"}


# ==================== UI COMPONENTS ====================


class SuggestModal(discord.ui.Modal, title="New Suggestion"):
    """Modal collecting the suggestion title, details, and anonymity choice."""

    title_input = discord.ui.TextInput(
        label="Title",
        placeholder="A short summary of your idea",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )
    details_input = discord.ui.TextInput(
        label="Details",
        placeholder="Explain your suggestion in full",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )
    anon_input = discord.ui.TextInput(
        label="Post anonymously? (yes/no)",
        placeholder="no",
        style=discord.TextStyle.short,
        max_length=10,
        required=False,
    )

    def __init__(self, cog: "ShadySuggest") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.cog.create_suggestion(
                interaction,
                self.title_input.value,
                self.details_input.value,
                self.anon_input.value,
            )
        except Exception as e:  # noqa: BLE001
            log.error(f"Error creating suggestion: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while posting your suggestion.", ephemeral=True
                )


class SuggestVoteView(discord.ui.View):
    """Persistent view with green/red vote buttons on each open suggestion."""

    def __init__(self, cog: Optional["ShadySuggest"] = None) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        emoji="✅", style=discord.ButtonStyle.green, custom_id="shady_suggest:up"
    )
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "up")

    @discord.ui.button(
        emoji="❌", style=discord.ButtonStyle.red, custom_id="shady_suggest:down"
    )
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "down")

    async def _vote(self, interaction: discord.Interaction, direction: str) -> None:
        if not self.cog:
            await interaction.response.send_message(
                "Suggestions are still loading, please try again in a moment.", ephemeral=True
            )
            return
        sid = self._sid_from_message(interaction)
        if sid is None:
            await interaction.response.send_message(
                "Couldn't identify this suggestion.", ephemeral=True
            )
            return
        await self.cog.handle_vote(interaction, sid, direction)

    @staticmethod
    def _sid_from_message(interaction: discord.Interaction) -> Optional[int]:
        if not interaction.message or not interaction.message.embeds:
            return None
        footer = interaction.message.embeds[0].footer
        if footer and footer.text and footer.text.startswith("Suggestion ID: "):
            try:
                return int(footer.text.replace("Suggestion ID: ", "").strip())
            except ValueError:
                return None
        return None


# ==================== MAIN COG ====================


class ShadySuggest(commands.Cog):
    """Suggestion board with anonymous voting and staff triage."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )

        default_guild = {
            "enabled": False,
            "submit_channel": None,
            "post_channel": None,
            "archive_channel": None,
            "log_channel": None,
            "participant_min_role": None,
            "staff_min_role": None,
            "vote_blocklist_roles": [],
            "next_id": 1,
            "suggestions": {},
        }
        self.config.register_guild(**default_guild)
        self._id_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        # Register the persistent view so vote buttons survive restarts.
        self.bot.add_view(SuggestVoteView(cog=self))

    # -------------------- authorization helpers --------------------

    @staticmethod
    def _meets_min_role(
        member: discord.Member, min_role_id: Optional[int], guild: discord.Guild
    ) -> bool:
        if min_role_id is None:
            return True  # gate unset => open
        role = guild.get_role(min_role_id)
        if role is None:
            return True  # stale role => treat as open
        return member.top_role.position >= role.position

    async def _participation_reason(
        self, member: discord.abc.User, guild: discord.Guild, *, voting: bool
    ) -> Optional[str]:
        """Return None if allowed to participate, else a user-facing reason."""
        if await self.bot.is_owner(member):
            return None
        if not isinstance(member, discord.Member):
            return "This can only be used by server members."
        if member.guild_permissions.administrator or member == guild.owner:
            return None

        min_role_id = await self.config.guild(guild).participant_min_role()
        if not self._meets_min_role(member, min_role_id, guild):
            role = guild.get_role(min_role_id) if min_role_id else None
            target = role.mention if role else "the required"
            return f"You need {target} role (or higher) to use suggestions."

        if voting:
            blocklist = await self.config.guild(guild).vote_blocklist_roles()
            if any(r.id in blocklist for r in member.roles):
                return "One of your roles is blocked from voting on suggestions."
        return None

    async def is_staff(self, member: discord.abc.User, guild: discord.Guild) -> bool:
        if await self.bot.is_owner(member):
            return True
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator or member == guild.owner:
            return True
        if member.guild_permissions.manage_guild:
            return True
        min_role_id = await self.config.guild(guild).staff_min_role()
        if min_role_id is None:
            return False
        return self._meets_min_role(member, min_role_id, guild)

    async def _require_staff(self, ctx: commands.Context) -> bool:
        if not await self.is_staff(ctx.author, ctx.guild):
            await ctx.send(
                "You don't have permission to manage suggestions.", ephemeral=True
            )
            return False
        return True

    # -------------------- embed / rendering --------------------

    def build_embed(self, rec: dict, guild: discord.Guild) -> discord.Embed:
        color, status_label = STATUS_META.get(rec["status"], STATUS_META["open"])
        embed = discord.Embed(
            title=f"Suggestion #{rec['id']}: {rec['title']}",
            description=rec["details"],
            color=color,
        )
        try:
            embed.timestamp = datetime.fromisoformat(rec["created_at"])
        except (ValueError, KeyError, TypeError):
            pass

        if rec.get("anonymous"):
            author_display = "Anonymous"
        else:
            m = guild.get_member(rec["author_id"])
            author_display = m.mention if m else f"<@{rec['author_id']}>"
        embed.add_field(name="Submitted by", value=author_display, inline=True)
        embed.add_field(name="Status", value=status_label, inline=True)

        up = len(rec["upvotes"])
        down = len(rec["downvotes"])
        score = up - down
        sign = "+" if score >= 0 else ""
        embed.add_field(
            name="Votes",
            value=f"✅ {up}   ❌ {down}   ·   Score: {sign}{score}",
            inline=False,
        )

        notes = rec.get("notes", [])
        if notes:
            lines = []
            for n in notes[-5:]:
                who = guild.get_member(n["author_id"])
                who_s = who.mention if who else f"<@{n['author_id']}>"
                lines.append(f"{who_s}: {n['text']}")
            val = "\n".join(lines)
            if len(val) > 1024:
                val = val[:1021] + "..."
            embed.add_field(name="Staff Notes", value=val, inline=False)

        embed.set_footer(text=f"Suggestion ID: {rec['id']}")
        return embed

    async def _refresh_open_embed(self, guild: discord.Guild, rec: dict) -> None:
        """Edit the live post-channel embed in place (keeps vote buttons)."""
        channel = guild.get_channel(rec["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(rec["message_id"])
            await message.edit(embed=self.build_embed(rec, guild))
        except discord.NotFound:
            pass

    async def _edit_archived(self, guild: discord.Guild, rec: dict) -> None:
        """Edit an already-archived embed in place (no buttons)."""
        channel = guild.get_channel(rec["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(rec["message_id"])
            await message.edit(embed=self.build_embed(rec, guild))
        except discord.NotFound:
            pass

    async def _resolve_in_place(self, guild: discord.Guild, rec: dict) -> None:
        """Fallback when no archive channel: strip buttons in the post channel."""
        channel = guild.get_channel(rec["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(rec["message_id"])
            await message.edit(embed=self.build_embed(rec, guild), view=None)
        except discord.NotFound:
            pass

    async def _move_to_archive(self, guild: discord.Guild, rec: dict) -> bool:
        """Repost the (button-less) embed to the archive channel and delete original.

        Returns False if no archive channel is configured. Mutates rec's
        message_id/channel_id/archived so the caller persists the new location.
        """
        archive_id = await self.config.guild(guild).archive_channel()
        archive_channel = guild.get_channel(archive_id) if archive_id else None
        if archive_channel is None:
            return False

        new_msg = await archive_channel.send(embed=self.build_embed(rec, guild))

        old_channel = guild.get_channel(rec["channel_id"])
        if old_channel:
            try:
                old = await old_channel.fetch_message(rec["message_id"])
                await old.delete()
            except discord.NotFound:
                pass

        rec["message_id"] = new_msg.id
        rec["channel_id"] = archive_channel.id
        rec["archived"] = True
        return True

    async def _log(self, guild: discord.Guild, text: str) -> None:
        log_id = await self.config.guild(guild).log_channel()
        if not log_id:
            return
        ch = guild.get_channel(log_id)
        if ch is None:
            return
        try:
            await ch.send(text)
        except discord.Forbidden:
            pass

    # -------------------- suggestion creation / voting --------------------

    async def create_suggestion(
        self, interaction: discord.Interaction, title: str, details: str, anon_raw: str
    ) -> None:
        guild = interaction.guild
        anonymous = str(anon_raw or "").strip().lower() in _TRUTHY

        post_id = await self.config.guild(guild).post_channel()
        post_channel = guild.get_channel(post_id) if post_id else None
        if post_channel is None:
            await interaction.response.send_message(
                "The suggestion post channel is not configured. Ask an admin.",
                ephemeral=True,
            )
            return

        async with self._id_lock:
            sid = await self.config.guild(guild).next_id()
            await self.config.guild(guild).next_id.set(sid + 1)

        rec = {
            "id": sid,
            "title": title,
            "details": details,
            "author_id": interaction.user.id,
            "anonymous": anonymous,
            "message_id": None,
            "channel_id": post_channel.id,
            "archived": False,
            "status": "open",
            "upvotes": [],
            "downvotes": [],
            "notes": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status_by": None,
            "status_at": None,
        }

        message = await post_channel.send(
            embed=self.build_embed(rec, guild), view=SuggestVoteView(cog=self)
        )
        rec["message_id"] = message.id

        async with self.config.guild(guild).suggestions() as suggestions:
            suggestions[str(sid)] = rec

        await interaction.response.send_message(
            f"✅ Your suggestion **#{sid}** has been posted in {post_channel.mention}.",
            ephemeral=True,
        )
        await self._log(
            guild,
            f"📝 New suggestion **#{sid}** posted"
            + (" (anonymous)." if anonymous else f" by {interaction.user.mention}."),
        )

    async def handle_vote(
        self, interaction: discord.Interaction, sid: int, direction: str
    ) -> None:
        guild = interaction.guild
        member = interaction.user

        reason = await self._participation_reason(member, guild, voting=True)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        msg = ""
        open_for_voting = True
        async with self.config.guild(guild).suggestions() as suggestions:
            rec = suggestions.get(str(sid))
            if not rec or rec["status"] != "open":
                open_for_voting = False
            else:
                uid = member.id
                up = rec["upvotes"]
                down = rec["downvotes"]
                if direction == "up":
                    if uid in up:
                        up.remove(uid)
                        msg = "Removed your ✅ vote."
                    else:
                        up.append(uid)
                        if uid in down:
                            down.remove(uid)
                        msg = "Recorded your ✅ vote."
                else:
                    if uid in down:
                        down.remove(uid)
                        msg = "Removed your ❌ vote."
                    else:
                        down.append(uid)
                        if uid in up:
                            up.remove(uid)
                        msg = "Recorded your ❌ vote."
                await self._refresh_open_embed(guild, rec)

        if not open_for_voting:
            await interaction.response.send_message(
                "This suggestion is closed for voting.", ephemeral=True
            )
            return
        await interaction.response.send_message(msg, ephemeral=True)

    # -------------------- user command --------------------

    @app_commands.command(name="suggest", description="Submit a suggestion")
    @app_commands.guild_only()
    async def suggest(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        conf = await self.config.guild(guild).all()

        if not conf["enabled"]:
            await interaction.response.send_message(
                "Suggestions are not enabled on this server.", ephemeral=True
            )
            return
        if not conf["submit_channel"] or not conf["post_channel"]:
            await interaction.response.send_message(
                "Suggestions aren't fully configured yet. Ask an admin.", ephemeral=True
            )
            return
        if interaction.channel_id != conf["submit_channel"]:
            await interaction.response.send_message(
                f"Please use <#{conf['submit_channel']}> to submit suggestions.",
                ephemeral=True,
            )
            return

        reason = await self._participation_reason(interaction.user, guild, voting=False)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_modal(SuggestModal(self))

    # -------------------- staff commands --------------------

    @commands.hybrid_group(name="suggestmod")
    @commands.guild_only()
    async def suggestmod(self, ctx: commands.Context) -> None:
        """Staff actions for triaging suggestions."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    async def _change_status(
        self, ctx: commands.Context, sid: int, status: str, reason: Optional[str]
    ) -> None:
        if not await self._require_staff(ctx):
            return
        guild = ctx.guild
        now = datetime.now(timezone.utc).isoformat()
        warn = ""
        found = True

        async with self.config.guild(guild).suggestions() as suggestions:
            rec = suggestions.get(str(sid))
            if not rec:
                found = False
            else:
                rec["status"] = status
                rec["status_by"] = ctx.author.id
                rec["status_at"] = now
                if reason:
                    rec["notes"].append(
                        {"author_id": ctx.author.id, "text": f"[{status}] {reason}", "at": now}
                    )
                if rec["archived"]:
                    await self._edit_archived(guild, rec)
                else:
                    moved = await self._move_to_archive(guild, rec)
                    if not moved:
                        await self._resolve_in_place(guild, rec)
                        warn = " (archive channel not set — resolved in the post channel)"

        if not found:
            await ctx.send(f"No suggestion **#{sid}** found.", ephemeral=True)
            return
        await ctx.send(
            f"✅ Suggestion **#{sid}** marked **{status}**.{warn}", ephemeral=True
        )
        await self._log(
            guild,
            f"📌 {ctx.author.mention} set suggestion **#{sid}** to **{status}**."
            + (f" Reason: {reason}" if reason else ""),
        )

    @suggestmod.command(name="approve")
    @app_commands.describe(suggestion_id="The suggestion number", reason="Optional note")
    async def suggestmod_approve(
        self, ctx: commands.Context, suggestion_id: int, *, reason: str = None
    ) -> None:
        """Approve a suggestion (moves it to the archive channel)."""
        await self._change_status(ctx, suggestion_id, "approved", reason)

    @suggestmod.command(name="deny")
    @app_commands.describe(suggestion_id="The suggestion number", reason="Optional note")
    async def suggestmod_deny(
        self, ctx: commands.Context, suggestion_id: int, *, reason: str = None
    ) -> None:
        """Deny a suggestion (moves it to the archive channel)."""
        await self._change_status(ctx, suggestion_id, "denied", reason)

    @suggestmod.command(name="implement")
    @app_commands.describe(suggestion_id="The suggestion number", reason="Optional note")
    async def suggestmod_implement(
        self, ctx: commands.Context, suggestion_id: int, *, reason: str = None
    ) -> None:
        """Mark a suggestion implemented (moves/updates it in the archive channel)."""
        await self._change_status(ctx, suggestion_id, "implemented", reason)

    @suggestmod.command(name="note")
    @app_commands.describe(suggestion_id="The suggestion number", text="The note to attach")
    async def suggestmod_note(
        self, ctx: commands.Context, suggestion_id: int, *, text: str
    ) -> None:
        """Attach a public staff note to a suggestion."""
        if not await self._require_staff(ctx):
            return
        guild = ctx.guild
        now = datetime.now(timezone.utc).isoformat()
        found = True
        async with self.config.guild(guild).suggestions() as suggestions:
            rec = suggestions.get(str(suggestion_id))
            if not rec:
                found = False
            else:
                rec["notes"].append({"author_id": ctx.author.id, "text": text, "at": now})
                if rec["archived"]:
                    await self._edit_archived(guild, rec)
                else:
                    await self._refresh_open_embed(guild, rec)
        if not found:
            await ctx.send(f"No suggestion **#{suggestion_id}** found.", ephemeral=True)
            return
        await ctx.send(
            f"✅ Note added to suggestion **#{suggestion_id}**.", ephemeral=True
        )
        await self._log(
            guild, f"🗒️ {ctx.author.mention} noted on **#{suggestion_id}**: {text}"
        )

    @suggestmod.command(name="list")
    @app_commands.describe(status="Optional filter: open, approved, denied, implemented")
    async def suggestmod_list(
        self, ctx: commands.Context, status: str = None
    ) -> None:
        """List suggestions, optionally filtered by status."""
        if not await self._require_staff(ctx):
            return
        status = status.lower() if status else None
        if status and status not in STATUS_META:
            await ctx.send(
                "Status must be one of: open, approved, denied, implemented.",
                ephemeral=True,
            )
            return

        suggestions = await self.config.guild(ctx.guild).suggestions()
        items = [
            rec
            for rec in suggestions.values()
            if status is None or rec["status"] == status
        ]
        items.sort(key=lambda r: r["id"])

        embed = discord.Embed(
            title="📋 Suggestions" + (f" — {status}" if status else ""),
            color=discord.Color.blurple(),
        )
        if not items:
            embed.description = "No suggestions found."
        else:
            lines = []
            for rec in items[:25]:
                score = len(rec["upvotes"]) - len(rec["downvotes"])
                _, label = STATUS_META.get(rec["status"], STATUS_META["open"])
                lines.append(
                    f"**#{rec['id']}** — {rec['title']} · {label} · Score {score:+d}"
                )
            embed.description = "\n".join(lines)
            if len(items) > 25:
                embed.set_footer(text=f"Showing 25 of {len(items)}")
        await ctx.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="suggestreveal",
        description="Reveal the author and voters of a suggestion (staff only)",
    )
    @app_commands.guild_only()
    @app_commands.describe(suggestion_id="The suggestion number")
    async def suggestreveal(
        self, interaction: discord.Interaction, suggestion_id: int
    ) -> None:
        guild = interaction.guild
        if not await self.is_staff(interaction.user, guild):
            await interaction.response.send_message(
                "You don't have permission to reveal suggestions.", ephemeral=True
            )
            return

        suggestions = await self.config.guild(guild).suggestions()
        rec = suggestions.get(str(suggestion_id))
        if not rec:
            await interaction.response.send_message(
                f"No suggestion **#{suggestion_id}** found.", ephemeral=True
            )
            return

        def fmt(ids):
            if not ids:
                return "*none*"
            value = ", ".join(f"<@{i}>" for i in ids)
            return value if len(value) <= 1024 else value[:1021] + "..."

        author = f"<@{rec['author_id']}>"
        if rec.get("anonymous"):
            author += " *(posted anonymously)*"

        embed = discord.Embed(
            title=f"🔎 Reveal — Suggestion #{suggestion_id}",
            description=rec["title"],
            color=discord.Color.orange(),
        )
        embed.add_field(name="Author", value=author, inline=False)
        embed.add_field(
            name=f"✅ Upvotes ({len(rec['upvotes'])})",
            value=fmt(rec["upvotes"]),
            inline=False,
        )
        embed.add_field(
            name=f"❌ Downvotes ({len(rec['downvotes'])})",
            value=fmt(rec["downvotes"]),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log(
            guild,
            f"🔎 {interaction.user.mention} revealed the author/voters of **#{suggestion_id}**.",
        )

    # -------------------- config commands --------------------

    @commands.hybrid_group(name="suggestset")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def suggestset(self, ctx: commands.Context) -> None:
        """Configure ShadySuggest (administrators only)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @suggestset.command(name="submitchannel")
    @app_commands.describe(channel="Channel where /suggest may be used")
    async def suggestset_submitchannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where members submit suggestions."""
        await self.config.guild(ctx.guild).submit_channel.set(channel.id)
        await ctx.send(f"✅ Submit channel set to {channel.mention}.", ephemeral=True)

    @suggestset.command(name="postchannel")
    @app_commands.describe(channel="Channel where open suggestion embeds are posted")
    async def suggestset_postchannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where open suggestions are posted."""
        await self.config.guild(ctx.guild).post_channel.set(channel.id)
        await ctx.send(f"✅ Post channel set to {channel.mention}.", ephemeral=True)

    @suggestset.command(name="archivechannel")
    @app_commands.describe(channel="Channel where resolved suggestions are moved")
    async def suggestset_archivechannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where resolved suggestions are archived."""
        await self.config.guild(ctx.guild).archive_channel.set(channel.id)
        await ctx.send(f"✅ Archive channel set to {channel.mention}.", ephemeral=True)

    @suggestset.command(name="logchannel")
    @app_commands.describe(channel="Optional staff audit-log channel")
    async def suggestset_logchannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the optional staff audit-log channel."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.", ephemeral=True)

    @suggestset.command(name="participantrole")
    @app_commands.describe(role="Minimum role (by hierarchy) to suggest and vote")
    async def suggestset_participantrole(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        """Set the minimum role required to suggest and vote."""
        await self.config.guild(ctx.guild).participant_min_role.set(role.id)
        await ctx.send(
            f"✅ Members with {role.mention} or higher can now suggest and vote.",
            ephemeral=True,
        )

    @suggestset.command(name="staffrole")
    @app_commands.describe(role="Minimum role (by hierarchy) for staff actions")
    async def suggestset_staffrole(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        """Set the minimum role required for staff actions."""
        await self.config.guild(ctx.guild).staff_min_role.set(role.id)
        await ctx.send(
            f"✅ Members with {role.mention} or higher can now approve/deny/note/reveal.",
            ephemeral=True,
        )

    @suggestset.command(name="blocklist")
    @app_commands.describe(action="add or remove", role="Role to block from voting")
    async def suggestset_blocklist(
        self, ctx: commands.Context, action: str, role: discord.Role
    ) -> None:
        """Add or remove a role from the vote blocklist (voting only)."""
        action = action.lower()
        if action not in ("add", "remove"):
            await ctx.send("Action must be `add` or `remove`.", ephemeral=True)
            return
        async with self.config.guild(ctx.guild).vote_blocklist_roles() as roles:
            if action == "add":
                if role.id in roles:
                    await ctx.send(
                        f"{role.mention} is already blocked from voting.", ephemeral=True
                    )
                    return
                roles.append(role.id)
                await ctx.send(
                    f"✅ {role.mention} can no longer vote (can still suggest).",
                    ephemeral=True,
                )
            else:
                if role.id not in roles:
                    await ctx.send(
                        f"{role.mention} is not on the vote blocklist.", ephemeral=True
                    )
                    return
                roles.remove(role.id)
                await ctx.send(
                    f"✅ {role.mention} can vote again.", ephemeral=True
                )

    @suggestset.command(name="enable")
    async def suggestset_enable(self, ctx: commands.Context) -> None:
        """Enable suggestions on this server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ Suggestions enabled.", ephemeral=True)

    @suggestset.command(name="disable")
    async def suggestset_disable(self, ctx: commands.Context) -> None:
        """Disable suggestions on this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("✅ Suggestions disabled.", ephemeral=True)

    @suggestset.command(name="view")
    async def suggestset_view(self, ctx: commands.Context) -> None:
        """Show the current ShadySuggest configuration."""
        conf = await self.config.guild(ctx.guild).all()

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        def role(rid):
            r = ctx.guild.get_role(rid) if rid else None
            return r.mention if r else ("Not set" if not rid else f"`{rid}` (missing)")

        blocklist = conf["vote_blocklist_roles"]
        block_mentions = []
        for rid in blocklist:
            r = ctx.guild.get_role(rid)
            if r:
                block_mentions.append(r.mention)

        embed = discord.Embed(
            title="⚙️ ShadySuggest Settings",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Enabled", value="✅ Yes" if conf["enabled"] else "❌ No", inline=True
        )
        embed.add_field(name="Submit channel", value=chan(conf["submit_channel"]), inline=True)
        embed.add_field(name="Post channel", value=chan(conf["post_channel"]), inline=True)
        embed.add_field(name="Archive channel", value=chan(conf["archive_channel"]), inline=True)
        embed.add_field(name="Log channel", value=chan(conf["log_channel"]), inline=True)
        embed.add_field(
            name="Participant min role",
            value=role(conf["participant_min_role"]),
            inline=True,
        )
        embed.add_field(
            name="Staff min role", value=role(conf["staff_min_role"]), inline=True
        )
        embed.add_field(
            name="Vote blocklist",
            value=", ".join(block_mentions) if block_mentions else "None",
            inline=False,
        )
        embed.set_footer(text=f"v{self.__version__}")
        await ctx.send(embed=embed, ephemeral=True)
