# FAFO

"Find Around Find Out" moderation button cog.

## Overview

FAFO posts a warning message with a big red button. Anyone who clicks it gets timed out. It's a fun way to handle people who can't resist pressing buttons they shouldn't.

## Setup

### 1. Load the Cog
```
[p]load fafo
```

### 2. (Optional) Configure Settings
```
[p]fafoset timeout 5
[p]fafoset message Your custom warning message
```

## Commands

| Command | Description |
|---------|-------------|
| `[p]fafo` | Post the FAFO button |
| `[p]fafoset timeout <minutes>` | Set timeout duration (1-1440) |
| `[p]fafoset message <text>` | Set the warning message |
| `[p]fafoset addrole <role>` | Add role that can use FAFO |
| `[p]fafoset removerole <role>` | Remove role from FAFO access |
| `[p]fafoset show` | Show current settings |
| `[p]fafoset listroles` | List all FAFO mod roles |

## How It Works

1. Moderator runs `[p]fafo`
2. Bot posts an embed with a tempting red "FAFO" button
3. Anyone who clicks gets timed out for the configured duration
4. Button message auto-deletes after 3 minutes

## Default Warning Message

> This button has been professionally engineered, thoroughly tested, and specifically designed to escalate your current situation dramatically.
>
> You should absolutely not press it.
>
> ...but statistically speaking, we both know you're going to.

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Moderate Members | Use FAFO command |
| Configured Mod Roles | Use FAFO command |
