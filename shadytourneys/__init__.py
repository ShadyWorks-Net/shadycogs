from redbot.core.bot import Red

from .shadytourneys import ShadyTourneys


async def setup(bot: Red) -> None:
    """Load the ShadyTourneys cog."""
    await bot.add_cog(ShadyTourneys(bot))
