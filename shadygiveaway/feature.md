# ShadyGiveaway

Advanced giveaway system with prize code management.

## Overview

Create giveaways with automatic winner selection, prize code delivery, claim verification, and automatic rerolls for unclaimed prizes.

## Setup

### 1. Load the Cog
```
[p]load shadygiveaway
```

### 2. Create a Giveaway
Use `/giveaway create` in the target channel.

## Commands

| Command | Description |
|---------|-------------|
| `/giveaway create` | Create a new giveaway (modal) |
| `/giveaway end <id>` | End a giveaway early |
| `/giveaway reroll <id>` | Reroll winners |
| `/giveaway list` | List active giveaways |
| `/giveaway info <id>` | View giveaway details |
| `/giveawayset` | Configure giveaway settings |

## Creating a Giveaway

The creation modal asks for:
- **Prize Name & Description** - What you're giving away
- **Duration** - How long the giveaway runs (e.g., 24h, 3d, 1w)
- **Number of Winners** - 1-20 winners
- **Prize Code** - The code/key winners receive in DM
- **Claim Timeout** - Time for winners to claim (e.g., 1h)

## Features

### Winner Selection
- Random selection from participants
- Configurable number of winners
- Role requirements supported
- Bonus entries for special roles (Nitro, etc.)

### Prize Claiming
- Winners receive DM with claim buttons
- Yes/No confirmation to accept prize
- Claim timeout with automatic reroll
- Prize codes delivered on claim

### Automatic Rerolls
- Unclaimed prizes are automatically rerolled
- New winners notified via DM
- Process repeats until claimed or manually ended

## Duration Formats

| Format | Example |
|--------|---------|
| Seconds | `30s` |
| Minutes | `30m` |
| Hours | `2h` |
| Days | `3d` |
| Weeks | `1w` |

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Configured Mod Roles | Create/manage giveaways |
