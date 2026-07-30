# Technical Design

## Overview

The confluence-markdown-mcp package is a single-file Python MCP server that provides bidirectional Markdown-to-Confluence sync. It reuses all existing download logic and adds publishing tools (create, update, publish-tree) with Markdown-to-Confluence-storage conversion, image attachment upload and safe dry-run defaults.

## Architecture

Single module: `confluence_markdown_mcp.py`

```
┌─────────────────────────────────────────────────┐
│  MCP Server (FastMCP "confluence-markdown")      │
├─────────────────────────────────────────────────┤
│  Download Tools (existing)                       │
│  - test_connection, list_spaces                  │
│  - download_space, download_page, download_tree  │
├─────────────────────────────────────────────────┤
│  Publish Tools (new)                             │
│  - publish_page                                  │
│  - update_page                                   │
│  - publish_tree                                  │
├─────────────────────────────────────────────────┤
│  Helpers                                         │
│  - _api_request (GET/POST/PUT with retry)        │
│  - _parse_frontmatter / _write_frontmatter       │
│  - _markdown_to_storage                          │
│  - _upload_attachment                            │
│  - _find_local_images                            │
└─────────────────────────────────────────────────┘
```

## Key Design Decisions

1. **Single file** - keeps packaging simple (no src layout, hatchling auto-discovers the module)
2. **Dry-run by default** - all publish tools have `confirm: bool = False`. Must be explicitly set True.
3. **Version conflict detection** - fetch remote version before PUT, abort if mismatch
4. **Attachment workflow** - create page first (empty or with content), upload attachments, then update body with ac:image references if needed
5. **Frontmatter library** - use `python-frontmatter` for robust YAML parsing/serialization that preserves unknown fields
6. **Markdown-to-HTML** - use Python `markdown` library with extensions (tables, fenced_code, sane_lists) then post-process to Confluence storage format (convert code blocks to macros, images to ac:image tags)

## API Design

### publish_page(file_path, space_key=None, parent_page_ref=None, title=None, confluence_url=None, confirm=False)

- Reads markdown file, parses frontmatter
- space_key param overrides frontmatter `confluence_space_key`
- If file has `confluence_page_id`, delegates to update_page instead
- Converts markdown body to storage format
- If confirm=False: returns dry-run report
- If confirm=True: POST creates page, uploads images, updates frontmatter

### update_page(file_path, page_ref=None, title=None, confluence_url=None, confirm=False)

- Reads markdown file, gets page_id from frontmatter or page_ref param
- Fetches remote version, compares with local `confluence_version`
- Converts markdown body to storage format
- If confirm=False: returns dry-run report with diff summary
- If confirm=True: PUT updates page, re-uploads images, updates frontmatter version

### publish_tree(directory_path, space_key, parent_page_ref=None, confluence_url=None, confirm=False)

- Recursively finds all .md files in directory
- Builds hierarchy from folder structure
- For each file: publish or update based on frontmatter presence
- Parent pages created before children
- Returns summary of all operations

## Confluence Storage Format Conversion

Markdown elements map to:
- `# heading` → `<h1>heading</h1>`
- `**bold**` → `<strong>bold</strong>`
- `*italic*` → `<em>italic</em>`
- `` `code` `` → `<code>code</code>`
- Fenced code blocks → `<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">lang</ac:parameter><ac:plain-text-body><![CDATA[...]]></ac:plain-text-body></ac:structured-macro>`
- `![alt](local/image.png)` → `<ac:image><ri:attachment ri:filename="image.png" /></ac:image>`
- `![alt](https://url)` → `<ac:image><ri:url ri:value="https://url" /></ac:image>`
- Tables → `<table><tbody><tr><th>...</th></tr><tr><td>...</td></tr></tbody></table>`

## Dependencies

- requests (HTTP client)
- markdownify (HTML→Markdown for downloads)
- markdown (Markdown→HTML for publishing)
- python-frontmatter (YAML frontmatter parsing)
- python-dotenv (env file loading)
- mcp[cli] (MCP server framework)
