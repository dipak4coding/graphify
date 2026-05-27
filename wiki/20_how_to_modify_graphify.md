# How to Modify Graphify — Complete Developer Guide

Tags: #development #extension #modification
Links: [[00_INDEX]] | [[19_adding_a_language]] | [[04_extract_ast]]

---

## Mental Model First

Before modifying any code, hold this mental model:

```
detect.py        → finds files
extract.py       → reads code structure (AST)
subagents        → reads docs/papers/images (semantic)
cache.py         → avoids re-reading unchanged files
build.py         → assembles NetworkX graph
cluster.py       → groups nodes into communities
analyze.py       → finds insights (god nodes, surprises)
report.py        → generates GRAPH_REPORT.md
export.py        → writes outputs (HTML, JSON, Obsidian)
wiki.py          → generates agent-crawlable wiki
```

Each module has one job. Changes to one module rarely require changing others. Find which module owns the behaviour you want to change.

---

## Common Modifications

### 1. Change What Files Are Ignored

**File:** `detect.py`
**Where:** `_SKIP_DIRS` set, `_SENSITIVE_PATTERNS` list, or `.graphifyignore`

Add a directory to always skip:
```python
_SKIP_DIRS = {
    "venv", ".venv", ...,
    "my_generated_code",  # add this
}
```

Add a sensitive file pattern:
```python
_SENSITIVE_PATTERNS = [
    ...,
    re.compile(r'my_secrets\.json', re.IGNORECASE),  # add this
]
```

Or, for project-specific ignores, create a `.graphifyignore` file in the project root:
```
my_generated_code/
*.secret.json
build/
```

---

### 2. Change the Confidence Scoring

**File:** Semantic subagent prompt in `skill.md`
**Where:** The confidence_score instructions section

The prompt currently says:
```
INFERRED edges: Direct structural evidence: 0.8-0.9. Reasonable inference: 0.6-0.7. Weak: 0.4-0.5.
```

To make Claude be more conservative (lower scores for uncertain edges):
```
INFERRED edges: Direct structural evidence: 0.7-0.8. Reasonable inference: 0.5-0.6. Weak: 0.3-0.4.
```

Or to be more aggressive:
```
INFERRED edges: Any reasonable inference: 0.8-0.9.
```

---

### 3. Change Community Detection Algorithm

**File:** `cluster.py`
**Where:** `_partition()` function

Current order: try Leiden → fallback to Louvain.

To switch to **spectral clustering** (different algorithm, groups by graph spectrum):
```python
def _partition(G: nx.Graph) -> dict[str, int]:
    from sklearn.cluster import SpectralClustering
    import numpy as np
    
    nodes = list(G.nodes())
    adj = nx.to_numpy_array(G)
    
    n_clusters = max(2, int(len(nodes) ** 0.5))  # heuristic
    sc = SpectralClustering(n_clusters=n_clusters, affinity="precomputed", random_state=42)
    labels = sc.fit_predict(adj)
    
    return {node: int(label) for node, label in zip(nodes, labels)}
```

To add a new algorithm as a **fallback after Louvain**:
```python
def _partition(G: nx.Graph) -> dict[str, int]:
    # Try Leiden
    try:
        from graspologic.partition import leiden
        return leiden(G)
    except ImportError:
        pass
    
    # Try Louvain
    communities = nx.community.louvain_communities(G, seed=42)
    return {node: cid for cid, nodes in enumerate(communities) for node in nodes}
```

---

### 4. Change the Surprise Score Formula

**File:** `analyze.py`
**Where:** `_surprise_score()` function

Current formula adds:
- AMBIGUOUS: +3, INFERRED: +2, EXTRACTED: +1 (for confidence)
- Cross file-type: +2
- Cross-repo: +2
- Cross-community: +1
- Semantic similarity: ×1.5
- Peripheral→hub: +1

To make cross-community edges always dominate:
```python
if cid_u is not None and cid_v is not None and cid_u != cid_v:
    score += 5   # was 1, now 5
```

To add a new surprise criterion (e.g., score higher for AMBIGUOUS edges in god nodes):
```python
# After existing scoring...
deg_u = G.degree(u)
deg_v = G.degree(v)
if conf == "AMBIGUOUS" and max(deg_u, deg_v) >= 10:
    score += 3
    reasons.append("ambiguous edge involving a highly-connected node")
```

---

### 5. Add a New Relation Type

**File:** Semantic subagent prompt in `skill.md`
**Where:** The JSON schema / relation field

Current relations: `calls|implements|references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for`

To add `"deprecated_by"`:
1. Edit the schema line in `skill.md`:
   ```
   "relation":"calls|implements|...|rationale_for|deprecated_by"
   ```

2. Optionally exclude it from surprise scoring in `analyze.py`:
   ```python
   if relation in ("imports", "imports_from", "contains", "method", "deprecated_by"):
       continue
   ```

3. The relation will now appear in the graph JSON and be visualised in the HTML graph.

---

### 6. Change the Report Format

**File:** `report.py`
**Where:** `generate()` function

The report is built as a list of strings that gets joined with `\n`. To add a new section:

```python
# After the existing God Nodes section:
lines += ["", "## Hot Paths (most-traversed routes)"]
betweenness = nx.betweenness_centrality(G)
hot_nodes = sorted(betweenness.items(), key=lambda x: -x[1])[:5]
for nid, score in hot_nodes:
    label = G.nodes[nid].get("label", nid)
    lines.append(f"- `{label}` (betweenness: {score:.3f})")
```

To remove a section, just delete those lines from `generate()`.

---

### 7. Add a New Export Format

**File:** `export.py`
**Where:** Add a new `to_<format>()` function

Example: adding JSON Lines export (one node/edge per line):

```python
def to_jsonlines(G: nx.Graph, path: str) -> None:
    """Export graph as JSON Lines — one JSON object per line."""
    lines = []
    for nid, data in G.nodes(data=True):
        lines.append(json.dumps({"type": "node", "id": nid, **data}))
    for u, v, data in G.edges(data=True):
        lines.append(json.dumps({"type": "edge", "source": u, "target": v, **data}))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
```

Then add it to the CLI in `__main__.py` and to the skill's Step 7 in `skill.md`.

---

### 8. Change the Cache Key

**File:** `cache.py`
**Where:** `file_hash()` function

Current key: SHA256 of file content + path.

To include the graphify version in the key (so upgrading graphify invalidates all caches):

```python
def file_hash(path: Path) -> str:
    from graphify import __version__
    p = Path(path)
    raw = p.read_bytes()
    content = _body_content(raw) if p.suffix.lower() == ".md" else raw
    h = hashlib.sha256()
    h.update(content)
    h.update(b"\x00")
    h.update(str(p.resolve()).encode())
    h.update(b"\x00")
    h.update(__version__.encode())  # add this
    return h.hexdigest()
```

Warning: this will invalidate all existing caches and trigger re-extraction of everything on the next run.

---

### 9. Add a New CLI Command

**File:** `__main__.py`
**Where:** `main()` function

Current commands: `install`, `query`, `save-result`, `benchmark`, `hook`, `claude`, `gemini`, `cursor`, ...

To add a `stats` command that prints graph statistics:

```python
elif cmd == "stats":
    from networkx.readwrite import json_graph
    import networkx as nx
    gp = Path("graphify-out/graph.json").resolve()
    if not gp.exists():
        print("error: no graph found. Run /graphify first.", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(gp.read_text(encoding="utf-8"))
    try:
        G = json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(raw)
    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")
    print(f"Average degree: {sum(dict(G.degree()).values()) / G.number_of_nodes():.1f}")
    print(f"Connected components: {nx.number_connected_components(G)}")
```

Then add it to the help text:
```python
print("  stats                   print graph statistics")
```

---

## Debugging Techniques

### Print the AST for a file

```python
import tree_sitter_python
from tree_sitter import Language, Parser
from pathlib import Path

lang = Language(tree_sitter_python.language())
parser = Parser(lang)
source = Path("myfile.py").read_bytes()
tree = parser.parse(source)

def print_tree(node, source, indent=0):
    text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")[:50]
    print("  " * indent + f"[{node.type}] {repr(text)}")
    for child in node.named_children:
        print_tree(child, source, indent + 1)

print_tree(tree.root_node, source)
```

### Inspect what extract.py produces

```python
from graphify.extract import extract
from pathlib import Path

result = extract([Path("myfile.py")])
print(f"Nodes: {len(result['nodes'])}")
for n in result['nodes']:
    print(f"  {n['id']}: {n['label']} @ {n.get('source_location', '?')}")
print(f"Edges: {len(result['edges'])}")
for e in result['edges']:
    print(f"  {e['source']} --{e['relation']}--> {e['target']}")
```

### Inspect the graph after build

```python
from graphify.build import build_from_json
import json

extraction = json.loads(open("graphify-out/.graphify_extract.json").read())
G = build_from_json(extraction)

print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# See all neighbors of a node
node_id = "client_httpclient"
print(f"Neighbors of {node_id}:")
for neighbor in G.neighbors(node_id):
    edge = G.edges[node_id, neighbor]
    print(f"  --{edge['relation']}--> {G.nodes[neighbor]['label']}")
```

### Check cache hits

```python
from graphify.cache import load_cached
from pathlib import Path

path = Path("myfile.py")
cached = load_cached(path)
if cached:
    print(f"Cache HIT: {len(cached['nodes'])} nodes, {len(cached['edges'])} edges")
else:
    print("Cache MISS")
```

---

## Running the Tests

```bash
cd /Users/dipaksukalkar/001_Git_Repositories/graphify
pip install -e ".[dev]"
pytest tests/ -v
```

Key test files:
- `tests/test_extract.py` — AST extraction tests
- `tests/test_languages.py` — One test per supported language
- `tests/test_build.py` — Graph building tests
- `tests/test_cluster.py` — Community detection tests
- `tests/test_analyze.py` — God nodes and surprises tests
- `tests/test_cache.py` — Cache tests
- `tests/test_detect.py` — File detection tests
- `tests/test_ingest.py` — URL ingestion tests
- `tests/test_wiki.py` — Wiki generation tests

---

## The Module Dependency Map

```
__main__.py
├── detect.py      (standalone)
├── extract.py
│   └── cache.py   (standalone)
├── build.py
│   └── validate.py (standalone)
├── cluster.py     (standalone, needs networkx)
├── analyze.py
│   └── cluster.py
├── report.py
│   └── analyze.py
├── export.py      (standalone, needs networkx)
├── wiki.py        (standalone, needs networkx)
├── ingest.py
│   └── security.py (standalone)
├── serve.py       (standalone, needs networkx)
├── watch.py       (standalone)
└── hooks.py       (standalone)
```

Circular dependencies: none. The dependency graph is a DAG. If you're adding a new module, keep it this way — don't import from `report.py` inside `extract.py`.

---

*You now have everything you need to understand, explain, and modify graphify like the developer who built it.*
