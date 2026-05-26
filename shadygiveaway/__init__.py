import traceback
import logging

log = logging.getLogger("red.shadycogs.shadygiveaway")

_IMPORT_ERROR = None
_IMPORT_TRACEBACK = None

try:
    from .shadygiveaway import setup
except Exception as e:
    _IMPORT_ERROR = e
    _IMPORT_TRACEBACK = traceback.format_exc()

    from redbot.core.bot import Red

    async def setup(bot: Red):
        """Setup failed due to import error - DM owner with traceback."""
        error_msg = (
            f"**ShadyGiveaway failed to load!**\n\n"
            f"**Error:** `{_IMPORT_ERROR}`\n\n"
            f"**Traceback:**\n```py\n{_IMPORT_TRACEBACK[-1700:]}\n```\n\n"
            f"**If cryptography is missing, run:**\n`[p]pipinstall cryptography`"
        )
        # Try to DM bot owner(s)
        try:
            owner_ids = []
            if hasattr(bot, 'owner_id') and bot.owner_id:
                owner_ids.append(bot.owner_id)
            if hasattr(bot, 'owner_ids') and bot.owner_ids:
                owner_ids.extend(bot.owner_ids)

            for owner_id in owner_ids:
                try:
                    owner = await bot.get_or_fetch_user(owner_id)
                    if owner:
                        await owner.send(error_msg)
                        log.info(f"Sent import error to owner {owner_id}")
                        break
                except Exception:
                    continue
        except Exception as dm_err:
            log.error(f"Could not DM owner about import error: {dm_err}")

        raise _IMPORT_ERROR
