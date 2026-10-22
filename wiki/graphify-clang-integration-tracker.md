# Clang + A2L integration: tracker

Branch: `feat/clang-integration` (from `docs/graphify-wiki`). Updated 2026-10-04.
Legend: DONE / PARTIAL / TODO / DECIDED.

## 1. Work packages

| ID | Topic | Status | Where |
|---|---|---|---|
| T1 | Branch + this tracker | DONE | `wiki/graphify-clang-integration-tracker.md` |
| T2 | Read sources, find hook points | DONE | `extract.py` hook before "Relativize source_file" |
| T3 | Test env + fixtures | DONE | `tests/fixtures/clang_toy/` (kku.c/.h, kku_out.c, toy.a2l); libclang via `pip install libclang` |
| T4 | Clang module | DONE (prototype) | `graphify/extractors/clang_c.py` |
| T5 | A2L scan + join | DONE (prototype) | `graphify/extractors/a2l.py` |
| T6 | Hook in `extract()` + config | DONE | hook, `graphify-clang.json`, and CLI flags `--clang --compile-commands --a2l --libclang` on `extract`/`update` (flags override the file; not remembered between runs) |
| T7 | `update` integration | DONE (toy) | second `graphify update` keeps all clang/A2L items |
| T8 | Registry wiring (`serve`, `affected`, `explain`) | DONE | findings 4, 7, 8, 11 |
| T9 | Tests | DONE | `tests/test_clang_integration.py` (12 pass); full suite: same 1233 failures before and after (pre-existing, environment) |
| T10 | Docs, Copilot instructions, push | TODO | |

## 2. The 12 findings (from `graphify-enrichment-compat.md`)

| # | Finding | Status | How |
|---|---|---|---|
| 1 | read+write edge lost | DONE | clang module emits one `reads_writes_var`; `read`/`write` context filters also keep `readwrite` |
| 2 | no `source_location` | DONE | nodes `L<n>`, edges `at=file:line` + `metadata.lines` |
| 3 | no `context` | DONE | `relations.context_for()` stamps every edge |
| 4 | `affected` ignores relations | DONE | `affected.py` defaults extended from registry (verified) |
| 5 | `update` wipes items | DONE | items are stamped `_origin="ast"` by `extract()`; rebuilt per file |
| 6 | A2L text not searchable | DONE | A2L description copied to `rationale` (verified: "oil temperature threshold") |
| 7 | metadata not printed | DONE | NODE line `kind= range= mask=`; `explain` and `get_node` print kind, A2L link, range, params; `explain` sorts writes > reads > calibration > axis > a2l before degree |
| 8 | question words | DONE | hints, intent terms, aliases from registry ("who writes X" infers context=write) |
| 9 | writer -> reader path | TODO | needs derived `feeds` edge or new command |
| 10 | absolute paths | DONE | ids and `source_file` root-relative |
| 11 | noise seeds (file node) | DONE | `_pick_seeds` drops file nodes when a real symbol also matched (walking `defined_in`/`includes` is moot: clang pass does not emit them) |
| 12 | hubs not expanded | DOCUMENTED | use `explain`/`get_neighbors`; add to Copilot instructions |

## 3. Design decisions

| Topic | Decision |
|---|---|
| Role of clang | Optional second pass; tree-sitter stays the base layer |
| Activation | `graphify-clang.json` in scan root, or `GRAPHIFY_CLANG_CONFIG`; absent config or libclang = skipped, extraction unchanged |
| Failure mode | Pass failure prints one line and extraction continues |
| `_origin` | clang and A2L items use `"ast"` so tier logic needs no change |
| Merge rules | Existing tree-sitter node: gap-fill `type`/metadata (existing keys win). New clang-only node: `source_extractor=clang_only`, honors `scope`. Edges added only when both endpoints exist |
| Calibration proof | Only pointers registered via `Bios_RegisterCalibrationData` (configurable) count as calibration access |
| Registry | `graphify/relations.py` is the one place for relation name, context, hint words, affected set |
| Ptr cache | `graphify-out/cache/clang_ptr_rom.json` so incremental runs know registrations from unchanged files |

## 4. Verified on the toy (end to end through `graphify update`)

| Check | Result |
|---|---|
| Clang pass | 2 C files, +13 nodes, +23 edges |
| A2L join | 4 characteristic matches, 1 measurement |
| `query "oil temperature threshold"` | finds A2L characteristic and code field |
| `query "who writes Kku_Anf"` | context=write inferred, `reads_writes_var` edge with `at=kku.c:L18` |
| `affected Kku_Anf` | lists the readers/writers |
| `diagnose multigraph` | 38 raw edges in, none lost |
| Registration args | no spurious `reads_var` on the ROM struct |

## 5. Known limitations

| Limitation | Note |
|---|---|
| Header-only change | `update` does not re-parse C files unless a `.c` changed; use `update --force` |
| Stale A2L entries | removed only by a full extract |
| `kind=` for tree-sitter-only nodes | tree-sitter nodes have no `type`, so only enrichment nodes show it |
| Only `.c` files are translation units | headers are parsed through their `.c` |
| Not yet run on a real-size project | prototype verified on the toy only |

## 6. Open items

- Finding 9: writer -> reader path (`feeds` edge or direction-aware command)
- Run on a real-size project (first real `compile_commands.json`)
- `.github/copilot-instructions.md` text, push, optional PR to `current-v8` only if asked (T10)

## 7. First real run fell back to tree-sitter (reported 2026-10-04)

| Likely cause | How the pass now tells you |
|---|---|
| No `graphify-clang.json` / no `--clang` flag | prints `clang pass OFF (tree-sitter only): no graphify-clang.json in <root>` |
| `libclang` not installed in the Python that runs graphify (venv / pipx / uv tool) | `clang-check` shows the exact interpreter and the `pip install` line |
| libclang DLL not found (Windows) | `clang-check` FAIL line; set `"libclang": "C:/.../libclang.dll"` in the config |
| `compile_commands.json` path wrong | `clang-check` FAIL line |

Run `graphify clang-check <project root>` first. Minimal `graphify-clang.json` in the project root:

```json
{
  "compile_commands": "compile_commands.json",
  "a2l": "path/to/file.a2l",
  "libclang": "optional path to libclang.dll / .so"
}
```

## 8. Extractor choice and debugging (added 2026-10-04)

| Option | How | Effect |
|---|---|---|
| `tree-sitter` | `--extractor tree-sitter` / `"extractor": "treesitter"` / `GRAPHIFY_EXTRACTOR=tree-sitter` | clang pass skipped; original Graphify behaviour |
| `both` (default) | `--extractor both` / `--clang` / config present | tree-sitter first, clang enriches and adds |
| `clang` | `--extractor clang` / `"extractor": "clang"` | tree-sitter symbol nodes (and their edges) of every C file clang parsed are replaced by clang's. File nodes and file-to-file `imports` stay. A C file clang cannot parse keeps its tree-sitter symbols and is listed in a warning. Non-C languages stay tree-sitter. |

| Debug | How | Output |
|---|---|---|
| Verbose trace | `--clang-debug` / `"debug": true` / `GRAPHIFY_CLANG_DEBUG=1` | stderr + `graphify-out/clang_debug.log` |
| Report | same switch | `graphify-out/clang_report.json` |

The trace and report show: config source, extractor, per-file compile-flag source (`compile_commands` / `extra_args` / default), every clang error with file:line:col, pointer registrations found, member accesses ignored because the pointer is not registered, per-file node/edge counts, tree-sitter nodes removed (clang mode), A2L parse counts, unmatched code fields and unmatched A2L characteristics.

Known limit of `clang` mode: type-stub nodes with no source file (e.g. typedef names) stay from tree-sitter; a header only partly visible to clang loses tree-sitter symbols that clang never referenced.

## 9. First real run, 38,761 files (2026-10-04)

| Observation | Meaning / action |
|---|---|
| `clang pass OFF` | no `graphify-clang.json` found; user's compile DB is named `compile_commands_full.json` (set `"compile_commands"` accordingly) |
| 214 files with syntax errors | tree-sitter has no preprocessor; macro-heavy / compiler-specific C (headers especially) trips it. Message only named 5 files, so `graphify-out/syntax_errors.txt` now lists all, with counts per extension |
| Headers are extracted by tree-sitter | yes (`.h` is scanned as C). Clang reads headers through the `.c` files that include them, with the real preprocessor |

## 10. Preprocessing problems (reported 2026-10-04: "clang still has issues")

| Cause | Fix in code |
|---|---|
| Relative `-I` in `compile_commands` resolved against the wrong folder (the entry's `directory` was ignored) | include paths (`-I -isystem -iquote -include ...`) are now resolved against the entry's `directory` |
| Build-tool flags libclang chokes on (`-o`, `-MD`, `-MF`, `-c`) | removed |
| `@file.rsp` response files | expanded |
| Cross-compiler-only flags (`-mcpu=...`) | config `drop_args` (prefix match) |
| Missing defines / target / extra include folders | config `append_args` (relative `-I` is relative to the config file) |
| Which header is missing? | `clang_report.json` -> `missing_includes_top`, plus a PREPROCESSING warning in the debug log |
| One file at a time | `graphify clang-check . --file path\to\x.c` prints exact arguments, every error, missing headers |

## 11. Hop depth (asked 2026-10-05)

| Surface | Depth control | Default / limit |
|---|---|---|
| `graphify query` (CLI) | `--depth N` (new) | default 2, range 1-6 |
| MCP tool `query_graph` (what the AI calls) | `depth` argument | default 3, capped at 6 in `serve.py` (`_tool_query_graph`) |
| `graphify affected` | `--depth N` | default 2 |
| Output size | `--budget N` / `token_budget` | default 2000 tokens; more hops need a bigger budget |
| Hubs | not expanded through unless they are a start node (degree >= max(50, p99)) | `_bfs` / `_dfs` in `serve.py` |

## 12. Syntax errors, md files, ignore (asked 2026-10-05)

| Observation | Meaning / action |
|---|---|
| Tree-sitter syntax errors: .c=74, .h=140 | expected for embedded C (no preprocessor, compiler extensions); `syntax_errors.txt` now also shows the failing source line (`>> ...`) |
| Clang runs after tree-sitter, not instead | tree-sitter pass still runs on every file; in `clang` mode its C symbols are replaced afterwards (speed-up option: skip tree-sitter for C files in clang mode: TODO, ask user) |
| `.md` files are extracted | `update` adds docs that have an AST extractor (markdown) to the code list; exclude via `.graphifyignore` |

## 13. File-level connections (reported 2026-10-05)

| Observation | Cause | Fix |
|---|---|---|
| KKU `.c`/`.h` not connected to other modules; many headers floating | tree-sitter resolves `#include "x.h"` only next to the including file (no `-I`), so cross-folder includes become dead placeholders | clang pass now emits file -> file `imports` edges from `tu.get_includes()` (real `-I` resolution); `metadata.resolved_by=clang` |
| `ext_*` header nodes on kku.c | tree-sitter placeholder for includes it could not resolve | `extractor=clang`: tree-sitter `imports` of every includer clang saw are replaced; orphaned `ext_*` nodes removed. `both`: clang edges added, tree-sitter ones kept |
| Headers outside the scan root | no file node exists | edge skipped; scan the parent folder or accept |

## 14. Module view and connectivity check (asked 2026-10-06)

| Topic | Status |
|---|---|
| New `graphify focus "<path or label>" [--depth N] [--max-degree N] [--out DIR]` | DONE: cuts a module subgraph (default 1 hop, hubs not expanded) to `graphify-out/focus/<name>/graph.json`, prints inside/outside edge counts by relation, ext_* count, file nodes with no `imports` edge |
| User still sees unrelated nodes and kku files unconnected after include fix | OPEN: needs `focus` output + `clang_debug.log` line `clang include edges added` from the user's run |

## 15. Missing `contains` edges (reported 2026-10-06)

| Cause | Fix |
|---|---|
| clang pass looked up file nodes by a derived id (`parent_stem`), but tree-sitter file ids are still absolute-path based at hook time: lookup failed silently. `clang` mode dropped tree-sitter's `contains` edges and added almost none back; `both` mode never added them for variables | file nodes are now found by `source_file` (`_file_id_map`); contains edges added for functions and variables in both modes. Test `test_contains_edges_for_functions_and_variables` |

## 16. Orphan nodes from headers (reported 2026-10-06)

| Cause | Fix |
|---|---|
| Clang walks every top-level declaration of every included (non-system) header; each `extern T x;` became a variable node, even when nothing used it -> large orphan clouds around a module | top-level non-definition variable declarations are skipped; a variable becomes a node only when it is defined in a parsed file or referenced by a function (read/write). Count in debug log: `extern declarations in headers not turned into nodes`. Test `test_unused_extern_declarations_in_headers_do_not_become_nodes` |
| Still open | tree-sitter nodes for the same headers (`both` mode, or headers outside clang's reach); function prototypes are only added when called |

## 17. One file node per included header (reported 2026-10-06)

| Observation | Cause | Fix |
|---|---|---|
| Symbols from included headers had no `contains` edge to their header; old standalone clang graph had a node per header | clang pass could only link to file nodes tree-sitter had made; headers outside the scan root or not in the scan had none | `_ensure_file_nodes`: every non-system file clang saw (every include, every symbol source) gets a file node if missing, in or out of the scan root; `contains` and `imports` edges then attach. Debug log: `clang added N header/file node(s)...`; summary line `+N file nodes` |
| Symbol ids contained the machine path for out-of-root headers | `stem()` used the absolute path | out-of-root files get portable `ext_<dir>_<file>` ids |
| System headers | not wanted | path heuristic (`/usr/include`, mingw, Windows Kits, Program Files, `-isystem` dirs). Add markers if your compiler lives elsewhere |

## 18. Header declarations (reported 2026-10-06; reverses part of section 16)

| Observation | Decision |
|---|---|
| Only headers' symbols that kku uses had `contains`; everything else a header declares was missing | the user wants the old standalone-graph behaviour: a header contains ALL it declares |
| Fix | config `header_declarations` (default `true`): top-level `extern` variables and function prototypes of every non-system header become nodes (decl-only) with `file --contains--> symbol`. A declaration with exactly one definition in the scan is merged into the definition (so it then belongs to the defining `.c`). `false` = previous behaviour (only referenced/defined symbols) |
| Cost | more nodes (every declaration of every included header); use `graphify focus` for module views |

## 19. Duplicate nodes `ext_*` vs normal (reported 2026-10-06)

| Observation | Cause (inferred; needs the user's log to confirm) | Fix |
|---|---|---|
| Same symbol twice: normal node + orphan `ext_src_vwh_vwh_flagrdlock` | header declaration (clang, decl-only) not merged with the definition when the definition node exists under another id (tree-sitter-made, or defined in a file clang did not parse) | `_unify_declarations`: a decl-only clang node whose label matches exactly one existing symbol node is merged into it (edges re-pointed). Survivors carry `metadata.declaration_only=true`. Ambiguous (several definitions) are left alone |
| `ext_` ids at all | header lies outside the scan root, or its path resolves differently | debug log lists up to 5 such files (`project files OUTSIDE the scan root`); scan a higher folder if they belong to the project |

## 20. Same label, two ids (user data, 2026-10-06)

| Data | Reading |
|---|---|
| `ext_src_vwh_vwh_flagrdlock` and `c_001_fahrzeug_..._03_swfunktion_ku_kku_src_di_vwh_src_vwh_vwh_flagrdlock`, same label | first = clang declaration node (out-of-root or decl-only form), second = a node whose id still carries the absolute machine path (tree-sitter / definition form) |
| Fix 1 (commit "merge header declarations") | declaration merged into a uniquely named existing symbol |
| Fix 2 | candidates grouped by root-relative source file (abs/rel forms of one file count once); several definers: the file with the same stem as the header (vwh.h <-> vwh.c) wins |
| Diagnostic | `graphify focus ...` now prints `symbols with the same label more than once` with ids and source files |
| Still unexplained | why one id keeps the absolute machine path after extract()'s id canonicalisation: needs that node's `source_file` from the user's graph.json |

## 21. Read/write classification (found 2026-10-08)

| Pattern | Was | Now |
|---|---|---|
| `g_x++` / `--g_x` | `reads_var` | `reads_writes_var` |
| `g_arr[i] = v`, `g_st.f = v` | `reads_var` | `writes_var` (follows `.member` / `[i]` on arrays and structs; stops at pointers) |
| Assignment inside a macro (`SETV(v)`) | `reads_var` (token offsets unreliable in expansions) | `writes_var` (operator kind read from libclang) |
| `&g_x` / `p = &g_x; *p = v` | `reads_var` | `reads_var` + `metadata.address_taken=true` (cannot be attributed statically) |
| Not detectable | | writes through pointers, code in inactive `#ifdef`, files outside the compile DB |

## 22. Arrow direction in graph.html (found 2026-10-08)

| Item | Result |
|---|---|
| Symptom | `calls`, `contains`, `imports` drawn backwards; `graph.json` was correct |
| Cause | graph.json is loaded as an undirected graph (endpoint order lost); renderers read `_src/_tgt` |
| Fix | `graphify export` and the shared `load_node_link_graph` stamp `_src/_tgt` from `source/target` |
| Check | every edge of `graph.html` compared with `graph.json`, all relations, all paths (`update`, `update --no-cluster`, `export html`): 0 mismatches |

## 23. Module slicing: `graphify focus` (2026-10-08)

A slice is a view of the finished full graph; nothing is re-parsed.

| Part | Rule |
|---|---|
| Inside | nodes whose `source_file` contains the pattern(s) (`--match path`, default); `--exclude` removes |
| Hop | `calls`: one edge. `data`: function -> variable -> function (the variable is pass-through, not a hop) |
| Direction | `in` = upstream (callers, writers of variables read); `out` = downstream (callees, readers of variables written); a node found upstream keeps going upstream |
| Variable interface | variables the module reads (`in`) / writes (`out`) are always shown as `shared` |
| Border | outside functions at the last hop: shown, not expanded; reasons in `focus_report.json` |
| Fan-out | more than `--fanout` (default 30) neighbours of one expansion: one `stub` node |
| Structure | module files + their `contains`; DIRECT includes of module files only (`--no-includes` drops); `contains`/`imports` are never followed outward |
| `reads_writes_var` | counts as reader and writer |
| `address_taken` | flagged on the variable node and listed in the report |
| Colours | role -> community: inside, border, shared, include, attached, stub |
| Legacy | `--flow all` = old N-hop expansion over every relation |
| Config | `--config focus.json` (`include`, `exclude`, `flow`, `direction`, `depth`, `fanout`, `match`, `includes`) |

## 24. Static vs global vs extern variables (2026-10-08)

| Variable | Before | Now |
|---|---|---|
| `static int s;` in a.c | `declaration_only=true` (wrong), contained by a.c | definition: `metadata.storage=static`, `defined_in=a.c`, contained by a.c (`role=definition`) only; same name in b.c is a separate node |
| `int g;` in a.c + `extern int g;` in g.h | one node in g.h, `declaration_only=true` (wrong), a.c not linked | `storage=global`, `defined_in=a.c`; contained by g.h (`role=declaration`) AND a.c (`role=definition`) |
| `extern int x;` never defined in a parsed file | declaration-only | unchanged: `declaration_only=true`, contained by the header only |
| Cause | libclang reports a tentative definition (`int g;`) as `is_definition()==False` | non-extern file-scope variable counts as a definition |
| `graphify focus` | owner = declaring file | a variable also belongs to the module that DEFINES it (`defined_in`) |

## 25. What the AI sees, and `explain --inputs` (2026-10-09)

| Command | Chooses | Prints |
|---|---|---|
| `explain X` | X + direct neighbours (1 hop), max 20, ordered write, read, calibration, axis, a2l, others | node facts, then `-->`/`<--` neighbours with `file:line` |
| `query "..."` | up to 3 scored seeds, BFS to `--depth` (default 2), hubs not expanded, ~2000-token cut (nodes first, then edges) | `NODE` and `EDGE` lines |
| `explain F --inputs [--depth N --fanout N --budget N]` | upstream data slice from F (`slice_graph`, `direction=in`, variable = pass-through) | params, callers, per input variable its writers with `file:line`, calibrations; hop by hop |

| Newly printed fact | Where |
|---|---|
| `storage`, `defined_in`, `decl_only` | `NODE` lines, `get_node`, `explain` |
| contains `role=declaration|definition`, `address_taken` | `EDGE` lines, `explain`, `get_neighbors` |
| `Address: taken` | `explain` / `get_node` for a variable whose address is taken |

| `--inputs` rule | Behaviour |
|---|---|
| Hop 1 | always complete |
| Deeper hop | printed only if the whole hop fits the budget, else "stopped after hop k" |
| Variable without writer | says so (pointer write, inactive `#ifdef`, outside compile DB, external input) |
| Read-modify-write | "also written by F itself" |
| Callees | not shown (outputs, not inputs) |

## 26. Slicing one function: `graphify focus --function` (2026-10-13)

| Item | Behaviour |
|---|---|
| Command | `graphify focus --function "Kku_Main" [--depth N] [--flow ...] [--direction ...] [--fanout N] [--graph P] [--out DIR]` |
| Selector | exact: `name`, `name()`, `path/file.c::name` or node id (no substring; a variable is not accepted) |
| Ambiguous name | lists the candidates (file + id) and exits 1, never guesses |
| Defaults | `--flow calls,data,calibration`, `--direction both`, `--depth 1` (a module keeps `calls,data`) |
| Slice | inside = the function; shared = its variables/calibrations; border = callers, callees, writers, readers; no headers |
| Output | same as module slicing: `graph.json`, `.graphify_labels.json`, `focus_report.json` (`graphify export html --graph ...`) |
| Not supported | `--flow all` (legacy) with `--function` |

## 27. Parameter databank (first prototype: one dataset)

| Item | Detail |
|---|---|
| Module | `graphify/params.py` - `.par` parser, SQLite databank (`params.db` next to `graph.json`), printing |
| Link key | A2L characteristic name = `metadata.a2l_characteristic_name` (axes via A2L `axis_references`, bits via parent + `.bitN`) |
| Import | `graphify params import FILE.par --name SET [--a2l FILE.a2l\|.json] [--project --variant --version] [--db PATH]` |
| Import report | `params_import_<SET>.json`: not_in_a2l, a2l_without_value, kind_vs_a2l, axis_missing, shape_vs_axis (rows = Y axis, cols = X axis), enum_issues (label vs `COMPU_VTAB`), unparsed lines, duplicates |
| Show | `graphify params show NAME [--dataset SET]`, `graphify params list` |
| AI output | `graphify explain F --inputs --dataset SET` prints the value under each calibration; scalar/enum 1 line, curve/map as table with axis values up to 64 cells, larger as summary + fetch hint |
| Compare | Not built yet (later: `params diff`, numeric by physical value, enum by raw + label change) |
| Assumptions | Row-major map order (checked only through the axis counts); raw first, physical second; no string parameters |
| Not static | Writes through pointers, inactive `#ifdef` code, files outside the compile DB |

## 28. `--par` in the extraction flow + A2L / PAR / code coverage report

| Item | Detail |
|---|---|
| Flags | `graphify update . --clang --a2l X.a2l --par X.par [--par-name SET]` (also keys `par`, `par_name` in `graphify-clang.json`) |
| Step | after the A2L join the `.par` is imported into `graphify-out/params.db`; failures only warn, extraction never breaks |
| Report | `graphify-out/param_coverage.csv` (`;`-separated, Excel-ready) + `param_coverage.json` + `params_import_<SET>.json` |
| Columns | name, kind (CHAR/AXIS), A2L yes/no, PAR yes/no, CODE yes/no, status, code nodes, note |
| CODE means | a code node joined to the A2L name (`a2l_characteristic_name` / `a2l_axis_pts_name`), or an unmatched `calibration_field` for names the A2L lacks |
| PAR means | entry in the `.par`; `Parent.bitN` counts for `Parent` |
| Status | ok / no value in PAR / not found in code / A2L only / not in A2L / PAR only / code only |
| Notes | A2L symbol the code should contain; "looks the same (case/index differs) - join missed it"; "A2L has 'X' (spelling differs)" |
| Needs | the coverage report needs `--a2l` as well; without it only the values are stored (warning) |

## 29. Struct calibration variables (A2L `variable_ko.x`, `variable_ko.y`)

| Item | Detail |
|---|---|
| Cause | Code has the struct variable `variable_ko`; the A2L has its members as separate characteristics. Member accesses on a struct that is not a registered calibration pointer are not code nodes, so the exact-name join found nothing |
| Fix | `join_a2l` links a still-unmatched `variable` node to every A2L characteristic whose symbol starts with `<name>.` or `<name>[` (edge `mapped_to_a2l_characteristic`) |
| Node metadata | `a2l_members` (up to 200 names), `a2l_member_count` |
| Stats | `struct_variables`, `struct_members`; these characteristics no longer appear in `unmatched_a2l` |
| Coverage report | CODE = yes, `code_nodes` shows `[struct: member not tracked]` |
| `explain --inputs --dataset` | a struct variable prints the values of up to 12 members |
| Limit | Which member a function reads is not known (member-level tracking would need the member path on the `reads_var` edge) |

## 30. Unresolved array indices (`ptr->parameter_ko[i][j]` vs 4 A2L element characteristics)

| Item | Detail |
|---|---|
| Situation | Code reads `parameter_ko[idx_x][idx_y]` with run-time indices; the A2L has one characteristic per element |
| Extractor | A non-constant index is expanded over the array size for the first index only; the rest stays `?` (e.g. `...parameter_ko[0][?]`, edges AMBIGUOUS) |
| Join | `join_a2l` matches such a calibration field against still-unmatched A2L symbols: both sides are split into tokens (`.`, `[`, `]`), `?` matches any token, the token count must be equal; spelling `x[1][2]` and `x.1.2` give the same tokens |
| Node metadata | `a2l_members` (matched names), `a2l_member_count`, `a2l_member_kind = index` |
| Stats | `index_fields`, `index_members`; matched symbols leave `unmatched_a2l`, the field leaves `unmatched_code` |
| Coverage report | CODE = yes, note `[index not resolved]` |
| Not covered | Code index that folds to a number while the A2L element carries a symbolic name (enum/name index like `[DS]`): the tokens differ, so it stays unmatched. Needs an enum map |

## 31. Integer suffix in constant indices (`Tab[0][0u]` vs A2L `Tab[0][0]`)

| Item | Detail |
|---|---|
| Symptom | Code node `CalibrationROM.Torque_Default_ko[0][0u]` never matched the A2L symbol `CalibrationROM.Torque_Default_ko[0][0]`; the A2L members only hung on the ROM struct variable (`a2l_members`) |
| Cause | `_resolve_access_path` copied the raw literal token for a constant index (`0u`, `1UL`, `0x1`); `_eval_int` already stripped suffixes but was not used there |
| Fix | A constant index is written as the plain decimal number |
| Effect | Exact join works for these fields (`a2l_characteristic_name` set, one edge per element) |
| Note | The struct-variable link (section 29) hid this: the coverage report showed CODE = yes with `[struct: member not tracked]`. After the fix those parameters are linked through the code node itself |

## 32. Struct fallback removed; every A2L node is kept

| Item | Detail |
|---|---|
| Removed | Section 29's fallback (linking a struct variable to all A2L characteristics below it, `a2l_members` on the variable). It made unmatched A2L entries look matched |
| New rule | `join_a2l` always creates a node for EVERY A2L characteristic (`a2l_characteristic`) and axis (`a2l_axis`), matched or not (axis edges included) |
| Flag | `metadata.code_matched` = true when a code node points at it (`mapped_to_a2l_characteristic` / `mapped_to_a2l_axis_pts`), else false |
| Kept | Unresolved-index match (section 30): it links real code nodes (`...[0][?]`) to element characteristics |
| Coverage report | A2L-only rows get the note `code has variable 'X', but no access to this member was found` when the struct variable exists in code |
| Cost | Graph size grows by the number of unmatched A2L entries |
| Later | Merge code and A2L nodes into one parameter node (planned, not now) |

## 33. New mapping: one code node per variable, indices and mask off the name

Supersedes the per-element handling of sections 30 and 31 (expansion over the array size, `[?]` paths, wildcard match, suffix fix). Struct fallback (section 29) stays removed.

| Item | Detail |
|---|---|
| Code node | One `calibration_field` per variable path with all `[..]` removed: `Cal.Tab_ko`. Struct/member names stay (`Cal.var_ko.x` and `.y` are two nodes). No copies per element, no `[?]` |
| Index of a read | On the read edge (`reads_calibration_field`): `metadata.index` = list of distinct texts, e.g. `["[0][1]", "[i]"]` (constants folded: `0u`, `0x1` -> `0`; run-time expressions keep their text). Read edges are EXTRACTED (certain at variable level); a run-time index is only visible as its text (`[i]`) |
| Bit mask | Not a separate node any more (`Flags#2`): `metadata.mask` list on the read edge |
| A2L key | `SYMBOL_LINK` (or link-map symbol), CANape `._0_` -> `[0]`, then split into plain name, index text, `#mask` (`split_symbol`). Whole-table types (CURVE, MAP, CUBOID) already arrive without the trailing index |
| Join | Plain code name == plain A2L name. One code node -> every A2L entry with that plain name (`mapped_to_a2l_characteristic` / `mapped_to_a2l_axis_pts`) |
| On the link | `metadata`: `characteristic` (A2L name as text), `index` (e.g. `[1][0]`), `mask` |
| On the code node | `a2l_members` (A2L names as text, max 200), `a2l_member_count`; if exactly one entry matched also `a2l_characteristic_name`; `a2l_range` over all entries; `a2l_axis_members` for axes |
| Unchanged | All A2L nodes are kept (`code_matched` flag); measurements still use `resolve_a2l_key`; `.par` matching by A2L name; coverage report columns |
| Display | `explain` / `query` edge lines show ` index=[..] mask=..` |
| Re-extract | Node ids of calibration fields changed (no index/`#mask` in the id): delete `graphify-out` and extract the full repo |

## 34. Calibratable symbols have two parts

| Item | Detail |
|---|---|
| Rule (from the project) | A calibratable variable's symbol always has at least two parts (`kwsapp_rom.variable`); a non-calibratable one is the variable label alone |
| Join | A code node links to A2L characteristics / axis points only when its plain name contains a `.`. A plain variable never links to a characteristic (it can still get `calibration_ref` from a measurement) |
| Coverage report | An A2L characteristic or axis whose symbol has a single part gets the note `A2L symbol has a single part ... - check` |

## 35. Rule of section 34 relaxed

| Item | Detail |
|---|---|
| Project fact | No ROM struct in the symbol means "not calibratable, but measurable"; it is a normal case |
| Join | The two-part restriction is removed again: a code node links to A2L characteristics / axis points by plain name, with or without a `.` |
| Coverage report | The note `A2L symbol has a single part ... - check` is removed. The note `code has variable 'X', but no access to this member was found` appears only for struct members (a `.` in the plain symbol) |
| Note kept | A2L entries whose symbol has no struct get the neutral note `no struct (plain variable, measurable)` in `param_coverage.csv` |

## 36. Prefix match: A2L symbol longer than the code path

| Item | Detail |
|---|---|
| Case | A2L `abc_rom.variable.an`, C code reads `abc_rom.variable`: the member `.an` does not exist in the C code, so no exact match is possible |
| Rule | After the exact join, every still-unmatched A2L entry links to the LONGEST code `calibration_field` path that is a prefix at a `.` boundary (`abc_rom.variable` of `abc_rom.variable.an`). The ROM struct variable (`abc_rom`) is never a target; an exact match always wins |
| Link | `metadata.via = prefix`, `metadata.suffix = .an` |
| Code node | `a2l_members` (merged with exact ones), `a2l_prefix_members` |
| Coverage report | CODE = yes, code_nodes text `[prefix match: A2L symbol is longer than the code path]`. The member note now reads `ROM struct 'X' is in the code, but no code access to this member was found` |

## 37. Whole-struct reads expand to member nodes (type-based)

- Trigger: a calibration path whose C type is a struct/union is read as a whole (`f(ptr->Feh)`, `&ptr->Feh`).
- The header type is walked with libclang (`get_fields()`): nested structs, arrays of structs, anonymous struct/union (flattened), bit-fields as leaves.
- Each leaf gets a `calibration_field` node `<path>.<member path>` (no array indices), `from_struct_type` metadata.
- Edges: function -> member `reads_calibration_field`, confidence `INFERRED`, `via: struct_type`; base -> member `exposes_calibration_field`.
- Direct member reads (`ptr->Feh.flag.Bit`) are not expanded.
- Limits: depth 10 (`_MAX_STRUCT_DEPTH`), 500 leaves per type (`_MAX_STRUCT_MEMBERS`); truncation flagged as `members_truncated` on the base node.
- Prefix match (section 36) stays as fallback.


## 38. Coverage report read a capped list (fix)

- Symptom: struct with many elements x bits; only the first ~200 A2L entries (alphabetical) showed as found in code, the rest "not found in code" although the edges existed.
- Cause: `coverage_rows` used `a2l_members` on the code node, which is cut to `MAX_STRUCT_MEMBERS` (200).
- Fix: `coverage_rows(..., edges)` also reads `mapped_to_a2l_*` edges (complete). Node lists stay capped (display only).
