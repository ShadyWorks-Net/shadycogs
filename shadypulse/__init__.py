from .shadypulse import ShadyPulse


async def setup(bot):
    await bot.add_cog(ShadyPulse(bot))
