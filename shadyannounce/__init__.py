from .shadyannounce import ShadyAnnounce

async def setup(bot):
    await bot.add_cog(ShadyAnnounce(bot))
