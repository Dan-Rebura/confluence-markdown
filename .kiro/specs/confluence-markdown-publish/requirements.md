# Requirements Document

## Introduction

This feature extends the existing confluence-downloader MCP server with bidirectional Markdown-to-Confluence publishing capabilities. The new package (confluence-markdown-mcp) includes all existing download tools plus new tools for creating and updating Confluence pages from local Markdown files, with support for image attachment uploads, version conflict detection and safe dry-run defaults.

## Glossary

- **Publisher**: The MCP server component responsible for converting Markdown files to Confluence storage format and creating or updating pages via the Confluence REST API
- **Frontmatter**: YAML metadata block at the top of a Markdown file, delimited by `---` lines, containing page metadata such as title, space key, page ID and version number
- **Storage_Format**: Confluence's internal XHTML-based page representation used by the REST API for page content
- **Converter**: The component that transforms Markdown content into Confluence Storage_Format HTML
- **Page_Ref**: A page identifier that can be either a numeric Confluence page ID or a full page URL containing the ID
- **Dry_Run**: An operation mode where the Publisher reports what changes would be made without executing any write operations against the Confluence API
- **Version_Number**: An integer maintained by Confluence that increments with each page edit, used for optimistic concurrency control
- **Attachment**: A file (typically an image) uploaded to a Confluence page and referenced within the page body

## Requirements

### Requirement 1: Markdown to Storage Format Conversion

**User Story:** As a team member, I want my Markdown files converted to valid Confluence storage format, so that pages render correctly when published.

#### Acceptance Criteria

1. WHEN a Markdown file is provided, THE Converter SHALL transform headings, paragraphs, bold, italic, code blocks, links, lists, tables and blockquotes into valid Confluence Storage_Format
2. WHEN the Markdown contains local image references (relative paths), THE Converter SHALL replace them with `<ac:image>` attachment macros referencing the corresponding Attachment filename
3. WHEN the Markdown contains external image URLs, THE Converter SHALL preserve them as `<ac:image>` elements with `<ri:url>` references
4. WHEN the Markdown contains fenced code blocks with a language identifier, THE Converter SHALL produce Confluence code macro blocks (`<ac:structured-macro ac:name="code">`) with the language parameter set
5. FOR ALL valid Markdown input, converting to Storage_Format then converting back to Markdown SHALL produce semantically equivalent content (round-trip property)

### Requirement 2: Publish New Page

**User Story:** As a team member, I want to create a new Confluence page from a local Markdown file, so that I can author content locally and publish it to Confluence without using the web editor.

#### Acceptance Criteria

1. WHEN a Markdown file with `confluence_space_key` in the Frontmatter is provided for publishing, THE Publisher SHALL create a new page in the specified space via POST to `/wiki/rest/api/content`
2. WHEN the Frontmatter includes `confluence_parent_id`, THE Publisher SHALL create the page as a child of that parent page
3. WHEN the Frontmatter does not include `confluence_parent_id`, THE Publisher SHALL create the page at the root of the specified space
4. WHEN a page is successfully created, THE Publisher SHALL update the local Markdown file's Frontmatter with the assigned `confluence_page_id` and `confluence_version: 1`
5. WHEN the Frontmatter is missing `confluence_space_key`, THE Publisher SHALL return an error indicating the required field is absent
6. THE Publisher SHALL use the `title` field from Frontmatter as the Confluence page title

### Requirement 3: Update Existing Page

**User Story:** As a team member, I want to update an existing Confluence page from my local Markdown file, so that edits made locally are pushed back to Confluence.

#### Acceptance Criteria

1. WHEN a Markdown file with `confluence_page_id` and `confluence_version` in the Frontmatter is provided for update, THE Publisher SHALL send a PUT request to `/wiki/rest/api/content/{id}` with the version incremented by 1
2. WHEN the remote page version does not match the local `confluence_version`, THE Publisher SHALL abort the update and return a version conflict error including both version numbers
3. WHEN the update succeeds, THE Publisher SHALL update the local Frontmatter `confluence_version` to the new version number returned by the API
4. IF the Confluence API returns a 409 Conflict response, THEN THE Publisher SHALL report the conflict and suggest the user download the latest version before retrying

### Requirement 4: Dry Run Mode

**User Story:** As a team member, I want publish and update operations to default to dry-run mode, so that I can preview what will change before committing to a write operation.

#### Acceptance Criteria

1. THE Publisher SHALL default to dry-run mode for all create and update operations
2. WHILE in Dry_Run mode, THE Publisher SHALL not make any POST or PUT requests to the Confluence API
3. WHILE in Dry_Run mode, THE Publisher SHALL return a preview report containing: the target space, target parent (if any), page title, and a summary of the converted content (character count and image count)
4. WHEN the `confirm` parameter is set to true, THE Publisher SHALL execute the actual create or update operation against the Confluence API
5. WHILE in Dry_Run mode, THE Publisher SHALL validate that the Frontmatter contains all required fields and report any missing fields

### Requirement 5: Image Attachment Upload

**User Story:** As a team member, I want local images referenced in my Markdown file to be uploaded as page attachments, so that images display correctly on the published Confluence page.

#### Acceptance Criteria

1. WHEN a Markdown file references local image files via relative paths, THE Publisher SHALL upload each referenced image as an Attachment to the target page
2. WHEN uploading an attachment, THE Publisher SHALL include the `X-Atlassian-Token: no-check` header as required by the Confluence API
3. WHEN an image file referenced in the Markdown does not exist at the specified relative path, THE Publisher SHALL report a warning listing the missing files and continue processing remaining images
4. WHEN an attachment with the same filename already exists on the page, THE Publisher SHALL upload a new version of that attachment rather than creating a duplicate
5. WHEN the upload is complete, THE Converter SHALL reference the uploaded images using `<ri:attachment ri:filename="...">` tags in the Storage_Format output

### Requirement 6: Frontmatter Management

**User Story:** As a team member, I want consistent YAML frontmatter in my Markdown files, so that the tool can track which local files map to which Confluence pages.

#### Acceptance Criteria

1. THE Publisher SHALL recognise the following Frontmatter fields: `title`, `confluence_page_id`, `confluence_space_key`, `confluence_parent_id`, `confluence_version`, `source` and `exported`
2. WHEN a page is downloaded, THE Publisher SHALL write Frontmatter including `title`, `source`, `exported`, `confluence_page_id`, `confluence_space_key` and `confluence_version`
3. WHEN a new page is published, THE Publisher SHALL add `confluence_page_id` and `confluence_version` to the existing Frontmatter without removing other fields
4. WHEN Frontmatter contains unrecognised fields, THE Publisher SHALL preserve them without modification during any read or write operation
5. FOR ALL Frontmatter read-then-write operations, parsing the Frontmatter then serialising it back SHALL produce output that preserves all original fields and values (round-trip property)

### Requirement 7: Multi-Org Support

**User Story:** As a team member working across multiple client Confluence instances, I want to override the target Confluence URL per tool call, so that I can publish to different organisations without changing environment configuration.

#### Acceptance Criteria

1. WHEN the `confluence_url` parameter is provided to a publish or update tool, THE Publisher SHALL use that URL instead of the `CONFLUENCE_URL` environment variable
2. WHEN the `confluence_url` parameter is not provided, THE Publisher SHALL fall back to the `CONFLUENCE_URL` environment variable
3. IF neither the parameter nor the environment variable is set, THEN THE Publisher SHALL return an error indicating no Confluence URL is available

### Requirement 8: Error Handling and Retry

**User Story:** As a team member, I want publish operations to handle transient failures gracefully, so that temporary network issues do not result in lost work or corrupted pages.

#### Acceptance Criteria

1. WHEN the Confluence API returns HTTP 429, THE Publisher SHALL wait for the duration specified in the `Retry-After` header before retrying
2. WHEN a connection error or timeout occurs during a publish operation, THE Publisher SHALL retry up to 3 times with exponential backoff
3. IF all retries are exhausted, THEN THE Publisher SHALL return an error with the details of the last failure
4. WHEN an API call fails after the page has already been created but before Frontmatter is updated locally, THE Publisher SHALL include the page ID in the error message so the user can recover manually
5. IF the Confluence API returns HTTP 401 or 403, THEN THE Publisher SHALL return a clear authentication or permission error without retrying

### Requirement 9: Publish Page Tree

**User Story:** As a team member, I want to publish a directory of Markdown files as a hierarchy of Confluence pages, so that I can maintain structured documentation locally and push it to Confluence in one operation.

#### Acceptance Criteria

1. WHEN a directory path is provided, THE Publisher SHALL discover all Markdown files within it recursively
2. THE Publisher SHALL use the directory structure to determine parent-child relationships between pages
3. WHEN publishing a tree, THE Publisher SHALL create parent pages before child pages to ensure valid parent IDs
4. WHEN a Markdown file in the tree already has a `confluence_page_id` in its Frontmatter, THE Publisher SHALL update the existing page rather than creating a duplicate
5. WHEN a tree publish is executed in Dry_Run mode, THE Publisher SHALL report the full tree structure that would be created or updated, including page count and hierarchy depth

### Requirement 10: Package and Distribution

**User Story:** As a team lead, I want the tool distributed as a pip-installable package, so that my team can install and use it via `uvx confluence-markdown-mcp` without manual setup.

#### Acceptance Criteria

1. THE package SHALL be installable from PyPI via `pip install confluence-markdown-mcp`
2. THE package SHALL expose a console entry point `confluence-markdown-mcp` that starts the MCP server
3. THE package SHALL include all download tools from the existing confluence-downloader alongside the new publish tools
4. THE package SHALL declare its dependencies with minimum version pins in `pyproject.toml`
5. THE package SHALL run via `uvx confluence-markdown-mcp` without requiring a pre-installed virtual environment
