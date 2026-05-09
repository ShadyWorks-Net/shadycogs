from .fafo import Fafo


async def setup(bot):
    await bot.add_cog(Fafo(bot))
