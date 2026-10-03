# Graphify commands: guide for YOU (simple version)

Source: `graphify --help` (`__main__.py`) and the code in `cli.py` (fork `dipak4coding/graphify`, branch `current-v8`).
Run any command in the VS Code terminal. Replace `merged.json` with your own graph file.

**One rule to remember:** almost every command reads `graphify-out/graph.json`. Use `--graph <file>` to point it at another file (for example your clang + A2L merged graph).

---

## 1. The 6 commands you will use most

| What you want | Command | What you get |
|---|---|---|
| Ask a question | `graphify query "who writes Kku_xyz" --graph merged.json` | A small subgraph: NODE lines and EDGE lines. This is what the AI also receives. |
| Find the route between two things | `graphify path "FuncA" "Kku_xyz" --graph merged.json` | Shortest chain of edges between A and B |
| Explain one thing | `graphify explain "FuncA" --graph merged.json` | The node and its neighbours in plain words |
| What breaks if I change X | `graphify affected "FuncA" --graph merged.json` | Everything that calls or uses X, found by walking the edges backwards |
| Biggest hubs | `graphify god-nodes --top 10 --graph merged.json` | The 10 most connected nodes |
| Refresh the graph after editing code | `graphify update .` | Re-reads the code files only. No AI and no API key needed. |

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
| `affected "X"` | `--relation R` | Which edge kinds to walk backwards (repeatable). Default: calls, indirect_call, references, imports, imports_from and a few more. |
| | `--depth N` | How many steps back (default 2) |
| `god-nodes` | `--top N`, `--json` | How many to show; JSON instead of text |

**Tips**
- Use exact names (for example the `Kku_` name). Plain English words do not match, because only labels and ids are searched.
- `affected` only follows the relations in its default list. Your own edges (`reads_var`, `writes_var`) are not in that list, so pass them with `--relation reads_var --relation writes_var`.

---

## 3. Building and updating the graph

| Command | Needs an AI key? | Plain meaning |
|---|---|---|
| `update [path]` | No | Re-extract code files with tree-sitter and rebuild the graph. Use after you change code. |
| | | `--force`: overwrite even if the new graph has fewer nodes (use after deleting code). Same as `GRAPHIFY_FORCE=1`. |
| | | `--no-cluster`: skip grouping into communities |
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

**For your clang pipeline:** `graphify update .` replaces only the tree-sitter (`ast`) items. Your clang and A2L items survive if they are not stamped `ast`. After `update`, run your `merge_into_graphify.py` step again to be safe.

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
| `export graphml` | GraphML file (Gephi, yEd) |
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
