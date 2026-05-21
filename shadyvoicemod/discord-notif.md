# Voice Moderation

Timed voice mutes with automatic expiry!

## What's New

**ShadyVoiceMod** provides a complete voice muting system with pending mutes, DM notifications, and audit logging.

## Key Features

- **Timed Mutes** - Set duration (30m, 2h, 1d, etc.)
- **Auto-Expiry** - Mutes automatically removed when time's up
- **DM Notifications** - Users are informed via DM
- **Pending Mutes** - Works even if user isn't in voice
- **Audit Log** - All actions logged for transparency
- **Mute Extension** - Extend existing mutes as needed

## For Moderators

### Mute Someone
```
/vmute @user
```
Fill in duration and reason in the modal.

### Check Active Mutes
```
/vmutes
```

### Remove a Mute Early
```
/vunmute @user
```

## How It Works

1. Moderator issues timed mute
2. User receives DM with reason and expiry
3. If in voice, mute applied immediately
4. If not in voice, mute applied when they join
5. Mute auto-expires and user is notified

Keeps voice channels civil with full transparency.
