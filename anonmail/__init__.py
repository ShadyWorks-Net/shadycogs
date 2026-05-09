from redbot.core.bot import Red

from .anonmail import AnonMail


async def setup(bot: Red) -> None:
    await bot.add_cog(AnonMail(bot))
