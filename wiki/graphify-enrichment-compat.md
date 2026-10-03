# What must change so Graphify still works with the enriched graph (reads, writes, calibrations)

Checked against fork `dipak4coding/graphify` @ `fe66389` (code) and your `extract_ast_graph.py` / `merge_into_graphify.py` (data).
**Verified** = I built a toy graph in your schema and ran the real `graphify query / explain / affected / path / diagnose / god-nodes` on it.
**Code-read** = confirmed by reading the code, not run.

Your schema as it reaches Graphify: nodes have `metadata.node_kind` and a `line` field (no `source_location`, no `_origin`). Edges have `relation`, `confidence`, `source_file`, `metadata` (no `context`, no `source_location`).

---

## 1. Summary: what breaks today

| # | Problem | Severity | Fix in | Status |
|---|---|---|---|---|
| 1 | Function that reads AND writes the same variable: one edge is lost | **Critical** | Data (merge) + small Graphify change | Verified |
| 2 | No `source_location`: NODE `loc=` is empty and EDGE has no `at=file:line` | **High** | Data (extract) | Verified |
| 3 | No `context` on edges: words like "call", "return", "parameter" delete all your edges | **High** | Data (extract/merge) | Verified |
| 4 | `affected` ignores your relations: "No affected nodes found" | **High** | Graphify (`affected.py`) | Verified |
| 5 | `graphify update` can wipe your items once they get `L<n>` locations without `_origin` | **High** (only after fix 2) | Data (stamp `_origin`) | Verified |
| 6 | A2L descriptions are not searchable ("oil temperature threshold" finds nothing) | Medium | Data (merge) | Verified |
| 7 | Metadata (unit, range, parameters, node kind) never printed to the AI | Medium | Graphify (`serve.py`, `cli.py`) | Verified |
| 8 | No read/write/calibration words in the question logic | Medium | Graphify (`serve.py`) | Code-read |
| 9 | Writer → reader cannot be followed with `path --directed` | Medium | Data or Graphify | Code-read |
| 10 | Absolute `C:/...` paths in `source_file` while Graphify uses repo-relative | Medium | Data (extract) | Verified (printed as is) |
| 11 | Noise seeds: file node and prefix-matching nodes become start points | Low | Graphify (`serve.py`) | Verified |
| 12 | Hub nodes (degree ≥ 50) are not expanded unless they are the start | Info | Instructions | Code-read |

Things that **already work** with no change: schema validation (`file_type` "document" and `<a2l>` are allowed), confidence values, `god-nodes` (file nodes are skipped), `diagnose`, `benchmark`, `save-result`, `reflect`, and `source_location` ranges like `L11-L50` (no code parses it as a number; only the check `^L\d` exists).

---

## 2. Details and exact fixes

### 1. Lost read/write edge (critical)
- **What happens:** Graphify loads the graph as a simple directed graph: one edge per (source, target). Toy: 11 edges in, 10 out. `Task10ms()` has `reads_var` and `writes_var` to `Kku_Anf`. After loading, only `reads_var` is printed. "Who writes Kku_Anf" misses `Task10ms()`.
- **When it occurs:** compound assignments (`x += 1` emits both), or any function that reads a variable in one place and writes it in another.
- **Fix (data):** in `merge_into_graphify.merge_edges`, when a pair has both `reads_var` and `writes_var`, replace them with one edge `reads_writes_var`.
- **Fix (Graphify):** treat `reads_writes_var` as both: in `affected.py` count it for reads and for writes (see 4).
- **Check afterwards:** `graphify diagnose multigraph --graph merged.json` must show `directed_same_endpoint_collapsed_edges: 0`.

### 2. No `source_location` (high)
- **Why it matters:** NODE and EDGE lines are the AI's only pointers. Without `at=file:line` it cannot open the exact snippet, so it reads whole files.
- **Fix:** in `extract_ast_graph.py`: nodes get `"source_location": "L<line>"` (use `L11-L50` from the clang extent if you want ranges); edges get the line of the access (`cursor.location.line` at `add_edge`).
- **Must be paired with 5.**

### 3. No `context` (high)
- **What happens:** the question logic turns words into context filters and keeps only edges with a matching `context`. Toy: "who calls BerKuehlAnf" returned 1 node and no edges.
- **Fix (data):** set `context` on every edge:

| Relation | `context` to set |
|---|---|
| `calls` | `call` |
| `references` (return type) | `return_type` (you store it in `metadata.ctx` today; move it) |
| `includes` | `import` |
| `reads_var` | `read` |
| `writes_var` | `write` |
| `reads_writes_var` | `readwrite` |
| `reads_calibration_field`, `exposes_calibration_field` | `calibration` |
| `x_axis`, `y_axis`, `z_axis`, `input_*`, `length_*` | `axis` |
| `defined_in` | `definition` |

- `--context <any value>` is accepted (verified), so the custom values work with `graphify query --context write`. To make them work from plain words, add hints in 8.

### 4. `affected` (high)
- **What happens:** the default relation list (`affected.py` L12-L27) has no `reads_var`, `writes_var`, `reads_calibration_field`, etc. Toy: `affected "Kku_Anf"` printed "No affected nodes found". With `--relation reads_var --relation writes_var` it works.
- **Fix:** add to `DEFAULT_AFFECTED_RELATIONS`: `reads_var`, `writes_var`, `reads_writes_var`, `reads_calibration_field`, `exposes_calibration_field`, `x_axis`, `y_axis`, `z_axis`, `input_x`, `input_y`, `input_z`.
- Optional: `--reads` / `--writes` flags that pick the right relation sets.

### 5. `update` wiping (high, appears after fix 2)
- **Rule (`build._is_ast_tier`):** an item with `_origin` is AST only if `_origin == "ast"`. An item without `_origin` whose `source_location` starts with `L<digit>` is treated as AST.
- **Verified:** `loc L11` and no `_origin` → AST (will be replaced). `loc L11` with `_origin: "clang"` → not AST (survives).
- **Fix:** stamp `"_origin": "clang"` on every node and edge in `extract_ast_graph.py` and in the A2L edges/nodes in `merge_into_graphify.py`. Do this together with fix 2, never before.
- After each `graphify update`, re-run `merge_into_graphify.py`: tree-sitter nodes are rebuilt and lose your added metadata.

### 6. A2L text not searchable
- **Rule:** search looks at label, id, source file and `rationale` only (`_node_search_text`, serve.py L363). Metadata is never searched.
- **Fix (data):** in `apply_a2l`, copy the A2L description / long identifier into the node's `rationale`. Free and authoritative.

### 7. Metadata never printed
- **What happens:** NODE line prints label, src, loc, community. `get_node` and `explain` print label, id, source, type, community, degree. Never `node_kind`, unit, min/max, parameters, `calibration_ref`.
- **Fix (Graphify):**
  - `serve._subgraph_to_text` (L1092): add `kind=<metadata.node_kind>`, and for calibration/A2L nodes a short `unit/min/max`.
  - `serve._tool_get_node` (L1987) and `cli explain` (L1760 on): print a whitelist of metadata keys.
  - `cli explain`: it shows only the top 20 connections sorted by degree. Sort by relation priority (writes, reads, calibration first) so the important edges are not cut.

### 8. Question words
- `_CONTEXT_HINTS` (serve.py L826): add `read` → read/reads/readers; `write` → write/writes/written/writer/writers; `calibration` → calibration/calibrations/parameter/applicative; `axis` → axis/axes/breakpoints.
- `_RELATIONAL_INTENT_TERMS` (L810): add read, reads, write, writes, written, set, sets. Without this, "writes" can seat a decoy start node.
- `_CONTEXT_FILTER_ALIASES` (L856): add matching aliases.

### 9. Writer → reader path
- **Why:** both edges point into the variable: `Writer -writes_var-> V <-reads_var- Reader`. There is no directed path from Writer to Reader, so `path --directed` and a directed walk cannot follow data flow.
- **Option A (data):** derive an edge `Writer -feeds-> Reader` (via V) in the merge step, with `metadata.via = V`.
- **Option B (Graphify):** a direction-aware command (your TODO C1/C5) that goes variable → writers / readers.
- `path` without `--directed` works but ignores direction.

### 10. Absolute paths
- Clang writes `C:/...` because it gets the path as typed. Graphify normalises its own paths to repo-relative. Result: mixed paths and the AI sees long absolute paths.
- **Fix:** in `extract_ast_graph.py` write `source_file` with `file_relative_path()` (already in the script) instead of the raw path.

### 11. Noise seeds
- Toy: "who writes Kku_Anf" started from `Kku_Anf`, `kku.c`, `KkuAppPtr`, `BerKuehlAnf()`. The file node and prefix-sharing nodes are noise.
- **Fix:** in `_pick_seeds` (serve.py L711) skip File nodes (`metadata.node_kind == "File"`), and do not walk `defined_in` / `includes` edges (your TODO B3).

### 12. Hubs
- `_bfs` / `_dfs` do not expand through nodes with degree ≥ max(50, 99th percentile) unless they are the start. A calibration pointer exposing hundreds of fields, and widely used globals, will appear but not expand.
- For "what does `KkuAppPtr` expose" use `explain` or `get_neighbors`, not `query`. Put this in the Copilot instructions. I have not run this on a real-size graph.

---

## 3. Order of work

| Step | Do | Files | Why first |
|---|---|---|---|
| 1 | Fix 1 (`reads_writes_var`) | `merge_into_graphify.py` | Stops silent data loss |
| 2 | Fixes 2 + 5 together (`source_location` + `_origin`), fix 10 (relative paths) | `extract_ast_graph.py`, `merge_into_graphify.py` | The AI gets `file:line` and `update` stops being a risk |
| 3 | Fix 3 (`context`) and fix 6 (`rationale`) | extract + merge | Questions stop returning empty |
| 4 | Fix 4 (`affected` relations) | `affected.py` | Impact analysis works |
| 5 | Fixes 8, 7, 11 | `serve.py`, `cli.py` | Better questions, better printout, less noise |
| 6 | Fix 9 | data or new command | Data-flow tracing |
| 7 | Update `.github/copilot-instructions.md` | project | Tell Copilot which command to use for reads/writes/calibrations (use `affected --relation ...`, `explain` for hubs) |

## 4. Re-test after each step

| Check | Command | Expected |
|---|---|---|
| No lost edges | `graphify diagnose multigraph --graph merged.json` | `directed_same_endpoint_collapsed_edges: 0` |
| Locations printed | `graphify query "KkuAppROM.SW_T_Oel" --graph merged.json` | NODE has `loc=L..`, EDGE has `at=file:line` |
| Context filter | `graphify query "who calls <func>" --graph merged.json` | call edges appear |
| Impact | `graphify affected "<variable>" --graph merged.json` | readers and writers listed |
| Survives update | `graphify update .`, then count clang nodes | unchanged |
| Plain words | `graphify query "oil temperature threshold" --graph merged.json` | finds the calibration |
