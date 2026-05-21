# AnonMail

Anonymous feedback system for Discord servers.

## Overview

AnonMail allows server members to submit anonymous feedback to designated recipients. Feedback is posted in private threads, keeping senders anonymous while providing a structured way to collect input.

## Setup

### 1. Load the Cog
```
[p]load anonmail
```

### 2. Configure via Interactive Setup
```
[p]anonmail setup
```

This opens an interactive panel where you can:
- Set recipient roles (who can receive feedback)
- Set sender role (who can send feedback, or leave as "Anyone")
- Set the feedback channel
- Configure thread prefix and cooldown

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/feedback` | Submit anonymous feedback to a recipient |

### Admin Commands

| Command | Description |
|---------|-------------|
| `[p]anonmail setup` | Interactive configuration panel |
| `[p]anonmail status` | View current configuration |
| `[p]anonmail channel` | Set the feedback channel |
| `[p]anonmail addrole` | Add a management role |
| `[p]anonmail removerole` | Remove a management role |
| `[p]anonmail listroles` | List management roles |
| `[p]anonmail listrecipients` | List recipient roles and member counts |

## How It Works

1. User runs `/feedback`
2. Selects a recipient from the dropdown (members with recipient roles)
3. Enters feedback in a modal
4. Feedback is posted to a thread named after the recipient
5. Sender identity remains completely anonymous

## Configuration Options

| Setting | Description |
|---------|-------------|
| **Recipient Roles** | Roles whose members can receive feedback |
| **Sender Role** | Role required to send feedback (or "Anyone") |
| **Feedback Channel** | Channel where feedback threads are created |
| **Thread Prefix** | Prefix for thread names (default: "Feedback - ") |
| **Cooldown** | Minutes between feedback submissions per user |

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Mod Roles | Manage settings |
| Sender Role | Submit feedback |
| Everyone | Nothing (unless configured) |
