# Graphify commands: guide for the AI assistant (Copilot, Claude, others)

Purpose: tell the AI which Graphify command to run for which question, so it reads a small answer instead of whole files.
Works the same for any assistant that can run terminal commands.

**Tags (updated 2026-10-23):** no tag = original Graphify; **[ADDED]** = added in this fork (clang AST, A2L, `.par` values, focus, inputs); **[CHANGED]** = original feature that we extended. Background and dates: `graphify-clang-integration-tracker.md` (§N).

---

## 1. Golden rules

| Rule | Why |
|---|---|
| If `graphify-out/graph.json` exists, run a Graphify command **before** reading source files | The answer is a few lines, not whole files |
| Use exact identifiers (function or `Kku_` names) in the command | Search matches labels and ids, not descriptions |
| Open source only at the `file:line` shown in an `at=` or `loc` field | Reads only the needed snippet |
| If output ends with `TRUNCATED`, narrow the question or raise `--budget` | Part of the answer was cut |
| If the graph is missing or stale, say so, then use `graphify update .` | Stale graphs give wrong answers |

---

## 2. Which command for which question

| Question type | Command | Notes |
|---|---|---|
| "How does X work" / "where is X" | `graphify query "X" --graph <graph>` | Breadth-first, depth 2 |
| "Trace the data flow from X" | `graphify query "X" --dfs --graph <graph>` | Follows one chain deeper |
| "How are A and B connected" | `graphify path "A" "B" --graph <graph>` | Shortest chain |
| "What is X" | `graphify explain "X" --graph <graph>` | Node plus neighbours |
| "What is impacted if I change X" **[CHANGED]** | `graphify affected "X" --depth 2 --graph <graph>` | The default now includes `reads_var`, `writes_var`, `reads_calibration_field` and related relations. `--relation R` restricts it. |
| "Main building blocks" | `graphify god-nodes --top 10 --graph <graph>` | Most connected nodes |
| **[ADDED]** "What feeds function F" / "which inputs and values does F use" | `graphify explain "F" --inputs --graph <graph>` (add `--dataset SET` for parameter values, `--depth N`) | Writers of every input variable with `file:line`, plus calibrations |
| **[ADDED]** "Show me one module / one function and its neighbours" | `graphify focus "<path part>" --flow data,calls,calibration` or `graphify focus --function F --depth 2` | Writes a small `graph.json` slice; query that file with `--graph` |
| **[ADDED]** "Which A2L characteristic is this code variable?" | `graphify explain "<Rom.var_ko>" --graph <graph>` | See `a2l_*` metadata and `mapped_to_a2l_*` edges (section 9) |
| **[ADDED]** "What is the value of parameter P" | `graphify params show P --dataset SET` | From the `.par` databank (needs `--par` at extraction) |
| Only call edges wanted | `graphify query "X" --context call --graph <graph>` | Filter by edge context. Known values: call, import, field, parameter_type, return_type, generic_arg |
| Larger or smaller answer | `--budget N` | Default 2000 tokens |

---

## 3. How to read the output

| Line | Meaning |
|---|---|
| `NODE <label> [src=<file> loc=<Lnn> community=<name>]` | A function, variable or file. `loc` is the line number to open. **[CHANGED]** may also show `kind=` (variable, calibration_field, a2l_characteristic, a2l_axis), `range=`, `storage`, `defined_in`. |
| `EDGE a --<relation> [<confidence> context=<kind>]--> b at=<file>:<line>` | A link from a to b. `context=` appears only if the edge has one. `at=` is the place in the source where it happens. **[CHANGED]** may also show ` index=...` (index text such as `[0][1]` or `[i]`) and ` mask=...` (bit mask). |
| `EXTRACTED` | Found directly in code or A2L. Trust it. **[CHANGED]** Calibration reads with a run-time index are also `EXTRACTED`; the index text is on the edge. |
| `INFERRED` | Guessed by a tool or AI. Check it in the source. **[ADDED]** For clang edges: members of a struct read as a whole (`via: struct_type`). |
| `AMBIGUOUS` | Uncertain. **[CHANGED]** The clang pass no longer uses it for loop indices. |
| `TRUNCATED` | Output hit the token budget |

**[ADDED]** Relations of the clang and A2L graph:

| Relation | Meaning |
|---|---|
| `reads_var`, `writes_var`, `reads_writes_var` | Function reads / writes / does both on a global variable |
| `reads_calibration_field` | Function reads a calibration (through a registered pointer); `index`, `mask` in the edge |
| `exposes_calibration_field` | The pointer variable exposes that calibration |
| `mapped_to_a2l_characteristic`, `mapped_to_a2l_axis_pts` | Code calibration -> A2L entry; metadata `characteristic`/`axis` (A2L name), `index`, `mask`, `via` (`prefix`, `union`), `suffix` |
| `x_axis`, `y_axis`, `z_axis`, `input_x/y/z`, `length_x/y/z`, `*_axis_reference` | A2L characteristic -> its axis points / input quantities |

---

## 4. Keeping the graph current

| Situation | Command |
|---|---|
| You changed code files | `graphify update .` (no AI key needed) |
| You deleted code and the node count dropped | `graphify update . --force` |
| **[ADDED]** You need the clang, A2L and parameter data (full run) | `python -m graphify update . --clang --a2l X.a2l --par Y.par` (details: section 9) |
| **[ADDED]** Only one module changed or is of interest | `python -m graphify update <subfolder> --clang --a2l X.a2l --par Y.par`. Back up `graphify-out` first. Coverage rows outside the folder read "not found in code". |
| Docs, papers or images changed | `/graphify --update` in the chat (needs an LLM) |
| Rebuild the grouping only | `graphify cluster-only .` |

After editing code, the project rule is to run `graphify update .`.

---

## 5. Chat commands (the `/graphify` skill)

Type these in the chat panel. They come from the installed skill file.

| Chat command | Meaning |
|---|---|
| `/graphify` | Build the graph for the current folder |
| `/graphify <path>` | Build it for another folder |
| `/graphify <path> --update` | Re-extract only new or changed files |
| `/graphify <path> --mode deep` | More thorough extraction with more INFERRED edges |
| `/graphify <path> --directed` | Keep edge direction (source to target) |
| `/graphify <path> --no-viz` | Skip the HTML picture |
| `/graphify <path> --wiki` | Write an index plus one article per community |
| `/graphify <path> --watch` | Rebuild automatically on code changes |
| `/graphify <path> --mcp` | Start the MCP server for agent access |
| `/graphify query "<q>"` | Same as `graphify query` |
| `/graphify path "A" "B"` | Same as `graphify path` |
| `/graphify explain "X"` | Same as `graphify explain` |
| `/graphify add <url>` | Fetch a URL into `./raw` and update the graph |

If a graph already exists and the user asks a plain question, the skill goes straight to `graphify query`. It does not rebuild.

---

## 6. MCP server tools (only if the MCP server is configured)

Start it with `python -m graphify.serve <graph.json>`. Names below are from `serve.py`.

| Tool | Meaning |
|---|---|
| `query_graph` | Same engine as `graphify query`. Default depth 3 (max 6), unlike the CLI's 2. |
| `get_node` | Label, ID, source, type, community, degree of one node. It does not print metadata. |
| `get_neighbors` | Direct neighbours of a node |
| `get_community` | All members of one community |
| `god_nodes` | Most connected nodes |
| `graph_stats` | Counts of nodes, edges, communities |
| `shortest_path` | Same as `graphify path` |
| `list_prs`, `get_pr_impact`, `triage_prs` | Pull-request tools |

For GitHub Copilot in VS Code, the installer only sets up the CLI route (`vscode install`). MCP is optional and its VS Code config has not been verified here.

---

## 7. Saving what worked (optional)

| Command | When |
|---|---|
| `graphify save-result --question "<q>" --answer "<a>" --nodes N1 N2 --outcome useful` | After a good answer. Other outcomes: `dead_end`, `corrected` plus `--correction "<text>"`. |
| `graphify reflect` | Later, to turn the saved outcomes into `graphify-out/reflections/LESSONS.md` |

---

## 8. Do not do

| Do not | Instead |
|---|---|
| Run `extract` or `label` without being asked | They can need an LLM key and take time |
| Run `uninstall --purge` | It deletes `graphify-out/` |
| **[ADDED]** Run a full `--clang` extraction on a big repo without being asked | It takes long (tens of thousands of files) |
| **[ADDED]** Treat `INFERRED` struct members or `via: union` / `via: prefix` links as certain | Check the C line and the A2L symbol first |
| Run `hook-guard`, `hook-check`, `cache-check`, `merge-chunks`, `merge-semantic` | They are internal helpers |
| Ask the user for an API key for code-only graphs | Code extraction needs none |

---

## 9. [ADDED] Answering calibration and A2L questions

**Facts to rely on**

| Fact | Detail |
|---|---|
| Code node | One `calibration_field` per variable path without indices, e.g. `Rom.var_ko`. Metadata `calibration_path`, `accessed_via` (pointer name). |
| Code -> A2L | Join on the plain name. A node can link to many A2L entries (one per index and bit). The A2L name is in the link (`characteristic`) and in node metadata `a2l_members` (first 200 only), `a2l_member_count`, `a2l_characteristic_name` (only if exactly one). |
| Index and mask | On the read edge (`index`, `mask`) and on the A2L link. The A2L symbol `Rom.var_ko._12_` has plain name `Rom.var_ko`, index `[12]`. `#1024` after a symbol is the bit mask (0x400). |
| Unmatched A2L entries | They are nodes too (`code_matched` false). Meaning: no code read was found for them. That can be genuine (unused ROM member) or a join gap. |
| Not calibratable | A symbol without a ROM struct part is measurable only; it is still linked by plain name. |

**Why a calibration may be missing in the graph**

| Reason | How to check |
|---|---|
| The pointer is not registered (function parameter, local copy) | `--clang-debug`, "info calibration pointer" lines; no node with `accessed_via` of that pointer |
| The registered ROM variable has another name than the A2L root | Node exists under another root |
| The file was not parsed | `clang_report.json` errors; `compile_commands.json` |
| The code path is longer than the A2L path (`P.var.flag` vs A2L `P.var`) | Prefix match works only the other way round |

**Coverage report** `graphify-out/param_coverage.csv`: one row per name in A2L, `.par` or code, with `a2l`, `par`, `code`, `status` and a `note`. Read the `note` before concluding a parameter is unused. To check one parameter, look at its row, then at its code nodes.

**Rules for answers**
- Quote the A2L characteristic name (text) and the C `file:line`, not only node ids.
- Say when a link comes from a type or a naming rule (`INFERRED`, `via: struct_type`, `via: union`, `via: prefix`).
- Values come from `graphify params show` or `explain --inputs --dataset`; do not guess them from the A2L limits.
