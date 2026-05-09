from redbot.core.bot import Red

from .shadyalts import ShadyAlts


async def setup(bot: Red) -> None:
    """Load the ShadyAlts cog."""
    await bot.add_cog(ShadyAlts(bot))
