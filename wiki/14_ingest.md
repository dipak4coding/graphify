# ingest.py — URL Ingestion

Tags: #module #layer5 #ingest
Links: [[00_INDEX]] | [[13_wiki_generation]] | [[17_incremental_update]]
File: `graphify/ingest.py`

---

## What This Module Does

`ingest.py` implements `/graphify add <url>` — the ability to fetch any URL and save it as a graphify-ready file in your `/raw` folder. The saved file is then processed on the next `--update` run.

Think of it as: "I found something interesting on the internet. Add it to my knowledge graph."

---

## Supported URL Types

`_detect_url_type()` classifies a URL into one of 7 types:

```python
def _detect_url_type(url: str) -> str:
    lower = url.lower()
    if "twitter.com" in lower or "x.com" in lower:
        return "tweet"
    if "arxiv.org" in lower:
        return "arxiv"
    if "github.com" in lower:
        return "github"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    return "webpage"
```

| Type | Handler | Output |
|------|---------|--------|
| `tweet` | oEmbed API | `.md` with tweet text |
| `arxiv` | arXiv HTML page | `.md` with title, authors, abstract |
| `github` | Generic webpage | `.md` with rendered markdown |
| `youtube` | yt-dlp audio download | `.mp3` (transcribed on next run) |
| `pdf` | Direct download | `.pdf` |
| `image` | Direct download | `.png`/`.jpg` etc |
| `webpage` | HTML → markdown | `.md` with page content |

---

## The ingest() Function

```python
def ingest(url: str, target_dir: Path, author: str | None = None, contributor: str | None = None) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    url_type = _detect_url_type(url)
    
    try:
        validate_url(url)
    except ValueError as exc:
        raise ValueError(f"ingest: {exc}") from exc
    
    # Handle each type
    if url_type == "pdf":
        out = _download_binary(url, ".pdf", target_dir)
    elif url_type == "image":
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
        out = _download_binary(url, suffix, target_dir)
    elif url_type == "youtube":
        from graphify.transcribe import download_audio
        out = download_audio(url, target_dir)
    elif url_type == "tweet":
        content, filename = _fetch_tweet(url, author, contributor)
    elif url_type == "arxiv":
        content, filename = _fetch_arxiv(url, author, contributor)
    else:
        content, filename = _fetch_webpage(url, author, contributor)
    
    # Avoid overwriting existing files
    out_path = target_dir / filename
    counter = 1
    while out_path.exists() and counter < 1000:
        stem = Path(filename).stem
        out_path = target_dir / f"{stem}_{counter}.md"
        counter += 1
    
    out_path.write_text(content, encoding="utf-8")
    return out_path
```

### Security: validate_url()

Before fetching anything, `security.validate_url()` checks:
- URL uses `http://` or `https://` (blocks `file://`, `ftp://`, etc.)
- URL doesn't point to internal/private IPs (blocks SSRF — Server-Side Request Forgery)
- Hostname is valid

---

## How Each Handler Works

### _fetch_tweet()

Uses Twitter's **oEmbed API** — a standard for embedding tweet previews:

```python
oembed_api = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}&omit_script=true"
data = json.loads(safe_fetch_text(oembed_api))
tweet_text = re.sub(r"<[^>]+>", "", data.get("html", "")).strip()
tweet_author = data.get("author_name", "unknown")
```

The oEmbed API returns HTML with the tweet text. Graphify strips HTML tags with a regex and saves the plain text.

Saved as markdown with YAML frontmatter:
```yaml
---
source_url: "https://twitter.com/..."
type: tweet
author: "karpathy"
captured_at: 2024-01-15T10:30:00Z
contributor: "dipak"
---

# Tweet by @karpathy

Simplest Transformer implementation I've seen...

Source: https://twitter.com/...
```

The YAML frontmatter is what graphify's semantic subagents read to extract `source_url`, `author`, and `contributor` into node metadata.

### _fetch_arxiv()

1. Extracts the arXiv ID from the URL using regex: `r"(\d{4}\.\d{4,5})"`
   - `"https://arxiv.org/abs/1706.03762"` → `"1706.03762"`

2. Fetches the abstract page from `https://export.arxiv.org/abs/{id}`

3. Extracts title, authors, and abstract with regex on the HTML:
   ```python
   abstract_match = re.search(r'class="abstract[^"]*"[^>]*>(.*?)</blockquote>', html, re.DOTALL)
   title_match = re.search(r'class="title[^"]*"[^>]*>(.*?)</h1>', html, re.DOTALL)
   authors_match = re.search(r'class="authors"[^>]*>(.*?)</div>', html, re.DOTALL)
   ```

4. Saves as markdown with frontmatter:
   ```yaml
   ---
   source_url: "https://arxiv.org/abs/1706.03762"
   arxiv_id: "1706.03762"
   type: paper
   title: "Attention Is All You Need"
   paper_authors: "Vaswani, Shazeer, Parmar..."
   captured_at: 2024-01-15T10:30:00Z
   ---
   ```

### _fetch_webpage()

1. Downloads the full HTML with `safe_fetch_text()`
2. Extracts the `<title>` tag with regex
3. Converts HTML to markdown using `html2text` (library) if available, else strips tags with regex
4. Saves first 12,000 characters of the converted markdown (prevents gigantic files)

### _html_to_markdown()

```python
def _html_to_markdown(html: str, url: str) -> str:
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0       # don't wrap lines
        return h.handle(html)
    except ImportError:
        # Fallback: regex strip
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)     # strip all remaining tags
        text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
        return text[:8000]                         # limit size
```

Prefers the `html2text` library (better quality). Falls back to regex stripping if not installed. The fallback:
1. Removes `<script>` blocks entirely (don't want JavaScript code in the graph)
2. Removes `<style>` blocks (don't want CSS)
3. Strips all remaining HTML tags
4. Collapses whitespace
5. Truncates to 8,000 characters

---

## safe_filename() — URL → Filename

```python
def _safe_filename(url: str, suffix: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = parsed.netloc + parsed.path                 # "arxiv.org/abs/1706.03762"
    name = re.sub(r"[^\w\-]", "_", name).strip("_")   # "arxiv_org_abs_1706_03762"
    name = re.sub(r"_+", "_", name)[:80]              # deduplicate underscores, limit to 80 chars
    return name + suffix                               # "arxiv_org_abs_1706_03762.md"
```

Converts the URL to a safe filename:
1. Extract the network location + path (`arxiv.org/abs/1706.03762`)
2. Replace any character that's not a word character or hyphen with underscore
3. Strip leading/trailing underscores
4. Collapse multiple consecutive underscores
5. Limit to 80 characters (avoid filesystem path-too-long errors)

---

## save_query_result() — The Feedback Loop

```python
def save_query_result(question, answer, memory_dir, query_type, source_nodes) -> Path:
    memory_dir = Path(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^\w]", "_", question.lower())[:50].strip("_")
    filename = f"query_{now.strftime('%Y%m%d_%H%M%S')}_{slug}.md"
    
    frontmatter = ["---", f'type: "{query_type}"', f'date: "{now.isoformat()}"', ...]
    body = [f"# Q: {question}", "", "## Answer", "", answer]
    if source_nodes:
        body += ["", "## Source Nodes", ""] + [f"- {n}" for n in source_nodes]
    
    content = "\n".join(frontmatter + body)
    out_path = memory_dir / filename
    out_path.write_text(content, encoding="utf-8")
    return out_path
```

Every time you run `/graphify query "..."`, the answer is saved to `graphify-out/memory/` as a markdown file. On the next `--update` run, this file is treated as a document and extracted into the graph.

**Why this matters:** The graph grows smarter from your questions. If you asked "How does authentication work?" and the answer referenced `AuthModule` and `SessionStore`, those connections become graph nodes. Next time someone asks a related question, the graph already has the answer's context.

This is the **feedback loop**: use the graph → save the answer → update the graph → better future answers.

---

*Next: [[17_incremental_update]] — How --update mode works*
