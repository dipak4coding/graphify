# Graphify commands: guide for the AI assistant (Copilot, Claude, others)

Purpose: tell the AI which Graphify command to run for which question, so it reads a small answer instead of whole files.
Works the same for any assistant that can run terminal commands.

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
| "What is impacted if I change X" | `graphify affected "X" --depth 2 --graph <graph>` | Add `--relation reads_var --relation writes_var` for variable impact in the clang graph |
| "Main building blocks" | `graphify god-nodes --top 10 --graph <graph>` | Most connected nodes |
| Only call edges wanted | `graphify query "X" --context call --graph <graph>` | Filter by edge context. Known values: call, import, field, parameter_type, return_type, generic_arg |
| Larger or smaller answer | `--budget N` | Default 2000 tokens |

---

## 3. How to read the output

| Line | Meaning |
|---|---|
| `NODE <label> [src=<file> loc=<Lnn> community=<name>]` | A function, variable or file. `loc` is the line number to open. |
| `EDGE a --<relation> [<confidence> context=<kind>]--> b at=<file>:<line>` | A link from a to b. `context=` appears only if the edge has one. `at=` is the place in the source where it happens. |
| `EXTRACTED` | Found directly in code. Trust it. |
| `INFERRED` | Guessed by a tool or AI. Check it in the source. |
| `AMBIGUOUS` | Uncertain, for example loop-index expansion. Always check in the source. |
| `TRUNCATED` | Output hit the token budget |

NODE lines do not print the node kind. In the clang graph, the relation names tell what a node is: `reads_var`, `writes_var`, `reads_calibration_field`, `exposes_calibration_field`.

---

## 4. Keeping the graph current

| Situation | Command |
|---|---|
| You changed code files | `graphify update .` (no AI key needed) |
| You deleted code and the node count dropped | `graphify update . --force` |
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
| Run `hook-guard`, `hook-check`, `cache-check`, `merge-chunks`, `merge-semantic` | They are internal helpers |
| Ask the user for an API key for code-only graphs | Code extraction needs none |
