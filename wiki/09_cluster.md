# cluster.py — Community Detection

Tags: #module #layer3 #algorithms
Links: [[00_INDEX]] | [[08_build]] | [[10_analyze]]
File: `graphify/cluster.py`

---

## What Is Community Detection?

Imagine your graph has 200 nodes. How do you understand it? You can't look at every edge. **Community detection** automatically groups nodes that are more strongly connected to each other than to the rest of the graph.

Think of it like finding friend groups in a social network — some people know everyone in their group really well, but only know a few people outside it. Those groups are communities.

In graphify, communities typically correspond to meaningful architectural concepts:
- "Authentication Module" — all the auth-related classes
- "Attention Mechanism" — all the attention/transformer concepts from papers
- "Data Pipeline" — parsers, validators, serialisers

---

## The Leiden Algorithm — What Graphify Prefers

Graphify first tries **Leiden** (from the `graspologic` library), which is an improvement over the older Louvain algorithm.

The basic intuition: Leiden optimises **modularity** — a measure of how much the community structure differs from what you'd expect in a random graph with the same number of edges. High modularity means nodes within communities are densely connected, and nodes between communities are sparsely connected.

```python
def _partition(G: nx.Graph) -> dict[str, int]:
    try:
        from graspologic.partition import leiden
        old_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()  # suppress ANSI escape codes
            with _suppress_output():
                result = leiden(G)
        finally:
            sys.stderr = old_stderr
        return result  # {node_id: community_id}
    except ImportError:
        pass
    # Fallback: networkx louvain
    ...
```

**Why suppress stderr?** `graspologic` uses a progress bar with ANSI escape codes (colour, cursor movement). On Windows PowerShell 5.1, these codes corrupt the terminal scroll buffer. Redirecting stderr to a `StringIO` buffer during the call prevents this.

---

## Fallback: Louvain in NetworkX

If `graspologic` is not installed:

```python
kwargs: dict = {"seed": 42, "threshold": 1e-4}
if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
    kwargs["max_level"] = 10
communities = nx.community.louvain_communities(G, **kwargs)
return {node: cid for cid, nodes in enumerate(communities) for node in nodes}
```

- `seed=42`: Makes results reproducible — same graph always produces same communities
- `threshold=1e-4`: Stop when modularity improvement per iteration is less than 0.0001 (convergence criterion)
- `max_level=10`: Limit the hierarchical levels (added in a later NetworkX release; `inspect.signature()` checks if this parameter exists before passing it, to stay compatible across versions)

The result is a list of frozensets, which is converted to `{node: community_id}` format.

---

## cluster() — The Main Function

```python
def cluster(G: nx.Graph) -> dict[int, list[str]]:
```

**Input:** A NetworkX graph.
**Output:** `{community_id: [node_ids]}` — e.g. `{0: ["auth_login", "session_store"], 1: ["parser", "validator"]}`

### Step 1 — Handle directed graphs

```python
if G.is_directed():
    G = G.to_undirected()
```

Louvain/Leiden require undirected graphs. `to_undirected()` converts a DiGraph by keeping one edge for each direction (or merging if both exist).

### Step 2 — Handle graphs with no edges

```python
if G.number_of_edges() == 0:
    return {i: [n] for i, n in enumerate(sorted(G.nodes))}
```

If there are no edges, every node is its own community. (Can't cluster what has no connections.)

### Step 3 — Handle isolated nodes

```python
isolates = [n for n in G.nodes() if G.degree(n) == 0]
connected_nodes = [n for n in G.nodes() if G.degree(n) > 0]
connected = G.subgraph(connected_nodes)
```

Leiden warns and drops isolated nodes (nodes with no edges). Graphify handles them separately — each isolated node becomes its own single-node community.

### Step 4 — Run community detection on connected nodes

```python
if connected.number_of_nodes() > 0:
    partition = _partition(connected)
    for node, cid in partition.items():
        raw.setdefault(cid, []).append(node)
```

`partition` is `{node_id: community_id}`. We invert it to `{community_id: [node_ids]}`.

### Step 5 — Split oversized communities

```python
_MAX_COMMUNITY_FRACTION = 0.25   # > 25% of graph → too big
_MIN_SPLIT_SIZE = 10             # only split if ≥ 10 nodes

max_size = max(_MIN_SPLIT_SIZE, int(G.number_of_nodes() * _MAX_COMMUNITY_FRACTION))
for nodes in raw.values():
    if len(nodes) > max_size:
        final_communities.extend(_split_community(G, nodes))
    else:
        final_communities.append(nodes)
```

If one community has more than 25% of the graph's nodes, it's too large to be meaningful. `_split_community()` runs a second Leiden pass on just that subgraph:

```python
def _split_community(G: nx.Graph, nodes: list[str]) -> list[list[str]]:
    subgraph = G.subgraph(nodes)
    if subgraph.number_of_edges() == 0:
        return [[n] for n in sorted(nodes)]  # no edges — each is its own
    sub_partition = _partition(subgraph)
    sub_communities = {}
    for node, cid in sub_partition.items():
        sub_communities.setdefault(cid, []).append(node)
    if len(sub_communities) <= 1:
        return [sorted(nodes)]  # Leiden couldn't split it — return as-is
    return [sorted(v) for v in sub_communities.values()]
```

### Step 6 — Stable re-indexing

```python
final_communities.sort(key=len, reverse=True)
return {i: sorted(nodes) for i, nodes in enumerate(final_communities)}
```

Community IDs are reassigned in descending size order: community `0` is always the largest. Node lists are sorted alphabetically. This gives **deterministic, stable** community IDs across runs on the same graph.

---

## cohesion_score() — How Tightly Connected Is a Community?

```python
def cohesion_score(G: nx.Graph, community_nodes: list[str]) -> float:
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    subgraph = G.subgraph(community_nodes)
    actual = subgraph.number_of_edges()
    possible = n * (n - 1) / 2      # max edges in undirected graph with n nodes
    return round(actual / possible, 2)
```

This is the **density** of the subgraph induced by the community — actual edges divided by maximum possible edges.

| Score | Meaning |
|-------|---------|
| 1.0 | Every node is connected to every other (complete graph) |
| 0.5 | About half the possible edges exist |
| 0.1 | Very sparse — loosely related nodes |
| 0.0 | No edges at all |

A low cohesion score (< 0.15 with ≥ 5 nodes) is flagged in `suggest_questions()` as a potential problem: "Should this community be split into smaller, more focused modules?"

`score_all()` just calls `cohesion_score()` for every community:

```python
def score_all(G, communities):
    return {cid: cohesion_score(G, nodes) for cid, nodes in communities.items()}
```

---

## What Community IDs Look Like in the Graph

After clustering, the skill annotates nodes with their community ID:

```python
# communities = {0: ["auth_login", "session_store"], 1: ["parser", "validator"]}
for cid, node_list in communities.items():
    for nid in node_list:
        G.nodes[nid]["community"] = cid
```

In `graph.json`, every node then has a `"community"` field. The HTML visualiser uses this to colour nodes — all nodes in community 0 get colour A, all nodes in community 1 get colour B, etc.

---

## Community Labels — Set by Claude in Step 5

The community IDs (0, 1, 2, ...) are meaningless to humans. After clustering, the skill reads each community's node labels and asks Claude to name it:

- Community 0 nodes: `["attention_head", "softmax", "query_key_value", "multi_head_attention"]`
  → Label: `"Attention Mechanism"`

- Community 1 nodes: `["adam_optimizer", "sgd", "learning_rate_scheduler", "gradient_clip"]`
  → Label: `"Training Pipeline"`

These labels are stored in `graphify-out/.graphify_labels.json` and used in:
- `GRAPH_REPORT.md` (Community Hub Navigation section)
- The HTML visualiser (legend)
- The Obsidian vault (one community article per community)

---

*Next: [[10_analyze]] — God nodes, surprising connections, and suggested questions*
