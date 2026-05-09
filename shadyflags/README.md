# ShadyFlags

Temporary warning/flag system with automatic new-account flagging based on account age thresholds.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadyflags
[p]load shadyflags
```

## Commands

### Moderation Commands (Slash Only)

| Command | Description |
|---------|-------------|
| `/flag add <user>` | Add flag to member (opens modal) |
| `/flag view <user>` | View flags for member |
| `/flag remove <user> <flag_id>` | Remove specific flag |
| `/flag clear <user>` | Clear all flags |
| `/flagid add/view/remove/clear <id>` | Same commands by user ID |
| `/flagall` | View all flagged members |
| `/flagqueue` | Review flagged users with action buttons |
| `/flagstats` | View flag statistics |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/flagset view` | Show current settings |
| `/flagset channel` | Set mod log channel |
| `/flagset autoflag` | Toggle auto-flagging |
| `/flagset threshold` | Set account age thresholds |
| `/flagset expiry` | Set flag expiry durations |
| `/flagset addrole @Role` | Add moderator role |
| `/flagset removerole @Role` | Remove moderator role |

## Setup

1. Load the cog: `[p]load shadyflags`
2. Set log channel: `/flagset channel`
3. Configure thresholds (optional): `/flagset threshold`

## Auto-Flag System

New accounts are automatically flagged based on age:

| Priority | Account Age | Default Expiry |
|----------|-------------|----------------|
| 🔴 Critical | < 1 day | 14 days |
| 🟠 High | < 7 days | 7 days |
| 🟡 Medium | < 30 days | 3 days |

## Features

- **Auto-Flagging**: New accounts flagged on join
- **Priority Levels**: Critical, High, Medium, Manual
- **Review Queue**: Action buttons to clear/mark false positive
- **Statistics**: Track flag counts and decisions
- **Decision Logging**: Records for ML training data
- **Configurable Thresholds**: Customize age limits and expiry

## Review Queue

The `/flagqueue` command shows flagged users with action buttons:
- ✅ **Clear Flags**: User is legitimate
- ❎ **False Positive**: Mark as incorrect flag
- ⏭️ **Skip**: Review later

## License

MIT License
