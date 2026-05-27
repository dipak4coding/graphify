# The Graphify Pipeline — 9 Steps from Folder to Graph

Tags: #concept #pipeline #overview
Links: [[00_INDEX]] | [[01_what_is_a_graph]] | [[03_detect]]

---

## Helicopter View

```
Your folder
    │
    ▼ Step 1 — Install check
    │
    ▼ Step 2 — detect.py: discover & classify all files
    │           ├── code/ (.py .ts .go ...)
    │           ├── docs/ (.md .txt ...)
    │           ├── papers/ (.pdf ...)
    │           ├── images/ (.png .jpg ...)
    │           └── video/ (.mp4 .mp3 ...)
    │
    ▼ Step 2.5 — (if video) transcribe.py: Whisper → .txt
    │
    ▼ Step 3A — extract.py: AST extraction (tree-sitter, free, deterministic)
    │           └── Nodes: files, classes, functions
    │               Edges: imports, contains, calls
    │
    ▼ Step 3B — Claude subagents: semantic extraction (parallel, costs tokens)
    │           └── Nodes: concepts, entities, paper topics
    │               Edges: references, cites, semantically_similar_to ...
    │           (Steps 3A and 3B run IN PARALLEL)
    │
    ▼ Step 3C — Merge AST + semantic → .graphify_extract.json
    │
    ▼ Step 4 — build.py: node/edge dicts → NetworkX graph
    │         cluster.py: Leiden community detection
    │         analyze.py: god nodes, surprises, questions
    │         report.py: GRAPH_REPORT.md
    │         export.py: graph.json
    │
    ▼ Step 5 — Claude labels the communities ("Attention Mechanism", etc.)
    │
    ▼ Step 6 — export.py: HTML + (optional) Obsidian vault
    │
    ▼ Steps 7-7d — (optional) Neo4j, SVG, GraphML, MCP server
    │
    ▼ Step 8 — benchmark.py: token reduction comparison
    │
    ▼ Step 9 — detect.py: save_manifest(), clean up temp files
    │
    ▼ Outputs:
        graphify-out/graph.json        ← the graph
        graphify-out/GRAPH_REPORT.md   ← human audit report
        graphify-out/graph.html        ← interactive browser visualisation
        graphify-out/obsidian/         ← (if --obsidian) Obsidian vault
```

---

## Step-by-Step Explanation

### Step 1 — Install Check

The skill checks if `graphify` (the Python package, published as `graphifyy` on PyPI) is importable. If not, it installs it silently with pip. Then it writes the Python interpreter path to `graphify-out/.graphify_python` — a tiny file that all subsequent steps read to guarantee they use the same Python environment.

**Why does this matter?** You might have multiple Pythons on your machine (system Python, venv, pyenv, pipx). By detecting and locking the interpreter once at the start, graphify avoids "module not found" errors halfway through a long pipeline.

---

### Step 2 — File Detection (`detect.py`)

`detect(root)` walks the entire folder tree and returns a dict like:
```json
{
  "files": {
    "code": ["src/client.py", "src/models.py"],
    "document": ["README.md", "notes.txt"],
    "paper": ["attention_paper.pdf"],
    "image": ["diagram.png"],
    "video": []
  },
  "total_files": 5,
  "total_words": 12400,
  "needs_graph": true,
  "warning": null,
  "skipped_sensitive": []
}
```

Key decisions made here:
- Skip directories: `node_modules`, `__pycache__`, `.git`, `venv`, `dist`
- Skip sensitive files: `.env`, `.pem`, `id_rsa`, anything with "credential" in the name
- Detect if a `.md` file is actually a paper (by scanning for academic signals like "arxiv", "doi", "abstract")
- Warn if corpus is too small (< 50,000 words) or too large (> 500,000 words or > 200 files)
- Respect `.graphifyignore` files (works like `.gitignore`)

See [[03_detect]] for full details.

---

### Step 2.5 — Transcription (only if video files found)

If `detect()` found `.mp4`, `.mp3`, `.wav` or other audio/video files, they can't be read as text. This step uses **OpenAI Whisper** (local, free) to transcribe them to `.txt` files, which then flow into semantic extraction in Step 3B just like any other document.

The skill writes a domain-hint prompt to Whisper (e.g. "Machine learning research on transformers") by reading the other god nodes from the corpus, improving transcription accuracy.

---

### Step 3 — Extraction (the heart of graphify)

This is the most important step. It has two parallel halves:

#### 3A — AST Extraction (`extract.py`)

**AST** stands for Abstract Syntax Tree. Tree-sitter parses each code file into a tree of syntax nodes. Graphify then walks that tree looking for:
- Class definitions → Class nodes
- Function definitions → Function nodes
- Import statements → Import edges
- Function calls → Call edges

This is **deterministic** (same code always gives same output), **free** (no API calls), and **fast** (100ms for a medium file).

Supported languages: Python, TypeScript, JavaScript, Java, Go, Rust, C, C++, Ruby, Swift, Kotlin, Scala, C#, PHP, Lua, Elixir, Julia, Zig, PowerShell, Objective-C

See [[04_extract_ast]] for full line-by-line explanation.

#### 3B — Semantic Extraction (Claude subagents)

Docs, papers, and images can't be parsed by a grammar. Instead, graphify dispatches **Claude subagents** — one per chunk of 20-25 files — that read the files and return structured JSON with nodes and edges.

These subagents also add **semantic edges** between code files (things like "these two classes share a data structure" that AST can't see).

The results are **cached** per file (SHA256 hash). Next time you run graphify, only changed files get re-extracted.

See [[05_extract_semantic]] and [[06_cache]] for details.

#### 3C — Merge

AST results and semantic results are merged into a single `graphify-out/.graphify_extract.json`. Deduplication rule: if the same node ID appears in both, the AST version wins (it has precise `source_location`).

---

### Step 4 — Build Graph + Analyse

Four things happen in one Python block:

1. **`build.py`**: Creates a NetworkX `Graph` from the merged extraction. Each node dict becomes a graph node; each edge dict becomes a graph edge. Validates that edge endpoints exist.

2. **`cluster.py`**: Runs Leiden community detection (falls back to Louvain). Groups nodes into communities — clusters of strongly-connected concepts. Returns `{community_id: [node_ids]}`.

3. **`analyze.py`**: Computes:
   - **God nodes**: the most-connected nodes (your core abstractions)
   - **Surprising connections**: edges that cross file-type boundaries or community boundaries
   - **Suggested questions**: questions the graph is uniquely positioned to answer

4. **`report.py`** + **`export.py`**: Writes `GRAPH_REPORT.md` and `graph.json`.

See [[08_build]], [[09_cluster]], [[10_analyze]], [[11_report]].

---

### Step 5 — Community Labelling

The AI reads the node labels inside each community (e.g. `["attention_head", "softmax", "query_key_value"]`) and names it with a 2-5 word plain-language label like `"Attention Mechanism"`.

Then it regenerates the report with those real names instead of "Community 0", "Community 1".

---

### Step 6 — Outputs

- **HTML** (`graph.html`): An interactive force-directed graph in your browser. Nodes are coloured by community. Click any node to see its connections.
- **Obsidian vault** (only with `--obsidian`): One `.md` file per node, plus `_COMMUNITY_*.md` files with cohesion scores and dataview queries, plus a `graph.canvas` file for the visual layout. Open the folder as an Obsidian vault.

See [[12_export]].

---

### Step 9 — Manifest + Cleanup

`save_manifest()` records each file's current modification time. On the next `--update` run, `detect_incremental()` compares current mtimes against this manifest and returns only the files that changed. This is how incremental re-extraction works.

All `.graphify_*.json` temp files are deleted. Only permanent outputs remain.

See [[17_incremental_update]].

---

## The Cache System in the Pipeline

```
Step 3B start
    │
    ▼ check_semantic_cache(all_files)
    ├── cache HIT  → load from graphify-out/cache/{hash}.json  (free)
    └── cache MISS → dispatch to Claude subagent              (costs tokens)
         │
         ▼ subagent returns JSON
         │
         ▼ save_semantic_cache() → graphify-out/cache/{hash}.json
```

The cache is keyed by SHA256 of the file's content. If the file hasn't changed, the cached extraction is used directly. See [[06_cache]] for implementation details.

---

*Next: [[03_detect]] — How files are discovered and classified*
