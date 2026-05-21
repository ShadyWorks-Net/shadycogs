# ShadyStatus

Multi-game server status query cog.

## Overview

Query game servers via the Steam A2S protocol. Supports multiple games including 7 Days to Die, ARK, Rust, Valheim, Project Zomboid, and V Rising.

## Setup

### 1. Install Dependency
```
pip install python-a2s
```

### 2. Load the Cog
```
[p]load shadystatus
```

### 3. Add Servers
```
/serverset add
```

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/serverstatus` | Query all tracked servers |
| `/serverstatus server:<name>` | Query specific server |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/serverset add` | Add a server to track |
| `/serverset remove` | Remove a tracked server |
| `/serverset list` | List tracked servers |
| `/serverset view` | View current settings |
| `/serverset addrole` | Add mod role |
| `/serverset removerole` | Remove mod role |

## Supported Games

| Game | Type |
|------|------|
| 7 Days to Die | `7dtd` |
| ARK: Survival Evolved | `ark` |
| Rust | `rust` |
| Valheim | `valheim` |
| Project Zomboid | `pz` |
| V Rising | `vrising` |
| Generic (any A2S) | `generic` |

## Server Information Displayed

- Server name
- Current/max players
- Map name
- Password protection status
- Server version
- Ping/latency
- Game-specific rules (if available)

## Query Protocol

Uses Steam A2S (Source Server Query) protocol. Works with any game server that implements A2S.

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Manage Guild | Add/remove servers |
| Configured Mod Roles | Add/remove servers |
| Everyone | Query status |
