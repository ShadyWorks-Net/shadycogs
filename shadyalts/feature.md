# ShadyAlts

Alt account tracking and network management for moderation.

## Overview

ShadyAlts lets moderators manually track alt account relationships. When accounts are linked as alts, the system maintains those connections and can auto-flag entire networks when one account is confirmed as a bad actor (banned for bot/spam).

## Setup

### 1. Load the Cog
```
[p]load shadyalts
```

### 2. Set Log Channel
```
/altset setting:Set Log Channel channel:#mod-logs
```

## Commands

### Alt Management

| Command | Description |
|---------|-------------|
| `/alt mark <user1> <user2>` | Link two users as alts |
| `/alt unmark <user1> <user2>` | Remove alt link |
| `/alt view <user>` | View user's alt network |
| `/altid mark` | Link by user ID (modal) |
| `/altid unmark` | Unlink by user ID (modal) |
| `/altid view <user_id>` | View alts by user ID |

### Network Viewing

| Command | Description |
|---------|-------------|
| `/altnetwork suspect` | View ML-flagged suspects |
| `/altnetwork confirmed` | View confirmed bad actors |
| `/altnetwork stats` | View network statistics |

### Settings

| Command | Description |
|---------|-------------|
| `/altset view` | View current settings |
| `/altset channel` | Set mod log channel |
| `/altset joinnotify` | Toggle join notifications |
| `/altset leavenotify` | Toggle leave notifications |
| `/altset addrole` | Add mod role |
| `/altset removerole` | Remove mod role |

## Concepts

### Alt Links
Direct connections between accounts known to be the same person. Bidirectional and form natural groups.

### Suspect Network
Users flagged by ML detection (from ShadyFlags) as potentially suspicious. Pending mod review.

### Confirmed Network
Users confirmed as bad actors (banned for bot/spam). When one account is confirmed, ALL linked alts are auto-confirmed.

## Auto-Confirmation

When you mark two accounts as alts and one is already confirmed bad:
- The other account is automatically confirmed
- All accounts in the network inherit the confirmed status

This ensures entire alt networks are flagged when any member is caught.

## Join/Leave Notifications

When enabled, the bot notifies the mod log channel when:
- A user who has been manually linked as an alt joins the server
- A user who has been manually linked as an alt leaves the server

This helps track when known ban evaders or their connected accounts appear.

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Moderate Members | Manage alts |
| Ban Members | Manage alts |
| Configured Mod Roles | Manage alts |
