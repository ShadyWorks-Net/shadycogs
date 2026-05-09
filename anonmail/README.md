# AnonMail Cog

Anonymous feedback system for Discord. Users can select a recipient from a configured role and submit anonymous feedback that gets posted to dedicated threads.

## Installation

```
[p]repo add myredcogs <repo_url>
[p]cog install myredcogs anonmail
[p]load anonmail
```

## Setup

1. Set the recipient role (members who can receive feedback):
   ```
   [p]anonmailset recipientrole @RoleName
   ```

2. Set the feedback channel (where threads will be created):
   ```
   [p]anonmailset channel #feedback
   ```

3. Optionally set a sender role (required to submit feedback):
   ```
   [p]anonmailset senderrole @Members
   ```

4. Enable the system:
   ```
   [p]anonmailset enable true
   ```

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `[p]feedback` | Submit anonymous feedback to a recipient |

### Admin Commands

| Command | Description |
|---------|-------------|
| `[p]anonmailset enable <true/false>` | Enable/disable the system |
| `[p]anonmailset recipientrole <role>` | Set role whose members receive feedback |
| `[p]anonmailset senderrole [role]` | Set role required to send (empty = anyone) |
| `[p]anonmailset channel <channel>` | Set channel for feedback threads |
| `[p]anonmailset prefix <text>` | Set thread name prefix |
| `[p]anonmailset show` | Show current settings |

## How It Works

1. User runs `[p]feedback`
2. Bot shows a dropdown with available recipients (from recipient role)
3. User selects a recipient and fills in the feedback modal
4. Bot creates or finds a thread named "{prefix}{recipient name}"
5. Feedback is posted anonymously as an embed
6. User gets confirmation (ephemeral)

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| enabled | false | Whether feedback is enabled |
| recipient_role | None | Role whose members can receive feedback |
| sender_role | None | Role required to send feedback (None = anyone) |
| feedback_channel | None | Channel where feedback threads are created |
| thread_prefix | "Feedback - " | Prefix for thread names |

## Use Cases

- DM feedback for tabletop RPGs
- Teacher/instructor feedback
- Anonymous team feedback
- Event organizer feedback

## Privacy

- Feedback is completely anonymous
- No user data is stored
- Threads are named after recipients, not senders
- Command message is auto-deleted

## License

MIT License
