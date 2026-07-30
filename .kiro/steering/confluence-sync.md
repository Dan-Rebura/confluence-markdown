---
inclusion: manual
---

# Confluence Markdown Sync

Tool for publishing local Markdown files to Confluence Cloud and keeping them in sync.

## Location

The sync script lives at: `C:/Users/dshone/OneDrive - Westcon Comstor/Projects/Rebura/confluence-markdown/confluence_markdown_mcp.py`

## Project Config

Each project that publishes to Confluence needs a `confluence.json` in its root:

```json
{
  "space_key": "ECM",
  "confluence_url": "https://rebura.atlassian.net",
  "docs_dir": "Design Documents",
  "parent_page_id": "1915061323"
}
```

## Running

All commands use the sync script's `sync_project` function. Run from the project directory that contains `confluence.json`.

### Dry-run (preview what will happen)

```
python -c "import sys; sys.path.insert(0, r'C:/Users/dshone/OneDrive - Westcon Comstor/Projects/Rebura/confluence-markdown'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>'))"
```

### Execute sync (publish/update all pages)

```
python -c "import sys; sys.path.insert(0, r'C:/Users/dshone/OneDrive - Westcon Comstor/Projects/Rebura/confluence-markdown'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>', confirm=True))"
```

### Update a single page

```
python -c "import sys; sys.path.insert(0, r'C:/Users/dshone/OneDrive - Westcon Comstor/Projects/Rebura/confluence-markdown'); from confluence_markdown_mcp import update_page; print(update_page(r'<path-to-file.md>', confirm=True))"
```

### Publish a single new page

```
python -c "import sys; sys.path.insert(0, r'C:/Users/dshone/OneDrive - Westcon Comstor/Projects/Rebura/confluence-markdown'); from confluence_markdown_mcp import publish_page; print(publish_page(r'<path-to-file.md>', confirm=True))"
```

## Credentials

The script reads from `.env` in the confluence-markdown project directory:
- `CONFLUENCE_URL` - default Atlassian site URL
- `CONFLUENCE_EMAIL` - login email
- `CONFLUENCE_API_TOKEN` - unscoped API token

## Frontmatter

Files track their Confluence state via YAML frontmatter:

```yaml
---
title: "Page Title"
confluence_page_id: "123456"
confluence_space_key: "ECM"
confluence_version: 3
confluence_homepage: true  # optional: updates the space homepage instead of creating new page
---
```

## Behaviour

- Dry-run by default (preview mode unless confirm=True)
- Mermaid diagrams rendered to 4x PNG and uploaded as attachments
- Source markdown preserved (mermaid blocks not replaced in local files)
- Version conflict detection (aborts if remote changed since last sync)
- Full-width layout applied to all pages
- Folder structure creates parent pages in Confluence
- Existing attachments updated rather than duplicated
- `confluence_homepage: true` updates the space's existing homepage content

## When the user says "upload to confluence" or "sync to confluence"

1. Find the `confluence.json` in the project
2. Run a dry-run first to show what will happen
3. Ask for confirmation
4. Run with confirm=True
