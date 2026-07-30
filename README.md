# Confluence Markdown

Kiro Power and MCP server for bidirectional Markdown-to-Confluence sync.

## Features

- Download spaces, pages and page trees as Markdown with images
- Publish new Confluence pages from local Markdown files
- Update existing pages with version conflict detection
- Publish directory trees as page hierarchies
- Local image upload as page attachments
- Dry-run by default for all write operations
- Multi-org support (override Confluence URL per request)
- YAML frontmatter for page tracking (ID, version, space)

## Install as a Kiro Power

Powers panel -> "Add Custom Power" -> "Import from GitHub" with this repo URL.

Configure credentials in MCP settings after install:

| Variable | Description |
|----------|-------------|
| `CONFLUENCE_URL` | Default Atlassian site URL |
| `CONFLUENCE_EMAIL` | Your Atlassian login email |
| `CONFLUENCE_API_TOKEN` | Unscoped API token |

## Install standalone

```bash
pip install confluence-markdown-mcp
```

Or run directly:

```bash
uvx confluence-markdown-mcp
```

## Tools

| Tool | Description |
|------|-------------|
| `test_connection` | Verify credentials and list spaces |
| `list_spaces` | List all accessible spaces |
| `download_space` | Download all pages in a space |
| `download_page` | Download a single page |
| `download_page_tree` | Download page and descendants |
| `publish_page` | Create new page from Markdown (dry-run default) |
| `update_page` | Update existing page (dry-run default) |
| `publish_tree` | Publish directory as page hierarchy (dry-run default) |

## MCP configuration

```json
{
  "mcpServers": {
    "confluence-markdown": {
      "command": "uvx",
      "args": ["confluence-markdown-mcp"],
      "env": {
        "CONFLUENCE_URL": "https://your-site.atlassian.net",
        "CONFLUENCE_EMAIL": "you@example.com",
        "CONFLUENCE_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

## License

MIT
