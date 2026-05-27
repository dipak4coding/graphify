# build.py — Assembling the NetworkX Graph

Tags: #module #layer2
Links: [[00_INDEX]] | [[07_nodes_and_edges_deep_dive]] | [[09_cluster]]
File: `graphify/build.py`

---

## What This Module Does

`build.py` takes the raw extraction output (a dict with `nodes` and `edges` lists) and converts it into a **NetworkX graph object**. NetworkX is a Python library for working with graphs — it gives you algorithms for finding shortest paths, calculating node centrality, detecting communities, and more.

This is a thin but important module. Most of the complexity was in extraction; `build.py` just assembles the pieces.

---

## build_from_json() — The Core Function

```python
def build_from_json(extraction: dict, *, directed: bool = False) -> nx.Graph:
```

**Input:** The merged extraction dict (from `graphify-out/.graphify_extract.json`):
```json
{
  "nodes": [...],
  "edges": [...],
  "hyperedges": [...],
  "input_tokens": 12345,
  "output_tokens": 6789
}
```

**Output:** A NetworkX `Graph` (or `DiGraph` if `directed=True`).

### Step 1 — NetworkX compatibility fix

```python
if "edges" not in extraction and "links" in extraction:
    extraction = dict(extraction, edges=extraction["links"])
```

NetworkX serialises edges as `"links"` in JSON (historical naming). If you load a `graph.json` saved by an older version of NetworkX, it uses `"links"`. This line normalises to `"edges"` so both formats work.

### Step 2 — Validation

```python
errors = validate_extraction(extraction)
real_errors = [e for e in errors if "does not match any node id" not in e]
if real_errors:
    print(f"[graphify] Extraction warning: {real_errors[0]}", file=sys.stderr)
```

`validate_extraction()` (in `validate.py`) checks that all edge source/target IDs exist as nodes. Edges to external libraries (like `requests`) fail this check — but those are expected and filtered out. Only genuine schema errors are printed as warnings.

### Step 3 — Create the graph

```python
G: nx.Graph = nx.DiGraph() if directed else nx.Graph()
```

- `nx.Graph()` — **undirected**: `A → B` and `B → A` are the same edge
- `nx.DiGraph()` — **directed**: `A → B` and `B → A` are different edges, direction preserved

Default is undirected for backward compatibility. Pass `--directed` to use a `DiGraph`.

### Step 4 — Add nodes

```python
for node in extraction.get("nodes", []):
    G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
```

`G.add_node(id, **attrs)` adds a node to the graph. The `id` is the node identifier; all other fields from the node dict become **node attributes** (accessible as `G.nodes["client_httpclient"]["label"]`).

**Key behaviour:** If you call `G.add_node()` with the same `id` twice, the second call *overwrites* the attributes. This is how the AST-vs-semantic priority works — AST nodes are added first, and any semantic node with the same ID overwrites them. This is the "last write wins" deduplication at the graph level.

### Step 5 — Add edges

```python
node_set = set(G.nodes())
for edge in extraction.get("edges", []):
    # Normalise "from"/"to" to "source"/"target" (older format compatibility)
    if "source" not in edge and "from" in edge:
        edge["source"] = edge["from"]
    if "target" not in edge and "to" in edge:
        edge["target"] = edge["to"]
    
    if "source" not in edge or "target" not in edge:
        continue  # malformed edge — skip
    
    src, tgt = edge["source"], edge["target"]
    if src not in node_set or tgt not in node_set:
        continue  # dangling edge (external library) — skip, not an error
    
    attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
    
    # Preserve original direction for undirected graphs
    attrs["_src"] = src
    attrs["_tgt"] = tgt
    
    G.add_edge(src, tgt, **attrs)
```

The `_src`/`_tgt` preservation is important. In an undirected graph, `G.edges["a", "b"]` == `G.edges["b", "a"]`. But `a → b` and `b → a` mean different things (caller vs callee, importer vs imported). By storing the original direction in the edge attributes, any code that needs to know "which way does this edge point?" can check `edge["_src"]` and `edge["_tgt"]`.

### Step 6 — Hyperedges

```python
hyperedges = extraction.get("hyperedges", [])
if hyperedges:
    G.graph["hyperedges"] = hyperedges
```

NetworkX doesn't natively support hyperedges. They're stored in `G.graph` — the graph-level attribute dict — rather than in the node or edge structures. `report.py` reads them from there when generating the GRAPH_REPORT.

---

## build() — Merging Multiple Extractions

```python
def build(extractions: list[dict], *, directed: bool = False) -> nx.Graph:
    combined = {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
    for ext in extractions:
        combined["nodes"].extend(ext.get("nodes", []))
        combined["edges"].extend(ext.get("edges", []))
        combined["hyperedges"].extend(ext.get("hyperedges", []))
        combined["input_tokens"] += ext.get("input_tokens", 0)
        combined["output_tokens"] += ext.get("output_tokens", 0)
    return build_from_json(combined, directed=directed)
```

`build()` is used when you want to merge multiple extraction results before building the graph (e.g., multiple runs, or chunk files). It concatenates all lists and then calls `build_from_json()`.

**Why concatenate then deduplicate in build_from_json()?** Because NetworkX's `add_node()` handles the deduplication naturally — you don't need to pre-deduplicate. Each call to `add_node()` with the same ID just updates the attributes.

---

## The Directed vs Undirected Choice

```python
G = nx.DiGraph() if directed else nx.Graph()
```

**Undirected (default):**
- `A → B` and `B → A` are the same edge
- Algorithms like Louvain community detection require undirected graphs
- Simpler for most visualisation tools

**Directed (--directed flag):**
- `A → B` and `B → A` are different edges
- More accurate for modelling "who imports who", "who calls who"
- Required for algorithms like PageRank, topological sort, cycle detection
- Leiden community detection works on directed graphs (graspologic supports it)

**The cluster() function always converts to undirected internally** (for Louvain fallback), so clustering works regardless.

---

## What NetworkX Gives You

Once the graph is built, you can use NetworkX's built-in algorithms:

```python
# Number of nodes and edges
G.number_of_nodes()
G.number_of_edges()

# Node degree (number of edges connected to a node)
G.degree("client_httpclient")

# All neighbors of a node
list(G.neighbors("client_httpclient"))

# Node attributes
G.nodes["client_httpclient"]["label"]      # "HTTPClient"
G.nodes["client_httpclient"]["source_file"] # "src/client.py"

# Edge attributes
G.edges["client_get", "requests"]["relation"]  # "calls"

# Shortest path between two nodes
nx.shortest_path(G, "auth_login", "database")

# Betweenness centrality (which nodes are most "in the middle")
nx.betweenness_centrality(G)

# All connected components
list(nx.connected_components(G))
```

This is the power of building a NetworkX graph — you get decades of graph algorithm research available as one-liners.

---

## The Three-Layer Deduplication (Recap)

The comments at the top of `build.py` explain the deduplication strategy:

```
# 1. Within a file (AST): seen_ids set — duplicate node IDs in same file collapse to first
# 2. Between files (build): G.add_node() is idempotent — second call overwrites first.
#    AST nodes added first, semantic nodes second → semantic attributes take precedence.
# 3. Semantic merge (skill): before calling build(), skill deduplicates with a seen set.
```

Each layer catches what the previous missed:
- Layer 1 catches same-class-name in one file
- Layer 2 catches same-concept appearing in both AST and semantic passes
- Layer 3 catches same-concept appearing across multiple semantic subagent chunks

---

*Next: [[09_cluster]] — How community detection groups nodes into clusters*
