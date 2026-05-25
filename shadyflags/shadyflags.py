"""
ShadyFlags - Temporary warning/flag system with account age auto-flagging.

Features:
- Manual flag creation with expiry dates
- Auto-flag new accounts based on age thresholds
- Flag review queue UI
- Flag statistics/metrics
- Configurable moderator roles
- Mod log channel integration
- ML feature extraction on member join
- ML-based risk scoring for new joins
"""

import discord
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadyflags")

# Try to import sklearn for ML, fallback to rule-based scoring if not available
try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    log.info("sklearn not available, using rule-based risk scoring")


# ===== ML FEATURE EXTRACTION =====

@dataclass
class JoinFeatures:
    """Features extracted from a member join event for ML analysis.

    IMPORTANT: Features are only stored for SUSPECTS (flagged users).
    Labels are only applied when user is CONFIRMED bad (banned for spam/bot).
    We never label users as "good" - dormant bots can be inactive for months/years.
    """
    user_id: int
    guild_id: int
    timestamp: str  # ISO format

    # Account metadata
    account_age_hours: float
    account_age_bucket: str  # "0-24h", "1-7d", "7-30d", "30-90d", "90d+"
    has_avatar: bool
    has_custom_avatar: bool  # vs default Discord avatar
    has_banner: bool

    # Username analysis
    username: str
    username_length: int
    username_entropy: float
    username_has_numbers: bool
    username_number_suffix: bool  # ends with numbers like "user1234"
    username_random_pattern: bool  # detected random string
    display_name_set: bool
    display_name_matches_username: bool

    # Server context (filled in separately)
    server_joins_last_hour: int = 0
    server_joins_last_day: int = 0

    # Network/Alt information (from ShadyAlts integration)
    is_in_alt_network: bool = False
    alt_network_size: int = 0
    alt_network_toxicity: float = 0.0  # % of network in known_bad
    has_toxic_alts: bool = False  # Any alt in known_bad

    # Outcome label - ONLY set to True when CONFIRMED bad (banned for spam/bot)
    # Never set to False - we can't know if someone is truly "good" (dormant bots exist)
    was_actioned: Optional[bool] = None  # True = confirmed bad, None = unknown
    action_type: Optional[str] = None  # "ban" only (for spam/bot reasons)


def calculate_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string. Higher = more random."""
    if not text:
        return 0.0
    text = text.lower()
    freq = Counter(text)
    length = len(text)
    return -sum((c/length) * math.log2(c/length) for c in freq.values())


def detect_random_pattern(username: str) -> bool:
    """Detect if username appears randomly generated."""
    if not username or len(username) < 4:
        return False

    # High entropy suggests randomness
    entropy = calculate_entropy(username)
    if entropy > 3.5 and len(username) >= 8:
        return True

    # Check for excessive mixed case without real words
    if len(username) >= 6:
        # Mixed case ratio
        upper = sum(1 for c in username if c.isupper())
        lower = sum(1 for c in username if c.islower())
        if upper > 0 and lower > 0:
            ratio = min(upper, lower) / max(upper, lower)
            # If roughly equal upper/lower and long, likely random
            if ratio > 0.3 and entropy > 3.0:
                return True

    # Pattern like "xK7mN2pQ" - alternating letters and numbers mixed randomly
    has_alternating = bool(re.search(r'[a-zA-Z]\d[a-zA-Z]|\d[a-zA-Z]\d', username))
    if has_alternating and len(username) >= 6:
        return True

    # Check consonant-heavy strings (no vowels = likely random)
    alpha_only = ''.join(c for c in username.lower() if c.isalpha())
    if len(alpha_only) >= 6:
        vowels = sum(1 for c in alpha_only if c in 'aeiou')
        vowel_ratio = vowels / len(alpha_only)
        if vowel_ratio < 0.15:  # Less than 15% vowels
            return True

    return False


def get_account_age_bucket(age_hours: float) -> str:
    """Categorize account age into buckets."""
    if age_hours < 24:
        return "0-24h"
    elif age_hours < 24 * 7:
        return "1-7d"
    elif age_hours < 24 * 30:
        return "7-30d"
    elif age_hours < 24 * 90:
        return "30-90d"
    else:
        return "90d+"


def extract_join_features(member: discord.Member, guild: discord.Guild) -> JoinFeatures:
    """Extract ML features from a member join event."""
    now = datetime.now(timezone.utc)
    created_at = member.created_at.replace(tzinfo=timezone.utc)
    account_age = now - created_at
    account_age_hours = account_age.total_seconds() / 3600

    username = member.name
    display_name = member.display_name

    return JoinFeatures(
        user_id=member.id,
        guild_id=guild.id,
        timestamp=now.isoformat(),

        # Account metadata
        account_age_hours=round(account_age_hours, 2),
        account_age_bucket=get_account_age_bucket(account_age_hours),
        has_avatar=member.avatar is not None,
        has_custom_avatar=member.avatar is not None and not str(member.avatar.url).startswith("https://cdn.discordapp.com/embed/avatars"),
        has_banner=getattr(member, 'banner', None) is not None,

        # Username analysis
        username=username,
        username_length=len(username),
        username_entropy=round(calculate_entropy(username), 3),
        username_has_numbers=any(c.isdigit() for c in username),
        username_number_suffix=bool(re.search(r'\d{2,}$', username)),  # ends with 2+ numbers
        username_random_pattern=detect_random_pattern(username),
        display_name_set=display_name != username,
        display_name_matches_username=display_name.lower() == username.lower(),
    )


# ===== ML RISK SCORING =====

@dataclass
class RiskScore:
    """Result of ML risk prediction."""
    score: float  # 0.0 - 1.0 (probability of being a bad actor)
    confidence: float  # 0.0 - 1.0 (how confident the model is)
    top_factors: List[Tuple[str, float]]  # [(factor_name, contribution)]
    method: str = "rule_based"  # "rule_based" or "ml_model"


# Default feature weights for rule-based scoring
# These can be tuned over time based on labeled data
DEFAULT_FEATURE_WEIGHTS = {
    "account_age_very_new": 0.25,      # < 24 hours
    "account_age_new": 0.15,           # < 7 days
    "no_avatar": 0.10,
    "no_custom_avatar": 0.05,
    "high_entropy_username": 0.15,     # entropy > 3.5
    "random_pattern_username": 0.20,
    "number_suffix": 0.08,
    "has_toxic_alts": 0.30,
    "high_network_toxicity": 0.25,     # > 50%
    "medium_network_toxicity": 0.15,   # > 25%
    "raid_indicator": 0.20,            # 5+ joins in last hour
}


class RiskModel:
    """ML risk scoring model with sklearn or rule-based fallback."""

    FEATURE_NAMES = [
        "account_age_hours",
        "has_avatar",
        "has_custom_avatar",
        "username_entropy",
        "username_has_numbers",
        "username_number_suffix",
        "username_random_pattern",
        "alt_network_toxicity",
        "has_toxic_alts",
        "server_joins_last_hour",
    ]

    def __init__(self, data_path: Path = None):
        self.data_path = data_path
        self.is_trained = False
        self.training_examples = 0
        self.accuracy = 0.0
        self.weights = DEFAULT_FEATURE_WEIGHTS.copy()

        if SKLEARN_AVAILABLE:
            self.model = LogisticRegression(max_iter=1000)
            self.scaler = StandardScaler()
            self.feature_importances = {}
        else:
            self.model = None
            self.scaler = None
            self.feature_importances = {}

    def features_to_vector(self, f: Dict[str, Any]) -> list:
        """Convert feature dict to numeric vector for ML."""
        return [
            f.get("account_age_hours", 0) / 720,  # Normalize to 30 days
            1 if f.get("has_avatar") else 0,
            1 if f.get("has_custom_avatar") else 0,
            f.get("username_entropy", 0) / 5.0,  # Normalize entropy
            1 if f.get("username_has_numbers") else 0,
            1 if f.get("username_number_suffix") else 0,
            1 if f.get("username_random_pattern") else 0,
            f.get("alt_network_toxicity", 0),
            1 if f.get("has_toxic_alts") else 0,
            min(f.get("server_joins_last_hour", 0) / 10, 1.0),  # Normalize joins
        ]

    def rule_based_score(self, features: Dict[str, Any]) -> RiskScore:
        """Calculate risk score using rule-based weights."""
        score = 0.0
        factors = []

        account_age = features.get("account_age_hours", 1000)

        # Account age
        if account_age < 24:
            contribution = self.weights["account_age_very_new"]
            score += contribution
            factors.append(("Very new account (<24h)", contribution))
        elif account_age < 24 * 7:
            contribution = self.weights["account_age_new"]
            score += contribution
            factors.append(("New account (<7d)", contribution))

        # Avatar
        if not features.get("has_avatar"):
            contribution = self.weights["no_avatar"]
            score += contribution
            factors.append(("No avatar", contribution))
        elif not features.get("has_custom_avatar"):
            contribution = self.weights["no_custom_avatar"]
            score += contribution
            factors.append(("Default avatar", contribution))

        # Username analysis
        entropy = features.get("username_entropy", 0)
        if entropy > 3.5:
            contribution = self.weights["high_entropy_username"]
            score += contribution
            factors.append(("High entropy username", contribution))

        if features.get("username_random_pattern"):
            contribution = self.weights["random_pattern_username"]
            score += contribution
            factors.append(("Random-looking username", contribution))

        if features.get("username_number_suffix"):
            contribution = self.weights["number_suffix"]
            score += contribution
            factors.append(("Username ends in numbers", contribution))

        # Alt network
        if features.get("has_toxic_alts"):
            contribution = self.weights["has_toxic_alts"]
            score += contribution
            factors.append(("Has toxic alts", contribution))
        else:
            toxicity = features.get("alt_network_toxicity", 0)
            if toxicity > 0.5:
                contribution = self.weights["high_network_toxicity"]
                score += contribution
                factors.append((f"High network toxicity ({toxicity*100:.0f}%)", contribution))
            elif toxicity > 0.25:
                contribution = self.weights["medium_network_toxicity"]
                score += contribution
                factors.append((f"Medium network toxicity ({toxicity*100:.0f}%)", contribution))

        # Raid detection
        joins_last_hour = features.get("server_joins_last_hour", 0)
        if joins_last_hour >= 5:
            contribution = self.weights["raid_indicator"]
            score += contribution
            factors.append((f"High join rate ({joins_last_hour}/hour)", contribution))

        # Cap at 1.0
        score = min(score, 1.0)

        # Sort factors by contribution
        factors.sort(key=lambda x: -x[1])

        # Confidence is based on how many factors contributed
        confidence = min(len(factors) / 5, 1.0)  # More factors = more confident

        return RiskScore(
            score=round(score, 3),
            confidence=round(confidence, 3),
            top_factors=factors[:3],
            method="rule_based"
        )

    def predict(self, features: Dict[str, Any]) -> RiskScore:
        """Predict risk score for features."""
        if not self.is_trained or not SKLEARN_AVAILABLE:
            return self.rule_based_score(features)

        try:
            X = np.array([self.features_to_vector(features)])
            X_scaled = self.scaler.transform(X)

            # Get probability of being a bad actor (class 1)
            proba = self.model.predict_proba(X_scaled)[0]
            if len(proba) > 1:
                score = proba[1]  # Probability of positive class
            else:
                score = proba[0]

            # Get feature contributions
            if hasattr(self.model, 'coef_'):
                contributions = []
                coefs = self.model.coef_[0]
                for i, (name, coef) in enumerate(zip(self.FEATURE_NAMES, coefs)):
                    if X_scaled[0][i] != 0:
                        contrib = abs(coef * X_scaled[0][i])
                        if contrib > 0.01:
                            readable_name = name.replace("_", " ").title()
                            contributions.append((readable_name, round(contrib, 3)))
                contributions.sort(key=lambda x: -x[1])
            else:
                contributions = []

            # Confidence based on distance from 0.5
            confidence = abs(score - 0.5) * 2

            return RiskScore(
                score=round(float(score), 3),
                confidence=round(float(confidence), 3),
                top_factors=contributions[:3],
                method="ml_model"
            )
        except Exception as e:
            log.error(f"ML prediction failed, falling back to rules: {e}")
            return self.rule_based_score(features)

    def train(self, features_list: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """Train the model on CONFIRMED bad actor data only.

        We only have positive examples (confirmed bad actors).
        We learn what bad actors look like and score similarity to that profile.
        """
        # Filter to CONFIRMED bad actors only (was_actioned=True)
        confirmed_bad = [f for f in features_list if f.get("was_actioned") is True]

        if len(confirmed_bad) < 20:
            return False, f"Need at least 20 confirmed bad actors (have {len(confirmed_bad)})"

        # Calculate feature prevalence among confirmed bad actors
        # This tells us which features are common in bad actors
        total = len(confirmed_bad)

        # Count how often each feature appears in bad actors
        feature_counts = {
            "no_avatar": sum(1 for f in confirmed_bad if not f.get("has_avatar", True)),
            "no_custom_avatar": sum(1 for f in confirmed_bad if not f.get("has_custom_avatar", True)),
            "high_entropy": sum(1 for f in confirmed_bad if f.get("username_entropy", 0) > 3.5),
            "random_pattern": sum(1 for f in confirmed_bad if f.get("username_random_pattern", False)),
            "number_suffix": sum(1 for f in confirmed_bad if f.get("username_number_suffix", False)),
            "very_new": sum(1 for f in confirmed_bad if f.get("account_age_hours", 1000) < 24),
            "new": sum(1 for f in confirmed_bad if 24 <= f.get("account_age_hours", 1000) < 168),
        }

        # Update weights based on prevalence (higher % = higher weight)
        for feature, count in feature_counts.items():
            prevalence = count / total
            if feature == "no_avatar":
                self.weights["no_avatar"] = max(0.05, prevalence * 0.3)
            elif feature == "no_custom_avatar":
                self.weights["no_custom_avatar"] = max(0.02, prevalence * 0.15)
            elif feature == "high_entropy":
                self.weights["high_entropy_username"] = max(0.05, prevalence * 0.25)
            elif feature == "random_pattern":
                self.weights["random_pattern_username"] = max(0.1, prevalence * 0.35)
            elif feature == "number_suffix":
                self.weights["number_suffix"] = max(0.03, prevalence * 0.15)
            elif feature == "very_new":
                self.weights["account_age_very_new"] = max(0.1, prevalence * 0.4)
            elif feature == "new":
                self.weights["account_age_new"] = max(0.05, prevalence * 0.25)

        self.is_trained = True
        self.training_examples = len(confirmed_bad)
        self.feature_importances = {k: v for k, v in feature_counts.items()}

        # Still try sklearn if available for probability calibration
        if not SKLEARN_AVAILABLE:
            return True, f"Learned from {len(confirmed_bad)} confirmed bad actors (rule-based)"

        # For sklearn: use confirmed bad as positives, unlabeled as weak negatives
        unlabeled = [f for f in features_list if f.get("was_actioned") is None]

        if len(unlabeled) < 20:
            return True, f"Learned from {len(confirmed_bad)} confirmed bad actors (rule-based, not enough unlabeled for ML)"

        # Use random sample of unlabeled as pseudo-negatives
        import random
        pseudo_negatives = random.sample(unlabeled, min(len(unlabeled), len(confirmed_bad) * 2))

        try:
            # Combine confirmed bad (label=1) with pseudo-negatives (label=0)
            training_data = confirmed_bad + pseudo_negatives
            X = np.array([self.features_to_vector(f) for f in training_data])
            y = np.array([1] * len(confirmed_bad) + [0] * len(pseudo_negatives))

            # Fit scaler
            X_scaled = self.scaler.fit_transform(X)

            # Train model
            self.model.fit(X_scaled, y)

            # Calculate accuracy (on training data)
            predictions = self.model.predict(X_scaled)
            self.accuracy = sum(predictions == y) / len(y)

            # Store feature importances from sklearn
            if hasattr(self.model, 'coef_'):
                for i, name in enumerate(self.FEATURE_NAMES):
                    self.feature_importances[name] = float(self.model.coef_[0][i])

            return True, f"Trained on {len(confirmed_bad)} confirmed bad + {len(pseudo_negatives)} unlabeled (accuracy: {self.accuracy*100:.1f}%)"

        except Exception as e:
            log.error(f"Training failed: {e}", exc_info=True)
            return False, f"Training failed: {str(e)}"

    def save(self, path: Path):
        """Save model state to disk."""
        state = {
            "is_trained": self.is_trained,
            "training_examples": self.training_examples,
            "accuracy": self.accuracy,
            "weights": self.weights,
            "feature_importances": self.feature_importances,
        }

        if SKLEARN_AVAILABLE and self.is_trained:
            # Save sklearn model separately
            import pickle
            model_path = path / "risk_model.pkl"
            with open(model_path, 'wb') as f:
                pickle.dump({
                    "model": self.model,
                    "scaler": self.scaler,
                }, f)

        # Save state as JSON
        state_path = path / "risk_model_state.json"
        with open(state_path, 'w') as f:
            json.dump(state, f, indent=2)

    def load(self, path: Path) -> bool:
        """Load model state from disk."""
        state_path = path / "risk_model_state.json"
        if not state_path.exists():
            return False

        try:
            with open(state_path, 'r') as f:
                state = json.load(f)

            self.is_trained = state.get("is_trained", False)
            self.training_examples = state.get("training_examples", 0)
            self.accuracy = state.get("accuracy", 0.0)
            self.weights = state.get("weights", DEFAULT_FEATURE_WEIGHTS.copy())
            self.feature_importances = state.get("feature_importances", {})

            if SKLEARN_AVAILABLE and self.is_trained:
                import pickle
                model_path = path / "risk_model.pkl"
                if model_path.exists():
                    with open(model_path, 'rb') as f:
                        saved = pickle.load(f)
                        self.model = saved["model"]
                        self.scaler = saved["scaler"]
                else:
                    self.is_trained = False

            return True

        except Exception as e:
            log.error(f"Failed to load model: {e}")
            return False


# ===== KNOWN NETWORK / BAD ACTOR DETECTION =====

BAD_ACTOR_KEYWORDS = [
    # Bot/automated
    "bot", "selfbot", "self bot", "spambot", "spam bot", "automod",
    # Spam
    "spam", "spammer", "spamming", "mass dm", "advertising", "advert", "promo",
    # Scam
    "scam", "scammer", "scamming", "phishing", "nitro scam", "free nitro",
    # Raid/attack
    "raid", "raider", "raiding", "nuke", "nuker", "nuking",
    # Malicious
    "malware", "grabber", "token", "compromised", "hacked", "harmful", "threat",
    # Suspicious (matches "Suspicious or spam account")
    "suspicious", "suspect",
    # HoneyPot detection
    "honeypot", "honey pot",
    # Defender bot auto-bans
    "defender", "quickaction",
]


@dataclass
class KnownBadActor:
    """Record of a confirmed bad actor in the network."""
    user_id: int
    added_at: str  # ISO format
    source: str  # "mod_action", "alt_of_confirmed", "manual", "history_scan"
    reason: str
    mod_action_type: Optional[str] = None  # "ban", "kick", etc.
    linked_alts: Optional[List[int]] = None
    username: Optional[str] = None  # Username at time of action
    actioned_by: Optional[int] = None  # Moderator who took action


def is_bad_actor_action(reason: str) -> bool:
    """Check if a mod action reason indicates a bad actor."""
    if not reason:
        return False
    reason_lower = reason.lower()
    return any(kw in reason_lower for kw in BAD_ACTOR_KEYWORDS)


def parse_user_id_from_text(text: str) -> Optional[int]:
    """Extract a user ID from text (supports mentions and raw IDs)."""
    # Try mention format: <@123456789> or <@!123456789>
    mention_match = re.search(r'<@!?(\d+)>', text)
    if mention_match:
        return int(mention_match.group(1))

    # Try raw ID
    id_match = re.search(r'\b(\d{17,20})\b', text)
    if id_match:
        return int(id_match.group(1))

    return None


# Config identifier for RedBot's Config system
CONFIG_IDENTIFIER = 260288776360820738


class AddFlagModal(discord.ui.Modal, title="Add Flag to User"):
    """Modal for adding flags by user ID."""

    user_id = discord.ui.TextInput(
        label="Discord User ID",
        placeholder="Enter the user's Discord ID...",
        required=True,
        max_length=20
    )

    notes = discord.ui.TextInput(
        label="Reason/Notes",
        placeholder="Why are you flagging this user?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    expiry_days = discord.ui.TextInput(
        label="Expiry (days)",
        placeholder="30",
        required=False,
        default="30",
        max_length=3
    )

    def __init__(self, cog: "ShadyFlags"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        """Handle flag submission."""
        try:
            uid = int(self.user_id.value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid user ID. Please provide a numeric Discord user ID.",
                ephemeral=True
            )
            return

        try:
            days = int(self.expiry_days.value) if self.expiry_days.value else 30
            if days < 1 or days > 365:
                days = 30
        except ValueError:
            days = 30

        flag_id = await self.cog.add_flag(
            interaction.guild.id,
            uid,
            interaction.user.id,
            self.notes.value,
            days
        )

        try:
            user = await self.cog.bot.fetch_user(uid)
            user_display = f"{user.name} ({uid})"
        except Exception:
            user_display = f"User ID: {uid}"

        embed = discord.Embed(
            title="✅ Flag Added",
            description=f"Flag added to {user_display}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Notes", value=self.notes.value, inline=False)
        embed.add_field(name="Flagged By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Expires", value=f"In {days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🚩 **Flag Added** by {interaction.user.mention}\n"
            f"**User:** <@{uid}> ({uid})\n"
            f"**Notes:** {self.notes.value}\n"
            f"**Expires:** {days} days"
        )


class ShadyFlags(commands.Cog):
    """Temporary warning/flag system with account age auto-flagging and ML risk scoring."""

    __version__ = "3.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)

        default_guild = {
            "flags": [],
            "mod_log_channel": None,
            "flag_expiry_days": 30,
            "auto_flag_enabled": True,
            "threshold_critical_days": 1,
            "threshold_high_days": 7,
            "threshold_medium_days": 30,
            "flag_expiry_critical_days": 14,
            "flag_expiry_high_days": 7,
            "flag_expiry_medium_days": 3,
            "next_flag_id": 1,
            "mod_roles": [],  # List of role IDs that can manage flags
            # Statistics tracking
            "stats": {
                "total_flags_created": 0,
                "total_flags_cleared": 0,
                "total_auto_flags": 0,
            },
            # Flag decision log for ML training
            "flag_decisions": [],  # List of {flag_id, user_id, action, moderator_id, timestamp}
            "max_decision_history": 1000,
            # ML feature extraction
            "join_features": [],  # List of JoinFeatures as dicts
            "max_join_features": 1000,
            # Known bad actor network (two-tier: suspect → confirmed)
            "suspect_network": {},  # Dict of user_id (str) -> suspect data (high risk, not yet actioned)
            "known_network": {},  # Dict of user_id (str) -> KnownBadActor dict (CONFIRMED bad actors)
            "auto_parse_mod_log": True,  # Auto-parse mod log for bad actors
            # ML Risk Scoring settings
            "ml_enabled": True,  # Whether to use ML scoring (enabled by default for suspect detection)
            "ml_suspect_threshold": 0.6,  # Score threshold for adding to suspect network
            "ml_threshold": 0.7,  # Score threshold for auto-flagging (0.5-0.95)
            "ml_critical_threshold": 0.9,  # Score for critical auto-flag
        }
        self.config.register_guild(**default_guild)

        # Initialize risk model (per-guild models stored in data path)
        self.risk_models: Dict[int, RiskModel] = {}  # guild_id -> RiskModel
        self._model_load_task = None

    async def cog_load(self):
        """Called when the cog is loaded."""
        # Load risk models for guilds that have them
        self._model_load_task = self.bot.loop.create_task(self._load_all_models())

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self._model_load_task:
            self._model_load_task.cancel()
        # Save all models
        for guild_id, model in self.risk_models.items():
            if model.is_trained:
                try:
                    data_path = cog_data_path(self) / str(guild_id)
                    data_path.mkdir(parents=True, exist_ok=True)
                    model.save(data_path)
                except Exception as e:
                    log.error(f"Failed to save model for guild {guild_id}: {e}")

    async def _load_all_models(self):
        """Load saved models for all guilds."""
        try:
            data_path = cog_data_path(self)
            if not data_path.exists():
                return

            for guild_dir in data_path.iterdir():
                if guild_dir.is_dir() and guild_dir.name.isdigit():
                    guild_id = int(guild_dir.name)
                    model = RiskModel(guild_dir)
                    if model.load(guild_dir):
                        self.risk_models[guild_id] = model
                        log.info(f"Loaded risk model for guild {guild_id}")
        except Exception as e:
            log.error(f"Error loading models: {e}")

    def get_risk_model(self, guild_id: int) -> RiskModel:
        """Get or create risk model for a guild."""
        if guild_id not in self.risk_models:
            data_path = cog_data_path(self) / str(guild_id)
            self.risk_models[guild_id] = RiskModel(data_path)
            # Try to load existing model
            if data_path.exists():
                self.risk_models[guild_id].load(data_path)
        return self.risk_models[guild_id]

    async def save_risk_model(self, guild_id: int):
        """Save the risk model for a guild."""
        if guild_id in self.risk_models:
            data_path = cog_data_path(self) / str(guild_id)
            data_path.mkdir(parents=True, exist_ok=True)
            self.risk_models[guild_id].save(data_path)

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage flags."""
        # Bot owner always authorized
        if await self.bot.is_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/guild owner always authorized
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True

        # Check for moderate_members permission
        if interaction.user.guild_permissions.moderate_members:
            return True

        # Check for ban_members permission
        if interaction.user.guild_permissions.ban_members:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(interaction.guild).mod_roles()
        return any(role.id in mod_roles for role in interaction.user.roles)

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

    # ===== ML FEATURE EXTRACTION METHODS =====

    async def extract_and_store_features(self, member: discord.Member) -> Dict[str, Any]:
        """Extract ML features from member join and store them."""
        try:
            # Extract base features
            features = extract_join_features(member, member.guild)

            # Add server join rate context
            join_features_list = await self.config.guild(member.guild).join_features()
            now = datetime.now(timezone.utc)

            # Count joins in last hour and day
            joins_last_hour = 0
            joins_last_day = 0
            hour_ago = now - timedelta(hours=1)
            day_ago = now - timedelta(days=1)

            for jf in join_features_list:
                try:
                    ts = datetime.fromisoformat(jf.get("timestamp", ""))
                    if ts > hour_ago:
                        joins_last_hour += 1
                    if ts > day_ago:
                        joins_last_day += 1
                except Exception:
                    pass

            features.server_joins_last_hour = joins_last_hour
            features.server_joins_last_day = joins_last_day

            # Store features
            features_dict = asdict(features)

            # Enrich with alt network information (ShadyAlts integration)
            features_dict = await self.enrich_features_with_network(
                features_dict, member.guild, member.id
            )

            async with self.config.guild(member.guild).join_features() as jf_list:
                jf_list.append(features_dict)
                # Limit to max_join_features
                max_features = await self.config.guild(member.guild).max_join_features()
                if len(jf_list) > max_features:
                    jf_list.pop(0)  # Remove oldest

            log.debug(f"Stored join features for {member} in {member.guild}")
            return features_dict

        except Exception as e:
            log.error(f"Error extracting join features: {e}", exc_info=True)
            return {}

    async def label_join_features(
        self,
        guild_id: int,
        user_id: int,
        was_actioned: bool,
        action_type: str = None
    ):
        """Retroactively label join features when mod action is taken."""
        try:
            async with self.config.guild_from_id(guild_id).join_features() as jf_list:
                for jf in jf_list:
                    if jf.get("user_id") == user_id and jf.get("was_actioned") is None:
                        jf["was_actioned"] = was_actioned
                        jf["action_type"] = action_type
                        log.debug(f"Labeled join features for user {user_id}: actioned={was_actioned}")
                        break
        except Exception as e:
            log.error(f"Error labeling join features: {e}", exc_info=True)

    async def get_join_features_stats(self, guild: discord.Guild) -> Dict[str, Any]:
        """Get aggregate statistics about collected join features."""
        jf_list = await self.config.guild(guild).join_features()

        if not jf_list:
            return {"total": 0}

        total = len(jf_list)
        labeled = sum(1 for jf in jf_list if jf.get("was_actioned") is not None)
        actioned = sum(1 for jf in jf_list if jf.get("was_actioned") is True)

        # Feature analysis
        random_patterns = sum(1 for jf in jf_list if jf.get("username_random_pattern"))
        no_avatar = sum(1 for jf in jf_list if not jf.get("has_avatar"))
        new_accounts = sum(1 for jf in jf_list if jf.get("account_age_bucket") == "0-24h")

        # Entropy statistics
        entropies = [jf.get("username_entropy", 0) for jf in jf_list]
        avg_entropy = sum(entropies) / len(entropies) if entropies else 0

        return {
            "total": total,
            "labeled": labeled,
            "actioned": actioned,
            "action_rate": (actioned / labeled * 100) if labeled > 0 else 0,
            "random_patterns": random_patterns,
            "no_avatar": no_avatar,
            "new_accounts_24h": new_accounts,
            "avg_entropy": round(avg_entropy, 3),
        }

    # ===== DATABASE METHODS =====

    async def add_flag(self, guild_id: int, user_id: int, moderator_id: int, reason: str, expiry_days: int, priority: str = "manual") -> int:
        """Add a flag to a user. Returns the flag ID."""
        async with self.config.guild_from_id(guild_id).all() as guild_data:
            flag_id = guild_data["next_flag_id"]
            guild_data["next_flag_id"] += 1

            expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat()

            flag = {
                "id": flag_id,
                "user_id": user_id,
                "moderator_id": moderator_id,
                "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at,
                "priority": priority
            }
            guild_data["flags"].append(flag)

            # Update statistics
            if "stats" not in guild_data:
                guild_data["stats"] = {"total_flags_created": 0, "total_flags_cleared": 0, "total_auto_flags": 0}
            guild_data["stats"]["total_flags_created"] += 1
            if priority != "manual":
                guild_data["stats"]["total_auto_flags"] += 1

            return flag_id

    async def record_flag_decision(self, guild_id: int, flag_id: int, user_id: int, action: str, moderator_id: int) -> None:
        """Record a flag decision for ML training data."""
        async with self.config.guild_from_id(guild_id).all() as guild_data:
            if "flag_decisions" not in guild_data:
                guild_data["flag_decisions"] = []

            decision = {
                "flag_id": flag_id,
                "user_id": user_id,
                "action": action,  # "cleared", "expired", "ban", "kick", "false_positive"
                "moderator_id": moderator_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            guild_data["flag_decisions"].append(decision)

            # Trim to max history
            max_history = guild_data.get("max_decision_history", 1000)
            if len(guild_data["flag_decisions"]) > max_history:
                guild_data["flag_decisions"] = guild_data["flag_decisions"][-max_history:]

    async def get_flags(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all active flags for a user."""
        await self._cleanup_expired_flags(guild_id)
        flags = await self.config.guild_from_id(guild_id).flags()
        now = datetime.now(timezone.utc)
        return [f for f in flags if f["user_id"] == user_id and datetime.fromisoformat(f["expires_at"]) > now]

    async def get_all_flagged(self, guild_id: int) -> List[dict]:
        """Get all flagged users with their flag counts."""
        await self._cleanup_expired_flags(guild_id)
        flags = await self.config.guild_from_id(guild_id).flags()
        now = datetime.now(timezone.utc)

        user_flags = {}
        for f in flags:
            if datetime.fromisoformat(f["expires_at"]) > now:
                uid = f["user_id"]
                if uid not in user_flags:
                    user_flags[uid] = {"user_id": uid, "flag_count": 0, "highest_priority": "manual"}
                user_flags[uid]["flag_count"] += 1
                priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
                if priority_order.get(f["priority"], 3) < priority_order.get(user_flags[uid]["highest_priority"], 3):
                    user_flags[uid]["highest_priority"] = f["priority"]

        return list(user_flags.values())

    async def clear_flags(self, guild_id: int, user_id: int) -> None:
        """Clear all flags for a user."""
        async with self.config.guild_from_id(guild_id).flags() as flags:
            flags[:] = [f for f in flags if f["user_id"] != user_id]

    async def remove_flag(self, guild_id: int, flag_id: int) -> Optional[dict]:
        """Remove a specific flag by ID."""
        async with self.config.guild_from_id(guild_id).flags() as flags:
            for i, f in enumerate(flags):
                if f["id"] == flag_id:
                    return flags.pop(i)
        return None

    async def _cleanup_expired_flags(self, guild_id: int) -> None:
        """Remove expired flags."""
        now = datetime.now(timezone.utc)
        async with self.config.guild_from_id(guild_id).flags() as flags:
            flags[:] = [f for f in flags if datetime.fromisoformat(f["expires_at"]) > now]

    def _build_flags_embed(
        self,
        flags: List[dict],
        user_display: str,
        avatar_url: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> discord.Embed:
        """Build an embed showing a user's flags."""
        embed = discord.Embed(
            title=f"🚩 Flags for {user_display}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if user_id:
            embed.add_field(name="User ID", value=str(user_id), inline=False)

        for flag in flags:
            created = datetime.fromisoformat(flag["created_at"])
            expires = datetime.fromisoformat(flag["expires_at"])
            days_left = (expires - datetime.now(timezone.utc)).days
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(flag["priority"], "🚩")

            value = f"**Reason:** {flag['reason']}\n"
            value += f"**Created:** <t:{int(created.timestamp())}:R>\n"
            value += f"**Expires:** <t:{int(expires.timestamp())}:R> ({days_left}d left)\n"
            value += f"**By:** <@{flag['moderator_id']}>"

            embed.add_field(name=f"{priority_emoji} Flag #{flag['id']}", value=value, inline=False)

        return embed

    async def log_to_mod_channel(self, guild: discord.Guild, message: str = None, embed: discord.Embed = None) -> None:
        """Log message to mod channel."""
        channel_id = await self.config.guild(guild).mod_log_channel()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                if embed:
                    await channel.send(embed=embed)
                elif message:
                    await channel.send(message)
            except discord.Forbidden:
                log.warning(f"Cannot send to mod log channel in {guild.name}")

    # ===== AUTO-FLAG ON JOIN =====

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Score all joins against ML features and check networks."""
        if member.bot:
            return

        # ===== STEP 1: EXTRACT FEATURES =====
        # Always extract features for ML (account age, avatar, username patterns, etc.)
        features = await self.extract_and_store_features(member)

        enabled = await self.config.guild(member.guild).auto_flag_enabled()
        if not enabled:
            return

        guild_config = await self.config.guild(member.guild).all()

        # ===== STEP 2: CHECK CONFIRMED NETWORK =====
        # Is this a returning banned user? (ban evasion)
        confirmed_actor = await self.is_in_known_network(member.guild, member.id)
        if confirmed_actor:
            # CRITICAL: Previously banned for bot/spam and rejoined
            ban_reason = confirmed_actor.get("reason", "Unknown")
            flag_reason = f"[RETURNING BANNED] Previously banned: {ban_reason[:100]}"

            flag_id = await self.add_flag(
                member.guild.id, member.id, self.bot.user.id,
                flag_reason, guild_config["flag_expiry_critical_days"], "critical"
            )

            embed = discord.Embed(
                title="🚨 RETURNING BANNED USER",
                description=f"{member.mention} was previously banned and has rejoined!",
                color=discord.Color.dark_red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
            embed.add_field(name="Flag ID", value=str(flag_id), inline=True)
            embed.add_field(name="Previous Ban Reason", value=ban_reason[:200], inline=False)
            embed.add_field(
                name="⚠️ IMMEDIATE ACTION REQUIRED",
                value="This user was previously banned for bot/spam activity",
                inline=False
            )

            await self.log_to_mod_channel(member.guild, embed=embed)
            return  # Don't need further checks

        # ===== STEP 3: CALCULATE RISK SCORE =====
        # Score ALL joins against ML features
        risk_model = self.get_risk_model(member.guild.id)
        risk_score = risk_model.predict(features) if features else None

        # ===== STEP 4: CHECK TOXIC ALT NETWORK =====
        toxicity, network_size, has_toxic = await self.calculate_network_toxicity(
            member.guild, member.id
        )

        if has_toxic or toxicity > 0.5:
            if toxicity > 0.5:
                network_priority = "critical"
                network_expiry = guild_config["flag_expiry_critical_days"]
            else:
                network_priority = "high"
                network_expiry = guild_config["flag_expiry_high_days"]

            network_reason = (
                f"[TOXIC ALTS] Network toxicity: {toxicity*100:.0f}% "
                f"({network_size} alts, has confirmed bad actors)"
            )
            network_flag_id = await self.add_flag(
                member.guild.id, member.id, self.bot.user.id,
                network_reason, network_expiry, network_priority
            )

            network_emoji = "🔴" if network_priority == "critical" else "🟠"

            network_embed = discord.Embed(
                title=f"{network_emoji} Toxic Alt Network Detected",
                description=f"{member.mention} has confirmed bad actors in their alt network",
                color=discord.Color.red() if network_priority == "critical" else discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            network_embed.set_thumbnail(url=member.display_avatar.url)
            network_embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
            network_embed.add_field(name="Network Size", value=str(network_size), inline=True)
            network_embed.add_field(name="Toxicity", value=f"{toxicity*100:.0f}%", inline=True)
            network_embed.add_field(name="Priority", value=network_priority.upper(), inline=True)
            network_embed.add_field(name="Flag ID", value=str(network_flag_id), inline=True)

            await self.log_to_mod_channel(member.guild, embed=network_embed)

        # ===== STEP 5: ML RISK SCORING =====
        # Check if risk score (calculated in Step 3) exceeds suspect threshold
        if risk_score:
            ml_suspect_threshold = guild_config.get("ml_suspect_threshold", 0.6)
            ml_threshold = guild_config.get("ml_threshold", 0.7)
            ml_critical = guild_config.get("ml_critical_threshold", 0.9)

            # If above suspect threshold, add to suspect network AND flag
            if risk_score.score >= ml_suspect_threshold:
                # Add to suspect network for tracking
                await self.add_to_suspect_network(
                    member.guild,
                    member.id,
                    risk_score.score,
                    risk_score.confidence,
                    risk_score.top_factors,
                    features
                )

                # Determine flag priority based on score
                if risk_score.score >= ml_critical:
                    ml_priority = "critical"
                    ml_expiry = guild_config["flag_expiry_critical_days"]
                elif risk_score.score >= ml_threshold:
                    ml_priority = "high"
                    ml_expiry = guild_config["flag_expiry_high_days"]
                else:
                    ml_priority = "medium"
                    ml_expiry = guild_config["flag_expiry_medium_days"]

                # Build flag reason with confidence %
                factors_str = ", ".join([f[0] for f in risk_score.top_factors[:3]]) if risk_score.top_factors else "multiple factors"
                ml_reason = (
                    f"[SUSPECT] {risk_score.confidence*100:.0f}% confidence bot/spam - "
                    f"Risk: {risk_score.score*100:.0f}% | {factors_str}"
                )

                ml_flag_id = await self.add_flag(
                    member.guild.id, member.id, self.bot.user.id,
                    ml_reason, ml_expiry, ml_priority
                )

                # Emoji based on priority
                ml_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(ml_priority, "⚪")

                ml_embed = discord.Embed(
                    title=f"{ml_emoji} Suspect Detected",
                    description=f"{member.mention} added to suspect network",
                    color={"critical": discord.Color.red(), "high": discord.Color.orange(), "medium": discord.Color.gold()}.get(ml_priority, discord.Color.greyple()),
                    timestamp=datetime.now(timezone.utc)
                )
                ml_embed.set_thumbnail(url=member.display_avatar.url)
                ml_embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
                ml_embed.add_field(name="Risk Score", value=f"{risk_score.score*100:.0f}%", inline=True)
                ml_embed.add_field(name="Bot/Spam Confidence", value=f"{risk_score.confidence*100:.0f}%", inline=True)
                ml_embed.add_field(name="Priority", value=ml_priority.upper(), inline=True)
                ml_embed.add_field(name="Flag ID", value=str(ml_flag_id), inline=True)
                ml_embed.add_field(name="Status", value="🔍 SUSPECT (pending mod action)", inline=True)

                if risk_score.top_factors:
                    factors_display = "\n".join([f"• {f[0]}" for f in risk_score.top_factors[:5]])
                    ml_embed.add_field(name="Risk Factors", value=factors_display, inline=False)

                ml_embed.set_footer(text="If banned for bot/spam, will be moved to confirmed network")

                await self.log_to_mod_channel(member.guild, embed=ml_embed)

        # ===== CHECK FOR NEW ACCOUNT =====
        threshold_critical_days = guild_config.get("threshold_critical_days", 1)
        threshold_high_days = guild_config["threshold_high_days"]
        threshold_medium_days = guild_config["threshold_medium_days"]

        account_age = datetime.now(timezone.utc) - member.created_at.replace(tzinfo=timezone.utc)
        account_age_days = account_age.days
        account_age_hours = account_age.total_seconds() / 3600

        priority = None
        expiry_days = 0

        if account_age_days < threshold_critical_days:
            priority = "critical"
            expiry_days = guild_config["flag_expiry_critical_days"]
            if account_age_hours < 24:
                age_display = f"{int(account_age_hours)} hours" if account_age_hours >= 1 else f"{int(account_age.total_seconds() / 60)} minutes"
            else:
                age_display = f"{account_age_days} days"
        elif account_age_days < threshold_high_days:
            priority = "high"
            expiry_days = guild_config["flag_expiry_high_days"]
            age_display = f"{account_age_days} days"
        elif account_age_days < threshold_medium_days:
            priority = "medium"
            expiry_days = guild_config["flag_expiry_medium_days"]
            age_display = f"{account_age_days} days"

        if not priority:
            return

        reason = f"[AUTO] New account detected - Account age: {age_display}"
        flag_id = await self.add_flag(member.guild.id, member.id, self.bot.user.id, reason, expiry_days, priority)

        priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(priority, "⚪")

        embed = discord.Embed(
            title=f"{priority_emoji} New Account Auto-Flagged",
            description=f"{member.mention} has joined with a very new account",
            color={"critical": discord.Color.red(), "high": discord.Color.orange(), "medium": discord.Color.gold()}.get(priority, discord.Color.greyple()),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="Account Age", value=age_display, inline=True)
        embed.add_field(name="Priority", value=priority.upper(), inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Flag Expires", value=f"In {expiry_days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await self.log_to_mod_channel(member.guild, embed=embed)

    # ===== MOD LOG PARSING =====

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for mod actions in the mod log channel."""
        if not message.guild or message.author == self.bot.user:
            return

        # Check if this is in the mod log channel
        mod_log_channel = await self.config.guild(message.guild).mod_log_channel()
        if not mod_log_channel or message.channel.id != mod_log_channel:
            return

        # Check if auto-parsing is enabled
        if not await self.config.guild(message.guild).auto_parse_mod_log():
            return

        # Only parse bot messages (mod bots)
        if not message.author.bot:
            return

        await self.parse_mod_action(message)

    async def parse_mod_action(self, message: discord.Message):
        """Parse a mod log message for bad actor indicators."""
        try:
            user_id = None
            action_type = None
            reason = None

            # Check embeds (most mod bots use embeds)
            for embed in message.embeds:
                # Extract user ID from various embed fields
                for field in embed.fields:
                    field_lower = field.name.lower()
                    if any(n in field_lower for n in ["user", "member", "target", "offender"]):
                        user_id = user_id or parse_user_id_from_text(field.value)
                    elif "action" in field_lower or "type" in field_lower:
                        action_type = action_type or field.value.lower()
                    elif "reason" in field_lower:
                        reason = reason or field.value

                # Also check description for Jeebs format
                if embed.description:
                    desc = embed.description

                    # Extract user ID from FIRST LINE only (Jeebs: "username (id)\nCase...")
                    # IMPORTANT: Don't grab IDs from reason text (could be mod ID)
                    if not user_id:
                        first_line_match = re.match(r'^([^\n(]+)\s*\((\d{17,20})\)', desc)
                        if first_line_match:
                            user_id = int(first_line_match.group(2))

                    # Extract reason from "Reason: ..." line in description (Jeebs format)
                    if not reason:
                        reason_match = re.search(r'Reason:\s*([^\n]+)', desc, re.IGNORECASE)
                        if reason_match:
                            reason = reason_match.group(1).strip()

                    # Check description for action keywords
                    desc_lower = desc.lower()
                    if "unban" in desc_lower:
                        action_type = "unban"  # Skip unbans
                    elif "banned" in desc_lower or "ban" in desc_lower:
                        action_type = action_type or "ban"
                    elif "kicked" in desc_lower:
                        action_type = action_type or "kick"

                # Check title for action type
                if embed.title:
                    title_lower = embed.title.lower()
                    if "ban" in title_lower:
                        action_type = action_type or "ban"
                    elif "kick" in title_lower:
                        action_type = action_type or "kick"

            # Also try plain text content
            if message.content:
                user_id = user_id or parse_user_id_from_text(message.content)

            # Need at least user ID and either ban/kick action or bad actor reason
            if not user_id:
                return

            # Check if this is a bad actor action
            is_bad = False
            if reason and is_bad_actor_action(reason):
                is_bad = True
            elif action_type and action_type in ["ban", "kick"]:
                # Only auto-add for bans, not all kicks
                if action_type == "ban":
                    is_bad = True

            if not is_bad:
                return

            # Check if user was in suspect network - if so, promote to confirmed
            was_suspect = await self.is_in_suspect_network(message.guild, user_id)
            if was_suspect:
                # Promote from suspect to confirmed (this also labels features)
                await self.promote_suspect_to_confirmed(
                    message.guild,
                    user_id,
                    action_type,
                    reason or "Confirmed from mod action"
                )
                log.info(f"Promoted user {user_id} from suspect to confirmed network in {message.guild}")
            else:
                # Not in suspect - add directly to confirmed network
                await self.add_to_known_network(
                    message.guild,
                    user_id,
                    source="mod_action",
                    reason=reason or "Auto-detected from mod log",
                    mod_action_type=action_type
                )
                log.info(f"Added user {user_id} to confirmed network from mod log in {message.guild}")

                # Label join features if we have them
                await self.label_join_features(message.guild.id, user_id, True, action_type)

        except Exception as e:
            log.error(f"Error parsing mod action: {e}", exc_info=True)

    # ===== KNOWN NETWORK METHODS =====

    async def add_to_known_network(
        self,
        guild: discord.Guild,
        user_id: int,
        source: str,
        reason: str,
        mod_action_type: str = None,
        linked_alts: List[int] = None,
        username: str = None,
        actioned_by: int = None,
        propagate: bool = True
    ):
        """Add a user to the known bad actor network.

        Args:
            propagate: If True, also add all known alts to the network.
                      Set to False when called from propagate_toxicity_to_alts to avoid recursion.
        """
        # Check if already in network to avoid duplicate propagation
        existing = await self.is_in_known_network(guild, user_id)
        if existing:
            return  # Already in network

        actor = KnownBadActor(
            user_id=user_id,
            added_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            reason=reason,
            mod_action_type=mod_action_type,
            linked_alts=linked_alts or [],
            username=username,
            actioned_by=actioned_by
        )

        async with self.config.guild(guild).known_network() as network:
            network[str(user_id)] = asdict(actor)

        # Also add to ShadyAlts "confirmed" network for cross-cog tracking
        # This will auto-confirm all manually-linked alts
        alts_cog = self.bot.get_cog("ShadyAlts")
        if alts_cog:
            try:
                result = await alts_cog.add_confirmed(
                    guild.id,
                    user_id,
                    reason=reason,
                    source=source
                )
                # Log auto-confirmations
                if result.get("confirmed"):
                    auto_confirmed = [uid for uid in result["confirmed"] if uid != user_id]
                    if auto_confirmed:
                        log.info(f"Auto-confirmed {len(auto_confirmed)} alts of {user_id}: {auto_confirmed}")
            except Exception as e:
                log.debug(f"Could not add to ShadyAlts confirmed network: {e}")

        # Propagate toxicity to all known alts (feedback loop) - for our internal network
        if propagate and source != "alt_of_confirmed":
            await self.propagate_toxicity_to_alts(guild, user_id, reason)

    async def remove_from_known_network(self, guild: discord.Guild, user_id: int) -> bool:
        """Remove a user from the known network. Returns True if removed."""
        async with self.config.guild(guild).known_network() as network:
            if str(user_id) in network:
                del network[str(user_id)]
                return True
        return False

    async def is_in_known_network(self, guild: discord.Guild, user_id: int) -> Optional[Dict]:
        """Check if user is in the known bad actor network.

        Checks both internal known_network AND ShadyAlts confirmed network.
        """
        # Check internal network first
        network = await self.config.guild(guild).known_network()
        if str(user_id) in network:
            return network.get(str(user_id))

        # Also check ShadyAlts confirmed network
        alts_cog = self.bot.get_cog("ShadyAlts")
        if alts_cog:
            try:
                confirmed = await alts_cog.get_confirmed(guild.id, user_id)
                if confirmed:
                    return confirmed
            except Exception as e:
                log.debug(f"Error checking ShadyAlts confirmed status: {e}")

        return None

    async def get_known_network_stats(self, guild: discord.Guild) -> Dict[str, Any]:
        """Get statistics about the known network."""
        network = await self.config.guild(guild).known_network()

        if not network:
            return {"total": 0}

        sources = {}
        actions = {}

        for actor in network.values():
            source = actor.get("source", "unknown")
            sources[source] = sources.get(source, 0) + 1

            action = actor.get("mod_action_type", "unknown")
            if action:
                actions[action] = actions.get(action, 0) + 1

        return {
            "total": len(network),
            "by_source": sources,
            "by_action": actions,
        }

    # ===== SUSPECT NETWORK METHODS =====

    async def add_to_suspect_network(
        self,
        guild: discord.Guild,
        user_id: int,
        risk_score: float,
        confidence: float,
        top_factors: List[Tuple[str, float]],
        features: Dict[str, Any] = None
    ):
        """Add a user to the suspect network (high risk, not yet confirmed)."""
        # Don't add if already in confirmed network
        if await self.is_in_known_network(guild, user_id):
            return

        suspect_data = {
            "user_id": user_id,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "risk_score": risk_score,
            "confidence": confidence,
            "top_factors": top_factors,
            "features": features or {},
        }

        async with self.config.guild(guild).suspect_network() as network:
            network[str(user_id)] = suspect_data

        # Also register in ShadyAlts "suspects" for visibility
        # (This allows /altnetwork suspect to show all suspects)
        alts_cog = self.bot.get_cog("ShadyAlts")
        if alts_cog:
            try:
                await alts_cog.add_suspect(
                    guild.id,
                    user_id,
                    reason=f"ML Risk: {risk_score*100:.0f}%",
                    risk_score=risk_score,
                    confidence=confidence,
                    top_factors=top_factors
                )
            except Exception as e:
                log.debug(f"Could not add to ShadyAlts suspect network: {e}")

    async def is_in_suspect_network(self, guild: discord.Guild, user_id: int) -> Optional[Dict]:
        """Check if user is in the suspect network."""
        network = await self.config.guild(guild).suspect_network()
        return network.get(str(user_id))

    async def remove_from_suspect_network(self, guild: discord.Guild, user_id: int) -> Optional[Dict]:
        """Remove user from suspect network. Returns their data if removed."""
        async with self.config.guild(guild).suspect_network() as network:
            if str(user_id) in network:
                return network.pop(str(user_id))
        return None

    async def promote_suspect_to_confirmed(
        self,
        guild: discord.Guild,
        user_id: int,
        mod_action_type: str,
        reason: str
    ):
        """Move user from suspect to confirmed network (on mod action)."""
        # Get and remove from suspect
        suspect_data = await self.remove_from_suspect_network(guild, user_id)

        # Add to confirmed network (this also notifies ShadyAlts)
        await self.add_to_known_network(
            guild,
            user_id,
            source="confirmed_from_suspect",
            reason=reason,
            mod_action_type=mod_action_type,
            propagate=True
        )

        # Also explicitly promote in ShadyAlts (removes from suspects, adds to confirmed)
        alts_cog = self.bot.get_cog("ShadyAlts")
        if alts_cog:
            try:
                await alts_cog.promote_suspect_to_confirmed(
                    guild.id,
                    user_id,
                    reason=reason,
                    source="mod_action"
                )
            except Exception as e:
                log.debug(f"Could not promote in ShadyAlts: {e}")

        # Label their join features for ML training
        await self.label_join_features(guild.id, user_id, True, mod_action_type)

        log.info(f"Promoted user {user_id} from suspect to confirmed network")
        return suspect_data

    async def get_suspect_network_stats(self, guild: discord.Guild) -> Dict[str, Any]:
        """Get statistics about the suspect network."""
        network = await self.config.guild(guild).suspect_network()

        if not network:
            return {"total": 0}

        risk_scores = [s.get("risk_score", 0) for s in network.values()]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        return {
            "total": len(network),
            "avg_risk_score": round(avg_risk, 2),
            "high_risk_count": sum(1 for r in risk_scores if r >= 0.8),
        }

    # ===== SHADYALTS INTEGRATION =====

    async def get_alt_network(self, guild_id: int, user_id: int) -> List[int]:
        """Get alt network for a user via ShadyAlts cog.

        Returns list of all user IDs in the same alt group (manually linked alts).
        """
        alts_cog = self.bot.get_cog("ShadyAlts")
        if not alts_cog:
            return []

        try:
            # Get the full alt group (all connected users via manual links)
            alt_group = await alts_cog.get_alt_group(guild_id, user_id)
            # Remove the user themselves from the list
            return [uid for uid in alt_group if uid != user_id]
        except Exception as e:
            log.error(f"Error getting alt network: {e}")
            return []

    async def add_to_alt_network(
        self,
        guild_id: int,
        user_id: int,
        alt_id: int,
        reason: str
    ) -> bool:
        """Add an alt relationship to ShadyAlts (manual alt link)."""
        alts_cog = self.bot.get_cog("ShadyAlts")
        if not alts_cog:
            log.warning("ShadyAlts cog not loaded - cannot add to alt network")
            return False

        try:
            await alts_cog.add_alt(guild_id, user_id, alt_id, reason)
            return True
        except Exception as e:
            log.error(f"Error adding to alt network: {e}")
            return False

    async def get_user_alt_networks(self, guild_id: int, user_id: int) -> dict:
        """Check user status in ShadyAlts networks.

        Returns dict with:
            - has_alts: bool
            - is_suspect: bool
            - is_confirmed: bool
        """
        alts_cog = self.bot.get_cog("ShadyAlts")
        if not alts_cog:
            return {"has_alts": False, "is_suspect": False, "is_confirmed": False}

        try:
            alts = await alts_cog.get_alts(guild_id, user_id)
            suspect = await alts_cog.get_suspect(guild_id, user_id)
            confirmed = await alts_cog.get_confirmed(guild_id, user_id)

            return {
                "has_alts": len(alts) > 0,
                "is_suspect": suspect is not None,
                "is_confirmed": confirmed is not None
            }
        except Exception as e:
            log.error(f"Error getting user networks: {e}")
            return {"has_alts": False, "is_suspect": False, "is_confirmed": False}

    async def calculate_network_toxicity(
        self,
        guild: discord.Guild,
        user_id: int
    ) -> tuple[float, int, bool]:
        """Calculate network toxicity score.

        Returns (toxicity_score, network_size, has_toxic_alts)
        Checks both internal known_network AND ShadyAlts confirmed network.
        """
        network = await self.get_alt_network(guild.id, user_id)
        if not network:
            return 0.0, 0, False

        # Check our internal known network
        known_network = await self.config.guild(guild).known_network()
        known_ids = set(int(k) for k in known_network.keys())

        # Also check ShadyAlts confirmed network
        alts_cog = self.bot.get_cog("ShadyAlts")
        if alts_cog:
            try:
                for uid in network:
                    if await alts_cog.is_confirmed(guild.id, uid):
                        known_ids.add(uid)
            except Exception as e:
                log.debug(f"Error checking ShadyAlts confirmed status: {e}")

        bad_in_network = sum(1 for uid in network if uid in known_ids)
        has_toxic = bad_in_network > 0
        toxicity = bad_in_network / len(network) if network else 0.0

        return toxicity, len(network), has_toxic

    async def propagate_toxicity_to_alts(self, guild: discord.Guild, user_id: int, reason: str):
        """When a user is confirmed bad, add their alts to known network."""
        network = await self.get_alt_network(guild.id, user_id)
        if not network:
            return

        for alt_id in network:
            existing = await self.is_in_known_network(guild, alt_id)
            if not existing:
                await self.add_to_known_network(
                    guild,
                    alt_id,
                    source="alt_of_confirmed",
                    reason=f"Alt of confirmed bad actor {user_id}: {reason}",
                    mod_action_type=None,
                    linked_alts=[user_id],
                    propagate=False  # Don't recurse - avoid infinite loop
                )
                log.info(f"Added alt {alt_id} to known network (linked to {user_id})")

    async def enrich_features_with_network(
        self,
        features_dict: Dict[str, Any],
        guild: discord.Guild,
        user_id: int
    ) -> Dict[str, Any]:
        """Enrich join features with network information."""
        toxicity, network_size, has_toxic = await self.calculate_network_toxicity(guild, user_id)

        features_dict["is_in_alt_network"] = network_size > 0
        features_dict["alt_network_size"] = network_size
        features_dict["alt_network_toxicity"] = round(toxicity, 3)
        features_dict["has_toxic_alts"] = has_toxic

        return features_dict

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="flag", description="Manage flags for server members")
    @app_commands.describe(
        action="Action to perform",
        user="User to flag/view/clear",
        flag_id="Flag ID number (required for Remove Flag)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Flag", value="add"),
        app_commands.Choice(name="View Flags", value="view"),
        app_commands.Choice(name="Remove Flag", value="remove"),
        app_commands.Choice(name="Clear All Flags", value="clear"),
    ])
    async def flag_cmd(self, interaction: discord.Interaction, action: str, user: discord.Member, flag_id: Optional[int] = None):
        """Flag management for server members."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if action == "add":
            # Open modal for adding flag
            modal = AddFlagMemberModal(self, user)
            await interaction.response.send_modal(modal)

        elif action == "view":
            flags = await self.get_flags(interaction.guild.id, user.id)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user.mention}", ephemeral=True)
                return

            embed = self._build_flags_embed(flags, user.display_name, user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "remove":
            if flag_id is None:
                await interaction.response.send_message(
                    "You must provide a `flag_id` to remove. Use `/flag view` to see flag IDs.",
                    ephemeral=True
                )
                return

            removed = await self.remove_flag(interaction.guild.id, flag_id)
            if not removed:
                await interaction.response.send_message(
                    f"Flag #{flag_id} not found. Use `/flag view` to see active flags.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"✅ Removed flag #{flag_id} from {user.mention}\n**Reason was:** {removed['reason']}",
                ephemeral=True
            )

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flag Removed** by {interaction.user.mention}\n"
                f"**User:** {user.mention}\n"
                f"**Flag ID:** {flag_id}\n"
                f"**Reason was:** {removed['reason']}"
            )

        elif action == "clear":
            flags = await self.get_flags(interaction.guild.id, user.id)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user.mention}", ephemeral=True)
                return

            count = len(flags)
            await self.clear_flags(interaction.guild.id, user.id)
            await interaction.response.send_message(f"✅ Cleared {count} flag(s) from {user.mention}", ephemeral=True)

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flags Cleared** by {interaction.user.mention}\n**User:** {user.mention}\n**Flags Removed:** {count}"
            )

    @app_commands.command(name="flagid", description="Manage flags by user ID (for users not in server)")
    @app_commands.describe(
        action="Action to perform",
        user_id="Discord User ID",
        flag_id="Flag ID number (required for Remove Flag)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Flag", value="add"),
        app_commands.Choice(name="View Flags", value="view"),
        app_commands.Choice(name="Remove Flag", value="remove"),
        app_commands.Choice(name="Clear All Flags", value="clear"),
    ])
    async def flagid_cmd(self, interaction: discord.Interaction, action: str, user_id: str, flag_id: Optional[int] = None):
        """Flag management by user ID."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(uid)
            user_display = user.name
        except Exception:
            user_display = f"User {uid}"

        if action == "add":
            modal = AddFlagModal(self)
            modal.user_id.default = user_id
            await interaction.response.send_modal(modal)

        elif action == "view":
            flags = await self.get_flags(interaction.guild.id, uid)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user_display}", ephemeral=True)
                return

            embed = self._build_flags_embed(flags, user_display, user_id=uid)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "remove":
            if flag_id is None:
                await interaction.response.send_message(
                    "You must provide a `flag_id` to remove. Use `/flagid view` to see flag IDs.",
                    ephemeral=True
                )
                return

            removed = await self.remove_flag(interaction.guild.id, flag_id)
            if not removed:
                await interaction.response.send_message(
                    f"Flag #{flag_id} not found. Use `/flagid view` to see active flags.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"✅ Removed flag #{flag_id} from {user_display}\n**Reason was:** {removed['reason']}",
                ephemeral=True
            )

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flag Removed** by {interaction.user.mention}\n"
                f"**User:** <@{uid}> ({uid})\n"
                f"**Flag ID:** {flag_id}\n"
                f"**Reason was:** {removed['reason']}"
            )

        elif action == "clear":
            flags = await self.get_flags(interaction.guild.id, uid)
            if not flags:
                await interaction.response.send_message(f"No active flags for {user_display}", ephemeral=True)
                return

            count = len(flags)
            await self.clear_flags(interaction.guild.id, uid)
            await interaction.response.send_message(f"✅ Cleared {count} flag(s) from {user_display}", ephemeral=True)

            await self.log_to_mod_channel(
                interaction.guild,
                f"🗑️ **Flags Cleared** by {interaction.user.mention}\n**User:** <@{uid}> ({uid})\n**Flags Removed:** {count}"
            )

    @app_commands.command(name="flagall", description="Show all flagged members")
    async def flagall_cmd(self, interaction: discord.Interaction):
        """Show all flagged members."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        flagged_users = await self.get_all_flagged(interaction.guild.id)

        if not flagged_users:
            await interaction.response.send_message("No members are currently flagged.", ephemeral=True)
            return

        priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
        flagged_users.sort(key=lambda x: (priority_order.get(x["highest_priority"], 3), -x["flag_count"]))

        embed = discord.Embed(title="🚩 Flagged Members", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))

        for user_data in flagged_users[:25]:
            member = interaction.guild.get_member(user_data["user_id"])
            name = member.mention if member else f"<@{user_data['user_id']}>"
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(user_data["highest_priority"], "🚩")
            embed.add_field(name=name, value=f"{priority_emoji} {user_data['flag_count']} flag(s)", inline=True)

        if len(flagged_users) > 25:
            embed.set_footer(text=f"Showing 25/{len(flagged_users)} flagged members")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="flagqueue", description="Review flagged users in a queue")
    async def flagqueue_cmd(self, interaction: discord.Interaction):
        """Show flag review queue with action buttons."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        flagged_users = await self.get_all_flagged(interaction.guild.id)

        if not flagged_users:
            await interaction.response.send_message("No members are currently flagged.", ephemeral=True)
            return

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "manual": 3}
        flagged_users.sort(key=lambda x: (priority_order.get(x["highest_priority"], 3), -x["flag_count"]))

        # Show first user in queue
        first_user = flagged_users[0]
        flags = await self.get_flags(interaction.guild.id, first_user["user_id"])

        member = interaction.guild.get_member(first_user["user_id"])
        if member:
            user_display = f"{member.mention} ({member.name})"
            avatar_url = member.display_avatar.url
        else:
            try:
                user = await self.bot.fetch_user(first_user["user_id"])
                user_display = f"<@{first_user['user_id']}> ({user.name})"
                avatar_url = user.display_avatar.url
            except Exception:
                user_display = f"<@{first_user['user_id']}>"
                avatar_url = None

        priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "manual": "🚩"}.get(first_user["highest_priority"], "🚩")

        embed = discord.Embed(
            title=f"{priority_emoji} Flag Review Queue",
            description=f"Reviewing: {user_display}",
            color={"critical": discord.Color.red(), "high": discord.Color.orange(), "medium": discord.Color.gold()}.get(first_user["highest_priority"], discord.Color.blurple()),
            timestamp=datetime.now(timezone.utc)
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.add_field(name="User ID", value=str(first_user["user_id"]), inline=True)
        embed.add_field(name="Flag Count", value=str(first_user["flag_count"]), inline=True)
        embed.add_field(name="Queue Position", value=f"1 of {len(flagged_users)}", inline=True)

        for flag in flags[:5]:
            created = datetime.fromisoformat(flag["created_at"])
            embed.add_field(
                name=f"Flag #{flag['id']} - {flag['priority'].upper()}",
                value=f"{flag['reason']}\n*<t:{int(created.timestamp())}:R>*",
                inline=False
            )

        if len(flags) > 5:
            embed.add_field(name="...", value=f"And {len(flags) - 5} more flags", inline=False)

        view = FlagReviewView(self, first_user["user_id"], len(flagged_users))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="flagstats", description="View flag statistics")
    async def flagstats_cmd(self, interaction: discord.Interaction):
        """View flag statistics and metrics."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        settings = await self.config.guild(interaction.guild).all()
        flags = settings.get("flags", [])
        stats = settings.get("stats", {"total_flags_created": 0, "total_flags_cleared": 0, "total_auto_flags": 0})
        decisions = settings.get("flag_decisions", [])

        # Count active flags by priority
        now = datetime.now(timezone.utc)
        active_by_priority = {"critical": 0, "high": 0, "medium": 0, "manual": 0}
        for f in flags:
            if datetime.fromisoformat(f["expires_at"]) > now:
                priority = f.get("priority", "manual")
                active_by_priority[priority] = active_by_priority.get(priority, 0) + 1

        total_active = sum(active_by_priority.values())

        embed = discord.Embed(
            title="📊 Flag Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        # Current status
        embed.add_field(name="Active Flags", value=str(total_active), inline=True)
        embed.add_field(name="Total Created", value=str(stats.get("total_flags_created", 0)), inline=True)
        embed.add_field(name="Auto-Flags", value=str(stats.get("total_auto_flags", 0)), inline=True)

        # By priority
        priority_breakdown = (
            f"🔴 Critical: {active_by_priority['critical']}\n"
            f"🟠 High: {active_by_priority['high']}\n"
            f"🟡 Medium: {active_by_priority['medium']}\n"
            f"🚩 Manual: {active_by_priority['manual']}"
        )
        embed.add_field(name="By Priority", value=priority_breakdown, inline=False)

        # Decision breakdown
        if decisions:
            action_counts = {}
            for d in decisions:
                action = d.get("action", "unknown")
                action_counts[action] = action_counts.get(action, 0) + 1

            decision_text = "\n".join([f"• {action}: {count}" for action, count in action_counts.items()])
            embed.add_field(name="Decision History", value=decision_text or "No decisions recorded", inline=False)
        else:
            embed.add_field(name="Decision History", value="No decisions recorded yet", inline=False)

        embed.set_footer(text=f"v{self.__version__}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="flagset", description="Configure flag settings")
    @app_commands.describe(
        setting="Setting to configure",
        role="Role for add/remove role actions",
        channel="Channel for log channel setting (bot-visible channels)"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="View Settings", value="view"),
        app_commands.Choice(name="Set Log Channel", value="channel"),
        app_commands.Choice(name="Toggle Auto-Flag", value="autoflag"),
        app_commands.Choice(name="Set Thresholds", value="threshold"),
        app_commands.Choice(name="Set Flag Expiry", value="expiry"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
    ])
    @app_commands.autocomplete(channel=bot_channel_autocomplete)
    async def flagset_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        role: Optional[discord.Role] = None,
        channel: Optional[str] = None
    ):
        """Configure flag settings."""
        # For role management, require admin (or bot owner)
        if setting in ("addrole", "removerole"):
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Only administrators can manage mod roles.", ephemeral=True
                )
                return
        elif not await self.is_authorized(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if setting == "view":
            settings = await self.config.guild(interaction.guild).all()
            channel = interaction.guild.get_channel(settings["mod_log_channel"]) if settings["mod_log_channel"] else None

            # Get mod roles
            mod_role_mentions = []
            for role_id in settings.get("mod_roles", []):
                r = interaction.guild.get_role(role_id)
                if r:
                    mod_role_mentions.append(r.mention)

            # Check for setup warnings
            warnings = []
            if not channel:
                warnings.append("⚠️ **No mod log channel set!** Use `/flagset channel` to receive flag notifications.")
            if settings.get("ml_enabled") and not self.get_risk_model(interaction.guild.id).is_trained:
                warnings.append("⚠️ ML is enabled but model not trained. Use `/flagml train` when you have 50+ labeled joins.")

            embed = discord.Embed(title="🚩 ShadyFlags Settings", color=discord.Color.blurple())

            if warnings:
                embed.description = "\n".join(warnings)

            embed.add_field(
                name="Mod Log Channel",
                value=channel.mention if channel else "❌ Not set",
                inline=True
            )
            embed.add_field(name="Auto-Flag Enabled", value="✅ Yes" if settings["auto_flag_enabled"] else "❌ No", inline=True)
            embed.add_field(name="ML Scoring", value="✅ Enabled" if settings.get("ml_enabled") else "❌ Disabled", inline=True)
            embed.add_field(
                name="Mod Roles",
                value=", ".join(mod_role_mentions) if mod_role_mentions else "None (admins + mod perms only)",
                inline=False
            )
            embed.add_field(
                name="Thresholds (flag if account younger than)",
                value=f"🔴 Critical: < {settings.get('threshold_critical_days', 1)} days\n"
                      f"🟠 High: < {settings['threshold_high_days']} days\n"
                      f"🟡 Medium: < {settings['threshold_medium_days']} days",
                inline=False
            )
            embed.add_field(
                name="Auto-Flag Expiry",
                value=f"🔴 Critical: {settings['flag_expiry_critical_days']} days\n"
                      f"🟠 High: {settings['flag_expiry_high_days']} days\n"
                      f"🟡 Medium: {settings['flag_expiry_medium_days']} days",
                inline=False
            )

            embed.set_footer(text=f"v{self.__version__}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif setting == "channel":
            if channel is None:
                await self.config.guild(interaction.guild).mod_log_channel.set(None)
                await interaction.response.send_message("✅ Mod log channel cleared.", ephemeral=True)
                return

            try:
                channel_id = int(channel)
                ch = interaction.guild.get_channel(channel_id)
                if not ch:
                    await interaction.response.send_message("Channel not found.", ephemeral=True)
                    return
                await self.config.guild(interaction.guild).mod_log_channel.set(channel_id)
                await interaction.response.send_message(f"✅ Mod log channel set to {ch.mention}", ephemeral=True)
            except ValueError:
                await interaction.response.send_message("Invalid channel.", ephemeral=True)

        elif setting == "autoflag":
            current = await self.config.guild(interaction.guild).auto_flag_enabled()
            await self.config.guild(interaction.guild).auto_flag_enabled.set(not current)
            status = "enabled" if not current else "disabled"
            await interaction.response.send_message(f"✅ Auto-flagging {status}.", ephemeral=True)

        elif setting == "threshold":
            modal = ThresholdModal(self)
            await interaction.response.send_modal(modal)

        elif setting == "expiry":
            modal = ExpiryModal(self)
            await interaction.response.send_modal(modal)

        elif setting == "addrole":
            if not role:
                await interaction.response.send_message(
                    "Please specify a role to add.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id in roles:
                    await interaction.response.send_message(
                        f"❌ {role.mention} is already a mod role.", ephemeral=True
                    )
                    return
                roles.append(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can now manage flags.", ephemeral=True
            )

        elif setting == "removerole":
            if not role:
                await interaction.response.send_message(
                    "Please specify a role to remove.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id not in roles:
                    await interaction.response.send_message(
                        f"❌ {role.mention} is not a mod role.", ephemeral=True
                    )
                    return
                roles.remove(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can no longer manage flags.", ephemeral=True
            )

    @app_commands.command(name="flagnetwork", description="Manage the known bad actor network")
    @app_commands.describe(
        action="Action to perform",
        user_id="User ID (for add/remove/check actions)",
        reason="Reason for adding (required for add action)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="View Stats", value="stats"),
        app_commands.Choice(name="View Suspects", value="suspects"),
        app_commands.Choice(name="List Confirmed", value="list"),
        app_commands.Choice(name="Scan History (Bootstrap)", value="scan"),
        app_commands.Choice(name="Add User", value="add"),
        app_commands.Choice(name="Remove User", value="remove"),
        app_commands.Choice(name="Check User", value="check"),
        app_commands.Choice(name="Toggle Auto-Parse", value="toggle"),
        app_commands.Choice(name="Clear All (Reset Network)", value="clearall"),
    ])
    async def flagnetwork_cmd(
        self,
        interaction: discord.Interaction,
        action: str,
        user_id: Optional[str] = None,
        reason: Optional[str] = None
    ):
        """Manage the known bad actor network."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        if action == "stats":
            confirmed_stats = await self.get_known_network_stats(interaction.guild)
            suspect_stats = await self.get_suspect_network_stats(interaction.guild)
            jf_stats = await self.get_join_features_stats(interaction.guild)

            embed = discord.Embed(
                title="🕸️ Network Statistics",
                color=discord.Color.blue()
            )

            # Suspect network (pending confirmation)
            embed.add_field(
                name="🔍 Suspect Network",
                value=f"Total: {suspect_stats.get('total', 0)}\n"
                      f"Avg Risk: {suspect_stats.get('avg_risk_score', 0)*100:.0f}%\n"
                      f"High Risk: {suspect_stats.get('high_risk_count', 0)}",
                inline=True
            )

            # Confirmed network
            embed.add_field(
                name="✅ Confirmed Bad Actors",
                value=f"Total: {confirmed_stats.get('total', 0)}",
                inline=True
            )

            # Source breakdown for confirmed
            if confirmed_stats.get("by_source"):
                source_text = "\n".join([
                    f"• {source}: {count}"
                    for source, count in confirmed_stats["by_source"].items()
                ])
                embed.add_field(name="Confirmed By Source", value=source_text, inline=True)

            # ML Training data
            embed.add_field(
                name="📊 ML Training Data",
                value=f"Total joins: {jf_stats.get('total', 0)}\n"
                      f"Labeled: {jf_stats.get('labeled', 0)}\n"
                      f"Action rate: {jf_stats.get('action_rate', 0):.1f}%",
                inline=False
            )

            # Setup hint if empty
            if confirmed_stats.get('total', 0) == 0:
                embed.add_field(
                    name="💡 Getting Started",
                    value="Use `/flagnetwork scan` to scan your mod log history\n"
                          "and bootstrap the confirmed network from past bans.",
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "suspects":
            network = await self.config.guild(interaction.guild).suspect_network()

            if not network:
                await interaction.response.send_message(
                    "No suspects in the network.\n"
                    "High-risk users will be added automatically on join.",
                    ephemeral=True
                )
                return

            # Sort by risk score (highest first)
            sorted_suspects = sorted(
                network.values(),
                key=lambda x: x.get("risk_score", 0),
                reverse=True
            )[:15]

            embed = discord.Embed(
                title="🔍 Suspect Network",
                description="Users with high risk scores pending confirmation",
                color=discord.Color.orange()
            )

            for suspect in sorted_suspects:
                uid = suspect.get("user_id")
                risk = suspect.get("risk_score", 0)
                confidence = suspect.get("confidence", 0)
                factors = suspect.get("top_factors", [])

                factor_str = ", ".join([f[0] for f in factors[:2]]) if factors else "N/A"

                embed.add_field(
                    name=f"Risk: {risk*100:.0f}% | ID: {uid}",
                    value=f"Confidence: {confidence*100:.0f}%\nFactors: {factor_str}",
                    inline=True
                )

            embed.set_footer(text=f"Total suspects: {len(network)} | Showing top {len(sorted_suspects)}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "scan":
            # Scan historical mod log to bootstrap the network
            mod_log_channel_id = await self.config.guild(interaction.guild).mod_log_channel()

            if not mod_log_channel_id:
                await interaction.response.send_message(
                    "❌ No mod log channel set!\n"
                    "Use `/flagset channel` to set one first.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(mod_log_channel_id)
            if not channel:
                await interaction.response.send_message(
                    "❌ Mod log channel not found. Please reconfigure with `/flagset channel`.",
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Scan last 1000 messages in mod log
            scanned = 0
            added = 0
            skipped_no_keyword = 0
            skipped_already_exists = 0
            errors = 0

            try:
                async for message in channel.history(limit=1000):
                    scanned += 1

                    # Only process bot messages (mod bots)
                    if not message.author.bot:
                        continue

                    try:
                        # Check each embed in the message
                        for embed in message.embeds:
                            desc = embed.description or ""
                            desc_lower = desc.lower()

                            # Must be a ban - check description and title
                            is_ban = "ban" in desc_lower
                            if embed.title:
                                is_ban = is_ban or "ban" in embed.title.lower()

                            if not is_ban:
                                continue

                            # Skip unbans
                            if "unban" in desc_lower:
                                continue

                            # Extract reason - look for "Reason:" line
                            reason_text = None
                            reason_match = re.search(r'Reason:\s*([^\n]+)', desc, re.IGNORECASE)
                            if reason_match:
                                reason_text = reason_match.group(1).strip()

                            # Also check embed fields for reason
                            if not reason_text:
                                for field in embed.fields:
                                    if "reason" in field.name.lower():
                                        reason_text = field.value
                                        break

                            # Must have reason with bad actor keywords
                            if not reason_text or not is_bad_actor_action(reason_text):
                                skipped_no_keyword += 1
                                continue

                            # Extract user ID and username from FIRST LINE of description
                            # Jeebs format: "username (user_id)\nCase #XXX | Ban..."
                            # IMPORTANT: Must get ID from first line, NOT from reason text
                            user_id = None
                            username = None
                            first_line_match = re.match(r'^([^\n(]+)\s*\((\d{17,20})\)', desc)
                            if first_line_match:
                                username = first_line_match.group(1).strip()
                                user_id = int(first_line_match.group(2))

                            # Fallback: check embed fields for user ID
                            if not user_id:
                                for field in embed.fields:
                                    field_lower = field.name.lower()
                                    if any(n in field_lower for n in ["user", "member", "target", "offender"]):
                                        user_id = parse_user_id_from_text(field.value)
                                        if user_id:
                                            break

                            if not user_id:
                                continue

                            # Extract moderator ID from "Moderator" line (NOT from reason)
                            mod_id = None
                            mod_match = re.search(r'Moderator[:\s]*[^\n]*\((\d{17,20})\)', desc, re.IGNORECASE)
                            if mod_match:
                                mod_id = int(mod_match.group(1))

                            # Check if already in network
                            existing = await self.is_in_known_network(interaction.guild, user_id)
                            if existing:
                                skipped_already_exists += 1
                                continue

                            # Add to known network with all captured data
                            await self.add_to_known_network(
                                interaction.guild,
                                user_id,
                                source="history_scan",
                                reason=reason_text,
                                mod_action_type="ban",
                                username=username,
                                actioned_by=mod_id,
                                propagate=False  # Don't propagate during bulk scan
                            )
                            added += 1

                    except Exception as e:
                        errors += 1
                        log.debug(f"Error parsing message during scan: {e}")

            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ I don't have permission to read the mod log channel history.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="✅ History Scan Complete",
                color=discord.Color.green()
            )
            embed.add_field(name="Messages Scanned", value=str(scanned), inline=True)
            embed.add_field(name="Bad Actors Added", value=str(added), inline=True)
            embed.add_field(name="Already in Network", value=str(skipped_already_exists), inline=True)
            embed.add_field(name="Skipped (No Keywords)", value=str(skipped_no_keyword), inline=True)
            embed.add_field(name="Parse Errors", value=str(errors), inline=True)

            if added > 0:
                embed.add_field(
                    name="Next Steps",
                    value="The confirmed network has been bootstrapped!\n"
                          "New members will now be checked against this data.",
                    inline=False
                )
            elif skipped_already_exists > 0:
                embed.add_field(
                    name="Note",
                    value="All matching bans were already in the network.",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Note",
                    value="No bans with trigger keywords found.\n"
                          f"Keywords: bot, spam, scam, suspicious, defender, quickaction, etc.",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == "list":
            network = await self.config.guild(interaction.guild).known_network()

            if not network:
                await interaction.response.send_message(
                    "No known bad actors in the network.",
                    ephemeral=True
                )
                return

            # Get most recent 15
            sorted_actors = sorted(
                network.values(),
                key=lambda x: x.get("added_at", ""),
                reverse=True
            )[:15]

            embed = discord.Embed(
                title="🕸️ Known Bad Actors (Recent)",
                color=discord.Color.red()
            )

            for actor in sorted_actors:
                uid = actor.get("user_id")
                source = actor.get("source", "unknown")
                action_type = actor.get("mod_action_type", "")
                actor_reason = actor.get("reason", "No reason")[:50]

                source_emoji = {"mod_action": "🤖", "alt_of_confirmed": "🔗", "manual": "✋"}.get(source, "❓")

                embed.add_field(
                    name=f"{source_emoji} {uid}",
                    value=f"**Action:** {action_type or 'N/A'}\n**Reason:** {actor_reason}",
                    inline=True
                )

            embed.set_footer(text=f"Total: {len(network)} | Showing {len(sorted_actors)}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "add":
            if not user_id:
                await interaction.response.send_message(
                    "Please provide a user ID to add.",
                    ephemeral=True
                )
                return

            try:
                uid = int(user_id.strip())
            except ValueError:
                await interaction.response.send_message(
                    "Invalid user ID. Please provide a numeric ID.",
                    ephemeral=True
                )
                return

            if not reason:
                await interaction.response.send_message(
                    "Please provide a reason for adding this user.",
                    ephemeral=True
                )
                return

            await self.add_to_known_network(
                interaction.guild,
                uid,
                source="manual",
                reason=reason,
                mod_action_type=None
            )

            await interaction.response.send_message(
                f"✅ Added user `{uid}` to the known network.\nReason: {reason}",
                ephemeral=True
            )

        elif action == "remove":
            if not user_id:
                await interaction.response.send_message(
                    "Please provide a user ID to remove.",
                    ephemeral=True
                )
                return

            try:
                uid = int(user_id.strip())
            except ValueError:
                await interaction.response.send_message(
                    "Invalid user ID.",
                    ephemeral=True
                )
                return

            removed = await self.remove_from_known_network(interaction.guild, uid)
            if removed:
                await interaction.response.send_message(
                    f"✅ Removed user `{uid}` from the known network.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"User `{uid}` was not in the known network.",
                    ephemeral=True
                )

        elif action == "check":
            if not user_id:
                await interaction.response.send_message(
                    "Please provide a user ID to check.",
                    ephemeral=True
                )
                return

            try:
                uid = int(user_id.strip())
            except ValueError:
                await interaction.response.send_message(
                    "Invalid user ID.",
                    ephemeral=True
                )
                return

            actor = await self.is_in_known_network(interaction.guild, uid)
            if actor:
                embed = discord.Embed(
                    title="🚨 User in Known Network",
                    description=f"User `{uid}` is a known bad actor.",
                    color=discord.Color.red()
                )
                embed.add_field(name="Source", value=actor.get("source", "Unknown"), inline=True)
                embed.add_field(name="Action", value=actor.get("mod_action_type", "N/A"), inline=True)
                embed.add_field(name="Reason", value=actor.get("reason", "No reason"), inline=False)
                embed.add_field(
                    name="Added",
                    value=f"<t:{int(datetime.fromisoformat(actor.get('added_at', datetime.now(timezone.utc).isoformat())).timestamp())}:R>",
                    inline=True
                )

                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"✅ User `{uid}` is **not** in the known network.",
                    ephemeral=True
                )

        elif action == "toggle":
            current = await self.config.guild(interaction.guild).auto_parse_mod_log()
            await self.config.guild(interaction.guild).auto_parse_mod_log.set(not current)

            status = "enabled" if not current else "disabled"
            await interaction.response.send_message(
                f"✅ Auto-parse mod log is now **{status}**.\n"
                f"{'Mod log messages will be parsed for bad actors.' if not current else 'Mod log parsing stopped.'}",
                ephemeral=True
            )

        elif action == "clearall":
            # Require admin or bot owner for this dangerous action
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ Only administrators can clear the entire network.",
                    ephemeral=True
                )
                return

            # Get current count
            network = await self.config.guild(interaction.guild).known_network()
            count = len(network)

            if count == 0:
                await interaction.response.send_message(
                    "Network is already empty.",
                    ephemeral=True
                )
                return

            # Clear the network
            await self.config.guild(interaction.guild).known_network.set({})

            # Also clear join features (ML training data)
            await self.config.guild(interaction.guild).join_features.set([])

            # Also clear ShadyAlts confirmed/suspects that came from ShadyFlags
            alts_cleared = False
            alts_cog = self.bot.get_cog("ShadyAlts")
            if alts_cog:
                try:
                    # Clear confirmed and suspects lists
                    await alts_cog.config.guild(interaction.guild).confirmed.set({})
                    await alts_cog.config.guild(interaction.guild).suspects.set({})
                    alts_cleared = True
                except Exception as e:
                    log.error(f"Error clearing ShadyAlts data: {e}")

            await interaction.response.send_message(
                f"✅ Cleared **{count}** bad actors from the network.\n"
                f"{'✅ ShadyAlts confirmed/suspects also cleared.' if alts_cleared else '⚠️ ShadyAlts not loaded - clear manually.'}\n"
                f"ML training data has been reset.\n"
                f"Run `/flagnetwork scan` to rebuild from mod log history.",
                ephemeral=True
            )

            await self.log_to_mod_channel(
                interaction.guild,
                f"⚠️ **Network Cleared** by {interaction.user.mention}\n"
                f"**Bad Actors Removed:** {count}\n"
                f"ShadyAlts and ML training data also reset."
            )

    @app_commands.command(name="flagml", description="ML risk scoring management")
    @app_commands.describe(
        action="ML action to perform",
        threshold="Risk threshold for auto-flagging (0.5-0.95)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="View Status", value="status"),
        app_commands.Choice(name="Train Model", value="train"),
        app_commands.Choice(name="Toggle ML Scoring", value="toggle"),
        app_commands.Choice(name="Set Threshold", value="threshold"),
        app_commands.Choice(name="Reset Model", value="reset"),
        app_commands.Choice(name="Test Score (self)", value="test"),
    ])
    async def flagml_cmd(
        self,
        interaction: discord.Interaction,
        action: str,
        threshold: Optional[float] = None
    ):
        """ML risk scoring management."""
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        if action == "status":
            ml_enabled = await self.config.guild(interaction.guild).ml_enabled()
            ml_threshold = await self.config.guild(interaction.guild).ml_threshold()
            ml_critical = await self.config.guild(interaction.guild).ml_critical_threshold()

            risk_model = self.get_risk_model(interaction.guild.id)
            jf_stats = await self.get_join_features_stats(interaction.guild)

            embed = discord.Embed(
                title="🤖 ML Risk Scoring Status",
                color=discord.Color.blue()
            )

            # Status
            status_text = "✅ Enabled" if ml_enabled else "❌ Disabled"
            embed.add_field(name="Status", value=status_text, inline=True)
            embed.add_field(name="sklearn Available", value="✅ Yes" if SKLEARN_AVAILABLE else "❌ No (rule-based)", inline=True)
            embed.add_field(name="Model Trained", value="✅ Yes" if risk_model.is_trained else "❌ No", inline=True)

            # Thresholds
            embed.add_field(
                name="Thresholds",
                value=f"High: {ml_threshold*100:.0f}%\nCritical: {ml_critical*100:.0f}%",
                inline=True
            )

            # Model info
            if risk_model.is_trained:
                embed.add_field(
                    name="Training Data",
                    value=f"Examples: {risk_model.training_examples}\nAccuracy: {risk_model.accuracy*100:.1f}%",
                    inline=True
                )

                # Feature importances
                if risk_model.feature_importances:
                    top_features = sorted(
                        risk_model.feature_importances.items(),
                        key=lambda x: abs(x[1]),
                        reverse=True
                    )[:5]
                    importance_text = "\n".join([
                        f"• {name.replace('_', ' ').title()}: {val:+.2f}"
                        for name, val in top_features
                    ])
                    embed.add_field(name="Top Features", value=importance_text, inline=False)
            else:
                embed.add_field(
                    name="Training Data",
                    value=f"Total joins: {jf_stats.get('total', 0)}\n"
                          f"Labeled: {jf_stats.get('labeled', 0)}\n"
                          f"Need 50+ labeled to train",
                    inline=True
                )

            embed.set_footer(text=f"ShadyFlags v{self.__version__}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "train":
            await interaction.response.defer(ephemeral=True)

            if not SKLEARN_AVAILABLE:
                await interaction.followup.send(
                    "❌ sklearn is not installed. Install it with:\n"
                    "`pip install scikit-learn numpy`\n\n"
                    "Without sklearn, rule-based scoring will be used.",
                    ephemeral=True
                )
                return

            # Get join features
            jf_list = await self.config.guild(interaction.guild).join_features()

            risk_model = self.get_risk_model(interaction.guild.id)
            success, message = risk_model.train(jf_list)

            if success:
                # Save the model
                await self.save_risk_model(interaction.guild.id)

                embed = discord.Embed(
                    title="✅ Model Trained Successfully",
                    description=message,
                    color=discord.Color.green()
                )

                if risk_model.feature_importances:
                    top_features = sorted(
                        risk_model.feature_importances.items(),
                        key=lambda x: abs(x[1]),
                        reverse=True
                    )[:5]
                    importance_text = "\n".join([
                        f"• {name.replace('_', ' ').title()}: {val:+.2f}"
                        for name, val in top_features
                    ])
                    embed.add_field(name="Feature Importances", value=importance_text, inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Training failed: {message}", ephemeral=True)

        elif action == "toggle":
            current = await self.config.guild(interaction.guild).ml_enabled()
            await self.config.guild(interaction.guild).ml_enabled.set(not current)

            status = "enabled" if not current else "disabled"
            method = "ML model" if SKLEARN_AVAILABLE else "rule-based"

            await interaction.response.send_message(
                f"✅ ML risk scoring is now **{status}**.\n"
                f"Using: {method} scoring",
                ephemeral=True
            )

        elif action == "threshold":
            if threshold is None:
                current = await self.config.guild(interaction.guild).ml_threshold()
                critical = await self.config.guild(interaction.guild).ml_critical_threshold()
                await interaction.response.send_message(
                    f"Current thresholds:\n"
                    f"• High flag: {current*100:.0f}%\n"
                    f"• Critical flag: {critical*100:.0f}%\n\n"
                    f"Use `/flagml threshold:<value>` to set the high threshold.",
                    ephemeral=True
                )
                return

            if not 0.5 <= threshold <= 0.95:
                await interaction.response.send_message(
                    "❌ Threshold must be between 0.5 and 0.95",
                    ephemeral=True
                )
                return

            await self.config.guild(interaction.guild).ml_threshold.set(threshold)

            # Auto-set critical threshold to threshold + 0.15 (capped at 0.95)
            critical = min(threshold + 0.15, 0.95)
            await self.config.guild(interaction.guild).ml_critical_threshold.set(critical)

            await interaction.response.send_message(
                f"✅ Thresholds updated:\n"
                f"• High flag: {threshold*100:.0f}%\n"
                f"• Critical flag: {critical*100:.0f}%",
                ephemeral=True
            )

        elif action == "reset":
            # Reset the model
            risk_model = self.get_risk_model(interaction.guild.id)
            self.risk_models[interaction.guild.id] = RiskModel()

            # Delete saved model files
            data_path = cog_data_path(self) / str(interaction.guild.id)
            if data_path.exists():
                import shutil
                try:
                    for f in data_path.glob("risk_model*"):
                        f.unlink()
                except Exception as e:
                    log.error(f"Error deleting model files: {e}")

            await interaction.response.send_message(
                "✅ ML model has been reset. You'll need to retrain.",
                ephemeral=True
            )

        elif action == "test":
            # Test scoring on the user themselves (for demo)
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True
                )
                return

            features = extract_join_features(interaction.user, interaction.guild)
            features_dict = asdict(features)

            # Enrich with network info
            features_dict = await self.enrich_features_with_network(
                features_dict, interaction.guild, interaction.user.id
            )

            risk_model = self.get_risk_model(interaction.guild.id)
            risk_score = risk_model.predict(features_dict)

            embed = discord.Embed(
                title="🔍 Risk Score Test",
                description=f"Simulated risk score for {interaction.user.mention}",
                color=discord.Color.blue()
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="Risk Score", value=f"{risk_score.score*100:.0f}%", inline=True)
            embed.add_field(name="Confidence", value=f"{risk_score.confidence*100:.0f}%", inline=True)
            embed.add_field(name="Method", value=risk_score.method.replace("_", " ").title(), inline=True)

            if risk_score.top_factors:
                factors_display = "\n".join([f"• {f[0]}" for f in risk_score.top_factors])
                embed.add_field(name="Contributing Factors", value=factors_display or "None", inline=False)

            # Show some features
            embed.add_field(
                name="Extracted Features",
                value=f"Account age: {features_dict.get('account_age_hours', 0):.0f}h\n"
                      f"Has avatar: {'Yes' if features_dict.get('has_avatar') else 'No'}\n"
                      f"Username entropy: {features_dict.get('username_entropy', 0):.2f}\n"
                      f"Random pattern: {'Yes' if features_dict.get('username_random_pattern') else 'No'}",
                inline=False
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)


class FlagReviewView(discord.ui.View):
    """View for flag review queue with action buttons."""

    def __init__(self, cog: "ShadyFlags", user_id: int, queue_size: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.queue_size = queue_size

    @discord.ui.button(label="Clear Flags", style=discord.ButtonStyle.success, emoji="✅")
    async def clear_flags(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Clear all flags for this user."""
        await self.cog.clear_flags(interaction.guild.id, self.user_id)
        await self.cog.record_flag_decision(
            interaction.guild.id, 0, self.user_id, "cleared", interaction.user.id
        )

        await interaction.response.send_message(
            f"✅ Cleared all flags for <@{self.user_id}>.",
            ephemeral=True
        )

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"✅ **Flags Cleared** by {interaction.user.mention}\n"
            f"**User:** <@{self.user_id}>\n"
            f"**Decision:** Cleared via review queue"
        )
        self.stop()

    @discord.ui.button(label="Mark False Positive", style=discord.ButtonStyle.secondary, emoji="❎")
    async def false_positive(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Mark as false positive and clear."""
        await self.cog.clear_flags(interaction.guild.id, self.user_id)
        await self.cog.record_flag_decision(
            interaction.guild.id, 0, self.user_id, "false_positive", interaction.user.id
        )

        await interaction.response.send_message(
            f"❎ Marked flags for <@{self.user_id}> as false positive and cleared.",
            ephemeral=True
        )

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"❎ **False Positive** by {interaction.user.mention}\n"
            f"**User:** <@{self.user_id}>\n"
            f"**Decision:** Marked as false positive"
        )
        self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip to next user in queue."""
        await interaction.response.send_message(
            "Skipped. Use `/flagqueue` to continue reviewing.",
            ephemeral=True
        )
        self.stop()


class AddFlagMemberModal(discord.ui.Modal, title="Add Flag"):
    """Modal for adding flag to a member."""

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why are you flagging this user?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    expiry_days = discord.ui.TextInput(
        label="Expiry (days)",
        placeholder="30",
        required=False,
        default="30",
        max_length=3
    )

    def __init__(self, cog: ShadyFlags, member: discord.Member):
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        try:
            days = int(self.expiry_days.value) if self.expiry_days.value else 30
            if days < 1 or days > 365:
                days = 30
        except ValueError:
            days = 30

        flag_id = await self.cog.add_flag(
            interaction.guild.id,
            self.member.id,
            interaction.user.id,
            self.reason.value,
            days
        )

        embed = discord.Embed(
            title="✅ Flag Added",
            description=f"Flag added to {self.member.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(name="Expires", value=f"In {days} days", inline=True)
        embed.add_field(name="Flag ID", value=str(flag_id), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.cog.log_to_mod_channel(
            interaction.guild,
            f"🚩 **Flag Added** by {interaction.user.mention}\n**User:** {self.member.mention}\n**Reason:** {self.reason.value}"
        )


class ThresholdModal(discord.ui.Modal, title="Set Auto-Flag Thresholds"):
    """Modal for setting thresholds."""

    critical = discord.ui.TextInput(label="Critical (days)", placeholder="1", required=False, max_length=3)
    high = discord.ui.TextInput(label="High (days)", placeholder="7", required=False, max_length=3)
    medium = discord.ui.TextInput(label="Medium (days)", placeholder="30", required=False, max_length=3)

    def __init__(self, cog: ShadyFlags):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        updates = []

        if self.critical.value:
            try:
                val = int(self.critical.value)
                if 1 <= val <= 7:
                    await self.cog.config.guild(interaction.guild).threshold_critical_days.set(val)
                    updates.append(f"🔴 Critical: {val} days")
            except ValueError:
                pass

        if self.high.value:
            try:
                val = int(self.high.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).threshold_high_days.set(val)
                    updates.append(f"🟠 High: {val} days")
            except ValueError:
                pass

        if self.medium.value:
            try:
                val = int(self.medium.value)
                if 1 <= val <= 365:
                    await self.cog.config.guild(interaction.guild).threshold_medium_days.set(val)
                    updates.append(f"🟡 Medium: {val} days")
            except ValueError:
                pass

        if updates:
            await interaction.response.send_message(f"✅ Updated thresholds:\n" + "\n".join(updates), ephemeral=True)
        else:
            await interaction.response.send_message("No valid thresholds provided.", ephemeral=True)


class ExpiryModal(discord.ui.Modal, title="Set Auto-Flag Expiry"):
    """Modal for setting flag expiry by priority."""

    critical = discord.ui.TextInput(label="Critical flags expire after (days)", placeholder="14", required=False, max_length=3)
    high = discord.ui.TextInput(label="High flags expire after (days)", placeholder="7", required=False, max_length=3)
    medium = discord.ui.TextInput(label="Medium flags expire after (days)", placeholder="3", required=False, max_length=3)

    def __init__(self, cog: ShadyFlags):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        updates = []

        if self.critical.value:
            try:
                val = int(self.critical.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_critical_days.set(val)
                    updates.append(f"🔴 Critical: {val} days")
            except ValueError:
                pass

        if self.high.value:
            try:
                val = int(self.high.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_high_days.set(val)
                    updates.append(f"🟠 High: {val} days")
            except ValueError:
                pass

        if self.medium.value:
            try:
                val = int(self.medium.value)
                if 1 <= val <= 90:
                    await self.cog.config.guild(interaction.guild).flag_expiry_medium_days.set(val)
                    updates.append(f"🟡 Medium: {val} days")
            except ValueError:
                pass

        if updates:
            await interaction.response.send_message(f"✅ Updated expiry:\n" + "\n".join(updates), ephemeral=True)
        else:
            await interaction.response.send_message("No valid expiry values provided.", ephemeral=True)


async def setup(bot: Red) -> None:
    """Load the ShadyFlags cog."""
    await bot.add_cog(ShadyFlags(bot))