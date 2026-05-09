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
            embed = discord.Embed(
                title=f"⚔️ {thread_name}",
                description=(
                    f"**Tournament:** {tournament['name']}\n"
                    f"**Round:** {match.get('round', 1)}\n"
                    f"**Deadline:** <t:{int(datetime.fromisoformat(match['deadline']).timestamp())}:F>\n\n"
                    "**How to schedule:**\n"
                    "1️⃣ Use **Propose Time** to suggest when to play\n"
                    "2️⃣ Opponent uses **Accept** or **Counter-Propose**\n"
                    "3️⃣ Both players **Check In** within ±15 min of match time\n"
                    "4️⃣ Play your match and report the result\n\n"
                    "⚠️ If opponent doesn't check in, you can **Claim Forfeit** after 15 min"
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
        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/owner always authorized
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
        """Generate single elimination bracket."""
        matches = []
        match_id = 1
        round_deadline = datetime.now(timezone.utc) + timedelta(hours=round_deadline_hours)

        for i in range(0, len(entities) - 1, 2):
            seed1 = i + 1  # 1-indexed seeds
            seed2 = i + 2
            matches.append({
                "id": match_id,
                "round": 1,
                "bracket_type": "winners",
                "participant1": entities[i],
                "participant2": entities[i + 1],
                "seed1": seed1,
                "seed2": seed2,
                "winner": None,
                "score": None,
                "completed": False,
                # Scheduling fields
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

        # Handle odd number of participants (BYE)
        if len(entities) % 2 != 0:
            matches.append({
                "id": match_id,
                "round": 1,
                "bracket_type": "winners",
                "participant1": entities[-1],
                "participant2": "BYE",
                "seed1": len(entities),
                "seed2": None,
                "winner": entities[-1],
                "score": "BYE",
                "completed": True,
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

        return matches

    def _generate_double_elimination(
        self, entities: List, round_deadline_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Generate double elimination bracket (winners + losers bracket)."""
        # Start with winners bracket (same as single elimination first round)
        matches = self._generate_single_elimination(entities, round_deadline_hours)

        # Mark as winners bracket (already done in single elim)
        # Losers bracket will be generated as matches complete
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
    @app_commands.describe(setting="Setting to configure", role="Role for add/remove role actions")
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
        app_commands.Choice(name="Set Default Format", value="format"),
    ])
    async def tourneyset(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None
    ):
        """Configure tournament settings."""
        if not interaction.user.guild_permissions.administrator:
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
        games = list(player_stats.keys())

        if not current:
            return [app_commands.Choice(name=g.title(), value=g) for g in games[:25]]

        filtered = [g for g in games if current.lower() in g.lower()]
        return [app_commands.Choice(name=g.title(), value=g) for g in filtered[:25]]

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
        bracket_text = self._format_bracket(bracket, is_team)

        response = f"🏆 **{tournament['name']}** has started!\n\n"
        response += f"**Seeding applied** (Manual → ELO → Random)\n\n"
        response += f"**First Round Matches:**\n{bracket_text}\n\n"

        if challonge_url:
            response += f"📊 **Bracket:** {challonge_url}\n\n"

        if scheduling_channel:
            response += f"📅 Match scheduling threads created in <#{scheduling_channel}>\n"
            response += "Participants: Use the threads to schedule your matches!\n\n"

        response += f"Use `/tourneymanage report` to report match results."

        await interaction.followup.send(response)

    def _format_bracket(self, bracket: List[Dict], is_team: bool = False) -> str:
        """Format bracket for display."""
        lines = []
        for match in bracket[:10]:  # Limit display
            if match.get("completed"):
                status = "✅"
            else:
                status = "⏳"

            p1 = match["participant1"]
            p2 = match["participant2"]

            if is_team:
                p1_str = p1 if p1 != "BYE" else "BYE"
                p2_str = p2 if p2 != "BYE" else "BYE"
            else:
                p1_str = f"<@{p1}>" if p1 != "BYE" else "BYE"
                p2_str = f"<@{p2}>" if p2 != "BYE" else "BYE"

            lines.append(f"{status} Match {match['match_number']}: {p1_str} vs {p2_str}")

        if len(bracket) > 10:
            lines.append(f"... and {len(bracket) - 10} more matches")

        return "\n".join(lines) if lines else "No matches generated."

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
            # Embed the bracket image directly
            embed.set_image(url=challonge_image)
            embed.add_field(
                name="📊 Interactive Bracket",
                value=f"[Open on Challonge]({challonge_url})",
                inline=False
            )
        elif challonge_url:
            embed.add_field(
                name="📊 Full Bracket",
                value=f"[View on Challonge]({challonge_url})",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        if proposed_by == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot accept your own proposal. Wait for your opponent.",
                ephemeral=True
            )
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
                "❌ You are not a participant in this match.",
                ephemeral=True
            )
            return

        # Accept the time
        match["scheduled_time"] = proposed_time
        match["proposed_time"] = None
        match["proposed_by"] = None

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

    @discord.ui.button(label="⚠️ Claim Forfeit", style=discord.ButtonStyle.red, custom_id="match_forfeit")
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
        match["completed"] = True
        match["forfeit"] = True
        match["forfeit_reason"] = "no_show"
        match["score"] = "FF"

        async with self.cog.config.guild(interaction.guild).tournaments() as all_tournaments:
            all_tournaments[tournament_id] = tournament

        await interaction.response.send_message(
            f"⚠️ **Forfeit claimed!**\n"
            f"Winner: **{claimant}** (opponent no-show)\n\n"
            f"The bracket has been updated.",
            ephemeral=False
        )


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

            # Check if tournament is complete
            pending = [m for m in bracket if not m.get("completed")]

            if not pending:
                tournament["completed"] = True

            # Report to Challonge if available
            challonge_id = tournament.get("challonge_id")
            if challonge_id and self.cog._challonge_ready:
                # Find challonge match ID (would need to track this)
                # For now, we'll just sync the result
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
        else:
            winner_str = f"<@{winner}>"

        await interaction.response.send_message(
            f"✅ Match {self.match_id} result recorded!\n"
            f"Winner: {winner_str}\n"
            f"Score: **{self.score}**",
            ephemeral=True
        )

        # Check for tournament completion
        tournaments = await self.cog.config.guild(interaction.guild).tournaments()
        tournament = tournaments[self.tournament_id]

        if tournament.get("completed"):
            # Announce winner
            channel = interaction.guild.get_channel(tournament["channel_id"])
            if channel:
                try:
                    await channel.send(
                        f"🏆 **{tournament['name']}** has concluded!\n\n"
                        f"**Champion:** {winner_str}\n\n"
                        "Congratulations! 🎉"
                    )
                except Exception as e:
                    log.error(f"Error announcing winner: {e}")

        self.stop()


async def setup(bot: Red) -> None:
    """Load the ShadyTourneys cog."""
    await bot.add_cog(ShadyTourneys(bot))
