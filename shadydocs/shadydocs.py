"""
ShadyDocs - In-Discord wiki/documentation cog.
Simple documentation system for servers without external hosting.

Features:
- Create/edit/delete doc pages
- Category organization
- Search across content
- Clean embed presentation
- Modal-based editing
- Autocomplete on lookups
"""

import discord
import logging
from datetime import datetime, timezone
from typing import Optional, List

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify
from discord import app_commands

log = logging.getLogger("red.shadycogs.shadydocs")


class DocEditModal(discord.ui.Modal, title="Edit Documentation Page"):
    """Modal for editing a doc page."""

    title_input = discord.ui.TextInput(
        label="Page Title",
        placeholder="Enter page title...",
        required=True,
        max_length=100
    )

    content = discord.ui.TextInput(
        label="Content",
        style=discord.TextStyle.paragraph,
        placeholder="Enter documentation content... (supports basic markdown)",
        required=True,
        max_length=4000
    )

    def __init__(self, cog: "ShadyDocs", page_name: str = None, existing_data: dict = None):
        super().__init__()
        self.cog = cog
        self.page_name = page_name
        self.is_new = page_name is None

        if existing_data:
            self.title_input.default = existing_data.get("title", "")
            self.content.default = existing_data.get("content", "")

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        content = self.content.value.strip()

        # Generate page name from title if new
        if self.is_new:
            page_name = title.lower().replace(" ", "-")
            page_name = "".join(c for c in page_name if c.isalnum() or c == "-")
        else:
            page_name = self.page_name

        # Save the page
        await self.cog.save_page(
            interaction.guild.id,
            page_name,
            title,
            content,
            interaction.user.id
        )

        action = "created" if self.is_new else "updated"
        await interaction.response.send_message(
            f"✅ Documentation page `{page_name}` {action}!",
            ephemeral=True
        )


class DocAddFieldModal(discord.ui.Modal, title="Add Embed Field"):
    """Modal for adding fields to a doc page."""

    field_name = discord.ui.TextInput(
        label="Field Name",
        placeholder="Enter field title...",
        required=True,
        max_length=256
    )

    field_value = discord.ui.TextInput(
        label="Field Value",
        style=discord.TextStyle.paragraph,
        placeholder="Enter field content...",
        required=True,
        max_length=1024
    )

    inline = discord.ui.TextInput(
        label="Inline (yes/no)",
        placeholder="yes or no",
        required=False,
        max_length=3,
        default="no"
    )

    def __init__(self, cog: "ShadyDocs", page_name: str):
        super().__init__()
        self.cog = cog
        self.page_name = page_name

    async def on_submit(self, interaction: discord.Interaction):
        pages = await self.cog.config.guild(interaction.guild).pages()

        if self.page_name not in pages:
            await interaction.response.send_message(
                "Page not found.", ephemeral=True
            )
            return

        field = {
            "name": self.field_name.value.strip(),
            "value": self.field_value.value.strip(),
            "inline": self.inline.value.lower() in ("yes", "y", "true", "1")
        }

        async with self.cog.config.guild(interaction.guild).pages() as pages:
            if "fields" not in pages[self.page_name]:
                pages[self.page_name]["fields"] = []
            pages[self.page_name]["fields"].append(field)
            pages[self.page_name]["updated_at"] = datetime.now(timezone.utc).isoformat()
            pages[self.page_name]["updated_by"] = interaction.user.id

        await interaction.response.send_message(
            f"✅ Added field to `{self.page_name}`.", ephemeral=True
        )


class ShadyDocs(commands.Cog):
    """In-Discord documentation/wiki system."""

    __version__ = "1.0.0"
    __author__ = "ShadyTidus"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567897, force_registration=True)

        default_guild = {
            "pages": {},  # name -> {title, content, category, fields, created_at, created_by, updated_at, updated_by}
            "categories": [],  # List of category names
            "embed_color": 0x3498db,  # Default blue
            "mod_roles": [],  # Roles that can manage documentation
        }

        # Page structure:
        # {
        #     "title": "Page Title",
        #     "content": "Main content...",
        #     "category": "category-name",
        #     "fields": [{"name": "...", "value": "...", "inline": bool}],
        #     "image_url": "https://...",  # Optional
        #     "thumbnail_url": "https://...",  # Optional
        #     "created_at": ISO timestamp,
        #     "created_by": user_id,
        #     "updated_at": ISO timestamp,
        #     "updated_by": user_id,
        # }

        self.config.register_guild(**default_guild)

    async def is_authorized(self, interaction: discord.Interaction) -> bool:
        """Check if user has permission to manage documentation."""
        # Bot owner always authorized
        if await self.bot.is_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        # Admin/guild owner always authorized
        if interaction.user.guild_permissions.administrator or interaction.user == interaction.guild.owner:
            return True

        # Check for configured mod roles
        mod_roles = await self.config.guild(interaction.guild).mod_roles()
        return any(role.id in mod_roles for role in interaction.user.roles)

    async def save_page(
        self,
        guild_id: int,
        name: str,
        title: str,
        content: str,
        author_id: int,
        category: str = None
    ):
        """Save or update a documentation page."""
        async with self.config.guild_from_id(guild_id).pages() as pages:
            now = datetime.now(timezone.utc).isoformat()

            if name in pages:
                # Update existing
                pages[name]["title"] = title
                pages[name]["content"] = content
                pages[name]["updated_at"] = now
                pages[name]["updated_by"] = author_id
                if category:
                    pages[name]["category"] = category
            else:
                # Create new
                pages[name] = {
                    "title": title,
                    "content": content,
                    "category": category,
                    "fields": [],
                    "image_url": None,
                    "thumbnail_url": None,
                    "created_at": now,
                    "created_by": author_id,
                    "updated_at": now,
                    "updated_by": author_id,
                }

    async def get_page_names(self, guild_id: int) -> List[str]:
        """Get all page names for a guild."""
        pages = await self.config.guild_from_id(guild_id).pages()
        return list(pages.keys())

    async def search_pages(self, guild_id: int, query: str) -> List[tuple]:
        """Search pages by title and content. Returns list of (name, title, snippet)."""
        pages = await self.config.guild_from_id(guild_id).pages()
        query = query.lower()
        results = []

        for name, data in pages.items():
            title = data.get("title", "")
            content = data.get("content", "")

            # Check title match
            if query in title.lower() or query in name.lower():
                snippet = content[:100] + "..." if len(content) > 100 else content
                results.append((name, title, snippet, 10))  # High priority for title match
                continue

            # Check content match
            if query in content.lower():
                # Find snippet around match
                idx = content.lower().find(query)
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 50)
                snippet = "..." + content[start:end] + "..."
                results.append((name, title, snippet, 5))  # Lower priority for content match

        # Sort by priority
        results.sort(key=lambda x: x[3], reverse=True)
        return [(r[0], r[1], r[2]) for r in results]

    def format_page_embed(self, data: dict, name: str, color: int) -> discord.Embed:
        """Format a documentation page as an embed."""
        embed = discord.Embed(
            title=data.get("title", name),
            description=data.get("content", ""),
            color=discord.Color(color),
            timestamp=datetime.now(timezone.utc)
        )

        # Add fields
        for field in data.get("fields", []):
            embed.add_field(
                name=field.get("name", ""),
                value=field.get("value", ""),
                inline=field.get("inline", False)
            )

        # Add images
        if data.get("image_url"):
            embed.set_image(url=data["image_url"])
        if data.get("thumbnail_url"):
            embed.set_thumbnail(url=data["thumbnail_url"])

        # Add category if set
        if data.get("category"):
            embed.set_footer(text=f"Category: {data['category']} | Page: {name}")
        else:
            embed.set_footer(text=f"Page: {name}")

        return embed

    # ==================== AUTOCOMPLETE ====================

    async def page_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for page names."""
        pages = await self.config.guild(interaction.guild).pages()
        choices = []

        for name, data in pages.items():
            title = data.get("title", name)
            if current.lower() in name.lower() or current.lower() in title.lower():
                display = f"{title} ({name})" if title != name else name
                choices.append(app_commands.Choice(name=display[:100], value=name))

        return choices[:25]

    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for category names."""
        categories = await self.config.guild(interaction.guild).categories()
        choices = []

        for cat in categories:
            if current.lower() in cat.lower():
                choices.append(app_commands.Choice(name=cat, value=cat))

        return choices[:25]

    # ==================== USER COMMANDS ====================

    @app_commands.command(name="doc", description="View a documentation page")
    @app_commands.describe(name="The page to view")
    @app_commands.autocomplete(name=page_autocomplete)
    @app_commands.guild_only()
    async def doc_view(self, interaction: discord.Interaction, name: str):
        """View a documentation page."""
        pages = await self.config.guild(interaction.guild).pages()
        color = await self.config.guild(interaction.guild).embed_color()

        if name not in pages:
            await interaction.response.send_message(
                f"Page `{name}` not found. Use `/docs` to see available pages.",
                ephemeral=True
            )
            return

        embed = self.format_page_embed(pages[name], name, color)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="docs", description="List all documentation pages")
    @app_commands.describe(category="Filter by category")
    @app_commands.autocomplete(category=category_autocomplete)
    @app_commands.guild_only()
    async def docs_list(self, interaction: discord.Interaction, category: Optional[str] = None):
        """List all documentation pages."""
        pages = await self.config.guild(interaction.guild).pages()
        categories = await self.config.guild(interaction.guild).categories()

        if not pages:
            await interaction.response.send_message(
                "No documentation pages yet. Admins can create pages with `/docset add`.",
                ephemeral=True
            )
            return

        # Organize by category
        organized = {"Uncategorized": []}
        for cat in categories:
            organized[cat] = []

        for name, data in pages.items():
            page_cat = data.get("category")
            if category and page_cat != category:
                continue

            if page_cat and page_cat in organized:
                organized[page_cat].append((name, data.get("title", name)))
            else:
                organized["Uncategorized"].append((name, data.get("title", name)))

        embed = discord.Embed(
            title="📚 Documentation",
            color=discord.Color.blue()
        )

        for cat, pages_list in organized.items():
            if not pages_list:
                continue

            value = "\n".join([f"• `{name}` - {title}" for name, title in pages_list[:10]])
            if len(pages_list) > 10:
                value += f"\n... and {len(pages_list) - 10} more"

            embed.add_field(name=cat, value=value, inline=False)

        embed.set_footer(text="Use /doc <name> to view a page")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="docsearch", description="Search documentation pages")
    @app_commands.describe(query="Search term")
    @app_commands.guild_only()
    async def doc_search(self, interaction: discord.Interaction, query: str):
        """Search documentation pages."""
        results = await self.search_pages(interaction.guild.id, query)

        if not results:
            await interaction.response.send_message(
                f"No results found for `{query}`.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🔍 Search Results for \"{query}\"",
            color=discord.Color.blue()
        )

        for name, title, snippet in results[:10]:
            embed.add_field(
                name=f"{title} (`{name}`)",
                value=snippet,
                inline=False
            )

        if len(results) > 10:
            embed.set_footer(text=f"Showing 10 of {len(results)} results")

        await interaction.response.send_message(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @app_commands.command(name="docset", description="Manage documentation pages")
    @app_commands.describe(
        action="Action to perform",
        name="Page name (for edit/delete/category/image)",
        category="Category name (for add category or set page category)",
        url="Image URL (for image action)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Page", value="add"),
        app_commands.Choice(name="Edit Page", value="edit"),
        app_commands.Choice(name="Delete Page", value="delete"),
        app_commands.Choice(name="Add Field to Page", value="addfield"),
        app_commands.Choice(name="Clear Fields", value="clearfields"),
        app_commands.Choice(name="Set Page Category", value="setcategory"),
        app_commands.Choice(name="Set Page Image", value="setimage"),
        app_commands.Choice(name="Set Page Thumbnail", value="setthumbnail"),
        app_commands.Choice(name="Add Category", value="addcategory"),
        app_commands.Choice(name="Remove Category", value="removecategory"),
        app_commands.Choice(name="List All Pages", value="list"),
        app_commands.Choice(name="Set Embed Color", value="setcolor"),
        app_commands.Choice(name="Add Mod Role", value="addrole"),
        app_commands.Choice(name="Remove Mod Role", value="removerole"),
        app_commands.Choice(name="List Mod Roles", value="listroles"),
    ])
    @app_commands.autocomplete(name=page_autocomplete, category=category_autocomplete)
    @app_commands.describe(role="Role for addrole/removerole actions")
    @app_commands.guild_only()
    async def docset(
        self,
        interaction: discord.Interaction,
        action: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        url: Optional[str] = None,
        role: Optional[discord.Role] = None
    ):
        """Manage documentation pages."""
        # Check permission
        if not await self.is_authorized(interaction):
            await interaction.response.send_message(
                "You don't have permission to manage documentation.",
                ephemeral=True
            )
            return

        if action == "add":
            modal = DocEditModal(self)
            await interaction.response.send_modal(modal)

        elif action == "edit":
            if not name:
                await interaction.response.send_message(
                    "Please specify a page name to edit.", ephemeral=True
                )
                return

            pages = await self.config.guild(interaction.guild).pages()
            if name not in pages:
                await interaction.response.send_message(
                    f"Page `{name}` not found.", ephemeral=True
                )
                return

            modal = DocEditModal(self, name, pages[name])
            await interaction.response.send_modal(modal)

        elif action == "delete":
            if not name:
                await interaction.response.send_message(
                    "Please specify a page name to delete.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).pages() as pages:
                if name not in pages:
                    await interaction.response.send_message(
                        f"Page `{name}` not found.", ephemeral=True
                    )
                    return
                del pages[name]

            await interaction.response.send_message(
                f"✅ Deleted page `{name}`.", ephemeral=True
            )

        elif action == "addfield":
            if not name:
                await interaction.response.send_message(
                    "Please specify a page name to add a field to.", ephemeral=True
                )
                return

            pages = await self.config.guild(interaction.guild).pages()
            if name not in pages:
                await interaction.response.send_message(
                    f"Page `{name}` not found.", ephemeral=True
                )
                return

            modal = DocAddFieldModal(self, name)
            await interaction.response.send_modal(modal)

        elif action == "clearfields":
            if not name:
                await interaction.response.send_message(
                    "Please specify a page name.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).pages() as pages:
                if name not in pages:
                    await interaction.response.send_message(
                        f"Page `{name}` not found.", ephemeral=True
                    )
                    return
                pages[name]["fields"] = []

            await interaction.response.send_message(
                f"✅ Cleared all fields from `{name}`.", ephemeral=True
            )

        elif action == "setcategory":
            if not name:
                await interaction.response.send_message(
                    "Please specify a page name.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).pages() as pages:
                if name not in pages:
                    await interaction.response.send_message(
                        f"Page `{name}` not found.", ephemeral=True
                    )
                    return
                pages[name]["category"] = category

            if category:
                await interaction.response.send_message(
                    f"✅ Set category of `{name}` to `{category}`.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"✅ Removed category from `{name}`.", ephemeral=True
                )

        elif action == "setimage":
            if not name or not url:
                await interaction.response.send_message(
                    "Please specify both page name and image URL.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).pages() as pages:
                if name not in pages:
                    await interaction.response.send_message(
                        f"Page `{name}` not found.", ephemeral=True
                    )
                    return
                pages[name]["image_url"] = url

            await interaction.response.send_message(
                f"✅ Set image for `{name}`.", ephemeral=True
            )

        elif action == "setthumbnail":
            if not name or not url:
                await interaction.response.send_message(
                    "Please specify both page name and thumbnail URL.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).pages() as pages:
                if name not in pages:
                    await interaction.response.send_message(
                        f"Page `{name}` not found.", ephemeral=True
                    )
                    return
                pages[name]["thumbnail_url"] = url

            await interaction.response.send_message(
                f"✅ Set thumbnail for `{name}`.", ephemeral=True
            )

        elif action == "addcategory":
            if not category:
                await interaction.response.send_message(
                    "Please specify a category name to add.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).categories() as categories:
                if category in categories:
                    await interaction.response.send_message(
                        f"Category `{category}` already exists.", ephemeral=True
                    )
                    return
                categories.append(category)

            await interaction.response.send_message(
                f"✅ Added category `{category}`.", ephemeral=True
            )

        elif action == "removecategory":
            if not category:
                await interaction.response.send_message(
                    "Please specify a category name to remove.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).categories() as categories:
                if category not in categories:
                    await interaction.response.send_message(
                        f"Category `{category}` not found.", ephemeral=True
                    )
                    return
                categories.remove(category)

            # Also remove from pages
            async with self.config.guild(interaction.guild).pages() as pages:
                for page in pages.values():
                    if page.get("category") == category:
                        page["category"] = None

            await interaction.response.send_message(
                f"✅ Removed category `{category}`.", ephemeral=True
            )

        elif action == "list":
            pages = await self.config.guild(interaction.guild).pages()
            categories = await self.config.guild(interaction.guild).categories()

            embed = discord.Embed(
                title="📚 Documentation Management",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Total Pages",
                value=str(len(pages)),
                inline=True
            )
            embed.add_field(
                name="Categories",
                value=", ".join(categories) if categories else "None",
                inline=True
            )

            if pages:
                page_list = []
                for name, data in list(pages.items())[:20]:
                    cat = data.get("category", "None")
                    page_list.append(f"• `{name}` ({cat})")

                embed.add_field(
                    name="Pages",
                    value="\n".join(page_list),
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "setcolor":
            # Parse hex color from category field (reusing parameter)
            if not category:
                await interaction.response.send_message(
                    "Please specify a hex color (e.g., #3498db or 3498db) in the category field.",
                    ephemeral=True
                )
                return

            try:
                color_str = category.lstrip("#")
                color = int(color_str, 16)
            except ValueError:
                await interaction.response.send_message(
                    "Invalid hex color. Use format like #3498db or 3498db.",
                    ephemeral=True
                )
                return

            await self.config.guild(interaction.guild).embed_color.set(color)
            await interaction.response.send_message(
                f"✅ Set embed color to `#{color_str}`.", ephemeral=True
            )

        elif action == "addrole":
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Only administrators can manage mod roles.", ephemeral=True
                )
                return

            if not role:
                await interaction.response.send_message(
                    "Please specify a role to add.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id in roles:
                    await interaction.response.send_message(
                        f"{role.mention} is already a mod role.", ephemeral=True
                    )
                    return
                roles.append(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can now manage documentation.", ephemeral=True
            )

        elif action == "removerole":
            is_owner = await self.bot.is_owner(interaction.user)
            if not is_owner and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Only administrators can manage mod roles.", ephemeral=True
                )
                return

            if not role:
                await interaction.response.send_message(
                    "Please specify a role to remove.", ephemeral=True
                )
                return

            async with self.config.guild(interaction.guild).mod_roles() as roles:
                if role.id not in roles:
                    await interaction.response.send_message(
                        f"{role.mention} is not a mod role.", ephemeral=True
                    )
                    return
                roles.remove(role.id)

            await interaction.response.send_message(
                f"✅ {role.mention} can no longer manage documentation.", ephemeral=True
            )

        elif action == "listroles":
            mod_roles = await self.config.guild(interaction.guild).mod_roles()

            if not mod_roles:
                await interaction.response.send_message(
                    "No mod roles configured. Admins only.",
                    ephemeral=True
                )
                return

            role_mentions = []
            for role_id in mod_roles:
                r = interaction.guild.get_role(role_id)
                if r:
                    role_mentions.append(r.mention)
                else:
                    role_mentions.append(f"Unknown ({role_id})")

            embed = discord.Embed(
                title="📚 ShadyDocs Mod Roles",
                description="\n".join(role_mentions),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Admins can always manage documentation")
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Red):
    """Add the cog to the bot."""
    await bot.add_cog(ShadyDocs(bot))
