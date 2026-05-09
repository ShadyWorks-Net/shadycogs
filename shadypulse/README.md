# ShadyPulse

Comprehensive health monitoring for your bot, external services, and other cogs. Features dashboard, alerts, uptime tracking, and automatic cog reload on failure.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadypulse
[p]load shadypulse
```

**Requirements:** `aiohttp`

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/pulse` | View health dashboard with all monitored services |
| `/uptime` | Show bot uptime |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/shadypulse alertchannel #channel` | Set channel for downtime alerts |
| `/shadypulse http add <name> <url>` | Add HTTP service to monitor |
| `/shadypulse http remove <name>` | Remove HTTP service |
| `/shadypulse http list` | List monitored HTTP services |
| `/shadypulse cog add <CogName>` | Add cog to monitor |
| `/shadypulse cog remove <CogName>` | Stop monitoring a cog |
| `/shadypulse cog list` | List monitored cogs |
| `/shadypulse setextension <CogName> <ext>` | Set extension name for cog reload |
| `/shadypulse autoreload <true/false>` | Enable/disable auto-reload on cog failure |
| `/shadypulse reset <CogName>` | Reset failure count for a cog |
| `/shadypulse show` | Show current configuration |

## Setup

1. Load the cog: `[p]load shadypulse`
2. Set alert channel: `/shadypulse alertchannel #alerts`
3. Add services to monitor:
   - HTTP: `/shadypulse http add api https://api.example.com/health`
   - Cogs: `/shadypulse cog add LevelUp`

## Features

- **Bot Health**: Monitors latency, shard status, uptime
- **HTTP Services**: Periodic health checks with configurable intervals
- **Cog Monitoring**: Detects cog crashes and can auto-reload
- **Alert System**: Posts to configured channel when services go down
- **Uptime Stats**: Tracks historical uptime percentages

## Auto-Reload

When enabled, ShadyPulse will attempt to reload crashed cogs:
- Maximum 3 retry attempts per cog
- 60-second cooldown between retries
- Alerts sent on failure and recovery

## License

MIT License
