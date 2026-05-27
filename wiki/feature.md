# Wiki

Community helper cog with server rules and info commands.

## Overview

Wiki provides quick-access commands for moderators to share server rules, hosting guidelines, and helpful info with members. All commands work as both slash commands and prefix commands.

## Setup

### 1. Load the Cog
```
[p]load wiki
[p]slash sync
```

### 2. (Optional) Add Authorized Roles
```
[p]wikiset addrole @Moderator mod
[p]wikiset addrole @Admin admin
```

## Commands

| Command | Description |
|---------|-------------|
| `/rule <1-11>` | Show a specific server rule |
| `/host` | Link to hosting/advertising guidelines |
| `/hosted` | Link to community-run servers channel |
| `/colors` | Show server colors/levels info |
| `/noaccess` | Explain genre-based channel access |
| `/promote` | Explain content promotion via Linked Roles |

### Setup Commands

| Command | Description |
|---------|-------------|
| `[p]wikiset addrole <role> [mod/admin]` | Add role that can use wiki commands |
| `[p]wikiset removerole <role>` | Remove role from wiki access |
| `[p]wikiset listroles` | List authorized roles |

## How It Works

1. Moderator sees a question about rules/access
2. Moderator runs the appropriate command (e.g., `/rule 6`)
3. Bot posts a formatted response with the relevant info
4. Prefix commands auto-delete the invoking message

## Server Rules (1-11)

### Rule 1 - Be Respectful
> **:Hug: 1) Be Respectful**
> No racism, trauma dumping, hateful content, sexual content, extreme vulgarity, argumentative behavior, impersonation or harassment. Keep it friendly and adult.
> Toxic behavior isn't tolerated, both in-game & in the server!
> Undesired DMs & Spamming are considered toxic behaviors and will lead to disciplinary action.

### Rule 2 - 18+ Only
> **🔞 2) 18+ Only**
> You must be 18 or older to be a member. No exceptions.

### Rule 3 - Be Civil & Read The Room
> **:mmmsphere: 3) Be Civil & Read The Room!**
> Avoid political, religious, drug-related conversations in voice channels unless all participants in the room are okay with the conversation.
> Political, religious, drug-related images/conversations are not allowed in text channels.
> We are an international community, even if something is legal in your area, it's not legal everywhere.

### Rule 4 - NSFW Content
> **:RockBrow: 4) NSFW Content Is Not Allowed**
> Pornographic & grotesque content sharing will result in an immediate ban.

### Rule 5 - Communication
> **🗣️ 5) Communication**
> We primarily speak English here. Do your best to communicate clearly with everyone.

### Rule 6 - Channels & Roles
> **:gandalf_bouncy: 6) Use Our Channels & Roles Properly**
> Use our roles & channels correctly by posting in the right places.
> Abusing role pings to promote yourself is prohibited.
> If you're unsure, double check Channels & Roles and THEN ask staff in #server-questions first BEFORE posting!

### Rule 7 - Promoting Content
> **📹 7) Promoting Content**
> Only post content (streaming links & clips) in #promote-content or #clip-sharing.
> Want to collaborate with PA? Apply here! #applications

### Rule 8 - Crowdfunding
> **🫰 8) Crowdfunding and Solicitation**
> No GoFundMe, service offers, or money requests - In public or in DMs.
> This includes but is not limited to requests for personal causes, projects, or business ventures.
> There are no exceptions to this rule for any reason.

### Rule 9 - Unauthorized Links
> **❌ 9) Unauthorized Links are Not Allowed!**
> Don't post/advertise or DM Discord server, guild/clan invites, game server links, or data collection links (surveys, questionaries, etc.). It's a safety risk.
> Game servers are allowed to be posted only after passing our vetting process! #applications

### Rule 10 - Build-A-VC Names
> **📝 10) Build-A-VC Channel Names**
> No swear-words/vulgarity when naming your Build-A-VC channels!

### Rule 11 - Staff Respect
> **:GES_DiscordStaff: 11) Staff Respect & Jurisdiction**
> Staff volunteer their personal time to make this a great place, please treat them with respect!
> "Mini-Modding" is prohibited, #contact-staff if something requires staff attention. If you disagree with a staff decision, open a ticket.
> Altercations outside of Parental Advisory will not be handled by Parental Advisory Staff. We ask you to keep personal matters outside of the server.

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Red Mod/Admin | Use wiki commands |
| Configured Roles | Use wiki commands |
