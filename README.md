# ShadyCogs

A collection of [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) cogs for moderation, events, server utilities, and more.

## Installation

```
[p]repo add shadycogs https://github.com/ShadyWorks-Net/shadycogs
[p]cog install shadycogs <cogname>
[p]load <cogname>
```

## Available Cogs

| Cog | Description |
|-----|-------------|
| **[AnonMail](anonmail/)** | Anonymous feedback submission system with cooldowns and abuse prevention |
| **[FAFO](fafo/)** | "Find Around Find Out" timeout button for moderation |
| **[ShadyAlts](shadyalts/)** | Alt account tracking and network visualization |
| **[ShadyDocs](shadydocs/)** | In-Discord wiki and documentation system |
| **[ShadyFlags](shadyflags/)** | Temporary warning/flag system with auto-flagging for new accounts |
| **[ShadyPulse](shadypulse/)** | Health monitoring for bot, external services, and cogs |
| **[ShadyStatus](shadystatus/)** | Multi-game server status (7DTD, ARK, Rust, Valheim, etc.) |
| **[ShadyTourneys](shadytourneys/)** | Tournament brackets with Challonge integration |
| **[ShadyVoiceMod](shadyvoicemod/)** | Voice moderation with timed mutes and audit logging |
| **[SignupTracker](signuptracker/)** | Track reactions on announcements with first-come-first-served ordering |

## Cog Details

### AnonMail
Anonymous feedback system with configurable recipients, per-user cooldowns, and a ban list for repeat abusers. Feedback is posted to dedicated threads.

### FAFO
Simple moderation tool that posts a warning message with a red button. Users who click it get automatically timed out. Configurable timeout duration and message.

### ShadyAlts
Track alt accounts with bidirectional linking and network visualization. Supports both in-server members and out-of-server users by ID. Features join/leave notifications for known alts.

### ShadyDocs
Simple in-Discord documentation/wiki for servers without external hosting. Create, edit, and organize doc pages with categories. Features autocomplete, search, and embed formatting.

### ShadyFlags
Comprehensive flag system for tracking suspicious accounts. Auto-flags new accounts based on age thresholds (critical/high/medium). Features flag review queue UI and statistics.

### ShadyPulse
Monitor bot health (latency, uptime), external HTTP services, and other cogs. Features dashboard command, alert channels, uptime statistics, and optional cog auto-reload on failure.

**Requirements:** `aiohttp`

### ShadyStatus
Query game servers via Steam A2S protocol with game-specific formatting. Supports 7 Days to Die, ARK, Rust, Valheim, Project Zomboid, V Rising, and generic A2S servers.

**Requirements:** `python-a2s`

### ShadyTourneys
Tournament management with solo/team modes, multiple bracket formats (single/double elimination, round robin), and Challonge API integration for hosted bracket images. Features ELO tracking, match scheduling, and prize pool tracking.

**Requirements:** `pychallonge`

### ShadyVoiceMod
Voice moderation with timed mutes, automatic expiry, DM notifications, and audit logging. Features modal-based UI and handles pending mutes for users not in voice.

### SignupTracker
Track reactions on announcement posts with persistent storage. Features signup history, statistics, export to JSON/CSV, deadline reminders, and first-come-first-served order tracking.

## Support

- **Issues:** [GitHub Issues](https://github.com/ShadyWorks-Net/shadycogs/issues)
- **Red Discord:** [Red - Discord Bot](https://discord.gg/red)

## License

MIT License - see individual cog directories for details.
