# Graphify commands: guide for YOU (simple version)

Source: `graphify --help` (`__main__.py`) and the code in `cli.py` (fork `dipak4coding/graphify`, branch `current-v8`).
Run any command in the VS Code terminal. Replace `merged.json` with your own graph file.

**How to read the tags (updated 2026-10-23)**

| Tag | Meaning |
|---|---|
| (no tag) | Original Graphify feature, as in the upstream project |
| **[ADDED]** | Added by us in this fork (clang AST, A2L, `.par` parameters, focus, inputs) |
| **[CHANGED]** | Original feature that we extended or whose behaviour changed |

Details and dates of every addition: `graphify-clang-integration-tracker.md` (section numbers are given as "tracker §N").

**One rule to remember:** almost every command reads `graphify-out/graph.json`. Use `--graph <file>` to point it at another file (for example your clang + A2L merged graph).

---

## 1. The 6 commands you will use most

| What you want | Command | What you get |
|---|---|---|
| Ask a question | `graphify query "who writes Kku_xyz" --graph merged.json` | A small subgraph: NODE lines and EDGE lines. This is what the AI also receives. |
| Find the route between two things | `graphify path "FuncA" "Kku_xyz" --graph merged.json` | Shortest chain of edges between A and B |
| Explain one thing **[CHANGED]** | `graphify explain "FuncA" --graph merged.json` | The node and its neighbours in plain words. Now also shows storage, `defined_in`, address-taken, and the A2L range of calibrations. |
| What goes INTO a function **[ADDED]** | `graphify explain "FuncA" --inputs --graph merged.json` | Parameters, callers, every input variable with its writers (`file:line`) and the calibrations used. |
| What breaks if I change X **[CHANGED]** | `graphify affected "FuncA" --graph merged.json` | Everything that calls or uses X. Our read/write/calibration relations are now in the default list. |
| Cut out one module **[ADDED]** | `graphify focus ku/kku --flow calibration` | A small graph of one module and its neighbours (nothing is re-parsed). See section 12. |
| Value of a parameter **[ADDED]** | `graphify params show NAME --dataset SET` | Value(s) from the `.par` databank. See section 14. |
| Biggest hubs | `graphify god-nodes --top 10 --graph merged.json` | The 10 most connected nodes |
| Refresh the graph after editing code | `graphify update .` | Re-reads the code files only. No AI and no API key needed. **[CHANGED]** With `--clang --a2l X --par Y` it also runs our clang, A2L and parameter steps (section 11). |

---

## 2. Asking questions (read the graph)

| Command | Options | Plain meaning |
|---|---|---|
| `query "<question>"` | `--dfs` | Go deep along one chain instead of wide around the start nodes |
| | `--budget N` | Maximum size of the answer in tokens (default 2000). Output is cut with a `TRUNCATED` notice. |
| | `--context C` | Keep only edges of one kind, e.g. `call`, `import` (can be repeated) |
| | `--graph <path>` | Which graph file to use |
| `path "A" "B"` | `--directed` / `--undirected` | Follow arrows only, or ignore arrow direction |
| `explain "X"` | `--graph` | Description of one node |
| **[ADDED]** `explain "F" --inputs` | `--depth N`, `--fanout N`, `--budget N` | Upstream data slice of function F, hop by hop (tracker §25). Hop 1 is always complete; a deeper hop is printed only if the whole hop fits the budget. |
| **[ADDED]** `explain "F" --inputs` | `--dataset NAME`, `--db PATH` | Also prints the parameter value under each calibration (from the `.par` databank, section 14) |
| `affected "X"` | `--relation R` | Which edge kinds to walk backwards (repeatable). **[CHANGED]** Default: calls, indirect_call, references, imports, imports_from and a few more, plus our `reads_var`, `writes_var`, `reads_calibration_field` and related relations. |
| | `--depth N` | How many steps back (default 2) |
| `god-nodes` | `--top N`, `--json` | How many to show; JSON instead of text |

**Tips**
- Use exact names (for example the `Kku_` name). Plain English words do not match, because only labels and ids are searched.
- **[CHANGED]** `affected` now also walks our read/write/calibration relations by default (they come from `relations.py`). Use `--relation R` only to restrict it.
- **[ADDED]** `query` words such as "reads", "writers", "calibrations", "axes", "asap2" now select the matching edge kinds, and file nodes no longer pull in their whole contents when a real symbol matched.
- **[CHANGED]** `NODE` lines now show `kind=` (variable, calibration_field, a2l_characteristic, a2l_axis) and `range=` for calibrations; `EDGE` lines show ` index=...` and ` mask=...` when present.

---

## 3. Building and updating the graph

| Command | Needs an AI key? | Plain meaning |
|---|---|---|
| `update [path]` | No | Re-extract code files with tree-sitter and rebuild the graph. Use after you change code. **[CHANGED]** Accepts the clang / A2L / `.par` flags of section 11. A sub-folder as `path` gives a partial run. |
| | | `--force`: overwrite even if the new graph has fewer nodes (use after deleting code). Same as `GRAPHIFY_FORCE=1`. |
| | | `--no-cluster`: skip grouping into communities |
| | | **[ADDED]** `--clang`, `--clang-debug`, `--extractor`, `--compile-commands`, `--libclang`, `--a2l`, `--par`, `--par-name`: see section 11 |
| | | **[ADDED]** writes `graphify-out/syntax_errors.txt`: every file tree-sitter could not parse (by extension, with the failing line) |
| `extract <path>` | Only for docs, papers and images | Full build in one go, for CI or scripts. Code-only folders need no key. |
| | | `--code-only`: skip documents and images |
| | | `--out DIR`: where `graphify-out/` is written |
| | | `--force`: ignore the cache and redo everything |
| | | `--max-workers N`: number of parallel code readers |
| | | `--no-cluster`: write raw extraction only |
| | | `--global`, `--as <tag>`: also add the result to the global graph |
| `watch <path>` | No | Stays running and rebuilds when a code file changes |
| `cluster-only <path>` | No | Redo only the community grouping and the report |
| | | `--no-viz`: do not make `graph.html` (use for big graphs) |
| `label <path>` | Yes (an LLM) | Give communities readable names instead of "Community 7" |
| | | `--missing-only`: only name the unnamed ones |
| `add <url>` | Yes for docs | Download a web page into `./raw` and update the graph |
| `clone <github-url>` | No | Clone a GitHub repo and print its path |
| `check-update <path>` | No | Tells you if a re-extraction with an AI is waiting. Safe to run from cron. |

**For your clang pipeline:** **[CHANGED]** with `--clang --a2l X.a2l --par Y.par`, `update` runs the clang pass, the A2L join and the `.par` step itself, so `merge_into_graphify.py` is no longer needed for that. Plain `graphify update .` replaces only the tree-sitter (`ast`) items; clang and A2L items survive.

---

## 4. Combining graphs

| Command | Plain meaning |
|---|---|
| `merge-graphs g1.json g2.json --out merged.json` | Join two or more graphs into one cross-repo graph (default output `graphify-out/merged-graph.json`) |
| `global add <graph.json> --as <tag>` | Add a project to the shared global graph at `~/.graphify/global-graph.json` |
| `global list` | List the projects in it |
| `global remove <tag>` | Remove one project |
| `global path` | Print where the global graph file lives |
| `merge-driver <base> <current> <other>` | Used by git to merge `graph.json` conflicts. You do not run it by hand; `hook install` sets it up. |

---

## 5. Pictures and exports

| Command | Output |
|---|---|
| `export html` | Interactive `graph.html` |
| `export callflow-html` | Mermaid architecture and call-flow page |
| `export obsidian` | Obsidian vault notes + canvas (`--dir PATH`) |
| `export wiki` | Markdown articles, one per community, plus an index |
| `export svg` | `graph.svg` |
| `export graphml` | GraphML file (Gephi, yEd). **[CHANGED]** Fixed for multigraphs (our graphs have several edges between two nodes). |
| `export neo4j` / `export falkordb` | Cypher text, or push to the database with `--push URI` (use the `NEO4J_PASSWORD` / `FALKORDB_PASSWORD` env var for the password) |
| `tree` | D3 collapsible-tree HTML (`--output`, `--max-children`) |

All exports accept `--graph PATH`.

---

## 6. Git hooks (keep the graph fresh automatically)

| Command | Plain meaning |
|---|---|
| `hook install` | After every commit and branch switch, the graph is rebuilt from the changed code |
| `hook status` | Shows whether the hooks are installed |
| `hook uninstall` | Removes them |

Set `GRAPHIFY_SKIP_HOOK=1` to skip the hook for one commit.

---

## 7. Connecting an AI assistant (installers)

| Command | What it writes |
|---|---|
| `vscode install` | **Your case (Copilot in VS Code):** skill file at `~/.copilot/skills/graphify/SKILL.md` and `.github/copilot-instructions.md` in the project |
| `copilot install` | Skill for GitHub Copilot CLI (terminal), not VS Code Chat |
| `claude install` | `CLAUDE.md` section and a Claude Code hook |
| `cursor`, `codex`, `gemini`, `aider`, `opencode`, `kilo`, `kiro`, `trae`, `droid`, `claw`, `hermes`, `pi`, `devin`, `antigravity`, `codebuddy` `install` | The matching file or skill for that tool |
| `<tool> uninstall` | Removes what the install wrote |
| `install --platform P` | Generic install for platform P |
| `uninstall` | Removes graphify from all detected tools (`--purge` also deletes `graphify-out/`) |

---

## 8. Memory and feedback (optional)

| Command | Plain meaning |
|---|---|
| `save-result --question Q --answer A --nodes N1 N2 --outcome useful` | Save a Q&A to `graphify-out/memory/`. Outcome is `useful`, `dead_end` or `corrected` (with `--correction TEXT`). |
| `reflect` | Summarise the saved outcomes into `graphify-out/reflections/LESSONS.md` |

---

## 9. Checking and measuring

| Command | Plain meaning |
|---|---|
| `benchmark [graph.json]` | Estimates tokens saved compared with reading the whole corpus. Reads `.graphify_detect.json` for the real word count. Without it, it guesses `nodes × 50 words`. |
| `diagnose multigraph` | Warns when two nodes have several edges that would collapse into one (`--json`, `--max-examples N`) |
| `prs` | Pull-request dashboard: CI state, reviews, worktrees |
| `provider list/show/add/remove` | Manage custom LLM providers for extraction and labelling |
| `--version` | Prints the Graphify version |

---

## 10. Helper commands you normally never type

| Command | Who uses it |
|---|---|
| `hook-guard`, `hook-check` | Run by the AI tool's hook, never by you |
| `cache-check`, `merge-chunks`, `merge-semantic` | Used inside the `/graphify` skill when it merges AI extraction results |

---

## 11. Useful environment variables

| Variable | Effect |
|---|---|
| `GRAPHIFY_FORCE=1` | Same as `--force` for `update` and `extract` |
| `GRAPHIFY_OUT` / `GRAPHIFY_OUT_NAME` | Change the `graphify-out` folder |
| `GRAPHIFY_MAX_GRAPH_BYTES` | Size limit for graph files (default 512 MiB) |
| `GRAPHIFY_QUERY_LOG_ENABLE=1` | Log queries to `~/.cache/graphify-queries.log` |
| `GRAPHIFY_NO_TIPS=1` | Hide the "set an API key" tip |
| `GRAPHIFY_SKIP_HOOK=1` | Skip the git hook once |

---

## 11. [ADDED] Clang + A2L + `.par` extraction (the main addition)

Everything in sections 11 to 16 is added in this fork. Run from PowerShell with `.venv_dev` active.

```powershell
python -m graphify update . --clang --a2l DQ381.a2l --par DQ381.par
```

| Flag | Meaning |
|---|---|
| `--clang` | Run the clang AST pass after tree-sitter. Reads `compile_commands.json` for the compile flags. |
| `--extractor tree-sitter\|clang\|both` | `tree-sitter` = original behaviour (no clang); `both` = default; `clang` = clang replaces tree-sitter symbols of every C file it parsed (tracker §8) |
| `--compile-commands FILE` | Which `compile_commands.json` to use |
| `--libclang PATH` | Path to the libclang library if it is not found automatically |
| `--a2l FILE` | Join the A2L: characteristics and axis points become nodes linked to the code (section 15) |
| `--par FILE` | Import the `.par` values into the databank and write the coverage report (section 14) |
| `--par-name SET` | Name of the parameter dataset (default: file name) |
| `--clang-debug` | Verbose trace to stderr and `graphify-out/clang_debug.log`, plus `clang_report.json` |

| Related command | Meaning |
|---|---|
| `graphify clang-check [path]` | Checks the clang setup (libclang, compile DB) before a long run |
| `graphify clang-check [path] --file F.c` | Parses one file and prints the clang errors |

**Config file** `graphify-clang.json` (same keys as the flags): `extractor`, `debug`, `compile_commands`, `a2l`, `par`, `par_name`, `extra_args`, `register_functions` (default `Bios_RegisterCalibrationData`, `RegisterCalibrationData`). Environment: `GRAPHIFY_EXTRACTOR`, `GRAPHIFY_CLANG_DEBUG=1`, `GRAPHIFY_CLANG_CONFIG`.

| What clang adds to the graph | Relation / node |
|---|---|
| Global variable read / written / both | `reads_var`, `writes_var`, `reads_writes_var` |
| Calibration read through a registered pointer | `calibration_field` node + `reads_calibration_field`, `exposes_calibration_field` |
| Static / global / extern variables | `metadata.storage`, `defined_in`, `declaration_only` (tracker §24) |
| Header declarations, one file node per header, `contains` edges | tracker §13 to §20 |
| A2L characteristics, axis points | `a2l_characteristic`, `a2l_axis` nodes; links `mapped_to_a2l_characteristic`, `mapped_to_a2l_axis_pts`, axes `x_axis`, `y_axis`, ... |

**Registration (why a calibration is seen at all):** pass 1 finds calls like `Bios_RegisterCalibrationData(&Ptr, &Rom, ...)` and initialised ROM variables, which gives `pointer -> ROM struct`. Pass 2 only creates calibration nodes for accesses through a registered pointer. A pointer that is not registered (a function parameter, a local copy) gives no node.

---

## 12. [ADDED] Module and function slices: `graphify focus`

```powershell
python -m graphify focus ku/kku --flow calibration
python -m graphify focus --function Kku_Main --depth 2
```

| Option | Meaning |
|---|---|
| `"<path substring>"` | The module: nodes whose `source_file` contains the text |
| `--function "<name>"` | One function (exact name, `path::name` or node id) instead of a module |
| `--flow calls,data,calibration\|all` | What counts as a hop. `all` = legacy N-hop expansion over every relation |
| `--direction in\|out\|both` | Upstream (callers, writers), downstream (callees, readers), or both |
| `--depth N`, `--fanout N` | Hops (default 1); more than N neighbours of one expansion become a "... N more" stub (default 30) |
| `--exclude P`, `--match path\|label\|both`, `--no-includes` | Filters |
| `--config focus.json`, `--graph PATH`, `--out DIR` | Config file, input graph, output folder |

Output: `graph.json`, `.graphify_labels.json`, `focus_report.json` (view with `graphify export html --graph ...`). Colours: inside, border, shared, include, attached, stub (tracker §23, §26).

---

## 13. [ADDED] Calibration mapping rules (code <-> A2L)

| Rule | Meaning |
|---|---|
| One code node per variable path | `Rom.var_ko`, no indices. `[i]` and `[0][1]` are stored as text (`index`) on the read edge, a constant bit mask as `mask`. |
| Join by plain name | The A2L SYMBOL_LINK `Rom.var_ko._12_` is read as plain name `Rom.var_ko`, index `[12]`; a BIT_MASK adds the mask (`#1024` = 0x400). |
| One node, many A2L entries | Every A2L entry with that plain name is linked. The link carries `characteristic` (the A2L name as text), `index`, `mask`. |
| Exact match first | Then the other rules below, only for entries not yet matched. |
| **Prefix match** | The A2L symbol is longer than the code path (`Rom.var.an` while the code reads `Rom.var`): linked to the longest code path that is a prefix. Link has `via: prefix`. (tracker §36) |
| **Struct expansion** | The code reads a whole struct (`f(Ptr->Feh)`, `&Ptr->Feh`): the header type is walked (nested structs, arrays of structs, bit-fields, anonymous unions; depth 10, 500 members) and each leaf gets a node. Read edges are `INFERRED`, `via: struct_type`. (§37) |
| **Union members** | The code reads `P.Gesamt`, the A2L has `P.Bits.Gang_07`: sibling union member is linked; if the code mask is constant only the A2L entry with the same mask. Link has `via: union`. (§40) |
| Nested brackets | `Ptr->feh_ko[Tab[i].s.FID]` is one index, not part of the path (§39) |
| No struct in the symbol | A symbol with only the variable label is not calibratable but measurable; linked by plain name, note "no struct (plain variable, measurable)" |
| Confidence | Direct reads are `EXTRACTED`. Only expansions from a type are `INFERRED`. |
| Every A2L entry is a node | Matched or not; `metadata.code_matched` says whether a code node points at it. |

---

## 14. [ADDED] Parameter values: `.par` databank

| Command | Meaning |
|---|---|
| `graphify update . --clang --a2l X --par Y.par [--par-name SET]` | Import during extraction; writes `graphify-out/params.db` and the coverage report |
| `graphify params import FILE.par --name SET [--a2l FILE] [--project P --variant V --version N] [--db PATH]` | Import one dataset on its own |
| `graphify params list` | List the datasets |
| `graphify params show NAME [--dataset SET]` | Show one parameter: scalar, enum (raw + text), curve or map as a table |

- The `.par` format that is understood: `Name [TYPE] raw ; physical`, continuation lines, `[INT(16),(r,c)]` shapes, bit entries `Parent.bitN`, struct bit members, names containing `[x]` (tracker §27, §28).
- `explain F --inputs --dataset SET` prints the value under each calibration.
- Not built yet: `params diff` (compare two datasets).

---

## 15. [ADDED] Coverage report `param_coverage.csv`

Written to `graphify-out/` (`;`-separated, opens in Excel) together with `param_coverage.json` and `params_import_<SET>.json`. One row per name found in A2L, `.par` or code.

| Column | Meaning |
|---|---|
| name, kind | A2L name, `CHAR` or `AXIS` |
| a2l / par / code | Found there (yes/no). CODE = a code node is linked to this A2L name (read from the edges, so large structs are complete). |
| status | `ok`, `no value in PAR`, `not found in code`, `A2L only`, `not in A2L`, `PAR only`, `code only` |
| code_nodes | Where in the code, with tags `[prefix match: ...]`, `[union match: ...]` |
| note | `A2L symbol: ...`; "ROM struct 'X' is in the code, but no code access to this member was found"; "no struct (plain variable, measurable)"; "looks the same (case/index differs) - join missed it"; "A2L has 'X' (spelling differs)" |

**If a row says "no code access found":** the pointer is not registered, the registered ROM name differs from the A2L root, the file was not parsed, or the code path is longer than the A2L path. Use `--clang-debug` and look at the "info calibration pointer" lines.

---

## 16. [ADDED] Files written to `graphify-out/`

| File | When |
|---|---|
| `graph.json` | always (original) |
| `syntax_errors.txt` | tree-sitter could not parse files |
| `clang_debug.log`, `clang_report.json` | `--clang-debug` |
| `params.db` | `--par` |
| `param_coverage.csv`, `param_coverage.json`, `params_import_<SET>.json` | `--par` (coverage needs `--a2l` too) |
| `focus_report.json` | `graphify focus` |

---

## 17. [ADDED] Function descriptions from code comments

| Item | Detail |
|---|---|
| Source | The header comment directly above the function (`/** ... */` or `///`). Plain `//` and `/* */` comments are only read if `-fparse-all-comments` is added to `extra_args` in `graphify-clang.json`. |
| Understood layout | `Funktion:`, `Parameter:` (name, then indented text), `Rueckgabe:` (German) and the English words Function / Parameter / Return |
| Stored on the function node | `summary`, `comment`, `comment_params`, `comment_returns`, `comment_source: code` |
| Where it is shown | `explain F` (Summary, Param doc, Return doc); `explain F --inputs` (summary of F, and one line under each writer) |

| Raw doxygen text **[ADDED]** | `metadata.doxygen` on function, variable and calibration nodes: the doxygen comment exactly as written (`/** */`, `///`, `///<`, ...). Missing comment = no key. Plain `//` and `/* */` are not doxygen and stay missing. For a calibration node it is the comment of the struct member (also for the members of an expanded struct). |
