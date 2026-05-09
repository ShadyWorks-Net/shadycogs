# FAFO Cog

**F**ind **A**round **F**ind **O**ut - A moderation tool that posts a warning message with a button. Users who click the button get timed out.

## Installation

```
[p]repo add myredcogs <repo_url>
[p]cog install myredcogs fafo
[p]load fafo
```

## Commands

| Command | Description |
|---------|-------------|
| `[p]fafo` | Post a warning message with the FAFO button |
| `[p]fafoset timeout <minutes>` | Set timeout duration (1-1440 minutes) |
| `[p]fafoset message <text>` | Set the warning message |
| `[p]fafoset show` | Show current settings |

## Permissions

- `[p]fafo` requires `Moderate Members` permission
- `[p]fafoset` commands require `Administrator` permission
- Bot needs `Moderate Members` permission to timeout users

## Configuration

Default timeout: 5 minutes
Default message:
```
__**Warning:**__
If you cannot abide by the rules from previous responses,
**Click Below To FAFO**
```

## How It Works

1. Admin uses `[p]fafo` in a channel
2. Bot posts warning message with red "FAFO" button
3. Any user who clicks the button gets timed out
4. Message auto-deletes after 3 minutes if no one clicks

## License

MIT License
