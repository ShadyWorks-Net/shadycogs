# Wiki

Server information and FAQ commands.

## Overview

Provides quick-access wiki commands for server information, rules, game-specific channels, and common questions. Configuration is stored in JSON files for easy customization.

## Setup

### 1. Load the Cog
```
[p]load wiki
```

### 2. Configure JSON Files
Edit the JSON files in the `config/` folder:
- `roles.json` - Authorized roles
- `games.json` - Game aliases
- `channels.json` - Channel mappings
- `commands.json` - Command configurations
- `rules.json` - Server rules

## Commands

Commands depend on your JSON configuration. Common patterns include:

| Command | Description |
|---------|-------------|
| `[p]rules <number>` | Show specific rule |
| `[p]game <alias>` | Get channel for a game |
| Various info commands | Configured per-server |

## Configuration Files

### roles.json
```json
{
  "authorized_roles": ["Moderator", "Admin"]
}
```

### games.json
```json
{
  "alias_to_role": {
    "mc": "Minecraft",
    "7dtd": "7 Days to Die"
  }
}
```

### channels.json
```json
{
  "role_to_channel": {
    "Minecraft": 123456789012345678
  }
}
```

### rules.json
```json
{
  "rules": {
    "1": "Be respectful to all members",
    "2": "No spam or self-promotion"
  }
}
```

## Features

- **Reply Support** - Commands can reply to the referenced message
- **Auto-Delete** - Original command message deleted
- **Role-Based Auth** - Only authorized roles can use commands

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Authorized Roles | Use wiki commands |
| Everyone Else | Cannot use commands |
