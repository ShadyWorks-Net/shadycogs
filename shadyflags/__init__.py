from redbot.core.bot import Red

from .shadyflags import ShadyFlags


async def setup(bot: Red) -> None:
    """Load the ShadyFlags cog."""
    await bot.add_cog(ShadyFlags(bot))
