# Incremental Update — --update Mode

Tags: #concept #lifecycle #performance
Links: [[00_INDEX]] | [[03_detect]] | [[06_cache]] | [[14_ingest]]

---

## The Problem --update Solves

Every time you run a full `/graphify .`, all files get re-detected and all non-cached files get re-extracted. For a 100-file corpus, this is slow and expensive even with caching — you still have to check every file's hash.

`--update` is the incremental solution: only re-extract files that **changed since the last run**.

---

## The Manifest — Tracking What Has Changed

After every successful run, graphify saves a **manifest** — a JSON file recording each processed file's modification timestamp:

```python
def save_manifest(files: dict[str, list[str]], manifest_path: str) -> None:
    manifest: dict[str, float] = {}
    for file_list in files.values():
        for f in file_list:
            manifest[f] = Path(f).stat().st_mtime   # Unix timestamp
    Path(manifest_path).write_text(json.dumps(manifest, indent=2))
```

`stat().st_mtime` is the file's last-modification time as a Unix timestamp (float, seconds since epoch). Stored in `graphify-out/manifest.json`.

Example manifest:
```json
{
  "src/client.py": 1705312345.678,
  "src/models.py": 1705298765.432,
  "README.md": 1705301234.567
}
```

---

## detect_incremental() — The Diff

```python
def detect_incremental(root: Path, manifest_path: str) -> dict:
    full = detect(root)              # discover all current files
    manifest = load_manifest(manifest_path)

    if not manifest:
        # No previous run — treat everything as new
        full["incremental"] = True
        full["new_files"] = full["files"]
        return full

    new_files = {k: [] for k in full["files"]}
    unchanged_files = {k: [] for k in full["files"]}

    for ftype, file_list in full["files"].items():
        for f in file_list:
            stored_mtime = manifest.get(f)
            current_mtime = Path(f).stat().st_mtime
            if stored_mtime is None or current_mtime > stored_mtime:
                new_files[ftype].append(f)        # new or changed
            else:
                unchanged_files[ftype].append(f)  # unchanged
    
    # Files in manifest that no longer exist → ghost nodes to prune
    current_files = {f for flist in full["files"].values() for f in flist}
    deleted_files = [f for f in manifest if f not in current_files]
    
    full["new_files"] = new_files
    full["unchanged_files"] = unchanged_files
    full["deleted_files"] = deleted_files
    full["new_total"] = sum(len(v) for v in new_files.values())
    return full
```

The key comparison: `current_mtime > stored_mtime`. If the current modification time is greater than what was stored, the file was modified since the last run.

---

## The Ghost Node Problem

When a file is **deleted**, all nodes from that file become "ghost nodes" — they exist in the graph but their source file is gone. On `--update`, ghost nodes are pruned:

```python
deleted = set(incremental.get("deleted_files", []))
if deleted:
    to_remove = [n for n, d in G_existing.nodes(data=True) if d.get("source_file") in deleted]
    G_existing.remove_nodes_from(to_remove)
    print(f"Pruned {len(to_remove)} ghost nodes from {len(deleted)} deleted file(s)")
```

This iterates all nodes and removes any whose `source_file` matches a deleted file.

---

## The Merge Step — Combining Old and New

After extracting the changed files:

```python
# Load existing graph
existing_data = json.loads(Path("graphify-out/graph.json").read_text())
G_existing = json_graph.node_link_graph(existing_data, edges="links")

# Load new extraction results
new_extraction = json.loads(Path("graphify-out/.graphify_extract.json").read_text())
G_new = build_from_json(new_extraction)

# Merge
G_existing.update(G_new)
```

`G_existing.update(G_new)` is NetworkX's graph merge:
- New nodes from `G_new` are added to `G_existing`
- If a node already exists, its attributes are updated with the new values
- New edges are added
- Existing edges with the same key are updated

This is how `--update` works: the old graph is the base, and only the changed files' nodes/edges are merged in.

---

## Code-Only Fast Path

```python
code_exts = {'.py','.ts','.js','.go','.rs','.java','.cpp','.c','.rb','.swift','.kt','.cs',...}
all_changed = [f for files in new_files.values() for f in files]
code_only = all(Path(f).suffix.lower() in code_exts for f in all_changed)
```

If **all** changed files are code files:
- Skip semantic extraction entirely (no Claude subagents)
- Run only AST extraction on the changed files
- No tokens spent, rebuild takes seconds

This is the common case when you're coding — you edit a `.py` file, the graph auto-updates with the new class/function structure, no LLM needed.

If any changed file is a doc/paper/image, full semantic extraction runs on that file.

---

## --watch Mode — Automatic Updates

`watch.py` implements a file system watcher that calls `--update` automatically:

```python
# watch.py (simplified)
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class GraphifyHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory:
            return
        self.pending.add(event.src_path)
        self.schedule_rebuild()   # debounced — waits 3 seconds after last change
    
    def do_rebuild(self):
        if all_code(self.pending):
            _rebuild_code(root)   # AST only — instant
        else:
            # Write a flag file — user must run --update manually for docs
            (root / "graphify-out" / ".needs_update").touch()
```

The debounce (3-second wait after the last change) prevents rebuilding on every keystroke when an agent is making many file edits in quick succession.

Code changes → instant rebuild. Doc changes → flag file written → user runs `--update` manually (LLM needed).

---

## The Graph Diff — What Changed?

After `--update` merges the new extraction, `graph_diff()` compares the old and new graphs:

```python
diff = graph_diff(G_old, G_new)
print(diff["summary"])
# → "3 new nodes, 5 new edges, 1 node removed"
```

This is shown to the user after an `--update` run so they can see what changed in the graph.

---

*Next: [[19_adding_a_language]] — How to add support for a new programming language*
