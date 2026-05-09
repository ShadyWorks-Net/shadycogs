# ShadyTourneys

Tournament and bracket management with Challonge integration for hosted bracket visualization.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadytourneys
[p]load shadytourneys
```

**Requirement:** `pychallonge` (auto-installed)

## Challonge Setup (Bot Owner)

1. Create account at [challonge.com](https://challonge.com) (free)
2. Get API key from: https://challonge.com/settings/developer
3. Configure the bot:
   ```
   /challongeset credentials <username> <api_key>
   /challongeset status  # Verify it's working
   ```
4. (Optional) Set organization subdomain:
   ```
   /challongeset subdomain myorg
   ```

**Note:** Free tier allows 500 API requests/month - plenty for most bots.

## Features

- **Bracket Images**: PNG bracket images embedded directly in Discord
- **Live Updates**: Brackets sync with Challonge in real-time
- **Multiple Formats**: Single/double elimination, round robin

## Commands

### Tournament Commands (Slash Only)

| Command | Description |
|---------|-------------|
| `/tourney create_solo` | Create solo tournament |
| `/tourney create_team` | Create team tournament |
| `/tourney list` | List active tournaments |
| `/tourneymanage start <id>` | Start tournament, generate bracket |
| `/tourneymanage cancel <id>` | Cancel tournament |
| `/tourneymanage bracket <id>` | View current bracket |
| `/tourneymanage report <id>` | Report match result |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/tourneyset view` | Show settings |
| `/tourneyset addrole @Role` | Add tournament organizer role |
| `/tourneyset removerole @Role` | Remove organizer role |
| `/tourneyset format` | Set default bracket format |

## Tournament Formats

| Format | Description |
|--------|-------------|
| Single Elimination | One loss eliminates |
| Double Elimination | Two losses to eliminate |
| Round Robin | Everyone plays everyone |

## Setup

1. Load the cog: `[p]load shadytourneys`
2. Create a tournament: `/tourney create_solo` or `/tourney create_team`
3. Users sign up via buttons on the tournament embed
4. Start when ready: `/tourneymanage start <tournament_id>`

## Solo Tournaments

1. Create with `/tourney create_solo`
2. Users click "Join Tournament" button
3. Start generates bracket with all participants

## Team Tournaments

1. Create with `/tourney create_team`
2. Users can:
   - ⭐ **Create Team**: Become captain
   - 👥 **Join a Team**: Select from dropdown
   - 🎲 **Join as Pickup**: Random assignment
3. Pickup players fill incomplete teams at start

## Features

- **Persistent Views**: Signup buttons survive bot restarts
- **Autocomplete**: Tournament IDs autocomplete in commands
- **Match Reporting**: Select match → Select winner workflow
- **Prize Pool**: Optional prize tracking
- **Team Management**: Captain system, pickup pool

## Example Workflow

```
1. /tourney create_team
   → "Marvel Rivals Championship" created

2. Users click signup buttons
   → Teams form, pickups join pool

3. /tourneymanage start 123456789_1234567890
   → Bracket generated, pickups distributed

4. /tourneymanage report 123456789_1234567890
   → Select match, select winner

5. Tournament completes
   → Champion announced in channel
```

## License

MIT License
