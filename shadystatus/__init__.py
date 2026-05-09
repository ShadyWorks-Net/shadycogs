from .shadystatus import ShadyStatus


async def setup(bot):
    await bot.add_cog(ShadyStatus(bot))
