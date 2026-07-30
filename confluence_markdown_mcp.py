"""
Confluence Markdown MCP Server

Bidirectional Markdown-to-Confluence sync: download pages as Markdown,
publish/update Markdown files back to Confluence.

Credentials loaded from environment variables (set via Kiro Power MCP config).
CONFLUENCE_URL can be overridden per tool call for multi-org access.
"""

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import frontmatter
import markdown
import requests
from dotenv import load_dotenv
from markdownify import markdownify as md_convert
from mcp.server.fastmcp import FastMCP


def _log(msg: str):
    """Log to stderr (visible in Kiro MCP output window)."""
    print(f"[confluence-markdown] {msg}", file=sys.stderr, flush=True)

# Load .env if present (fallback if env vars not set by Kiro)
_script_dir = Path(__file__).parent
_env_file = _script_dir / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
DEFAULT_CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "").rstrip("/")
DEFAULT_OUTPUT_DIR = "confluence-export"

mcp = FastMCP(
    "confluence-markdown",
    instructions="Bidirectional Markdown-to-Confluence sync: download, publish and update pages"
)


# --- Core Helpers ---


def _get_auth():
    """Return basic auth tuple."""
    if not CONFLUENCE_EMAIL or not CONFLUENCE_API_TOKEN:
        raise ValueError("CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set in .env or MCP config")
    return (CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN)


def _resolve_url(confluence_url: str | None) -> str:
    """Resolve the Confluence base URL."""
    url = (confluence_url or DEFAULT_CONFLUENCE_URL).rstrip("/")
    if not url:
        raise ValueError(
            "No Confluence URL provided and CONFLUENCE_URL is not set. "
            "Pass confluence_url parameter or set CONFLUENCE_URL in env."
        )
    return url


def _resolve_output_dir(output_dir: str | None) -> Path:
    """Resolve the output directory. Defaults to ./confluence-export relative to cwd."""
    if output_dir:
        return Path(output_dir).resolve()
    return Path.cwd() / DEFAULT_OUTPUT_DIR


def _api_request(method: str, base_url: str, path: str, params: dict | None = None,
                 json_data: dict | None = None, files: dict | None = None,
                 extra_headers: dict | None = None, retries: int = 3):
    """Generic authenticated request to Confluence REST API with retry logic."""
    url = f"{base_url}/wiki/rest/api/{path}"
    headers = extra_headers or {}

    for attempt in range(retries):
        try:
            response = requests.request(
                method, url, auth=_get_auth(),
                params=params or {}, json=json_data,
                files=files, headers=headers, timeout=30
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue

            response.raise_for_status()

            if response.status_code == 204:
                return {}
            return response.json()

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                raise

    raise RuntimeError(f"Request to {url} failed after {retries} retries (rate limited)")


def _api_get(base_url: str, path: str, params: dict | None = None, retries: int = 3):
    """Authenticated GET request."""
    return _api_request("GET", base_url, path, params=params, retries=retries)


def _api_post(base_url: str, path: str, json_data: dict | None = None,
              files: dict | None = None, extra_headers: dict | None = None, retries: int = 3):
    """Authenticated POST request."""
    return _api_request("POST", base_url, path, json_data=json_data,
                        files=files, extra_headers=extra_headers, retries=retries)


def _api_put(base_url: str, path: str, json_data: dict | None = None, retries: int = 3):
    """Authenticated PUT request."""
    return _api_request("PUT", base_url, path, json_data=json_data, retries=retries)


# --- Frontmatter Management ---


def _parse_markdown_file(file_path: Path) -> tuple[dict, str]:
    """Parse a markdown file returning (metadata_dict, body_text)."""
    post = frontmatter.load(str(file_path))
    return dict(post.metadata), post.content


def _write_frontmatter(file_path: Path, metadata: dict, body: str):
    """Write markdown file with YAML frontmatter, preserving field order."""
    post = frontmatter.Post(body, **metadata)
    file_path.write_text(frontmatter.dumps(post), encoding="utf-8")


# --- Markdown to Confluence Storage Format ---


def _render_mermaid_blocks(md_text: str, output_dir: Path) -> tuple[str, list[Path]]:
    """Find ```mermaid code blocks, render each to PNG via mmdc, and replace
    with image references. Returns (modified_md, list_of_png_paths)."""
    import subprocess as sp

    pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
    images: list[Path] = []
    counter = [0]

    output_dir.mkdir(parents=True, exist_ok=True)

    def replace_block(match):
        mermaid_code = match.group(1)
        counter[0] += 1
        mmd_file = output_dir / f"mermaid_{counter[0]}.mmd"
        png_file = output_dir / f"mermaid_{counter[0]}.png"

        mmd_file.write_text(mermaid_code, encoding="utf-8")
        _log(f"Rendering mermaid diagram {counter[0]}...")

        try:
            # Use .cmd extension on Windows for npm-installed commands
            mmdc_cmd = "mmdc.cmd" if os.name == "nt" else "mmdc"
            sp.run(
                [mmdc_cmd, "-i", str(mmd_file), "-o", str(png_file), "-b", "transparent", "-s", "4"],
                check=True, capture_output=True, timeout=30
            )
            _log(f"Mermaid diagram {counter[0]} rendered: {png_file.stat().st_size} bytes")
            images.append(png_file)
            return f"![Diagram {counter[0]}]({png_file.name})"
        except FileNotFoundError:
            _log(f"mmdc not found - skipping mermaid diagram {counter[0]}")
            return match.group(0)
        except (sp.CalledProcessError, sp.TimeoutExpired) as e:
            _log(f"Mermaid render failed for diagram {counter[0]}: {e}")
            return match.group(0)

    result = pattern.sub(replace_block, md_text)
    return result, images


def _find_local_images(md_text: str, base_dir: Path) -> list[tuple[str, Path | None]]:
    """Find image references in markdown. Returns list of (ref, resolved_path_or_None)."""
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    images = []
    for match in re.finditer(pattern, md_text):
        ref = match.group(2).strip()
        if ref.startswith("http://") or ref.startswith("https://"):
            continue
        resolved = base_dir / ref
        images.append((ref, resolved if resolved.exists() else None))
    return images


def _prepare_body_for_publish(body: str, file_dir: Path, temp_dir: Path | None = None) -> tuple[str, list[tuple[str, Path | None]], list[Path]]:
    """Render mermaid diagrams and find local images.
    Returns (processed_body, local_images_list, mermaid_png_paths)."""
    import tempfile

    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="confluence_mermaid_"))

    # Render mermaid blocks to PNGs
    processed_body, mermaid_pngs = _render_mermaid_blocks(body, temp_dir)

    # Find all local image references
    local_images = _find_local_images(processed_body, file_dir)

    # Replace mermaid refs that resolved to None with the actual temp dir paths
    for png_path in mermaid_pngs:
        # Find and replace the entry with None path
        for i, (ref, path) in enumerate(local_images):
            if png_path.name in ref and path is None:
                local_images[i] = (ref, png_path)
                break
        else:
            # Not found at all, add it
            local_images.append((png_path.name, png_path))

    return processed_body, local_images, mermaid_pngs


def _markdown_to_storage(md_text: str, local_image_filenames: list[str] | None = None) -> str:
    """Convert markdown to Confluence storage format HTML."""
    if local_image_filenames is None:
        local_image_filenames = []

    # Convert markdown to HTML
    html = markdown.markdown(
        md_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"]
    )

    # Post-process: convert fenced code blocks to Confluence code macros
    def replace_code_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        macro = '<ac:structured-macro ac:name="code">'
        if lang:
            macro += f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
        macro += f'<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>'
        macro += '</ac:structured-macro>'
        return macro

    html = re.sub(
        r'<pre><code class="language-([^"]*)">(.*?)</code></pre>',
        replace_code_block, html, flags=re.DOTALL
    )
    # Handle code blocks without language
    html = re.sub(
        r'<pre><code>(.*?)</code></pre>',
        lambda m: replace_code_block(re.Match.__new__(re.Match) if False else type('M', (), {'group': lambda s, n: "" if n == 1 else m.group(1)})()),
        html, flags=re.DOTALL
    )
    # Simpler approach for no-language code blocks
    html = re.sub(
        r'<pre><code>(.*?)</code></pre>',
        lambda m: f'<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[{m.group(1)}]]></ac:plain-text-body></ac:structured-macro>',
        html, flags=re.DOTALL
    )

    # Convert local image references to ac:image attachment macros
    for filename in local_image_filenames:
        img_pattern = rf'<img[^>]+src="[^"]*{re.escape(filename)}"[^>]*/>'
        replacement = f'<ac:image><ri:attachment ri:filename="{filename}" /></ac:image>'
        html = re.sub(img_pattern, replacement, html)
        # Also handle img without self-close
        img_pattern2 = rf'<img[^>]+src="[^"]*{re.escape(filename)}"[^>]*>'
        html = re.sub(img_pattern2, replacement, html)

    # Convert external image URLs to ac:image ri:url
    def replace_external_img(match):
        tag = match.group(0)
        src_match = re.search(r'src="(https?://[^"]+)"', tag)
        if src_match:
            url = src_match.group(1)
            return f'<ac:image><ri:url ri:value="{url}" /></ac:image>'
        return tag

    html = re.sub(r'<img[^>]+src="https?://[^"]*"[^>]*/?>',  replace_external_img, html)

    return html


# --- Attachment Upload ---


_FULL_WIDTH_METADATA = {
    "properties": {
        "content-appearance-draft": {"value": "full-width"},
        "content-appearance-published": {"value": "full-width"}
    }
}


def _set_space_homepage(base_url: str, space_key: str, page_id: str) -> bool:
    """Set a page as the space homepage."""
    try:
        _api_put(base_url, f"space/{space_key}", json_data={
            "key": space_key,
            "homepage": {"id": page_id}
        })
        return True
    except Exception:
        return False


def _upload_attachment(base_url: str, page_id: str, file_path: Path) -> bool:
    """Upload a file as an attachment to a Confluence page. Updates if it already exists."""
    url = f"{base_url}/wiki/rest/api/content/{page_id}/child/attachment"
    headers = {"X-Atlassian-Token": "no-check"}

    try:
        # Check if attachment already exists
        existing = requests.get(
            url, auth=_get_auth(), params={"filename": file_path.name}, timeout=30
        )
        existing_id = None
        if existing.status_code == 200:
            results = existing.json().get("results", [])
            if results:
                existing_id = results[0].get("id")

        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}

            if existing_id:
                # Update existing attachment
                update_url = f"{url}/{existing_id}/data"
                response = requests.post(
                    update_url, auth=_get_auth(), headers=headers,
                    files=files, timeout=60
                )
            else:
                # Create new attachment
                response = requests.post(
                    url, auth=_get_auth(), headers=headers,
                    files=files, timeout=60
                )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            time.sleep(retry_after)
            return _upload_attachment(base_url, page_id, file_path)

        return response.status_code in (200, 201)

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False


# --- Download Helpers (from confluence-downloader) ---


def _sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > 100:
        name = name[:100]
    return name


def _download_image(base_url: str, url: str, dest_path: Path, retries: int = 3) -> bool:
    """Download an image file from Confluence."""
    for attempt in range(retries):
        try:
            response = requests.get(url, auth=_get_auth(), timeout=30, stream=True)
            if response.status_code == 429:
                time.sleep(int(response.headers.get("Retry-After", 5)))
                continue
            if response.status_code != 200:
                return False
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                return False
    return False


def _extract_and_download_images(base_url: str, html_content: str, page_dir: Path, page_id: str | None = None) -> dict:
    """Find all images in HTML, download them, return URL-to-local-path mapping."""
    if not html_content:
        return {}

    img_patterns = [r'<img[^>]+src=["\']([^"\']+)["\']', r'<ri:url[^>]+ri:value=["\']([^"\']+)["\']']
    urls = set()
    for pattern in img_patterns:
        urls.update(re.findall(pattern, html_content))

    attachment_pattern = r'<ri:attachment\s+ri:filename=["\']([^"\']+)["\']'
    attachment_filenames = re.findall(attachment_pattern, html_content)

    url_mapping = {}
    images_dir = page_dir / "images"
    img_count = 0

    for url in urls:
        if url.startswith("data:") or "/emoticons/" in url or "/icons/" in url:
            continue
        if url.startswith("/"):
            full_url = f"{base_url}{url}"
        elif not url.startswith("http"):
            continue
        else:
            full_url = url
        parsed = urlparse(full_url)
        filename = unquote(parsed.path.split("/")[-1])
        if not filename or filename == "download":
            img_count += 1
            filename = f"image_{img_count}.png"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        dest = images_dir / filename
        if _download_image(base_url, full_url, dest):
            url_mapping[url] = f"images/{filename}"

    if attachment_filenames and page_id:
        for att_filename in attachment_filenames:
            try:
                att_data = _api_get(base_url, f"content/{page_id}/child/attachment", {"filename": att_filename})
                results = att_data.get("results", [])
                if results:
                    download_link = results[0].get("_links", {}).get("download", "")
                    if download_link:
                        full_url = f"{base_url}/wiki{download_link}"
                        safe_filename = re.sub(r'[<>:"/\\|?*]', '_', att_filename).replace(' ', '-')
                        dest = images_dir / safe_filename
                        if _download_image(base_url, full_url, dest):
                            url_mapping[att_filename] = f"images/{safe_filename}"
            except Exception:
                pass

    return url_mapping


def _preprocess_confluence_html(html_content: str, image_mapping: dict) -> str:
    """Convert Confluence-specific XML tags to standard HTML."""
    if not html_content:
        return html_content

    def replace_ac_image(match):
        block = match.group(0)
        att_match = re.search(r'<ri:attachment\s+ri:filename=["\']([^"\']+)["\']', block)
        if att_match:
            filename = att_match.group(1)
            src = image_mapping.get(filename, filename)
            alt = filename.rsplit('.', 1)[0]
            return f'<img src="{src}" alt="{alt}" />'
        url_match = re.search(r'<ri:url\s+ri:value=["\']([^"\']+)["\']', block)
        if url_match:
            url = url_match.group(1)
            src = image_mapping.get(url, url)
            return f'<img src="{src}" alt="" />'
        return ''

    html_content = re.sub(r'<ac:image[^>]*>.*?</ac:image>', replace_ac_image, html_content, flags=re.DOTALL)
    return html_content


def _convert_to_markdown(html_content: str, title: str, url: str,
                         image_mapping: dict | None = None,
                         page_id: str | None = None,
                         space_key: str | None = None,
                         version: int | None = None) -> str:
    """Convert HTML content to markdown with frontmatter including sync metadata."""
    if not html_content:
        return f"---\ntitle: \"{title}\"\n---\n\n# {title}\n\n*No content*\n"

    if image_mapping is None:
        image_mapping = {}

    html_content = _preprocess_confluence_html(html_content, image_mapping)

    markdown_body = md_convert(
        html_content, heading_style="ATX",
        code_language_callback=lambda el: el.get("data-language", ""),
        strip=["script", "style"]
    )

    result = "---\n"
    result += f'title: "{title}"\n'
    if page_id:
        result += f'confluence_page_id: "{page_id}"\n'
    if space_key:
        result += f'confluence_space_key: "{space_key}"\n'
    if version:
        result += f'confluence_version: {version}\n'
    result += f'source: "{url}"\n'
    result += f'exported: "{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}"\n'
    result += "---\n\n"
    result += f"# {title}\n\n"
    result += markdown_body

    return result


def _extract_page_id(page_ref: str) -> str | None:
    """Extract a page ID from a URL or return the ID if numeric."""
    if page_ref.startswith("http"):
        match = re.search(r'/pages/(\d+)', page_ref)
        if match:
            return match.group(1)
        match = re.search(r'pageId=(\d+)', page_ref)
        if match:
            return match.group(1)
    elif page_ref.isdigit():
        return page_ref
    return None


# --- Download Tools ---


@mcp.tool()
def test_connection(confluence_url: str | None = None) -> str:
    """Test connectivity to Confluence and list available spaces.

    Args:
        confluence_url: Confluence base URL. Falls back to env if not provided.
    """
    base_url = _resolve_url(confluence_url)
    try:
        spaces = []
        start = 0
        while True:
            data = _api_get(base_url, "space", {"start": start, "limit": 25})
            spaces.extend(data.get("results", []))
            if data.get("size", 0) < 25:
                break
            start += 25
        lines = [f"Connected to: {base_url}", f"Found {len(spaces)} spaces:", ""]
        for space in spaces:
            lines.append(f"  [{space['key']}] {space['name']}")
        if not spaces:
            lines.append("  (none returned - try accessing spaces directly by key)")
        return "\n".join(lines)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return f"Authentication failed for {base_url}."
        elif e.response.status_code == 403:
            return f"Access denied to {base_url}."
        return f"HTTP error: {e}"
    except requests.exceptions.ConnectionError:
        return f"Could not connect to {base_url}."


@mcp.tool()
def list_spaces(confluence_url: str | None = None) -> str:
    """List all available Confluence spaces.

    Args:
        confluence_url: Confluence base URL. Falls back to env if not provided.
    """
    base_url = _resolve_url(confluence_url)
    spaces = []
    start = 0
    while True:
        data = _api_get(base_url, "space", {"start": start, "limit": 25})
        spaces.extend(data.get("results", []))
        if data.get("size", 0) < 25:
            break
        start += 25
    if not spaces:
        return "No spaces found."
    lines = [f"Found {len(spaces)} spaces on {base_url}:", ""]
    for space in spaces:
        lines.append(f"  [{space['key']}] {space['name']}")
    return "\n".join(lines)


@mcp.tool()
def download_space(space_key: str, confluence_url: str | None = None, output_dir: str | None = None) -> str:
    """Download all pages from a Confluence space as Markdown files.

    Args:
        space_key: The space key (e.g. CTM, DEV).
        confluence_url: Confluence base URL. Falls back to env if not provided.
        output_dir: Directory to save files to. Defaults to ./confluence-export.
    """
    base_url = _resolve_url(confluence_url)
    out_dir = _resolve_output_dir(output_dir)

    pages = []
    start = 0
    while True:
        data = _api_get(base_url, f"space/{space_key}/content/page", {"start": start, "limit": 25, "expand": "ancestors,version"})
        pages.extend(data.get("results", []))
        if data.get("size", 0) < 25:
            break
        start += 25

    if not pages:
        return f"No pages found in space '{space_key}'."

    tree = {}
    for page in pages:
        ancestors = page.get("ancestors", [])
        path_parts = [_sanitize_filename(a.get("title", "untitled")) for a in ancestors]
        path_parts.append(_sanitize_filename(page["title"]))
        tree[page["id"]] = {"title": page["title"], "path": path_parts}

    space_dir = out_dir / _sanitize_filename(space_key)
    space_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    errors = 0

    for page in pages:
        page_info = tree.get(page["id"])
        if not page_info:
            continue
        try:
            full_page = _api_get(base_url, f"content/{page['id']}", {"expand": "body.storage,version"})
            html_content = full_page.get("body", {}).get("storage", {}).get("value", "")
            version = full_page.get("version", {}).get("number")
            page_url = f"{base_url}/wiki/spaces/{space_key}/pages/{page['id']}"

            file_path = space_dir
            for part in page_info["path"][:-1]:
                file_path = file_path / part
            file_path = file_path / (page_info["path"][-1] + ".md")
            file_path.parent.mkdir(parents=True, exist_ok=True)

            image_mapping = _extract_and_download_images(base_url, html_content, file_path.parent, page_id=page["id"])
            md = _convert_to_markdown(html_content, page_info["title"], page_url,
                                      image_mapping, page_id=page["id"],
                                      space_key=space_key, version=version)
            file_path.write_text(md, encoding="utf-8")
            downloaded += 1
            time.sleep(0.5)
        except Exception as e:
            errors += 1

    return f"Space '{space_key}': {downloaded} downloaded, {errors} errors.\nOutput: {space_dir.absolute()}"


@mcp.tool()
def download_page(page_ref: str, confluence_url: str | None = None, output_dir: str | None = None, raw: bool = False) -> str:
    """Download a single Confluence page as Markdown.

    Args:
        page_ref: Page URL or numeric page ID.
        confluence_url: Confluence base URL. Falls back to env if not provided.
        output_dir: Directory to save files to. Defaults to ./confluence-export.
        raw: If True, save raw HTML storage format instead of Markdown.
    """
    base_url = _resolve_url(confluence_url)
    out_dir = _resolve_output_dir(output_dir)
    page_id = _extract_page_id(page_ref)
    if not page_id:
        return f"Could not extract page ID from '{page_ref}'."

    try:
        full_page = _api_get(base_url, f"content/{page_id}", {"expand": "body.storage,version,space"})
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Page {page_id} not found."
        return f"Error: {e}"

    title = full_page.get("title", "Untitled")
    html_content = full_page.get("body", {}).get("storage", {}).get("value", "")
    version = full_page.get("version", {}).get("number")
    space_key = full_page.get("space", {}).get("key")
    page_url = f"{base_url}/wiki/pages/{page_id}"

    out_dir.mkdir(parents=True, exist_ok=True)

    if raw:
        raw_path = out_dir / (_sanitize_filename(title) + ".html")
        raw_path.write_text(html_content, encoding="utf-8")
        return f"Raw HTML saved: {raw_path.absolute()}"

    file_path = out_dir / (_sanitize_filename(title) + ".md")
    image_mapping = _extract_and_download_images(base_url, html_content, file_path.parent, page_id=page_id)
    md = _convert_to_markdown(html_content, title, page_url, image_mapping,
                              page_id=page_id, space_key=space_key, version=version)
    file_path.write_text(md, encoding="utf-8")

    img_note = f" (+{len(image_mapping)} images)" if image_mapping else ""
    return f"Downloaded: {title}{img_note}\nSaved to: {file_path.absolute()}"


@mcp.tool()
def download_page_tree(page_ref: str, confluence_url: str | None = None, output_dir: str | None = None) -> str:
    """Download a page and all its descendants recursively as Markdown.

    Args:
        page_ref: Page URL or numeric page ID for the root page.
        confluence_url: Confluence base URL. Falls back to env if not provided.
        output_dir: Directory to save files to. Defaults to ./confluence-export.
    """
    base_url = _resolve_url(confluence_url)
    out_dir = _resolve_output_dir(output_dir)
    page_id = _extract_page_id(page_ref)
    if not page_id:
        return f"Could not extract page ID from '{page_ref}'."

    try:
        root_page = _api_get(base_url, f"content/{page_id}", {"expand": "body.storage,version,space"})
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Page {page_id} not found."
        return f"Error: {e}"

    root_title = root_page.get("title", "Untitled")
    space_key = root_page.get("space", {}).get("key")

    def get_all_descendants(pid):
        descendants = []
        start = 0
        while True:
            data = _api_get(base_url, f"content/{pid}/child/page", {"start": start, "limit": 25})
            children = data.get("results", [])
            descendants.extend(children)
            for child in children:
                descendants.extend(get_all_descendants(child["id"]))
            if data.get("size", 0) < 25:
                break
            start += 25
        return descendants

    descendants = get_all_descendants(page_id)
    all_pages = [root_page] + descendants
    tree_dir = out_dir / _sanitize_filename(root_title)
    tree_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for page in all_pages:
        pid = page["id"]
        title = page.get("title", "Untitled")
        try:
            if "body" not in page:
                page = _api_get(base_url, f"content/{pid}", {"expand": "body.storage,version,space"})
            html_content = page.get("body", {}).get("storage", {}).get("value", "")
            version = page.get("version", {}).get("number")
            page_url = f"{base_url}/wiki/pages/{pid}"

            if pid == page_id:
                file_path = tree_dir / (_sanitize_filename(title) + ".md")
            else:
                full_data = _api_get(base_url, f"content/{pid}", {"expand": "ancestors"})
                ancestors = full_data.get("ancestors", [])
                path_parts = []
                found_root = False
                for ancestor in ancestors:
                    if ancestor["id"] == page_id:
                        found_root = True
                        continue
                    if found_root:
                        path_parts.append(_sanitize_filename(ancestor.get("title", "untitled")))
                path_parts.append(_sanitize_filename(title))
                file_path = tree_dir
                for part in path_parts[:-1]:
                    file_path = file_path / part
                file_path = file_path / (path_parts[-1] + ".md")

            file_path.parent.mkdir(parents=True, exist_ok=True)
            image_mapping = _extract_and_download_images(base_url, html_content, file_path.parent, page_id=pid)
            md = _convert_to_markdown(html_content, title, page_url, image_mapping,
                                      page_id=pid, space_key=space_key, version=version)
            file_path.write_text(md, encoding="utf-8")
            downloaded += 1
            time.sleep(0.5)
        except Exception:
            pass

    return f"Tree '{root_title}': {downloaded}/{len(all_pages)} pages.\nOutput: {tree_dir.absolute()}"


# --- Publish Tools ---


@mcp.tool()
def publish_page(file_path: str, space_key: str | None = None, parent_page_ref: str | None = None,
                 title: str | None = None, confluence_url: str | None = None, confirm: bool = False) -> str:
    """Create a new Confluence page from a local Markdown file.

    Defaults to dry-run mode. Set confirm=True to actually create the page.
    If the file already has a confluence_page_id in frontmatter, use update_page instead.

    Args:
        file_path: Path to the Markdown file to publish.
        space_key: Confluence space key. Overrides frontmatter confluence_space_key.
        parent_page_ref: Parent page URL or ID. Overrides frontmatter confluence_parent_id.
        title: Page title. Overrides frontmatter title.
        confluence_url: Confluence base URL. Falls back to env if not provided.
        confirm: Set to True to execute the publish. Defaults to dry-run.
    """
    base_url = _resolve_url(confluence_url)
    fp = Path(file_path)

    if not fp.exists():
        return f"File not found: {file_path}"

    metadata, body = _parse_markdown_file(fp)

    # Resolve parameters
    page_title = title or metadata.get("title") or fp.stem
    target_space = space_key or metadata.get("confluence_space_key")
    parent_id = None
    if parent_page_ref:
        parent_id = _extract_page_id(parent_page_ref) or parent_page_ref
    elif metadata.get("confluence_parent_id"):
        parent_id = str(metadata["confluence_parent_id"])

    # If file already has a page ID, suggest update instead
    if metadata.get("confluence_page_id"):
        return (f"This file already has confluence_page_id={metadata['confluence_page_id']}. "
                f"Use update_page instead to update the existing page.")

    # If flagged as homepage, update the space's existing homepage instead of creating new
    if metadata.get("confluence_homepage"):
        if not target_space:
            return "Error: No space_key provided and confluence_space_key not in frontmatter."

        # Fetch the space's current homepage
        try:
            space_data = _api_get(base_url, f"space/{target_space}", {"expand": "homepage"})
            hp = space_data.get("homepage", {})
            hp_id = hp.get("id")
            if not hp_id:
                return f"Error: Space {target_space} has no homepage set."
        except requests.exceptions.HTTPError as e:
            return f"Error fetching space: {e}"

        # Render mermaid diagrams and find local images (keep original body for file write-back)
        original_body = body
        body, local_images, _mermaid_pngs = _prepare_body_for_publish(body, fp.parent)
        image_filenames = [Path(ref).name for ref, path in local_images if path]
        missing_images = [ref for ref, path in local_images if path is None]
        storage_html = _markdown_to_storage(body, image_filenames)

        if not confirm:
            lines = [
                "DRY RUN - publish_page (homepage update) preview:",
                f"  Will update existing homepage: {hp.get('title')} (id: {hp_id})",
                f"  Space: {target_space}",
                f"  New title: {page_title}",
                f"  Content: {len(storage_html)} chars of storage HTML",
                f"  Images: {len(image_filenames)} to upload",
            ]
            if missing_images:
                lines.append(f"  WARNING - missing images: {missing_images}")
            lines.append("")
            lines.append("Set confirm=True to execute this update.")
            return "\n".join(lines)

        # Fetch current version
        remote = _api_get(base_url, f"content/{hp_id}", {"expand": "version"})
        remote_version = remote.get("version", {}).get("number", 1)

        payload = {
            "type": "page",
            "title": page_title,
            "version": {"number": remote_version + 1},
            "body": {"storage": {"value": storage_html, "representation": "storage"}},
            "metadata": _FULL_WIDTH_METADATA
        }

        try:
            result = _api_put(base_url, f"content/{hp_id}", json_data=payload)
        except requests.exceptions.HTTPError as e:
            return f"Failed to update homepage: {e}"

        actual_version = result.get("version", {}).get("number", remote_version + 1)
        page_url = f"{base_url}/wiki/spaces/{target_space}/pages/{hp_id}"

        # Upload images
        uploaded = 0
        for ref, path in local_images:
            if path and path.exists():
                if _upload_attachment(base_url, hp_id, path):
                    uploaded += 1
                time.sleep(0.3)

        # Update local frontmatter
        metadata["confluence_page_id"] = hp_id
        metadata["confluence_space_key"] = target_space
        metadata["confluence_version"] = actual_version
        metadata["source"] = page_url
        _write_frontmatter(fp, metadata, original_body)

        lines = [
            f"Homepage updated: {page_title}",
            f"  Page ID: {hp_id}",
            f"  URL: {page_url}",
            f"  Version: {remote_version} -> {actual_version}",
            f"  Images uploaded: {uploaded}/{len(image_filenames)}",
        ]
        if missing_images:
            lines.append(f"  WARNING - missing images: {missing_images}")
        return "\n".join(lines)

    # Validate
    if not target_space:
        return "Error: No space_key provided and confluence_space_key not in frontmatter."

    # Render mermaid diagrams and find local images (keep original body for file write-back)
    original_body = body
    body, local_images, _mermaid_pngs = _prepare_body_for_publish(body, fp.parent)
    image_filenames = [Path(ref).name for ref, path in local_images if path]
    missing_images = [ref for ref, path in local_images if path is None]

    # Convert to storage format
    storage_html = _markdown_to_storage(body, image_filenames)

    # Dry-run report
    if not confirm:
        lines = [
            "DRY RUN - publish_page preview:",
            f"  Title: {page_title}",
            f"  Space: {target_space}",
            f"  Parent: {parent_id or '(space root)'}",
            f"  Content: {len(storage_html)} chars of storage HTML",
            f"  Images: {len(image_filenames)} to upload",
        ]
        if missing_images:
            lines.append(f"  WARNING - missing images: {missing_images}")
        lines.append("")
        lines.append("Set confirm=True to execute this publish.")
        return "\n".join(lines)

    # Execute publish
    payload = {
        "type": "page",
        "title": page_title,
        "space": {"key": target_space},
        "body": {"storage": {"value": storage_html, "representation": "storage"}},
        "metadata": _FULL_WIDTH_METADATA
    }
    if parent_id:
        payload["ancestors"] = [{"id": parent_id}]

    try:
        result = _api_post(base_url, "content", json_data=payload)
    except requests.exceptions.HTTPError as e:
        return f"Failed to create page: {e}"

    new_page_id = result.get("id")
    new_version = result.get("version", {}).get("number", 1)
    page_url = f"{base_url}/wiki/spaces/{target_space}/pages/{new_page_id}"

    # Upload images
    uploaded = 0
    for ref, path in local_images:
        if path and path.exists():
            if _upload_attachment(base_url, new_page_id, path):
                uploaded += 1
            time.sleep(0.3)

    # Update local frontmatter
    metadata["confluence_page_id"] = new_page_id
    metadata["confluence_space_key"] = target_space
    metadata["confluence_version"] = new_version
    metadata["source"] = page_url
    if parent_id:
        metadata["confluence_parent_id"] = parent_id
    _write_frontmatter(fp, metadata, original_body)

    lines = [
        f"Published: {page_title}",
        f"  Page ID: {new_page_id}",
        f"  URL: {page_url}",
        f"  Version: {new_version}",
        f"  Images uploaded: {uploaded}/{len(image_filenames)}",
    ]
    if missing_images:
        lines.append(f"  WARNING - missing images: {missing_images}")
    return "\n".join(lines)


@mcp.tool()
def update_page(file_path: str, page_ref: str | None = None, title: str | None = None,
                confluence_url: str | None = None, confirm: bool = False) -> str:
    """Update an existing Confluence page from a local Markdown file.

    Defaults to dry-run mode. Set confirm=True to actually update.
    Checks version conflicts before updating.

    Args:
        file_path: Path to the Markdown file.
        page_ref: Page URL or ID. Overrides frontmatter confluence_page_id.
        title: Page title override. Defaults to frontmatter title.
        confluence_url: Confluence base URL. Falls back to env if not provided.
        confirm: Set to True to execute the update. Defaults to dry-run.
    """
    base_url = _resolve_url(confluence_url)
    fp = Path(file_path)

    if not fp.exists():
        return f"File not found: {file_path}"

    metadata, body = _parse_markdown_file(fp)

    # Resolve page ID
    page_id = None
    if page_ref:
        page_id = _extract_page_id(page_ref) or page_ref
    elif metadata.get("confluence_page_id"):
        page_id = str(metadata["confluence_page_id"])

    if not page_id:
        return "Error: No page_ref provided and confluence_page_id not in frontmatter."

    page_title = title or metadata.get("title") or fp.stem
    local_version = metadata.get("confluence_version")

    # Fetch remote page to check version
    try:
        remote_page = _api_get(base_url, f"content/{page_id}", {"expand": "version,space"})
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Page {page_id} not found on Confluence."
        return f"Error fetching page: {e}"

    remote_version = remote_page.get("version", {}).get("number")
    space_key = remote_page.get("space", {}).get("key")

    # Version conflict check
    if local_version and remote_version and int(local_version) != int(remote_version):
        return (f"Version conflict: local has version {local_version}, "
                f"remote has version {remote_version}. "
                f"Download the latest version before updating.")

    # Render mermaid diagrams and find local images (keep original body for file write-back)
    original_body = body
    body, local_images, _mermaid_pngs = _prepare_body_for_publish(body, fp.parent)
    image_filenames = [Path(ref).name for ref, path in local_images if path]
    missing_images = [ref for ref, path in local_images if path is None]

    # Convert to storage format
    storage_html = _markdown_to_storage(body, image_filenames)

    new_version = (remote_version or 0) + 1

    # Dry-run report
    if not confirm:
        lines = [
            "DRY RUN - update_page preview:",
            f"  Page ID: {page_id}",
            f"  Title: {page_title}",
            f"  Space: {space_key}",
            f"  Current version: {remote_version} -> {new_version}",
            f"  Content: {len(storage_html)} chars of storage HTML",
            f"  Images: {len(image_filenames)} to upload",
        ]
        if missing_images:
            lines.append(f"  WARNING - missing images: {missing_images}")
        lines.append("")
        lines.append("Set confirm=True to execute this update.")
        return "\n".join(lines)

    # Execute update
    payload = {
        "type": "page",
        "title": page_title,
        "version": {"number": new_version},
        "body": {"storage": {"value": storage_html, "representation": "storage"}},
        "metadata": _FULL_WIDTH_METADATA
    }

    try:
        result = _api_put(base_url, f"content/{page_id}", json_data=payload)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 409:
            return (f"Conflict (409): The page was modified on Confluence since your last download. "
                    f"Download the latest version and merge your changes.")
        return f"Failed to update page: {e}"

    actual_version = result.get("version", {}).get("number", new_version)
    page_url = f"{base_url}/wiki/spaces/{space_key}/pages/{page_id}"

    # Upload images
    uploaded = 0
    for ref, path in local_images:
        if path and path.exists():
            if _upload_attachment(base_url, page_id, path):
                uploaded += 1
            time.sleep(0.3)

    # Update local frontmatter
    metadata["confluence_page_id"] = page_id
    metadata["confluence_version"] = actual_version
    metadata["confluence_space_key"] = space_key
    metadata["source"] = page_url
    _write_frontmatter(fp, metadata, original_body)

    lines = [
        f"Updated: {page_title}",
        f"  Page ID: {page_id}",
        f"  URL: {page_url}",
        f"  Version: {remote_version} -> {actual_version}",
        f"  Images uploaded: {uploaded}/{len(image_filenames)}",
    ]
    if missing_images:
        lines.append(f"  WARNING - missing images: {missing_images}")
    return "\n".join(lines)


@mcp.tool()
def publish_tree(directory_path: str, space_key: str, parent_page_ref: str | None = None,
                 confluence_url: str | None = None, confirm: bool = False) -> str:
    """Publish a directory of Markdown files as a hierarchy of Confluence pages.

    Folder structure maps to page hierarchy. Defaults to dry-run mode.

    Args:
        directory_path: Path to the directory containing Markdown files.
        space_key: Confluence space key to publish into.
        parent_page_ref: Parent page URL or ID for the root level. Optional.
        confluence_url: Confluence base URL. Falls back to env if not provided.
        confirm: Set to True to execute. Defaults to dry-run.
    """
    base_url = _resolve_url(confluence_url)
    root_dir = Path(directory_path)

    if not root_dir.exists() or not root_dir.is_dir():
        return f"Directory not found: {directory_path}"

    # Discover all .md files
    md_files = sorted(root_dir.rglob("*.md"))
    if not md_files:
        return f"No Markdown files found in {directory_path}"

    parent_id = None
    if parent_page_ref:
        parent_id = _extract_page_id(parent_page_ref) or parent_page_ref

    # Identify subfolders that will become parent pages
    subdirs = sorted(set(
        str(md_file.relative_to(root_dir).parent)
        for md_file in md_files
        if str(md_file.relative_to(root_dir).parent) != "."
    ))

    # Build tree structure for reporting
    creates = []
    updates = []

    for md_file in md_files:
        metadata, body = _parse_markdown_file(md_file)
        rel_path = md_file.relative_to(root_dir)
        page_title = metadata.get("title") or md_file.stem

        if metadata.get("confluence_page_id"):
            updates.append({"file": str(rel_path), "title": page_title, "id": metadata["confluence_page_id"]})
        else:
            creates.append({"file": str(rel_path), "title": page_title})

    # Dry-run report
    if not confirm:
        lines = [
            "DRY RUN - publish_tree preview:",
            f"  Directory: {root_dir.absolute()}",
            f"  Space: {space_key}",
            f"  Parent: {parent_id or '(space root)'}",
            f"  Total files: {len(md_files)}",
            f"  Folder pages to create: {len(subdirs)}",
            f"  File pages to create: {len(creates)}",
            f"  File pages to update: {len(updates)}",
        ]
        if subdirs:
            lines.append("")
            lines.append("Folder parent pages:")
            for sd in subdirs:
                lines.append(f"    [folder] {sd}")
        lines.append("")
        lines.append("Creates:")
        for c in creates[:20]:
            lines.append(f"    + {c['file']} -> \"{c['title']}\"")
        if len(creates) > 20:
            lines.append(f"    ... and {len(creates) - 20} more")
        lines.append("")
        lines.append("Updates:")
        for u in updates[:20]:
            lines.append(f"    ~ {u['file']} -> \"{u['title']}\" (id: {u['id']})")
        if len(updates) > 20:
            lines.append(f"    ... and {len(updates) - 20} more")
        lines.append("")
        lines.append("Set confirm=True to execute this publish.")
        return "\n".join(lines)

    # Execute: process files in directory order (parents before children)
    published = 0
    updated = 0
    errors = 0
    # Track page IDs by relative directory path for parent resolution
    dir_page_map: dict[str, str] = {}
    if parent_id:
        dir_page_map["."] = parent_id

    # First pass: create parent pages for each subfolder
    subdirs = sorted(set(
        str(md_file.relative_to(root_dir).parent)
        for md_file in md_files
        if str(md_file.relative_to(root_dir).parent) != "."
    ))

    for subdir in subdirs:
        parts = Path(subdir).parts
        # Build each level of the path, creating parent pages as needed
        for i in range(len(parts)):
            dir_key = str(Path(*parts[:i+1]))
            if dir_key in dir_page_map:
                continue  # Already created

            folder_name = parts[i]
            # Determine this folder's parent
            if i == 0:
                folder_parent_id = dir_page_map.get(".", parent_id)
            else:
                parent_dir_key = str(Path(*parts[:i]))
                folder_parent_id = dir_page_map.get(parent_dir_key, parent_id)

            # Create a parent page for this folder
            _log(f"Creating folder page: {folder_name}")
            folder_payload = {
                "type": "page",
                "title": folder_name,
                "space": {"key": space_key},
                "body": {"storage": {
                    "value": f"<p>This page contains sub-pages for: <strong>{folder_name}</strong></p>",
                    "representation": "storage"
                }},
                "metadata": _FULL_WIDTH_METADATA
            }
            if folder_parent_id:
                folder_payload["ancestors"] = [{"id": folder_parent_id}]

            try:
                result = _api_post(base_url, "content", json_data=folder_payload)
                folder_page_id = result.get("id")
                dir_page_map[dir_key] = folder_page_id
                published += 1
                time.sleep(0.5)
            except Exception:
                errors += 1

    # Second pass: publish/update each markdown file under its folder's parent page
    for md_file in md_files:
        metadata, body = _parse_markdown_file(md_file)
        original_body = body
        rel_path = md_file.relative_to(root_dir)
        page_title = metadata.get("title") or md_file.stem
        rel_dir = str(rel_path.parent)

        # Determine parent: use the folder's page, or the root parent
        file_parent_id = dir_page_map.get(rel_dir, dir_page_map.get(".", parent_id))

        try:
            if metadata.get("confluence_page_id"):
                # Update existing
                page_id = str(metadata["confluence_page_id"])
                _log(f"Updating: {page_title} (id: {page_id})")
                remote = _api_get(base_url, f"content/{page_id}", {"expand": "version"})
                remote_version = remote.get("version", {}).get("number", 0)

                body, local_images, _ = _prepare_body_for_publish(body, md_file.parent)
                image_filenames = [Path(ref).name for ref, path in local_images if path]
                storage_html = _markdown_to_storage(body, image_filenames)

                payload = {
                    "type": "page", "title": page_title,
                    "version": {"number": remote_version + 1},
                    "body": {"storage": {"value": storage_html, "representation": "storage"}},
                    "metadata": _FULL_WIDTH_METADATA
                }
                result = _api_put(base_url, f"content/{page_id}", json_data=payload)
                new_ver = result.get("version", {}).get("number", remote_version + 1)

                for ref, path in local_images:
                    if path and path.exists():
                        _upload_attachment(base_url, page_id, path)

                metadata["confluence_version"] = new_ver
                _write_frontmatter(md_file, metadata, original_body)
                updated += 1

            else:
                # Create new
                _log(f"Publishing: {page_title}")
                body, local_images, _ = _prepare_body_for_publish(body, md_file.parent)
                image_filenames = [Path(ref).name for ref, path in local_images if path]
                storage_html = _markdown_to_storage(body, image_filenames)

                payload = {
                    "type": "page", "title": page_title,
                    "space": {"key": space_key},
                    "body": {"storage": {"value": storage_html, "representation": "storage"}},
                    "metadata": _FULL_WIDTH_METADATA
                }
                if file_parent_id:
                    payload["ancestors"] = [{"id": file_parent_id}]

                result = _api_post(base_url, "content", json_data=payload)
                new_page_id = result.get("id")
                new_ver = result.get("version", {}).get("number", 1)

                for ref, path in local_images:
                    if path and path.exists():
                        _upload_attachment(base_url, new_page_id, path)

                metadata["confluence_page_id"] = new_page_id
                metadata["confluence_space_key"] = space_key
                metadata["confluence_version"] = new_ver
                if file_parent_id:
                    metadata["confluence_parent_id"] = file_parent_id
                _write_frontmatter(md_file, metadata, original_body)
                published += 1

            time.sleep(0.5)

        except Exception as e:
            errors += 1
            _log(f"ERROR: {page_title} - {e}")

    _log(f"Tree complete: {published} created, {updated} updated, {errors} errors")
    return f"Tree publish complete: {published} created, {updated} updated, {errors} errors."


# --- Project Sync ---


@mcp.tool()
def sync_project(config_path: str | None = None, confirm: bool = False) -> str:
    """Sync a project's Markdown files to Confluence using a confluence.json config file.

    Looks for confluence.json in the current working directory (or at config_path).
    The config specifies the space, docs folder, parent page and Confluence URL.

    Config file format (confluence.json):
    {
        "space_key": "ECM",
        "confluence_url": "https://rebura.atlassian.net",
        "docs_dir": "Design Documents",
        "parent_page_id": "1915061323"
    }

    Defaults to dry-run mode. Set confirm=True to execute.

    Args:
        config_path: Path to confluence.json. Defaults to ./confluence.json in cwd.
        confirm: Set to True to execute the sync. Defaults to dry-run.
    """
    import json

    # Find config file
    if config_path:
        cfg_file = Path(config_path)
    else:
        cfg_file = Path.cwd() / "confluence.json"

    if not cfg_file.exists():
        return (f"Config file not found: {cfg_file}\n"
                f"Create a confluence.json with: space_key, docs_dir, "
                f"and optionally confluence_url and parent_page_id.")

    # Load config
    try:
        config = json.loads(cfg_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in {cfg_file}: {e}"

    space_key = config.get("space_key")
    if not space_key:
        return "Error: confluence.json must have 'space_key' defined."

    docs_dir = config.get("docs_dir", ".")
    confluence_url = config.get("confluence_url")
    parent_page_id = config.get("parent_page_id")

    # Resolve docs_dir relative to the config file location
    docs_path = cfg_file.parent / docs_dir
    if not docs_path.exists() or not docs_path.is_dir():
        return f"Error: docs_dir '{docs_dir}' not found at {docs_path}"

    _log(f"Syncing: space={space_key}, docs={docs_path}, parent={parent_page_id or 'root'}")

    # Delegate to publish_tree
    return publish_tree(
        directory_path=str(docs_path),
        space_key=space_key,
        parent_page_ref=parent_page_id,
        confluence_url=confluence_url,
        confirm=confirm
    )


# --- Entry Point ---


def main():
    mcp.run()


if __name__ == "__main__":
    main()
