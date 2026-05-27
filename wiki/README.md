# Wiki Cog - Community Helper

A Discord bot cog for community management with server rules, hosting guidelines, and helpful info commands.

Created for the Parental Advisory (PA) Discord - "Parents That Game".

## Features

- **Server Rules Reference** - Quick `/rule <number>` command for rules 1-11
- **Wiki Links** - Commands for hosting guidelines, colors info, etc.
- **Slash Commands** - Modern Discord slash command support
- **Role-Based Authorization** - Uses Red's mod/admin system

---

## Installation

```bash
[p]load wiki
[p]slash sync
```

---

## Commands

### Slash Commands
| Command | Description |
|---------|-------------|
| `/rule <number>` | Show specific server rule (1-11) |
| `/host` | Link to hosting/advertising guidelines |
| `/hosted` | Link to community-run servers channel |
| `/colors` | Show server colors/levels info |
| `/noaccess` | Explain how to access channels (genre system) |
| `/promote` | Explain how to promote content via Linked Roles |

### Prefix Commands
All slash commands also work with prefix (`[p]rule`, `[p]host`, etc.)

### Setup Commands
| Command | Description |
|---------|-------------|
| `[p]wikiset addrole <role> [mod/admin]` | Add a role that can use wiki commands |
| `[p]wikiset removerole <role>` | Remove a role from wiki access |
| `[p]wikiset listroles` | List authorized roles |

---

## Authorization

Users can use wiki commands if they have:
- Bot owner status
- Administrator permission
- Red's mod or admin role
- A role added via `[p]wikiset addrole`

---

## Support

- **Author**: ShadyTidus
