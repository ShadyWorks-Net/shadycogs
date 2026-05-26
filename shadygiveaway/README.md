# ShadyGiveaway

Advanced giveaway system with prize code management, claim verification, and automatic rerolls.

## Features

- **Prize Code System**: Store codes/keys securely and only send to winners who actively claim
- **Claim Verification**: Winners must click "Yes" within a timeout period to receive their prize
- **Automatic Rerolls**: If winner clicks "No" or times out, automatically picks next winner
- **Multiple Winners**: Support for giveaways with multiple winners
- **Role Authorization**: Only authorized staff can create giveaways (uses wiki/config/roles.json)
- **Entry Tracking**: Real-time entry count display
- **Background Task**: Automatically ends giveaways when duration expires

## Installation

```
[p]repo add ShadyRepo <repo_url>
[p]cog install ShadyRepo shadygiveaway
[p]load shadygiveaway
```

## Commands

### `/giveaway create`
Opens a modal to create a new giveaway with:
- **Prize Name**: What you're giving away
- **Description**: Optional additional details
- **Duration**: How long the giveaway runs (e.g., `24h`, `3d`, `1w`)
- **Number of Winners**: How many people win (1-20)
- **Prize Code/Key**: The code that winners receive
- **Claim Timeout**: How long winners have to claim (e.g., `1h`, `30m`)

### `/giveaway list`
Shows all currently active giveaways with:
- Prize name
- Channel
- Entry count
- Time remaining
- Giveaway ID

## How It Works

### 1. Creating a Giveaway
Staff member uses `/giveaway create`, selects channel, and fills in the modal with giveaway details.

### 2. Users Enter
Bot posts an embed with an "🎉 Enter Giveaway" button. Users click to enter. Each user can only enter once.

### 3. Giveaway Ends
When the duration expires, the bot:
- Updates the embed to show "ENDED"
- Removes the entry button
- Picks a random winner from entries

### 4. Winner Claim Process
Winner receives a DM with an embed containing:
- Prize name
- Time limit to claim
- Two buttons: **✅ Yes, I claim this prize!** and **❌ No, reroll**

**If they click Yes:**
- Instantly receives a DM with the prize code
- Announcement in the giveaway channel

**If they click No or timeout:**
- Bot announces the reroll in the channel
- Automatically picks a new winner
- New winner gets the same claim process

This prevents the first winner from getting the code without responding, ensuring the second winner gets a valid prize.

### 5. Multiple Winners
For giveaways with multiple winners, the process repeats for each winner position.

## Authorization

Only users with the following can create/manage giveaways:
- Administrator permission
- Role ID listed in `E:/wiki/config/roles.json`

## Technical Details

- **Storage**: Uses Red's Config system (guild-scoped)
- **Background Task**: Checks every 30 seconds for ended giveaways
- **Persistent Views**: Enter buttons persist through bot restarts
- **Claim Timeout**: View timeout automatically triggers reroll if winner doesn't respond

## Examples

### Quick Giveaway
```
/giveaway create channel:#giveaways
Prize: Discord Nitro
Duration: 24h
Winners: 1
Prize Code: XXXX-XXXX-XXXX-XXXX
Claim Timeout: 1h
```

### Multi-Winner Event
```
/giveaway create channel:#events
Prize: $10 Steam Gift Card
Duration: 3d
Winners: 5
Prize Code: [List of 5 codes separated by commas]
Claim Timeout: 2h
```

## Notes

- Winners who left the server are automatically skipped and new winners are picked
- If a winner can't receive DMs, the claim embed is posted in the giveaway channel
- Prize codes are never logged or visible to anyone except the winner who claims
- Giveaway data persists through bot restarts

## Future Enhancements

Potential features for future versions:
- Required/excluded roles for participation
- Scheduled giveaways (start at a specific time)
- Giveaway templates
- Export winner history
- Blacklist system
