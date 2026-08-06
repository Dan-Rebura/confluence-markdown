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
            proc = sp.Popen(
                [mmdc_cmd, "-i", str(mmd_file), "-o", str(png_file), "-b", "white", "-s", "2"],
                stdout=sp.PIPE, stderr=sp.PIPE
            )
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except sp.TimeoutExpired:
                proc.kill()
                proc.communicate()
                _log(f"Mermaid diagram {counter[0]} timed out after 30s - skipping")
                return match.group(0)

            if proc.returncode != 0:
                _log(f"Mermaid diagram {counter[0]} failed: {stderr.decode()[:200]}")
                return match.group(0)

            _log(f"Mermaid diagram {counter[0]} rendered: {png_file.stat().st_size} bytes")
            images.append(png_file)
            return f"![Diagram {counter[0]}]({png_file.name})"
        except FileNotFoundError:
            _log(f"mmdc not found - skipping mermaid diagram {counter[0]}")
            return match.group(0)
        except Exception as e:
            _log(f"Mermaid render error for diagram {counter[0]}: {e}")
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



# --- Project Sync ---


def _extract_title_from_md(file_path: Path) -> str:
    """Extract the page title from a markdown file. Uses first # heading or filename."""
    try:
        content = file_path.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:]
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return file_path.stem


@mcp.tool()
def sync_project(config_path: str | None = None, confirm: bool = False) -> str:
    """Sync a project's Markdown files to Confluence using a confluence.json config file.

    Page state (IDs, versions) is stored in confluence.json under a "pages" key.
    Markdown files are not modified.

    Args:
        config_path: Path to confluence.json. Defaults to ./confluence.json in cwd.
        confirm: Set to True to execute the sync. Defaults to dry-run.
    """
    import json

    if config_path:
        cfg_file = Path(config_path)
    else:
        cfg_file = Path.cwd() / "confluence.json"

    if not cfg_file.exists():
        return f"Config file not found: {cfg_file}"

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
    exclude_dirs = config.get("exclude_dirs", [])
    pages_state = config.get("pages", {})

    base_url = _resolve_url(confluence_url)

    docs_path = cfg_file.parent / docs_dir
    if not docs_path.exists() or not docs_path.is_dir():
        return f"Error: docs_dir '{docs_dir}' not found at {docs_path}"

    # Discover markdown files with exclusions
    normalised_excludes = [ex.replace("\\", "/") for ex in exclude_dirs]
    md_files = sorted(
        f for f in docs_path.rglob("*.md")
        if not any(
            str(f.relative_to(docs_path)).replace("\\", "/").startswith(ex)
            for ex in normalised_excludes
        )
    )

    if not md_files:
        return f"No Markdown files found in {docs_path}"

    # Categorise files
    creates = []
    updates = []
    homepage_files = []

    for md_file in md_files:
        rel_key = str(md_file.relative_to(docs_path)).replace("\\", "/")
        page_info = pages_state.get(rel_key, {})
        title = _extract_title_from_md(md_file)

        if page_info.get("confluence_homepage"):
            homepage_files.append({"file": md_file, "key": rel_key, "title": title, "info": page_info})
        elif page_info.get("confluence_page_id"):
            updates.append({"file": md_file, "key": rel_key, "title": title, "info": page_info})
        else:
            creates.append({"file": md_file, "key": rel_key, "title": title, "info": page_info})

    _log(f"Syncing: space={space_key}, homepage={len(homepage_files)}, updates={len(updates)}, creates={len(creates)}")

    # Identify subfolders
    subdirs = sorted(set(
        str(md_file.relative_to(docs_path).parent).replace("\\", "/")
        for md_file in md_files
        if str(md_file.relative_to(docs_path).parent) != "."
    ))

    # Dry-run report
    if not confirm:
        lines = [
            "DRY RUN - sync_project preview:",
            f"  Space: {space_key}",
            f"  Parent: {parent_page_id or '(space root)'}",
            f"  Total files: {len(md_files)}",
            f"  Folder pages: {len(subdirs)}",
            f"  Homepage: {len(homepage_files)}",
            f"  Updates: {len(updates)}",
            f"  Creates: {len(creates)}",
        ]
        if subdirs:
            lines.append("")
            lines.append("Folders:")
            for sd in subdirs:
                lines.append(f"    [folder] {sd}")
        if homepage_files:
            lines.append("")
            lines.append("Homepage:")
            for h in homepage_files:
                lines.append(f"    * {h['key']} -> \"{h['title']}\"")
        if updates:
            lines.append("")
            lines.append("Updates:")
            for u in updates[:20]:
                lines.append(f"    ~ {u['key']} -> \"{u['title']}\"")
            if len(updates) > 20:
                lines.append(f"    ... and {len(updates) - 20} more")
        if creates:
            lines.append("")
            lines.append("Creates:")
            for c in creates[:20]:
                lines.append(f"    + {c['key']} -> \"{c['title']}\"")
            if len(creates) > 20:
                lines.append(f"    ... and {len(creates) - 20} more")
        lines.append("")
        lines.append("Set confirm=True to execute.")
        return "\n".join(lines)

    # --- Execute ---
    published = 0
    updated = 0
    errors = 0
    dir_page_map: dict[str, str] = {}
    if parent_page_id:
        dir_page_map["."] = parent_page_id

    # Create folder parent pages
    for subdir in subdirs:
        parts = subdir.split("/")
        for i in range(len(parts)):
            dir_key = "/".join(parts[:i+1])
            if dir_key in dir_page_map:
                continue
            folder_name = parts[i]
            folder_parent_id = dir_page_map.get("/".join(parts[:i]) if i > 0 else ".", parent_page_id)
            _log(f"Creating folder page: {folder_name}")
            try:
                result = _api_post(base_url, "content", json_data={
                    "type": "page", "title": folder_name,
                    "space": {"key": space_key},
                    "body": {"storage": {"value": f"<p><strong>{folder_name}</strong></p>", "representation": "storage"}},
                    "metadata": _FULL_WIDTH_METADATA,
                    **({"ancestors": [{"id": folder_parent_id}]} if folder_parent_id else {})
                })
                dir_page_map[dir_key] = result.get("id")
                published += 1
                time.sleep(0.5)
            except Exception as e:
                _log(f"ERROR folder {folder_name}: {e}")
                errors += 1

    # Process homepage
    for item in homepage_files:
        md_file, rel_key, page_title = item["file"], item["key"], item["title"]
        try:
            body = md_file.read_text(encoding="utf-8")
            body, local_images, _ = _prepare_body_for_publish(body, md_file.parent)
            image_filenames = [Path(ref).name for ref, path in local_images if path]
            storage_html = _markdown_to_storage(body, image_filenames)

            space_data = _api_get(base_url, f"space/{space_key}", {"expand": "homepage"})
            hp_id = space_data.get("homepage", {}).get("id")
            if not hp_id:
                _log(f"ERROR: No homepage in space {space_key}")
                errors += 1
                continue

            remote = _api_get(base_url, f"content/{hp_id}", {"expand": "version"})
            hp_version = remote.get("version", {}).get("number", 1)

            _log(f"Updating homepage: {page_title}")
            result = _api_put(base_url, f"content/{hp_id}", json_data={
                "type": "page", "title": page_title,
                "version": {"number": hp_version + 1},
                "body": {"storage": {"value": storage_html, "representation": "storage"}},
                "metadata": _FULL_WIDTH_METADATA
            })
            new_ver = result.get("version", {}).get("number", hp_version + 1)

            for ref, path in local_images:
                if path and path.exists():
                    _upload_attachment(base_url, hp_id, path)

            pages_state[rel_key] = {"confluence_page_id": hp_id, "confluence_version": new_ver, "confluence_homepage": True}
            updated += 1
            time.sleep(0.5)
        except Exception as e:
            _log(f"ERROR: {page_title} - {e}")
            errors += 1

    # Process updates
    for item in updates:
        md_file, rel_key, page_title = item["file"], item["key"], item["title"]
        page_id = str(item["info"]["confluence_page_id"])
        try:
            body = md_file.read_text(encoding="utf-8")
            body, local_images, _ = _prepare_body_for_publish(body, md_file.parent)
            image_filenames = [Path(ref).name for ref, path in local_images if path]
            storage_html = _markdown_to_storage(body, image_filenames)

            remote = _api_get(base_url, f"content/{page_id}", {"expand": "version"})
            remote_version = remote.get("version", {}).get("number", 0)

            _log(f"Updating: {page_title} (id: {page_id})")
            result = _api_put(base_url, f"content/{page_id}", json_data={
                "type": "page", "title": page_title,
                "version": {"number": remote_version + 1},
                "body": {"storage": {"value": storage_html, "representation": "storage"}},
                "metadata": _FULL_WIDTH_METADATA
            })
            new_ver = result.get("version", {}).get("number", remote_version + 1)

            for ref, path in local_images:
                if path and path.exists():
                    _upload_attachment(base_url, page_id, path)

            pages_state[rel_key] = {"confluence_page_id": page_id, "confluence_version": new_ver}
            updated += 1
            time.sleep(0.5)
        except Exception as e:
            _log(f"ERROR: {page_title} - {e}")
            errors += 1

    # Process creates
    for item in creates:
        md_file, rel_key, page_title = item["file"], item["key"], item["title"]
        rel_dir = str(md_file.relative_to(docs_path).parent).replace("\\", "/")
        file_parent_id = dir_page_map.get(rel_dir, dir_page_map.get(".", parent_page_id))

        try:
            body = md_file.read_text(encoding="utf-8")
            body, local_images, _ = _prepare_body_for_publish(body, md_file.parent)
            image_filenames = [Path(ref).name for ref, path in local_images if path]
            storage_html = _markdown_to_storage(body, image_filenames)

            _log(f"Publishing: {page_title}")
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

            pages_state[rel_key] = {"confluence_page_id": new_page_id, "confluence_version": new_ver}
            published += 1
            time.sleep(0.5)
        except Exception as e:
            _log(f"ERROR: {page_title} - {e}")
            errors += 1

    # Save state
    config["pages"] = pages_state
    cfg_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    _log(f"Sync complete: {published} created, {updated} updated, {errors} errors")
    return f"Sync complete: {published} created, {updated} updated, {errors} errors."


# --- Entry Point ---


def migrate_frontmatter(config_path: str, strip: bool = False) -> str:
    """Migrate frontmatter-based page state to confluence.json.

    Scans all markdown files in docs_dir, extracts confluence_page_id,
    confluence_version and confluence_homepage from frontmatter, and
    writes them into the pages map in confluence.json.

    Args:
        config_path: Path to confluence.json.
        strip: If True, remove confluence_* fields from file frontmatter after migration.
    """
    import json

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        return f"Config file not found: {cfg_file}"

    config = json.loads(cfg_file.read_text(encoding="utf-8"))
    docs_dir = config.get("docs_dir", ".")
    docs_path = cfg_file.parent / docs_dir

    if not docs_path.exists():
        return f"docs_dir not found: {docs_path}"

    pages_state = config.get("pages", {})
    migrated = 0

    for md_file in sorted(docs_path.rglob("*.md")):
        metadata, body = _parse_markdown_file(md_file)
        page_id = metadata.get("confluence_page_id")
        if not page_id:
            continue

        rel_key = str(md_file.relative_to(docs_path)).replace("\\", "/")
        entry = {"confluence_page_id": str(page_id)}

        version = metadata.get("confluence_version")
        if version:
            entry["confluence_version"] = int(version)

        if metadata.get("confluence_homepage"):
            entry["confluence_homepage"] = True

        pages_state[rel_key] = entry
        migrated += 1

        # Optionally strip confluence fields from frontmatter
        if strip:
            keys_to_remove = [k for k in metadata if k.startswith("confluence_") or k in ("source", "exported")]
            for k in keys_to_remove:
                del metadata[k]
            _write_frontmatter(md_file, metadata, body)

    config["pages"] = pages_state
    cfg_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    result = f"Migrated {migrated} pages to confluence.json"
    if strip:
        result += " (frontmatter stripped)"
    return result


def main():
    mcp.run()


if __name__ == "__main__":
    main()
