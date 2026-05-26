"""
ShadyGiveaway - Advanced giveaway system with prize code management
Features prize claim verification with Yes/No buttons, automatic rerolls, role requirements,
and bonus entries for Nitro/special event roles.
"""

import asyncio
import aiohttp
import csv
import discord
import io
import json
import logging
import random
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_timedelta
from discord import app_commands

# Try to import cryptography, track if it fails
CRYPTO_AVAILABLE = True
CRYPTO_ERROR = None
try:
    from cryptography.fernet import Fernet
except ImportError as e:
    CRYPTO_AVAILABLE = False
    CRYPTO_ERROR = str(e)
    Fernet = None  # Placeholder

log = logging.getLogger("red.shadycogs.shadygiveaway")


class GiveawayCreateModal(discord.ui.Modal, title="Create Giveaway"):
    """Modal for creating a new giveaway - basic info only."""

    prize_description = discord.ui.TextInput(
        label="Prize Name & Description",
        style=discord.TextStyle.paragraph,
        placeholder="Line 1: Prize name (e.g., Discord Nitro)\nLine 2+: Optional description",
        required=True,
        max_length=500,
    )
    
    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="e.g., 24h, 3d, 1w (s/m/h/d/w)",
        required=True,
        max_length=20,
    )
    
    winners_count = discord.ui.TextInput(
        label="Number of Winners",
        placeholder="1",
        required=True,
        max_length=2,
    )
    
    prize_code = discord.ui.TextInput(
        label="Prize Code/Key",
        placeholder="Code that winners will receive in DM",
        required=True,
        max_length=500,
    )
    
    claim_timeout = discord.ui.TextInput(
        label="Claim Timeout",
        placeholder="e.g., 1h, 30m - Time to claim after winning",
        required=True,
        max_length=20,
    )

    max_entries = discord.ui.TextInput(
        label="Max Entries (Optional)",
        placeholder="Leave empty for unlimited entries",
        required=False,
        max_length=10,
    )

    def __init__(self, cog: "ShadyGiveaway"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "This command must be run in a text channel!",
                    ephemeral=True
                )
                return
            
            duration_delta = await self.cog.parse_duration(str(self.duration))
            if duration_delta is None:
                await interaction.response.send_message(
                    "Invalid duration format. Use formats like `30m`, `2h`, `1d`, `3d`, `1w`.",
                    ephemeral=True,
                )
                return

            claim_timeout_delta = await self.cog.parse_duration(str(self.claim_timeout))
            if claim_timeout_delta is None:
                await interaction.response.send_message(
                    "Invalid claim timeout format. Use formats like `30m`, `1h`, `2h`.",
                    ephemeral=True,
                )
                return

            try:
                winners = int(str(self.winners_count))
                if winners < 1 or winners > 20:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Winners count must be a number between 1 and 20.",
                    ephemeral=True,
                )
                return

            # Parse max entries
            max_entries_value = None
            max_entries_str = str(self.max_entries).strip()
            if max_entries_str:
                try:
                    max_entries_value = int(max_entries_str)
                    if max_entries_value < 1:
                        raise ValueError
                except ValueError:
                    await interaction.response.send_message(
                        "Max entries must be a positive number or leave empty for unlimited.",
                        ephemeral=True,
                    )
                    return

            # Parse prize name and description (split on first newline)
            full_text = str(self.prize_description)
            if "\n" in full_text:
                prize_name, description = full_text.split("\n", 1)
                prize_name = prize_name.strip()
                description = description.strip()
            else:
                prize_name = full_text.strip()
                description = ""

            # Store pending giveaway data and show options view
            pending_data = {
                "channel_id": channel.id,
                "prize_name": prize_name,
                "description": description,
                "duration_seconds": int(duration_delta.total_seconds()),
                "winners_count": winners,
                "prize_code": str(self.prize_code),
                "claim_timeout_seconds": int(claim_timeout_delta.total_seconds()),
                "max_entries": max_entries_value,
                "igdb_data": None,
            }

            # Try IGDB lookup if configured
            igdb_data = await self.cog.search_igdb(interaction.guild.id, prize_name)
            if igdb_data:
                # Show confirmation with game info
                game_name = igdb_data.get("name", prize_name)
                cover_url = igdb_data.get("cover", {}).get("url", "")
                if cover_url and not cover_url.startswith("http"):
                    cover_url = "https:" + cover_url
                cover_url = cover_url.replace("t_thumb", "t_cover_big")

                confirm_embed = discord.Embed(
                    title="🎮 IGDB Game Found",
                    description=f"Is **{game_name}** the correct game?",
                    color=discord.Color.blue()
                )
                if cover_url:
                    confirm_embed.set_thumbnail(url=cover_url)
                if igdb_data.get("summary"):
                    summary = igdb_data["summary"][:200]
                    if len(igdb_data["summary"]) > 200:
                        summary += "..."
                    confirm_embed.add_field(name="Description", value=summary, inline=False)

                view = GiveawayOptionsView(self.cog, pending_data, interaction.guild)
                view.pending_igdb_data = igdb_data  # Store for later confirmation

                await interaction.response.send_message(
                    embed=confirm_embed,
                    view=IGDBConfirmThenOptionsView(self.cog, igdb_data, pending_data, interaction.guild),
                    ephemeral=True
                )
                return

            view = GiveawayOptionsView(self.cog, pending_data, interaction.guild)
            await interaction.response.send_message(
                "**Step 2: Configure Entry Requirements & Bonuses**\n\n"
                "• **Required Roles (AND):** User must have ALL selected roles\n"
                "• **Optional Roles (OR):** User must have at least ONE selected role\n"
                "• **Bonus Roles:** Grant +1 entry each\n\n"
                "Leave selects empty for no requirements:",
                view=view,
                ephemeral=True
            )
            
        except Exception as e:
            error_msg = f"**Error in modal submission:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in modal submission: {e}", exc_info=True)


class GiveawayOptionsView(discord.ui.View):
    """View for configuring giveaway role requirements and bonus entries."""

    def __init__(self, cog: "ShadyGiveaway", pending_data: Dict[str, Any], guild: discord.Guild):
        super().__init__(timeout=300)
        self.cog = cog
        self.pending_data = pending_data
        self.guild = guild

        # Selected options - now supporting multiple roles
        self.required_roles: List[int] = []  # AND logic: must have ALL
        self.optional_roles: List[int] = []  # OR logic: must have at least ONE
        self.nitro_bonus_enabled: bool = False
        self.special_bonus_role_id: Optional[int] = None
        self.scheduled_start: Optional[datetime] = None  # When to start (None = immediate)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Required Roles (must have ALL)",
        min_values=0,
        max_values=5,
        row=0
    )
    async def required_roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        """Handle required roles selection (AND logic)."""
        valid_roles = []
        invalid_roles = []

        for role in select.values:
            if role.is_bot_managed() or role.is_integration() or role.is_default():
                invalid_roles.append(role.name)
            else:
                valid_roles.append(role)

        if invalid_roles:
            await interaction.response.send_message(
                f"Skipped invalid roles: {', '.join(invalid_roles)}. Please choose regular roles.",
                ephemeral=True
            )
            return

        self.required_roles = [r.id for r in valid_roles]

        if valid_roles:
            role_names = ", ".join([r.name for r in valid_roles])
            await interaction.response.send_message(
                f"✅ **Required roles set (AND logic):**\nMust have ALL of: {role_names}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "✅ Required roles cleared - no AND requirements",
                ephemeral=True
            )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Optional Roles (must have ONE)",
        min_values=0,
        max_values=5,
        row=1
    )
    async def optional_roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        """Handle optional roles selection (OR logic)."""
        valid_roles = []
        invalid_roles = []

        for role in select.values:
            if role.is_bot_managed() or role.is_integration() or role.is_default():
                invalid_roles.append(role.name)
            else:
                valid_roles.append(role)

        if invalid_roles:
            await interaction.response.send_message(
                f"Skipped invalid roles: {', '.join(invalid_roles)}. Please choose regular roles.",
                ephemeral=True
            )
            return

        self.optional_roles = [r.id for r in valid_roles]

        if valid_roles:
            role_names = ", ".join([r.name for r in valid_roles])
            await interaction.response.send_message(
                f"✅ **Optional roles set (OR logic):**\nMust have ONE of: {role_names}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "✅ Optional roles cleared - no OR requirements",
                ephemeral=True
            )

    @discord.ui.select(
        placeholder="Nitro Bonus (+1 entry)",
        options=[
            discord.SelectOption(
                label="Nitro Bonus Disabled",
                value="disabled",
                description="No bonus entry for Nitro role",
                emoji="❌"
            ),
            discord.SelectOption(
                label="Nitro Bonus Enabled",
                value="enabled",
                description="+1 entry for users with Nitro role",
                emoji="💎"
            )
        ],
        row=2
    )
    async def nitro_toggle_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        """Handle nitro bonus toggle."""
        value = select.values[0]
        self.nitro_bonus_enabled = value == "enabled"

        status = "Enabled 💎" if self.nitro_bonus_enabled else "Disabled"
        await interaction.response.send_message(
            f"✅ Nitro bonus: **{status}**",
            ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Special Bonus Role (+1 entry, optional)",
        min_values=0,
        max_values=1,
        row=3
    )
    async def special_bonus_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        """Handle special bonus role selection."""
        if select.values:
            role = select.values[0]
            # Filter out bot-managed and integration roles
            if role.is_bot_managed() or role.is_integration() or role.is_default():
                await interaction.response.send_message(
                    "You cannot select that role. Please choose a regular role.",
                    ephemeral=True
                )
                return
            self.special_bonus_role_id = role.id
            await interaction.response.send_message(
                f"✅ Special bonus role set to: **{role.name}**",
                ephemeral=True
            )
        else:
            self.special_bonus_role_id = None
            await interaction.response.send_message(
                "✅ Special bonus role cleared - no bonus role for this giveaway",
                ephemeral=True
            )

    @discord.ui.button(label="⏰ Schedule Start", style=discord.ButtonStyle.secondary, row=4)
    async def schedule_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open modal to schedule giveaway start time."""
        modal = ScheduleStartModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Create Giveaway", style=discord.ButtonStyle.green, row=4)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm and create the giveaway."""
        await self.cog.create_giveaway(
            interaction,
            self.pending_data,
            self.required_roles,
            self.optional_roles,
            self.nitro_bonus_enabled,
            self.special_bonus_role_id,
            self.scheduled_start
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, row=4)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel giveaway creation."""
        await interaction.response.send_message("Giveaway creation cancelled.", ephemeral=True)
        self.stop()


class ScheduleStartModal(discord.ui.Modal, title="Schedule Giveaway Start"):
    """Modal for scheduling when a giveaway should start."""

    start_time = discord.ui.TextInput(
        label="Start Time",
        placeholder="e.g., 2h, 1d, tomorrow 8pm, or leave empty for immediate",
        required=False,
        max_length=50,
    )

    def __init__(self, options_view: "GiveawayOptionsView"):
        super().__init__()
        self.options_view = options_view

    async def on_submit(self, interaction: discord.Interaction):
        start_str = str(self.start_time).strip()

        if not start_str:
            self.options_view.scheduled_start = None
            await interaction.response.send_message(
                "✅ Giveaway will start **immediately** when created.",
                ephemeral=True
            )
            return

        # Try to parse as duration (e.g., "2h", "1d")
        cog = self.options_view.cog
        duration = await cog.parse_duration(start_str)

        if duration:
            self.options_view.scheduled_start = datetime.now(timezone.utc) + duration
            await interaction.response.send_message(
                f"✅ Giveaway will start <t:{int(self.options_view.scheduled_start.timestamp())}:R>\n"
                f"(At <t:{int(self.options_view.scheduled_start.timestamp())}:F>)",
                ephemeral=True
            )
        else:
            # Try common time phrases
            parsed = cog.parse_time_phrase(start_str)
            if parsed:
                self.options_view.scheduled_start = parsed
                await interaction.response.send_message(
                    f"✅ Giveaway will start <t:{int(self.options_view.scheduled_start.timestamp())}:R>\n"
                    f"(At <t:{int(self.options_view.scheduled_start.timestamp())}:F>)",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Could not parse time. Use formats like:\n"
                    "• `2h` or `1d` (relative)\n"
                    "• `tomorrow 8pm`\n"
                    "• Leave empty for immediate start",
                    ephemeral=True
                )


class PersistentGiveawayView(discord.ui.View):
    """Persistent view with Enter and Leave buttons for giveaway participation.
    
    This view survives bot restarts by encoding the giveaway_id in the custom_id
    and using a class-level interaction handler.
    """

    def __init__(self, cog: "ShadyGiveaway" = None):
        super().__init__(timeout=None)
        self.cog = cog
    
    @discord.ui.button(
        label="🎉 Enter Giveaway",
        style=discord.ButtonStyle.green,
        custom_id="shady_giveaway:enter"
    )
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle enter button click."""
        if not self.cog:
            await interaction.response.send_message(
                "Giveaway system is still loading. Please try again in a moment.",
                ephemeral=True
            )
            return
        
        # Get giveaway_id from the message
        giveaway_id = await self._get_giveaway_id_from_message(interaction)
        if not giveaway_id:
            await interaction.response.send_message(
                "Could not find giveaway information. The giveaway may have been deleted.",
                ephemeral=True
            )
            return
        
        await self.cog.handle_entry(interaction, giveaway_id)
    
    @discord.ui.button(
        label="🚪 Leave Giveaway",
        style=discord.ButtonStyle.secondary,
        custom_id="shady_giveaway:leave"
    )
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle leave button click."""
        if not self.cog:
            await interaction.response.send_message(
                "Giveaway system is still loading. Please try again in a moment.",
                ephemeral=True
            )
            return
        
        # Get giveaway_id from the message
        giveaway_id = await self._get_giveaway_id_from_message(interaction)
        if not giveaway_id:
            await interaction.response.send_message(
                "Could not find giveaway information. The giveaway may have been deleted.",
                ephemeral=True
            )
            return
        
        await self.cog.handle_leave(interaction, giveaway_id)
    
    async def _get_giveaway_id_from_message(self, interaction: discord.Interaction) -> Optional[str]:
        """Extract giveaway_id from the message embed footer."""
        if not interaction.message or not interaction.message.embeds:
            return None
        
        embed = interaction.message.embeds[0]
        if embed.footer and embed.footer.text:
            # Footer format: "Giveaway ID: {giveaway_id}"
            footer_text = embed.footer.text
            if footer_text.startswith("Giveaway ID: "):
                return footer_text.replace("Giveaway ID: ", "")
        
        return None


class PersistentClaimView(discord.ui.View):
    """Persistent view with Yes/No buttons for winners to claim prizes.

    This view survives bot restarts by storing pending claims in config
    and using persistent custom_ids with giveaway_id and winner_id encoded.
    """

    def __init__(self, cog: "ShadyGiveaway" = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="✅ Yes, I claim this prize!",
        style=discord.ButtonStyle.green,
        custom_id="shady_giveaway:claim_yes"
    )
    async def claim_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle claim yes button click."""
        if not self.cog:
            await interaction.response.send_message(
                "Giveaway system is still loading. Please try again in a moment.",
                ephemeral=True
            )
            return

        # Get claim info from embed footer
        claim_info = await self._get_claim_info_from_message(interaction)
        if not claim_info:
            await interaction.response.send_message(
                "Could not find claim information. The claim may have expired.",
                ephemeral=True
            )
            return

        giveaway_id, winner_id = claim_info

        if interaction.user.id != winner_id:
            await interaction.response.send_message("This claim prompt is not for you!", ephemeral=True)
            return

        await self.cog.handle_claim_response(interaction, giveaway_id, winner_id, claimed=True)

    @discord.ui.button(
        label="❌ No, reroll",
        style=discord.ButtonStyle.red,
        custom_id="shady_giveaway:claim_no"
    )
    async def claim_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle claim no button click."""
        if not self.cog:
            await interaction.response.send_message(
                "Giveaway system is still loading. Please try again in a moment.",
                ephemeral=True
            )
            return

        # Get claim info from embed footer
        claim_info = await self._get_claim_info_from_message(interaction)
        if not claim_info:
            await interaction.response.send_message(
                "Could not find claim information. The claim may have expired.",
                ephemeral=True
            )
            return

        giveaway_id, winner_id = claim_info

        if interaction.user.id != winner_id:
            await interaction.response.send_message("This claim prompt is not for you!", ephemeral=True)
            return

        await self.cog.handle_claim_response(interaction, giveaway_id, winner_id, claimed=False)

    async def _get_claim_info_from_message(self, interaction: discord.Interaction) -> Optional[tuple]:
        """Extract giveaway_id and winner_id from the message embed footer."""
        if not interaction.message or not interaction.message.embeds:
            return None

        embed = interaction.message.embeds[0]
        if embed.footer and embed.footer.text:
            # Footer format: "Giveaway ID: {giveaway_id} | Winner: {winner_id}"
            footer_text = embed.footer.text
            if "Giveaway ID:" in footer_text and "Winner:" in footer_text:
                try:
                    parts = footer_text.split("|")
                    giveaway_id = parts[0].replace("Giveaway ID:", "").strip()
                    winner_id = int(parts[1].replace("Winner:", "").strip())
                    return (giveaway_id, winner_id)
                except (IndexError, ValueError):
                    return None

        return None


class WinnerClaimView(discord.ui.View):
    """Non-persistent wrapper for claim view with timeout handling.

    This view manages the timeout locally but uses the persistent view for buttons.
    When timeout occurs, it triggers reroll.
    """

    def __init__(self, cog: "ShadyGiveaway", giveaway_id: str, winner_id: int, timeout_seconds: int):
        super().__init__(timeout=timeout_seconds)
        self.cog = cog
        self.giveaway_id = giveaway_id
        self.winner_id = winner_id
        self.claimed = False

        # Clear default items and add the persistent buttons manually
        self.clear_items()

        # Create buttons with unique custom_ids containing claim info
        yes_button = discord.ui.Button(
            label="✅ Yes, I claim this prize!",
            style=discord.ButtonStyle.green,
            custom_id=f"shady_claim:{giveaway_id}:{winner_id}:yes"
        )
        yes_button.callback = self._claim_yes_callback
        self.add_item(yes_button)

        no_button = discord.ui.Button(
            label="❌ No, reroll",
            style=discord.ButtonStyle.red,
            custom_id=f"shady_claim:{giveaway_id}:{winner_id}:no"
        )
        no_button.callback = self._claim_no_callback
        self.add_item(no_button)

    async def _claim_yes_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.winner_id:
            await interaction.response.send_message("This claim prompt is not for you!", ephemeral=True)
            return
        self.claimed = True
        await self.cog.handle_claim_response(interaction, self.giveaway_id, self.winner_id, claimed=True)
        self.stop()

    async def _claim_no_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.winner_id:
            await interaction.response.send_message("This claim prompt is not for you!", ephemeral=True)
            return
        self.claimed = True
        await self.cog.handle_claim_response(interaction, self.giveaway_id, self.winner_id, claimed=False)
        self.stop()

    async def on_timeout(self):
        if not self.claimed:
            await self.cog.handle_claim_timeout(self.giveaway_id, self.winner_id)


class GiveawaySelectView(discord.ui.View):
    """View with dropdown to select a giveaway for management."""

    def __init__(self, cog: "ShadyGiveaway", giveaways: List[tuple], action: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.action = action
        
        options = []
        for giveaway_id, giveaway in giveaways[:25]:  # Discord limit is 25 options
            status = "🎲 Picking" if giveaway.get("picking_winners") else "🟢 Active"
            # Use prize_name if available, fall back to description for old giveaways
            display_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
            desc = display_name[:50] + "..." if len(display_name) > 50 else display_name
            
            # Count total entries (sum of all entry weights)
            total_entries = sum(giveaway.get("entries", {}).values()) if isinstance(giveaway.get("entries"), dict) else len(giveaway.get("entries", []))
            
            options.append(
                discord.SelectOption(
                    label=desc,
                    value=giveaway_id,
                    description=f"{status} | {total_entries} entries"
                )
            )
        
        self.select = discord.ui.Select(
            placeholder="Select a giveaway...",
            options=options
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        giveaway_id = self.select.values[0]
        
        if self.action == "end":
            await self.cog.force_end_giveaway(interaction, giveaway_id)
        elif self.action == "cancel":
            await self.cog.cancel_giveaway(interaction, giveaway_id)
        elif self.action == "info":
            await self.cog.show_giveaway_info(interaction, giveaway_id)
        
        self.stop()


class NitroRoleSelectView(discord.ui.View):
    """View for selecting the server's Nitro role."""

    def __init__(self, cog: "ShadyGiveaway", guild: discord.Guild):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select the Nitro role for this server...",
        min_values=0,
        max_values=1,
        row=0
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        """Handle role selection."""
        if select.values:
            role = select.values[0]
            # Filter out bot-managed and integration roles
            if role.is_bot_managed() or role.is_integration() or role.is_default():
                await interaction.response.send_message(
                    "You cannot select that role. Please choose a regular role.",
                    ephemeral=True
                )
                return
            await self.cog.config.guild(self.guild).nitro_role_id.set(role.id)
            await interaction.response.send_message(
                f"✅ Nitro role set to: **{role.name}**\n\n"
                f"Users with this role will get +1 entry when Nitro bonus is enabled for a giveaway.",
                ephemeral=True
            )
        else:
            await self.cog.config.guild(self.guild).nitro_role_id.set(None)
            await interaction.response.send_message(
                "✅ Nitro role has been cleared. Nitro bonus will not work until a role is set.",
                ephemeral=True
            )
        self.stop()

    @discord.ui.button(label="Clear Nitro Role", style=discord.ButtonStyle.red, row=1)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Clear the configured Nitro role."""
        await self.cog.config.guild(self.guild).nitro_role_id.set(None)
        await interaction.response.send_message(
            "✅ Nitro role has been cleared. Nitro bonus will not work until a role is set.",
            ephemeral=True
        )
        self.stop()


class IGDBCredentialsModal(discord.ui.Modal, title="IGDB API Credentials"):
    """Modal for entering IGDB API credentials."""

    client_id = discord.ui.TextInput(
        label="Client ID",
        placeholder="Your Twitch/IGDB Client ID",
        required=True,
        max_length=100,
    )

    client_secret = discord.ui.TextInput(
        label="Client Secret",
        placeholder="Your Twitch/IGDB Client Secret",
        required=True,
        max_length=100,
        style=discord.TextStyle.short,
    )

    def __init__(self, cog: "ShadyGiveaway"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Encrypt and store credentials
            encrypted_id = await self.cog.encrypt_value(
                interaction.guild.id,
                str(self.client_id)
            )
            encrypted_secret = await self.cog.encrypt_value(
                interaction.guild.id,
                str(self.client_secret)
            )

            await self.cog.config.guild(interaction.guild).igdb_client_id_encrypted.set(encrypted_id)
            await self.cog.config.guild(interaction.guild).igdb_client_secret_encrypted.set(encrypted_secret)

            # Clear cached token to force refresh
            if interaction.guild.id in self.cog._igdb_tokens:
                del self.cog._igdb_tokens[interaction.guild.id]

            # Test the credentials
            token = await self.cog.get_igdb_token(interaction.guild.id)
            if token:
                await self.cog.audit_log(
                    interaction.guild,
                    "IGDB Credentials Configured",
                    user=interaction.user.mention
                )
                await interaction.response.send_message(
                    "✅ IGDB credentials saved and verified successfully!\n\n"
                    "Game information will now be fetched automatically when creating giveaways.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "⚠️ Credentials saved but verification failed. Please check your Client ID and Secret.\n\n"
                    "Get credentials at: https://dev.twitch.tv/console/apps",
                    ephemeral=True
                )

        except Exception as e:
            await interaction.response.send_message(
                f"Error saving credentials: {str(e)}",
                ephemeral=True
            )


class AuditChannelSelectView(discord.ui.View):
    """View for selecting the audit log channel."""

    def __init__(self, cog: "ShadyGiveaway", guild: discord.Guild):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select audit log channel...",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        """Handle channel selection."""
        channel = select.values[0]
        await self.cog.config.guild(self.guild).audit_channel_id.set(channel.id)

        await interaction.response.send_message(
            f"✅ Audit log channel set to: {channel.mention}\n\n"
            f"All giveaway actions will now be logged to this channel.",
            ephemeral=True
        )

        # Send test message
        await self.cog.audit_log(
            self.guild,
            "Audit Logging Enabled",
            configured_by=interaction.user.mention,
            channel=channel.mention
        )
        self.stop()


class IGDBConfirmThenOptionsView(discord.ui.View):
    """View for confirming IGDB game match and then showing giveaway options."""

    def __init__(self, cog: "ShadyGiveaway", game_data: Dict[str, Any], pending_data: Dict[str, Any],
                 guild: discord.Guild):
        super().__init__(timeout=120)
        self.cog = cog
        self.game_data = game_data
        self.pending_data = pending_data
        self.guild = guild

    @discord.ui.button(label="✅ Yes, use this game info", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm the IGDB match and proceed to options."""
        # Store IGDB data in pending_data
        self.pending_data["igdb_data"] = self.game_data

        # Show the options view
        view = GiveawayOptionsView(self.cog, self.pending_data, self.guild)
        await interaction.response.edit_message(
            content="**Step 2: Configure Entry Requirements & Bonuses**\n\n"
                    "✅ *Game info from IGDB will be included*\n\n"
                    "• **Required Roles (AND):** User must have ALL selected roles\n"
                    "• **Optional Roles (OR):** User must have at least ONE selected role\n"
                    "• **Bonus Roles:** Grant +1 entry each\n\n"
                    "Leave selects empty for no requirements:",
            embed=None,
            view=view
        )
        self.stop()

    @discord.ui.button(label="❌ Skip game info", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip IGDB data and proceed to options."""
        self.pending_data["igdb_data"] = None

        # Show the options view
        view = GiveawayOptionsView(self.cog, self.pending_data, self.guild)
        await interaction.response.edit_message(
            content="**Step 2: Configure Entry Requirements & Bonuses**\n\n"
                    "• **Required Roles (AND):** User must have ALL selected roles\n"
                    "• **Optional Roles (OR):** User must have at least ONE selected role\n"
                    "• **Bonus Roles:** Grant +1 entry each\n\n"
                    "Leave selects empty for no requirements:",
            embed=None,
            view=view
        )
        self.stop()


class IGDBConfirmView(discord.ui.View):
    """View for confirming IGDB game match."""

    def __init__(self, cog: "ShadyGiveaway", game_data: Dict[str, Any], pending_data: Dict[str, Any],
                 options_view: "GiveawayOptionsView"):
        super().__init__(timeout=120)
        self.cog = cog
        self.game_data = game_data
        self.pending_data = pending_data
        self.options_view = options_view
        self.confirmed = None

    @discord.ui.button(label="✅ Yes, this is the game", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm the IGDB match."""
        self.confirmed = True
        # Store IGDB data in pending_data
        self.pending_data["igdb_data"] = self.game_data
        await interaction.response.send_message(
            "✅ Game info will be included in the giveaway embed.",
            ephemeral=True
        )
        self.stop()

    @discord.ui.button(label="❌ No, skip game info", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip IGDB data."""
        self.confirmed = False
        await interaction.response.send_message(
            "Skipping game info. Giveaway will be created without IGDB data.",
            ephemeral=True
        )
        self.stop()


class ShadyGiveaway(commands.Cog):
    """Advanced giveaway system with prize code management and claim verification."""

    # Built-in templates for common giveaway configurations
    BUILTIN_TEMPLATES = {
        "quick_drop": {
            "description": "Fast 1-hour giveaway for quick prizes",
            "duration": "1h",
            "winners_count": 1,
            "claim_timeout": "30m",
            "required_roles": [],
            "optional_roles": [],
            "nitro_bonus": False,
        },
        "daily": {
            "description": "24-hour daily giveaway with Nitro bonus",
            "duration": "24h",
            "winners_count": 1,
            "claim_timeout": "2h",
            "required_roles": [],
            "optional_roles": [],
            "nitro_bonus": True,
        },
        "weekly": {
            "description": "Week-long giveaway with multiple winners",
            "duration": "7d",
            "winners_count": 3,
            "claim_timeout": "24h",
            "required_roles": [],
            "optional_roles": [],
            "nitro_bonus": True,
        },
        "game_night": {
            "description": "Short giveaway for game night events",
            "duration": "2h",
            "winners_count": 5,
            "claim_timeout": "30m",
            "required_roles": [],
            "optional_roles": [],
            "nitro_bonus": False,
        },
        "flash": {
            "description": "15-minute flash giveaway",
            "duration": "15m",
            "winners_count": 1,
            "claim_timeout": "15m",
            "required_roles": [],
            "optional_roles": [],
            "nitro_bonus": False,
        },
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=260288776360820736, force_registration=True)

        default_guild = {
            "giveaways": {},
            "nitro_role_id": None,
            "blacklisted_users": [],  # User IDs blocked from entering giveaways
            "custom_templates": {},  # Custom giveaway templates (max 10)
            "giveaway_history": {},  # Completed giveaway data keyed by giveaway_id
            "encryption_key": None,  # Fernet key for encrypting sensitive data
            "igdb_client_id_encrypted": None,  # Encrypted IGDB client ID
            "igdb_client_secret_encrypted": None,  # Encrypted IGDB client secret
            "audit_channel_id": None,  # Channel for audit logs
            "pending_claims": {},  # Pending winner claims for persistence
        }
        self.config.register_guild(**default_guild)

        self.giveaway_check_task = None
        self.claim_check_task = None

        # IGDB token cache: {guild_id: {"access_token": str, "expires_at": float}}
        self._igdb_tokens: Dict[int, Dict[str, Any]] = {}

        # Create persistent views and set cog reference
        self.persistent_view = PersistentGiveawayView(cog=self)
        self.persistent_claim_view = PersistentClaimView(cog=self)
        
    async def cog_load(self):
        """Start background tasks and register persistent views when cog loads."""
        # Register the persistent views so buttons work after restart
        self.bot.add_view(self.persistent_view)
        self.bot.add_view(self.persistent_claim_view)

        # Restore pending claims
        await self.restore_pending_claims()

        self.giveaway_check_task = asyncio.create_task(self.check_ended_giveaways())
        self.claim_check_task = asyncio.create_task(self.check_expired_claims())
        log.info("ShadyGiveaway: Persistent views registered, background tasks started")

    async def cog_unload(self):
        """Cancel background tasks when cog unloads."""
        if self.giveaway_check_task:
            self.giveaway_check_task.cancel()
        if self.claim_check_task:
            self.claim_check_task.cancel()

    # ==================== ENCRYPTION METHODS ====================

    async def get_or_create_encryption_key(self, guild_id: int) -> bytes:
        """Get or create the encryption key for a guild."""
        key = await self.config.guild_from_id(guild_id).encryption_key()
        if not key:
            key = Fernet.generate_key().decode()
            await self.config.guild_from_id(guild_id).encryption_key.set(key)
        return key.encode()

    async def encrypt_value(self, guild_id: int, value: str) -> str:
        """Encrypt a string value for storage."""
        if not value:
            return ""
        key = await self.get_or_create_encryption_key(guild_id)
        f = Fernet(key)
        return f.encrypt(value.encode()).decode()

    async def decrypt_value(self, guild_id: int, encrypted: str) -> str:
        """Decrypt a stored encrypted value."""
        if not encrypted:
            return ""
        try:
            key = await self.get_or_create_encryption_key(guild_id)
            f = Fernet(key)
            return f.decrypt(encrypted.encode()).decode()
        except Exception as e:
            log.error(f"Error decrypting value: {e}")
            return ""

    # ==================== IGDB API METHODS ====================

    async def get_igdb_token(self, guild_id: int) -> Optional[str]:
        """Get a valid IGDB access token, refreshing if needed."""
        # Check cached token first
        cached = self._igdb_tokens.get(guild_id)
        if cached and cached["expires_at"] > time.time():
            return cached["access_token"]

        # Get encrypted credentials
        encrypted_client_id = await self.config.guild_from_id(guild_id).igdb_client_id_encrypted()
        encrypted_client_secret = await self.config.guild_from_id(guild_id).igdb_client_secret_encrypted()

        if not encrypted_client_id or not encrypted_client_secret:
            return None

        # Decrypt credentials
        client_id = await self.decrypt_value(guild_id, encrypted_client_id)
        client_secret = await self.decrypt_value(guild_id, encrypted_client_secret)

        if not client_id or not client_secret:
            return None

        # Request new token from Twitch OAuth
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://id.twitch.tv/oauth2/token"
                params = {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials"
                }
                async with session.post(url, params=params) as resp:
                    if resp.status != 200:
                        log.error(f"IGDB token request failed: {resp.status}")
                        return None
                    data = await resp.json()

            # Cache token with 60 second buffer
            self._igdb_tokens[guild_id] = {
                "access_token": data["access_token"],
                "expires_at": time.time() + data.get("expires_in", 3600) - 60
            }
            return data["access_token"]

        except Exception as e:
            log.error(f"Error getting IGDB token: {e}")
            return None

    async def search_igdb(self, guild_id: int, game_name: str) -> Optional[Dict[str, Any]]:
        """Search IGDB for a game by name."""
        token = await self.get_igdb_token(guild_id)
        if not token:
            return None

        encrypted_client_id = await self.config.guild_from_id(guild_id).igdb_client_id_encrypted()
        client_id = await self.decrypt_value(guild_id, encrypted_client_id)

        if not client_id:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.igdb.com/v4/games"
                headers = {
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {token}"
                }
                # Escape quotes in game name
                safe_name = game_name.replace('"', '\\"')
                body = f'search "{safe_name}"; fields name,cover.url,summary,first_release_date,platforms.name; limit 5;'

                async with session.post(url, headers=headers, data=body) as resp:
                    if resp.status != 200:
                        log.error(f"IGDB search failed: {resp.status}")
                        return None
                    games = await resp.json()
                    return games[0] if games else None

        except Exception as e:
            log.error(f"Error searching IGDB: {e}")
            return None

    # ==================== AUDIT LOGGING ====================

    async def audit_log(self, guild: discord.Guild, action: str, **details):
        """Send an audit log entry to the configured channel."""
        channel_id = await self.config.guild(guild).audit_channel_id()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # Build embed
        embed = discord.Embed(
            title="📋 Giveaway Audit Log",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Action", value=action, inline=False)

        # Add details
        for key, value in details.items():
            if value is not None:
                # Format key nicely
                formatted_key = key.replace("_", " ").title()
                embed.add_field(name=formatted_key, value=str(value), inline=True)

        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Error sending audit log: {e}")

    # ==================== PERSISTENT CLAIMS ====================

    async def restore_pending_claims(self):
        """Restore pending claim views after bot restart."""
        for guild in self.bot.guilds:
            try:
                pending_claims = await self.config.guild(guild).pending_claims()
                now = datetime.now(timezone.utc).timestamp()

                for giveaway_id, claim_data in list(pending_claims.items()):
                    expires_at = claim_data.get("expires_at", 0)

                    # If claim has expired, trigger reroll
                    if now >= expires_at:
                        log.info(f"Pending claim {giveaway_id} expired during restart, triggering reroll")
                        # Remove from pending and trigger timeout
                        async with self.config.guild(guild).pending_claims() as claims:
                            if giveaway_id in claims:
                                del claims[giveaway_id]
                        await self.handle_claim_timeout(giveaway_id, claim_data.get("winner_id"))
                    else:
                        # Claim still valid - the persistent view will handle button clicks
                        log.info(f"Restored pending claim for {giveaway_id}, expires in {int(expires_at - now)}s")

            except Exception as e:
                log.error(f"Error restoring pending claims for {guild.name}: {e}")

    async def check_expired_claims(self):
        """Background task to check for expired claims and trigger rerolls."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                await asyncio.sleep(30)

                for guild in self.bot.guilds:
                    pending_claims = await self.config.guild(guild).pending_claims()
                    now = datetime.now(timezone.utc).timestamp()

                    for giveaway_id, claim_data in list(pending_claims.items()):
                        expires_at = claim_data.get("expires_at", 0)

                        if now >= expires_at:
                            log.info(f"Claim for {giveaway_id} expired, triggering reroll")
                            # Remove from pending
                            async with self.config.guild(guild).pending_claims() as claims:
                                if giveaway_id in claims:
                                    del claims[giveaway_id]
                            # Trigger timeout handler
                            await self.handle_claim_timeout(giveaway_id, claim_data.get("winner_id"))

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in claim check task: {e}", exc_info=True)

    # ==================== EMBED UPDATE HELPER ====================

    async def update_giveaway_embed(self, guild: discord.Guild, giveaway_id: str, disable_button: bool = False):
        """Update the giveaway embed with current entry count."""
        giveaways = await self.config.guild(guild).giveaways()
        giveaway = giveaways.get(giveaway_id)

        if not giveaway or giveaway.get("ended"):
            return

        try:
            channel = guild.get_channel(giveaway["channel_id"])
            if not channel:
                return

            message = await channel.fetch_message(giveaway["message_id"])
            if not message.embeds:
                return

            embed = message.embeds[0]

            # Calculate entry count
            entries = giveaway.get("entries", {})
            if isinstance(entries, dict):
                entry_count = len(entries)
            else:
                entry_count = len(entries)

            max_entries = giveaway.get("max_entries")

            # Format entry count field
            if max_entries:
                entry_text = f"**{entry_count}/{max_entries}**"
            else:
                entry_text = f"**{entry_count}**"

            # Find or add Entries field
            entries_field_idx = None
            for i, field in enumerate(embed.fields):
                if field.name.startswith("🎫"):
                    entries_field_idx = i
                    break

            if entries_field_idx is not None:
                embed.set_field_at(entries_field_idx, name="🎫 Entries", value=entry_text, inline=True)
            else:
                # Insert after "Ends" field or at position 2
                insert_idx = 2
                for i, field in enumerate(embed.fields):
                    if field.name == "Ends":
                        insert_idx = i + 1
                        break
                embed.insert_field_at(insert_idx, name="🎫 Entries", value=entry_text, inline=True)

            # Update view if needed
            if disable_button or (max_entries and entry_count >= max_entries):
                # Create disabled view
                view = discord.ui.View(timeout=None)
                enter_btn = discord.ui.Button(
                    label="🎉 Enter Giveaway",
                    style=discord.ButtonStyle.green,
                    custom_id="shady_giveaway:enter",
                    disabled=True
                )
                leave_btn = discord.ui.Button(
                    label="🚪 Leave Giveaway",
                    style=discord.ButtonStyle.secondary,
                    custom_id="shady_giveaway:leave"
                )
                view.add_item(enter_btn)
                view.add_item(leave_btn)
                await message.edit(embed=embed, view=view)
            else:
                await message.edit(embed=embed)

        except discord.NotFound:
            log.debug(f"Message not found for giveaway {giveaway_id}")
        except Exception as e:
            log.error(f"Error updating giveaway embed: {e}")

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage giveaways."""
        if not isinstance(interaction.user, discord.Member):
            return True
        
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True
        
        try:
            cogs_dir = Path(__file__).parent.parent
            roles_file = cogs_dir / "wiki" / "config" / "roles.json"
            
            if roles_file.exists():
                with open(roles_file, "r", encoding="utf-8") as f:
                    roles_data = json.load(f)
                    allowed_roles = roles_data.get("authorized_roles", [])
                    return any(role.name in allowed_roles for role in interaction.user.roles)
        except Exception as e:
            log.error(f"Error reading roles.json: {e}")
        
        return False

    async def parse_duration(self, duration_str: str) -> Optional[timedelta]:
        """Parse duration string like '1h', '30m', '2d' into timedelta."""
        duration_str = duration_str.strip().lower()
        if not duration_str:
            return None
        
        unit = duration_str[-1]
        try:
            value = int(duration_str[:-1])
        except ValueError:
            return None
        
        multipliers = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800,
        }
        
        if unit not in multipliers:
            return None
        
        return timedelta(seconds=value * multipliers[unit])

    def parse_time_phrase(self, phrase: str) -> Optional[datetime]:
        """Parse common time phrases like 'tomorrow 8pm' into datetime."""
        import re
        phrase = phrase.lower().strip()
        now = datetime.now(timezone.utc)

        # Handle "tomorrow" with optional time
        if phrase.startswith("tomorrow"):
            tomorrow = now + timedelta(days=1)
            time_part = phrase.replace("tomorrow", "").strip()

            if time_part:
                # Try to parse time like "8pm", "8:00pm", "20:00"
                time_match = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_part)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2) or 0)
                    am_pm = time_match.group(3)

                    if am_pm == "pm" and hour != 12:
                        hour += 12
                    elif am_pm == "am" and hour == 12:
                        hour = 0

                    return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Default to noon tomorrow
            return tomorrow.replace(hour=12, minute=0, second=0, microsecond=0)

        # Handle "today" with optional time
        if phrase.startswith("today"):
            time_part = phrase.replace("today", "").strip()

            if time_part:
                time_match = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_part)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2) or 0)
                    am_pm = time_match.group(3)

                    if am_pm == "pm" and hour != 12:
                        hour += 12
                    elif am_pm == "am" and hour == 12:
                        hour = 0

                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # If time has passed today, return None
                    if target <= now:
                        return None
                    return target

        return None

    def calculate_entries(
        self,
        member: discord.Member,
        giveaway: Dict[str, Any],
        nitro_role_id: Optional[int]
    ) -> int:
        """Calculate how many entries a member gets based on their roles."""
        entries = 1  # Base entry
        
        # Check Nitro bonus
        if giveaway.get("nitro_bonus_enabled") and nitro_role_id:
            if any(r.id == nitro_role_id for r in member.roles):
                entries += 1
        
        # Check special bonus role
        special_role_id = giveaway.get("special_bonus_role_id")
        if special_role_id:
            if any(r.id == special_role_id for r in member.roles):
                entries += 1
        
        return entries

    def check_role_requirement(
        self,
        member: discord.Member,
        giveaway: Dict[str, Any]
    ) -> tuple[bool, str]:
        """Check if member meets the role requirements.

        Returns (passed, error_message).
        Supports backward compatibility with old min_role_id format.
        """
        member_role_ids = {r.id for r in member.roles}

        # Check required roles (AND logic) - must have ALL
        required_roles = giveaway.get("required_roles", [])

        # Backward compatibility: migrate old min_role_id to required_roles
        if not required_roles and giveaway.get("min_role_id"):
            min_role_id = giveaway["min_role_id"]
            min_role = member.guild.get_role(min_role_id)
            if min_role:
                # Old behavior: check if user has min role or higher
                has_required = False
                for role in member.roles:
                    if role.position >= min_role.position and not role.is_default():
                        has_required = True
                        break
                if not has_required:
                    return False, f"You need the **{min_role.name}** role or higher to enter!"
            return True, ""

        # New AND logic: must have ALL required roles
        if required_roles:
            missing = []
            for role_id in required_roles:
                role = member.guild.get_role(role_id)
                if role and role_id not in member_role_ids:
                    missing.append(role.name)

            if missing:
                return False, f"You need ALL of these roles to enter: **{', '.join(missing)}**"

        # Check optional roles (OR logic) - must have at least ONE
        optional_roles = giveaway.get("optional_roles", [])
        if optional_roles:
            has_one = False
            valid_roles = []
            for role_id in optional_roles:
                role = member.guild.get_role(role_id)
                if role:
                    valid_roles.append(role.name)
                    if role_id in member_role_ids:
                        has_one = True
                        break

            if not has_one and valid_roles:
                return False, f"You need ONE of these roles to enter: **{', '.join(valid_roles)}**"

        return True, ""

    @app_commands.command(name="giveawaynitro", description="Set the Nitro role for bonus entries")
    async def giveawaynitro(self, interaction: discord.Interaction):
        """Configure which role counts as 'Nitro' for bonus entries."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to configure giveaways.",
                    ephemeral=True
                )
                return
            
            current_nitro_id = await self.config.guild(interaction.guild).nitro_role_id()
            current_role = interaction.guild.get_role(current_nitro_id) if current_nitro_id else None
            
            current_text = f"**Current Nitro role:** {current_role.mention if current_role else 'Not set'}\n\n"
            
            view = NitroRoleSelectView(self, interaction.guild)
            await interaction.response.send_message(
                f"{current_text}Select the role that represents Nitro subscribers in your server:",
                view=view,
                ephemeral=True
            )
            
        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawaynitro command: {e}", exc_info=True)

    @app_commands.command(name="giveawayblacklist", description="Manage giveaway blacklist")
    @app_commands.describe(
        action="Action to perform",
        user="User to add/remove (required for add/remove actions)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add user to blacklist", value="add"),
        app_commands.Choice(name="Remove user from blacklist", value="remove"),
        app_commands.Choice(name="List blacklisted users", value="list"),
        app_commands.Choice(name="Clear entire blacklist", value="clear"),
    ])
    async def giveawayblacklist(
        self,
        interaction: discord.Interaction,
        action: str,
        user: Optional[discord.Member] = None
    ):
        """Manage the giveaway blacklist - prevent specific users from entering."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to manage the blacklist.",
                    ephemeral=True
                )
                return

            if action == "add":
                if not user:
                    await interaction.response.send_message(
                        "Please specify a user to add to the blacklist.",
                        ephemeral=True
                    )
                    return

                blacklist = await self.config.guild(interaction.guild).blacklisted_users()
                if user.id in blacklist:
                    await interaction.response.send_message(
                        f"{user.mention} is already blacklisted.",
                        ephemeral=True
                    )
                    return

                async with self.config.guild(interaction.guild).blacklisted_users() as bl:
                    bl.append(user.id)

                log.info(f"Giveaway blacklist: {interaction.user} added {user} (ID: {user.id}) in {interaction.guild}")
                await self.audit_log(
                    interaction.guild,
                    "User Blacklisted",
                    user=user.mention,
                    added_by=interaction.user.mention
                )
                await interaction.response.send_message(
                    f"Added {user.mention} to the giveaway blacklist.",
                    ephemeral=True
                )

            elif action == "remove":
                if not user:
                    await interaction.response.send_message(
                        "Please specify a user to remove from the blacklist.",
                        ephemeral=True
                    )
                    return

                blacklist = await self.config.guild(interaction.guild).blacklisted_users()
                if user.id not in blacklist:
                    await interaction.response.send_message(
                        f"{user.mention} is not blacklisted.",
                        ephemeral=True
                    )
                    return

                async with self.config.guild(interaction.guild).blacklisted_users() as bl:
                    bl.remove(user.id)

                log.info(f"Giveaway blacklist: {interaction.user} removed {user} (ID: {user.id}) in {interaction.guild}")
                await self.audit_log(
                    interaction.guild,
                    "User Unblacklisted",
                    user=user.mention,
                    removed_by=interaction.user.mention
                )
                await interaction.response.send_message(
                    f"Removed {user.mention} from the giveaway blacklist.",
                    ephemeral=True
                )

            elif action == "list":
                blacklist = await self.config.guild(interaction.guild).blacklisted_users()

                if not blacklist:
                    await interaction.response.send_message(
                        "The giveaway blacklist is empty.",
                        ephemeral=True
                    )
                    return

                # Build user list
                user_mentions = []
                for uid in blacklist:
                    member = interaction.guild.get_member(uid)
                    if member:
                        user_mentions.append(f"• {member.mention} (`{uid}`)")
                    else:
                        user_mentions.append(f"• Unknown User (`{uid}`)")

                embed = discord.Embed(
                    title="Giveaway Blacklist",
                    description="\n".join(user_mentions[:25]),
                    color=discord.Color.red()
                )

                if len(blacklist) > 25:
                    embed.set_footer(text=f"Showing 25 of {len(blacklist)} blacklisted users")

                await interaction.response.send_message(embed=embed, ephemeral=True)

            elif action == "clear":
                blacklist = await self.config.guild(interaction.guild).blacklisted_users()

                if not blacklist:
                    await interaction.response.send_message(
                        "The blacklist is already empty.",
                        ephemeral=True
                    )
                    return

                count = len(blacklist)
                await self.config.guild(interaction.guild).blacklisted_users.set([])

                log.info(f"Giveaway blacklist: {interaction.user} cleared {count} users in {interaction.guild}")
                await interaction.response.send_message(
                    f"Cleared {count} users from the giveaway blacklist.",
                    ephemeral=True
                )

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawayblacklist command: {e}", exc_info=True)

    @app_commands.command(name="giveawayset", description="Configure giveaway settings")
    @app_commands.describe(setting="Setting to configure")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Set IGDB Credentials", value="igdb"),
        app_commands.Choice(name="Set Audit Log Channel", value="audit"),
        app_commands.Choice(name="Clear IGDB Credentials", value="clear_igdb"),
        app_commands.Choice(name="Clear Audit Channel", value="clear_audit"),
        app_commands.Choice(name="View Current Settings", value="view"),
    ])
    async def giveawayset(self, interaction: discord.Interaction, setting: str):
        """Configure giveaway system settings."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to configure giveaway settings.",
                    ephemeral=True
                )
                return

            if setting == "igdb":
                modal = IGDBCredentialsModal(self)
                await interaction.response.send_modal(modal)

            elif setting == "audit":
                view = AuditChannelSelectView(self, interaction.guild)
                await interaction.response.send_message(
                    "Select a channel for giveaway audit logs:",
                    view=view,
                    ephemeral=True
                )

            elif setting == "clear_igdb":
                await self.config.guild(interaction.guild).igdb_client_id_encrypted.set(None)
                await self.config.guild(interaction.guild).igdb_client_secret_encrypted.set(None)
                # Clear cached token
                if interaction.guild.id in self._igdb_tokens:
                    del self._igdb_tokens[interaction.guild.id]
                await self.audit_log(interaction.guild, "IGDB Credentials Cleared", user=interaction.user.mention)
                await interaction.response.send_message(
                    "✅ IGDB credentials have been cleared.",
                    ephemeral=True
                )

            elif setting == "clear_audit":
                await self.config.guild(interaction.guild).audit_channel_id.set(None)
                await interaction.response.send_message(
                    "✅ Audit log channel has been cleared. Audit logging is now disabled.",
                    ephemeral=True
                )

            elif setting == "view":
                # Get current settings
                igdb_configured = bool(await self.config.guild(interaction.guild).igdb_client_id_encrypted())
                audit_channel_id = await self.config.guild(interaction.guild).audit_channel_id()
                audit_channel = interaction.guild.get_channel(audit_channel_id) if audit_channel_id else None
                nitro_role_id = await self.config.guild(interaction.guild).nitro_role_id()
                nitro_role = interaction.guild.get_role(nitro_role_id) if nitro_role_id else None

                embed = discord.Embed(
                    title="⚙️ Giveaway Settings",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="IGDB Integration",
                    value="✅ Configured" if igdb_configured else "❌ Not configured",
                    inline=True
                )
                embed.add_field(
                    name="Audit Channel",
                    value=audit_channel.mention if audit_channel else "Not set",
                    inline=True
                )
                embed.add_field(
                    name="Nitro Role",
                    value=nitro_role.mention if nitro_role else "Not set",
                    inline=True
                )

                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawayset command: {e}", exc_info=True)

    @app_commands.command(name="giveawayhistory", description="View giveaway history")
    @app_commands.describe(limit="Number of giveaways to show (default 10)")
    async def giveaway_history(self, interaction: discord.Interaction, limit: int = 10):
        """View recent giveaway history."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to view giveaway history.",
                    ephemeral=True
                )
                return

            history = await self.config.guild(interaction.guild).giveaway_history()

            if not history:
                await interaction.response.send_message(
                    "No giveaway history yet. History will be recorded when giveaways complete.",
                    ephemeral=True
                )
                return

            # Convert dict to list if needed (for new format)
            if isinstance(history, dict):
                history_list = list(history.values())
            else:
                history_list = history

            # Sort by ended_at (newest first) and limit
            history_list = sorted(history_list, key=lambda x: x.get("ended_at", 0), reverse=True)[:limit]

            embed = discord.Embed(
                title="📜 Giveaway History",
                description=f"Showing last {len(history_list)} giveaway(s)",
                color=discord.Color.blue()
            )

            for entry in history_list:
                prize_name = entry.get("prize_name", "Unknown")[:40]
                ended_at = entry.get("ended_at", 0)
                winners_claimed = entry.get("winners_claimed", 0)
                winners_count = entry.get("winners_count", 1)
                total_entries = entry.get("total_entries", 0)
                cancelled = entry.get("cancelled", False)

                if cancelled:
                    status = "🚫 Cancelled"
                elif isinstance(winners_claimed, list):
                    status = f"✅ {len(winners_claimed)}/{winners_count} claimed"
                else:
                    status = f"✅ {winners_claimed}/{winners_count} claimed"

                # Get winner info
                winner_ids = entry.get("winner_ids", [])
                if winner_ids:
                    winner_mentions = [f"<@{wid}>" for wid in winner_ids[:3]]
                    if len(winner_ids) > 3:
                        winner_mentions.append(f"+{len(winner_ids) - 3} more")
                    winners_text = ", ".join(winner_mentions)
                else:
                    winners_text = "None"

                embed.add_field(
                    name=f"🎁 {prize_name}",
                    value=f"Status: {status}\n"
                          f"Entries: {total_entries}\n"
                          f"Winners: {winners_text}\n"
                          f"Ended: <t:{ended_at}:R>",
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawayhistory command: {e}", exc_info=True)

    @app_commands.command(name="giveawayexport", description="Export giveaway history to CSV")
    @app_commands.describe(giveaway_id="Specific giveaway ID to export (optional)")
    async def giveaway_export(self, interaction: discord.Interaction, giveaway_id: Optional[str] = None):
        """Export giveaway history to CSV file."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to export giveaway data.",
                    ephemeral=True
                )
                return

            history = await self.config.guild(interaction.guild).giveaway_history()

            if not history:
                await interaction.response.send_message(
                    "No giveaway history to export.",
                    ephemeral=True
                )
                return

            # Convert dict to list if needed
            if isinstance(history, dict):
                history_list = list(history.values())
            else:
                history_list = history

            # Filter by giveaway_id if specified
            if giveaway_id:
                history_list = [h for h in history_list if h.get("giveaway_id") == giveaway_id]
                if not history_list:
                    await interaction.response.send_message(
                        f"No history found for giveaway ID: {giveaway_id}",
                        ephemeral=True
                    )
                    return

            # Build CSV
            output = io.StringIO()
            writer = csv.writer(output)

            # Header
            writer.writerow([
                "giveaway_id", "prize_name", "host_id", "winner_ids", "winners_claimed",
                "total_entries", "ended_at", "cancelled"
            ])

            # Data rows
            for entry in history_list:
                winner_ids = entry.get("winner_ids", [])
                ended_timestamp = entry.get("ended_at", 0)
                ended_date = datetime.fromtimestamp(ended_timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if ended_timestamp else ""

                writer.writerow([
                    entry.get("giveaway_id", ""),
                    entry.get("prize_name", ""),
                    entry.get("host_id", ""),
                    ";".join(str(w) for w in winner_ids),
                    entry.get("winners_claimed", 0) if isinstance(entry.get("winners_claimed"), int) else len(entry.get("winners_claimed", [])),
                    entry.get("total_entries", 0),
                    ended_date,
                    "Yes" if entry.get("cancelled") else "No"
                ])

            # Create file
            output.seek(0)
            csv_content = output.getvalue()
            file = discord.File(
                io.BytesIO(csv_content.encode()),
                filename=f"giveaway_history_{interaction.guild.id}.csv"
            )

            await interaction.response.send_message(
                f"📄 Exported {len(history_list)} giveaway(s) to CSV:",
                file=file,
                ephemeral=True
            )

            await self.audit_log(
                interaction.guild,
                "History Exported",
                user=interaction.user.mention,
                records=len(history_list)
            )

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawayexport command: {e}", exc_info=True)

    @app_commands.command(name="giveawaytemplate", description="Manage giveaway templates")
    @app_commands.describe(
        action="Action to perform",
        name="Template name (for save/delete/use actions)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="List all templates", value="list"),
        app_commands.Choice(name="Use a template", value="use"),
        app_commands.Choice(name="Save current settings as template", value="save"),
        app_commands.Choice(name="Delete a custom template", value="delete"),
    ])
    async def giveawaytemplate(
        self,
        interaction: discord.Interaction,
        action: str,
        name: Optional[str] = None
    ):
        """Manage giveaway templates for quick creation."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to manage templates.",
                    ephemeral=True
                )
                return

            if action == "list":
                await self.list_templates(interaction)

            elif action == "use":
                if not name:
                    await interaction.response.send_message(
                        "Please specify a template name. Use `/giveawaytemplate list` to see available templates.",
                        ephemeral=True
                    )
                    return
                await self.use_template(interaction, name.lower())

            elif action == "save":
                if not name:
                    await interaction.response.send_message(
                        "Please specify a name for the template.",
                        ephemeral=True
                    )
                    return
                await self.save_template(interaction, name.lower())

            elif action == "delete":
                if not name:
                    await interaction.response.send_message(
                        "Please specify the template name to delete.",
                        ephemeral=True
                    )
                    return
                await self.delete_template(interaction, name.lower())

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawaytemplate command: {e}", exc_info=True)

    async def list_templates(self, interaction: discord.Interaction):
        """List all available templates."""
        custom_templates = await self.config.guild(interaction.guild).custom_templates()

        embed = discord.Embed(
            title="📋 Giveaway Templates",
            description="Use `/giveawaytemplate use <name>` to create a giveaway from a template.",
            color=discord.Color.blue()
        )

        # Built-in templates
        builtin_text = []
        for name, template in self.BUILTIN_TEMPLATES.items():
            desc = template.get("description", "")
            duration = template.get("duration", "24h")
            winners = template.get("winners_count", 1)
            nitro = "💎" if template.get("nitro_bonus") else ""
            builtin_text.append(f"• **{name}** - {duration}, {winners} winner(s) {nitro}\n  _{desc}_")

        embed.add_field(
            name="🔧 Built-in Templates",
            value="\n".join(builtin_text) if builtin_text else "None",
            inline=False
        )

        # Custom templates
        if custom_templates:
            custom_text = []
            for name, template in custom_templates.items():
                duration = template.get("duration", "24h")
                winners = template.get("winners_count", 1)
                desc = template.get("description", "Custom template")
                custom_text.append(f"• **{name}** - {duration}, {winners} winner(s)\n  _{desc}_")

            embed.add_field(
                name="⭐ Custom Templates",
                value="\n".join(custom_text[:10]),
                inline=False
            )
        else:
            embed.add_field(
                name="⭐ Custom Templates",
                value="None - Use `/giveawaytemplate save <name>` to create one",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def use_template(self, interaction: discord.Interaction, name: str):
        """Create a giveaway using a template."""
        # Check built-in templates first
        template = self.BUILTIN_TEMPLATES.get(name)

        # Then check custom templates
        if not template:
            custom_templates = await self.config.guild(interaction.guild).custom_templates()
            template = custom_templates.get(name)

        if not template:
            await interaction.response.send_message(
                f"Template `{name}` not found. Use `/giveawaytemplate list` to see available templates.",
                ephemeral=True
            )
            return

        # Show modal with pre-filled values from template
        modal = TemplateGiveawayModal(self, template, name)
        await interaction.response.send_modal(modal)

    async def save_template(self, interaction: discord.Interaction, name: str):
        """Save a custom template (opens modal for details)."""
        # Check if name conflicts with built-in
        if name in self.BUILTIN_TEMPLATES:
            await interaction.response.send_message(
                f"Cannot use name `{name}` - it's a built-in template name.",
                ephemeral=True
            )
            return

        # Check custom template limit
        custom_templates = await self.config.guild(interaction.guild).custom_templates()
        if len(custom_templates) >= 10 and name not in custom_templates:
            await interaction.response.send_message(
                "Maximum 10 custom templates allowed. Delete one first.",
                ephemeral=True
            )
            return

        # Show modal to configure template
        modal = SaveTemplateModal(self, name)
        await interaction.response.send_modal(modal)

    async def delete_template(self, interaction: discord.Interaction, name: str):
        """Delete a custom template."""
        if name in self.BUILTIN_TEMPLATES:
            await interaction.response.send_message(
                "Cannot delete built-in templates.",
                ephemeral=True
            )
            return

        custom_templates = await self.config.guild(interaction.guild).custom_templates()
        if name not in custom_templates:
            await interaction.response.send_message(
                f"Custom template `{name}` not found.",
                ephemeral=True
            )
            return

        async with self.config.guild(interaction.guild).custom_templates() as templates:
            del templates[name]

        await interaction.response.send_message(
            f"✅ Deleted custom template `{name}`.",
            ephemeral=True
        )


class TemplateGiveawayModal(discord.ui.Modal, title="Create Giveaway from Template"):
    """Modal for creating a giveaway from a template - just needs prize info."""

    prize_description = discord.ui.TextInput(
        label="Prize Name & Description",
        style=discord.TextStyle.paragraph,
        placeholder="Line 1: Prize name\nLine 2+: Optional description",
        required=True,
        max_length=500,
    )

    prize_code = discord.ui.TextInput(
        label="Prize Code/Key",
        placeholder="Code that winners will receive in DM",
        required=True,
        max_length=500,
    )

    def __init__(self, cog: "ShadyGiveaway", template: Dict[str, Any], template_name: str):
        super().__init__()
        self.cog = cog
        self.template = template
        self.template_name = template_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "This command must be run in a text channel!",
                    ephemeral=True
                )
                return

            # Parse template values
            duration_str = self.template.get("duration", "24h")
            duration_delta = await self.cog.parse_duration(duration_str)
            if duration_delta is None:
                duration_delta = timedelta(hours=24)

            claim_timeout_str = self.template.get("claim_timeout", "1h")
            claim_timeout_delta = await self.cog.parse_duration(claim_timeout_str)
            if claim_timeout_delta is None:
                claim_timeout_delta = timedelta(hours=1)

            # Parse prize name and description
            full_text = str(self.prize_description)
            if "\n" in full_text:
                prize_name, description = full_text.split("\n", 1)
                prize_name = prize_name.strip()
                description = description.strip()
            else:
                prize_name = full_text.strip()
                description = ""

            # Build pending data from template
            pending_data = {
                "channel_id": channel.id,
                "prize_name": prize_name,
                "description": description,
                "duration_seconds": int(duration_delta.total_seconds()),
                "winners_count": self.template.get("winners_count", 1),
                "prize_code": str(self.prize_code),
                "claim_timeout_seconds": int(claim_timeout_delta.total_seconds()),
            }

            # Get role requirements from template
            required_roles = self.template.get("required_roles", [])
            optional_roles = self.template.get("optional_roles", [])
            nitro_bonus = self.template.get("nitro_bonus", False)
            special_bonus_role = self.template.get("special_bonus_role_id")

            # Create the giveaway directly
            await self.cog.create_giveaway(
                interaction,
                pending_data,
                required_roles,
                optional_roles,
                nitro_bonus,
                special_bonus_role,
                None  # No scheduled start
            )

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in template giveaway modal: {e}", exc_info=True)


class SaveTemplateModal(discord.ui.Modal, title="Save Custom Template"):
    """Modal for saving a custom giveaway template."""

    description = discord.ui.TextInput(
        label="Template Description",
        placeholder="What is this template for?",
        required=True,
        max_length=100,
    )

    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="e.g., 24h, 3d, 1w",
        required=True,
        max_length=20,
    )

    winners_count = discord.ui.TextInput(
        label="Number of Winners",
        placeholder="1",
        required=True,
        max_length=2,
    )

    claim_timeout = discord.ui.TextInput(
        label="Claim Timeout",
        placeholder="e.g., 1h, 30m",
        required=True,
        max_length=20,
    )

    def __init__(self, cog: "ShadyGiveaway", name: str):
        super().__init__()
        self.cog = cog
        self.name = name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validate inputs
            duration_delta = await self.cog.parse_duration(str(self.duration))
            if duration_delta is None:
                await interaction.response.send_message(
                    "Invalid duration format.",
                    ephemeral=True
                )
                return

            claim_delta = await self.cog.parse_duration(str(self.claim_timeout))
            if claim_delta is None:
                await interaction.response.send_message(
                    "Invalid claim timeout format.",
                    ephemeral=True
                )
                return

            try:
                winners = int(str(self.winners_count))
                if winners < 1 or winners > 20:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Winners must be between 1 and 20.",
                    ephemeral=True
                )
                return

            # Save template
            template = {
                "description": str(self.description),
                "duration": str(self.duration),
                "winners_count": winners,
                "claim_timeout": str(self.claim_timeout),
                "required_roles": [],
                "optional_roles": [],
                "nitro_bonus": False,
            }

            async with self.cog.config.guild(interaction.guild).custom_templates() as templates:
                templates[self.name] = template

            await interaction.response.send_message(
                f"✅ Saved custom template `{self.name}`!\n"
                f"Duration: {self.duration} | Winners: {winners} | Claim: {self.claim_timeout}",
                ephemeral=True
            )

        except Exception as e:
            error_msg = f"**Error:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error saving template: {e}", exc_info=True)

    @app_commands.command(name="giveaway", description="Create or list giveaways")
    @app_commands.describe(action="Action to perform")
    @app_commands.choices(action=[
        app_commands.Choice(name="Create", value="create"),
        app_commands.Choice(name="List Active", value="list"),
        app_commands.Choice(name="View Stats", value="stats"),
    ])
    async def giveaway(self, interaction: discord.Interaction, action: str):
        """Main giveaway command handler."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to manage giveaways.",
                    ephemeral=True
                )
                return

            if action == "create":
                modal = GiveawayCreateModal(self)
                await interaction.response.send_modal(modal)

            elif action == "list":
                await self.list_giveaways(interaction)

            elif action == "stats":
                await self.get_giveaway_stats(interaction)

        except Exception as e:
            error_msg = f"**Error in giveaway command:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveaway command: {e}", exc_info=True)

    @app_commands.command(name="giveawaymanage", description="Manage active giveaways (end early, cancel, view info)")
    @app_commands.describe(action="Action to perform on a giveaway")
    @app_commands.choices(action=[
        app_commands.Choice(name="End Early (pick winners now)", value="end"),
        app_commands.Choice(name="Cancel (no winners)", value="cancel"),
        app_commands.Choice(name="View Info", value="info"),
    ])
    async def giveawaymanage(self, interaction: discord.Interaction, action: str):
        """Manage active giveaways with dropdown selection."""
        try:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "You don't have permission to manage giveaways.",
                    ephemeral=True
                )
                return
            
            giveaways = await self.config.guild(interaction.guild).giveaways()
            active = [(gid, g) for gid, g in giveaways.items() if not g["ended"]]
            
            if not active:
                await interaction.response.send_message("No active giveaways to manage.", ephemeral=True)
                return
            
            view = GiveawaySelectView(self, active, action)
            
            action_text = {
                "end": "end early (pick winners now)",
                "cancel": "cancel (no winners picked)",
                "info": "view detailed info for"
            }
            
            await interaction.response.send_message(
                f"Select a giveaway to {action_text[action]}:",
                view=view,
                ephemeral=True
            )
            
        except Exception as e:
            error_msg = f"**Error in giveawaymanage command:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error in giveawaymanage command: {e}", exc_info=True)

    async def force_end_giveaway(self, interaction: discord.Interaction, giveaway_id: str):
        """Force end a giveaway and pick winners."""
        giveaways = await self.config.guild(interaction.guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        
        if not giveaway:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        
        if giveaway["ended"]:
            await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)
            return
        
        await interaction.response.send_message(
            f"Ending giveaway **{giveaway['description']}** and picking winners...",
            ephemeral=True
        )
        
        await self.end_giveaway(interaction.guild, giveaway_id, giveaway)

    async def cancel_giveaway(self, interaction: discord.Interaction, giveaway_id: str):
        """Cancel a giveaway without picking winners."""
        giveaways = await self.config.guild(interaction.guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        
        if not giveaway:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        
        if giveaway["ended"]:
            await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)
            return
        
        # Mark as ended without picking winners
        async with self.config.guild(interaction.guild).giveaways() as all_giveaways:
            all_giveaways[giveaway_id]["ended"] = True
            all_giveaways[giveaway_id]["cancelled"] = True

        prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")

        # Record stats for analytics
        await self.record_giveaway_stats(interaction.guild, giveaway_id, giveaway)

        # Audit log
        await self.audit_log(
            interaction.guild,
            "Giveaway Cancelled",
            giveaway=f"{prize_name} (`{giveaway_id}`)",
            cancelled_by=interaction.user.mention,
            entries=len(giveaway.get("entries", {}))
        )

        # Update the giveaway message
        try:
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            if channel:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.red()
                embed.title = "🚫 GIVEAWAY CANCELLED"
                await message.edit(embed=embed, view=None)
                await channel.send(f"Giveaway **{prize_name}** has been cancelled by {interaction.user.mention}.")
        except Exception as e:
            log.error(f"Error updating cancelled giveaway message: {e}")

        await interaction.response.send_message(
            f"Giveaway **{prize_name}** has been cancelled.",
            ephemeral=True
        )

    async def show_giveaway_info(self, interaction: discord.Interaction, giveaway_id: str):
        """Show detailed info about a giveaway."""
        giveaways = await self.config.guild(interaction.guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        
        if not giveaway:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        
        channel = interaction.guild.get_channel(giveaway["channel_id"])
        host = interaction.guild.get_member(giveaway["host_id"])
        
        # Determine status
        if giveaway.get("cancelled"):
            status = "🚫 Cancelled"
            color = discord.Color.red()
        elif giveaway["ended"]:
            status = "✅ Complete"
            color = discord.Color.green()
        elif giveaway.get("status") == "scheduled":
            status = "⏰ Scheduled"
            color = discord.Color.blue()
        elif giveaway.get("picking_winners", False):
            status = "🎲 Picking Winners"
            color = discord.Color.orange()
        else:
            status = "🟢 Active"
            color = discord.Color.gold()
        
        prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
        description = giveaway.get("description", "")
        
        embed = discord.Embed(
            title=f"🎉 Giveaway: {prize_name}",
            description=description if description else None,
            color=color
        )
        
        embed.add_field(name="Channel", value=channel.mention if channel else "Unknown", inline=True)
        embed.add_field(name="Host", value=host.mention if host else "Unknown", inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Winners Needed", value=str(giveaway["winners_count"]), inline=True)
        
        # Entry count - handle both old (list) and new (dict) formats
        entries = giveaway.get("entries", {})
        if isinstance(entries, dict):
            unique_entrants = len(entries)
            total_entries = sum(entries.values())
            embed.add_field(name="Entrants", value=str(unique_entrants), inline=True)
            embed.add_field(name="Total Entries", value=str(total_entries), inline=True)
        else:
            embed.add_field(name="Total Entries", value=str(len(entries)), inline=True)
        
        embed.add_field(name="Winners Claimed", value=str(len(giveaway.get("winners_claimed", []))), inline=True)
        embed.add_field(name="Winners Picked", value=str(len(giveaway.get("winners_picked", []))), inline=True)
        embed.add_field(name="Claim Timeout", value=humanize_timedelta(seconds=giveaway["claim_timeout_seconds"]), inline=True)
        embed.add_field(name="Ends/Ended", value=f"<t:{giveaway['end_timestamp']}:R>", inline=True)
        
        # Role requirements - support both old and new formats
        req_parts = []

        # New format: required_roles (AND logic)
        required_roles = giveaway.get("required_roles", [])
        if required_roles:
            role_mentions = []
            for role_id in required_roles:
                role = interaction.guild.get_role(role_id)
                role_mentions.append(role.mention if role else "Deleted")
            req_parts.append(f"**ALL:** {' & '.join(role_mentions)}")

        # New format: optional_roles (OR logic)
        optional_roles = giveaway.get("optional_roles", [])
        if optional_roles:
            role_mentions = []
            for role_id in optional_roles:
                role = interaction.guild.get_role(role_id)
                role_mentions.append(role.mention if role else "Deleted")
            req_parts.append(f"**ONE:** {' | '.join(role_mentions)}")

        # Old format: min_role_id (backward compatibility)
        if not required_roles and not optional_roles:
            min_role_id = giveaway.get("min_role_id")
            if min_role_id:
                min_role = interaction.guild.get_role(min_role_id)
                req_parts.append(f"{min_role.mention}+ (hierarchy)" if min_role else "Deleted Role")

        embed.add_field(
            name="Requirements",
            value="\n".join(req_parts) if req_parts else "None",
            inline=False
        )
        
        # Bonus info
        bonuses = []
        if giveaway.get("nitro_bonus_enabled"):
            nitro_role_id = await self.config.guild(interaction.guild).nitro_role_id()
            nitro_role = interaction.guild.get_role(nitro_role_id) if nitro_role_id else None
            bonuses.append(f"💎 Nitro ({nitro_role.mention if nitro_role else 'Not configured'})")
        
        special_role_id = giveaway.get("special_bonus_role_id")
        if special_role_id:
            special_role = interaction.guild.get_role(special_role_id)
            bonuses.append(f"⭐ {special_role.mention if special_role else 'Deleted Role'}")
        
        embed.add_field(name="Bonus Roles", value="\n".join(bonuses) if bonuses else "None", inline=False)
        
        embed.add_field(name="Giveaway ID", value=f"`{giveaway_id}`", inline=False)
        
        # Show participants if less than 20
        if isinstance(entries, dict) and len(entries) <= 20 and entries:
            participants = [f"<@{uid}> ({count})" for uid, count in entries.items()]
            embed.add_field(
                name=f"Participants ({len(participants)})",
                value=", ".join(participants),
                inline=False
            )
        elif isinstance(entries, list) and len(entries) <= 20 and entries:
            participants = [f"<@{uid}>" for uid in entries]
            embed.add_field(
                name=f"Participants ({len(participants)})",
                value=", ".join(participants),
                inline=False
            )
        
        # Show claimed winners
        if giveaway.get("winners_claimed"):
            winners = [f"<@{uid}>" for uid in giveaway["winners_claimed"]]
            embed.add_field(
                name="Claimed Winners",
                value=", ".join(winners),
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def create_giveaway(
        self,
        interaction: discord.Interaction,
        pending_data: Dict[str, Any],
        required_roles: List[int],
        optional_roles: List[int],
        nitro_bonus_enabled: bool,
        special_bonus_role_id: Optional[int],
        scheduled_start: Optional[datetime] = None,
    ):
        """Create a new giveaway with role requirements and optional scheduling."""
        try:
            channel = interaction.guild.get_channel(pending_data["channel_id"])
            if not channel:
                await interaction.response.send_message("Channel not found!", ephemeral=True)
                return

            giveaway_id = f"{interaction.guild.id}_{int(datetime.now(timezone.utc).timestamp())}"
            duration = timedelta(seconds=pending_data["duration_seconds"])

            # Determine if scheduled or immediate
            is_scheduled = scheduled_start is not None and scheduled_start > datetime.now(timezone.utc)

            if is_scheduled:
                # End time is calculated from scheduled start
                end_time = scheduled_start + duration
                status = "scheduled"
            else:
                # Immediate start
                end_time = datetime.now(timezone.utc) + duration
                scheduled_start = None  # Clear if in the past
                status = "active"

            # Get prize name and description
            prize_name = pending_data["prize_name"]
            description = pending_data.get("description", "")
            max_entries = pending_data.get("max_entries")

            # Get IGDB data if available
            igdb_data = pending_data.get("igdb_data")

            # Encrypt the prize code
            encrypted_prize_code = await self.encrypt_value(
                interaction.guild.id,
                pending_data["prize_code"]
            )

            # Build requirement/bonus text (shared between both embed types)
            req_text = []
            if required_roles:
                role_mentions = []
                for role_id in required_roles:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        role_mentions.append(role.mention)
                if role_mentions:
                    req_text.append(f"**Must have ALL:** {' AND '.join(role_mentions)}")

            if optional_roles:
                role_mentions = []
                for role_id in optional_roles:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        role_mentions.append(role.mention)
                if role_mentions:
                    req_text.append(f"**Must have ONE:** {' OR '.join(role_mentions)}")

            bonus_text = []
            if nitro_bonus_enabled:
                nitro_role_id = await self.config.guild(interaction.guild).nitro_role_id()
                if nitro_role_id:
                    nitro_role = interaction.guild.get_role(nitro_role_id)
                    bonus_text.append(f"💎 {nitro_role.mention}: +1 entry" if nitro_role else "")

            if special_bonus_role_id:
                special_role = interaction.guild.get_role(special_bonus_role_id)
                bonus_text.append(f"⭐ {special_role.mention}: +1 entry" if special_role else "")

            # Build embed description with IGDB summary if available
            embed_description = description if description else None
            if igdb_data and igdb_data.get("summary"):
                summary = igdb_data["summary"][:200]
                if len(igdb_data["summary"]) > 200:
                    summary += "..."
                if embed_description:
                    embed_description += f"\n\n**About this game:** {summary}"
                else:
                    embed_description = f"**About this game:** {summary}"

            # Build embed based on status
            if is_scheduled:
                embed = discord.Embed(
                    title=f"⏰ UPCOMING GIVEAWAY: {prize_name}",
                    description=embed_description,
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Winners", value=str(pending_data["winners_count"]), inline=True)
                embed.add_field(name="Starts", value=f"<t:{int(scheduled_start.timestamp())}:R>", inline=True)
                embed.add_field(
                    name="Duration",
                    value=humanize_timedelta(timedelta=duration),
                    inline=True
                )
                embed.add_field(name="Hosted by", value=interaction.user.mention, inline=True)
            else:
                embed = discord.Embed(
                    title=f"🎉 GIVEAWAY: {prize_name}",
                    description=embed_description,
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Winners", value=str(pending_data["winners_count"]), inline=True)
                embed.add_field(name="Ends", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)
                # Add entry count field
                if max_entries:
                    embed.add_field(name="🎫 Entries", value=f"**0/{max_entries}**", inline=True)
                else:
                    embed.add_field(name="🎫 Entries", value="**0**", inline=True)
                embed.add_field(name="Hosted by", value=interaction.user.mention, inline=True)

            if req_text or bonus_text:
                info_value = ""
                if req_text:
                    info_value += "\n".join(filter(None, req_text))
                if bonus_text:
                    if info_value:
                        info_value += "\n"
                    info_value += "\n".join(filter(None, bonus_text))
                if info_value:
                    embed.add_field(name="Entry Info", value=info_value, inline=False)

            # Add IGDB game info if available
            if igdb_data:
                # Set cover art as thumbnail
                cover_url = igdb_data.get("cover", {}).get("url")
                if cover_url:
                    # Convert thumbnail to larger image
                    cover_url = cover_url.replace("t_thumb", "t_cover_big")
                    if not cover_url.startswith("http"):
                        cover_url = "https:" + cover_url
                    embed.set_thumbnail(url=cover_url)

                # Add platforms if available
                platforms = igdb_data.get("platforms", [])
                if platforms:
                    platform_names = [p.get("name", "") for p in platforms[:5]]
                    platform_names = [p for p in platform_names if p]
                    if platform_names:
                        embed.add_field(
                            name="Platforms",
                            value=", ".join(platform_names),
                            inline=False
                        )

            embed.set_footer(text=f"Giveaway ID: {giveaway_id}")

            # Use the persistent view (disabled for scheduled)
            if is_scheduled:
                # For scheduled giveaways, no view yet (entries disabled)
                message = await channel.send(embed=embed)
            else:
                view = PersistentGiveawayView(cog=self)
                message = await channel.send(embed=embed, view=view)

            async with self.config.guild(interaction.guild).giveaways() as giveaways:
                giveaways[giveaway_id] = {
                    "message_id": message.id,
                    "channel_id": channel.id,
                    "prize_name": prize_name,
                    "description": description,
                    "host_id": interaction.user.id,
                    "winners_count": pending_data["winners_count"],
                    "prize_code_encrypted": encrypted_prize_code,  # Encrypted!
                    "claim_timeout_seconds": pending_data["claim_timeout_seconds"],
                    "duration_seconds": pending_data["duration_seconds"],  # Store for scheduled activation
                    "end_timestamp": int(end_time.timestamp()),
                    "scheduled_start": int(scheduled_start.timestamp()) if scheduled_start else None,
                    "status": status,  # "scheduled", "active", "ended", "cancelled"
                    "entries": {},  # Dict of user_id: entry_count
                    "ended": False,
                    "winners_picked": [],
                    "winners_claimed": [],
                    "required_roles": required_roles,  # AND logic
                    "optional_roles": optional_roles,  # OR logic
                    "nitro_bonus_enabled": nitro_bonus_enabled,
                    "special_bonus_role_id": special_bonus_role_id,
                    "max_entries": max_entries,  # Entry limit
                    "igdb_data": igdb_data,  # IGDB game data
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

            # Audit log
            await self.audit_log(
                interaction.guild,
                "Giveaway Created" if not is_scheduled else "Giveaway Scheduled",
                giveaway=f"{prize_name} (`{giveaway_id}`)",
                host=interaction.user.mention,
                channel=channel.mention,
                winners=pending_data["winners_count"],
                duration=humanize_timedelta(timedelta=duration),
                max_entries=max_entries if max_entries else "Unlimited"
            )

            if is_scheduled:
                await interaction.response.send_message(
                    f"✅ Giveaway scheduled in {channel.mention}!\n"
                    f"Starts: <t:{int(scheduled_start.timestamp())}:R>\n"
                    f"ID: `{giveaway_id}`",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"✅ Giveaway created in {channel.mention}!\nID: `{giveaway_id}`",
                    ephemeral=True
                )
        except Exception as e:
            error_msg = f"**Error creating giveaway:**\n```\n{type(e).__name__}: {str(e)}\n```"
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            log.error(f"Error creating giveaway: {e}", exc_info=True)

    async def handle_entry(self, interaction: discord.Interaction, giveaway_id: str):
        """Handle user entering a giveaway."""
        giveaways = await self.config.guild(interaction.guild).giveaways()
        
        if giveaway_id not in giveaways:
            await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            return
        
        giveaway = giveaways[giveaway_id]
        
        if giveaway["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        # Check if giveaway is still scheduled (not yet active)
        if giveaway.get("status") == "scheduled":
            scheduled_start = giveaway.get("scheduled_start")
            if scheduled_start:
                await interaction.response.send_message(
                    f"This giveaway hasn't started yet. Starts <t:{scheduled_start}:R>",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "This giveaway hasn't started yet.",
                    ephemeral=True
                )
            return

        member = interaction.user
        user_id_str = str(member.id)

        # Check blacklist (don't reveal they're blacklisted for privacy)
        blacklist = await self.config.guild(interaction.guild).blacklisted_users()
        if member.id in blacklist:
            await interaction.response.send_message(
                "You are not eligible to enter giveaways.",
                ephemeral=True
            )
            return
        
        # Check if already entered
        entries = giveaway.get("entries", {})
        if isinstance(entries, dict) and user_id_str in entries:
            await interaction.response.send_message("You've already entered this giveaway! Use the Leave button if you want to withdraw.", ephemeral=True)
            return
        elif isinstance(entries, list) and member.id in entries:
            await interaction.response.send_message("You've already entered this giveaway! Use the Leave button if you want to withdraw.", ephemeral=True)
            return

        # Check max entries limit
        max_entries = giveaway.get("max_entries")
        if max_entries:
            current_count = len(entries) if isinstance(entries, dict) else len(entries)
            if current_count >= max_entries:
                await interaction.response.send_message(
                    "🚫 This giveaway is full! Entry limit has been reached.",
                    ephemeral=True
                )
                return

        # Check role requirement
        passed, error_msg = self.check_role_requirement(member, giveaway)
        if not passed:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        # Calculate entries
        nitro_role_id = await self.config.guild(interaction.guild).nitro_role_id()
        entry_count = self.calculate_entries(member, giveaway, nitro_role_id)

        # Add entry
        async with self.config.guild(interaction.guild).giveaways() as all_giveaways:
            # Migrate old format if needed
            if isinstance(all_giveaways[giveaway_id].get("entries"), list):
                old_entries = all_giveaways[giveaway_id]["entries"]
                all_giveaways[giveaway_id]["entries"] = {str(uid): 1 for uid in old_entries}

            all_giveaways[giveaway_id]["entries"][user_id_str] = entry_count

        # Build response - get fresh count
        giveaways_updated = await self.config.guild(interaction.guild).giveaways()
        entries_updated = giveaways_updated[giveaway_id].get("entries", {})
        entry_count_now = len(entries_updated) if isinstance(entries_updated, dict) else len(entries_updated)

        bonus_info = ""
        if entry_count > 1:
            bonus_info = f"\n🎁 **Bonus entries:** You got {entry_count} entries!"

        # Check if giveaway is now full
        is_full = max_entries and entry_count_now >= max_entries
        full_msg = "\n⚠️ The giveaway is now full!" if is_full else ""

        await interaction.response.send_message(
            f"You've been entered into the giveaway! Good luck!{bonus_info}{full_msg}\n"
            f"*({entry_count_now} total entrants)*",
            ephemeral=True
        )

        # Update the giveaway embed with new entry count
        await self.update_giveaway_embed(interaction.guild, giveaway_id, disable_button=is_full)

    async def handle_leave(self, interaction: discord.Interaction, giveaway_id: str):
        """Handle user leaving a giveaway."""
        giveaways = await self.config.guild(interaction.guild).giveaways()
        
        if giveaway_id not in giveaways:
            await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            return
        
        giveaway = giveaways[giveaway_id]
        
        if giveaway["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        
        user_id_str = str(interaction.user.id)
        entries = giveaway.get("entries", {})
        
        # Check if user is entered
        if isinstance(entries, dict):
            if user_id_str not in entries:
                await interaction.response.send_message("You haven't entered this giveaway!", ephemeral=True)
                return

            removed_entries = entries[user_id_str]
            async with self.config.guild(interaction.guild).giveaways() as all_giveaways:
                del all_giveaways[giveaway_id]["entries"][user_id_str]

            await interaction.response.send_message(
                f"You've left the giveaway. ({removed_entries} {'entry' if removed_entries == 1 else 'entries'} removed)",
                ephemeral=True
            )
        elif isinstance(entries, list):
            if interaction.user.id not in entries:
                await interaction.response.send_message("You haven't entered this giveaway!", ephemeral=True)
                return

            async with self.config.guild(interaction.guild).giveaways() as all_giveaways:
                all_giveaways[giveaway_id]["entries"].remove(interaction.user.id)

            await interaction.response.send_message(
                "You've left the giveaway.",
                ephemeral=True
            )

        # Update the giveaway embed with new entry count
        await self.update_giveaway_embed(interaction.guild, giveaway_id)

    async def check_ended_giveaways(self):
        """Background task to check for scheduled and ended giveaways."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                await asyncio.sleep(30)

                for guild in self.bot.guilds:
                    giveaways = await self.config.guild(guild).giveaways()
                    now = datetime.now(timezone.utc).timestamp()

                    for giveaway_id, giveaway in list(giveaways.items()):
                        # Check for scheduled giveaways that need to start
                        if giveaway.get("status") == "scheduled":
                            scheduled_start = giveaway.get("scheduled_start")
                            if scheduled_start and now >= scheduled_start:
                                await self.activate_scheduled_giveaway(guild, giveaway_id, giveaway)
                                continue

                        # Check for active giveaways that need to end
                        if not giveaway.get("picking_winners", False) and not giveaway["ended"]:
                            if giveaway.get("status", "active") == "active" and now >= giveaway["end_timestamp"]:
                                await self.end_giveaway(guild, giveaway_id, giveaway)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in giveaway check task: {e}", exc_info=True)

    async def activate_scheduled_giveaway(
        self,
        guild: discord.Guild,
        giveaway_id: str,
        giveaway: Dict[str, Any]
    ):
        """Activate a scheduled giveaway - update embed and enable entries."""
        try:
            channel = guild.get_channel(giveaway["channel_id"])
            if not channel:
                log.error(f"Channel not found for scheduled giveaway {giveaway_id}")
                return

            # Calculate new end time based on duration
            duration_seconds = giveaway.get("duration_seconds", 86400)  # Default 24h
            now = datetime.now(timezone.utc)
            end_time = now + timedelta(seconds=duration_seconds)

            # Update giveaway data
            async with self.config.guild(guild).giveaways() as giveaways:
                giveaways[giveaway_id]["status"] = "active"
                giveaways[giveaway_id]["end_timestamp"] = int(end_time.timestamp())
                giveaways[giveaway_id]["scheduled_start"] = None  # Clear scheduled flag

            # Update the message with active giveaway embed
            try:
                message = await channel.fetch_message(giveaway["message_id"])
                prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
                description = giveaway.get("description", "")

                embed = discord.Embed(
                    title=f"🎉 GIVEAWAY: {prize_name}",
                    description=description if description else None,
                    color=discord.Color.gold(),
                    timestamp=now
                )
                embed.add_field(name="Winners", value=str(giveaway["winners_count"]), inline=True)
                embed.add_field(name="Ends", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)

                host = guild.get_member(giveaway["host_id"])
                embed.add_field(name="Hosted by", value=host.mention if host else "Unknown", inline=True)

                # Rebuild requirement info
                req_text = []
                required_roles = giveaway.get("required_roles", [])
                if required_roles:
                    role_mentions = []
                    for role_id in required_roles:
                        role = guild.get_role(role_id)
                        if role:
                            role_mentions.append(role.mention)
                    if role_mentions:
                        req_text.append(f"**Must have ALL:** {' AND '.join(role_mentions)}")

                optional_roles = giveaway.get("optional_roles", [])
                if optional_roles:
                    role_mentions = []
                    for role_id in optional_roles:
                        role = guild.get_role(role_id)
                        if role:
                            role_mentions.append(role.mention)
                    if role_mentions:
                        req_text.append(f"**Must have ONE:** {' OR '.join(role_mentions)}")

                bonus_text = []
                if giveaway.get("nitro_bonus_enabled"):
                    nitro_role_id = await self.config.guild(guild).nitro_role_id()
                    if nitro_role_id:
                        nitro_role = guild.get_role(nitro_role_id)
                        bonus_text.append(f"💎 {nitro_role.mention}: +1 entry" if nitro_role else "")

                special_role_id = giveaway.get("special_bonus_role_id")
                if special_role_id:
                    special_role = guild.get_role(special_role_id)
                    bonus_text.append(f"⭐ {special_role.mention}: +1 entry" if special_role else "")

                if req_text or bonus_text:
                    info_value = ""
                    if req_text:
                        info_value += "\n".join(filter(None, req_text))
                    if bonus_text:
                        if info_value:
                            info_value += "\n"
                        info_value += "\n".join(filter(None, bonus_text))
                    if info_value:
                        embed.add_field(name="Entry Info", value=info_value, inline=False)

                embed.set_footer(text=f"Giveaway ID: {giveaway_id}")

                # Add entry view
                view = PersistentGiveawayView(cog=self)
                await message.edit(embed=embed, view=view)

                # Announce activation
                await channel.send(f"🎉 **Giveaway for {prize_name} is now LIVE!** Good luck!")

                log.info(f"Activated scheduled giveaway {giveaway_id} in {guild.name}")

            except Exception as e:
                log.error(f"Error updating scheduled giveaway message: {e}")

        except Exception as e:
            log.error(f"Error activating scheduled giveaway: {e}", exc_info=True)

    async def end_giveaway(self, guild: discord.Guild, giveaway_id: str, giveaway: Dict[str, Any]):
        """End a giveaway and start picking winners."""
        async with self.config.guild(guild).giveaways() as giveaways:
            giveaways[giveaway_id]["picking_winners"] = True
        
        channel = None
        try:
            channel = guild.get_channel(giveaway["channel_id"])
            if channel:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.orange()
                embed.title = "🎉 GIVEAWAY - Picking Winners..."
                await message.edit(embed=embed, view=None)
        except Exception as e:
            log.error(f"Error updating giveaway message: {e}")
        
        entries = giveaway.get("entries", {})
        has_entries = (isinstance(entries, dict) and entries) or (isinstance(entries, list) and entries)
        
        if not has_entries:
            async with self.config.guild(guild).giveaways() as giveaways:
                giveaways[giveaway_id]["ended"] = True

            # Record stats for analytics
            await self.record_giveaway_stats(guild, giveaway_id, giveaway)

            try:
                if channel:
                    prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
                    await channel.send(f"Giveaway for **{prize_name}** ended with no entries! 😢")
                    message = await channel.fetch_message(giveaway["message_id"])
                    embed = message.embeds[0]
                    embed.color = discord.Color.red()
                    embed.title = "🎉 GIVEAWAY ENDED - No Entries"
                    await message.edit(embed=embed)
            except Exception:
                pass
            return
        
        await self.pick_and_notify_winner(guild, giveaway_id, giveaway)

    async def pick_and_notify_winner(self, guild: discord.Guild, giveaway_id: str, giveaway: Dict[str, Any]):
        """Pick a random winner from entries using weighted selection."""
        giveaways = await self.config.guild(guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        if not giveaway:
            return
        
        claimed_count = len(giveaway.get("winners_claimed", []))
        if claimed_count >= giveaway["winners_count"]:
            return
        
        entries = giveaway.get("entries", {})
        winners_picked = giveaway.get("winners_picked", [])

        # Get blacklist for filtering
        blacklist = await self.config.guild(guild).blacklisted_users()
        blacklist_set = set(blacklist)

        # Build weighted pool excluding already picked winners and blacklisted users
        if isinstance(entries, dict):
            available = {
                uid: count for uid, count in entries.items()
                if int(uid) not in winners_picked and int(uid) not in blacklist_set
            }
            if not available:
                await self._handle_no_entries_remaining(guild, giveaway_id, giveaway, claimed_count)
                return

            # Weighted random selection
            pool = []
            for uid, count in available.items():
                pool.extend([int(uid)] * count)

            winner_id = random.choice(pool)
        else:
            # Legacy list format
            available = [uid for uid in entries if uid not in winners_picked and uid not in blacklist_set]
            if not available:
                await self._handle_no_entries_remaining(guild, giveaway_id, giveaway, claimed_count)
                return
            winner_id = random.choice(available)
        
        async with self.config.guild(guild).giveaways() as all_giveaways:
            all_giveaways[giveaway_id]["winners_picked"].append(winner_id)
            if "winners_claimed" not in all_giveaways[giveaway_id]:
                all_giveaways[giveaway_id]["winners_claimed"] = []
        
        winner = guild.get_member(winner_id)
        if not winner:
            giveaways = await self.config.guild(guild).giveaways()
            await self.pick_and_notify_winner(guild, giveaway_id, giveaways.get(giveaway_id))
            return
        
        giveaways = await self.config.guild(guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        winner_number = len(giveaway["winners_picked"])
        
        prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
        
        claim_embed = discord.Embed(
            title="🎉 You Won a Giveaway!",
            description=f"Congratulations! You won **{prize_name}**!",
            color=discord.Color.gold()
        )
        
        if giveaway["winners_count"] > 1:
            claim_embed.add_field(
                name="🏆 Winner Position",
                value=f"You are winner #{winner_number} of {giveaway['winners_count']}",
                inline=False
            )
        
        claim_embed.add_field(
            name="⏰ Time to Claim",
            value=f"You have **{humanize_timedelta(seconds=giveaway['claim_timeout_seconds'])}** to claim your prize.",
            inline=False
        )
        claim_embed.add_field(
            name="📋 Instructions",
            value="Click **Yes** below to receive your prize code.\nClick **No** to decline and we'll pick another winner.",
            inline=False
        )
        # Store winner ID in footer for persistent view to read
        claim_embed.set_footer(text=f"Giveaway ID: {giveaway_id} | Winner: {winner_id}")

        view = WinnerClaimView(
            self,
            giveaway_id,
            winner_id,
            giveaway["claim_timeout_seconds"]
        )

        # Store pending claim for persistence
        expires_at = datetime.now(timezone.utc).timestamp() + giveaway["claim_timeout_seconds"]
        async with self.config.guild(guild).pending_claims() as claims:
            claims[giveaway_id] = {
                "winner_id": winner_id,
                "expires_at": expires_at,
                "prize_name": prize_name,
            }

        # Audit log - winner selected
        await self.audit_log(
            guild,
            "Winner Selected",
            giveaway=f"{prize_name} (`{giveaway_id}`)",
            winner=f"<@{winner_id}>",
            position=f"{winner_number}/{giveaway['winners_count']}",
            claim_timeout=humanize_timedelta(seconds=giveaway["claim_timeout_seconds"])
        )

        try:
            await winner.send(embed=claim_embed, view=view)
            channel = guild.get_channel(giveaway["channel_id"])
            if channel:
                await channel.send(f"🎲 {winner.mention} has been selected as a potential winner for **{prize_name}**! Check your DMs to claim.")
        except discord.Forbidden:
            channel = guild.get_channel(giveaway["channel_id"])
            if channel:
                await channel.send(
                    f"{winner.mention} You won **{prize_name}** but I can't DM you! "
                    f"Please respond here within {humanize_timedelta(seconds=giveaway['claim_timeout_seconds'])}.",
                    embed=claim_embed,
                    view=view
                )

    async def _handle_no_entries_remaining(self, guild: discord.Guild, giveaway_id: str, giveaway: Dict[str, Any], claimed_count: int):
        """Handle case when no more eligible entries remain."""
        channel = guild.get_channel(giveaway["channel_id"])
        remaining = giveaway["winners_count"] - claimed_count
        prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")

        async with self.config.guild(guild).giveaways() as all_giveaways:
            all_giveaways[giveaway_id]["ended"] = True

        # Record stats for analytics
        await self.record_giveaway_stats(guild, giveaway_id, giveaway)

        if channel:
            await channel.send(
                f"⚠️ Giveaway **{prize_name}** has ended. "
                f"Needed {remaining} more winner(s) but no eligible entries remain. "
                f"Total winners: {claimed_count}/{giveaway['winners_count']}"
            )
            try:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.orange()
                embed.title = f"🎉 GIVEAWAY ENDED - {claimed_count}/{giveaway['winners_count']} Winners"
                await message.edit(embed=embed)
            except Exception as e:
                log.error(f"Error updating partial giveaway message: {e}")

    async def handle_claim_response(
        self,
        interaction: discord.Interaction,
        giveaway_id: str,
        winner_id: int,
        claimed: bool
    ):
        """Handle winner's Yes/No response to claim."""
        try:
            guild_id = int(giveaway_id.split("_")[0])
            guild = self.bot.get_guild(guild_id)
            if not guild:
                await interaction.response.send_message(
                    "Could not find the server for this giveaway. It may have been deleted.",
                    ephemeral=True
                )
                return
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "Invalid giveaway ID format.",
                ephemeral=True
            )
            return
        
        giveaways = await self.config.guild(guild).giveaways()
        giveaway = giveaways.get(giveaway_id)
        
        if not giveaway:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        
        # Remove from pending claims
        async with self.config.guild(guild).pending_claims() as claims:
            if giveaway_id in claims:
                del claims[giveaway_id]

        if claimed:
            async with self.config.guild(guild).giveaways() as all_giveaways:
                if "winners_claimed" not in all_giveaways[giveaway_id]:
                    all_giveaways[giveaway_id]["winners_claimed"] = []
                all_giveaways[giveaway_id]["winners_claimed"].append(winner_id)

            prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")

            # Decrypt prize code - support both old and new formats
            prize_code = giveaway.get("prize_code_encrypted")
            if prize_code:
                prize_code = await self.decrypt_value(guild.id, prize_code)
            else:
                # Fallback to old unencrypted format
                prize_code = giveaway.get("prize_code", "No code available")

            code_embed = discord.Embed(
                title="🎁 Your Prize Code",
                description=f"**Prize:** {prize_name}\n\n**Code/Key:**\n```\n{prize_code}\n```",
                color=discord.Color.green()
            )
            code_embed.set_footer(text="Congratulations! Enjoy your prize!")

            await interaction.response.send_message(embed=code_embed, ephemeral=True)

            channel = guild.get_channel(giveaway["channel_id"])

            giveaways_updated = await self.config.guild(guild).giveaways()
            giveaway_updated = giveaways_updated.get(giveaway_id)

            # Audit log - winner claimed
            await self.audit_log(
                guild,
                "Winner Claimed",
                giveaway=f"{prize_name} (`{giveaway_id}`)",
                winner=f"<@{winner_id}>",
                prize_sent="Yes"
            )
            log.info(f"Prize code sent to user {winner_id} for giveaway {giveaway_id}")

            if channel:
                claimed_count = len(giveaway_updated.get("winners_claimed", []))
                if giveaway["winners_count"] > 1:
                    await channel.send(
                        f"🎉 Congratulations {interaction.user.mention} for claiming prize #{claimed_count} of {giveaway['winners_count']} for **{prize_name}**!"
                    )
                else:
                    await channel.send(f"🎉 Congratulations {interaction.user.mention} for winning **{prize_name}**!")

            if claimed_count < giveaway["winners_count"]:
                await self.pick_and_notify_winner(guild, giveaway_id, giveaway_updated)
            else:
                async with self.config.guild(guild).giveaways() as all_giveaways:
                    all_giveaways[giveaway_id]["ended"] = True

                # Record stats for analytics
                await self.record_giveaway_stats(guild, giveaway_id, giveaway_updated)

                # Audit log - giveaway complete
                await self.audit_log(
                    guild,
                    "Giveaway Completed",
                    giveaway=f"{prize_name} (`{giveaway_id}`)",
                    winners=claimed_count
                )

                try:
                    if channel:
                        message = await channel.fetch_message(giveaway["message_id"])
                        embed = message.embeds[0]
                        embed.color = discord.Color.green()
                        embed.title = "🎉 GIVEAWAY COMPLETE!"
                        await message.edit(embed=embed)
                except Exception as e:
                    log.error(f"Error updating completed giveaway message: {e}")

                winners_list = [f"<@{wid}>" for wid in giveaway_updated["winners_claimed"]]

                final_embed = discord.Embed(
                    title="🏆 Giveaway Winners!",
                    description=f"**{prize_name}**",
                    color=discord.Color.gold()
                )
                final_embed.add_field(
                    name=f"{'Winner' if len(winners_list) == 1 else 'Winners'}",
                    value=", ".join(winners_list),
                    inline=False
                )
                final_embed.set_footer(text="Congratulations to all winners!")

                if channel:
                    await channel.send(embed=final_embed)

        else:
            await interaction.response.send_message(
                "You've declined the prize. We'll pick another winner!",
                ephemeral=True
            )

            prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")

            # Audit log - winner declined
            await self.audit_log(
                guild,
                "Winner Declined",
                giveaway=f"{prize_name} (`{giveaway_id}`)",
                user=f"<@{winner_id}>",
                action="Rerolling"
            )

            channel = guild.get_channel(giveaway["channel_id"])
            if channel:
                await channel.send(f"{interaction.user.mention} declined **{prize_name}**. Picking a new winner...")

            await self.pick_and_notify_winner(guild, giveaway_id, giveaway)

    async def handle_claim_timeout(self, giveaway_id: str, winner_id: int):
        """Handle when winner doesn't respond in time."""
        for guild in self.bot.guilds:
            giveaways = await self.config.guild(guild).giveaways()
            if giveaway_id in giveaways:
                giveaway = giveaways[giveaway_id]

                # Remove from pending claims
                async with self.config.guild(guild).pending_claims() as claims:
                    if giveaway_id in claims:
                        del claims[giveaway_id]

                prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")
                channel = guild.get_channel(giveaway["channel_id"])
                winner = guild.get_member(winner_id)
                winner_mention = winner.mention if winner else f"<@{winner_id}>"

                # Audit log - claim timeout
                await self.audit_log(
                    guild,
                    "Claim Timeout",
                    giveaway=f"{prize_name} (`{giveaway_id}`)",
                    user=f"<@{winner_id}>",
                    action="Rerolling"
                )

                if channel:
                    await channel.send(
                        f"⏰ {winner_mention} didn't claim **{prize_name}** in time. Picking a new winner..."
                    )

                await self.pick_and_notify_winner(guild, giveaway_id, giveaway)
                break

    async def record_giveaway_stats(
        self,
        guild: discord.Guild,
        giveaway_id: str,
        giveaway: Dict[str, Any]
    ):
        """Record completed giveaway statistics for analytics."""
        try:
            entries = giveaway.get("entries", {})

            # Calculate stats
            if isinstance(entries, dict):
                unique_entrants = len(entries)
                total_entries = sum(entries.values())
                bonus_entries = total_entries - unique_entrants
            else:
                unique_entrants = len(entries)
                total_entries = unique_entrants
                bonus_entries = 0

            winners_picked = len(giveaway.get("winners_picked", []))
            winners_claimed = len(giveaway.get("winners_claimed", []))
            rerolls = winners_picked - winners_claimed if winners_picked > winners_claimed else 0

            # Calculate duration
            created_at = giveaway.get("created_at")
            end_timestamp = giveaway.get("end_timestamp", 0)
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at)
                    duration_seconds = end_timestamp - int(created.timestamp())
                except Exception:
                    duration_seconds = giveaway.get("duration_seconds", 0)
            else:
                duration_seconds = giveaway.get("duration_seconds", 0)

            stats_entry = {
                "giveaway_id": giveaway_id,
                "prize_name": giveaway.get("prize_name") or giveaway.get("description", "Unknown"),
                "host_id": giveaway.get("host_id"),
                "channel_id": giveaway.get("channel_id"),
                "created_at": giveaway.get("created_at"),
                "ended_at": int(datetime.now(timezone.utc).timestamp()),
                "duration_seconds": duration_seconds,
                "winners_count": giveaway.get("winners_count", 1),
                "winners_claimed": winners_claimed,
                "winner_ids": giveaway.get("winners_claimed", []),  # List of winner IDs
                "unique_entrants": unique_entrants,
                "total_entries": total_entries,
                "bonus_entries": bonus_entries,
                "rerolls": rerolls,
                "cancelled": giveaway.get("cancelled", False),
                "participant_ids": list(entries.keys()) if isinstance(entries, dict) else [str(x) for x in entries],
            }

            # Add to history as dict keyed by giveaway_id (migrate from list if needed)
            async with self.config.guild(guild).giveaway_history() as history:
                # Handle migration from list to dict
                if isinstance(history, list):
                    # Convert list to dict
                    new_history = {}
                    for entry in history:
                        gid = entry.get("giveaway_id", str(len(new_history)))
                        new_history[gid] = entry
                    history.clear()
                    history.update(new_history)

                # Add new entry
                history[giveaway_id] = stats_entry

                # Limit to 100 entries (remove oldest)
                if len(history) > 100:
                    # Sort by ended_at and remove oldest
                    sorted_keys = sorted(
                        history.keys(),
                        key=lambda k: history[k].get("ended_at", 0)
                    )
                    for key in sorted_keys[:-100]:
                        del history[key]

            log.info(f"Recorded stats for giveaway {giveaway_id}")

        except Exception as e:
            log.error(f"Error recording giveaway stats: {e}", exc_info=True)

    async def get_giveaway_stats(self, interaction: discord.Interaction):
        """Show giveaway analytics for the guild."""
        history = await self.config.guild(interaction.guild).giveaway_history()

        if not history:
            await interaction.response.send_message(
                "No giveaway history yet. Stats will be available after giveaways complete.",
                ephemeral=True
            )
            return

        # Convert dict to list if needed
        if isinstance(history, dict):
            history_list = list(history.values())
        else:
            history_list = history

        # Calculate aggregate statistics
        total_giveaways = len(history_list)
        total_entries = sum(h.get("total_entries", 0) for h in history_list)
        total_winners = sum(
            h.get("winners_claimed", 0) if isinstance(h.get("winners_claimed"), int)
            else len(h.get("winners_claimed", []))
            for h in history_list
        )
        total_rerolls = sum(h.get("rerolls", 0) for h in history_list)

        # Unique participants
        all_participants = set()
        participant_entry_counts = {}
        participant_wins = {}

        for h in history_list:
            for pid in h.get("participant_ids", []):
                all_participants.add(pid)
                participant_entry_counts[pid] = participant_entry_counts.get(pid, 0) + 1

            # Track winners
            for wid in h.get("winner_ids", []):
                participant_wins[str(wid)] = participant_wins.get(str(wid), 0) + 1

        unique_participants = len(all_participants)

        # Calculate averages
        avg_participation = total_entries / total_giveaways if total_giveaways > 0 else 0
        total_winners_needed = sum(h.get("winners_count", 1) for h in history_list if not h.get("cancelled"))
        claim_rate = (total_winners / total_winners_needed * 100) if total_winners_needed > 0 else 0
        reroll_rate = (total_rerolls / total_winners_needed * 100) if total_winners_needed > 0 else 0

        # Top participants by entry count
        top_participants = sorted(participant_entry_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        embed = discord.Embed(
            title="📊 Giveaway Statistics",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="📈 Totals",
            value=f"**Giveaways:** {total_giveaways}\n"
                  f"**Total Entries:** {total_entries:,}\n"
                  f"**Total Winners:** {total_winners}\n"
                  f"**Unique Participants:** {unique_participants}",
            inline=True
        )

        embed.add_field(
            name="📊 Averages",
            value=f"**Participation:** {avg_participation:.1f}/giveaway\n"
                  f"**Claim Rate:** {claim_rate:.1f}%\n"
                  f"**Reroll Rate:** {reroll_rate:.1f}%",
            inline=True
        )

        # Top participants
        if top_participants:
            top_text = []
            for i, (pid, count) in enumerate(top_participants, 1):
                try:
                    member = interaction.guild.get_member(int(pid))
                    name = member.display_name if member else f"User {str(pid)[:6]}..."
                except (ValueError, TypeError):
                    name = f"User {str(pid)[:6]}..."
                top_text.append(f"{i}. **{name}** - {count} entries")

            embed.add_field(
                name="🏆 Top Participants",
                value="\n".join(top_text),
                inline=False
            )

        # Recent giveaways - sort by ended_at and get last 5
        sorted_history = sorted(history_list, key=lambda x: x.get("ended_at", 0), reverse=True)
        recent = sorted_history[:5]
        if recent:
            recent_text = []
            for h in recent:
                prize = h.get("prize_name", "Unknown")[:30]
                entries = h.get("unique_entrants", 0)
                winners = h.get("winners_claimed", 0)
                if isinstance(winners, list):
                    winners = len(winners)
                if h.get("cancelled"):
                    recent_text.append(f"• ~~{prize}~~ - Cancelled")
                else:
                    recent_text.append(f"• {prize} - {entries} entries, {winners} winner(s)")

            embed.add_field(
                name="📋 Recent Giveaways",
                value="\n".join(recent_text),
                inline=False
            )

        embed.set_footer(text=f"Based on last {len(history_list)} giveaways")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def list_giveaways(self, interaction: discord.Interaction):
        """List all active and scheduled giveaways in the guild."""
        giveaways = await self.config.guild(interaction.guild).giveaways()

        active = [(gid, g) for gid, g in giveaways.items() if not g["ended"]]

        if not active:
            await interaction.response.send_message(
                "No active or scheduled giveaways.\n\nUse `/giveaway create` to create one!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎉 Active & Scheduled Giveaways",
            description="Use `/giveawaymanage` to end, cancel, or view details.",
            color=discord.Color.gold()
        )

        for giveaway_id, giveaway in active[:10]:
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            channel_mention = channel.mention if channel else "Unknown Channel"

            prize_name = giveaway.get("prize_name") or giveaway.get("description", "Unknown")

            entries = giveaway.get("entries", {})
            if isinstance(entries, dict):
                entrants_count = len(entries)
                total_entries = sum(entries.values())
                entries_text = f"Entrants: {entrants_count} ({total_entries} entries)"
            else:
                entries_text = f"Entries: {len(entries)}"

            # Determine status
            if giveaway.get("status") == "scheduled":
                status = "⏰ Scheduled"
                scheduled_start = giveaway.get("scheduled_start")
                time_text = f"Starts: <t:{scheduled_start}:R>" if scheduled_start else "Scheduled"
            elif giveaway.get("picking_winners", False):
                status = "🎲 Picking Winners"
                time_text = f"Ends: <t:{giveaway['end_timestamp']}:R>"
            else:
                status = "🟢 Active"
                time_text = f"Ends: <t:{giveaway['end_timestamp']}:R>"

            embed.add_field(
                name=f"{prize_name}",
                value=f"Status: {status}\n"
                      f"Channel: {channel_mention}\n"
                      f"{entries_text}\n"
                      f"Claimed: {len(giveaway.get('winners_claimed', []))}/{giveaway['winners_count']}\n"
                      f"{time_text}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Red):
    # DM bot owner if cryptography is missing
    if not CRYPTO_AVAILABLE:
        error_msg = (
            f"**ShadyGiveaway failed to load!**\n\n"
            f"Missing dependency: `cryptography`\n"
            f"Error: `{CRYPTO_ERROR}`\n\n"
            f"**Fix:** Run this command:\n"
            f"```\n[p]pipinstall cryptography\n```\n"
            f"Then restart the bot or reload the cog."
        )
        try:
            owner = await bot.get_or_fetch_user(bot.owner_id)
            if owner:
                await owner.send(error_msg)
        except Exception:
            pass
        raise ImportError(f"cryptography package not installed: {CRYPTO_ERROR}")

    try:
        cog = ShadyGiveaway(bot)
        await bot.add_cog(cog)
    except Exception as e:
        # DM full traceback to bot owner
        tb = traceback.format_exc()
        error_msg = f"**ShadyGiveaway failed to load!**\n\n```py\n{tb[-1900:]}\n```"
        try:
            owner = await bot.get_or_fetch_user(bot.owner_id)
            if owner:
                await owner.send(error_msg)
        except Exception:
            pass
        raise
