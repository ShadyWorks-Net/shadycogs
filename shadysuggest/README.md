# ShadySuggest

A suggestion board for Red-DiscordBot. Members submit ideas that get posted as
clean embeds, the community votes with **anonymous** 👍/👎 buttons, and staff
triage each suggestion (approve / deny / implemented), attach public notes, and
— via a role-locked command — reveal who voted and who authored an anonymous
suggestion.

## Features

- **Anonymous suggestions** — the author chooses per-suggestion (in the modal)
  whether to post publicly or anonymously. The real author is always stored
  privately for staff reveal.
- **Anonymous voting** — 👍/👎 buttons. Vote counts are public; who voted is not.
  One vote per member, switchable; click the same button again to clear.
- **Two hierarchy-minimum role gates**
  - *Participant* role — members at or above this role can suggest **and** vote.
  - *Staff* role — members at or above this role can approve/deny/implement,
    add notes, and reveal.
- **Vote-only blocklist** — roles that cannot vote but may still suggest.
- **Three channels** — a submit channel (`/suggest` only works here), a post
  channel (open suggestions), and an archive channel (resolved suggestions are
  moved here automatically).
- **Manage button** — every suggestion has a ⚙️ Manage button. Only staff can
  use it (non-staff get an ephemeral "no permission"); it opens a private,
  ephemeral panel with **Approve / Deny / Implemented / 📝 Note** buttons. Each
  opens a modal for the reason/note. (Discord can't hide a button per-role, so
  it's visible to all but gated on click.)
- **DM notifications** — the submitter gets a DM when their suggestion is posted,
  when its status changes (with the staff response), and when a note is added.
  Toggle with `[p]suggestset dmnotify on|off`.
- **Persistent buttons** — voting and Manage keep working across bot restarts.

## Setup (admins)

```
[p]suggestset submitchannel #ideas
[p]suggestset postchannel #suggestions
[p]suggestset archivechannel #suggest-archive
[p]suggestset participantrole @Member
[p]suggestset staffrole @Moderator
[p]suggestset logchannel #suggest-log        # optional
[p]suggestset blocklist add @Muted           # optional (vote-only block)
[p]suggestset dmnotify on                     # optional (default on)
[p]suggestset enable
```

Review everything with `[p]suggestset view`.

## Commands

Staff actions exist both as the ⚙️ Manage button on each suggestion **and** as
top-level slash commands. The commands use autocomplete to pick the suggestion
(shows `#12 — Title`, filters as you type, so it scales to many), then open a
modal for the reason/note.

| Command | Who | What |
|---|---|---|
| `/suggest` | Participants | Opens a modal (Title, Details, anonymous yes/no) and posts to the post channel. Must be run in the submit channel. |
| 👍 / 👎 buttons | Participants | Anonymous up/down vote; ephemeral confirmation. |
| ⚙️ Manage button | Staff | Opens an ephemeral panel: Approve / Deny / Implemented / 📝 Note (each opens a modal). |
| `/suggestapprove <suggestion>` | Staff | Approve → modal for staff response → moves the embed to the archive channel. |
| `/suggestdeny <suggestion>` | Staff | Deny → modal → moves to archive. |
| `/suggestimplement <suggestion>` | Staff | Mark implemented → modal → moves/updates in archive. |
| `/suggestnote <suggestion>` | Staff | Modal to attach a public staff note. |
| `/suggestlist [status]` | Staff | Ephemeral list of suggestions. |
| `/suggestreveal <suggestion>` | Staff | Ephemeral (runner-only) reveal of the real author and both voter lists (paginated when long). |
| `[p]suggestset ...` | Admins | Configuration (also available as `/suggestset`). |

Once a suggestion is approved/denied/implemented it is moved to the archive
channel and voting closes. You can still change its status afterward
(e.g. approved → implemented) via its Manage button or the commands — the
archived embed updates in place, and the submitter is DMed each change.
