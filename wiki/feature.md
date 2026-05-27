# Wiki

Community helper cog with server rules and info commands.

## Overview

Wiki provides quick-access commands for moderators to share server rules, hosting guidelines, and helpful info with members. All commands work as both slash commands and prefix commands.

## Setup

### 1. Load the Cog
```
[p]load wiki
[p]slash sync
```

### 2. (Optional) Add Authorized Roles
```
[p]wikiset addrole @Moderator mod
[p]wikiset addrole @Admin admin
```

## Commands

| Command | Description |
|---------|-------------|
| `/rule <1-11>` | Show a specific server rule |
| `/host` | Link to hosting/advertising guidelines |
| `/hosted` | Link to community-run servers channel |
| `/colors` | Show server colors/levels info |
| `/noaccess` | Explain genre-based channel access |
| `/promote` | Explain content promotion via Linked Roles |

### Setup Commands

| Command | Description |
|---------|-------------|
| `[p]wikiset addrole <role> [mod/admin]` | Add role that can use wiki commands |
| `[p]wikiset removerole <role>` | Remove role from wiki access |
| `[p]wikiset listroles` | List authorized roles |

## How It Works

1. Moderator sees a question about rules/access
2. Moderator runs the appropriate command (e.g., `/rule 6`)
3. Bot posts a formatted response with the relevant info
4. Prefix commands auto-delete the invoking message

## Server Rules (1-11)

1. Be Respectful
2. 18+ Only
3. Be Civil & Read The Room
4. NSFW Content Is Not Allowed
5. Communication
6. Use Our Channels & Roles Properly
7. Promoting Content
8. Crowdfunding and Solicitation
9. Unauthorized Links are Not Allowed
10. Build-A-VC Channel Names
11. Staff Respect & Jurisdiction

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Red Mod/Admin | Use wiki commands |
| Configured Roles | Use wiki commands |
