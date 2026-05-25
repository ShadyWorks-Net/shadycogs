# New Feature: Scheduled Announcements

Schedule announcements to post automatically at a specific time!

## What's New

**ShadyAnnounce** lets you schedule posts to any channel using your personal timezone. No more setting alarms or being online at weird hours.

## Key Benefits

- **Timezone-aware** - Set your timezone once, then schedule in your local time
- **Preview before posting** - See exactly how your announcement will look
- **Edit until perfect** - Modify your announcement before confirming
- **Manage your posts** - View and cancel scheduled announcements anytime

## How to Use

1. **Set your timezone** (one-time): `/mytime`
2. **Go to target channel** and run: `/announce`
3. **Fill in the details**: date/time + content
4. **Preview and confirm**

Your announcement will be posted automatically at the scheduled time.

## Step-by-Step Flow

1. Go to channel → run `/announce`
2. Enter **date** (`5/25` or `May 25`) and **time** (`2pm`)
3. Write content → **Confirm** to schedule

### Auto-Parsing
Type these directly - they convert automatically:

- `@time(May 25 2pm)` → Discord timestamp
- `@RoleName` or `@Username` → mentions

**Formats for @time():** `May 25 2pm`, `tomorrow 3pm`, `in 2 hours`

### Placeholders (Button Method)
Use `(time)`, `(role)`, `(user)` placeholders, then click buttons to fill them:
```
Hey (role)! Event starts (time). Contact (user).
```
Click **Add Timestamp** or **Add Mention** to replace each placeholder.

## Commands

- `/mytime` - Set your timezone
- `/announce` - Schedule an announcement
- `/announcelist` - View pending posts
- `/announcecancel <id>` - Cancel a scheduled post

## Feedback Welcome

Try it out and let us know what you think! Report any issues or suggestions.
