# ShadyVoiceMod

Voice channel moderation with timed mutes, automatic expiry, DM notifications, and audit logging.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadyvoicemod
[p]load shadyvoicemod
```

## Commands

### Moderation Commands

| Command | Description |
|---------|-------------|
| `/vmute <user>` | Voice mute a user (opens modal for duration/reason) |
| `/vunmute <user>` | Remove a voice mute |
| `/vmutes` | List all active voice mutes |
| `[p]vmodinfo` | Show help and current settings |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/vmodset logchannel #channel` | Set mod log channel |
| `/vmodset addrole @Role` | Add a moderator role |
| `/vmodset removerole @Role` | Remove a moderator role |
| `/vmodset show` | Show current settings |

## Setup

1. Load the cog: `[p]load shadyvoicemod`
2. Set log channel: `/vmodset logchannel #mod-logs`
3. (Optional) Add mod roles: `/vmodset addrole @Moderator`

## Features

- **Timed Mutes**: Set duration in minutes, hours, or days
- **Auto-Expiry**: Background task automatically unmutes when time expires
- **DM Notifications**: Users receive DM when muted/unmuted
- **Pending Mutes**: If user isn't in voice, mute applies when they join
- **Mute Extension**: Extend existing mutes without removing first
- **Audit Logging**: All actions logged to configured channel

## Permissions

- Moderators need `Mute Members` permission OR a configured mod role
- Bot needs `Mute Members` permission
- Administrators always have access

## How It Works

1. Moderator uses `/vmute @user`
2. Modal opens for duration and reason
3. User is server muted in their current voice channel
4. User receives DM notification
5. Action is logged to mod channel
6. Mute automatically expires after duration

## License

MIT License
