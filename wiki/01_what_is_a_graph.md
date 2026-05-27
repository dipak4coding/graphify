# What Is a Graph? (And Why Graphify Uses One)

Tags: #concept #fundamentals
Links: [[00_INDEX]] | [[02_pipeline_overview]] | [[07_nodes_and_edges_deep_dive]]

---

## The Big Idea — From Files to a Map

Imagine you just joined a new project. You open the repo and there are 80 Python files, 30 markdown notes, and 5 PDFs. You have no idea what depends on what.

A **graph** solves this by making the relationships visible.

```
 parser.py ──imports──▶ models.py
    │                      │
 contains               contains
    │                      │
 parse()            User, Post, Tag
    │
 calls
    │
    ▼
 validate()  ──semantically_similar_to──▶ (paper: "Input Validation Patterns")
```

This is what graphify builds. Everything is either a **node** or an **edge**.

---

## Nodes — The "Things"

A node is any named concept that graphify finds. It can be:

| Node type | Example | Where it comes from |
|-----------|---------|-------------------|
| Source file | `client.py` | AST extractor creates one per file |
| Class | `HTTPClient` | AST extractor reads class definitions |
| Function | `fetch_data()` | AST extractor reads function definitions |
| Module/import target | `requests` | AST extractor reads import statements |
| Document concept | `"Attention Mechanism"` | Claude semantic extractor reads docs/papers |
| Paper entity | `"Transformer"` | Claude reads PDFs/markdown papers |
| Image concept | `"Login Screen"` | Claude vision reads screenshots |
| Q&A node | `query_2024_what_is_x` | Created when you run `/graphify query` |

Every node has an **ID** (a stable snake_case string like `client_httpclient`) and a **label** (the human-readable name like `HTTPClient`).

### Why does a node need both an ID and a label?

- **ID** is used internally for lookups — it must be unique and URL-safe. It's built by combining the file stem + entity name, lowercased, with symbols replaced by underscores.
- **Label** is what you see in the graph visualisation — it's the original name as it appeared in the source.

Example: A class `HTTPClient` in `client.py` gets:
- ID: `client_httpclient`
- Label: `HTTPClient`

The ID creation lives in `extract.py:_make_id()`:
```python
def _make_id(*parts: str) -> str:
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()
```
*Translation:* Join parts with underscore, strip leading/trailing punctuation from each part, replace any non-alphanumeric character with underscore, lowercase everything. This guarantees IDs are always safe for use as dictionary keys and JSON properties.

---

## Edges — The "Relationships"

An edge connects two nodes and has a **relation** label describing how they are connected.

### Structural edges (from AST — 100% certain)

| Relation | Meaning | Example |
|----------|---------|---------|
| `contains` | File/class owns a function | `client.py` → `fetch_data()` |
| `imports` | File imports a module | `client.py` → `requests` |
| `imports_from` | `from X import Y` | `client.py` → `models` |
| `calls` | One function calls another | `fetch_data()` → `validate()` |
| `method` | A method belongs to a class | `HTTPClient` → `.get()` |

### Semantic edges (from Claude — may be inferred or uncertain)

| Relation | Meaning | Example |
|----------|---------|---------|
| `references` | A doc mentions a concept | `notes.md` → `Transformer` |
| `cites` | A paper cites another | `paper_a` → `paper_b` |
| `implements` | Class implements a pattern/interface | `SortedList` → `sorting algorithm` |
| `conceptually_related_to` | Two concepts are related | `attention` → `positional_encoding` |
| `shares_data_with` | Two components share a data structure | `parser` → `validator` |
| `semantically_similar_to` | Same idea, no structural link | `validate_input()` → `sanitize_user_data()` |
| `rationale_for` | A section explains WHY a design choice | `"Design Notes"` → `"AuthMiddleware"` |

---

## Confidence — How Sure Is graphify?

Every edge carries a **confidence** tag. This is graphify's honesty system.

| Tag | Meaning | Who sets it |
|-----|---------|------------|
| `EXTRACTED` | Directly visible in source code | AST extractor |
| `INFERRED` | Reasonable conclusion from context | Claude subagent |
| `AMBIGUOUS` | Uncertain — flagged for your review | Claude subagent |

**Why does this matter?** If you're using the graph to make architectural decisions, you need to know whether an edge is a hard fact (EXTRACTED import statement) or a model's best guess (INFERRED conceptual link).

Every edge also has a **confidence_score** (0.0–1.0):
- EXTRACTED edges: always `1.0`
- INFERRED edges: `0.6–0.9` depending on how strong the evidence is
- AMBIGUOUS edges: `0.1–0.3`

---

## Hyperedges — Group Relationships

Sometimes a relationship involves 3+ nodes simultaneously. For example: "AuthModule, SessionStore, and TokenValidator all participate in the authentication flow." A regular edge can only connect two nodes.

Graphify supports **hyperedges** — group relationships stored in a separate `hyperedges` list in the JSON. They are displayed in GRAPH_REPORT.md but not rendered in the standard NetworkX graph (NetworkX doesn't support hyperedges natively).

---

## Why a Graph Beats a File List (for an AI)

When an AI assistant reads files one by one, it loses the *relationships*. It knows what's in `client.py` and what's in `auth.py` but it doesn't know that `client.py` calls a function from `auth.py` which was *designed to solve the problem described in* `design_notes.md`.

A graph encodes all of this as traversable structure. The AI can:
1. Find the `HTTPClient` node
2. Follow its edges to see what it imports, calls, and is related to
3. Navigate to those nodes and read *their* connections
4. Answer "how does authentication work?" by traversing 4 hops across 3 files in 1 second

This is the entire point of graphify.

---

*Next: [[02_pipeline_overview]] — The 9-step journey from folder to graph*
