# ShadySuggest

A suggestion board for Red-DiscordBot. Members submit ideas that get posted as
clean embeds, the community votes with **anonymous** ✅/❌ buttons, and staff
triage each suggestion (approve / deny / implemented), attach public notes, and
— via a role-locked command — reveal who voted and who authored an anonymous
suggestion.

## Features

- **Anonymous suggestions** — the author chooses per-suggestion (in the modal)
  whether to post publicly or anonymously. The real author is always stored
  privately for staff reveal.
- **Anonymous voting** — ✅/❌ buttons. Vote counts are public; who voted is not.
  One vote per member, switchable; click the same button again to clear.
- **Two hierarchy-minimum role gates**
  - *Participant* role — members at or above this role can suggest **and** vote.
  - *Staff* role — members at or above this role can approve/deny/implement,
    add notes, and reveal.
- **Vote-only blocklist** — roles that cannot vote but may still suggest.
- **Three channels** — a submit channel (`/suggest` only works here), a post
  channel (open suggestions), and an archive channel (resolved suggestions are
  moved here automatically).
- **Persistent buttons** — voting keeps working across bot restarts.

## Setup (admins)

```
[p]suggestset submitchannel #ideas
[p]suggestset postchannel #suggestions
[p]suggestset archivechannel #suggest-archive
[p]suggestset participantrole @Member
[p]suggestset staffrole @Moderator
[p]suggestset logchannel #suggest-log        # optional
[p]suggestset blocklist add @Muted           # optional (vote-only block)
[p]suggestset enable
```

Review everything with `[p]suggestset view`.

## Commands

| Command | Who | What |
|---|---|---|
| `/suggest` | Participants | Opens a modal (Title, Details, anonymous yes/no) and posts to the post channel. Must be run in the submit channel. |
| ✅ / ❌ buttons | Participants | Anonymous up/down vote; ephemeral confirmation. |
| `/suggestmod approve <id> [reason]` | Staff | Approve → moves the embed to the archive channel. |
| `/suggestmod deny <id> [reason]` | Staff | Deny → moves to archive. |
| `/suggestmod implement <id> [reason]` | Staff | Mark implemented → moves/updates in archive. |
| `/suggestmod note <id> <text>` | Staff | Attach a public staff note. |
| `/suggestmod list [status]` | Staff | Ephemeral list of suggestions. |
| `/suggestreveal <id>` | Staff | Ephemeral (runner-only) reveal of the real author and both voter lists. |
| `[p]suggestset ...` | Admins | Configuration (also available as `/suggestset`). |

Once a suggestion is approved/denied/implemented it is moved to the archive
channel and voting closes. You can still change its status afterward
(e.g. approved → implemented) — the archived embed updates in place.
