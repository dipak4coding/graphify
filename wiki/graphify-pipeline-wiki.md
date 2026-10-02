# Graphify Wiki (plain-language guide to your code)

> **What this covers:** every file and function in your Graphify enrichment pipeline (the clang + A2L layer you built around Graphify), plus a map of the core `graphify` package.
> **Read from source:** all 10 pipeline scripts, `run-kku-clang.ps1`, `explain_fn.py`, and the two notes in `claude/`.
> **Not read line by line:** the core `graphify/` package (`serve.py`, `cli.py`, …). It is only searchable in the project, so section 5 is based on your `graphify-query-flow.md` note, not on the code itself.

---

## 1. The big picture in one minute

Graphify's own parser (tree-sitter) finds functions and calls, but it is blind to a lot of embedded-C detail. Your pipeline adds that detail:

| Step | What happens | Script | Output file |
|---|---|---|---|
| 0 | Make clang able to compile the ECU code (include paths, shim) | `generate_compile_commands.py`, `run-kku-clang.ps1` | `compile_commands.json`, `clang_compat_shim.h` |
| 1 | Read the C code with clang → graph of functions, variables, calibration reads | `extract_ast_graph.py` | `<module>_ast.json` |
| 2 | Read the A2L calibration file → measurements, characteristics, axes | `a2l_extractor.py` | `a2l_export.json` |
| 3 | Merge both into Graphify's `graph.json` | `merge_into_graphify.py` | `<module>_merged.json` |
| 4 | Show the result (copy to `graphify-out/graph.json`, export HTML/Obsidian) | `clangit.py view` | `graphify-out/` |
| Check | Is the join correct? Did we find everything? | `compare_ids.py`, `inspect_matches.py`, `coverage_check.py` | printed reports |

`clangit.py` is the remote control that runs steps 1–4 for you from one config file.

```
 .c / .h files ──► extract_ast_graph.py ──► kku_ast.json ─┐
                                                          ├─► merge_into_graphify.py ─► kku_merged.json ─► graphify export
 .a2l file ──────► a2l_extractor.py ─────► a2l_export.json┘            ▲
                                                      Graphify graph.json (tree-sitter)
```

---

## 2. Quick reference: which file does what

| File | One-line job | Run by hand? | Needs |
|---|---|---|---|
| `clangit.py` | One-command wrapper: extract / a2l / merge / view / all | Yes (main entry) | `pipeline_config.json` |
| `extract_ast_graph.py` | clang AST → Graphify-style nodes and edges | Via clangit or by hand | `libclang`, compile args |
| `a2l_extractor.py` | `.a2l` → JSON (measurements, characteristics, axes, units) | Via clangit or by hand | the `.a2l` file |
| `merge_into_graphify.py` | Combine Graphify + clang + A2L graphs | Via clangit or by hand | the three JSON files |
| `generate_compile_commands.py` | Builds `compile_commands.json` for a whole source tree | Yes, once per tree | repo root |
| `run-kku-clang.ps1` | PowerShell: compile only `kku.c`/`kku_app.c`, create the shim and config header | Yes (Windows) | `clang.exe` |
| `compare_ids.py` | Do Graphify and clang agree on node ids? | Yes (check) | two JSON files |
| `inspect_matches.py` | Is the A2L join correct, and is the A2L data in scope? | Yes (check) | AST/merged + A2L JSON |
| `coverage_check.py` | Did A2L extraction find everything? (counts vs CANape) | Yes (check) | A2L JSON + your counts |
| `common_key_jsons.py` | One-off: keep only keys present in both `b08.json` and `kku.json` | Once | two A2L JSONs |
| `claude/explain_fn.py` | Prototype: "explain a function" with direction-aware walk | Yes (prototype) | a graph JSON |
| `claude/graphify-query-flow.md` | Note: how a `/graphify` question travels to an answer | Read only | – |
| `claude/graphify-todos.md` | Note: your enrichment TODO list (A–E) | Read only | – |
| `*.cs` (Program, Tools, …) | Original ScanDKSW C# tool; `a2l_extractor.py` is a port of its `scanA2L()` | – | – (not read here) |

---

## 3. Core concepts (so the details make sense)

| Word | Meaning here |
|---|---|
| **Node** | One thing in the graph: a File, Function, Variable, CalibrationField, A2LCharacteristic, AxisReference |
| **Edge** | A relationship between two nodes (`calls`, `reads_var`, …) |
| **Node id** | Stable name like `ku_kku_src_kku_kku_init`; built by `make_id()` so clang and Graphify produce the same id for the same symbol |
| **Calibration pointer** | The pattern `CalAppPtr->Field`: the code reads the pointer, but the A2L describes the ROM dataset (`CalAppROM.Field`). The pipeline translates one into the other |
| **MEASUREMENT / CHARACTERISTIC / AXIS_PTS** | A2L object types: live signal / tunable value-curve-map / breakpoints of a curve or map |
| **COMPU_METHOD / COMPU_VTAB** | How raw numbers become units (IDENTICAL, LINEAR) or text (TAB_VERB lookup table) |
| **Confidence** | `EXTRACTED` = clang proved it; `AMBIGUOUS` = one of several possible array elements |

---

## 4. File-by-file detail

### 4.1 `a2l_extractor.py`: reads the A2L calibration file

Reads the file line by line (a "state machine": it remembers whether it is currently inside a MEASUREMENT, CHARACTERISTIC, etc.). File is read as Latin-1.

**Data containers (dataclasses)**

| Name | Holds | Notes |
|---|---|---|
| `Measurement` | description, `A2LName`, var type, bit mask, compu method, min/max, `a2l_characteristic_name` | `A2LName` = internal symbol from `LINK_MAP`; `a2l_characteristic_name` = the friendly name on the `/begin MEASUREMENT` line |
| `CompuMethod` | unit, type (IDENTICAL/LINEAR/TAB_VERB), factor, offset, vtab name | – |
| `CompuVtab` | description + dict raw value → text | – |
| `Characteristic` | type (VALUE/CURVE/MAP…), address, layout, compu method, min/max, `symbol_link`, `link_map_symbol`, `axis_references`, `bit_mask` | Property `internal_symbol`: uses `SYMBOL_LINK`, else `LINK_MAP`; adds `#<decimal mask>` if a bit mask exists. Property `symbol_mismatch`: True if both exist and differ |
| `AxisPts` | description, address, input quantity, max points, min/max, symbols | Same `internal_symbol` / `symbol_mismatch` idea |

**Helper functions**

| Function | What it does |
|---|---|
| `kill_special_characters(s)` | Removes every backslash plus the character after it |
| `patch_index(s)` | Turns CANape style `._0_._1_` into `[0][1]` |
| `strip_trailing_indices(symbol)` | `Foo[0][0]` → `Foo` |
| `canonical_bitmask(raw)` | `"0x02"` → `"2"` (decimal, so joins never fail on hex formatting) |
| `_extract_quoted(line)` | Text between the first and last `"` |

**Main function: `scan_a2l(path)`**

- Walks the file once and fills five dictionaries: measurements, compu_methods, compu_vtabs, characteristics, axis_pts.
- **Measurement dict key** = the `LINK_MAP` symbol (not the name on the `/begin` line). Bit-masked duplicates get a `[mask]` suffix; any other name clash gets `_` appended.
- **CURVE/MAP/CUBOID** symbols have trailing `[n]` stripped (only one base address exists for a whole table). VALUE arrays keep their index.
- Handles descriptions that wrap over several lines (`pending_description_for`).
- Returns one dict with the five sections; characteristics and axis_pts also get `internal_symbol` and `symbol_mismatch` written in.

**`main()`**: `python a2l_extractor.py file.a2l -o a2l_export.json`. Prints counts and warns about any SYMBOL_LINK vs LINK_MAP disagreements.

---

### 4.2 `extract_ast_graph.py`: reads C code with clang

**Settings at the top (edit these for a new machine)**

| Setting | Meaning |
|---|---|
| `CLANG_LIB` | Path to `libclang.dll` (override with `--clang-lib`) |
| `CLANG_ARGS` | Fallback `-I` / `-include` flags if no `compile_commands.json` |
| `REGISTER_CAL_FUNCS` | Names of the "register calibration data" functions (`Bios_RegisterCalibrationData`, `RegisterCalibrationData`) |
| `CONFIDENCE`, `FILE_TYPE` | `EXTRACTED`, `code` |

**Id helpers**

| Function | What it does |
|---|---|
| `normalize_id`, `make_id` | Copy of Graphify's own id recipe, so ids match exactly |
| `repo_relative_stem` | Path relative to `--repo-root`, no extension |

**`GraphBuilder` class (the notebook the walk writes into)**

| Method | What it does |
|---|---|
| `__init__` | Keeps `nodes`, `edges`, `ptr_to_rom` (pointer name → ROM name), `calibration_call_candidates` (diagnostics) |
| `file_stem` / `file_relative_path` | Entity ids drop the extension; **File node ids keep it**, so `kku.c` and `kku.h` stay different |
| `add_node` | Adds a node, or merges metadata into an existing one. Category goes in `metadata.node_kind` |
| `add_edge` | Appends an edge with confidence and source file |
| `to_dict` / `write_json` | Output `{nodes, edges}` |

**Function parameters**

| Function | What it does |
|---|---|
| `classify_parameter_role` | `input` for by-value or pointer-to-const; `output_candidate` for pointer/array to non-const. Resolves typedefs and array brackets first |
| `extract_parameters` | List of `{name, type, role}` stored on the Function node |

**Calibration pointer detection (runs first, on all files)**

| Function | What it does |
|---|---|
| `find_base_decl_ref` | Digs through casts / `&` / parentheses to find the variable name |
| `scan_calibration_registrations` | Finds `ptr → rom` pairs from a Register call **or** from `Ptr = &Rom;` initializers |

**Reading calibration access**

| Function | What it does |
|---|---|
| `_find_array_size` | Finds the real array size behind a decayed pointer |
| `resolve_access_path` | Turns `Ptr->Field[i]` into a path like `.Field[0]`; if the index is unknown but the array size known, marks it for expansion |
| `_operator_str` | Gets the operator (`&`, `+=` …) of a binary expression |
| `_evaluate_int_expr` | Computes constant integer expressions (literals, enums, `+ - * <<`) |
| `handle_calibration_access` | Creates `CalibrationField` nodes + `reads_calibration_field` and `exposes_calibration_field` edges. Unknown loop index → one `AMBIGUOUS` edge per possible element |
| `handle_bitmask_access` | Handles `(Ptr->flags & (1<<X))`; path gets `#<mask>` so each bit flag is its own node |
| `_unwrap_passthrough`, `_resolve_calibration_field_id`, `_resolve_variable_or_calibration_id` | Small helpers for the lookup-table linkage |
| `handle_ipo_lookup_axes` | For `Ipo_2D/3D/4D…` calls: links a map to its axes (`x_axis`, `y_axis`, `z_axis`) and inputs (`input_x…`, `length_x…`) |
| `handle_assignment_write` | `x = …` / `x += …` on a global → `writes_var` (plus `reads_var` for compound forms) |

**The main walker: `walk(cursor, …)`** (order of checks)

1. Struct/array access → calibration read
2. `&` expression → bit-mask read, else assignment → write
3. Compound assignment (`+=`) → write
4. Function definition → Function node, `defined_in`, return type, parameters, then walk the body
5. Call → Function node for the callee + `calls` edge (+ Ipo axis linking)
6. Variable reference to a **global** → `reads_var`
7. Global variable declaration → Variable node + `defined_in`
8. Recurse into children

**Other functions**

| Function | What it does |
|---|---|
| `extract_includes` | `includes` edges between files |
| `load_compile_commands`, `args_for_file` | Per-file clang arguments; removes the duplicate trailing file name that causes load errors |
| `find_clang_exe`, `parse_file` | Parse one file; on failure re-runs the real `clang.exe` to show the true error |
| `expand_at_files` | Expands `@filelist.txt` into file names (avoids Windows command-line length limit) |
| `main` | Pass 1: scan registrations over all files. Pass 2: includes + walk. Writes JSON and prints the pointer→ROM table |

**Edges produced:** `includes`, `defined_in`, `calls`, `reads_var`, `writes_var`, `references` (return type), `reads_calibration_field`, `exposes_calibration_field`, `x_axis`/`y_axis`/`z_axis`, `input_*`, `length_*`.

---

### 4.3 `merge_into_graphify.py`: combines everything

**Rules it follows**

- Clang's artifact nodes (Type nodes, the shim header) are dropped.
- Same id on both sides: **Graphify's fields win**; clang only fills gaps (parameters, return type).
- A node only clang saw is added and tagged `source_extractor = "clang_only"`. `--scope` limits which of these are added.
- Duplicate edges (same source, target, relation) collapse to one.

| Function | What it does |
|---|---|
| `normalize_id`, `make_id` | Id recipe (needed for the new A2L nodes) |
| `load_graph`, `load_a2l` | Read JSON |
| `is_artifact_node` | True for Type nodes and shim nodes |
| `merge_nodes` | Enrich matched nodes, add clang-only nodes, return statistics |
| `merge_edges` | Combine, drop dangling/artifact edges, de-duplicate |
| `resolve_calibration_ref` | Builds the `calibration_ref` block (type, min/max, unit, factor/offset, verbal table) |
| `build_characteristic_symbol_index`, `build_axis_pts_symbol_index` | Lookup: internal symbol → A2L entry |
| `axis_relation_name` | CURVE → `axis_reference`; MAP → `x_axis_reference`, `y_axis_reference` |
| `resolve_a2l_key` | Tries exact key, then adds `[0]`, `[0][0]`… (up to 4), then strips trailing indices |
| `apply_a2l` | The A2L join: matches by `calibration_path` (else label) against measurements, characteristics, axis_pts |
| `main` | CLI: `--graphify`, `--clang`, `--a2l`, `--scope`, `-o`; prints a merge summary |

**What `apply_a2l` adds to the graph**

| Added | Where |
|---|---|
| `metadata.calibration_ref` | on matching nodes (measurement match) |
| `metadata.a2l_characteristic_name`, `a2l_axis_pts_name` | on matching nodes |
| `A2LCharacteristic` node (id `a2lchar_<name>`) | new |
| `AxisReference` node (id `axis_<name>`) | new |
| Edges `mapped_to_a2l_characteristic`, `mapped_to_a2l_axis_pts`, `axis_reference` / `x_axis_reference` / `y_axis_reference` | new |
| `metadata.a2l_implicit_index_fallback = True` | when the `[0]` / strip fallback was needed |

---

### 4.4 `clangit.py`: the remote control

Reads `pipeline_config.json` (next to the script) and runs the other scripts.

| Config key | Meaning |
|---|---|
| `repo_root` | Source tree root |
| `output_dir` | Where JSON files go |
| `compile_commands`, `clang_lib` | Passed on to `extract_ast_graph.py` |
| `a2l_file` | The `.a2l` to read |
| `graphify_json` | Existing Graphify `graph.json` to merge into |
| `graphify_project_dir` | Folder that contains `graphify-out/` |
| `scripts_dir`, `exclude_dirs`, `default_scope` | Optional |

| Function / step | What it does |
|---|---|
| `load_config` | Loads JSON; explains the Windows-backslash mistake if parsing fails |
| `get_scripts_dir`, `default_config_path` | Defaults to the folder of `clangit.py` |
| `run` | Prints and executes a command; stops on failure |
| `find_c_files` | `--module kku` → `**/kku/src/*.c`; otherwise every `.c` |
| `extract` | Writes `<module>_filelist.txt`, runs `extract_ast_graph.py` → `<module>_ast.json` |
| `a2l` | Runs `a2l_extractor.py` → `a2l_export.json` |
| `merge` | Runs `merge_into_graphify.py` → `<module>_merged.json` |
| `view` | Copies the chosen JSON to `graphify-out/graph.json`, runs `graphify export html` (and/or `obsidian`) |
| `all` | extract + a2l + merge (**not** view) |

Examples: `python clangit.py all --module kku` then `python clangit.py view --module kku`.

---

### 4.5 `generate_compile_commands.py`

Builds a `compile_commands.json` so clang can compile every `.c` file.

| Function | What it does |
|---|---|
| `should_skip` | Directory excluded if its path contains an `--exclude-dir` text |
| `scan_tree` | One walk: collects folders that contain `.h` files (the `-I` list) and every `.c` file |
| `scan_headers_only` | Same, but only headers; used for `--extra-include-root` (e.g. `02_SwBasis`) |
| `build_arguments` | `clang -c -I… [-include shim] file.c` |
| `main` | Writes one entry per `.c` file |

---

### 4.6 `run-kku-clang.ps1` (PowerShell)

Compiles only `kku.c` and `kku_app.c` (picked by regex from the compile database) to see if clang is happy; can also dump the AST.

| Function | What it does |
|---|---|
| `Ensure-Se2CsHeader` | Creates `se2_cs.h` from `se2_cs.vorlage.h` with fixed values (SE2_AN, DQ381, AN, MQB_EVO) if missing |
| `Find-MissingHeaders` | Reads "file not found" errors and searches the workspace for candidates |
| `Get-ExtraIncludeArgs` | Builds `-I` flags from `02_SwBasis\inc` (plus `03_SwFunktion` with `-IncludeSwFunktionRecursively`) |
| `Get-ForceIncludeArgs` | Writes `clang_compat_shim.h` containing `#define __indirect` and force-includes it |
| `Invoke-NativeTool` | Runs a program with timeout; writes huge AST dumps straight to files |
| `Convert-ToRspToken` | Quotes arguments for a response file |

Switches: `-EmitAst`, `-AstDumpText`, `-StopOnFirstError`, `-BaseIncludeRoots`, `-IncludeSwFunktionRecursively`. Exit code 0 = success.

---

### 4.7 Check tools

**`compare_ids.py`**: do Graphify and clang use the same ids?

| Function | What it does |
|---|---|
| `load_nodes`, `filter_nodes`, `index_by_id` | Load, filter (`--kind`, `--file-contains`), index (warns on duplicate ids) |
| `_name_key`, `find_likely_pairs` | Finds "same label, different id" pairs, the sign of an id or repo-root mismatch |
| `main` | Prints counts, agreement %, examples; `-o` writes a report; **exit code 1** if anything differs |

**`inspect_matches.py`**: is the A2L join right?

| Function | What it does |
|---|---|
| `resolve_a2l_key` | Copy of the merge script's matcher (see warning in section 7) |
| `build_symbol_index` | symbol → entry |
| `simulate_join` | Re-does the join live from raw AST + A2L (no merge file needed) |
| `check_scope` | What % of A2L names mention `kku`? Tells you if a low match rate is normal |
| `closest_candidates` | Fuzzy near-miss suggestions |
| `show_matched_examples`, `show_unmatched` | Prints real matches with edges; lists unmatched nodes and unmatched A2L entries |
| `main` | Use exactly one of `--merged` or `--ast`, plus `--a2l` |

**`coverage_check.py`**: did extraction find everything?

| Function | What it does |
|---|---|
| `matches_scope` | Optional module filter |
| `count_by_suffix` | Counts names ending `_kl` (curve), `_kf` (map), `_ka` (axis), `_ko` (constant) |
| `count_measurements` | Counts measurements |
| `report` | OK / MORE THAN EXPECTED / MISSING SOME per category |
| `main` | You pass expected numbers from CANape: `--kl 6 --kf 14 --ka 20 --ko 61 --measurements 100` |

---

### 4.8 `common_key_jsons.py` (one-off script, no functions)

- Loads `b08.json` and `kku.json` (hard-coded paths).
- For measurements, characteristics, axis_pts, compu_methods, compu_vtabs: keeps only keys present in **both**, taking the values from `b08`.
- Writes `kku_final.json`.

---

### 4.9 `claude/explain_fn.py` (prototype)

Usage: `python explain_fn.py graph.json <function_name>`. Prints for one function:

| Section | Meaning |
|---|---|
| INPUTS | variables it reads, plus who writes them |
| OUTPUTS | variables it writes, plus who reads them |
| CALIBRATIONS | calibration items, their axes, and the axis input variable |
| CALLS / CALLED BY | callees and callers |

It deliberately skips co-readers, other users of the same calibration, and file siblings (the "noise" from the normal query).

---

### 4.10 The two notes in `claude/`

| File | Content |
|---|---|
| `graphify-query-flow.md` | The 10-step journey of a `/graphify` question; function and line numbers in `serve.py` / `cli.py` (commit `fe66389`); "where to hook in" table |
| `graphify-todos.md` | TODO list A–E: A = clang/A2L data, B = what Graphify prints, C = how Claude walks the graph, D = plain-language layer, E = validation |

---

## 5. The core `graphify` package (map only, from your query-flow note)

| File | Role | Key functions |
|---|---|---|
| `__main__.py` | Entry point | `main()` |
| `cli.py` | Command dispatch (`query`, `explain`, `path`, `affected`, `export`, `save-result`, `reflect`, hooks) | `_default_graph_path`, `_enforce_graph_size_cap_or_exit`, `_touch_query_stamp`, `_run_hook_guard` |
| `serve.py` | Search engine + MCP server | `_query_terms`, `_search_tokens`, `_compute_idf`, `_trigram_candidates`, `_score_query`, `_pick_seeds`, `_filter_graph_by_context`, `_bfs` / `_dfs`, `_subgraph_to_text`, `_query_graph_text`, `_tool_get_node`, `_tool_get_neighbors`, `_shortest_path_text` |
| `querylog.py` | Optional query log (`GRAPHIFY_QUERY_LOG_ENABLE=1`) | `log_query` |
| `ingest.py` | Saves Q&A as memory | `save_query_result` |
| `reflect.py` | Lessons for future sessions | `aggregate_lessons`, `render_lessons_md` |
| `install.py` | Installs the skill and hooks | `_skill_registration`, `_claude_pretooluse_hooks` |
| `build.py` | Builds the graph, keeps non-AST items across `update` | `_is_ast_tier` |
| `affected.py` | Reverse traversal for impact analysis | – |

**How a query works in one line:** question → keywords → score nodes → pick up to 3 start nodes → walk 2–3 hops → print NODE/EDGE text → Claude reads only that text.

---

## 6. "I want to change X: where do I go?"

| Goal | File and place |
|---|---|
| New machine paths (libclang, include paths) | `extract_ast_graph.py` top constants; `pipeline_config.json`; `run-kku-clang.ps1` parameters |
| Calibration registration function has a different name | `REGISTER_CAL_FUNCS` in `extract_ast_graph.py` |
| Add a new relationship found in C code | `walk()` or a new `handle_*` function in `extract_ast_graph.py` |
| Another lookup-function family (like `Ipo_*`) | `_IPO_LOOKUP_ROLES` in `extract_ast_graph.py` |
| Add a field from the A2L file (e.g. long description) | the dataclass and `scan_a2l()` in `a2l_extractor.py`, then `resolve_calibration_ref()` / `apply_a2l()` in the merge script |
| Change how A2L names match code names | `resolve_a2l_key()` in the merge script (and the copy in `inspect_matches.py`) |
| Set `context` on custom edges (TODO A6) | `add_edge()` in `extract_ast_graph.py` and the edge dicts in `apply_a2l()` |
| Mark custom items `_origin: "clang"` (TODO A7) | `add_node()` / `add_edge()` in the extractor, or at the end of `merge_into_graphify.py` |
| Function line range (TODO A5) | `walk()` FUNCTION_DECL branch: use `cursor.extent.start.line` / `.end.line` |
| One-liner per node in query output (TODO B1) | `_subgraph_to_text()` in core `serve.py` |
| Umlaut matching (TODO B4) | `_strip_diacritics()` / `_search_tokens()` in core `serve.py` |
| New pipeline step | add `cmd_<name>` and a choice in `main()` of `clangit.py` |

---

## 7. Things I noticed while reading (worth checking before you edit)

| # | Observation | Why it matters |
|---|---|---|
| 1 | `inspect_matches.py` `resolve_a2l_key` only **adds** `[0]`; the merge script also **strips** trailing indices | `inspect_matches --ast` can report fewer matches than a real merge for loop-expanded curves. Its own docstring says to keep them in sync |
| 2 | `extract_ast_graph.py` header docstring says `uses_var`, but the code emits `reads_var` / `writes_var` | The code already writes directed read/write relations. TODO A1 may be partly done; check what Graphify's `query` does with them (it loads the graph undirected but keeps `_src`/`_tgt`) |
| 3 | `explain_fn.py` expects relations `reads`, `writes`, `reads_calibration`, `has_axis`, `axis_input` | These names do not match `reads_var`, `writes_var`, `reads_calibration_field`, `x_axis_reference`… from your pipeline. On the merged graph it will show empty sections until names are aligned |
| 4 | Custom edges carry no `context` field | Questions containing words like "return" or "parameter" filter them out (TODO A6) |
| 5 | `clangit.py` mentions `pipeline.py` and `pipeline_config.example.json` | The example config is not among the project files; you need to create `pipeline_config.json` yourself |
| 6 | `clangit.py all` does not run `view` | Run `view` separately to see the result in Graphify |
| 7 | `run-kku-clang.ps1` appears twice in the project (15 Sep and 30 Sep) | Make sure you edit the newer one |
| 8 | Several scripts have hard-coded `C:\...` paths | `common_key_jsons.py` has no arguments at all |
| 9 | A2L: `LINK_MAP` decides the measurement key, not the `/begin MEASUREMENT` name | Intentional (kept from ScanDKSW); the friendly name is saved separately in `a2l_characteristic_name` |

---

## 8. Open items

- To document the core package function by function, I need to read each file (they are only searchable in the project right now). Tell me which ones matter most: `serve.py` and `cli.py` are the likely first two.
- The C# files (ScanDKSW) were not read; say so if you want them documented too.
