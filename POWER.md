---
name: "confluence-markdown"
displayName: "Confluence Markdown"
description: "Bidirectional Markdown-to-Confluence sync. Download pages as Markdown, publish and update Markdown files back to Confluence with images."
keywords: ["confluence", "atlassian", "markdown", "publish", "create", "update", "sync", "wiki", "documentation"]
author: "Daniel Shone"
---

# Confluence Markdown

## Overview

Bidirectional sync between local Markdown files and Confluence Cloud pages. Download pages as Markdown with images, then publish or update them back to Confluence from your local files.

All publish/update operations default to dry-run mode for safety.

## Available MCP Servers

This power provides one MCP server: `confluence-markdown`

### Tools

| Tool | Description |
|------|-------------|
| `test_connection` | Verify credentials and list spaces |
| `list_spaces` | List all accessible spaces |
| `download_space` | Download all pages in a space |
| `download_page` | Download a single page by URL or ID |
| `download_page_tree` | Download a page and all descendants |
| `publish_page` | Create a new Confluence page from Markdown |
| `update_page` | Update an existing page from Markdown |
| `publish_tree` | Publish a directory as a page hierarchy |

## Onboarding

### Prerequisites

- Python 3.10+
- `uv` installed (for `uvx` command)

### Installation

```
uvx confluence-markdown-mcp
```

### Credential Setup

Configure env vars in the Power's MCP settings:

- `CONFLUENCE_URL` - default Atlassian site URL (overridable per call)
- `CONFLUENCE_EMAIL` - your Atlassian login email
- `CONFLUENCE_API_TOKEN` - unscoped API token

Generate tokens at: https://id.atlassian.com/manage-profile/security/api-tokens

**Important:** Token must be unscoped. Scoped tokens return 403.

## Tool Usage Examples

### Publish a new page

Ask: "Publish my architecture.md to the SA space on Confluence"

The agent calls `publish_page` with the file path and space_key. First in dry-run, then with confirm=True after your approval.

### Update an existing page

Ask: "Update the Confluence page from my local design.md"

The agent calls `update_page`. The file's frontmatter contains the page ID and version for conflict detection.

### Publish a folder as a page tree

Ask: "Publish everything in docs/ to the DEV space under page 123456"

The agent calls `publish_tree` with the directory, space key and parent page.

## Frontmatter

Downloaded and published files use YAML frontmatter for sync tracking:

```yaml
---
title: "Architecture Overview"
confluence_page_id: "123456"
confluence_space_key: "SA"
confluence_parent_id: "987654"
confluence_version: 3
source: "https://site.atlassian.net/wiki/spaces/SA/pages/123456"
exported: "2026-07-16T12:00:00Z"
---
```

## Safety

- All publish/update operations default to dry-run
- Version conflict detection prevents overwriting remote changes
- Missing images are reported as warnings, not failures
- Page IDs in frontmatter prevent duplicate page creation

## Troubleshooting

### Version conflict

The remote page was edited since your last download. Download the latest version, merge your changes, then update.

### Authentication failed (401)

Check credentials. Token must be generated for the same email.

### Access denied (403)

Token is scoped or you lack write permissions in the target space.
