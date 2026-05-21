# ShadyAnnounce

Scheduled announcements cog allowing staff to schedule posts to channels using their personal timezone.

## Overview

ShadyAnnounce lets authorized users schedule announcements to Discord channels with:
- Personal timezone settings (one-time setup)
- Preview/edit workflow before confirming
- Role-based access control
- Background posting at scheduled times

## Setup

### 1. Load the Cog
```
[p]load shadyannounce
```

### 2. Configure Allowed Roles (Optional)
By default, only administrators can schedule announcements. To allow other roles:
```
/announceset addrole @Moderators
```

### 3. Set Your Timezone
Each user must set their timezone before scheduling:
```
/mytime
```
Select from the dropdown of common timezones (US, EU, Asia/Pacific).

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/mytime` | Set your personal timezone (required before scheduling) |
| `/announce` | Schedule an announcement to the current channel |
| `/announcelist` | View pending scheduled announcements |
| `/announcecancel <id>` | Cancel a scheduled announcement |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/announceset addrole <role>` | Add a role that can schedule announcements |
| `/announceset removerole <role>` | Remove a role from scheduling |
| `/announceset view` | View current settings |

## User Flow

### First-Time Setup
1. Run `/mytime`
2. Select your timezone from the dropdown
3. Done! You only need to do this once

### Scheduling an Announcement
1. Go to the channel where you want the announcement posted
2. Run `/announce`
3. Fill in the modal:
   - **Date & Time**: Enter in your timezone (e.g., `2024-01-15 14:30` or `Jan 15, 2024 2:30 PM`)
   - **Content**: Your announcement text (supports Discord markdown)
4. Review the preview:
   - See the target channel
   - See the scheduled time as a Discord timestamp
   - See how your content will look
5. Click **Confirm** to schedule, **Edit** to modify, or **Cancel** to abort
6. The announcement will be posted automatically at the scheduled time

### Managing Announcements
- Use `/announcelist` to see pending announcements
- Use `/announcecancel <id>` to cancel a scheduled announcement
- Admins can see and cancel all announcements; regular users see only their own

## Date/Time Formats

The following formats are supported (in your timezone):

| Format | Example |
|--------|---------|
| `YYYY-MM-DD HH:MM` | `2024-01-15 14:30` |
| `YYYY-MM-DD h:MM AM/PM` | `2024-01-15 2:30 PM` |
| `MM/DD/YYYY HH:MM` | `01/15/2024 14:30` |
| `MM/DD/YYYY h:MM AM/PM` | `01/15/2024 2:30 PM` |
| `DD-MM-YYYY HH:MM` | `15-01-2024 14:30` |
| `Mon DD, YYYY h:MM AM/PM` | `Jan 15, 2024 2:30 PM` |
| `Month DD, YYYY h:MM AM/PM` | `January 15, 2024 2:30 PM` |

## Supported Timezones

- **US**: Eastern, Central, Mountain, Pacific, Alaska, Hawaii
- **Europe**: UK, Paris, Berlin, Amsterdam, Helsinki, Moscow
- **Asia/Pacific**: Tokyo, Shanghai, Singapore, Sydney, Perth, Auckland
- **Other**: UTC

## Content Formatting

Announcements support standard Discord markdown:
- **Bold**: `**text**`
- *Italic*: `*text*`
- ~~Strikethrough~~: `~~text~~`
- ||Spoiler||: `||text||`
- `Code`: `` `code` ``
- > Quotes: `> text`
- Links: `https://example.com`
- Mentions: `@user`, `@role`, `#channel`

## Technical Notes

- Announcements are stored in UTC and converted for display
- Background task checks every 3 minutes for due announcements
- History is kept for the last 50 posted announcements (configurable)
- Timezone data uses Python's built-in `zoneinfo` module

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Guild Owner | Everything |
| Administrator | Everything |
| Manage Guild | Schedule, view own, cancel own |
| Allowed Roles | Schedule, view own, cancel own |
| Everyone else | Nothing |

## Troubleshooting

**"Please set your timezone first"**
- Run `/mytime` and select your timezone

**"Invalid timezone stored"**
- Run `/mytime` again to reset your timezone

**"I don't have permission to send messages"**
- Make sure the bot has Send Messages permission in the target channel

**Announcement didn't post**
- Check that the bot is online and has permissions
- Announcements are checked every 3 minutes, so there may be a slight delay
