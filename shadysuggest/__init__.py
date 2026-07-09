from redbot.core.bot import Red

from .shadysuggest import ShadySuggest


async def setup(bot: Red) -> None:
    """Load the ShadySuggest cog."""
    await bot.add_cog(ShadySuggest(bot))
