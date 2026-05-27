# cache.py — The Extraction Cache

Tags: #module #layer1 #cache #performance
Links: [[00_INDEX]] | [[05_extract_semantic]] | [[07_nodes_and_edges_deep_dive]]
File: `graphify/cache.py`

---

## Why a Cache Exists

Semantic extraction (Claude subagents) costs money and takes time. If you run `/graphify` on a corpus of 50 docs, and then add one new doc and run again, you don't want to pay for 50 docs again — only the new one.

The cache solves this by saving each file's extraction result keyed by its content hash. If the file hasn't changed, the cached result is used directly.

---

## The Cache Key — SHA256 of File Content

```python
def file_hash(path: Path) -> str:
    p = Path(path)
    raw = p.read_bytes()
    content = _body_content(raw) if p.suffix.lower() == ".md" else raw
    h = hashlib.sha256()
    h.update(content)
    h.update(b"\x00")                           # separator
    h.update(str(p.resolve()).encode())          # include absolute path
    return h.hexdigest()
```

Breaking this down:

1. **Read the raw bytes** of the file.

2. **Strip YAML frontmatter for `.md` files:**
   ```python
   def _body_content(content: bytes) -> bytes:
       text = content.decode(errors="replace")
       if text.startswith("---"):
           end = text.find("\n---", 3)
           if end != -1:
               return text[end + 4:].encode()
       return content
   ```
   Why? If you add `reviewed: true` to a doc's YAML frontmatter (a metadata change), the content hasn't actually changed. Without this, graphify would re-extract and re-pay for the same content. By hashing only the body below the frontmatter, metadata changes don't invalidate the cache.

3. **Include the absolute path** (separated by a null byte `\x00`). This prevents a collision where two different files with identical content get the same hash. Without this, a `README.md` in two different directories with identical text would share a cache entry — wrong, because they have different `source_file` paths in their extracted nodes.

4. **SHA256** produces a 64-character hex string like `a3c5220ed581781e1dc2f4e9a82eeee...`. This becomes the filename.

---

## Cache Storage — Where Cache Files Live

```python
def cache_dir(root: Path = Path(".")) -> Path:
    d = Path(root) / "graphify-out" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

Cache files live in `graphify-out/cache/{hash}.json`. Each file is a complete extraction result:

```json
{
  "nodes": [...],
  "edges": [...],
  "hyperedges": [...]
}
```

You can see these in the repo at `tests/fixtures/graphify-out/cache/`.

---

## load_cached() — Check if Cache Exists

```python
def load_cached(path: Path, root: Path = Path(".")) -> dict | None:
    try:
        h = file_hash(path)
    except OSError:
        return None                    # file deleted? return None
    entry = cache_dir(root) / f"{h}.json"
    if not entry.exists():
        return None                    # no cache entry
    try:
        return json.loads(entry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None                    # corrupted cache? treat as miss
```

Returns the cached dict if it exists and is valid, or `None` if not. The caller pattern is always:

```python
cached = load_cached(path)
if cached:
    use_cached_result(cached)
else:
    result = expensive_extraction(path)
    save_cached(path, result)
    use_result(result)
```

---

## save_cached() — Atomic Write

```python
def save_cached(path: Path, result: dict, root: Path = Path(".")) -> None:
    h = file_hash(path)
    entry = cache_dir(root) / f"{h}.json"
    tmp = entry.with_suffix(".tmp")          # e.g. "abc123.tmp"
    try:
        tmp.write_text(json.dumps(result), encoding="utf-8")
        os.replace(tmp, entry)               # atomic rename
    except Exception:
        tmp.unlink(missing_ok=True)          # clean up if write fails
        raise
```

**Why write to a `.tmp` file first and then rename?**

`os.replace()` is atomic on most file systems — it either succeeds completely or doesn't happen at all. If you wrote directly to the target file and the process was killed halfway through, you'd have a corrupted partial JSON file. The write-then-rename pattern guarantees the cache file is always either absent or complete.

---

## Semantic Cache — Batch Operations

For semantic extraction (docs/papers), results come back from subagents in batches. Two helper functions manage this:

### check_semantic_cache() — Check all files at once

```python
def check_semantic_cache(files: list[str], root: Path) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    cached_nodes, cached_edges, cached_hyperedges = [], [], []
    uncached = []

    for fpath in files:
        result = load_cached(Path(fpath), root)
        if result is not None:
            cached_nodes.extend(result.get("nodes", []))
            cached_edges.extend(result.get("edges", []))
            cached_hyperedges.extend(result.get("hyperedges", []))
        else:
            uncached.append(fpath)

    return cached_nodes, cached_edges, cached_hyperedges, uncached
```

The pipeline calls this before dispatching any subagents:
- **Cache hits** → nodes/edges collected immediately (no Claude needed)
- **Cache misses** → file paths collected in `uncached` list → these are dispatched to subagents

The `uncached` list is what determines how many subagents are spawned and how much it costs.

### save_semantic_cache() — Save per file

```python
def save_semantic_cache(nodes, edges, hyperedges, root) -> int:
    # Group by source_file
    by_file = defaultdict(lambda: {"nodes": [], "edges": [], "hyperedges": []})
    for n in nodes:
        src = n.get("source_file", "")
        if src:
            by_file[src]["nodes"].append(n)
    for e in edges:
        src = e.get("source_file", "")
        if src:
            by_file[src]["edges"].append(e)
    
    # Save one cache entry per file
    saved = 0
    for fpath, result in by_file.items():
        p = Path(fpath)
        if p.exists():
            save_cached(p, result, root)
            saved += 1
    return saved
```

After subagents return, their results are grouped by `source_file` and saved as individual cache entries — one per file. This means on the next run, each file's cache can be independently validated, and only files whose content changed are re-extracted.

---

## Cache Invalidation — When Does Cache Miss?

The cache misses (and triggers re-extraction) when:

1. **File content changed** — SHA256 changes → different cache filename → old entry not found
2. **File is new** — no cache entry exists yet
3. **Cache file is corrupted** — `json.JSONDecodeError` → treated as miss
4. **File was deleted** — `OSError` reading the file → treated as miss
5. **YAML body changed** (for `.md` files) — frontmatter-only changes do NOT invalidate

The cache never expires on its own. It grows indefinitely. `clear_cache()` deletes everything if you want a fresh start.

---

## The AST Cache (in extract.py)

The AST extractor (`extract.py`) also uses the cache:

```python
# In extract_file():
cached = load_cached(path)
if cached:
    return cached     # instant — no tree-sitter parsing needed

result = run_ast_extraction(path, config)
save_cached(path, result)
return result
```

So even AST extraction (which is free) is cached. This means `--update` on a code-only corpus is nearly instantaneous — only changed code files are re-parsed.

---

## Seeing the Cache in Action

If you run `/graphify .` twice on the same directory:

**First run:**
```
Cache: 0 files hit, 45 files need extraction
Semantic extraction: ~45 files → 3 agents, estimated ~45s
```

**Second run (nothing changed):**
```
Cache: 45 files hit, 0 files need extraction
[skips subagent dispatch entirely]
```

**Second run (one file changed):**
```
Cache: 44 files hit, 1 files need extraction
Semantic extraction: ~1 files → 1 agent, estimated ~45s
```

---

*Next: [[07_nodes_and_edges_deep_dive]] — Every node and edge type explained in full detail*
