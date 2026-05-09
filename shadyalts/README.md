# ShadyAlts

Track alt accounts with bidirectional linking, network visualization, and join/leave notifications.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadyalts
[p]load shadyalts
```

## Commands

### Moderation Commands (Slash Only)

| Command | Description |
|---------|-------------|
| `/alt mark <user> <alt>` | Link two accounts as alts |
| `/alt unmark <user> <alt>` | Remove alt link |
| `/alt view <user>` | View user's alt network |
| `/alt network <user>` | Visualize full alt network |
| `/altid mark <id1> <id2>` | Link by user ID (for users not in server) |
| `/altid unmark <id1> <id2>` | Remove link by ID |
| `/altid view <id>` | View alts by ID |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/altset channel #channel` | Set mod log channel |
| `/altset addrole @Role` | Add moderator role |
| `/altset removerole @Role` | Remove moderator role |
| `/altset show` | Show settings |

## Setup

1. Load the cog: `[p]load shadyalts`
2. Set log channel: `/altset channel #mod-logs`
3. (Optional) Add mod roles: `/altset addrole @Moderator`

## Features

- **Bidirectional Linking**: A↔B means both are linked
- **Network View**: See entire alt network, not just direct links
- **Reason Tracking**: Store why accounts were linked
- **Join/Leave Alerts**: Notified when known alts join or leave
- **Out-of-Server Support**: Link users by ID even if not in server

## Use Cases

- Track ban evaders
- Monitor suspicious account clusters
- Document bot/spam networks
- Keep records of shared accounts

## Example

```
/alt mark @SuspiciousUser @NewAccount
Reason: Same IP in ban appeal

Network for SuspiciousUser:
├─ NewAccount (linked by Mod, 2 days ago)
├─ OldBannedAlt (linked by Admin, 30 days ago)
└─ AnotherAlt (linked by Mod, 15 days ago)
```

## License

MIT License
