"""
ShadyTourneys - Tournament and bracket management system.

Features:
- Solo and team tournament modes
- Multiple formats (single/double elimination, round robin)
- Challonge API integration for bracket hosting and visualization
- Match reporting via Discord
- Prize pool tracking
- Configurable moderator roles

Challonge Setup (Bot Owner):
1. Get API key from https://challonge.com/settings/developer
2. Run: [p]challongeset credentials <username> <api_key>
"""

import asyncio
import discord
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadytourneys")

# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 260288776360820739

# Try to import challonge library
try:
    import challonge
    CHALLONGE_AVAILABLE = True
except ImportError:
    challonge = None
    CHALLONGE_AVAILABLE = False
    log.info("pychallonge not installed. Install with: pip install pychallonge")


class TournamentFormat(Enum):
    """Tournament format types."""
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"
    ROUND_ROBIN = "round_robin"


class TournamentStatus(Enum):
    """Tournament status states."""
    SIGNUP = "signup"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ShadyTourneys(commands.Cog):
    """Tournament and bracket management system."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        # Global config (bot owner only) for Challonge API
        default_global = {
            "challonge_username": None,
            "challonge_api_key": None,
            "challonge_subdomain": None,  # Optional subdomain for organization
        }

        default_guild = {
            "tournaments": {},
            "mod_roles": [],  # Roles that can manage tournaments
            "default_format": "single_elimination",
            "player_stats": {},  # Per-game stats: {game: {user_id_str: stats}}
            "supported_games": ["rivals"],  # Games that appear in autocomplete
            "seed_lists": {
                "rivals": {
                    "One Above All": 1500,
                    "Eternity": 1400,
                    "Celestial": 1300,
                    "Grandmaster": 1200,
                    "Diamond": 1100,
                    "Platinum": 1050,
                    "Gold": 1000,
                    "Silver": 950,
                    "Bronze": 900,
                }
            },
        }

        # Default generic seed list (used when game has no custom list)
        self.default_seed_list = {
            "S Tier": 1400,
            "A Tier": 1250,
            "B Tier": 1100,
            "C Tier": 1000,
            "D Tier": 900,
            "Unranked": 850,
        }

        # Player stats structure:
        # player_stats[game_lower][str(user_id)] = {
        #     "elo": 1000,
        #     "wins": 0,
        #     "losses": 0,
        #     "matches_played": 0,
        #     "tournaments_won": 0,
        #     "last_played": ISO timestamp,
        # }
        #
        # Team seeding = average ELO of team members (no separate team tracking)
        # ELO visibility: Admin only

        # Tournament structure:
        # {
        #     "message_id": int,
        #     "channel_id": int,
        #     "name": str,
        #     "game": str,
        #     "host_id": int,
        #     "type": "solo" | "team",
        #     "format": "single_elimination" | "double_elimination" | "round_robin",
        #     "team_size": int | None,
        #     "participants": [user_ids],
        #     "seeds": {str(user_id): seed_number},  # Manual seeds (optional)
        #     "teams": {team_name: {"captain": id, "players": [ids], "seed": int|None}},
        #     "pickup_players": [user_ids],
        #     "started": bool,
        #     "cancelled": bool,
        #     "bracket": [match_dicts],
        #     "prize_pool": str | None,
        #     "round_deadline_hours": int,  # Hours per round (default 48)
        #     "scheduling_channel": int | None,  # Channel for match threads
        #     "challonge_id": int | None,
        #     "challonge_url": str | None,
        #     "challonge_image": str | None,
        #     "created_at": ISO timestamp,
        # }

        # Match structure (in bracket):
        # {
        #     "id": int,
        #     "round": int,
        #     "participant1": user_id | team_name,
        #     "participant2": user_id | team_name,
        #     "seed1": int | None,
        #     "seed2": int | None,
        #     "completed": bool,
        #     "winner": user_id | team_name | None,
        #     "score": "2-1" | None,
        #     "bracket_type": "winners" | "losers" | "round_robin",
        #     # Scheduling fields:
        #     "deadline": ISO timestamp,  # Must be played by
        #     "proposed_time": ISO timestamp | None,
        #     "proposed_by": user_id | None,
        #     "scheduled_time": ISO timestamp | None,  # Agreed time
        #     "scheduling_thread": thread_id | None,
        #     "reminder_sent": bool,
        #     "checked_in": [user_ids],  # Players who checked in for match
        #     "forfeit": bool,
        #     "forfeit_reason": str | None,  # "no_show", "deadline", "manual"
        #     "history": [  # Lightweight audit log for disputes
        #         {"event": "proposed", "by": user_id, "at": ISO, "time": proposed_ISO},
        #         {"event": "accepted", "by": user_id, "at": ISO},
        #         {"event": "checkin", "by": user_id, "at": ISO},
        #         {"event": "reported", "by": user_id, "at": ISO, "score": "2-1", "winner": ...},
        #         {"event": "forfeit", "by": user_id, "at": ISO},
        #     ],
        # }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.active_views: Dict[str, discord.ui.View] = {}
        self._challonge_ready = False
        self._scheduler_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Re-register persistent views on cog load."""
        await self.bot.wait_until_ready()
        await self._init_challonge()
        await self.restore_views()
        # Start background scheduler task
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def cog_unload(self) -> None:
        """Cleanup on cog unload."""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

    async def _scheduler_loop(self) -> None:
        """Background task for match reminders and deadline enforcement."""
        await self.bot.wait_until_ready()

        while True:
            try:
                await self._check_match_schedules()
            except Exception as e:
                log.error(f"Error in scheduler loop: {e}")

            # Check every 5 minutes
            await asyncio.sleep(300)

    async def _check_match_schedules(self) -> None:
        """Check all active tournaments for scheduled matches and deadlines."""
        now = datetime.now(timezone.utc)

        for guild in self.bot.guilds:
            tournaments = await self.config.guild(guild).tournaments()

            for tournament_id, tournament in tournaments.items():
                if not tournament.get("started") or tournament.get("completed"):
                    continue

                bracket = tournament.get("bracket", [])
                updated = False

                for match in bracket:
                    if match.get("completed"):
                        continue

                    # Check for upcoming scheduled matches (30 min reminder)
                    scheduled_time = match.get("scheduled_time")
                    if scheduled_time and not match.get("reminder_sent"):
                        scheduled = datetime.fromisoformat(scheduled_time)
                        time_until = scheduled - now

                        if timedelta(minutes=25) <= time_until <= timedelta(minutes=35):
                            await self._send_match_reminder(guild, match, tournament, 30)
                            match["reminder_sent"] = True
                            updated = True

                    # Check for deadline enforcement (auto-forfeit if past deadline and no activity)
                    deadline_str = match.get("deadline")
                    if deadline_str:
                        deadline = datetime.fromisoformat(deadline_str)
                        if now > deadline:
                            # Past deadline - check if there's been any scheduling activity
                            if not match.get("scheduled_time") and not match.get("proposed_time"):
                                # No activity - mark as double forfeit (both lose)
                                match["completed"] = True
                                match["forfeit"] = True
                                match["forfeit_reason"] = "deadline"
                                match["winner"] = None
                                match["score"] = "DQ"
                                updated = True

                                # Notify in thread if exists
                                thread_id = match.get("scheduling_thread")
                                if thread_id:
                                    thread = guild.get_thread(thread_id)
                                    if thread:
                                        try:
                                            await thread.send(
                                                "⚠️ **Match deadline passed!**\n"
                                                "Neither participant scheduled or played the match.\n"
                                                "Both participants have been disqualified from this match."
                                            )
                                        except Exception:
                                            pass

                if updated:
                    async with self.config.guild(guild).tournaments() as all_tournaments:
                        all_tournaments[tournament_id] = tournament

    async def _init_challonge(self) -> bool:
        """Initialize Challonge API credentials."""
        if not CHALLONGE_AVAILABLE:
            return False

        username = await self.config.challonge_username()
        api_key = await self.config.challonge_api_key()

        if username and api_key:
            try:
                challonge.set_credentials(username, api_key)
                self._challonge_ready = True
                log.info("Challonge API initialized successfully")
                return True
            except Exception as e:
                log.error(f"Failed to initialize Challonge: {e}")
                self._challonge_ready = False
                return False

        return False

    def _sanitize_url(self, name: str) -> str:
        """Convert tournament name to valid Challonge URL slug."""
        # Remove special characters, replace spaces with underscores
        slug = re.sub(r'[^a-zA-Z0-9\s_-]', '', name)
        slug = re.sub(r'\s+', '_', slug).lower()
        # Add timestamp for uniqueness
        slug = f"{slug}_{int(datetime.now(timezone.utc).timestamp())}"
        return slug[:60]  # Challonge URL limit

    async def _challonge_create_tournament(
        self,
        name: str,
        game: str,
        tournament_format: str,
        description: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Create a tournament on Challonge."""
        if not self._challonge_ready:
            return None

        # Map our format names to Challonge's
        format_map = {
            "single_elimination": "single elimination",
            "double_elimination": "double elimination",
            "round_robin": "round robin",
        }

        try:
            subdomain = await self.config.challonge_subdomain()
            url_slug = self._sanitize_url(name)

            # Run in executor since pychallonge is synchronous
            tournament = await asyncio.to_thread(
                challonge.tournaments.create,
                name=name,
                url=url_slug,
                tournament_type=format_map.get(tournament_format, "single elimination"),
                game_name=game,
                description=description,
                subdomain=subdomain if subdomain else None,
                open_signup=False,
                private=False,
            )

            # Build the public URL
            if subdomain:
                public_url = f"https://{subdomain}.challonge.com/{url_slug}"
                image_url = f"https://{subdomain}.challonge.com/{url_slug}.png"
            else:
                public_url = f"https://challonge.com/{url_slug}"
                image_url = f"https://challonge.com/{url_slug}.png"

            return {
                "id": tournament["id"],
                "url": public_url,
                "image_url": image_url,
                "slug": url_slug,
            }
        except Exception as e:
            log.error(f"Failed to create Challonge tournament: {e}")
            return None

    async def _challonge_add_participant(
        self,
        tournament_id: int,
        name: str,
        misc: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Add a participant to a Challonge tournament."""
        if not self._challonge_ready:
            return None

        try:
            participant = await asyncio.to_thread(
                challonge.participants.create,
                tournament_id,
                name=name,
                misc=misc,
            )
            return participant
        except Exception as e:
            log.error(f"Failed to add Challonge participant: {e}")
            return None

    async def _challonge_add_participants_bulk(
        self,
        tournament_id: int,
        names: List[str]
    ) -> bool:
        """Add multiple participants to a Challonge tournament."""
        if not self._challonge_ready:
            return False

        try:
            await asyncio.to_thread(
                challonge.participants.bulk_add,
                tournament_id,
                [{"name": name} for name in names],
            )
            return True
        except Exception as e:
            log.error(f"Failed to bulk add Challonge participants: {e}")
            return False

    async def _challonge_start_tournament(self, tournament_id: int) -> bool:
        """Start a Challonge tournament."""
        if not self._challonge_ready:
            return False

        try:
            await asyncio.to_thread(
                challonge.tournaments.start,
                tournament_id,
            )
            return True
        except Exception as e:
            log.error(f"Failed to start Challonge tournament: {e}")
            return False

    async def _challonge_delete_tournament(self, tournament_id: int) -> bool:
        """Delete a Challonge tournament."""
        if not self._challonge_ready:
            return False

        try:
            await asyncio.to_thread(
                challonge.tournaments.destroy,
                tournament_id,
            )
            return True
        except Exception as e:
            log.error(f"Failed to delete Challonge tournament: {e}")
            return False

    async def _challonge_report_match(
        self,
        tournament_id: int,
        match_id: int,
        winner_id: int,
        scores: str = "1-0"
    ) -> bool:
        """Report a match result on Challonge."""
        if not self._challonge_ready:
            return False

        try:
            await asyncio.to_thread(
                challonge.matches.update,
                tournament_id,
                match_id,
                winner_id=winner_id,
                scores_csv=scores,
            )
            return True
        except Exception as e:
            log.error(f"Failed to report Challonge match: {e}")
            return False

    # ==================== ELO & STATS HELPERS ====================

    DEFAULT_ELO = 1000
    ELO_K_FACTOR = 32  # How much ELO changes per match

    def _calculate_elo_change(
        self, winner_elo: int, loser_elo: int, score_margin: float = 1.0
    ) -> tuple[int, int]:
        """Calculate ELO changes after a match.

        Args:
            winner_elo: Winner's current ELO
            loser_elo: Loser's current ELO
            score_margin: Multiplier based on score (1.0 = normal, higher = bigger win)

        Returns:
            (winner_gain, loser_loss) - both positive numbers
        """
        # Expected score formula
        expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
        expected_loser = 1 - expected_winner

        # Base ELO change
        winner_gain = int(self.ELO_K_FACTOR * (1 - expected_winner) * score_margin)
        loser_loss = int(self.ELO_K_FACTOR * (0 - expected_loser) * score_margin)

        # Minimum change of 1
        winner_gain = max(1, winner_gain)
        loser_loss = abs(min(-1, loser_loss))

        return winner_gain, loser_loss

    def _parse_score(self, score_str: str) -> tuple[int, int, float]:
        """Parse score string like '2-1' into (winner_score, loser_score, margin).

        Returns margin multiplier: 1.0 for close, up to 1.5 for sweeps.
        """
        try:
            parts = score_str.replace(" ", "").split("-")
            if len(parts) == 2:
                s1, s2 = int(parts[0]), int(parts[1])
                winner_score = max(s1, s2)
                loser_score = min(s1, s2)

                # Calculate margin multiplier (1.0 to 1.5)
                if winner_score > 0:
                    margin = 1.0 + (0.5 * (1 - loser_score / winner_score))
                else:
                    margin = 1.0

                return winner_score, loser_score, margin
        except (ValueError, ZeroDivisionError):
            pass

        return 1, 0, 1.0  # Default: 1-0, normal margin

    async def _get_player_stats(
        self, guild_id: int, game: str, user_id: int
    ) -> dict:
        """Get player stats for a specific game, creating if needed."""
        game_key = game.lower().strip()
        user_key = str(user_id)

        async with self.config.guild_from_id(guild_id).player_stats() as stats:
            if game_key not in stats:
                stats[game_key] = {}

            if user_key not in stats[game_key]:
                stats[game_key][user_key] = {
                    "elo": self.DEFAULT_ELO,
                    "wins": 0,
                    "losses": 0,
                    "matches_played": 0,
                    "tournaments_won": 0,
                    "last_played": None,
                }

            return stats[game_key][user_key].copy()

    async def _update_player_stats(
        self,
        guild_id: int,
        game: str,
        winner_id: int,
        loser_id: int,
        score_str: str = "1-0"
    ) -> tuple[int, int]:
        """Update stats after a match. Returns (winner_elo_change, loser_elo_change)."""
        game_key = game.lower().strip()
        winner_key = str(winner_id)
        loser_key = str(loser_id)

        # Parse score for margin
        _, _, margin = self._parse_score(score_str)

        async with self.config.guild_from_id(guild_id).player_stats() as stats:
            if game_key not in stats:
                stats[game_key] = {}

            # Ensure both players exist
            for user_key in [winner_key, loser_key]:
                if user_key not in stats[game_key]:
                    stats[game_key][user_key] = {
                        "elo": self.DEFAULT_ELO,
                        "wins": 0,
                        "losses": 0,
                        "matches_played": 0,
                        "tournaments_won": 0,
                        "last_played": None,
                    }

            winner_stats = stats[game_key][winner_key]
            loser_stats = stats[game_key][loser_key]

            # Calculate ELO change
            winner_gain, loser_loss = self._calculate_elo_change(
                winner_stats["elo"], loser_stats["elo"], margin
            )

            # Update winner
            winner_stats["elo"] += winner_gain
            winner_stats["wins"] += 1
            winner_stats["matches_played"] += 1
            winner_stats["last_played"] = datetime.now(timezone.utc).isoformat()

            # Update loser
            loser_stats["elo"] -= loser_loss
            loser_stats["losses"] += 1
            loser_stats["matches_played"] += 1
            loser_stats["last_played"] = datetime.now(timezone.utc).isoformat()

            return winner_gain, loser_loss

    async def _get_seeded_entities(
        self,
        guild_id: int,
        game: str,
        entities: List,
        manual_seeds: Optional[Dict] = None,
        is_team: bool = False,
        teams_data: Optional[Dict] = None
    ) -> List:
        """Sort entities by seed (manual > ELO > random).

        Args:
            guild_id: Guild ID
            game: Game name for ELO lookup
            entities: List of user_ids (solo) or team_names (team)
            manual_seeds: {entity: seed_number} for manual overrides
            is_team: Whether this is a team tournament
            teams_data: Team data dict for looking up member ELOs

        Returns:
            Sorted list of entities (seed 1 first)
        """
        manual_seeds = manual_seeds or {}

        # Build (entity, seed_priority, elo) tuples
        seeding_data = []

        for entity in entities:
            # Check for manual seed
            entity_key = str(entity)
            if entity_key in manual_seeds:
                # Manual seed: priority 0 (highest), use manual seed as ELO placeholder
                seeding_data.append((entity, 0, manual_seeds[entity_key]))
                continue

            # Calculate ELO
            if is_team and teams_data:
                # Team: average ELO of members
                team_data = teams_data.get(entity, {})
                member_ids = team_data.get("players", [])
                if member_ids:
                    elos = []
                    for member_id in member_ids:
                        stats = await self._get_player_stats(guild_id, game, member_id)
                        elos.append(stats["elo"])
                    avg_elo = sum(elos) / len(elos)
                else:
                    avg_elo = self.DEFAULT_ELO
                seeding_data.append((entity, 1, avg_elo))
            else:
                # Solo: player's ELO
                stats = await self._get_player_stats(guild_id, game, entity)
                seeding_data.append((entity, 1, stats["elo"]))

        # Sort: manual seeds first (priority 0), then by ELO descending
        # For manual seeds, lower seed number = better = should be first
        seeding_data.sort(key=lambda x: (x[1], -x[2] if x[1] == 1 else x[2]))

        return [entity for entity, _, _ in seeding_data]

    # ==================== MATCH SCHEDULING HELPERS ====================

    async def _create_match_thread(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        match: Dict[str, Any],
        tournament: Dict[str, Any]
    ) -> Optional[discord.Thread]:
        """Create a thread for match scheduling."""
        try:
            p1 = match["participant1"]
            p2 = match["participant2"]

            if tournament["type"] == "team":
                thread_name = f"Match: {p1} vs {p2}"
            else:
                member1 = guild.get_member(p1)
                member2 = guild.get_member(p2)
                name1 = member1.display_name if member1 else f"User{p1}"
                name2 = member2.display_name if member2 else f"User{p2}"
                thread_name = f"Match: {name1} vs {name2}"

            thread = await channel.create_thread(
                name=thread_name[:100],
                type=discord.ChannelType.public_thread,
                reason=f"Match scheduling for {tournament['name']}"
            )

            # Post initial message with scheduling buttons
            if tournament["type"] == "team":
                schedule_instructions = (
                    "**How to schedule (Team Match):**\n"
                    "1️⃣ Anyone can **Propose Time** to suggest when to play\n"
                    "2️⃣ **ALL players** from both teams must **Accept**\n"
                    "3️⃣ Or anyone can **Counter-Propose** a different time\n"
                    "4️⃣ Once scheduled, players **Check In** within ±15 min\n"
                    "5️⃣ Play and **Report Score** when done\n\n"
                    "⚠️ If opponents don't check in, you can **Claim Forfeit** after 15 min"
                )
            else:
                schedule_instructions = (
                    "**How to schedule:**\n"
                    "1️⃣ Use **Propose Time** to suggest when to play\n"
                    "2️⃣ Opponent uses **Accept** or **Counter-Propose**\n"
                    "3️⃣ Both players **Check In** within ±15 min of match time\n"
                    "4️⃣ Play and **Report Score** when done\n\n"
                    "⚠️ If opponent doesn't check in, you can **Claim Forfeit** after 15 min"
                )

            embed = discord.Embed(
                title=f"⚔️ {thread_name}",
                description=(
                    f"**Tournament:** {tournament['name']}\n"
                    f"**Round:** {match.get('round', 1)}\n"
                    f"**Deadline:** <t:{int(datetime.fromisoformat(match['deadline']).timestamp())}:F>\n\n"
                    f"{schedule_instructions}"
                ),
                color=discord.Color.blue()
            )

            view = MatchSchedulingView(self, match["id"], tournament)
            await thread.send(embed=embed, view=view)

            # Mention participants
            if tournament["type"] == "team":
                teams_data = tournament.get("teams", {})
                mentions = []
                for team_name in [p1, p2]:
                    team = teams_data.get(team_name, {})
                    for player_id in team.get("players", []):
                        mentions.append(f"<@{player_id}>")
                await thread.send(f"Participants: {' '.join(mentions)}")
            else:
                await thread.send(f"Participants: <@{p1}> <@{p2}>")

            return thread
        except Exception as e:
            log.error(f"Failed to create match thread: {e}")
            return None

    async def _send_match_reminder(
        self,
        guild: discord.Guild,
        match: Dict[str, Any],
        tournament: Dict[str, Any],
        minutes_until: int
    ) -> None:
        """Send reminder for upcoming scheduled match."""
        thread_id = match.get("scheduling_thread")
        if not thread_id:
            return

        try:
            thread = guild.get_thread(thread_id)
            if thread:
                scheduled = datetime.fromisoformat(match["scheduled_time"])
                await thread.send(
                    f"⏰ **Reminder:** Your match is scheduled for <t:{int(scheduled.timestamp())}:R>!\n"
                    f"Please be ready to play."
                )
        except Exception as e:
            log.error(f"Failed to send match reminder: {e}")

    async def restore_views(self) -> None:
        """Restore views for active tournaments after bot restart."""
        for guild in self.bot.guilds:
            tournaments = await self.config.guild(guild).tournaments()
            for tournament_id, tournament in tournaments.items():
                if not tournament.get("started") and not tournament.get("cancelled"):
                    try:
                        if tournament["type"] == "solo":
                            view = SoloSignupView(self, tournament_id)
                        else:
                            view = TeamSignupView(self, tournament_id, tournament["team_size"])

                        self.active_views[tournament_id] = view
                        self.bot.add_view(view, message_id=tournament["message_id"])
                    except Exception as e:
                        log.error(f"Error restoring view for tournament {tournament_id}: {e}")

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage tournaments."""
        # Bot owner always authorized
        if await self.bot.is_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/guild owner always authorized
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True

        # Check for manage_events permission
        if interaction.user.guild_permissions.manage_events:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(interaction.guild).mod_roles()
        return any(role.id in mod_roles for role in interaction.user.roles)

    def generate_bracket(
        self,
        entities: List,
        format: str = "single_elimination",
        seeded: bool = False,
        round_deadline_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Generate tournament bracket based on format.

        Args:
            entities: List of participants (already sorted by seed if seeded=True)
            format: Bracket format
            seeded: If True, entities are already sorted by seed (don't shuffle)
            round_deadline_hours: Hours until each round's deadline
        """
        entities = list(entities)

        if not seeded:
            random.shuffle(entities)

        if format == "single_elimination":
            return self._generate_single_elimination(entities, round_deadline_hours)
        elif format == "double_elimination":
            return self._generate_double_elimination(entities, round_deadline_hours)
        elif format == "round_robin":
            return self._generate_round_robin(entities, round_deadline_hours)
        else:
            return self._generate_single_elimination(entities, round_deadline_hours)

    def _generate_single_elimination(
        self, entities: List, round_deadline_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Generate single elimination bracket with all rounds.

        Creates the full bracket structure upfront with TBD placeholders
        for future rounds. This allows for proper bracket advancement.
        """
        import math

        matches = []
        match_id = 1
        n = len(entities)

        if n < 2:
            return matches

        # Calculate number of rounds needed
        num_rounds = math.ceil(math.log2(n))

        # Pad to power of 2 for cleaner bracket (with BYEs)
        bracket_size = 2 ** num_rounds

        round_deadline = datetime.now(timezone.utc) + timedelta(hours=round_deadline_hours)

        # First round - pair up entities with proper seeding
        r1_matches = []
        for i in range(bracket_size // 2):
            p1 = entities[i] if i < len(entities) else "BYE"
            p2_idx = bracket_size - 1 - i
            p2 = entities[p2_idx] if p2_idx < len(entities) else "BYE"

            # Handle BYE matches
            is_bye = p1 == "BYE" or p2 == "BYE"
            winner = p1 if p2 == "BYE" else (p2 if p1 == "BYE" else None)

            match = {
                "id": match_id,
                "round": 1,
                "bracket_type": "winners",
                "participant1": p1,
                "participant2": p2,
                "seed1": i + 1 if p1 != "BYE" else None,
                "seed2": p2_idx + 1 if p2 != "BYE" else None,
                "winner": winner,
                "loser": None,
                "score": "BYE" if is_bye else None,
                "completed": is_bye,
                "deadline": round_deadline.isoformat(),
                "proposed_time": None,
                "proposed_by": None,
                "scheduled_time": None,
                "scheduling_thread": None,
                "reminder_sent": False,
                "checked_in": [],
                "forfeit": False,
                "forfeit_reason": None,
                "next_winner_match": None,  # Will be set below
            }
            matches.append(match)
            r1_matches.append(match_id)
            match_id += 1

        # Create remaining rounds with TBD participants
        matches_by_round = {1: r1_matches}
        current_round_matches = r1_matches

        for round_num in range(2, num_rounds + 1):
            round_deadline = datetime.now(timezone.utc) + timedelta(
                hours=round_deadline_hours * round_num
            )
            next_round_matches = []

            for i in range(0, len(current_round_matches), 2):
                source = []
                if i < len(current_round_matches):
                    source.append(current_round_matches[i])
                if i + 1 < len(current_round_matches):
                    source.append(current_round_matches[i + 1])

                match = {
                    "id": match_id,
                    "round": round_num,
                    "bracket_type": "winners",
                    "participant1": "TBD",
                    "participant2": "TBD",
                    "seed1": None,
                    "seed2": None,
                    "winner": None,
                    "loser": None,
                    "score": None,
                    "completed": False,
                    "deadline": round_deadline.isoformat(),
                    "proposed_time": None,
                    "proposed_by": None,
                    "scheduled_time": None,
                    "scheduling_thread": None,
                    "reminder_sent": False,
                    "checked_in": [],
                    "forfeit": False,
                    "forfeit_reason": None,
                    "next_winner_match": None,
                    "source_matches": source,
                }
                matches.append(match)
                next_round_matches.append(match_id)

                # Link previous round matches to this one
                for prev_match_id in source:
                    for m in matches:
                        if m["id"] == prev_match_id:
                            m["next_winner_match"] = match_id
                            break

                match_id += 1

            matches_by_round[round_num] = next_round_matches
            current_round_matches = next_round_matches

        return matches

    def _generate_double_elimination(
        self, entities: List, round_deadline_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Generate double elimination bracket (winners + losers bracket).

        Double elimination structure:
        - Winners bracket: Standard single elimination
        - Losers bracket: Losers drop down, play until one remains
        - Grand finals: Winners bracket champ vs Losers bracket champ
        - Bracket reset: If losers champ wins grand finals, play one more match

        We generate the full structure upfront with TBD placeholders.
        """
        matches = []
        match_id = 1
        n = len(entities)

        if n < 2:
            return matches

        # Calculate number of rounds needed
        import math
        winners_rounds = math.ceil(math.log2(n))

        # Pad to power of 2 for cleaner bracket (with BYEs)
        bracket_size = 2 ** winners_rounds

        # ==================== WINNERS BRACKET ====================
        round_deadline = datetime.now(timezone.utc) + timedelta(hours=round_deadline_hours)

        # First round - pair up entities
        winners_r1_matches = []
        for i in range(bracket_size // 2):
            p1 = entities[i] if i < len(entities) else "BYE"
            p2_idx = bracket_size - 1 - i
            p2 = entities[p2_idx] if p2_idx < len(entities) else "BYE"

            # Handle BYE matches
            is_bye = p1 == "BYE" or p2 == "BYE"
            winner = p1 if p2 == "BYE" else (p2 if p1 == "BYE" else None)

            match = {
                "id": match_id,
                "round": 1,
                "bracket_type": "winners",
                "winners_round": 1,
                "participant1": p1,
                "participant2": p2,
                "seed1": i + 1 if p1 != "BYE" else None,
                "seed2": p2_idx + 1 if p2 != "BYE" else None,
                "winner": winner,
                "loser": None,
                "score": "BYE" if is_bye else None,
                "completed": is_bye,
                "deadline": round_deadline.isoformat(),
                "proposed_time": None,
                "proposed_by": None,
                "scheduled_time": None,
                "scheduling_thread": None,
                "reminder_sent": False,
                "checked_in": [],
                "forfeit": False,
                "forfeit_reason": None,
                "next_winner_match": None,  # Will be set after all matches created
                "next_loser_match": None,   # For double elim - where loser goes
            }
            matches.append(match)
            winners_r1_matches.append(match_id)
            match_id += 1

        # Create remaining winners bracket rounds (with TBD participants)
        winners_matches_by_round = {1: winners_r1_matches}
        current_round_matches = winners_r1_matches

        for round_num in range(2, winners_rounds + 1):
            round_deadline = datetime.now(timezone.utc) + timedelta(
                hours=round_deadline_hours * round_num
            )
            next_round_matches = []

            for i in range(0, len(current_round_matches), 2):
                match = {
                    "id": match_id,
                    "round": round_num,
                    "bracket_type": "winners",
                    "winners_round": round_num,
                    "participant1": "TBD",
                    "participant2": "TBD",
                    "seed1": None,
                    "seed2": None,
                    "winner": None,
                    "loser": None,
                    "score": None,
                    "completed": False,
                    "deadline": round_deadline.isoformat(),
                    "proposed_time": None,
                    "proposed_by": None,
                    "scheduled_time": None,
                    "scheduling_thread": None,
                    "reminder_sent": False,
                    "checked_in": [],
                    "forfeit": False,
                    "forfeit_reason": None,
                    "next_winner_match": None,
                    "next_loser_match": None,
                    "source_matches": [current_round_matches[i], current_round_matches[i + 1]] if i + 1 < len(current_round_matches) else [current_round_matches[i]],
                }
                matches.append(match)
                next_round_matches.append(match_id)

                # Link previous round matches to this one
                for prev_match_id in match.get("source_matches", []):
                    for m in matches:
                        if m["id"] == prev_match_id:
                            m["next_winner_match"] = match_id
                            break

                match_id += 1

            winners_matches_by_round[round_num] = next_round_matches
            current_round_matches = next_round_matches

        # ==================== LOSERS BRACKET ====================
        # Losers bracket has (2 * winners_rounds - 2) rounds
        # - Odd rounds: losers from winners bracket drop in
        # - Even rounds: losers bracket survivors play each other

        losers_rounds = (winners_rounds - 1) * 2 if winners_rounds > 1 else 0
        losers_matches_by_round = {}

        if losers_rounds > 0:
            # Track which winners round feeds into which losers round
            # Winners R1 losers -> Losers R1
            # Winners R2 losers -> Losers R2 (play vs Losers R1 winners)
            # etc.

            prev_losers_winners = []  # Winners from previous losers round

            for losers_round in range(1, losers_rounds + 1):
                round_deadline = datetime.now(timezone.utc) + timedelta(
                    hours=round_deadline_hours * (winners_rounds + losers_round)
                )
                round_matches = []

                # Determine how many matches in this losers round
                # This depends on the bracket structure
                corresponding_winners_round = (losers_round + 1) // 2

                if losers_round == 1:
                    # First losers round: losers from Winners R1 play each other
                    num_matches = len(winners_matches_by_round.get(1, [])) // 2
                    for i in range(num_matches):
                        match = {
                            "id": match_id,
                            "round": losers_round,
                            "bracket_type": "losers",
                            "losers_round": losers_round,
                            "participant1": "TBD",  # Loser from WR1 match
                            "participant2": "TBD",  # Loser from WR1 match
                            "seed1": None,
                            "seed2": None,
                            "winner": None,
                            "loser": None,
                            "score": None,
                            "completed": False,
                            "deadline": round_deadline.isoformat(),
                            "proposed_time": None,
                            "proposed_by": None,
                            "scheduled_time": None,
                            "scheduling_thread": None,
                            "reminder_sent": False,
                            "checked_in": [],
                            "forfeit": False,
                            "forfeit_reason": None,
                            "next_winner_match": None,
                            "next_loser_match": None,
                            "source_losers_from": [],  # Will be filled with winners bracket match IDs
                        }

                        # Link to winners bracket losers
                        wr1_matches = winners_matches_by_round.get(1, [])
                        if i * 2 < len(wr1_matches):
                            match["source_losers_from"].append(wr1_matches[i * 2])
                            for m in matches:
                                if m["id"] == wr1_matches[i * 2]:
                                    m["next_loser_match"] = match_id
                                    break
                        if i * 2 + 1 < len(wr1_matches):
                            match["source_losers_from"].append(wr1_matches[i * 2 + 1])
                            for m in matches:
                                if m["id"] == wr1_matches[i * 2 + 1]:
                                    m["next_loser_match"] = match_id
                                    break

                        matches.append(match)
                        round_matches.append(match_id)
                        match_id += 1

                elif losers_round % 2 == 0:
                    # Even losers rounds: previous losers round winners vs each other
                    prev_round_matches = losers_matches_by_round.get(losers_round - 1, [])
                    num_matches = len(prev_round_matches) // 2

                    for i in range(max(1, num_matches)):
                        match = {
                            "id": match_id,
                            "round": losers_round,
                            "bracket_type": "losers",
                            "losers_round": losers_round,
                            "participant1": "TBD",
                            "participant2": "TBD",
                            "seed1": None,
                            "seed2": None,
                            "winner": None,
                            "loser": None,
                            "score": None,
                            "completed": False,
                            "deadline": round_deadline.isoformat(),
                            "proposed_time": None,
                            "proposed_by": None,
                            "scheduled_time": None,
                            "scheduling_thread": None,
                            "reminder_sent": False,
                            "checked_in": [],
                            "forfeit": False,
                            "forfeit_reason": None,
                            "next_winner_match": None,
                            "next_loser_match": None,
                            "source_matches": [],
                        }

                        # Link from previous losers round
                        if i * 2 < len(prev_round_matches):
                            match["source_matches"].append(prev_round_matches[i * 2])
                            for m in matches:
                                if m["id"] == prev_round_matches[i * 2]:
                                    m["next_winner_match"] = match_id
                                    break
                        if i * 2 + 1 < len(prev_round_matches):
                            match["source_matches"].append(prev_round_matches[i * 2 + 1])
                            for m in matches:
                                if m["id"] == prev_round_matches[i * 2 + 1]:
                                    m["next_winner_match"] = match_id
                                    break

                        matches.append(match)
                        round_matches.append(match_id)
                        match_id += 1

                else:
                    # Odd losers rounds (except R1): winners bracket losers drop in
                    # Play against previous losers round winners
                    prev_round_matches = losers_matches_by_round.get(losers_round - 1, [])
                    winners_round_for_drops = (losers_round + 1) // 2 + 1
                    dropping_matches = winners_matches_by_round.get(winners_round_for_drops, [])

                    num_matches = max(len(prev_round_matches), len(dropping_matches) // 2)

                    for i in range(max(1, num_matches)):
                        match = {
                            "id": match_id,
                            "round": losers_round,
                            "bracket_type": "losers",
                            "losers_round": losers_round,
                            "participant1": "TBD",  # Previous losers winner
                            "participant2": "TBD",  # Dropping from winners
                            "seed1": None,
                            "seed2": None,
                            "winner": None,
                            "loser": None,
                            "score": None,
                            "completed": False,
                            "deadline": round_deadline.isoformat(),
                            "proposed_time": None,
                            "proposed_by": None,
                            "scheduled_time": None,
                            "scheduling_thread": None,
                            "reminder_sent": False,
                            "checked_in": [],
                            "forfeit": False,
                            "forfeit_reason": None,
                            "next_winner_match": None,
                            "next_loser_match": None,
                            "source_matches": [],
                            "source_losers_from": [],
                        }

                        # Link from previous losers round winner
                        if i < len(prev_round_matches):
                            match["source_matches"].append(prev_round_matches[i])
                            for m in matches:
                                if m["id"] == prev_round_matches[i]:
                                    m["next_winner_match"] = match_id
                                    break

                        # Link from winners bracket loser dropping down
                        if i < len(dropping_matches):
                            match["source_losers_from"].append(dropping_matches[i])
                            for m in matches:
                                if m["id"] == dropping_matches[i]:
                                    m["next_loser_match"] = match_id
                                    break

                        matches.append(match)
                        round_matches.append(match_id)
                        match_id += 1

                losers_matches_by_round[losers_round] = round_matches

        # ==================== GRAND FINALS ====================
        gf_deadline = datetime.now(timezone.utc) + timedelta(
            hours=round_deadline_hours * (winners_rounds + losers_rounds + 1)
        )

        # Grand Finals Match 1
        gf1_match = {
            "id": match_id,
            "round": 1,
            "bracket_type": "grand_finals",
            "gf_match": 1,
            "participant1": "TBD",  # Winners bracket champion
            "participant2": "TBD",  # Losers bracket champion
            "seed1": None,
            "seed2": None,
            "winner": None,
            "loser": None,
            "score": None,
            "completed": False,
            "deadline": gf_deadline.isoformat(),
            "proposed_time": None,
            "proposed_by": None,
            "scheduled_time": None,
            "scheduling_thread": None,
            "reminder_sent": False,
            "checked_in": [],
            "forfeit": False,
            "forfeit_reason": None,
            "next_winner_match": match_id + 1,  # Links to GF2 (bracket reset)
            "next_loser_match": None,
            "source_matches": [],
        }

        # Link winners bracket final to GF
        if winners_matches_by_round.get(winners_rounds):
            wf_match_id = winners_matches_by_round[winners_rounds][0]
            gf1_match["source_matches"].append(wf_match_id)
            for m in matches:
                if m["id"] == wf_match_id:
                    m["next_winner_match"] = match_id
                    break

        # Link losers bracket final to GF
        if losers_matches_by_round.get(losers_rounds):
            lf_match_id = losers_matches_by_round[losers_rounds][0]
            gf1_match["source_matches"].append(lf_match_id)
            for m in matches:
                if m["id"] == lf_match_id:
                    m["next_winner_match"] = match_id
                    break

        matches.append(gf1_match)
        gf1_id = match_id
        match_id += 1

        # Grand Finals Match 2 (Bracket Reset - only played if losers champ wins GF1)
        gf_reset_deadline = datetime.now(timezone.utc) + timedelta(
            hours=round_deadline_hours * (winners_rounds + losers_rounds + 2)
        )

        gf2_match = {
            "id": match_id,
            "round": 2,
            "bracket_type": "grand_finals",
            "gf_match": 2,
            "is_bracket_reset": True,
            "participant1": "TBD",  # Winner of GF1 (if bracket reset needed)
            "participant2": "TBD",  # Loser of GF1 (winners bracket champ)
            "seed1": None,
            "seed2": None,
            "winner": None,
            "loser": None,
            "score": None,
            "completed": False,
            "skipped": False,  # Will be True if winners champ wins GF1
            "deadline": gf_reset_deadline.isoformat(),
            "proposed_time": None,
            "proposed_by": None,
            "scheduled_time": None,
            "scheduling_thread": None,
            "reminder_sent": False,
            "checked_in": [],
            "forfeit": False,
            "forfeit_reason": None,
            "next_winner_match": None,
            "next_loser_match": None,
            "source_matches": [gf1_id],
        }
        matches.append(gf2_match)

        return matches

    def _generate_round_robin(
        self, entities: List, round_deadline_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Generate round robin bracket (everyone plays everyone)."""
        matches = []
        match_id = 1

        n = len(entities)
        if n % 2 != 0:
            entities = list(entities) + ["BYE"]
            n += 1

        # Round robin algorithm
        for round_idx in range(n - 1):
            round_deadline = datetime.now(timezone.utc) + timedelta(
                hours=round_deadline_hours * (round_idx + 1)
            )

            for i in range(n // 2):
                p1 = entities[i]
                p2 = entities[n - 1 - i]

                if p1 != "BYE" and p2 != "BYE":
                    matches.append({
                        "id": match_id,
                        "round": round_idx + 1,
                        "bracket_type": "round_robin",
                        "participant1": p1,
                        "participant2": p2,
                        "seed1": None,  # No seeds in round robin
                        "seed2": None,
                        "winner": None,
                        "score": None,
                        "completed": False,
                        "deadline": round_deadline.isoformat(),
                        "proposed_time": None,
                        "proposed_by": None,
                        "scheduled_time": None,
                        "scheduling_thread": None,
                        "reminder_sent": False,
                        "checked_in": [],
                        "forfeit": False,
                        "forfeit_reason": None,
                    })
                    match_id += 1

            # Rotate entities (keep first fixed)
            entities = [entities[0]] + [entities[-1]] + entities[1:-1]

        return matches

    async def _advance_bracket(
        self,
        guild: discord.Guild,
        tournament: Dict[str, Any],
        completed_match: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Advance the bracket after a match is completed.

        This method:
        1. Populates TBD slots in the next match with the winner
        2. For double elimination, moves the loser to their losers bracket match
        3. Handles grand finals logic (bracket reset)
        4. Creates match threads for newly-ready matches

        Returns list of matches that are now ready to be played.
        """
        bracket = tournament.get("bracket", [])
        tournament_format = tournament.get("format", "single_elimination")
        ready_matches = []

        winner = completed_match.get("winner")
        loser = completed_match.get("loser")

        # If loser not explicitly set, determine it
        if not loser and winner:
            p1 = completed_match.get("participant1")
            p2 = completed_match.get("participant2")
            loser = p2 if winner == p1 else p1

        # Store loser in completed match for reference
        if loser and loser != "BYE":
            completed_match["loser"] = loser

        # Handle winner advancement
        next_winner_match_id = completed_match.get("next_winner_match")
        if next_winner_match_id and winner:
            for match in bracket:
                if match["id"] == next_winner_match_id:
                    # Place winner in appropriate slot
                    if match["participant1"] == "TBD":
                        match["participant1"] = winner
                    elif match["participant2"] == "TBD":
                        match["participant2"] = winner

                    # Check if match is now ready (both participants set and not TBD)
                    if (match["participant1"] != "TBD" and
                        match["participant2"] != "TBD" and
                        not match.get("completed")):

                        # Handle BYE advancement
                        if match["participant1"] == "BYE":
                            match["winner"] = match["participant2"]
                            match["score"] = "BYE"
                            match["completed"] = True
                            # Recursively advance
                            ready_matches.extend(await self._advance_bracket(guild, tournament, match))
                        elif match["participant2"] == "BYE":
                            match["winner"] = match["participant1"]
                            match["score"] = "BYE"
                            match["completed"] = True
                            ready_matches.extend(await self._advance_bracket(guild, tournament, match))
                        else:
                            ready_matches.append(match)
                    break

        # Handle loser movement (double elimination only)
        if tournament_format == "double_elimination" and loser and loser != "BYE":
            next_loser_match_id = completed_match.get("next_loser_match")

            if next_loser_match_id:
                for match in bracket:
                    if match["id"] == next_loser_match_id:
                        # Place loser in appropriate slot
                        if match["participant1"] == "TBD":
                            match["participant1"] = loser
                        elif match["participant2"] == "TBD":
                            match["participant2"] = loser

                        # Check if match is now ready
                        if (match["participant1"] != "TBD" and
                            match["participant2"] != "TBD" and
                            not match.get("completed")):

                            # Handle BYE
                            if match["participant1"] == "BYE":
                                match["winner"] = match["participant2"]
                                match["score"] = "BYE"
                                match["completed"] = True
                                ready_matches.extend(await self._advance_bracket(guild, tournament, match))
                            elif match["participant2"] == "BYE":
                                match["winner"] = match["participant1"]
                                match["score"] = "BYE"
                                match["completed"] = True
                                ready_matches.extend(await self._advance_bracket(guild, tournament, match))
                            else:
                                ready_matches.append(match)
                        break

        # Handle Grand Finals special logic
        if completed_match.get("bracket_type") == "grand_finals":
            gf_match_num = completed_match.get("gf_match", 1)

            if gf_match_num == 1:
                # GF1 completed - check if bracket reset needed
                # Bracket reset only if the player from losers bracket wins

                # Find who came from winners bracket (they have the "advantage")
                # In GF1, participant1 should be winners bracket champ
                winners_bracket_champ = completed_match.get("participant1")

                if winner == winners_bracket_champ:
                    # Winners bracket champ won - tournament over, skip GF2
                    for match in bracket:
                        if match.get("bracket_type") == "grand_finals" and match.get("gf_match") == 2:
                            match["skipped"] = True
                            match["completed"] = True
                            match["score"] = "N/A"
                            match["winner"] = winner
                            break
                else:
                    # Losers bracket champ won - bracket reset needed
                    for match in bracket:
                        if match.get("bracket_type") == "grand_finals" and match.get("gf_match") == 2:
                            match["participant1"] = winner  # GF1 winner
                            match["participant2"] = loser   # GF1 loser (winners bracket champ)
                            ready_matches.append(match)
                            break

        # Create match threads for newly ready matches
        scheduling_channel_id = tournament.get("scheduling_channel")
        if scheduling_channel_id and ready_matches:
            sched_channel = guild.get_channel(scheduling_channel_id)
            if sched_channel:
                for match in ready_matches:
                    if not match.get("scheduling_thread") and not match.get("completed"):
                        try:
                            thread = await self._create_match_thread(
                                guild, sched_channel, match, tournament
                            )
                            if thread:
                                match["scheduling_thread"] = thread.id
                        except Exception as e:
                            log.error(f"Failed to create match thread: {e}")

        return ready_matches

    def _is_tournament_complete(self, tournament: Dict[str, Any]) -> tuple[bool, Any]:
        """Check if tournament is complete and return (is_complete, champion).

        For single elimination: complete when final match is done
        For double elimination: complete when grand finals (including potential reset) is done
        For round robin: complete when all matches are done
        """
        bracket = tournament.get("bracket", [])
        tournament_format = tournament.get("format", "single_elimination")

        if not bracket:
            return False, None

        if tournament_format == "round_robin":
            # Round robin: all matches must be complete
            incomplete = [m for m in bracket if not m.get("completed")]
            if incomplete:
                return False, None

            # Determine winner by most wins
            wins = {}
            for match in bracket:
                winner = match.get("winner")
                if winner:
                    wins[winner] = wins.get(winner, 0) + 1

            if wins:
                champion = max(wins, key=wins.get)
                return True, champion
            return True, None

        elif tournament_format == "double_elimination":
            # Find grand finals matches
            gf_matches = [m for m in bracket if m.get("bracket_type") == "grand_finals"]

            if not gf_matches:
                # Fallback: check if all matches complete
                incomplete = [m for m in bracket if not m.get("completed")]
                if incomplete:
                    return False, None
                # Get winner from last completed match
                completed = [m for m in bracket if m.get("completed") and m.get("winner")]
                if completed:
                    return True, completed[-1].get("winner")
                return True, None

            # Check GF2 (bracket reset) status
            gf2 = next((m for m in gf_matches if m.get("gf_match") == 2), None)
            gf1 = next((m for m in gf_matches if m.get("gf_match") == 1), None)

            if gf2:
                if gf2.get("skipped"):
                    # GF2 skipped means winners bracket champ won GF1
                    return True, gf1.get("winner") if gf1 else None
                elif gf2.get("completed"):
                    # GF2 was played (bracket reset happened)
                    return True, gf2.get("winner")
                else:
                    # GF2 not yet complete
                    return False, None
            elif gf1 and gf1.get("completed"):
                # Only GF1 exists and is complete
                return True, gf1.get("winner")

            return False, None

        else:
            # Single elimination: find the final match (highest round in winners)
            winners_matches = [m for m in bracket if m.get("bracket_type") == "winners"]
            if not winners_matches:
                winners_matches = bracket  # Fallback for old format

            max_round = max(m.get("round", 1) for m in winners_matches)
            final_matches = [m for m in winners_matches if m.get("round") == max_round]

            if final_matches:
                final = final_matches[0]
                if final.get("completed"):
                    return True, final.get("winner")

            return False, None

    async def update_tournament_embed(self, guild: discord.Guild, tournament_id: str, tournament: Dict[str, Any]) -> None:
        """Update the tournament embed with current signup info."""
        try:
            channel = guild.get_channel(tournament["channel_id"])
            if not channel:
                return

            message = await channel.fetch_message(tournament["message_id"])
            embed = message.embeds[0]

            if tournament["type"] == "solo":
                for i, field in enumerate(embed.fields):
                    if field.name == "Participants":
                        embed.set_field_at(i, name="Participants", value=str(len(tournament["participants"])), inline=True)
                        break
            else:
                team_size = tournament["team_size"]

                if tournament["teams"]:
                    teams_text = ""
                    for team_name, team_data in tournament["teams"].items():
                        player_mentions = []
                        for pid in team_data["players"]:
                            if pid == team_data["captain"]:
                                player_mentions.append(f"⭐<@{pid}>")
                            else:
                                player_mentions.append(f"<@{pid}>")

                        count = len(team_data["players"])
                        status = "✅" if count == team_size else f"({count}/{team_size})"
                        teams_text += f"**{team_name}** {status}: {', '.join(player_mentions)}\n"

                    if len(teams_text) > 1024:
                        teams_text = f"{len(tournament['teams'])} teams registered"
                else:
                    teams_text = "None yet"

                for i, field in enumerate(embed.fields):
                    if field.name == "Teams":
                        embed.set_field_at(i, name="Teams", value=teams_text, inline=False)
                    elif field.name == "Pickup Players":
                        embed.set_field_at(i, name="Pickup Players", value=str(len(tournament["pickup_players"])), inline=True)

            await message.edit(embed=embed)

        except Exception as e:
            log.error(f"Error updating tournament embed: {e}")

    # ==================== SLASH COMMANDS ====================

    @app_commands.command(name="tourney", description="Create or list tournaments")
    @app_commands.describe(action="Action to perform")
    @app_commands.choices(action=[
        app_commands.Choice(name="Create Solo Tournament", value="create_solo"),
        app_commands.Choice(name="Create Team Tournament", value="create_team"),
        app_commands.Choice(name="List Active", value="list"),
    ])
    async def tourney(self, interaction: discord.Interaction, action: str):
        """Main tournament command handler."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to manage tournaments.",
                ephemeral=True
            )
            return

        if action == "create_solo":
            modal = TournamentCreateModal(self, "solo")
            await interaction.response.send_modal(modal)

        elif action == "create_team":
            modal = TournamentCreateModal(self, "team")
            await interaction.response.send_modal(modal)

        elif action == "list":
            await self.list_tournaments(interaction)

    # ==================== OWNER COMMANDS (Challonge Setup) ====================

    @commands.hybrid_group(name="challongeset")
    @commands.is_owner()
    async def challongeset(self, ctx: commands.Context):
        """Configure Challonge API credentials (Bot Owner only)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @challongeset.command(name="credentials")
    @app_commands.describe(
        username="Your Challonge username",
        api_key="Your Challonge API key from https://challonge.com/settings/developer"
    )
    async def challongeset_credentials(
        self, ctx: commands.Context, username: str, api_key: str
    ):
        """Set Challonge API credentials."""
        if not CHALLONGE_AVAILABLE:
            await ctx.send(
                "❌ **pychallonge not installed.**\n"
                "Install it with: `pip install pychallonge`\n"
                "Then reload the cog.",
                ephemeral=True
            )
            return

        # Test the credentials
        try:
            challonge.set_credentials(username, api_key)
            # Try to list tournaments to verify
            await asyncio.to_thread(challonge.tournaments.index)

            # Save credentials
            await self.config.challonge_username.set(username)
            await self.config.challonge_api_key.set(api_key)
            self._challonge_ready = True

            await ctx.send("✅ **Challonge credentials saved and verified!**", ephemeral=True)

            # Delete the command message for security (contains API key) - only for prefix commands
            if ctx.interaction is None:
                try:
                    await ctx.message.delete()
                except discord.Forbidden:
                    await ctx.send("⚠️ Could not delete your message. Please delete it manually to protect your API key.")

        except Exception as e:
            await ctx.send(f"❌ **Failed to verify credentials:** {e}", ephemeral=True)

    @challongeset.command(name="subdomain")
    @app_commands.describe(subdomain="Organization subdomain (leave empty to clear)")
    async def challongeset_subdomain(
        self, ctx: commands.Context, subdomain: Optional[str] = None
    ):
        """Set Challonge organization subdomain (optional)."""
        await self.config.challonge_subdomain.set(subdomain)

        if subdomain:
            await ctx.send(
                f"✅ Challonge subdomain set to: `{subdomain}`\n"
                f"Brackets will be at: `https://{subdomain}.challonge.com/...`",
                ephemeral=True
            )
        else:
            await ctx.send("✅ Challonge subdomain cleared. Using default challonge.com", ephemeral=True)

    @challongeset.command(name="status")
    async def challongeset_status(self, ctx: commands.Context):
        """Check Challonge integration status."""
        embed = discord.Embed(
            title="🏆 Challonge Integration Status",
            color=discord.Color.blue()
        )

        # Library status
        if CHALLONGE_AVAILABLE:
            embed.add_field(name="Library", value="✅ pychallonge installed", inline=True)
        else:
            embed.add_field(name="Library", value="❌ pychallonge not installed", inline=True)

        # Credentials status
        username = await self.config.challonge_username()
        api_key = await self.config.challonge_api_key()
        subdomain = await self.config.challonge_subdomain()

        if username and api_key:
            embed.add_field(name="Credentials", value=f"✅ Set (user: {username})", inline=True)
        else:
            embed.add_field(name="Credentials", value="❌ Not configured", inline=True)

        # Subdomain
        embed.add_field(
            name="Subdomain",
            value=subdomain if subdomain else "None (using challonge.com)",
            inline=True
        )

        # Ready status
        embed.add_field(
            name="Status",
            value="✅ Ready" if self._challonge_ready else "❌ Not ready",
            inline=True
        )

        await ctx.send(embed=embed, ephemeral=True)

    @challongeset.command(name="test")
    async def challongeset_test(self, ctx: commands.Context):
        """Test Challonge API connection."""
        if not self._challonge_ready:
            await ctx.send("❌ Challonge not configured. Use `/challongeset credentials` first.", ephemeral=True)
            return

        try:
            tournaments = await asyncio.to_thread(challonge.tournaments.index)
            await ctx.send(f"✅ **Connection successful!**\nFound {len(tournaments)} tournaments on your account.", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ **Connection failed:** {e}", ephemeral=True)

    # ==================== GUILD ADMIN COMMANDS ====================

    @app_commands.command(name="tourneyset", description="Configure tournament settings")
    @app_commands.describe(
        setting="Setting to configure",
        role="Role for add/remove role actions",
        game="Game name for game-related actions"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
        app_commands.Choice(name="Set Default Format", value="format"),
        app_commands.Choice(name="Add Game", value="addgame"),
        app_commands.Choice(name="Remove Game", value="removegame"),
        app_commands.Choice(name="List Games", value="listgames"),
        app_commands.Choice(name="Configure Seed List", value="seedlist"),
    ])
    async def tourneyset(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None,
        game: Optional[str] = None
    ):
        """Configure tournament settings."""
        # Bot owner always authorized
        is_owner = await self.bot.is_owner(interaction.user)
        if not is_owner and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can change tournament settings.",
                ephemeral=True
            )
            return

        if setting == "view":
            config = await self.config.guild(interaction.guild).all()

            mod_role_mentions = []
            for role_id in config.get("mod_roles", []):
                r = interaction.guild.get_role(role_id)
                if r:
                    mod_role_mentions.append(r.mention)

            embed = discord.Embed(
                title="🏆 ShadyTourneys Settings",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Mod Roles",
                value=", ".join(mod_role_mentions) if mod_role_mentions else "None (admins only)",
                inline=False
            )
            embed.add_field(
                name="Default Format",
                value=config.get("default_format", "single_elimination"),
                inline=True
            )

            # Check global Challonge config
            challonge_status = "❌ Not configured"
            if not CHALLONGE_AVAILABLE:
                challonge_status = "⚠️ pychallonge not installed"
            elif self._challonge_ready:
                challonge_status = "✅ Ready"

            embed.add_field(
                name="Challonge Integration",
                value=challonge_status,
                inline=True
            )
            embed.add_field(
                name="Active Tournaments",
                value=str(len([t for t in config.get("tournaments", {}).values() if not t.get("cancelled")])),
                inline=True
            )

            # Show supported games
            supported_games = config.get("supported_games", ["rivals"])
            embed.add_field(
                name="Supported Games",
                value=", ".join(g.title() for g in supported_games) if supported_games else "None",
                inline=False
            )

            embed.set_footer(text=f"v{self.__version__}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif setting == "addrole":
            if not role:
                await interaction.response.send_message("Please specify a role.", ephemeral=True)
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id in roles:
                    await interaction.response.send_message(f"❌ {role.mention} is already a mod role.", ephemeral=True)
                    return
                roles.append(role.id)

            await interaction.response.send_message(f"✅ {role.mention} can now manage tournaments.", ephemeral=True)

        elif setting == "removerole":
            if not role:
                await interaction.response.send_message("Please specify a role.", ephemeral=True)
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id not in roles:
                    await interaction.response.send_message(f"❌ {role.mention} is not a mod role.", ephemeral=True)
                    return
                roles.remove(role.id)

            await interaction.response.send_message(f"✅ {role.mention} can no longer manage tournaments.", ephemeral=True)

        elif setting == "format":
            # Show format selection view
            view = FormatSelectView(self)
            await interaction.response.send_message(
                "Select default tournament format:",
                view=view,
                ephemeral=True
            )

        elif setting == "addgame":
            if not game:
                await interaction.response.send_message(
                    "Please specify a game name. Example: `/tourneyset setting:Add Game game:valorant`",
                    ephemeral=True
                )
                return

            game_lower = game.lower().strip()
            async with self.config.guild(interaction.guild).supported_games() as games:
                if game_lower in games:
                    await interaction.response.send_message(
                        f"❌ **{game.title()}** is already in the supported games list.",
                        ephemeral=True
                    )
                    return
                games.append(game_lower)

            await interaction.response.send_message(
                f"✅ Added **{game.title()}** to supported games.\n"
                f"It will now appear in game autocomplete dropdowns.",
                ephemeral=True
            )

        elif setting == "removegame":
            if not game:
                await interaction.response.send_message(
                    "Please specify a game name to remove.",
                    ephemeral=True
                )
                return

            game_lower = game.lower().strip()
            async with self.config.guild(interaction.guild).supported_games() as games:
                if game_lower not in games:
                    await interaction.response.send_message(
                        f"❌ **{game.title()}** is not in the supported games list.",
                        ephemeral=True
                    )
                    return
                games.remove(game_lower)

            await interaction.response.send_message(
                f"✅ Removed **{game.title()}** from supported games.\n"
                f"Note: Existing player stats for this game are preserved.",
                ephemeral=True
            )

        elif setting == "listgames":
            supported_games = await self.config.guild(interaction.guild).supported_games()
            player_stats = await self.config.guild(interaction.guild).player_stats()
            seed_lists = await self.config.guild(interaction.guild).seed_lists()

            embed = discord.Embed(
                title="🎮 Tournament Games",
                color=discord.Color.blue()
            )

            if supported_games:
                game_list = []
                for g in supported_games:
                    has_seeds = "✅" if g in seed_lists else "⚪"
                    game_list.append(f"{has_seeds} {g.title()}")
                embed.add_field(
                    name="Supported Games (✅ = has seed list)",
                    value="\n".join(game_list),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Supported Games",
                    value="None configured",
                    inline=False
                )

            # Show games with existing stats
            games_with_stats = [g for g in player_stats.keys() if g not in supported_games]
            if games_with_stats:
                embed.add_field(
                    name="Games with Stats (not in list)",
                    value="\n".join(f"• {g.title()}" for g in games_with_stats),
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif setting == "seedlist":
            if not game:
                await interaction.response.send_message(
                    "Please specify a game. Example: `/tourneyset setting:Configure Seed List game:valorant`",
                    ephemeral=True
                )
                return

            game_lower = game.lower().strip()

            # Check if game is in supported list
            supported_games = await self.config.guild(interaction.guild).supported_games()
            if game_lower not in supported_games:
                await interaction.response.send_message(
                    f"⚠️ **{game.title()}** is not in supported games.\n"
                    f"Add it first with `/tourneyset setting:Add Game game:{game}`",
                    ephemeral=True
                )
                return

            # Get current seed list for this game
            seed_lists = await self.config.guild(interaction.guild).seed_lists()
            current_list = seed_lists.get(game_lower, {})

            modal = SeedListModal(self, game_lower, current_list)
            await interaction.response.send_modal(modal)

    @app_commands.command(name="tourneystats", description="View player ELO statistics (Admin only)")
    @app_commands.describe(
        game="Game to view stats for",
        player="Optional: View specific player's stats"
    )
    async def tourneystats(
        self,
        interaction: discord.Interaction,
        game: str,
        player: Optional[discord.Member] = None
    ):
        """View player ELO statistics (admin only)."""
        if not interaction.user.guild_permissions.administrator:
            # Also check mod roles
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "Only tournament moderators can view player stats.",
                    ephemeral=True
                )
                return

        player_stats = await self.config.guild(interaction.guild).player_stats()
        game_lower = game.lower()

        if game_lower not in player_stats:
            await interaction.response.send_message(
                f"No stats found for game: **{game}**\n"
                "Stats are recorded after tournaments are played.",
                ephemeral=True
            )
            return

        game_stats = player_stats[game_lower]

        if player:
            # Show specific player's stats
            user_id_str = str(player.id)
            if user_id_str not in game_stats:
                await interaction.response.send_message(
                    f"No stats found for {player.mention} in **{game}**.",
                    ephemeral=True
                )
                return

            stats = game_stats[user_id_str]
            embed = discord.Embed(
                title=f"📊 Player Stats: {player.display_name}",
                description=f"**Game:** {game}",
                color=discord.Color.blue()
            )
            embed.add_field(name="ELO Rating", value=str(stats.get("elo", 1000)), inline=True)
            embed.add_field(name="Wins", value=str(stats.get("wins", 0)), inline=True)
            embed.add_field(name="Losses", value=str(stats.get("losses", 0)), inline=True)

            matches = stats.get("matches_played", 0)
            wins = stats.get("wins", 0)
            winrate = f"{(wins/matches)*100:.1f}%" if matches > 0 else "N/A"
            embed.add_field(name="Win Rate", value=winrate, inline=True)
            embed.add_field(name="Matches Played", value=str(matches), inline=True)
            embed.add_field(name="Tournaments Won", value=str(stats.get("tournaments_won", 0)), inline=True)

            if stats.get("last_played"):
                last = datetime.fromisoformat(stats["last_played"])
                embed.add_field(name="Last Played", value=f"<t:{int(last.timestamp())}:R>", inline=True)

            embed.set_thumbnail(url=player.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        else:
            # Show leaderboard for game
            sorted_players = sorted(
                game_stats.items(),
                key=lambda x: x[1].get("elo", 1000),
                reverse=True
            )[:20]

            if not sorted_players:
                await interaction.response.send_message(
                    f"No players have stats for **{game}** yet.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"📊 ELO Leaderboard: {game}",
                color=discord.Color.gold()
            )

            lines = []
            for i, (user_id_str, stats) in enumerate(sorted_players, 1):
                elo = stats.get("elo", 1000)
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)

                member = interaction.guild.get_member(int(user_id_str))
                name = member.display_name if member else f"User {user_id_str[:8]}..."

                medal = ""
                if i == 1:
                    medal = "🥇 "
                elif i == 2:
                    medal = "🥈 "
                elif i == 3:
                    medal = "🥉 "

                lines.append(f"{medal}**{i}.** {name} - **{elo}** ELO ({wins}W/{losses}L)")

            embed.description = "\n".join(lines)
            embed.set_footer(text="ELO is admin-visible only")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @tourneystats.autocomplete("game")
    async def tourneystats_game_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for game names in stats."""
        player_stats = await self.config.guild(interaction.guild).player_stats()
        supported_games = await self.config.guild(interaction.guild).supported_games()

        # Combine supported games + games with existing stats
        games = list(set(supported_games + list(player_stats.keys())))
        games.sort()

        if not current:
            return [app_commands.Choice(name=g.title(), value=g) for g in games[:25]]

        filtered = [g for g in games if current.lower() in g.lower()]
        return [app_commands.Choice(name=g.title(), value=g) for g in filtered[:25]]

    @app_commands.command(name="tourneyseed", description="Seed players with initial ELO based on rank")
    async def tourneyseed(self, interaction: discord.Interaction):
        """Open the seeding interface to set initial ELO for players."""
        if not interaction.user.guild_permissions.administrator:
            if not await self.is_authorized(interaction):
                await interaction.response.send_message(
                    "Only tournament moderators can set player seeds.",
                    ephemeral=True
                )
                return

        supported_games = await self.config.guild(interaction.guild).supported_games()
        seed_lists = await self.config.guild(interaction.guild).seed_lists()

        if not supported_games:
            await interaction.response.send_message(
                "❌ No games configured.\n\n"
                "Add games first with `/tourneyset setting:Add Game game:<name>`\n"
                "Then configure ranks with `/tourneyset setting:Configure Seed List game:<name>`",
                ephemeral=True
            )
            return

        view = SeedingView(self, supported_games, seed_lists)
        await interaction.response.send_message(
            "**🎯 Player Seeding**\n\n"
            "1️⃣ Select a game\n"
            "2️⃣ Select a rank\n"
            "3️⃣ Select player(s) to seed\n"
            "4️⃣ Confirm",
            view=view,
            ephemeral=True
        )

    async def list_tournaments(self, interaction: discord.Interaction) -> None:
        """List all active tournaments."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        active = [(tid, t) for tid, t in tournaments.items() if not t.get("cancelled")]

        if not active:
            await interaction.response.send_message(
                "No active tournaments.\n\nUse `/tourney` to create one!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏆 Active Tournaments",
            color=discord.Color.blue()
        )

        for tournament_id, tournament in active[:10]:
            channel = interaction.guild.get_channel(tournament["channel_id"])
            channel_mention = channel.mention if channel else "Unknown"

            status = "🏁 Started" if tournament.get("started") else "🟢 Open"

            if tournament["type"] == "solo":
                count_str = f"{len(tournament.get('participants', []))} participants"
            else:
                count_str = f"{len(tournament.get('teams', {}))} teams"

            format_display = tournament.get("format", "single_elimination").replace("_", " ").title()

            embed.add_field(
                name=f"{tournament['name']} ({tournament['game']})",
                value=f"Status: {status}\nFormat: {format_display}\nChannel: {channel_mention}\n{count_str}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def create_tournament(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        name: str,
        game: str,
        tournament_type: str,
        team_size: Optional[int],
        format: str = "single_elimination",
        prize_pool: Optional[str] = None,
    ) -> None:
        """Create a new tournament."""
        tournament_id = f"{interaction.guild.id}_{int(datetime.now(timezone.utc).timestamp())}"

        # Create on Challonge if configured
        challonge_id = None
        challonge_url = None
        challonge_image = None

        if self._challonge_ready:
            description = f"Tournament hosted by {interaction.guild.name}"
            if prize_pool:
                description += f"\nPrize Pool: {prize_pool}"

            challonge_result = await self._challonge_create_tournament(
                name=name,
                game=game,
                tournament_format=format,
                description=description,
            )

            if challonge_result:
                challonge_id = challonge_result["id"]
                challonge_url = challonge_result["url"]
                challonge_image = challonge_result["image_url"]
                log.info(f"Created Challonge tournament: {challonge_url}")

        embed = discord.Embed(
            title=f"🏆 {name}",
            description=f"**Game:** {game}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        format_display = format.replace("_", " ").title()
        embed.add_field(name="Format", value=format_display, inline=True)

        if prize_pool:
            embed.add_field(name="Prize Pool", value=prize_pool, inline=True)

        if tournament_type == "solo":
            embed.add_field(name="Type", value="Solo", inline=True)
            embed.add_field(name="Participants", value="0", inline=True)
            view = SoloSignupView(self, tournament_id)
        else:
            embed.add_field(name="Type", value=f"Team ({team_size}v{team_size})", inline=True)
            embed.add_field(
                name="How to Join",
                value="**⭐ Create Team:** Become captain\n"
                      "**👥 Join a Team:** Pick from dropdown\n"
                      "**🎲 Join as Pickup:** Random assignment",
                inline=False
            )
            embed.add_field(name="Teams", value="None yet", inline=False)
            embed.add_field(name="Pickup Players", value="0", inline=True)
            view = TeamSignupView(self, tournament_id, team_size)

        embed.add_field(name="Status", value="🟢 Open for Signups", inline=False)

        # Add bracket link if Challonge is configured
        if challonge_url:
            embed.add_field(name="📊 Bracket", value=f"[View on Challonge]({challonge_url})", inline=False)

        embed.set_footer(text=f"Tournament ID: {tournament_id}")

        message = await channel.send(embed=embed, view=view)

        self.active_views[tournament_id] = view

        async with self.config.guild(interaction.guild).tournaments() as tournaments:
            tournaments[tournament_id] = {
                "message_id": message.id,
                "channel_id": channel.id,
                "name": name,
                "game": game,
                "host_id": interaction.user.id,
                "type": tournament_type,
                "format": format,
                "team_size": team_size,
                "participants": [],
                "teams": {},
                "pickup_players": [],
                "started": False,
                "cancelled": False,
                "bracket": None,
                "prize_pool": prize_pool,
                "challonge_id": challonge_id,
                "challonge_url": challonge_url,
                "challonge_image": challonge_image,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        response_msg = f"✅ Tournament **{name}** created in {channel.mention}!"
        if challonge_url:
            response_msg += f"\n📊 Bracket: {challonge_url}"
        else:
            response_msg += "\n⚠️ Challonge not configured - using local brackets only."

        await interaction.response.send_message(response_msg, ephemeral=True)

    # Handler methods for signup views
    async def handle_solo_join(self, interaction: discord.Interaction, tournament_id: str) -> None:
        """Handle solo tournament join."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        if tournament_id not in tournaments:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        tournament = tournaments[tournament_id]

        if tournament.get("started"):
            await interaction.response.send_message("This tournament has already started.", ephemeral=True)
            return

        if tournament.get("cancelled"):
            await interaction.response.send_message("This tournament has been cancelled.", ephemeral=True)
            return

        if interaction.user.id in tournament["participants"]:
            await interaction.response.send_message("You've already joined this tournament!", ephemeral=True)
            return

        tournament["participants"].append(interaction.user.id)
        async with self.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        await self.update_tournament_embed(interaction.guild, tournament_id, tournament)

        await interaction.response.send_message(
            f"✅ You've joined **{tournament['name']}**! ({len(tournament['participants'])} participants)",
            ephemeral=True
        )

    async def handle_leave(self, interaction: discord.Interaction, tournament_id: str) -> None:
        """Handle player leaving a tournament."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        if tournament_id not in tournaments:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        tournament = tournaments[tournament_id]

        if tournament.get("started"):
            await interaction.response.send_message("Cannot leave a tournament that has started.", ephemeral=True)
            return

        left = False
        left_from = ""

        if interaction.user.id in tournament.get("participants", []):
            tournament["participants"].remove(interaction.user.id)
            left = True
            left_from = "the tournament"

        if interaction.user.id in tournament.get("pickup_players", []):
            tournament["pickup_players"].remove(interaction.user.id)
            left = True
            left_from = "the pickup pool"

        for team_name, team_data in list(tournament.get("teams", {}).items()):
            if interaction.user.id in team_data["players"]:
                team_data["players"].remove(interaction.user.id)

                if not team_data["players"]:
                    del tournament["teams"][team_name]
                    left_from = f"team **{team_name}** (team disbanded)"
                elif team_data["captain"] == interaction.user.id:
                    team_data["captain"] = team_data["players"][0]
                    left_from = f"team **{team_name}** (captain transferred)"
                else:
                    left_from = f"team **{team_name}**"

                left = True
                break

        if not left:
            await interaction.response.send_message("You're not in this tournament.", ephemeral=True)
            return

        async with self.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        await self.update_tournament_embed(interaction.guild, tournament_id, tournament)

        await interaction.response.send_message(
            f"✅ You've left {left_from}.",
            ephemeral=True
        )

    async def handle_pickup_join(self, interaction: discord.Interaction, tournament_id: str) -> None:
        """Handle player joining the pickup pool."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        if tournament_id not in tournaments:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        tournament = tournaments[tournament_id]

        if tournament.get("started"):
            await interaction.response.send_message("This tournament has already started.", ephemeral=True)
            return

        if tournament.get("cancelled"):
            await interaction.response.send_message("This tournament has been cancelled.", ephemeral=True)
            return

        # Check if already in a team
        for team_name, team_data in tournament.get("teams", {}).items():
            if interaction.user.id in team_data["players"]:
                await interaction.response.send_message(
                    f"You're already on team **{team_name}**. Leave first to join as pickup.",
                    ephemeral=True
                )
                return

        # Check if already in pickup pool
        if interaction.user.id in tournament.get("pickup_players", []):
            await interaction.response.send_message("You're already in the pickup pool!", ephemeral=True)
            return

        tournament["pickup_players"].append(interaction.user.id)
        async with self.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        await self.update_tournament_embed(interaction.guild, tournament_id, tournament)

        await interaction.response.send_message(
            f"✅ You've joined the pickup pool! ({len(tournament['pickup_players'])} pickups waiting)\n"
            "You'll be assigned to a team that needs players when the tournament starts.",
            ephemeral=True
        )

    # ==================== TOURNAMENT MANAGEMENT COMMANDS ====================

    async def tournament_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for tournament IDs."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        choices = []
        for tid, tournament in tournaments.items():
            if tournament.get("cancelled"):
                continue

            # Match by name or ID
            name = tournament.get("name", "Unknown")
            if current.lower() in name.lower() or current.lower() in tid.lower():
                label = f"{name} ({tournament.get('game', 'Unknown')})"
                if len(label) > 100:
                    label = label[:97] + "..."
                choices.append(app_commands.Choice(name=label, value=tid))

        return choices[:25]

    async def active_tournament_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for active (started, not completed) tournaments."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        choices = []
        for tid, tournament in tournaments.items():
            # Only show started, non-cancelled, non-completed tournaments
            if tournament.get("cancelled") or not tournament.get("started"):
                continue
            if tournament.get("completed"):
                continue

            # Match by name or ID
            name = tournament.get("name", "Unknown")
            if current.lower() in name.lower() or current.lower() in tid.lower():
                # Show status indicator
                bracket = tournament.get("bracket", [])
                completed = sum(1 for m in bracket if m.get("completed"))
                total = len([m for m in bracket if m.get("participant1") != "TBD" or m.get("completed")])
                label = f"{name} ({completed}/{total} matches)"
                if len(label) > 100:
                    label = label[:97] + "..."
                choices.append(app_commands.Choice(name=label, value=tid))

        return choices[:25]

    @app_commands.command(name="bracket", description="View a tournament bracket")
    @app_commands.describe(
        tournament_id="Tournament to view (leave empty for only active tournament)"
    )
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    async def bracket_command(
        self,
        interaction: discord.Interaction,
        tournament_id: Optional[str] = None
    ):
        """View a tournament bracket. Anyone can use this command."""
        tournaments = await self.config.guild(interaction.guild).tournaments()

        # If no tournament specified, try to find the only active one
        if tournament_id is None:
            active = [
                (tid, t) for tid, t in tournaments.items()
                if t.get("started") and not t.get("cancelled") and not t.get("completed")
            ]

            if len(active) == 0:
                await interaction.response.send_message(
                    "No active tournaments. Use `/tourney` to create one!",
                    ephemeral=True
                )
                return
            elif len(active) == 1:
                tournament_id = active[0][0]
            else:
                # Multiple active - list them
                lines = ["**Multiple active tournaments:**"]
                for tid, t in active[:10]:
                    lines.append(f"• **{t['name']}** - `{tid}`")
                lines.append("\nUse `/bracket <tournament_id>` to view a specific bracket.")
                await interaction.response.send_message("\n".join(lines), ephemeral=True)
                return

        if tournament_id not in tournaments:
            await interaction.response.send_message(
                "Tournament not found. Check the tournament ID.",
                ephemeral=True
            )
            return

        tournament = tournaments[tournament_id]

        # Check tournament status
        if tournament.get("cancelled"):
            await interaction.response.send_message(
                "This tournament was cancelled.",
                ephemeral=True
            )
            return

        if not tournament.get("started"):
            # Show signup status instead
            participants = tournament.get("participants", [])
            teams = tournament.get("teams", {})
            count = len(teams) if tournament["type"] == "team" else len(participants)

            embed = discord.Embed(
                title=f"📋 {tournament['name']} - Signups Open",
                description="Tournament hasn't started yet. Bracket will be available after start.",
                color=discord.Color.blue()
            )
            embed.add_field(name="Game", value=tournament.get("game", "Unknown"), inline=True)
            embed.add_field(name="Format", value=tournament.get("format", "single_elimination").replace("_", " ").title(), inline=True)
            embed.add_field(name="Participants", value=str(count), inline=True)

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Tournament is active - show bracket
        bracket = tournament.get("bracket", [])
        if not bracket:
            await interaction.response.send_message(
                "No bracket data available.",
                ephemeral=True
            )
            return

        is_team = tournament["type"] == "team"
        tournament_format = tournament.get("format", "single_elimination")

        # Calculate stats
        total_matches = len([m for m in bracket if m.get("participant1") != "TBD" or m.get("completed")])
        completed_matches = sum(1 for m in bracket if m.get("completed"))
        remaining = total_matches - completed_matches

        # Determine current round/phase
        if tournament_format == "double_elimination":
            # Find current phase
            winners_pending = [m for m in bracket if m.get("bracket_type") == "winners" and not m.get("completed") and m.get("participant1") != "TBD"]
            losers_pending = [m for m in bracket if m.get("bracket_type") == "losers" and not m.get("completed") and m.get("participant1") != "TBD"]
            gf_pending = [m for m in bracket if m.get("bracket_type") == "grand_finals" and not m.get("completed") and m.get("participant1") != "TBD"]

            if gf_pending:
                current_phase = "Grand Finals"
            elif losers_pending and not winners_pending:
                current_phase = "Losers Bracket"
            elif winners_pending:
                max_round = max(m.get("round", 1) for m in winners_pending)
                current_phase = f"Winners Round {max_round}"
            else:
                current_phase = "Completed"
        else:
            pending = [m for m in bracket if not m.get("completed") and m.get("participant1") != "TBD"]
            if pending:
                max_round = max(m.get("round", 1) for m in pending)
                current_phase = f"Round {max_round}"
            else:
                current_phase = "Completed"

        # Build embed
        format_name = {
            "single_elimination": "Single Elimination",
            "double_elimination": "Double Elimination",
            "round_robin": "Round Robin"
        }.get(tournament_format, "Unknown")

        embed = discord.Embed(
            title=f"🏆 {tournament['name']} - Bracket",
            color=discord.Color.gold() if tournament.get("completed") else discord.Color.blue()
        )

        embed.add_field(name="Format", value=format_name, inline=True)
        embed.add_field(name="Status", value=current_phase, inline=True)
        embed.add_field(name="Progress", value=f"{completed_matches}/{total_matches} matches", inline=True)

        if remaining > 0:
            embed.add_field(name="Remaining", value=f"{remaining} matches", inline=True)

        # Add text bracket summary
        bracket_text = self._format_bracket(bracket, is_team, tournament_format)
        if len(bracket_text) > 1000:
            bracket_text = bracket_text[:997] + "..."
        embed.add_field(name="Current Matches", value=bracket_text, inline=False)

        # Add Challonge integration
        challonge_url = tournament.get("challonge_url")
        challonge_image = tournament.get("challonge_image")

        if challonge_image:
            # Add timestamp to bust cache and get latest bracket
            cache_bust = f"?t={int(datetime.now(timezone.utc).timestamp())}"
            embed.set_image(url=challonge_image + cache_bust)

        # Create view with buttons
        view = BracketView(challonge_url)

        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="tourneymanage", description="Manage an active tournament")
    @app_commands.describe(
        action="Action to perform",
        tournament_id="Tournament ID (from embed footer)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Start Tournament", value="start"),
        app_commands.Choice(name="Cancel Tournament", value="cancel"),
        app_commands.Choice(name="View Bracket", value="bracket"),
        app_commands.Choice(name="Report Match Result", value="report"),
        app_commands.Choice(name="Match History (Disputes)", value="history"),
    ])
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    async def tourneymanage(
        self,
        interaction: discord.Interaction,
        action: str,
        tournament_id: str
    ):
        """Manage tournament actions."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to manage tournaments.",
                ephemeral=True
            )
            return

        tournaments = await self.config.guild(interaction.guild).tournaments()

        if tournament_id not in tournaments:
            await interaction.response.send_message(
                "Tournament not found. Check the tournament ID from the embed footer.",
                ephemeral=True
            )
            return

        tournament = tournaments[tournament_id]

        if action == "start":
            await self.start_tournament(interaction, tournament_id, tournament)
        elif action == "cancel":
            await self.cancel_tournament(interaction, tournament_id, tournament)
        elif action == "bracket":
            await self.show_bracket(interaction, tournament_id, tournament)
        elif action == "report":
            await self.report_match(interaction, tournament_id, tournament)
        elif action == "history":
            await self.show_match_history(interaction, tournament_id, tournament)

    async def show_match_history(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        tournament: Dict[str, Any]
    ) -> None:
        """Show match history for dispute resolution."""
        bracket = tournament.get("bracket", [])

        if not bracket:
            await interaction.response.send_message("No matches in this tournament yet.", ephemeral=True)
            return

        # Build select menu for matches
        options = []
        for match in bracket:
            p1 = match["participant1"]
            p2 = match["participant2"]

            if tournament["type"] == "team":
                label = f"R{match.get('round', '?')}: {p1} vs {p2}"
            else:
                label = f"R{match.get('round', '?')}: Match {match['id']}"

            status = "✅" if match.get("completed") else "⏳"
            options.append(discord.SelectOption(
                label=f"{status} {label}"[:100],
                value=str(match["id"]),
                description=f"Score: {match.get('score', 'Pending')}"[:100] if match.get("completed") else "Not played yet"
            ))

        if not options:
            await interaction.response.send_message("No matches found.", ephemeral=True)
            return

        view = MatchHistorySelectView(self, tournament_id, tournament, options[:25])
        await interaction.response.send_message(
            "Select a match to view its history:",
            view=view,
            ephemeral=True
        )

    async def start_tournament(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        tournament: Dict[str, Any]
    ) -> None:
        """Start a tournament and generate bracket."""
        if tournament.get("started"):
            await interaction.response.send_message("Tournament has already started.", ephemeral=True)
            return

        if tournament.get("cancelled"):
            await interaction.response.send_message("Tournament was cancelled.", ephemeral=True)
            return

        await interaction.response.defer()

        # Validate participants
        is_team = tournament["type"] == "team"

        if not is_team:
            participants = tournament.get("participants", [])
            if len(participants) < 2:
                await interaction.followup.send(
                    "Need at least 2 participants to start.",
                    ephemeral=True
                )
                return
            entities = participants
            teams_data = None
        else:
            teams = tournament.get("teams", {})
            team_size = tournament["team_size"]

            # Distribute pickup players to incomplete teams
            pickup_players = list(tournament.get("pickup_players", []))
            random.shuffle(pickup_players)

            for team_name, team_data in teams.items():
                while len(team_data["players"]) < team_size and pickup_players:
                    team_data["players"].append(pickup_players.pop(0))

            # Filter to only full teams
            full_teams = [name for name, data in teams.items() if len(data["players"]) >= team_size]

            if len(full_teams) < 2:
                await interaction.followup.send(
                    f"Need at least 2 full teams ({team_size} players each) to start.\n"
                    f"Current full teams: {len(full_teams)}",
                    ephemeral=True
                )
                return

            entities = full_teams
            tournament["teams"] = {name: teams[name] for name in full_teams}
            teams_data = tournament["teams"]

            # Remaining pickups couldn't be placed
            tournament["pickup_players"] = pickup_players if pickup_players else []

        # Apply seeding (manual > ELO > random)
        manual_seeds = tournament.get("seeds", {})
        game = tournament.get("game", "Unknown")

        seeded_entities = await self._get_seeded_entities(
            guild_id=interaction.guild.id,
            game=game,
            entities=entities,
            manual_seeds=manual_seeds,
            is_team=is_team,
            teams_data=teams_data
        )

        # Get round deadline (default 48 hours)
        round_deadline_hours = tournament.get("round_deadline_hours", 48)

        # Generate local bracket with seeding
        bracket = self.generate_bracket(
            seeded_entities,
            tournament.get("format", "single_elimination"),
            seeded=True,
            round_deadline_hours=round_deadline_hours
        )
        tournament["bracket"] = bracket
        tournament["started"] = True

        # Sync to Challonge if configured
        challonge_id = tournament.get("challonge_id")
        challonge_url = tournament.get("challonge_url")
        challonge_started = False

        if challonge_id and self._challonge_ready:
            # Build participant names (use seeded order)
            if not is_team:
                participant_names = []
                for user_id in seeded_entities:
                    member = interaction.guild.get_member(user_id)
                    if member:
                        participant_names.append(member.display_name)
                    else:
                        participant_names.append(f"User_{user_id}")
            else:
                participant_names = seeded_entities

            # Add participants to Challonge
            if await self._challonge_add_participants_bulk(challonge_id, participant_names):
                # Start the tournament on Challonge
                if await self._challonge_start_tournament(challonge_id):
                    challonge_started = True
                    log.info(f"Started Challonge tournament: {challonge_url}")
                else:
                    log.error(f"Failed to start Challonge tournament {challonge_id}")
            else:
                log.error(f"Failed to add participants to Challonge tournament {challonge_id}")

        # Create match scheduling threads for first round
        scheduling_channel = tournament.get("scheduling_channel")
        if scheduling_channel:
            sched_channel = interaction.guild.get_channel(scheduling_channel)
            if sched_channel:
                for match in bracket:
                    if match.get("round") == 1 and not match.get("completed"):
                        thread = await self._create_match_thread(
                            interaction.guild, sched_channel, match, tournament
                        )
                        if thread:
                            match["scheduling_thread"] = thread.id

        async with self.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        # Update the original message
        try:
            channel = interaction.guild.get_channel(tournament["channel_id"])
            if channel:
                message = await channel.fetch_message(tournament["message_id"])
                embed = message.embeds[0]

                # Update status field
                for i, field in enumerate(embed.fields):
                    if field.name == "Status":
                        embed.set_field_at(i, name="Status", value="🏁 Tournament Started!", inline=False)
                        break

                # Remove buttons
                await message.edit(embed=embed, view=None)
        except Exception as e:
            log.error(f"Error updating tournament message: {e}")

        # Remove from active views
        if tournament_id in self.active_views:
            self.active_views[tournament_id].stop()
            del self.active_views[tournament_id]

        # Build bracket display
        tournament_format = tournament.get("format", "single_elimination")
        bracket_text = self._format_bracket(bracket, is_team, tournament_format)

        # Format header based on tournament format
        format_name = {
            "single_elimination": "Single Elimination",
            "double_elimination": "Double Elimination",
            "round_robin": "Round Robin"
        }.get(tournament_format, "Unknown")

        response = f"🏆 **{tournament['name']}** has started!\n\n"
        response += f"**Format:** {format_name}\n"
        response += f"**Seeding applied** (Manual → ELO → Random)\n\n"
        response += f"**Bracket:**\n{bracket_text}\n\n"

        if challonge_url:
            response += f"📊 **Bracket:** {challonge_url}\n\n"

        if scheduling_channel:
            response += f"📅 Match scheduling threads created in <#{scheduling_channel}>\n"
            response += "Participants: Use the threads to schedule your matches!\n\n"

        response += f"Use `/tourneymanage report` to report match results."

        await interaction.followup.send(response)

    def _format_bracket(self, bracket: List[Dict], is_team: bool = False, tournament_format: str = "single_elimination") -> str:
        """Format bracket for display.

        For double elimination, groups matches by winners/losers/grand finals.
        """
        if not bracket:
            return "No matches generated."

        def format_participant(p):
            if p == "BYE":
                return "BYE"
            if p == "TBD":
                return "TBD"
            if is_team:
                return str(p)
            return f"<@{p}>"

        def format_match(match):
            status = "✅" if match.get("completed") else ("⏸️" if match.get("skipped") else "⏳")
            p1 = format_participant(match["participant1"])
            p2 = format_participant(match["participant2"])
            round_num = match.get("round", "?")

            if match.get("winner"):
                winner = format_participant(match["winner"])
                score = match.get("score", "")
                return f"{status} R{round_num} M{match['id']}: ~~{p1}~~ vs ~~{p2}~~ → {winner} ({score})"
            else:
                return f"{status} R{round_num} M{match['id']}: {p1} vs {p2}"

        lines = []

        if tournament_format == "double_elimination":
            # Group by bracket type
            winners = [m for m in bracket if m.get("bracket_type") == "winners"]
            losers = [m for m in bracket if m.get("bracket_type") == "losers"]
            grand_finals = [m for m in bracket if m.get("bracket_type") == "grand_finals"]

            # Winners bracket
            if winners:
                lines.append("**🏆 Winners Bracket**")
                # Only show playable matches (not TBD vs TBD)
                playable = [m for m in winners if m["participant1"] != "TBD" or m["participant2"] != "TBD"]
                for match in playable[:8]:
                    lines.append(format_match(match))
                if len(playable) > 8:
                    lines.append(f"  ... and {len(playable) - 8} more")

            # Losers bracket
            if losers:
                lines.append("\n**💀 Losers Bracket**")
                playable = [m for m in losers if m["participant1"] != "TBD" or m["participant2"] != "TBD"]
                for match in playable[:6]:
                    lines.append(format_match(match))
                if len(playable) > 6:
                    lines.append(f"  ... and {len(playable) - 6} more")

            # Grand Finals
            if grand_finals:
                lines.append("\n**🎖️ Grand Finals**")
                for match in grand_finals:
                    if match.get("skipped"):
                        lines.append(f"⏸️ GF{match.get('gf_match', '?')}: Skipped (no bracket reset needed)")
                    else:
                        lines.append(format_match(match))

        else:
            # Single elimination or round robin - simple list
            playable = [m for m in bracket if m["participant1"] != "TBD" or m.get("completed")]
            for match in playable[:10]:
                lines.append(format_match(match))
            if len(playable) > 10:
                lines.append(f"... and {len(playable) - 10} more matches")

        return "\n".join(lines) if lines else "No matches to display."

    async def cancel_tournament(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        tournament: Dict[str, Any]
    ) -> None:
        """Cancel a tournament."""
        if tournament.get("cancelled"):
            await interaction.response.send_message("Tournament is already cancelled.", ephemeral=True)
            return

        tournament["cancelled"] = True

        # Delete on Challonge if configured
        challonge_id = tournament.get("challonge_id")
        if challonge_id and self._challonge_ready:
            await self._challonge_delete_tournament(challonge_id)
            log.info(f"Deleted Challonge tournament {challonge_id}")

        async with self.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        # Update the original message
        try:
            channel = interaction.guild.get_channel(tournament["channel_id"])
            if channel:
                message = await channel.fetch_message(tournament["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.red()

                for i, field in enumerate(embed.fields):
                    if field.name == "Status":
                        embed.set_field_at(i, name="Status", value="❌ Cancelled", inline=False)
                        break

                await message.edit(embed=embed, view=None)
        except Exception as e:
            log.error(f"Error updating cancelled tournament message: {e}")

        # Remove from active views
        if tournament_id in self.active_views:
            self.active_views[tournament_id].stop()
            del self.active_views[tournament_id]

        await interaction.response.send_message(
            f"❌ Tournament **{tournament['name']}** has been cancelled.",
            ephemeral=False
        )

    async def show_bracket(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        tournament: Dict[str, Any]
    ) -> None:
        """Show current bracket state."""
        if not tournament.get("started"):
            await interaction.response.send_message(
                "Tournament hasn't started yet. No bracket to show.",
                ephemeral=True
            )
            return

        bracket = tournament.get("bracket", [])
        if not bracket:
            await interaction.response.send_message("No bracket data available.", ephemeral=True)
            return

        is_team = tournament["type"] == "team"

        embed = discord.Embed(
            title=f"🏆 {tournament['name']} - Bracket",
            color=discord.Color.blue()
        )

        # Group by round
        rounds = {}
        for match in bracket:
            round_num = match.get("round", 1)
            if round_num not in rounds:
                rounds[round_num] = []
            rounds[round_num].append(match)

        for round_num in sorted(rounds.keys()):
            matches = rounds[round_num]
            round_text = []

            for match in matches:
                p1 = match["participant1"]
                p2 = match["participant2"]

                if is_team:
                    p1_str = p1 if p1 != "BYE" else "BYE"
                    p2_str = p2 if p2 != "BYE" else "BYE"
                else:
                    p1_str = f"<@{p1}>" if p1 != "BYE" else "BYE"
                    p2_str = f"<@{p2}>" if p2 != "BYE" else "BYE"

                if match.get("completed"):
                    winner = match.get("winner")
                    if is_team:
                        winner_str = winner
                    else:
                        winner_str = f"<@{winner}>" if winner != "BYE" else "BYE"
                    round_text.append(f"~~{p1_str}~~ vs ~~{p2_str}~~ → **{winner_str}**")
                else:
                    round_text.append(f"{p1_str} vs {p2_str}")

            bracket_type = matches[0].get("bracket_type", "winners")
            round_name = f"Round {round_num}"
            if bracket_type == "losers":
                round_name += " (Losers)"

            embed.add_field(
                name=round_name,
                value="\n".join(round_text) if round_text else "No matches",
                inline=False
            )

        # Add Challonge image if available
        challonge_url = tournament.get("challonge_url")
        challonge_image = tournament.get("challonge_image")

        if challonge_image:
            # Add timestamp to bust cache and get latest bracket
            cache_bust = f"?t={int(datetime.now(timezone.utc).timestamp())}"
            embed.set_image(url=challonge_image + cache_bust)

        # Create view with Challonge link button
        view = BracketView(challonge_url)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def report_match(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        tournament: Dict[str, Any]
    ) -> None:
        """Report a match result."""
        if not tournament.get("started"):
            await interaction.response.send_message("Tournament hasn't started yet.", ephemeral=True)
            return

        bracket = tournament.get("bracket", [])
        pending_matches = [m for m in bracket if not m.get("completed")]

        if not pending_matches:
            await interaction.response.send_message(
                "All matches have been completed! 🏆",
                ephemeral=True
            )
            return

        # Show match select view
        view = MatchReportSelectView(self, tournament_id, pending_matches, tournament["type"] == "team")
        await interaction.response.send_message(
            "Select a match to report:",
            view=view,
            ephemeral=True
        )


# ==================== UI VIEWS AND MODALS ====================


class BracketView(discord.ui.View):
    """View for bracket command with Challonge link button."""

    def __init__(self, challonge_url: Optional[str] = None):
        super().__init__(timeout=300)  # 5 minute timeout

        if challonge_url:
            self.add_item(discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="View on Challonge",
                url=challonge_url,
                emoji="📊"
            ))


class SeedListModal(discord.ui.Modal, title="Configure Seed List"):
    """Modal for configuring rank-to-ELO mappings for a game."""

    ranks = discord.ui.TextInput(
        label="Ranks (one per line, highest first)",
        placeholder="Radiant\nImmortal\nAscendant\nDiamond\nPlatinum\nGold",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    elos = discord.ui.TextInput(
        label="ELO values (one per line, matching ranks)",
        placeholder="1500\n1400\n1300\n1200\n1100\n1000",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=200,
    )

    def __init__(self, cog: "ShadyTourneys", game: str, current_list: dict):
        super().__init__()
        self.cog = cog
        self.game = game

        # Pre-fill with current values if exists
        if current_list:
            self.ranks.default = "\n".join(current_list.keys())
            self.elos.default = "\n".join(str(v) for v in current_list.values())

    async def on_submit(self, interaction: discord.Interaction):
        rank_lines = [r.strip() for r in self.ranks.value.strip().split("\n") if r.strip()]
        elo_lines = [e.strip() for e in self.elos.value.strip().split("\n") if e.strip()]

        if len(rank_lines) != len(elo_lines):
            await interaction.response.send_message(
                f"❌ Mismatch: {len(rank_lines)} ranks but {len(elo_lines)} ELO values.\n"
                f"Each rank needs a corresponding ELO value.",
                ephemeral=True
            )
            return

        # Parse ELO values
        seed_list = {}
        for rank, elo_str in zip(rank_lines, elo_lines):
            try:
                elo = int(elo_str)
                if elo < 0 or elo > 5000:
                    await interaction.response.send_message(
                        f"❌ Invalid ELO for **{rank}**: {elo_str}\nELO must be between 0-5000.",
                        ephemeral=True
                    )
                    return
                seed_list[rank] = elo
            except ValueError:
                await interaction.response.send_message(
                    f"❌ Invalid ELO value for **{rank}**: `{elo_str}`\nMust be a number.",
                    ephemeral=True
                )
                return

        # Save the seed list
        async with self.cog.config.guild(interaction.guild).seed_lists() as seed_lists:
            seed_lists[self.game] = seed_list

        # Format for display
        display = "\n".join(f"• **{rank}**: {elo}" for rank, elo in seed_list.items())

        await interaction.response.send_message(
            f"✅ Seed list configured for **{self.game.title()}**:\n\n{display}",
            ephemeral=True
        )


class SeedingView(discord.ui.View):
    """View for seeding players with game and rank selection."""

    def __init__(self, cog: "ShadyTourneys", supported_games: list, seed_lists: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.seed_lists = seed_lists
        self.selected_game = None
        self.selected_rank = None
        self.selected_elo = None
        self.selected_players = []

        # Add game select
        game_options = [
            discord.SelectOption(label=g.title(), value=g)
            for g in supported_games[:25]
        ]
        if game_options:
            self.game_select = discord.ui.Select(
                placeholder="1️⃣ Select game...",
                options=game_options,
                row=0
            )
            self.game_select.callback = self.game_callback
            self.add_item(self.game_select)

    async def game_callback(self, interaction: discord.Interaction):
        self.selected_game = self.game_select.values[0]

        # Get seed list for this game (or default)
        if self.selected_game in self.seed_lists:
            seed_list = self.seed_lists[self.selected_game]
        else:
            seed_list = self.cog.default_seed_list

        # Update or add rank select
        rank_options = [
            discord.SelectOption(label=f"{rank} ({elo} ELO)", value=rank)
            for rank, elo in seed_list.items()
        ]

        # Remove old rank select if exists
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select) and item.placeholder and "rank" in item.placeholder.lower():
                self.remove_item(item)

        self.rank_select = discord.ui.Select(
            placeholder="2️⃣ Select rank...",
            options=rank_options[:25],
            row=1
        )
        self.rank_select.callback = self.rank_callback
        self.add_item(self.rank_select)

        await interaction.response.edit_message(
            content=f"**Game:** {self.selected_game.title()}\n\nNow select a rank:",
            view=self
        )

    async def rank_callback(self, interaction: discord.Interaction):
        self.selected_rank = self.rank_select.values[0]

        # Get ELO for selected rank
        if self.selected_game in self.seed_lists:
            self.selected_elo = self.seed_lists[self.selected_game].get(self.selected_rank, 1000)
        else:
            self.selected_elo = self.cog.default_seed_list.get(self.selected_rank, 1000)

        # Remove old player select if exists
        for item in self.children[:]:
            if isinstance(item, discord.ui.UserSelect):
                self.remove_item(item)

        self.player_select = discord.ui.UserSelect(
            placeholder="3️⃣ Select players to seed...",
            min_values=1,
            max_values=10,
            row=2
        )
        self.player_select.callback = self.player_callback
        self.add_item(self.player_select)

        await interaction.response.edit_message(
            content=f"**Game:** {self.selected_game.title()}\n**Rank:** {self.selected_rank} ({self.selected_elo} ELO)\n\nSelect players to seed:",
            view=self
        )

    async def player_callback(self, interaction: discord.Interaction):
        self.selected_players = self.player_select.values

        # Show confirmation with submit button
        player_mentions = ", ".join(p.mention for p in self.selected_players)

        # Remove old submit button if exists
        for item in self.children[:]:
            if isinstance(item, discord.ui.Button) and item.label == "Seed Players":
                self.remove_item(item)

        self.submit_button = discord.ui.Button(
            label="Seed Players",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=3
        )
        self.submit_button.callback = self.submit_callback
        self.add_item(self.submit_button)

        await interaction.response.edit_message(
            content=f"**Game:** {self.selected_game.title()}\n**Rank:** {self.selected_rank} ({self.selected_elo} ELO)\n**Players:** {player_mentions}\n\nClick **Seed Players** to confirm.",
            view=self
        )

    async def submit_callback(self, interaction: discord.Interaction):
        if not self.selected_game or not self.selected_rank or not self.selected_players:
            await interaction.response.send_message("Please complete all selections.", ephemeral=True)
            return

        game_lower = self.selected_game.lower()
        seeded = []
        skipped = []

        async with self.cog.config.guild(interaction.guild).player_stats() as player_stats:
            if game_lower not in player_stats:
                player_stats[game_lower] = {}

            for player in self.selected_players:
                user_id_str = str(player.id)

                # Check if player already has match history
                if user_id_str in player_stats[game_lower]:
                    existing = player_stats[game_lower][user_id_str]
                    if existing.get("matches_played", 0) > 0:
                        skipped.append(f"{player.display_name} (has {existing.get('matches_played', 0)} matches)")
                        continue

                # Set initial stats
                player_stats[game_lower][user_id_str] = {
                    "elo": self.selected_elo,
                    "wins": 0,
                    "losses": 0,
                    "matches_played": 0,
                    "seeded_rank": self.selected_rank,
                }
                seeded.append(player.display_name)

        response = f"✅ **Seeding Complete for {self.selected_game.title()}**\n\n"
        response += f"**Rank:** {self.selected_rank} ({self.selected_elo} ELO)\n\n"

        if seeded:
            response += f"**Seeded ({len(seeded)}):** {', '.join(seeded)}\n"
        if skipped:
            response += f"\n⚠️ **Skipped (have match history):** {', '.join(skipped)}"

        await interaction.response.edit_message(content=response, view=None)
        self.stop()


class TournamentCreateModal(discord.ui.Modal, title="Create Tournament"):
    """Modal for creating a new tournament."""

    tournament_name = discord.ui.TextInput(
        label="Tournament Name",
        placeholder="e.g., Marvel Rivals Championship",
        required=True,
        max_length=100,
    )
    game = discord.ui.TextInput(
        label="Game/Category",
        placeholder="e.g., Marvel Rivals, Rocket League",
        required=True,
        max_length=50,
    )
    team_size = discord.ui.TextInput(
        label="Team Size (for team tournaments, 2-10)",
        placeholder="Leave blank for solo, or enter 3, 5, 6, etc.",
        required=False,
        max_length=2,
    )
    prize_pool = discord.ui.TextInput(
        label="Prize Pool (optional)",
        placeholder="e.g., $100, Steam gift card, bragging rights",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: ShadyTourneys, tournament_type: str):
        super().__init__()
        self.cog = cog
        self.tournament_type = tournament_type

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be run in a text channel!",
                ephemeral=True
            )
            return

        team_size_val = None

        if self.tournament_type == "team":
            try:
                team_size_val = int(str(self.team_size.value).strip()) if self.team_size.value else 3
                if team_size_val < 2 or team_size_val > 10:
                    await interaction.response.send_message(
                        "Team size must be between 2 and 10.",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    "Invalid team size. Please enter a number between 2 and 10.",
                    ephemeral=True
                )
                return

        default_format = await self.cog.config.guild(interaction.guild).default_format()

        await self.cog.create_tournament(
            interaction,
            channel,
            str(self.tournament_name.value),
            str(self.game.value),
            self.tournament_type,
            team_size_val,
            format=default_format,
            prize_pool=str(self.prize_pool.value) if self.prize_pool.value else None,
        )


class ProposeTimeModal(discord.ui.Modal, title="Propose Match Time"):
    """Modal for proposing a match time."""

    def __init__(self, cog, match_id: int, tournament_id: str):
        super().__init__()
        self.cog = cog
        self.match_id = match_id
        self.tournament_id = tournament_id

    proposed_time = discord.ui.TextInput(
        label="Proposed Time (UTC)",
        placeholder="e.g., 2024-01-15 20:00 or 'Saturday 8pm UTC'",
        style=discord.TextStyle.short,
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Handle time proposal submission."""
        time_str = str(self.proposed_time.value).strip()

        # Try to parse the time
        parsed_time = None
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%m/%d/%Y %H:%M",
        ]

        for fmt in formats:
            try:
                parsed_time = datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

        if not parsed_time:
            await interaction.response.send_message(
                f"❌ Could not parse time: `{time_str}`\n"
                "Please use format: `YYYY-MM-DD HH:MM` (in UTC)\n"
                "Example: `2024-01-15 20:00`",
                ephemeral=True
            )
            return

        # Check if time is in the future
        if parsed_time <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ Proposed time must be in the future.",
                ephemeral=True
            )
            return

        # Update the match with proposed time
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament = tournaments.get(self.tournament_id)

        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        bracket = tournament.get("bracket", [])
        match = None
        for m in bracket:
            if m["id"] == self.match_id:
                match = m
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        # Update match
        match["proposed_time"] = parsed_time.isoformat()
        match["proposed_by"] = interaction.user.id
        match["time_accepts"] = []  # Clear any previous accepts when new time is proposed

        # Log to history
        history = match.setdefault("history", [])
        history.append({
            "event": "proposed",
            "by": interaction.user.id,
            "at": datetime.now(timezone.utc).isoformat(),
            "time": parsed_time.isoformat()
        })

        async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[self.tournament_id] = tournament

        await interaction.response.send_message(
            f"✅ **Time proposed:** <t:{int(parsed_time.timestamp())}:F>\n"
            f"Waiting for opponent to accept or counter-propose.",
            ephemeral=False
        )


class MatchSchedulingView(discord.ui.View):
    """View for match scheduling with propose/accept/counter buttons."""

    def __init__(self, cog, match_id: int, tournament: Dict[str, Any]):
        super().__init__(timeout=None)
        self.cog = cog
        self.match_id = match_id
        self.tournament = tournament
        self.tournament_id = f"{tournament.get('message_id', 'unknown')}"

        # Find tournament ID from the cog's active tournaments
        # This is a bit hacky but necessary for persistent views

    @discord.ui.button(label="📅 Propose Time", style=discord.ButtonStyle.blurple, custom_id="match_propose")
    async def propose_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open modal to propose a time."""
        # Find tournament ID
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament_id = None
        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    break
            if tournament_id:
                break

        if not tournament_id:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        modal = ProposeTimeModal(self.cog, self.match_id, tournament_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🎮 Check In", style=discord.ButtonStyle.green, custom_id="match_checkin")
    async def checkin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Check in for the scheduled match."""
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()

        # Find tournament and match
        tournament_id = None
        tournament = None
        match = None

        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    tournament = t
                    match = m
                    break
            if match:
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        scheduled_time = match.get("scheduled_time")
        if not scheduled_time:
            await interaction.response.send_message(
                "❌ No scheduled time yet. Schedule the match first!",
                ephemeral=True
            )
            return

        # Check if user is a participant
        p1, p2 = match["participant1"], match["participant2"]
        is_participant = False
        participant_entity = None

        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            for team_name in [p1, p2]:
                team = teams_data.get(team_name, {})
                if interaction.user.id in team.get("players", []):
                    is_participant = True
                    participant_entity = team_name
                    break
        else:
            if interaction.user.id == p1:
                is_participant = True
                participant_entity = p1
            elif interaction.user.id == p2:
                is_participant = True
                participant_entity = p2

        if not is_participant:
            await interaction.response.send_message(
                "❌ You are not a participant in this match.",
                ephemeral=True
            )
            return

        # Check if check-in window is open (15 min before to 15 min after scheduled time)
        scheduled = datetime.fromisoformat(scheduled_time)
        now = datetime.now(timezone.utc)
        checkin_opens = scheduled - timedelta(minutes=15)
        checkin_closes = scheduled + timedelta(minutes=15)

        if now < checkin_opens:
            await interaction.response.send_message(
                f"❌ Check-in opens <t:{int(checkin_opens.timestamp())}:R>.\n"
                f"Come back closer to your scheduled time!",
                ephemeral=True
            )
            return

        if now > checkin_closes:
            await interaction.response.send_message(
                "❌ Check-in window has closed. Use **Claim Forfeit** if opponent didn't show.",
                ephemeral=True
            )
            return

        # Check in the user
        checked_in = match.get("checked_in", [])
        if interaction.user.id in checked_in:
            await interaction.response.send_message(
                "✅ You're already checked in! Waiting for opponent...",
                ephemeral=True
            )
            return

        checked_in.append(interaction.user.id)
        match["checked_in"] = checked_in

        # Log to history
        history = match.setdefault("history", [])
        history.append({
            "event": "checkin",
            "by": interaction.user.id,
            "at": datetime.now(timezone.utc).isoformat()
        })

        async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        # Notify in thread
        if tournament["type"] == "team":
            entity_name = participant_entity
        else:
            entity_name = interaction.user.display_name

        await interaction.response.send_message(
            f"✅ **{entity_name}** has checked in!\n"
            f"{'Both players are ready! Play your match and report the result.' if len(checked_in) >= 2 else 'Waiting for opponent to check in...'}",
            ephemeral=False
        )

    @discord.ui.button(label="✅ Accept Time", style=discord.ButtonStyle.green, custom_id="match_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Accept the proposed time."""
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()

        # Find tournament and match
        tournament_id = None
        tournament = None
        match = None

        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    tournament = t
                    match = m
                    break
            if match:
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        proposed_time = match.get("proposed_time")
        proposed_by = match.get("proposed_by")

        if not proposed_time:
            await interaction.response.send_message(
                "❌ No time has been proposed yet. Use **Propose Time** first.",
                ephemeral=True
            )
            return

        # Check if user is a participant
        p1, p2 = match["participant1"], match["participant2"]
        is_participant = False
        user_team = None

        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            for team_name in [p1, p2]:
                team = teams_data.get(team_name, {})
                if interaction.user.id in team.get("players", []):
                    is_participant = True
                    user_team = team_name
                    break
        else:
            is_participant = interaction.user.id in [p1, p2]

        if not is_participant:
            await interaction.response.send_message(
                "❌ You are not a participant in this match.",
                ephemeral=True
            )
            return

        # For solo matches, proposer can't accept their own proposal
        if tournament["type"] != "team" and proposed_by == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot accept your own proposal. Wait for your opponent.",
                ephemeral=True
            )
            return

        # Track who has accepted the proposed time
        time_accepts = match.setdefault("time_accepts", [])

        if interaction.user.id in time_accepts:
            await interaction.response.send_message(
                "✅ You've already accepted this time. Waiting for others...",
                ephemeral=True
            )
            return

        time_accepts.append(interaction.user.id)

        # Log to history
        history = match.setdefault("history", [])
        history.append({
            "event": "accepted",
            "by": interaction.user.id,
            "at": datetime.now(timezone.utc).isoformat(),
            "time": proposed_time
        })

        # For team matches, check if ALL players from BOTH teams have accepted
        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            team1 = teams_data.get(p1, {})
            team2 = teams_data.get(p2, {})
            all_players = team1.get("players", []) + team2.get("players", [])

            accepted_count = len([p for p in all_players if p in time_accepts])
            total_players = len(all_players)

            if accepted_count < total_players:
                # Not everyone has accepted yet
                async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
                    all_tournaments[tournament_id] = tournament

                # Show who still needs to accept
                pending = [f"<@{p}>" for p in all_players if p not in time_accepts]
                parsed = datetime.fromisoformat(proposed_time)

                await interaction.response.send_message(
                    f"✅ **{interaction.user.display_name}** accepted the proposed time!\n"
                    f"📅 Time: <t:{int(parsed.timestamp())}:F>\n\n"
                    f"**Progress:** {accepted_count}/{total_players} players accepted\n"
                    f"**Waiting on:** {', '.join(pending[:10])}{'...' if len(pending) > 10 else ''}",
                    ephemeral=False
                )
                return

        # All required accepts received - schedule the match
        match["scheduled_time"] = proposed_time
        match["proposed_time"] = None
        match["proposed_by"] = None
        match["time_accepts"] = []  # Clear for potential reschedule

        async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        scheduled = datetime.fromisoformat(proposed_time)
        await interaction.response.send_message(
            f"✅ **Match scheduled!**\n"
            f"📅 Time: <t:{int(scheduled.timestamp())}:F>\n"
            f"⏰ Reminders will be sent before the match.\n\n"
            f"Good luck! 🎮",
            ephemeral=False
        )

    @discord.ui.button(label="🔄 Counter-Propose", style=discord.ButtonStyle.gray, custom_id="match_counter")
    async def counter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Counter-propose a different time."""
        # Same as propose - just opens the modal
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament_id = None
        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    break
            if tournament_id:
                break

        if not tournament_id:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        modal = ProposeTimeModal(self.cog, self.match_id, tournament_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📊 Report Score", style=discord.ButtonStyle.green, custom_id="match_report", row=1)
    async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Report match result (players only)."""
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()

        # Find tournament and match
        tournament_id = None
        tournament = None
        match = None

        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    tournament = t
                    match = m
                    break
            if match:
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        if match.get("completed"):
            await interaction.response.send_message("This match has already been reported.", ephemeral=True)
            return

        # Check if user is a participant
        p1, p2 = match["participant1"], match["participant2"]
        is_participant = False

        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            for team_name in [p1, p2]:
                team = teams_data.get(team_name, {})
                if interaction.user.id in team.get("players", []):
                    is_participant = True
                    break
        else:
            is_participant = interaction.user.id in [p1, p2]

        if not is_participant:
            await interaction.response.send_message(
                "❌ Only match participants can report the score.",
                ephemeral=True
            )
            return

        # Open score modal
        modal = MatchScoreModal(
            self.cog,
            tournament_id,
            self.match_id,
            p1,
            p2,
            tournament["type"] == "team"
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⚠️ Claim Forfeit", style=discord.ButtonStyle.red, custom_id="match_forfeit", row=1)
    async def forfeit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Claim forfeit if opponent no-showed."""
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()

        # Find tournament and match
        tournament_id = None
        tournament = None
        match = None

        for tid, t in tournaments.items():
            bracket = t.get("bracket", [])
            for m in bracket:
                if m.get("id") == self.match_id:
                    tournament_id = tid
                    tournament = t
                    match = m
                    break
            if match:
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        scheduled_time = match.get("scheduled_time")

        if not scheduled_time:
            await interaction.response.send_message(
                "❌ Cannot claim forfeit - no scheduled time agreed upon.",
                ephemeral=True
            )
            return

        scheduled = datetime.fromisoformat(scheduled_time)
        now = datetime.now(timezone.utc)

        # Allow forfeit claim 15 minutes after scheduled time
        grace_period = timedelta(minutes=15)
        if now < scheduled + grace_period:
            await interaction.response.send_message(
                f"❌ Cannot claim forfeit yet. Wait until <t:{int((scheduled + grace_period).timestamp())}:R> "
                f"(15 min grace period after scheduled time).",
                ephemeral=True
            )
            return

        # Determine who is claiming and who forfeits
        p1, p2 = match["participant1"], match["participant2"]
        claimant = None
        forfeiter = None
        claimant_user_id = None

        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            for team_name in [p1, p2]:
                team = teams_data.get(team_name, {})
                if interaction.user.id in team.get("players", []):
                    claimant = team_name
                    claimant_user_id = interaction.user.id
                    forfeiter = p2 if team_name == p1 else p1
                    break
        else:
            if interaction.user.id == p1:
                claimant = p1
                claimant_user_id = p1
                forfeiter = p2
            elif interaction.user.id == p2:
                claimant = p2
                claimant_user_id = p2
                forfeiter = p1

        if not claimant:
            await interaction.response.send_message(
                "❌ You are not a participant in this match.",
                ephemeral=True
            )
            return

        # Check if claimant checked in
        checked_in = match.get("checked_in", [])
        if claimant_user_id not in checked_in:
            await interaction.response.send_message(
                "❌ You must check in before you can claim a forfeit.\n"
                "Use **Check In** to confirm you're ready to play.",
                ephemeral=True
            )
            return

        # Check if opponent checked in (if they did, can't claim forfeit)
        opponent_checked_in = False
        if tournament["type"] == "team":
            teams_data = tournament.get("teams", {})
            forfeiter_team = teams_data.get(forfeiter, {})
            for player_id in forfeiter_team.get("players", []):
                if player_id in checked_in:
                    opponent_checked_in = True
                    break
        else:
            if forfeiter in checked_in:
                opponent_checked_in = True

        if opponent_checked_in:
            await interaction.response.send_message(
                "❌ Your opponent has checked in! You cannot claim forfeit.\n"
                "Play the match and report the result.",
                ephemeral=True
            )
            return

        # Mark forfeit
        match["winner"] = claimant
        match["loser"] = forfeiter
        match["completed"] = True
        match["forfeit"] = True
        match["forfeit_reason"] = "no_show"
        match["score"] = "FF"

        # Log to history
        history = match.setdefault("history", [])
        history.append({
            "event": "forfeit",
            "by": interaction.user.id,
            "at": datetime.now(timezone.utc).isoformat(),
            "winner": claimant,
            "forfeiter": forfeiter
        })

        # Advance the bracket
        ready_matches = []
        try:
            ready_matches = await self.cog._advance_bracket(
                interaction.guild, tournament, match
            )
        except Exception as e:
            log.error(f"Failed to advance bracket after forfeit: {e}")

        # Check if tournament is complete
        is_complete, champion = self.cog._is_tournament_complete(tournament)
        if is_complete:
            tournament["completed"] = True
            tournament["champion"] = champion

        async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        response_msg = (
            f"⚠️ **Forfeit claimed!**\n"
            f"Winner: **{claimant}** (opponent no-show)\n\n"
            f"The bracket has been updated."
        )

        if ready_matches:
            response_msg += f"\n\n📋 **{len(ready_matches)} new match(es)** are now ready!"

        await interaction.response.send_message(response_msg, ephemeral=False)

        # Delete the match thread after forfeit
        thread_id = match.get("scheduling_thread")
        if thread_id:
            try:
                thread = interaction.guild.get_thread(thread_id)
                if thread:
                    await asyncio.sleep(10)
                    await thread.delete()
            except Exception as e:
                log.error(f"Failed to delete match thread after forfeit: {e}")

        # Check for tournament completion
        if tournament.get("completed"):
            if tournament["type"] == "team":
                champion_str = f"**{champion}**"
            else:
                champion_str = f"<@{champion}>"

            channel = interaction.guild.get_channel(tournament["channel_id"])
            if channel:
                try:
                    challonge_url = tournament.get("challonge_url")
                    bracket_link = f"\n\n📊 **Final Bracket:** {challonge_url}" if challonge_url else ""

                    await channel.send(
                        f"🏆 **{tournament['name']}** has concluded!\n\n"
                        f"**Champion:** {champion_str}"
                        f"{bracket_link}\n\n"
                        "Congratulations! 🎉"
                    )
                except Exception as e:
                    log.error(f"Error announcing winner: {e}")


class SoloSignupView(discord.ui.View):
    """View for solo tournament signups."""

    def __init__(self, cog: ShadyTourneys, tournament_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.tournament_id = tournament_id

    @discord.ui.button(label="🎮 Join Tournament", style=discord.ButtonStyle.green, custom_id="tourney_join_solo")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_solo_join(interaction, self.tournament_id)

    @discord.ui.button(label="🚪 Leave", style=discord.ButtonStyle.red, custom_id="tourney_leave_solo")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_leave(interaction, self.tournament_id)


class TeamSignupView(discord.ui.View):
    """View for team tournament signups."""

    def __init__(self, cog: ShadyTourneys, tournament_id: str, team_size: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.tournament_id = tournament_id
        self.team_size = team_size

    @discord.ui.button(label="⭐ Create Team", style=discord.ButtonStyle.blurple, custom_id="tourney_create_team")
    async def create_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user is already in tournament
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        if self.tournament_id not in tournaments:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        tournament = tournaments[self.tournament_id]
        if tournament.get("started"):
            await interaction.response.send_message("This tournament has already started.", ephemeral=True)
            return

        # Check if already in a team
        for team_name, team_data in tournament.get("teams", {}).items():
            if interaction.user.id in team_data["players"]:
                await interaction.response.send_message(
                    f"You're already on team **{team_name}**. Leave first to create a new team.",
                    ephemeral=True
                )
                return

        # Check if in pickup pool
        if interaction.user.id in tournament.get("pickup_players", []):
            await interaction.response.send_message(
                "You're in the pickup pool. Leave first to create a team.",
                ephemeral=True
            )
            return

        modal = TeamCreateModal(self.cog, self.tournament_id, self.team_size)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="👥 Join a Team", style=discord.ButtonStyle.green, custom_id="tourney_join_team")
    async def join_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        if self.tournament_id not in tournaments:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        tournament = tournaments[self.tournament_id]
        if tournament.get("started"):
            await interaction.response.send_message("This tournament has already started.", ephemeral=True)
            return

        # Check if already in a team
        for team_name, team_data in tournament.get("teams", {}).items():
            if interaction.user.id in team_data["players"]:
                await interaction.response.send_message(
                    f"You're already on team **{team_name}**.",
                    ephemeral=True
                )
                return

        # Check if in pickup pool
        if interaction.user.id in tournament.get("pickup_players", []):
            await interaction.response.send_message(
                "You're in the pickup pool. Leave first to join a specific team.",
                ephemeral=True
            )
            return

        # Get available teams (not full)
        available_teams = []
        for team_name, team_data in tournament.get("teams", {}).items():
            if len(team_data["players"]) < self.team_size:
                available_teams.append(team_name)

        if not available_teams:
            await interaction.response.send_message(
                "No teams with open slots. Create a new team or join as pickup!",
                ephemeral=True
            )
            return

        view = JoinTeamSelectView(self.cog, self.tournament_id, available_teams)
        await interaction.response.send_message(
            "Select a team to join:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="🎲 Join as Pickup", style=discord.ButtonStyle.gray, custom_id="tourney_join_pickup")
    async def join_pickup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_pickup_join(interaction, self.tournament_id)

    @discord.ui.button(label="🚪 Leave", style=discord.ButtonStyle.red, custom_id="tourney_leave_team")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_leave(interaction, self.tournament_id)


class TeamCreateModal(discord.ui.Modal, title="Create Team"):
    """Modal for creating a new team."""

    team_name = discord.ui.TextInput(
        label="Team Name",
        placeholder="e.g., The Destroyers",
        required=True,
        max_length=50,
    )

    def __init__(self, cog: ShadyTourneys, tournament_id: str, team_size: int):
        super().__init__()
        self.cog = cog
        self.tournament_id = tournament_id
        self.team_size = team_size

    async def on_submit(self, interaction: discord.Interaction):
        team_name = str(self.team_name.value).strip()

        async with self.cog.config.guild(interaction.guild).tournaments() as tournaments:
            if self.tournament_id not in tournaments:
                await interaction.response.send_message("Tournament not found.", ephemeral=True)
                return

            tournament = tournaments[self.tournament_id]

            # Check if team name already exists
            if team_name.lower() in [t.lower() for t in tournament.get("teams", {}).keys()]:
                await interaction.response.send_message(
                    f"A team named **{team_name}** already exists. Choose a different name.",
                    ephemeral=True
                )
                return

            # Create the team with user as captain
            if "teams" not in tournament:
                tournament["teams"] = {}

            tournament["teams"][team_name] = {
                "captain": interaction.user.id,
                "players": [interaction.user.id]
            }

        # Update the embed
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        await self.cog.update_tournament_embed(interaction.guild, self.tournament_id, tournaments[self.tournament_id])

        await interaction.response.send_message(
            f"✅ Team **{team_name}** created! You are the captain.\n"
            f"Team size: 1/{self.team_size}",
            ephemeral=True
        )


class JoinTeamSelectView(discord.ui.View):
    """View for selecting which team to join."""

    def __init__(self, cog: ShadyTourneys, tournament_id: str, available_teams: List[str]):
        super().__init__(timeout=60)
        self.cog = cog
        self.tournament_id = tournament_id

        # Add select menu with available teams
        options = [
            discord.SelectOption(label=team_name, value=team_name)
            for team_name in available_teams[:25]  # Discord limit
        ]

        select = discord.ui.Select(
            placeholder="Select a team...",
            options=options,
            custom_id="tourney_team_select"
        )
        select.callback = self.team_selected
        self.add_item(select)

    async def team_selected(self, interaction: discord.Interaction):
        team_name = interaction.data["values"][0]

        async with self.cog.config.guild(interaction.guild).tournaments() as tournaments:
            if self.tournament_id not in tournaments:
                await interaction.response.send_message("Tournament not found.", ephemeral=True)
                return

            tournament = tournaments[self.tournament_id]

            if team_name not in tournament.get("teams", {}):
                await interaction.response.send_message("That team no longer exists.", ephemeral=True)
                return

            team_data = tournament["teams"][team_name]
            team_size = tournament["team_size"]

            if len(team_data["players"]) >= team_size:
                await interaction.response.send_message(
                    f"Team **{team_name}** is now full. Try another team.",
                    ephemeral=True
                )
                return

            # Add player to team
            team_data["players"].append(interaction.user.id)

        # Update the embed
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        await self.cog.update_tournament_embed(interaction.guild, self.tournament_id, tournaments[self.tournament_id])

        team_data = tournaments[self.tournament_id]["teams"][team_name]
        team_size = tournaments[self.tournament_id]["team_size"]

        await interaction.response.send_message(
            f"✅ You joined team **{team_name}**!\n"
            f"Team size: {len(team_data['players'])}/{team_size}",
            ephemeral=True
        )
        self.stop()


class FormatSelectView(discord.ui.View):
    """View for selecting tournament format."""

    def __init__(self, cog: ShadyTourneys):
        super().__init__(timeout=60)
        self.cog = cog

    @discord.ui.select(
        placeholder="Select format...",
        options=[
            discord.SelectOption(label="Single Elimination", value="single_elimination", description="Standard bracket, one loss eliminates"),
            discord.SelectOption(label="Double Elimination", value="double_elimination", description="Two losses to eliminate"),
            discord.SelectOption(label="Round Robin", value="round_robin", description="Everyone plays everyone"),
        ]
    )
    async def format_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        format_value = select.values[0]
        await self.cog.config.guild(interaction.guild).default_format.set(format_value)
        await interaction.response.send_message(
            f"✅ Default format set to **{format_value.replace('_', ' ').title()}**.",
            ephemeral=True
        )
        self.stop()


class MatchReportSelectView(discord.ui.View):
    """View for selecting which match to report."""

    def __init__(self, cog: ShadyTourneys, tournament_id: str, pending_matches: List[Dict], is_team: bool):
        super().__init__(timeout=120)
        self.cog = cog
        self.tournament_id = tournament_id
        self.pending_matches = pending_matches
        self.is_team = is_team

        # Build options
        options = []
        for match in pending_matches[:25]:
            p1 = match["participant1"]
            p2 = match["participant2"]

            if is_team:
                label = f"Match {match['id']}: {p1} vs {p2}"
            else:
                # For solo, we can't show mentions in select options
                label = f"Match {match['id']}"

            options.append(discord.SelectOption(
                label=label[:100],
                value=str(match["id"]),
                description=f"Round {match.get('round', 1)}"
            ))

        select = discord.ui.Select(
            placeholder="Select a match...",
            options=options,
            custom_id="match_select"
        )
        select.callback = self.match_selected
        self.add_item(select)

    async def match_selected(self, interaction: discord.Interaction):
        match_id = int(interaction.data["values"][0])

        # Find the match
        match = next((m for m in self.pending_matches if m["id"] == match_id), None)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        # Show score input modal
        modal = MatchScoreModal(
            self.cog,
            self.tournament_id,
            match_id,
            match["participant1"],
            match["participant2"],
            self.is_team
        )
        await interaction.response.send_modal(modal)
        self.stop()


class MatchScoreModal(discord.ui.Modal, title="Report Match Result"):
    """Modal for reporting match score and winner."""

    score = discord.ui.TextInput(
        label="Score (e.g., 2-1, 3-0)",
        placeholder="Winner score - Loser score",
        style=discord.TextStyle.short,
        required=True,
        max_length=10,
    )

    def __init__(
        self,
        cog: ShadyTourneys,
        tournament_id: str,
        match_id: int,
        participant1: Any,
        participant2: Any,
        is_team: bool
    ):
        super().__init__()
        self.cog = cog
        self.tournament_id = tournament_id
        self.match_id = match_id
        self.participant1 = participant1
        self.participant2 = participant2
        self.is_team = is_team

    async def on_submit(self, interaction: discord.Interaction):
        score_str = str(self.score.value).strip()

        # Validate score format
        if not re.match(r'^\d+-\d+$', score_str):
            await interaction.response.send_message(
                "❌ Invalid score format. Use `X-Y` (e.g., `2-1`, `3-0`).",
                ephemeral=True
            )
            return

        # Parse scores
        try:
            parts = score_str.split('-')
            winner_score = int(parts[0])
            loser_score = int(parts[1])
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid score format. Use numbers (e.g., `2-1`).",
                ephemeral=True
            )
            return

        if winner_score <= loser_score:
            await interaction.response.send_message(
                "❌ Winner score must be higher than loser score.",
                ephemeral=True
            )
            return

        # Show winner selection with score context
        view = MatchWinnerSelectView(
            self.cog,
            self.tournament_id,
            self.match_id,
            self.participant1,
            self.participant2,
            self.is_team,
            score_str
        )

        p1 = self.participant1
        p2 = self.participant2

        if self.is_team:
            match_text = f"**{p1}** vs **{p2}**"
        else:
            match_text = f"<@{p1}> vs <@{p2}>"

        await interaction.response.send_message(
            f"Select the winner of Match {self.match_id}:\n{match_text}\n**Score:** {score_str}",
            view=view,
            ephemeral=True
        )
        self.stop()


class MatchWinnerSelectView(discord.ui.View):
    """View for selecting match winner."""

    def __init__(
        self,
        cog: ShadyTourneys,
        tournament_id: str,
        match_id: int,
        participant1: Any,
        participant2: Any,
        is_team: bool,
        score: str = "1-0"
    ):
        super().__init__(timeout=60)
        self.cog = cog
        self.tournament_id = tournament_id
        self.match_id = match_id
        self.participant1 = participant1
        self.participant2 = participant2
        self.is_team = is_team
        self.score = score

        # Build options
        options = []

        if is_team:
            p1_label = str(participant1)
            p2_label = str(participant2)
        else:
            p1_label = f"Player 1 (ID: {participant1})"
            p2_label = f"Player 2 (ID: {participant2})"

        if participant1 != "BYE":
            options.append(discord.SelectOption(
                label=p1_label[:100],
                value=str(participant1)
            ))

        if participant2 != "BYE":
            options.append(discord.SelectOption(
                label=p2_label[:100],
                value=str(participant2)
            ))

        select = discord.ui.Select(
            placeholder="Select the winner...",
            options=options,
            custom_id="winner_select"
        )
        select.callback = self.winner_selected
        self.add_item(select)

    async def winner_selected(self, interaction: discord.Interaction):
        winner_value = interaction.data["values"][0]

        # Convert back to int if it's a user ID
        if not self.is_team:
            winner = int(winner_value)
            loser = self.participant2 if winner == self.participant1 else self.participant1
        else:
            winner = winner_value
            loser = self.participant2 if winner == self.participant1 else self.participant1

        async with self.cog.config.guild(interaction.guild).tournaments() as tournaments:
            if self.tournament_id not in tournaments:
                await interaction.response.send_message("Tournament not found.", ephemeral=True)
                return

            tournament = tournaments[self.tournament_id]
            bracket = tournament.get("bracket", [])
            game = tournament.get("game", "Unknown")

            # Find and update the match
            match_found = None
            for match in bracket:
                if match["id"] == self.match_id:
                    match["winner"] = winner
                    match["score"] = self.score
                    match["completed"] = True

                    # Log to history
                    history = match.setdefault("history", [])
                    history.append({
                        "event": "reported",
                        "by": interaction.user.id,
                        "at": datetime.now(timezone.utc).isoformat(),
                        "score": self.score,
                        "winner": winner,
                        "loser": loser
                    })

                    match_found = match
                    break

            if not match_found:
                await interaction.response.send_message("Match not found.", ephemeral=True)
                return

            tournament["bracket"] = bracket

            # Update ELO for solo tournaments (silently - ELO is admin-only)
            if not self.is_team:
                try:
                    await self.cog._update_player_stats(
                        interaction.guild.id, game, winner, loser, self.score
                    )
                except Exception as e:
                    log.error(f"Failed to update ELO: {e}")
            else:
                # For team tournaments, update ELO for all team members
                teams_data = tournament.get("teams", {})
                winner_team = teams_data.get(winner, {})
                loser_team = teams_data.get(loser, {})

                if winner_team and loser_team:
                    try:
                        for winner_player in winner_team.get("players", []):
                            for loser_player in loser_team.get("players", []):
                                # Each player gets a small ELO adjustment (1/team_size)
                                team_size = len(winner_team.get("players", [1]))
                                await self.cog._update_player_stats(
                                    interaction.guild.id, game, winner_player, loser_player, self.score
                                )
                    except Exception as e:
                        log.error(f"Failed to update team ELO: {e}")

            # Store loser in match
            match_found["loser"] = loser

            # Advance the bracket (populate next matches with winner, move loser in double elim)
            ready_matches = []
            try:
                ready_matches = await self.cog._advance_bracket(
                    interaction.guild, tournament, match_found
                )
            except Exception as e:
                log.error(f"Failed to advance bracket: {e}")

            # Check if tournament is complete using proper logic
            is_complete, champion = self.cog._is_tournament_complete(tournament)

            if is_complete:
                tournament["completed"] = True
                tournament["champion"] = champion

            # Report to Challonge if available
            challonge_id = tournament.get("challonge_id")
            if challonge_id and self.cog._challonge_ready:
                try:
                    await self.cog._challonge_report_match(
                        challonge_id,
                        match_found.get("challonge_match_id"),
                        winner,
                        self.score
                    )
                except Exception as e:
                    log.error(f"Failed to report to Challonge: {e}")

        if self.is_team:
            winner_str = f"**{winner}**"
            loser_str = f"**{loser}**"
        else:
            winner_str = f"<@{winner}>"
            loser_str = f"<@{loser}>"

        # Build response message
        response_msg = (
            f"✅ Match {self.match_id} result recorded!\n"
            f"Winner: {winner_str}\n"
            f"Score: **{self.score}**"
        )

        # Add info about next matches if any were created
        if ready_matches:
            response_msg += f"\n\n📋 **{len(ready_matches)} new match(es)** are now ready to be played!"

        await interaction.response.send_message(response_msg, ephemeral=True)

        # Delete the match thread if it exists
        thread_id = match_found.get("scheduling_thread")
        if thread_id:
            try:
                thread = interaction.guild.get_thread(thread_id)
                if thread:
                    # Send final message before deleting
                    try:
                        await thread.send(
                            f"✅ **Match Complete!**\n"
                            f"Winner: {winner_str}\n"
                            f"Score: **{self.score}**\n\n"
                            f"*This thread will be deleted in 30 seconds...*"
                        )
                        await asyncio.sleep(30)
                    except Exception:
                        pass
                    await thread.delete()
            except Exception as e:
                log.error(f"Failed to delete match thread: {e}")

        # Check for tournament completion and announce
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament = tournaments[self.tournament_id]

        if tournament.get("completed"):
            # Get the actual champion from the tournament
            champion = tournament.get("champion", winner)
            if self.is_team:
                champion_str = f"**{champion}**"
            else:
                champion_str = f"<@{champion}>"

            # Announce winner
            channel = interaction.guild.get_channel(tournament["channel_id"])
            if channel:
                try:
                    # Build announcement based on format
                    tournament_format = tournament.get("format", "single_elimination")
                    if tournament_format == "double_elimination":
                        format_text = "Double Elimination"
                    elif tournament_format == "round_robin":
                        format_text = "Round Robin"
                    else:
                        format_text = "Single Elimination"

                    challonge_url = tournament.get("challonge_url")
                    bracket_link = f"\n\n📊 **Final Bracket:** {challonge_url}" if challonge_url else ""

                    await channel.send(
                        f"🏆 **{tournament['name']}** has concluded!\n\n"
                        f"**Format:** {format_text}\n"
                        f"**Champion:** {champion_str}\n"
                        f"{bracket_link}\n\n"
                        "Congratulations! 🎉"
                    )
                except Exception as e:
                    log.error(f"Error announcing winner: {e}")

        self.stop()


class MatchHistorySelectView(discord.ui.View):
    """View for selecting a match to view its history."""

    def __init__(self, cog, tournament_id: str, tournament: Dict[str, Any], options: List[discord.SelectOption]):
        super().__init__(timeout=120)
        self.cog = cog
        self.tournament_id = tournament_id
        self.tournament = tournament

        select = discord.ui.Select(
            placeholder="Select a match...",
            options=options,
            custom_id="history_match_select"
        )
        select.callback = self.match_selected
        self.add_item(select)

    async def match_selected(self, interaction: discord.Interaction):
        match_id = int(interaction.data["values"][0])

        # Get fresh tournament data
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament = tournaments.get(self.tournament_id)

        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return

        # Find the match
        bracket = tournament.get("bracket", [])
        match = None
        for m in bracket:
            if m["id"] == match_id:
                match = m
                break

        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        # Build history embed
        p1 = match["participant1"]
        p2 = match["participant2"]

        if tournament["type"] == "team":
            title = f"Match History: {p1} vs {p2}"
        else:
            title = f"Match History: <@{p1}> vs <@{p2}>"

        embed = discord.Embed(
            title=title,
            color=discord.Color.blue()
        )

        # Match info
        embed.add_field(
            name="Status",
            value="✅ Completed" if match.get("completed") else "⏳ Pending",
            inline=True
        )
        if match.get("score"):
            embed.add_field(name="Score", value=match["score"], inline=True)
        if match.get("winner"):
            winner = match["winner"]
            if tournament["type"] == "team":
                embed.add_field(name="Winner", value=winner, inline=True)
            else:
                embed.add_field(name="Winner", value=f"<@{winner}>", inline=True)

        # History log
        history = match.get("history", [])
        if history:
            history_text = ""
            for entry in history:
                event = entry.get("event", "unknown")
                by = entry.get("by")
                at = entry.get("at", "?")

                # Format timestamp
                try:
                    ts = datetime.fromisoformat(at)
                    time_str = f"<t:{int(ts.timestamp())}:R>"
                except:
                    time_str = at[:19] if len(at) > 19 else at

                if event == "proposed":
                    prop_time = entry.get("time", "?")
                    try:
                        prop_ts = datetime.fromisoformat(prop_time)
                        prop_str = f"<t:{int(prop_ts.timestamp())}:F>"
                    except:
                        prop_str = prop_time
                    history_text += f"📅 **Proposed** by <@{by}> {time_str}\n   → Time: {prop_str}\n"
                elif event == "accepted":
                    history_text += f"✅ **Accepted** by <@{by}> {time_str}\n"
                elif event == "checkin":
                    history_text += f"🎮 **Checked in** by <@{by}> {time_str}\n"
                elif event == "reported":
                    score = entry.get("score", "?")
                    winner = entry.get("winner", "?")
                    history_text += f"📊 **Reported** by <@{by}> {time_str}\n   → Score: {score}, Winner: {winner}\n"
                elif event == "forfeit":
                    history_text += f"⚠️ **Forfeit** claimed by <@{by}> {time_str}\n"
                else:
                    history_text += f"❓ **{event}** by <@{by}> {time_str}\n"

            embed.add_field(
                name="Event History",
                value=history_text[:1024] if history_text else "No events recorded",
                inline=False
            )
        else:
            embed.add_field(
                name="Event History",
                value="No events recorded yet.",
                inline=False
            )

        embed.set_footer(text=f"Match ID: {match_id} | Tournament: {tournament.get('name', 'Unknown')}")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.stop()


async def setup(bot: Red) -> None:
    """Load the ShadyTourneys cog."""
    await bot.add_cog(ShadyTourneys(bot))
