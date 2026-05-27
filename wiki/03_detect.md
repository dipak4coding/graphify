# detect.py — File Discovery and Classification

Tags: #module #layer1
Links: [[00_INDEX]] | [[02_pipeline_overview]] | [[04_extract_ast]]
File: `graphify/detect.py`

---

## What This Module Does

`detect.py` is the **scout**. Before graphify reads a single file's content, `detect()` walks the folder tree and answers three questions:
1. What files are here?
2. What type is each file (code / doc / paper / image / video)?
3. Should this corpus even have a graph (is it big enough)?

It returns a structured dict that all later steps rely on.

---

## The Entry Point: `detect(root)`

```python
def detect(root: Path, *, follow_symlinks: bool = False) -> dict:
```

**Input:** a `Path` to the folder you want to scan.
**Output:** a dict with keys `files`, `total_files`, `total_words`, `needs_graph`, `warning`, `skipped_sensitive`.

### Inside detect() — step by step

#### 1. Load .graphifyignore patterns

```python
ignore_patterns = _load_graphifyignore(root)
```

`_load_graphifyignore()` climbs up the directory tree from `root`, reading any `.graphifyignore` file it finds (like how `.gitignore` discovery works). It stops at the git repo root (`.git` directory). Patterns use gitignore glob syntax.

#### 2. Set up scan paths

```python
scan_paths = [root]
if memory_dir.exists():
    scan_paths.append(memory_dir)
```

`memory_dir` is `graphify-out/memory/` — this is where saved Q&A results live (see [[14_ingest]]). It's always included because previous query answers should be part of the graph. It's scanned separately so its `.md` files bypass the normal noise-dir filters.

#### 3. Walk the directory tree

```python
for dirpath, dirnames, filenames in os.walk(scan_root, followlinks=follow_symlinks):
```

`os.walk()` is Python's built-in recursive directory walker. For each directory it visits, it gives you:
- `dirpath`: the current directory path (as a string)
- `dirnames`: list of subdirectory names in this directory
- `filenames`: list of file names in this directory

**Critical trick — pruning `dirnames` in-place:**
```python
dirnames[:] = [
    d for d in dirnames
    if not d.startswith(".")
    and not _is_noise_dir(d)
    and not _is_ignored(dp / d, root, ignore_patterns)
]
```

By modifying `dirnames` *in-place* (using `[:]`), `os.walk()` is told to never descend into those directories at all. This is why graphify never enters `node_modules`, `venv`, `.git`, `__pycache__`, etc. It doesn't walk in and then filter — it prevents the walk from happening.

#### 4. Filter each file

For every file found:
- Skip hidden files (name starts with `.`)
- Skip files inside `graphify-out/converted/` (sidecar files graphify itself created)
- Skip if matched by `.graphifyignore`
- Skip if `_is_sensitive()` matches (`.env`, `.pem`, `id_rsa`, anything with "credential" in name)
- Call `classify_file()` to get its type

#### 5. Office file conversion

```python
if p.suffix.lower() in OFFICE_EXTENSIONS:
    md_path = convert_office_file(p, converted_dir)
```

`.docx` and `.xlsx` files can't be read as plain text. `convert_office_file()` converts them to markdown sidecar files in `graphify-out/converted/` using `python-docx` and `openpyxl`. The markdown sidecar is what actually gets extracted.

---

## classify_file() — The Type Classifier

```python
def classify_file(path: Path) -> FileType | None:
    ext = path.suffix.lower()
    if ext in CODE_EXTENSIONS:
        return FileType.CODE
    if ext in PAPER_EXTENSIONS:
        ...
        return FileType.PAPER
    if ext in DOC_EXTENSIONS:
        if _looks_like_paper(path):
            return FileType.PAPER
        return FileType.DOCUMENT
    ...
    return None  # unknown type — skip it
```

**The clever bit — `_looks_like_paper(path)`:**

A `.md` file might actually be a converted academic paper. `_looks_like_paper()` reads the first 3000 characters and counts how many "paper signals" match:

```python
_PAPER_SIGNALS = [
    re.compile(r'\barxiv\b'),
    re.compile(r'\bdoi\s*:'),
    re.compile(r'\babstract\b'),
    re.compile(r'\bproceedings\b'),
    re.compile(r'\[\\d+\]'),   # numbered citation [1], [23]
    re.compile(r'\\cite\{'),   # LaTeX citation
    re.compile(r'\bwe propose\b'),
    # ... more
]
_PAPER_SIGNAL_THRESHOLD = 3
```

If 3 or more signals match, it's classified as `PAPER` rather than `DOCUMENT`. This matters because papers get semantic extraction with citation-focused prompts.

---

## _is_sensitive() — The Security Guard

```python
_SENSITIVE_PATTERNS = [
    re.compile(r'(^|[\\/])\.(env|envrc)(\.|$)'),
    re.compile(r'\.(pem|key|p12|pfx|cert|crt)$'),
    re.compile(r'(credential|secret|passwd|password|token|private_key)'),
    re.compile(r'(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$'),
    ...
]
```

These files are silently skipped. Their paths are recorded in `skipped_sensitive` so the skill can report "X sensitive files were skipped" without revealing which files they were.

---

## _is_noise_dir() — The Junk Filter

```python
_SKIP_DIRS = {
    "venv", ".venv", "env", ".env",
    "node_modules", "__pycache__", ".git",
    "dist", "build", "target", "out",
    "site-packages", "lib64",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", "*.egg-info",
}

def _is_noise_dir(part: str) -> bool:
    if part in _SKIP_DIRS:
        return True
    if part.endswith("_venv") or part.endswith("_env"):
        return True
    if part.endswith(".egg-info"):
        return True
    return False
```

This catches common virtual environments and build artifacts. The pattern-based checks (`endswith("_venv")`) handle cases like `myproject_venv` that aren't in the static set.

---

## The Manifest System (for --update)

```python
def save_manifest(files: dict[str, list[str]], manifest_path: str) -> None:
    manifest: dict[str, float] = {}
    for file_list in files.values():
        for f in file_list:
            manifest[f] = Path(f).stat().st_mtime
    Path(manifest_path).write_text(json.dumps(manifest, indent=2))
```

After a full run, graphify saves each file's **modification time** (`st_mtime`) to `graphify-out/manifest.json`. On the next `--update` run:

```python
def detect_incremental(root: Path, manifest_path: str) -> dict:
    full = detect(root)
    manifest = load_manifest(manifest_path)
    
    for f in file_list:
        stored_mtime = manifest.get(f)
        current_mtime = Path(f).stat().st_mtime
        if stored_mtime is None or current_mtime > stored_mtime:
            new_files[ftype].append(f)    # changed — re-extract
        else:
            unchanged_files[ftype].append(f)  # unchanged — skip
```

`st_mtime` is the Unix timestamp of when the file was last modified. If it's greater than what was stored, the file changed.

Also detects **deleted files** — files that were in the manifest but no longer exist. These become "ghost nodes" that need to be pruned from the graph.

---

## Corpus Health Warnings

```python
CORPUS_WARN_THRESHOLD = 50_000    # words — below this: you may not need a graph
CORPUS_UPPER_THRESHOLD = 500_000  # words — above this: warn about token cost
FILE_COUNT_UPPER = 200            # files — above this: warn about token cost
```

- **Too small:** "Corpus is ~8,000 words — fits in a single context window. You may not need a graph." The whole corpus can be given to an AI at once — there's no need for a navigation system.
- **Too large:** "Large corpus: 312 files · ~820,000 words. Semantic extraction will be expensive." The user should run on a subfolder or use `--no-semantic`.

---

## Extension Points (for developers)

To add a new file type:
1. Add the extension to the appropriate set (`CODE_EXTENSIONS`, `DOC_EXTENSIONS`, etc.) in `detect.py`
2. Handle it in `classify_file()`
3. Add extraction logic in `extract.py` (for code) or rely on semantic subagents (for docs)

To add a new ignore pattern:
1. Add a regex to `_SENSITIVE_PATTERNS` (for sensitive files)
2. Add a directory name to `_SKIP_DIRS` (for noise directories)

---

*Next: [[04_extract_ast]] — How code structure is read with tree-sitter*
