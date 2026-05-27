# analyze.py — Graph Analysis

Tags: #module #layer3 #analysis
Links: [[00_INDEX]] | [[09_cluster]] | [[11_report]]
File: `graphify/analyze.py`

---

## What This Module Does

After the graph is built and communities are detected, `analyze.py` extracts three types of **insights**:

1. **God nodes** — the most-connected nodes (your core abstractions)
2. **Surprising connections** — edges that cross file-type or community boundaries unexpectedly
3. **Suggested questions** — questions the graph is uniquely positioned to answer

These insights populate the `GRAPH_REPORT.md` and guide the AI assistant's tour of the graph.

---

## god_nodes() — The Most Connected Concepts

```python
def god_nodes(G: nx.Graph, top_n: int = 10) -> list[dict]:
    degree = dict(G.degree())
    sorted_nodes = sorted(degree.items(), key=lambda x: x[1], reverse=True)
    result = []
    for node_id, deg in sorted_nodes:
        if _is_file_node(G, node_id) or _is_concept_node(G, node_id):
            continue
        result.append({
            "id": node_id,
            "label": G.nodes[node_id].get("label", node_id),
            "edges": deg,
        })
        if len(result) >= top_n:
            break
    return result
```

`G.degree()` returns a dict of `{node_id: edge_count}`. We sort descending and return the top 10.

**But we skip two types of nodes:**

### _is_file_node() — Skip Mechanical Hubs

```python
def _is_file_node(G: nx.Graph, node_id: str) -> bool:
    attrs = G.nodes[node_id]
    label = attrs.get("label", "")
    source_file = attrs.get("source_file", "")
    if source_file:
        if label == Path(source_file).name:
            return True          # "client.py" == filename → it's a hub
    if label.startswith(".") and label.endswith("()"):
        return True              # ".get()" → method stub
    if label.endswith("()") and G.degree(node_id) <= 1:
        return True              # "parse()" with ≤1 edge → isolated function
    return False
```

File-level hub nodes (like `client.py`) have many edges just because they `contain` every class and function inside them. That's not architectural importance — it's a side effect of how AST extraction works. A file with 50 functions has 50 `contains` edges. We exclude these.

### _is_concept_node() — Skip Injected Semantic Nodes

```python
def _is_concept_node(G: nx.Graph, node_id: str) -> bool:
    data = G.nodes[node_id]
    source = data.get("source_file", "")
    if not source:
        return True              # no source file → manually injected concept
    if "." not in source.split("/")[-1]:
        return True              # no file extension → not a real file path
    return False
```

Semantic extraction creates nodes for abstract concepts like "Attention Mechanism" that don't live in any specific file. These accumulate many edges because everything in the transformer community references them. We exclude these too — they're definitional, not discovered.

The result is the top-10 **real entities** (classes, functions, modules) with the most connections — your genuine architectural load-bearing abstractions.

---

## surprising_connections() — Unexpected Edges

```python
def surprising_connections(G, communities, top_n=5) -> list[dict]:
    source_files = {data.get("source_file", "") for _, data in G.nodes(data=True) if data.get("source_file", "")}
    is_multi_source = len(source_files) > 1

    if is_multi_source:
        return _cross_file_surprises(G, communities or {}, top_n)
    else:
        return _cross_community_surprises(G, communities or {}, top_n)
```

Two strategies depending on the corpus:

**Multi-file corpus:** Look for edges that cross file boundaries in surprising ways (cross-file-type, cross-repo, uncertain confidence).

**Single-file corpus** (or all nodes from same source): Look for edges that bridge different Leiden communities — structurally distant parts of the graph that are unexpectedly connected.

### _cross_file_surprises() — Multi-Source Strategy

For each edge in the graph:

1. Skip structural edges (`imports`, `contains`, `calls`, `method`) — these are expected
2. Skip concept nodes and file hub nodes
3. Skip edges where both endpoints are in the same file
4. Score the remaining edges with `_surprise_score()`

```python
def _surprise_score(G, u, v, data, node_community, u_source, v_source) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    # 1. Confidence weight — uncertain connections are more noteworthy
    conf = data.get("confidence", "EXTRACTED")
    conf_bonus = {"AMBIGUOUS": 3, "INFERRED": 2, "EXTRACTED": 1}.get(conf, 1)
    score += conf_bonus
    
    # 2. Cross file-type — code↔paper is more surprising than code↔code
    cat_u = _file_category(u_source)   # "code", "paper", "doc", "image"
    cat_v = _file_category(v_source)
    if cat_u != cat_v:
        score += 2
        reasons.append(f"crosses file types ({cat_u} ↔ {cat_v})")
    
    # 3. Cross-repo — different top-level directory
    if _top_level_dir(u_source) != _top_level_dir(v_source):
        score += 2
        reasons.append("connects across different repos/directories")
    
    # 4. Cross-community — Leiden says these are structurally distant
    cid_u = node_community.get(u)
    cid_v = node_community.get(v)
    if cid_u is not None and cid_v is not None and cid_u != cid_v:
        score += 1
        reasons.append("bridges separate communities")
    
    # 4b. Semantic similarity — non-obvious conceptual links
    if data.get("relation") == "semantically_similar_to":
        score = int(score * 1.5)   # 50% bonus
        reasons.append("semantically similar concepts with no structural link")
    
    # 5. Peripheral→hub — low-degree node connecting to high-degree one
    deg_u = G.degree(u)
    deg_v = G.degree(v)
    if min(deg_u, deg_v) <= 2 and max(deg_u, deg_v) >= 5:
        score += 1
        peripheral = "..."
        reasons.append(f"peripheral node unexpectedly reaches hub")
    
    return score, reasons
```

The top-5 highest-scoring edges become the "Surprising Connections" section in the report. Each result includes a `why` field: the list of reasons, joined with semicolons.

---

## suggest_questions() — Questions the Graph Can Answer

This is one of graphify's most useful features. After clustering and analysis, it generates questions that the graph structure is uniquely positioned to answer:

```python
def suggest_questions(G, communities, community_labels, top_n=7) -> list[dict]:
    questions = []
    
    # 1. AMBIGUOUS edges → unresolved relationship questions
    for u, v, data in G.edges(data=True):
        if data.get("confidence") == "AMBIGUOUS":
            ul = G.nodes[u].get("label", u)
            vl = G.nodes[v].get("label", v)
            questions.append({
                "type": "ambiguous_edge",
                "question": f"What is the exact relationship between `{ul}` and `{vl}`?",
                "why": f"Edge tagged AMBIGUOUS — confidence is low.",
            })
    
    # 2. Bridge nodes → cross-cutting concern questions
    betweenness = nx.betweenness_centrality(G)
    bridges = sorted([(n, s) for n, s in betweenness.items() if ...], ...)[:3]
    for node_id, score in bridges:
        label = G.nodes[node_id].get("label", node_id)
        # ... find which communities this node bridges
        questions.append({
            "type": "bridge_node",
            "question": f"Why does `{label}` connect `Community A` to `Community B`?",
            "why": f"High betweenness centrality ({score:.3f}) — cross-community bridge.",
        })
    
    # 3. God nodes with many INFERRED edges → verification questions
    for node_id in top_5_nodes:
        inferred = [edges that are INFERRED]
        if len(inferred) >= 2:
            questions.append({
                "type": "verify_inferred",
                "question": f"Are the {N} inferred relationships involving `{label}` correct?",
            })
    
    # 4. Isolated/weakly-connected nodes → exploration questions
    isolated = [n for n in G.nodes() if G.degree(n) <= 1 and ...]
    if isolated:
        questions.append({
            "type": "isolated_nodes",
            "question": f"What connects `A`, `B`, `C` to the rest of the system?",
        })
    
    # 5. Low-cohesion communities → structural questions
    for cid, nodes in communities.items():
        score = cohesion_score(G, nodes)
        if score < 0.15 and len(nodes) >= 5:
            questions.append({
                "type": "low_cohesion",
                "question": f"Should `{label}` be split into smaller modules?",
            })
    
    return questions[:top_n]
```

### Betweenness Centrality

A node's **betweenness centrality** measures how often it appears on the shortest path between other pairs of nodes. A node with high betweenness is a "bridge" — remove it, and many other nodes lose their fastest connection.

```python
betweenness = nx.betweenness_centrality(G)
# Returns {node_id: score} where score is in [0, 1]
# Score 1.0: this node is on every shortest path in the graph
# Score 0.0: this node is never on any shortest path
```

High-betweenness nodes that span community boundaries are especially interesting — they're the "connective tissue" between different architectural modules.

---

## graph_diff() — What Changed Between Two Graph Versions

```python
def graph_diff(G_old: nx.Graph, G_new: nx.Graph) -> dict:
    old_nodes = set(G_old.nodes())
    new_nodes = set(G_new.nodes())

    added_node_ids = new_nodes - old_nodes
    removed_node_ids = old_nodes - new_nodes
    
    # Edge comparison: normalise direction for undirected graphs
    def edge_key(G, u, v, data):
        if G.is_directed():
            return (u, v, data.get("relation", ""))
        return (min(u, v), max(u, v), data.get("relation", ""))  # canonical order
    
    added_edge_keys = new_edge_keys - old_edge_keys
    removed_edge_keys = old_edge_keys - new_edge_keys
    ...
    
    return {
        "new_nodes": [...],
        "removed_nodes": [...],
        "new_edges": [...],
        "removed_edges": [...],
        "summary": "3 new nodes, 5 new edges, 1 node removed"
    }
```

Used by `--update` to show a diff after incremental re-extraction. The edge comparison normalises direction in undirected graphs using `min(u,v), max(u,v)` so `(a,b)` and `(b,a)` are considered the same edge.

---

## The "No Signal" Case

If none of the five question generators finds anything to flag:

```python
if not questions:
    return [{
        "type": "no_signal",
        "question": None,
        "why": (
            "Not enough signal to generate questions. "
            "This usually means the corpus has no AMBIGUOUS edges, no bridge nodes, "
            "no INFERRED relationships, and all communities are tightly cohesive. "
            "Add more files or run with --mode deep."
        ),
    }]
```

An empty `questions` list isn't returned — instead a "no signal" sentinel is returned. This ensures `report.py` always has something to write in the "Suggested Questions" section, and the message explains why no questions were generated.

---

*Next: [[11_report]] — How GRAPH_REPORT.md is generated*
