# report.py — Generating GRAPH_REPORT.md

Tags: #module #layer4 #output
Links: [[00_INDEX]] | [[10_analyze]] | [[12_export]]
File: `graphify/report.py`

---

## What Is GRAPH_REPORT.md?

`GRAPH_REPORT.md` is the **human-readable audit trail** — a plain-language summary of everything graphify found. It's the first thing an AI assistant reads when you open a project with a knowledge graph.

Graphify's PreToolUse hook tells Claude:
> "Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md for god nodes and community structure before searching raw files."

So this report is the map. Before exploring the codebase, the AI reads this to understand the territory.

---

## The generate() Function

```python
def generate(
    G: nx.Graph,
    communities: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    community_labels: dict[int, str],
    god_node_list: list[dict],
    surprise_list: list[dict],
    detection_result: dict,
    token_cost: dict,
    root: str,
    suggested_questions: list[dict] | None = None,
) -> str:
```

Takes in everything that's been computed and returns a big string that gets written to `GRAPH_REPORT.md`.

---

## What the Report Contains — Section by Section

### Section 1: Corpus Check

```python
lines = [f"# Graph Report - {root}  ({today})", "", "## Corpus Check"]
if detection_result.get("warning"):
    lines.append(f"- {detection_result['warning']}")
else:
    lines += [
        f"- {detection_result['total_files']} files · ~{detection_result['total_words']:,} words",
        "- Verdict: corpus is large enough that graph structure adds value.",
    ]
```

Reports the corpus size and whether it's big enough to benefit from a graph (>50,000 words). If `detect()` returned a warning (too small or too large), that warning is shown here.

### Section 2: Summary Statistics

```python
# Count confidence distribution
confidences = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
total = len(confidences) or 1
ext_pct = round(confidences.count("EXTRACTED") / total * 100)
inf_pct = round(confidences.count("INFERRED") / total * 100)
amb_pct = round(confidences.count("AMBIGUOUS") / total * 100)

lines += [
    f"- {G.number_of_nodes()} nodes · {G.number_of_edges()} edges · {len(communities)} communities detected",
    f"- Extraction: {ext_pct}% EXTRACTED · {inf_pct}% INFERRED · {amb_pct}% AMBIGUOUS",
    f"- Token cost: {token_cost.get('input', 0):,} input · {token_cost.get('output', 0):,} output",
]
```

This tells you at a glance: how much the AI extracted vs inferred vs was uncertain about, and how much it cost.

### Section 3: Community Hub Navigation

```python
for cid in communities:
    label = community_labels.get(cid, f"Community {cid}")
    safe = _safe_community_name(label)
    lines.append(f"- [[_COMMUNITY_{safe}|{label}]]")
```

These are **Obsidian wikilinks** to community hub files. Without this section, `GRAPH_REPORT.md` would be a dead end — you'd read it and then have nowhere to go. With these links, you can click through to each community's full article in the Obsidian vault.

The `_safe_community_name()` function strips characters that Obsidian can't use in filenames (`\`, `/`, `*`, `?`, `"`, `<`, `>`, `|`, `#`, `^`, `[`, `]`).

### Section 4: God Nodes

```python
lines += ["", "## God Nodes (most connected — your core abstractions)"]
for i, node in enumerate(god_node_list, 1):
    lines.append(f"{i}. `{node['label']}` - {node['edges']} edges")
```

Simple numbered list. The AI reads this to know which nodes are most architecturally important — these are the first ones to understand when exploring an unfamiliar codebase.

### Section 5: Surprising Connections

```python
for s in surprise_list:
    relation = s.get("relation", "related_to")
    conf = s.get("confidence", "EXTRACTED")
    cscore = s.get("confidence_score")
    conf_tag = f"INFERRED {cscore:.2f}" if conf == "INFERRED" and cscore else conf
    sem_tag = " [semantically similar]" if relation == "semantically_similar_to" else ""
    lines += [
        f"- `{s['source']}` --{relation}--> `{s['target']}`  [{conf_tag}]{sem_tag}",
        f"  {files[0]} → {files[1]}" + (f"  _{note}_" if note else ""),
    ]
```

Each surprise is on two lines:
1. The edge: `source --relation--> target [confidence]`
2. The source files and why it's surprising

### Section 6: Hyperedges

```python
hyperedges = G.graph.get("hyperedges", [])
if hyperedges:
    lines += ["", "## Hyperedges (group relationships)"]
    for h in hyperedges:
        node_labels = ", ".join(h.get("nodes", []))
        lines.append(f"- **{h.get('label', '')}** — {node_labels} [{conf_tag}]")
```

Only appears if hyperedges were extracted.

### Section 7: Communities Detail

```python
for cid, nodes in communities.items():
    label = community_labels.get(cid, f"Community {cid}")
    score = cohesion_scores.get(cid, 0.0)
    # Filter out method/function stubs — they're structural noise
    real_nodes = [n for n in nodes if not _is_file_node(G, n)]
    display = [G.nodes[n].get("label", n) for n in real_nodes[:8]]
    suffix = f" (+{len(real_nodes)-8} more)" if len(real_nodes) > 8 else ""
    lines += [
        f"### Community {cid} - \"{label}\"",
        f"Cohesion: {score}",
        f"Nodes ({len(real_nodes)}): {', '.join(display)}{suffix}",
    ]
```

For each community: its label, cohesion score, and first 8 node labels. File-level hub nodes (`client.py`, etc.) are filtered out — they're structural noise, not meaningful architectural concepts.

### Section 8: Ambiguous Edges

```python
ambiguous = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("confidence") == "AMBIGUOUS"]
if ambiguous:
    lines += ["", "## Ambiguous Edges - Review These"]
    for u, v, d in ambiguous:
        ul = G.nodes[u].get("label", u)
        vl = G.nodes[v].get("label", v)
        lines += [
            f"- `{ul}` → `{vl}`  [AMBIGUOUS]",
            f"  {d.get('source_file', '')} · relation: {d.get('relation', 'unknown')}",
        ]
```

These are the edges Claude was unsure about. They need human review — either confirm them (update the source docs) or mark them as wrong.

### Section 9: Knowledge Gaps

```python
isolated = [n for n in G.nodes() if G.degree(n) <= 1 and not _is_file_node(G, n) and not _is_concept_node(G, n)]
thin_communities = {cid: nodes for cid, nodes in communities.items() if len(nodes) < 3}
gap_count = len(isolated) + len(thin_communities)

if gap_count > 0 or amb_pct > 20:
    lines += ["", "## Knowledge Gaps"]
    if isolated:
        lines.append(f"- **{len(isolated)} isolated node(s):** ...")
        lines.append("  These have ≤1 connection - possible missing edges or undocumented components.")
    if thin_communities:
        # ...
    if amb_pct > 20:
        lines.append(f"- **High ambiguity: {amb_pct}% of edges are AMBIGUOUS.** Review the Ambiguous Edges section.")
```

Three types of gaps:
- **Isolated nodes** (≤1 edge): Things that exist in the codebase but aren't connected to anything. Possible undocumented components or missing documentation.
- **Thin communities** (<3 nodes): Too small to be a meaningful cluster. May be noise or need more context.
- **High ambiguity** (>20% AMBIGUOUS edges): The model was frequently uncertain — the documentation may need improvement.

### Section 10: Suggested Questions

```python
if suggested_questions:
    lines += ["", "## Suggested Questions"]
    for q in suggested_questions:
        if q.get("question"):
            lines.append(f"- **{q['question']}**")
            lines.append(f"  _{q['why']}_")
```

The questions from `suggest_questions()` printed as a to-do list. The AI reads these as prompts for its graph exploration tour.

---

## Why GRAPH_REPORT.md Is the AI's Entry Point

The PreToolUse hook fires before every Glob or Grep tool call:
```
[ -f graphify-out/graph.json ] && 
echo '{"additionalContext":"graphify: Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md..."}'
```

This means every time the AI tries to search files, it first gets reminded to read GRAPH_REPORT.md. The report gives the AI a map so it knows:
- Which nodes are architecturally central (god nodes)
- Which communities exist and what they're called
- Where the surprising cross-cutting connections are
- What questions are worth exploring

Without this, the AI would start reading files randomly. With it, it reads the most important things first.

---

*Next: [[12_export]] — HTML, JSON, Obsidian vault, and other output formats*
