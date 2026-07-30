# Confluence Markdown

Bidirectional Markdown-to-Confluence sync. Download pages as Markdown, publish and update them back with images and mermaid diagrams.

## Features

- Download spaces, pages and page trees as Markdown with images
- Publish new Confluence pages from local Markdown files
- Update existing pages with version conflict detection
- Publish directory trees as page hierarchies
- Mermaid diagrams rendered to PNG and uploaded as attachments
- Local image upload as page attachments
- Dry-run by default for all write operations
- Multi-org support (override Confluence URL per request)
- YAML frontmatter for page tracking (ID, version, space)
- Full-width page layout applied automatically
- Homepage support (update space homepage from a markdown file)

## Setup

1. Clone this repo
2. Install dependencies:

```bash
pip install -e .
```

3. Create a `.env` file in the project root:

```
CONFLUENCE_URL=https://your-site.atlassian.net
CONFLUENCE_EMAIL=you@example.com
CONFLUENCE_API_TOKEN=your-api-token
```

Generate tokens at [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens). Token must be **unscoped**.

## Project Config

Each project that publishes to Confluence needs a `confluence.json`:

```json
{
  "space_key": "ECM",
  "confluence_url": "https://rebura.atlassian.net",
  "docs_dir": "Design Documents",
  "parent_page_id": "1915061323"
}
```

## Usage

### Sync a project (dry-run)

```bash
python -c "import sys; sys.path.insert(0, r'<path-to-this-repo>'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>'))"
```

### Sync a project (execute)

```bash
python -c "import sys; sys.path.insert(0, r'<path-to-this-repo>'); from confluence_markdown_mcp import sync_project; print(sync_project(r'<path-to-confluence.json>', confirm=True))"
```

### Update a single page

```bash
python -c "import sys; sys.path.insert(0, r'<path-to-this-repo>'); from confluence_markdown_mcp import update_page; print(update_page(r'<path-to-file.md>', confirm=True))"
```

### Publish a single new page

```bash
python -c "import sys; sys.path.insert(0, r'<path-to-this-repo>'); from confluence_markdown_mcp import publish_page; print(publish_page(r'<path-to-file.md>', confirm=True))"
```

## Kiro Integration

A steering file is included at `.kiro/steering/confluence-sync.md`. Reference it with `#confluence-sync` in Kiro chat to enable conversational sync (e.g. "upload to confluence").

## Frontmatter

Files track their Confluence state via YAML frontmatter:

```yaml
---
title: "Page Title"
confluence_page_id: "123456"
confluence_space_key: "ECM"
confluence_version: 3
confluence_homepage: true
---
```

- `confluence_homepage: true` - updates the space's existing homepage content instead of creating a new page
- Files without `confluence_page_id` are created as new pages
- Files with `confluence_page_id` are updated in place

## Mermaid Diagrams

Mermaid code blocks are automatically rendered to PNG (2x scale, white background) and uploaded as page attachments. The source mermaid stays in your local files.

Requires `mmdc` (Mermaid CLI): `npm install -g @mermaid-js/mermaid-cli`

## License

MIT
