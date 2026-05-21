# ShadyDocs

In-Discord wiki and documentation system.

## Overview

Create and manage documentation pages directly in Discord. No external hosting required - everything is stored and displayed within Discord using embeds.

## Setup

### 1. Load the Cog
```
[p]load shadydocs
```

### 2. Create Your First Page
```
/docset action:Add Page
```

## Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/doc <name>` | View a documentation page |
| `/docs` | List all documentation pages |
| `/docs category:<name>` | List pages in a category |
| `/docsearch <query>` | Search documentation |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/docset action:Add Page` | Create a new page |
| `/docset action:Edit Page name:<page>` | Edit existing page |
| `/docset action:Delete Page name:<page>` | Delete a page |
| `/docset action:Add Field to Page name:<page>` | Add embed field |
| `/docset action:Clear Fields name:<page>` | Remove all fields |
| `/docset action:Set Page Category` | Assign category to page |
| `/docset action:Set Page Image` | Add image to page |
| `/docset action:Set Page Thumbnail` | Add thumbnail to page |
| `/docset action:Add Category` | Create new category |
| `/docset action:Remove Category` | Delete category |
| `/docset action:List All Pages` | View all pages (admin) |
| `/docset action:Set Embed Color` | Change embed color |
| `/docset action:Add Mod Role` | Add documentation editor role |
| `/docset action:Remove Mod Role` | Remove editor role |

## Page Structure

Each page can have:
- **Title** - Page heading
- **Content** - Main body text (supports markdown)
- **Category** - For organization
- **Fields** - Additional embed fields
- **Image** - Large image at bottom
- **Thumbnail** - Small image in corner

## Use Cases

- Server rules and guidelines
- Game-specific information
- FAQ pages
- Command help
- Role descriptions
- Event information

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Mod Roles | Create/edit/delete pages |
| Everyone | View pages, search |
