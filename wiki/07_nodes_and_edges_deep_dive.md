# Nodes and Edges — Complete Deep Dive

Tags: #concept #fundamentals #deep-dive
Links: [[00_INDEX]] | [[04_extract_ast]] | [[05_extract_semantic]] | [[08_build]]

---

## Where Nodes Come From — The Full Picture

Nodes enter the graph from three sources, in this priority order:

```
Source 1: AST Extractor (extract.py)
  → Creates: file nodes, class nodes, function nodes, import-target nodes
  → Confidence: always EXTRACTED
  → Cost: free, deterministic

Source 2: Semantic Subagents (Claude)
  → Creates: concept nodes, paper-entity nodes, image-concept nodes, Q&A nodes
  → Confidence: EXTRACTED, INFERRED, or AMBIGUOUS
  → Cost: Claude API tokens

Source 3: ingest.py (URL fetching)
  → Creates: doc nodes with YAML frontmatter metadata
  → Flows into: semantic extraction on next --update run
  → Cost: free (HTTP fetch)
```

If the same node ID appears in both AST and semantic results, **AST wins** (merged first, semantic deduplicated). This is intentional — AST nodes have precise `source_location` line numbers.

---

## Complete Node Schema

```json
{
  "id":              "client_httpclient",
  "label":           "HTTPClient",
  "file_type":       "code",
  "source_file":     "src/client.py",
  "source_location": "L47",
  "source_url":      null,
  "captured_at":     null,
  "author":          null,
  "contributor":     null,
  "community":       2
}
```

| Field | Type | Set by | Meaning |
|-------|------|--------|---------|
| `id` | string | extract.py `_make_id()` | Stable, unique, snake_case identifier |
| `label` | string | extractor | Human-readable display name |
| `file_type` | string | extractor | `"code"`, `"document"`, `"paper"`, `"image"` |
| `source_file` | string | extractor | Relative path to the source file |
| `source_location` | string or null | AST extractor | `"L47"` (line number) or null for semantic |
| `source_url` | string or null | ingest.py | Original URL if fetched via `/graphify add` |
| `captured_at` | ISO datetime or null | ingest.py | When the URL was fetched |
| `author` | string or null | ingest.py / YAML frontmatter | Who wrote the content |
| `contributor` | string or null | ingest.py / YAML frontmatter | Who added it to the corpus |
| `community` | int or null | build.py + cluster.py | Set during graph building, after clustering |

**The `community` field** is added to nodes *after* clustering (not during extraction). `build.py` annotates each node with its community ID during the `build_from_json()` call. This is how the HTML visualisation knows which colour to give each node.

---

## Node ID Construction — _make_id() Deep Dive

```python
def _make_id(*parts: str) -> str:
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()
```

Example walkthrough for a class `HTTPClient` in file `client.py`:

```
parts = ("client", "HTTPClient")

Step 1 — strip leading/trailing dots and underscores from each part:
  "client" → "client"     (no change)
  "HTTPClient" → "HTTPClient"  (no change)

Step 2 — join with underscore:
  "client_HTTPClient"

Step 3 — replace non-alphanumeric with underscore:
  "client_HTTPClient" → "client_HTTPClient"  (no change — all chars are valid)

Step 4 — strip leading/trailing underscores:
  "client_HTTPClient" → "client_HTTPClient"

Step 5 — lowercase:
  "client_httpclient"
```

Another example: a module import `os.path`:
```
parts = ("os.path",)
Step 3: "os.path" → "os_path"  (dot replaced by underscore)
Step 5: "os_path"
```

Another example: a function named `__init__`:
```
parts = ("client_httpclient", "__init__")
Step 1 strip: "__init__" → "init"  (strips leading/trailing underscores)
Step 2 join: "client_httpclient_init"
Step 5: "client_httpclient_init"
```

---

## Complete Edge Schema

```json
{
  "source":            "client_get",
  "target":            "requests",
  "relation":          "calls",
  "confidence":        "EXTRACTED",
  "confidence_score":  1.0,
  "source_file":       "src/client.py",
  "source_location":   "L52",
  "weight":            1.0,
  "_src":              "client_get",
  "_tgt":              "requests"
}
```

| Field | Type | Set by | Meaning |
|-------|------|--------|---------|
| `source` | string | extractor | ID of the source node |
| `target` | string | extractor | ID of the target node |
| `relation` | string | extractor | Type of relationship (see table below) |
| `confidence` | string | extractor | `"EXTRACTED"`, `"INFERRED"`, or `"AMBIGUOUS"` |
| `confidence_score` | float | extractor | 0.0–1.0, how certain this edge is |
| `source_file` | string | extractor | Which file this edge was found in |
| `source_location` | string or null | AST | Line number in that file |
| `weight` | float | extractor | Edge weight for graph algorithms (default 1.0) |
| `_src` | string | build.py | Preserved original direction (see below) |
| `_tgt` | string | build.py | Preserved original direction (see below) |

### The `_src` and `_tgt` fields

```python
# In build.py:
attrs["_src"] = src
attrs["_tgt"] = tgt
G.add_edge(src, tgt, **attrs)
```

NetworkX's undirected `Graph` doesn't preserve edge direction — `G.edges["a", "b"]` and `G.edges["b", "a"]` return the same edge. But graphify needs to know the original direction for reporting purposes. By storing `_src` and `_tgt` in the edge attributes, `analyze.py` and `report.py` can reconstruct direction even in an undirected graph.

---

## All Relation Types — What Each Means

### Structural relations (from AST)

| Relation | Direction | Example | How it's created |
|----------|-----------|---------|-----------------|
| `contains` | file → class, file → function, class → method | `client.py` → `HTTPClient` | Any class/function definition inside a file/class |
| `imports` | file → module | `client.py` → `requests` | `import X` statement |
| `imports_from` | file → module | `client.py` → `graphify.detect` | `from X import Y` statement |
| `calls` | function → function | `fetch_data()` → `validate()` | Function call found inside a function body |
| `method` | class → method | `HTTPClient` → `.get()` | Method defined inside a class |
| `case_of` | enum → case | (Swift only) `Color` → `red` | `enum_entry` inside a Swift `enum` |

### Semantic relations (from Claude)

| Relation | Direction | Example | When to use |
|----------|-----------|---------|------------|
| `references` | doc → concept | `notes.md` → `Transformer` | A doc mentions a named concept |
| `cites` | paper → paper | `attention.md` → `bert_paper` | One paper cites another |
| `implements` | entity → concept | `SelfAttention` → `attention mechanism` | A class/function implements a known pattern |
| `conceptually_related_to` | concept ↔ concept | `encoder` ↔ `decoder` | Two concepts are related without structural link |
| `shares_data_with` | module ↔ module | `parser` ↔ `validator` | Two components operate on the same data |
| `semantically_similar_to` | entity ↔ entity | `validate_email()` ↔ `check_email_format()` | Same idea, different names, no structural link |
| `rationale_for` | rationale → entity | `"JWT Design Notes"` → `"JWT token"` | A doc section explains WHY something was designed this way |
| `depends_on` | module → module | `auth` → `database` | Dependency not captured by imports |
| `uses` | entity → entity | `TrainingLoop` → `Adam optimizer` | Usage relationship |

---

## How Nodes Get Their Community Label

After `cluster()` runs and assigns each node to a community, `build_from_json()` annotates each node:

```python
# In build.py (conceptually - actually done in the skill's Step 4):
for cid, node_list in communities.items():
    for nid in node_list:
        if nid in G:
            G.nodes[nid]["community"] = cid
```

This means when you open `graph.json` and look at a node, you can see which community it belongs to. The HTML visualiser uses this to colour nodes.

---

## Ghost Nodes — Dangling Import Targets

When Python code says `import requests`, graphify creates an edge to a node with id `"requests"`. But if `requests` isn't in the corpus, there's no `"requests"` node in the graph.

These are called **dangling edges** or **ghost nodes**. `build.py` handles them:

```python
for edge in extraction.get("edges", []):
    src, tgt = edge["source"], edge["target"]
    if src not in node_set or tgt not in node_set:
        continue  # skip edges to external/stdlib nodes — expected, not an error
```

The edge is silently skipped. The validation step `validate_extraction()` does warn about these but they're filtered out of "real errors":

```python
real_errors = [e for e in errors if "does not match any node id" not in e]
```

"Does not match any node id" is the message for dangling edge targets — it's not an error, it's expected. Every codebase imports external libraries.

---

## File-Level Hub Nodes — The "Boring" Nodes

When AST extracts `client.py`, it creates:
- Node `"client"` with label `"client.py"` — the **file hub node**
- All classes and functions inside the file have `contains` edges FROM this node

File hub nodes mechanically accumulate many edges (one `contains` edge per class/function, one `imports` edge per import statement). This makes them appear very highly connected — but that's not meaningful architecture, it's just "this file has 20 things in it".

`analyze.py` detects and excludes file hub nodes from god-node calculations:

```python
def _is_file_node(G: nx.Graph, node_id: str) -> bool:
    attrs = G.nodes[node_id]
    label = attrs.get("label", "")
    source_file = attrs.get("source_file", "")
    if source_file:
        if label == Path(source_file).name:  # label == "client.py" == the filename
            return True
    if label.startswith(".") and label.endswith("()"):
        return True  # method stub like ".get()"
    if label.endswith("()") and G.degree(node_id) <= 1:
        return True  # isolated function stub
    return False
```

---

## The Deduplication Story — Three Layers

1. **Within a file (AST):** `seen_ids` set in `extract_file()`. Same node ID in the same file → only first occurrence kept.

2. **Between files (build.py):** NetworkX `G.add_node()` is idempotent — calling it twice with the same ID overwrites attributes with the second call. AST nodes are added first; semantic nodes are added second (so semantic attributes overwrite AST if same ID). This is intentional — semantic nodes often have richer labels.

3. **Semantic merge:** Before `build()`, the skill deduplicates the merged `nodes` list with a Python `seen` set keyed on `node["id"]`. First occurrence wins.

Result: No duplicate nodes ever reach the final graph.

---

*Next: [[08_build]] — How raw node/edge dicts become a NetworkX graph*
