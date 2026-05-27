# Semantic Extraction — How Claude Reads Docs and Papers

Tags: #module #layer1 #semantic #claude
Links: [[00_INDEX]] | [[04_extract_ast]] | [[06_cache]] | [[07_nodes_and_edges_deep_dive]]

---

## Why AST Is Not Enough

Tree-sitter (AST extraction) is great for code — it gives you classes, functions, and imports with 100% accuracy. But:

- A PDF paper has no grammar. Tree-sitter can't parse it.
- A markdown file full of architecture notes has no grammar either.
- Even for code, AST can't tell you that two functions that never call each other are solving the same problem (semantic similarity).

For everything that can't be parsed deterministically, graphify sends files to **Claude subagents** with a structured extraction prompt.

---

## The Subagent System

When `/graphify` runs, the skill (the AI orchestrating the pipeline) dispatches **one Claude subagent per chunk of 20-25 files** — all in a single message, so they run in parallel.

Each subagent gets:
1. A list of file paths to read
2. A schema: what JSON to output
3. Rules: when to use EXTRACTED vs INFERRED vs AMBIGUOUS
4. Instructions for each file type (doc, paper, image, code)

Each subagent returns a JSON object with `nodes`, `edges`, and `hyperedges`. This is written to a chunk file on disk (`graphify-out/.graphify_chunk_NN.json`).

---

## The Extraction Prompt — Annotated

Here is the prompt sent to each subagent, broken down line by line:

```
You are a graphify extraction subagent. Read the files listed and extract a knowledge graph fragment.
Output ONLY valid JSON matching the schema below - no explanation, no markdown fences, no preamble.
```
→ No prose. Just JSON. This is important — any text before or after the JSON breaks the merge step.

```
Files (chunk 2 of 5):
/path/to/attention_paper.md
/path/to/notes.md
```
→ Exact file paths the subagent must read.

```
Rules:
- EXTRACTED: relationship explicit in source (import, call, citation, "see §3.2")
- INFERRED: reasonable inference (shared data structure, implied dependency)
- AMBIGUOUS: uncertain - flag for review, do not omit
```
→ The three-level confidence system. "Do not omit" means the subagent should never silently drop an uncertain relationship — AMBIGUOUS is always better than nothing.

```
Doc/paper files: extract named concepts, entities, citations.
Also extract rationale — sections that explain WHY a decision was made, trade-offs chosen, or design intent.
These become nodes with `rationale_for` edges pointing to the concept they explain.
```
→ Rationale nodes are a special feature. If a document says "We chose JWT tokens because stateless auth scales better", graphify creates:
- Node: "JWT stateless auth rationale"
- Edge: `rationale_for` → "JWT token"

This lets you later ask "why was JWT chosen?" and get an answer from the graph.

```
Image files: use vision to understand what the image IS - do not just OCR.
  UI screenshot: layout patterns, design decisions, key elements, purpose.
  Chart: metric, trend/insight, data source.
  Diagram: components and connections.
```
→ Images are sent to Claude's vision capability. The instruction prevents Claude from just reading the text in an image and ignoring the visual structure.

```
DEEP_MODE (if --mode deep was given): be aggressive with INFERRED edges.
Mark uncertain ones AMBIGUOUS instead of omitting.
```
→ `--mode deep` tells subagents to look harder for indirect relationships.

```
Semantic similarity: if two concepts in this chunk solve the same problem or represent the same idea 
without any structural link (no import, no call, no citation), add a `semantically_similar_to` edge 
marked INFERRED with a confidence_score reflecting how similar they are (0.6-0.95).
```
→ This is how graphify finds non-obvious connections. Example: a `validate_email()` function in code and an "Email Validation" section in a spec doc — they're not linked structurally, but they describe the same thing.

```
confidence_score is REQUIRED on every edge - never omit it, never use 0.5 as a default:
- EXTRACTED edges: confidence_score = 1.0 always
- INFERRED edges: reason about each edge individually. 0.6-0.9 range.
- AMBIGUOUS edges: 0.1-0.3
```
→ `0.5` is explicitly banned as a default. The subagent must reason about each edge, not just slap a middle value on everything.

---

## The Output Schema — Node by Node

Each node in the output JSON looks like:

```json
{
  "id": "attention_paper_transformer",
  "label": "Transformer",
  "file_type": "paper",
  "source_file": "attention_paper.md",
  "source_location": null,
  "source_url": "https://arxiv.org/abs/1706.03762",
  "captured_at": "2024-01-15T10:30:00Z",
  "author": "Vaswani et al.",
  "contributor": "dipak"
}
```

| Field | What it is | Example |
|-------|-----------|---------|
| `id` | Stable snake_case ID | `"attention_paper_transformer"` |
| `label` | Human name shown in the graph | `"Transformer"` |
| `file_type` | `"code"`, `"document"`, `"paper"`, or `"image"` | `"paper"` |
| `source_file` | Relative path to the file it came from | `"notes/attention.md"` |
| `source_location` | Line number or section heading | `"L42"` or `null` |
| `source_url` | If the file was fetched from a URL | `"https://..."` |
| `captured_at` | Timestamp from YAML frontmatter | `"2024-01-15T..."` |
| `author` | From YAML frontmatter | `"Vaswani et al."` |
| `contributor` | Who added this file to the corpus | `"dipak"` |

The last four fields are read from **YAML frontmatter** if the file has it (a block between `---` at the top of a markdown file). This is how graphify preserves provenance when you use `/graphify add` to fetch URLs.

Each edge looks like:

```json
{
  "source": "attention_paper_transformer",
  "target": "notes_positional_encoding",
  "relation": "conceptually_related_to",
  "confidence": "INFERRED",
  "confidence_score": 0.82,
  "source_file": "attention_paper.md",
  "source_location": null,
  "weight": 1.0
}
```

---

## How the Subagent Results Are Merged

After all subagents finish, the merge step in the skill:

1. Reads all `graphify-out/.graphify_chunk_NN.json` files
2. Merges their `nodes`, `edges`, `hyperedges` lists
3. **Deduplicates nodes** by `id` using a `seen` set — first occurrence wins
4. Saves to `graphify-out/.graphify_semantic_new.json`
5. Runs `save_semantic_cache()` to cache the new results
6. Combines with cached results (from previous runs)
7. Saves final `graphify-out/.graphify_semantic.json`

The deduplication is critical: if two subagents both extract a node for "Transformer", only one survives.

---

## Hyperedges — Group Relationships

The subagent can also emit **hyperedges** — relationships involving 3+ nodes simultaneously:

```json
{
  "id": "auth_flow",
  "label": "Authentication Flow",
  "nodes": ["auth_login", "session_store", "token_validator"],
  "relation": "participate_in",
  "confidence": "INFERRED",
  "confidence_score": 0.78,
  "source_file": "auth.py"
}
```

Instructions say to use them sparingly (max 3 per chunk) and only when the group relationship adds information beyond what pairwise edges already say.

Hyperedges are stored in `G.graph["hyperedges"]` in NetworkX (graph-level metadata, not node/edge data) and are shown in `GRAPH_REPORT.md`.

---

## What the Subagent Does For Each File Type

### Documents (.md, .txt, .rst)

Extracts:
- Named concepts as nodes (anything with a proper name that gets discussed)
- Cross-references between sections as edges
- Rationale sections as `rationale_for` edges

### Papers (.pdf, converted .md)

Extracts:
- Paper title, authors as a node
- Cited papers as `cites` edges (literature review)
- Key concepts (attention, encoder, decoder)
- Equations or algorithms as concept nodes

### Images (.png, .jpg)

Uses Claude vision. Extracts:
- For UI screenshots: component names, user flows, design decisions
- For charts: metric name, trend, data source
- For architecture diagrams: service names, arrows become edges

### Code files (semantic pass)

The AST pass already handled imports and structure. The semantic pass finds:
- Which functions are doing conceptually the same thing (`semantically_similar_to`)
- Which components share data structures (`shares_data_with`)
- What architectural patterns are being implemented (`implements`)

---

## Why Subagents, Not One Big Call?

If you have 60 files, you can't send them all to one Claude call — the context window isn't big enough, and accuracy drops on long contexts. By chunking into groups of 20-25 and running in parallel:

- Each subagent has a focused, manageable context
- All chunks run simultaneously (wall-clock time ≈ slowest chunk, not sum of all chunks)
- Failed chunks don't abort the whole pipeline — they're skipped with a warning

---

*Next: [[06_cache]] — How results are cached to avoid re-reading unchanged files*
