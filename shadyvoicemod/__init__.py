"""ShadyVoiceMod - Voice moderation cog for RedBot."""

from redbot.core.bot import Red

from .shadyvoicemod import ShadyVoiceMod


async def setup(bot: Red) -> None:
    """Load ShadyVoiceMod cog."""
    await bot.add_cog(ShadyVoiceMod(bot))
