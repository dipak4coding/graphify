# Graphify Core Wiki (the original tool, in plain language)

> **Source read:** your fork `dipak4coding/graphify`, branch `current-v8`, commit `fe66389` (47,000 lines of Python).
> **How it was read:** every module listed with its functions and docstrings; the key parts (C extraction, query engine, build, update, CLI commands) read in the code itself. Function **bodies** of the big support modules (`llm.py`, `install.py`, `dedup.py`, `cache.py`, `callflow_html.py`) were not read line by line, so those are described by what their names and docstrings say.
> **Line numbers** (`L123`) refer to this commit and will shift when you edit.
> Companion file: `graphify-wiki.md` (your clang + A2L pipeline).

---

## 1. What Graphify does in one minute

Graphify turns a folder of files into a **knowledge graph** (nodes = things, edges = relationships), then lets Claude answer questions from the graph instead of reading all the code.

```
detect() → extract() → build() → cluster() → analyze → report → export
 (which    (read the   (make the  (group     (key     (GRAPH_    (graph.json,
  files?)   files)      graph)     nodes)     nodes)   REPORT.md) html, obsidian…)
```

| Stage | Module | Plain meaning |
|---|---|---|
| detect | `detect.py` | Walk the folder, decide which files count (code / doc / image), skip junk and secrets |
| extract | `extract.py` + `extractors/` | Read each file with tree-sitter, produce nodes and edges |
| build | `build.py` | Merge all extraction results into one NetworkX graph |
| cluster | `cluster.py` | Find communities (groups of closely linked nodes) |
| analyze | `analyze.py` | God nodes, surprising links, suggested questions, import cycles |
| report | `report.py` | Write `GRAPH_REPORT.md` |
| export | `export.py`, `exporters/` | Write `graph.json`, HTML, Obsidian, GraphML, SVG, Cypher, Canvas |
| query | `serve.py`, `cli.py` | Answer questions from `graph.json` (command line or MCP server) |

**Two ways the graph gets filled:**

| Tier | By whom | Cost | Stamp |
|---|---|---|---|
| **AST** (code) | tree-sitter, no AI | free | `_origin = "ast"`, `source_location = "L<line>"` |
| **Semantic** (docs, papers, images) | an AI model | tokens | `_origin` other than `ast`, `source_location` empty |

Edge confidence: `EXTRACTED` (stated in the source), `INFERRED` (reasonable deduction), `AMBIGUOUS` (flagged for review).

---

## 2. How Graphify reads YOUR C code

| Step | What happens | Where |
|---|---|---|
| 1 | `.c` goes to `extract_c()`, which calls the generic engine with `_C_CONFIG` | `extract.py` L2330, config L1025 |
| 2 | `.h` files: `_is_objc_header()` / `_is_cpp_header()` decide if a header is Objective-C or C++; otherwise it is plain C | `extract.py` L5993, L6036 |
| 3 | Parser = `tree_sitter_c`. **Function node** for every `function_definition`; name found by unwrapping the declarator (`_get_c_func_name`, L882) | `extract.py` |
| 4 | `#include` → `imports` edge; quoted includes are resolved to a real file when possible (`_import_c`, L686) | `extract.py` |
| 5 | Calls: a `call_expression` becomes a `calls` edge (`EXTRACTED`, `context="call"`) when the callee is found in the same file or in the label index | `engine.py` L5872 area |
| 6 | Callee not found in the same file → saved as a "raw call" and resolved across files later in `extract()` | `extract.py` L6336 |
| 7 | Return type (and parameter types) → `references` edge, `context` = `return_type` etc. (`_c_collect_type_refs`, engine L1111) | `engine.py` L4788 |
| 8 | Function pointers / dispatch tables → `indirect_call` edge, `INFERRED` | `engine.py` L5192 |

**What this means for you (matches your TODO list):**

- Graphify's C pass creates functions, files, includes, calls, type references. It does **not** create nodes for global variables or calibration items. That is exactly why your clang + A2L layer exists (TODO A3).
- Calls to functions that have no definition in the scanned files (for example `Bios_*`) have nothing to point at, so they are missing (TODO A2).
- Function nodes carry only a start line (`L11`), not a range (TODO A5). The line comes from `node.start_point` in the engine.

---

## 3. Module map: every file in `graphify/`

### 3.1 The main pipeline

| File | Lines | Job | Key functions |
|---|---|---|---|
| `detect.py` | 2577 | Decides what to scan | `detect()` L1727, `detect_incremental()` L2439, `classify_file()` L503, `_is_sensitive()` (skip secrets), `_is_ignored()` (`.graphifyignore`), `load_manifest()` / `save_manifest()` (change tracking by hash) |
| `extract.py` | 7856 | Dispatcher + per-language wrappers + cross-file resolvers | `extract()` L6336, `collect_files()` L7784, `_get_extractor()` L6051, `extract_c()` L2330, `extract_cpp()`, `extract_python()`, `extract_js()` …, `_extract_parallel()` / `_extract_sequential()`, `_resolve_*_member_calls()` (one per language) |
| `extractors/engine.py` | 6514 | The **generic tree-sitter engine** shared by C, C++, Python, JS, Java, C#… | `_extract_generic()` L3144 (walks the syntax tree, creates nodes and edges), `walk()`, `walk_calls()` (call graph second pass) |
| `extractors/resolution.py` | 3698 | Cross-file name resolution helpers | ~100 helpers |
| `extractors/*.py` | small | One module per language moved out of `extract.py` (Go, Rust, SQL, Bash, Fortran, Terraform, Pascal, Zig, Markdown, …) | see `extractors/MIGRATION.md` (C and C++ are **not** migrated yet; they still use the shared engine) |
| `build.py` | 2300 | Makes the graph and merges updates | `build_from_json()` L799, `build()` L1398, `build_merge()` L1787, `merge_raw_extraction()` L1643, `dedupe_nodes()`, `dedupe_edges()`, `_is_ast_tier()` L45, `deduplicate_by_label()` |
| `cluster.py` | 418 | Communities (Leiden, falls back to Louvain) | `cluster()` L232, `cohesion_score()`, `label_communities_by_hub()` |
| `analyze.py` | 769 | Analysis | `god_nodes()` L109, `surprising_connections()` L148, `suggest_questions()` L443, `graph_diff()` L571, `find_import_cycles()` L655 |
| `report.py` | 361 | `GRAPH_REPORT.md` | `generate()` L96 |
| `export.py` | 1354 | Output formats | `to_json()` L271, `to_obsidian()` L686, `to_canvas()`, `to_graphml()`, `to_svg()`, `to_cypher()`, `prune_dangling_edges()`, `backup_if_protected()` |
| `exporters/html.py`, `graphdb.py` | | HTML graph and graph-DB export (moved out of `export.py`) | |
| `wiki.py` | 405 | One article per community + `index.md` | `to_wiki()` L273 |
| `tree_html.py`, `callflow_html.py` | | D3 tree view, Mermaid call-flow architecture page | `write_callflow_html()` |
| `validate.py` | 95 | Schema check of an extraction | `validate_extraction()`, `assert_valid()` |
| `ids.py` | 93 | **The one place node ids are made** | `normalize_id()` L50, `make_id()` L86 (your scripts copy this) |

### 3.2 Query and serving

| File | Job | Key functions |
|---|---|---|
| `serve.py` (2617) | Search engine and MCP server | see section 4 |
| `querylog.py` (80) | Optional query log (`GRAPHIFY_QUERY_LOG_ENABLE=1`) | `log_query()` L43 (never raises) |
| `affected.py` (318) | "What breaks if I change X?" (reverse walk) | `resolve_seed()`, `affected_nodes()` L190, `format_affected()` |
| `ingest.py` (358) | Fetch a URL into the corpus; save Q&A as memory | `ingest()` L219, `save_query_result()` L275 |
| `reflect.py` (882) | Turns saved Q&A memory into lessons | `reflect()` L564, `aggregate_lessons()` L364, `render_lessons_md()`, `build_learning_overlay()`, `write_learning_sidecar()` (`.graphify_learning.json`) |
| `benchmark.py` | Token saving vs reading everything | `run_benchmark()` |

### 3.3 Keeping the graph up to date

| File | Job | Key functions |
|---|---|---|
| `watch.py` (2295) | Auto-rebuild on file change; **the engine behind `graphify update`** | `_rebuild_code()` L1314, `watch()` L2197, `_reconcile_existing_graph()` L726, `_check_shrink()` L1120, `_rebuild_lock()` |
| `hooks.py` (939) | Git hooks (post-commit, post-checkout) and the `graph.json` merge driver | `install()`, `uninstall()`, `status()`, `_register_merge_driver()` |
| `cache.py` (1740) | Cache of extraction results so unchanged files are not re-read | `file_hash()`, `load_cached()`, `save_cached()`, `check_semantic_cache()`, `save_semantic_cache()` |
| `dedup.py` (1213) | Merge near-duplicate entities | `deduplicate_entities()` L550 |
| `semantic_cleanup.py`, `file_slice.py`, `manifest*.py` | Cleanup of semantic results, slicing huge files, package manifests | |

### 3.4 Installing into AI assistants

| File | Job |
|---|---|
| `install.py` (2367) | Copies the skill into Claude Code, Gemini, VS Code, Kiro, Cursor, Antigravity, Kilo, OpenCode… and registers always-on rules and hooks (`_claude_pretooluse_hooks()` L324, `_skill_registration()` L352) |
| `skill.md`, `skill-*.md`, `skills/claude/references/*.md` | The instruction text Claude reads (`query.md`, `update.md`, `hooks.md`, `extraction-spec.md`, …) |
| `always_on/*.md` | The always-on blocks (for example `claude-md.md` goes into `CLAUDE.md`) |
| `__main__.py` (755) | Entry point `main()` L486; install and uninstall subcommands |
| `cli.py` (4745) | All other commands: `dispatch_command()` L1064 (see section 5) |

### 3.5 Support modules

| File | Job |
|---|---|
| `security.py` | URL validation (blocks private IPs, SSRF), `safe_fetch()`, `validate_graph_path()`, graph-size cap, `sanitize_label()` (strips control chars, caps length, escapes HTML) |
| `paths.py` | Output dir name, `out_path()`, atomic file writes (`write_json_atomic()`), path helpers, `disambiguate_ambiguous_candidates()` (bare-name call with several candidates) |
| `llm.py` (3544) | Semantic extraction through AI providers (Anthropic, OpenAI-compatible, Bedrock, Ollama…), prompt building, JSON repair, retries. Not needed for code-only graphs |
| `symbol_resolution.py`, `resolver_registry.py` | Conservative cross-file symbol resolution framework |
| `*_resolution.py`, `csharp_dispatch.py`, `cross_repo_*.py` | Language-specific or cross-repo resolution (Ruby, Pascal, C#) |
| `global_graph.py`, `prs.py`, `diagnostics.py`, `multigraph_compat.py` | Multi-repo "global" graph, PR dashboard, read-only diagnostics, MultiDiGraph probe |
| `scip_ingest.py`, `mcp_ingest.py`, `pg_introspect.py`, `cargo_introspect.py`, `transcribe.py`, `google_workspace.py` | Optional importers (SCIP, MCP configs, Postgres, Cargo, audio, Google files) |
| `_minhash.py` | Small MinHash implementation used by dedup |

---

## 4. The query engine: `serve.py` in detail

**One question, step by step** (CLI: `graphify query "…"`, MCP: `query_graph`). Driver = `_query_graph_text()` L1285.

| # | Function (line) | What it does |
|---|---|---|
| 1 | `_query_terms()` L281 | Splits the question into search words, drops filler words |
| 2 | `_search_tokens()` L191, `_strip_diacritics()` L183 | Tokenises; `_` and `-` separate words; accents removed (ü→u, **not** ue) |
| 3 | `_compute_idf()` L317 | Rare words get more weight |
| 4 | `_node_search_text()` L363, `_node_rationale_text()` L349 | The text of a node that gets matched: label, id, path, and `rationale` |
| 5 | `_trigram_candidates()` L431 | Quick pre-filter of possible nodes |
| 6 | `_score_query()` L510 | Scores every candidate (exact > prefix > substring > path > rationale) |
| 7 | `_pick_seeds()` L711 | Chooses up to 3 start nodes plus one per matched word |
| 8 | `_resolve_context_filters()` L902, `_infer_context_filters()` L890, `_filter_graph_by_context()` L912 | Hint words in the question switch on an edge-`context` filter (table `_CONTEXT_HINTS` L826): *call/caller* → `call`; *import/module* → `import`; *field/member/property* → `field`; *parameter/argument* → `parameter_type`; *return* → `return_type`; *generic/template* → `generic_arg`. Only edges with that `context` survive |
| 9 | `_traversal_view()` L1242 | Undirected copy so callers and callees are both reachable; real direction kept in `_src` / `_tgt` |
| 10 | `_bfs()` L979 / `_dfs()` L1010 | Walk from the start nodes (CLI depth 2, MCP default 3, max 6) |
| 11 | `_complete_induced_edges()` L929 | Adds missing edges between nodes already visited |
| 12 | `_subgraph_to_text()` L1037, `_cut_lines_to_budget()` L1195 | Prints NODE and EDGE lines under a token budget (about 3 characters per token) |

**Other lookups**

| Function | Used by |
|---|---|
| `_find_node()` L1524, `_find_node_tiers()` L1425, `find_node_ambiguity()` L1535, `_resolve_single_node()` L1562 | `explain`, `get_node`, `get_neighbors` (exact beats prefix beats substring) |
| `_shortest_path_text()` L1588 | `path` command, `shortest_path` tool |
| `_load_graph()` L44 | Loads graph for MCP as **directed**, attaches the learning overlay |
| `_GraphContextCache` L121 | Keeps several project graphs loaded (LRU, default 8) |
| `_build_server()` L1731 | Registers the MCP tools |
| `serve()` L2331 / `serve_http()` L2489 | Start the MCP server over stdio / HTTP (HTTP has API-key middleware) |

**MCP tools registered:** `query_graph`, `get_node`, `get_neighbors`, `get_community`, `god_nodes`, `graph_stats`, `shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs`. All accept an optional `project_path`.

**What Claude sees vs. what is hidden:** only label, file, line, community (and relation / confidence / context on edges). Metadata such as `parameters`, `return_type`, `unit`, `calibration_ref` is **never printed** by the query output (TODO B1).

---

## 5. Commands in `cli.py` (all start in `dispatch_command()` L1064)

| Command | What it does |
|---|---|
| `query "<q>"` L1202 | The search above (`--dfs`, `--budget`, `--context`, `--graph`) |
| `explain "<X>"` L1716 | Node plus its neighbours |
| `path "<A>" "<B>"` L1549 | Shortest path |
| `affected "<X>"` L1323 | Reverse impact analysis |
| `god-nodes` L1391 | Most connected real entities |
| `update [path] [--force] [--no-cluster]` L2403 | Re-read **code files only** (no AI), rebuild graph; calls `watch._rebuild_code()` |
| `extract` L3174 | Full build including semantic (AI) extraction (large block, L3174 to L4552) |
| `cluster-only`, `label` L1995 | Re-cluster or relabel communities |
| `export <html/obsidian/…>` L2782 | Write an output format from `graph.json` |
| `watch` L1982 | Rebuild on file changes |
| `hook install/uninstall/status` L1185, `hook-check`, `hook-guard` L2468 | Git hooks and the PreToolUse guard that nudges Claude to use the graph |
| `check-update` L2480 | Is a semantic update pending? |
| `save-result` L1461, `reflect` L1492 | Save Q&A memory; build lessons |
| `add <url>` L1947 | Fetch a URL into the corpus |
| `merge-graphs` L2606, `merge-driver` L2551, `global` L3108, `clone` L2758 | Multi-repo and git merge helpers |
| `tree` L2488, `benchmark` L3091, `diagnose` L1853, `prs` L1182, `provider` L1065 | Tree HTML, benchmark, diagnostics, PR dashboard, LLM providers |
| `cache-check`, `merge-chunks`, `merge-semantic` | Helper steps used by the skill's semantic workflow |

Helper functions in `cli.py`: `_default_graph_path()` L84, `_enforce_graph_size_cap_or_exit()` L658, `_touch_query_stamp()` L687, `_run_hook_guard()` L814, `_prune_graph_json_sources()` L583, `_stale_graph_sources()` L221, `_clone_repo()` L1000.

---

## 6. How `graph.json` is built and updated (important for your enrichment)

| Function | What it does | Why it matters to you |
|---|---|---|
| `build_from_json(extraction, directed=False)` build.py L799 | Normalises legacy fields, checks ids, creates the NetworkX graph (undirected by default) | Direction is lost unless `directed=True`; `query` re-adds it through `_src` / `_tgt` |
| `build_merge()` L1787, `merge_raw_extraction()` L1643 | Merges old `graph.json` with new extraction | This is what `update` relies on |
| `_tier_replacement_sources()` L1577 + `_is_ast_tier()` L45 | Replaces only the **AST** nodes of changed files; semantic (non-AST) items are kept | Items **without** `_origin = "ast"` survive an update (TODO A7) |
| `dedupe_nodes()` L556 | Same id twice: **last writer wins** on attributes | Order of merging matters |
| `dedupe_edges()` L575 | Parallel edges with the same (source, target, relation) collapse; generic relations (`references`, `uses`, `mentions`) lose against specific ones | Your custom relation names should be specific |
| `_check_shrink()` watch.py L1120 / `to_json()` | Refuses to overwrite a graph with a much smaller one unless `--force` | Use `--force` after deleting code |

**`graphify update` flow:** `cli update` → `_rebuild_code()` → detect → extract changed code files (cache) → `build_merge` → optional cluster → `GRAPH_REPORT.md` → `to_json` (with shrink guard).

---

## 7. "I want to change X: where do I go?" (core side)

| Your TODO | Place in the core code |
|---|---|
| **B1** one-liner per node in query output | NODE line in `_subgraph_to_text()` serve.py L1037; `_tool_get_node()` in `_build_server()` |
| **B2** print function range `L11-L50` | same NODE line; also check `_format_location()` in affected.py and anything parsing `source_location` (regex `_AST_LOC_RE` in build.py expects `L<number>`; **TODO E3**) |
| **B3** stop sibling flood from `contains` edges | `_bfs()` L979 (skip `contains` or hub nodes) and `_pick_seeds()` L711 (do not seed on file nodes) |
| **B4** umlaut fix (ü → ue) | `_strip_diacritics()` L183 and `_search_tokens()` L191 in serve.py; `export.py` has its own `_strip_diacritics()` L123 for Obsidian names |
| **A6** custom edges need `context` | `_CONTEXT_HINTS` L826 (question words → context) and `_filter_graph_by_context()` L912. Use one of: `call`, `import`, `field`, `parameter_type`, `return_type`, `generic_arg` |
| **A7** keep custom items across `update` | `_is_ast_tier()` build.py L45: set `_origin` to something other than `"ast"` |
| **C1/C2** new `explain-fn` command | add a branch in `dispatch_command()` (next to `explain`, L1716) and a renderer in `serve.py`; add an MCP tool in `_build_server()` (tools listed around L1805) |
| **C2/C3** skill rules | `graphify/skills/claude/references/query.md` and `graphify/skill.md` (installed copy is `.claude/skills/graphify/`) |
| **D4** make descriptions searchable | write them into node `rationale`; `_node_rationale_text()` L349 and `_score_query()` L510 already score it |
| Change traversal depth | CLI branch of `query` (cli.py around L1305) and MCP `_tool_query_graph()` |
| Add a language | `ARCHITECTURE.md` section "Adding a new language extractor" (config in `extract.py`, register suffix in `_get_extractor()`, `collect_files()`, `detect.py` `CODE_EXTENSIONS`, `watch.py` `_WATCHED_EXTENSIONS`) |
| Another output format | new `to_<format>()` in `export.py`, then a branch under `export` in `cli.py` |

---

## 8. Things worth knowing before you edit

| # | Point |
|---|---|
| 1 | After every `pip install`/upgrade or reinstall, the **installed skill copy** (`.claude/skills/graphify/`) can differ from your edited `skill.md`; `__main__._check_skill_version()` warns when the version stamp is old |
| 2 | `extract.py` is being split (`extractors/`). C and C++ still live in `extract.py` plus `engine.py`. Edit there until they are migrated |
| 3 | `serve.py` and `build.py` are used by tests; `tests/test_architecture_doc.py` imports every symbol named in `ARCHITECTURE.md`, so keep those names |
| 4 | The query output is deliberately small. Anything you add to NODE lines costs tokens on every query; keep one-liners short |
| 5 | Node ids come from `ids.make_id()`. Your clang script copies the recipe; if you change `ids.py`, change the copies in `extract_ast_graph.py` and `merge_into_graphify.py` too |
| 6 | The edge `context` values the question filter understands are `call`, `import`, `field`, `parameter_type`, `return_type`, `generic_arg` (`_CONTEXT_HINTS` L826). A custom edge with another context, or none, disappears when the question contains one of the hint words |
