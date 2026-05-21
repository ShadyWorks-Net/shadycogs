# SignupTracker

Track reactions on announcement posts for signups.

## Overview

Create an ordered list of users who react to announcements. Perfect for events, game sessions, or any signup that needs first-come-first-served tracking.

## Setup

### 1. Load the Cog
```
[p]load signuptracker
```

### 2. Configure Settings
```
[p]signupset setup
```

## Commands

### Admin Commands

| Command | Description |
|---------|-------------|
| `[p]signupset setup` | Interactive settings panel |
| `[p]signupset track <message_id>` | Track reactions on a message |
| `[p]signupset status` | View current tracking status |
| `[p]signupset clear` | Clear current tracking |
| `[p]signupset export` | Export signup list (CSV/JSON) |
| `[p]signupset deadline <hours>` | Set signup deadline |
| `[p]signupset history` | View past signups |
| `[p]signupset addrole` | Add mod role |
| `[p]signupset removerole` | Remove mod role |
| `[p]signupset listroles` | List mod roles |

## Features

### Reaction Tracking
- Track multiple reaction emojis
- Order preserved (first reactor is #1)
- Real-time updates to tracker message

### Deadlines
- Set automatic deadlines
- Reminder notifications before deadline
- Configurable default deadline hours

### History
- Past signups stored
- View historical participation
- Statistics tracking

### Export
- Export to CSV or JSON
- Include timestamps
- Sorted by signup order

## How It Works

1. Admin posts announcement
2. Admin runs `[p]signupset track <message_id>`
3. Users react to the announcement
4. Bot maintains ordered list of reactors
5. List updates in real-time in a separate tracker message
6. Optional deadline closes signups automatically

## Tracker Display

The tracker message shows:
- Signup title
- Ordered list of participants (with timestamps)
- Total count
- Deadline (if set)

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Manage Guild | Track/manage signups |
| Configured Mod Roles | Track/manage signups |
