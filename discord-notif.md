# ShadyTourneys - Tournament Management Cog

A comprehensive tournament system for Discord with Challonge integration, ELO tracking, and match scheduling.

---

## Tournament Types

| Type | Description |
|------|-------------|
| **Solo** | Individual players compete |
| **Team** | Teams of players compete (captain system) |

## Bracket Formats

| Format | Description |
|--------|-------------|
| **Single Elimination** | One loss and you're out |
| **Double Elimination** | Two losses to eliminate (winners/losers brackets) |
| **Round Robin** | Everyone plays everyone |

---

## Commands Overview

### User Commands

| Command | Description |
|---------|-------------|
| `/tourney create_solo` | Create a solo tournament |
| `/tourney create_team` | Create a team tournament |
| `/tourney list` | List active tournaments |

### Tournament Management

| Command | Description |
|---------|-------------|
| `/tourneymanage start <id>` | Start tournament & generate bracket |
| `/tourneymanage cancel <id>` | Cancel a tournament |
| `/tourneymanage bracket <id>` | View current bracket (with Challonge image) |
| `/tourneymanage report <id>` | Report match result with score |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/tourneyset view` | Show current settings |
| `/tourneyset addrole @Role` | Add tournament organizer role |
| `/tourneyset removerole @Role` | Remove organizer role |
| `/tourneyset format` | Set default bracket format |
| `/tourneystats <game>` | View ELO leaderboard (admin only) |
| `/tourneystats <game> @player` | View player's ELO stats |
| `/tourneyseed @player <rank> [game]` | Set initial seed/ELO for new player |

### Bot Owner Commands (Challonge Setup)

| Command | Description |
|---------|-------------|
| `/challongeset credentials <user> <key>` | Set Challonge API credentials |
| `/challongeset status` | Check Challonge connection |
| `/challongeset subdomain <org>` | Set organization subdomain (optional) |

---

## Features

### Signup System
- Persistent signup buttons (survive bot restarts)
- Solo: Click "Join Tournament" to sign up
- Team: Create team, join existing team, or join pickup pool
- Pickup players auto-distributed to incomplete teams at start

### Challonge Integration
- Bracket images embedded directly in Discord
- Live sync with Challonge.com
- Automatic bracket generation on tournament start
- Match results sync to Challonge

### ELO Rating System
- Per-game ELO tracking
- Default starting ELO: 1000
- Score-based ELO adjustments (margin matters)
- **Admin-only visibility** - players don't see ELO

### Seeding System

Seeding in tournaments is **purely ELO-based**:
1. **ELO-Based** - Higher ELO = higher seed
2. **Random** - If no ELO history, randomized

**Initial Seeding (One-Time Setup):**

For new players with no match history, admins can set an **initial seed** which converts to starting ELO:

| Seed Rank | Starting ELO |
|-----------|--------------|
| Seed #1 | 1200 |
| Seed #2 | 1150 |
| Seed #3 | 1100 |
| Seed #4 | 1050 |
| Seed #5+ | 1000 (min) |

**Command:**
```
/tourneyseed @player <seed_rank> [game]
```

**Example:**
```
/tourneyseed @ProPlayer 1 rivals    → Starts at 1200 ELO
/tourneyseed @GoodPlayer 3 rivals   → Starts at 1100 ELO
```

Once seeded, their ELO adjusts naturally from match results. No more manual intervention needed - the system learns from there.

**Note:** Initial seeding only works for players with **no match history**. Players who've already competed keep their earned ELO.

### Match Scheduling
- Per-match threads for coordination
- Propose/Accept/Counter-propose times
- Discord timestamps (auto-converts to user's timezone)
- Check-in system (15 min window around match time)
- Forfeit claiming (if opponent doesn't check in)
- Round deadlines with auto-DQ

---

## Match Scheduling Flow

```
1. Tournament starts → Match threads created
2. Player A clicks "Propose Time" → Enters UTC time
3. Player B clicks "Accept" or "Counter-Propose"
4. Match scheduled → Both players notified
5. 15 min before: Reminder sent
6. Match time: Both players "Check In"
7. Play match → Report result with score
8. If no-show: Checked-in player can "Claim Forfeit"
```

### Scheduling Buttons

| Button | Action |
|--------|--------|
| **Propose Time** | Suggest a match time |
| **Accept Time** | Accept opponent's proposal |
| **Counter-Propose** | Suggest different time |
| **Check In** | Confirm you're ready (±15 min of match time) |
| **Claim Forfeit** | Win by opponent no-show (requires your check-in) |

---

## Team Tournaments

### Team Formation
- **Create Team**: Become captain, name your team
- **Join Team**: Select from dropdown of existing teams
- **Join Pickup**: Random assignment when tournament starts

### Team Identity
- Captain ID + Team Name
- Captain manages roster
- ELO updates for all team members on match result

---

## Match Reporting

1. Run `/tourneymanage report <tournament_id>`
2. Select match from dropdown
3. Enter score (e.g., `2-1`, `3-0`)
4. Select winner
5. ELO updated silently, bracket advances

---

## Prize Pool

- Optional prize tracking when creating tournament
- Displayed on tournament embed
- Announced with champion at tournament end

---

## Setup Checklist

1. **Bot Owner**: Set Challonge credentials
   ```
   /challongeset credentials <username> <api_key>
   ```

2. **Admin**: Add organizer roles
   ```
   /tourneyset addrole @TournamentOrganizer
   ```

3. **Create Tournament**
   ```
   /tourney create_solo
   ```

4. **Wait for signups** (users click buttons)

5. **Start when ready**
   ```
   /tourneymanage start <id>
   ```

---

## Requirements

- `pychallonge` library (auto-installed)
- Challonge account with API key (free tier: 500 requests/month)

---

**Get Challonge API Key:** https://challonge.com/settings/developer
