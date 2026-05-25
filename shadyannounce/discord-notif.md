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

### Basic Announcement
1. Go to the channel where you want to post
2. Run `/announce`
3. Enter the **date** (e.g., `5/25` or `May 25`)
4. Enter the **time** (e.g., `2pm` or `2:00 pm`)
5. Write your **content**
6. Click **Confirm** to schedule

### Auto-Parsing (Type Directly)
You can type timestamps and mentions directly in your content - they auto-convert when you confirm:

**Timestamps with `@time(...)`:**
```
Event starts @time(May 25 2pm) - don't be late!
```
Converts to a clickable Discord timestamp showing in each user's local time.

Supported formats inside `@time()`:
- `May 25 2pm` or `Jan 15 3:30pm`
- `tomorrow 3pm`
- `in 2 hours`

**Mentions with `@Name`:**
```
Hey @Moderators! Contact @JohnDoe for details.
```
Automatically converts to proper Discord mentions if the role/user exists.

### Using Placeholders (Button Method)
Alternatively, use placeholders that get replaced when you click the buttons:

| Placeholder | Button | Result |
|-------------|--------|--------|
| `(time)` | Add Timestamp | Replaced with Discord timestamp |
| `(role)` | Add Mention | Replaced with role mention |
| `(user)` | Add Mention | Replaced with user mention |

**Example content:**
```
Hey (role)! Join us for our event (time).
Contact (user) with questions.
```

Then click the buttons to fill in the placeholders.

### Adding Timestamps
1. Write content with `(time)` where you want the timestamp
2. Click **Add Timestamp**
3. Enter the date and time for the event
4. The `(time)` placeholder is replaced with a clickable Discord timestamp

If you don't use `(time)`, the timestamp is added on a new line.

### Adding Mentions
1. Write content with `(role)` or `(user)` where you want the mention
2. Click **Add Mention**
3. Type the exact role or username
4. The placeholder is replaced with the mention

If you don't use placeholders, mentions are added on a new line.

### Preview and Edit
- **Preview** shows exactly how your announcement will look
- Click **Edit** to go back and make changes
- Click **Confirm** when satisfied
- Click **Cancel** to discard

## Commands

- `/mytime` - Set your timezone
- `/announce` - Schedule an announcement
- `/announcelist` - View pending posts
- `/announcecancel <id>` - Cancel a scheduled post

## Feedback Welcome

Try it out and let us know what you think! Report any issues or suggestions.
