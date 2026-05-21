# ShadyPulse

Health monitoring for bot and services.

## Overview

Monitor bot status, HTTP services, and cog health. Includes automatic cog reloading when failures are detected.

## Setup (Bot Owner Only)

### 1. Load the Cog
```
[p]load shadypulse
```

### 2. Interactive Setup
```
[p]shadypulse setup
```

## Commands

### Staff Commands

| Command | Description |
|---------|-------------|
| `/pulse` | View health dashboard |
| `/uptime` | Show bot uptime |

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]shadypulse setup` | Interactive setup panel |
| `[p]shadypulse status` | View detailed configuration |
| `[p]shadypulse alert <channel>` | Set alert channel |
| `[p]shadypulse autoreload <on/off>` | Toggle cog auto-reload |
| `[p]shadypulse removehttp <name>` | Remove HTTP service |
| `[p]shadypulse removecog <name>` | Stop monitoring a cog |
| `[p]shadypulse list` | List all monitored services |

## Features

### Bot Health
- Latency monitoring
- Uptime tracking
- Status indicators (Online/Degraded/Offline)

### HTTP Service Monitoring
- Monitor external API endpoints
- Expected status code checks
- Configurable timeouts
- Failure detection

### Cog Monitoring
- Check if cogs are loaded
- Optional auto-reload on failure
- Retry limits and cooldowns

## Health Status

| Status | Meaning |
|--------|---------|
| Online | Everything working |
| Degraded | Partial issues (e.g., high latency) |
| Offline | Service unreachable or cog unloaded |

## Monitoring Loop

- Configurable check interval (30-600 seconds)
- Background task runs continuously
- Alerts sent to configured channel

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Full configuration |
| Administrator | View dashboard |
| Manage Guild | View dashboard |
