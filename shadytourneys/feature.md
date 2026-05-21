# ShadyTourneys

Tournament and bracket management system with Challonge integration.

## Overview

Create and manage tournaments with Discord signups, seeding, and Challonge bracket hosting. Supports solo and team tournaments in multiple formats.

## Setup

### 1. Load the Cog
```
[p]load shadytourneys
```

### 2. (Optional) Configure Challonge
```
[p]challongeset credentials <username> <api_key>
```

Get your API key from https://challonge.com/settings/developer

## Commands

### Tournament Commands

| Command | Description |
|---------|-------------|
| `/tourney create` | Create a new tournament |
| `/tourney signup <tournament>` | Sign up for a tournament |
| `/tourney withdraw <tournament>` | Withdraw from tournament |
| `/tourney start <tournament>` | Start the tournament |
| `/tourney report <tournament>` | Report match result |
| `/tourney status <tournament>` | View tournament status |
| `/tourney bracket <tournament>` | Get bracket link |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/tourneyset view` | View settings |
| `/tourneyset addrole` | Add tournament mod role |
| `/tourneyset removerole` | Remove mod role |
| `/tourneyset seed` | Configure seeding lists |
| `/tourneyset games` | Manage supported games |

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]challongeset credentials` | Set Challonge API credentials |

## Tournament Formats

| Format | Description |
|--------|-------------|
| Single Elimination | Lose once, you're out |
| Double Elimination | Lose twice to be eliminated |
| Round Robin | Everyone plays everyone |

## Features

### Seeding
- Per-game seed lists (e.g., rank names to seed values)
- Auto-seeding based on player rank selection
- Manual seed override

### Challonge Integration
- Automatic bracket creation
- Match reporting synced to Challonge
- Live bracket links

### Player Stats
- Track wins/losses per game
- Historical tournament performance
- Stats reset options

## Default Seed List (Marvel Rivals Example)

| Rank | Seed Value |
|------|------------|
| One Above All | 1500 |
| Eternity | 1400 |
| Celestial | 1300 |
| Grandmaster | 1200 |
| Diamond | 1100 |
| Platinum | 1050 |
| Gold | 1000 |
| Silver | 950 |
| Bronze | 900 |

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything + Challonge config |
| Administrator | Everything except Challonge config |
| Mod Roles | Create/manage tournaments |
| Everyone | Sign up, view brackets |
