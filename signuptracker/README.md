# SignupTracker Cog

Track reactions on announcement posts. When someone posts in a watched announcements channel, the cog creates a tracker message in a log channel that maintains an ordered list of users who react.

## Installation

```
[p]repo add myredcogs <repo_url>
[p]cog install myredcogs signuptracker
[p]load signuptracker
```

## How It Works

1. Configure an announcements channel to watch
2. Configure a log channel/thread where tracker messages appear
3. When a non-bot user posts in the announcements channel:
   - A new tracker message is created in the log channel
   - The tracker shows "No signups yet."
4. When users react to the announcement:
   - Their name is added to the tracker in order of reaction
5. When users remove their reaction:
   - Their name is removed from the tracker
6. Order is preserved - first reactor stays #1

## Setup

1. Set the announcements channel to watch:
   ```
   [p]signuptracker announcements #announcements
   ```

2. Set the log channel where trackers appear:
   ```
   [p]signuptracker log #signup-log
   ```

3. (Optional) Customize the tracker title:
   ```
   [p]signuptracker title "Event Signups"
   ```

4. Enable tracking:
   ```
   [p]signuptracker enable true
   ```

## Commands

All commands require admin permissions or `manage_guild`.

| Command | Description |
|---------|-------------|
| `[p]signuptracker enable <true/false>` | Enable/disable tracking |
| `[p]signuptracker announcements <channel>` | Set channel to watch |
| `[p]signuptracker log <channel>` | Set tracker output channel/thread |
| `[p]signuptracker title <text>` | Set tracker message title |
| `[p]signuptracker numbers <true/false>` | Toggle numbered list |
| `[p]signuptracker status` | Show current configuration |
| `[p]signuptracker reset` | Reset tracking data |

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| enabled | false | Whether tracking is active |
| announcements_channel | None | Channel to watch for posts |
| log_channel | None | Channel/thread for tracker messages |
| tracker_title | "Signup Tracker" | Title shown on tracker messages |
| show_numbers | true | Show numbered list vs plain list |

## Example Output

When `show_numbers` is true:
```
**Event Signups**
1. @User1
2. @User2
3. @User3
```

When `show_numbers` is false:
```
**Event Signups**
@User1
@User2
@User3
```

## Use Cases

- DnD session signups
- Event RSVPs
- Limited slot registrations
- Any scenario where order of interest matters

## Notes

- Only tracks the most recent announcement in the configured channel
- Tracking data is stored in memory and resets on cog reload
- Each guild has independent configuration
- Works with threads as log destination

## License

MIT License
