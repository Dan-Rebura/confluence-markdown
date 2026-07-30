---
inclusion: manual
---

# Confluence Markdown Sync

Tool for publishing local Markdown files to Confluence Cloud and keeping them in sync.

## Location

The sync script lives at: `<UPDATE-THIS-PATH>/confluence-markdown/confluence_markdown_mcp.py`

## Project Config

This project uses a `confluence.json` in its root to configure the sync:

```json
{
  "space_key": "<SPACE_KEY>",
  "confluence_url": "https://<your-site>.atlassian.net",
  "docs_dir": "<folder-containing-markdown-files>",
  "parent_page_id": "<optional-parent-page-id>"
}
```

## Running

### Dry-run (preview what will happen)

```
python -c "import sys; sys.path.insert(0, r'<UPDATE-THIS-PATH>/confluence-markdown'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>'))"
```

### Execute sync (publish/update all pages)

```
python -c "import sys; sys.path.insert(0, r'<UPDATE-THIS-PATH>/confluence-markdown'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>', confirm=True))"
```

### Update a single page

```
python -c "import sys; sys.path.insert(0, r'<UPDATE-THIS-PATH>/confluence-markdown'); from confluence_markdown_mcp import update_page; print(update_page(r'<path-to-file.md>', confirm=True))"
```

### Publish a single new page

```
python -c "import sys; sys.path.insert(0, r'<UPDATE-THIS-PATH>/confluence-markdown'); from confluence_markdown_mcp import publish_page; print(publish_page(r'<path-to-file.md>', confirm=True))"
```

## Frontmatter

Files track their Confluence state via YAML frontmatter:

```yaml
---
title: "Page Title"
confluence_space_key: "SA"
confluence_page_id: "123456"
confluence_version: 3
confluence_homepage: true
---
```

- `confluence_homepage: true` - updates the space's existing homepage instead of creating a new page
- Files without `confluence_page_id` are created as new pages
- Files with `confluence_page_id` are updated in place

## Behaviour

- Dry-run by default (preview mode unless confirm=True)
- Mermaid diagrams rendered to PNG (2x, white background) and uploaded as attachments
- Source markdown preserved (mermaid blocks not replaced in local files)
- Version conflict detection (aborts if remote changed since last sync)
- Full-width layout applied to all pages
- Folder structure creates parent pages in Confluence
- Existing attachments updated rather than duplicated

## When the user says "upload to confluence" or "sync to confluence"

1. Find the `confluence.json` in the project
2. Run a dry-run first to show what will happen
3. Ask for confirmation
4. Run with confirm=True
