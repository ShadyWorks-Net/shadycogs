# ShadyStatus

Multi-game server status queries with game-specific formatting. Query game servers via Steam A2S protocol and display rich status embeds.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadystatus
[p]load shadystatus
```

**Requirements:** `python-a2s`

## Supported Games

| Game | Code | Special Fields |
|------|------|----------------|
| 7 Days to Die | `7dtd` | Day number, time, zombies |
| ARK: Survival Evolved | `ark` | Mod list, cluster info |
| Rust | `rust` | FPS, entities, queue |
| Valheim | `valheim` | World name, modded status |
| Project Zomboid | `pz` | Mod count, PvP status |
| V Rising | `vrising` | Blood moon, castles |
| Generic A2S | `generic` | Standard fields only |

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/server <name>` | Query a server (with autocomplete) |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/shadystatus add <name> <ip> <port> <game>` | Add a server |
| `/shadystatus remove <name>` | Remove a server |
| `/shadystatus list` | List all configured servers |
| `/shadystatus show` | Show settings |

## Setup

1. Load the cog: `[p]load shadystatus`
2. Add your servers:
   ```
   /shadystatus add survival 192.168.1.100 27015 7dtd
   /shadystatus add arkserver ark.example.com 27015 ark
   ```
3. Users can now query: `/server survival`

## Features

- **Autocomplete**: Server names autocomplete as users type
- **Rate Limiting**: Per-user cooldowns prevent spam
- **Game-Specific Display**: Each game shows relevant fields
- **Offline Detection**: Shows clear offline status

## Example Output

```
🎮 My 7DTD Server
━━━━━━━━━━━━━━━━━━━━━━━
Status: 🟢 Online
Players: 12/24
Map: Navezgane
Day: 42 (14:30)
Ping: 45ms
```

## License

MIT License
