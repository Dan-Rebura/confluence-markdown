# Tasks

## Task 1: Rename and restructure package
- [x] Rename module to `confluence_markdown_mcp.py`
- [x] Update pyproject.toml (name, description, entry point, dependencies)
- [x] Update mcp.json for new package name
- [x] Update POWER.md with new identity and tools
- [x] Update README.md
- [x] Add .gitignore and .env.example

## Task 2: Add generic API request helper
- [x] Create `_api_request(method, base_url, path, ...)` supporting GET/POST/PUT
- [x] Refactor `_api_get` to use generic helper
- [x] Add `_api_post` and `_api_put` wrappers
- [x] Fix infinite recursion on exhausted retries

## Task 3: Implement frontmatter management
- [x] Add python-frontmatter dependency
- [x] Create `_parse_markdown_file(path)` returning (frontmatter_dict, body_text)
- [x] Create `_write_frontmatter(path, metadata, body)` preserving unknown fields

## Task 4: Implement Markdown-to-Storage conversion
- [x] Add markdown dependency
- [x] Create `_markdown_to_storage(md_text, local_images)` function
- [x] Convert fenced code blocks to Confluence code macros
- [x] Convert local image refs to ac:image/ri:attachment tags
- [x] Convert external image URLs to ac:image/ri:url tags

## Task 5: Implement image attachment upload
- [x] Create `_find_local_images(md_text, base_dir)` returning list of (ref, path)
- [x] Create `_upload_attachment(base_url, page_id, file_path)` with X-Atlassian-Token header
- [x] Handle existing attachment updates (new version)
- [x] Report missing image files as warnings

## Task 6: Implement publish_page tool
- [x] Create MCP tool with dry-run default
- [x] Read file, parse frontmatter, validate required fields
- [x] Convert markdown to storage format
- [x] Dry-run: return preview report
- [x] Confirm: POST to create page, upload images, update frontmatter

## Task 7: Implement update_page tool
- [x] Create MCP tool with dry-run default
- [x] Fetch remote version, compare with local
- [x] Abort on version mismatch
- [x] Dry-run: return preview report
- [x] Confirm: PUT to update page, re-upload images, update frontmatter version

## Task 8: Implement publish_tree tool
- [x] Create MCP tool with dry-run default
- [x] Recursively discover .md files
- [x] Build parent-child hierarchy from folder structure
- [x] Process parents before children
- [x] Dry-run: return tree structure report

## Task 9: Update download tools frontmatter
- [x] Modify _convert_to_markdown to include confluence_page_id, confluence_space_key, confluence_version

## Task 10: Build, test and package
- [ ] Install and verify module loads
- [ ] Test publish_page dry-run locally
- [ ] Build wheel/sdist
- [ ] Publish to PyPI
