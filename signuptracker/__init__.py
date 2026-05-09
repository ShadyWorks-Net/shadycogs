from .signuptracker import SignupTracker


async def setup(bot):
    await bot.add_cog(SignupTracker(bot))
