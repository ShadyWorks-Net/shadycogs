from .shadydocs import ShadyDocs


async def setup(bot):
    await bot.add_cog(ShadyDocs(bot))
