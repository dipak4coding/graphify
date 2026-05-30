# Graphify Usage Guide — CLI, Integrations & Outputs

Tags: #usage #cli #setup #integrations
Links: [[00_INDEX]] | [[02_pipeline_overview]] | [[04_extract_ast]] | [[05_extract_semantic]] | [[12_export]]

---

## Two Ways to Run Graphify

### Way 1 — Installed as a Package

```powershell
pip install graphify
graphify update .
```

Runs from **anywhere**. The `graphify` command is available globally in your terminal after install.

### Way 2 — From Local Repo (dev mode)

If you have the graphify source checked out and want to modify it:

```powershell
# inside the graphify repo folder — run once
pip install -e .

# then run from your source code folder
cd C:\your\source\code
graphify update .
```

Or without installing at all (useful for quick tests):

```powershell
# from inside the graphify repo, point to your source code
python -m graphify update C:\your\source\code
```

**Your specific setup (local repo):**

```powershell
# once
cd C:\path\to\graphify\repo
pip install -e .

# then always run from your source code folder
cd C:\001_Fahrzeug\...\03_SwFunktion
graphify update .
```

---

## CLI — No LLM / No API Key Needed

These commands work purely from the AST — no API key required. Start here.

```powershell
# go to your source code folder first
cd C:\your\source\code

# extract AST only — no API key needed
python -m graphify update .

# shortest path between two functions
python -m graphify path "kusmain" "krm_setnangradsollreal_alt"

# explain a node and its neighbors
python -m graphify explain "kusmain"

# watch folder and auto-rebuild on code changes
python -m graphify watch .

# generate collapsible tree HTML
python -m graphify tree

# rerun clustering on existing graph.json (no re-extraction)
python -m graphify cluster-only .
```

---

## CLI — With LLM (API Key Required)

These commands add semantic understanding on top of the AST. Set your key first:

```powershell
$env:ANTHROPIC_API_KEY = "sk-..."     # Claude
$env:OPENAI_API_KEY    = "sk-..."     # OpenAI
$env:GEMINI_API_KEY    = "..."        # Gemini
$env:MOONSHOT_API_KEY  = "..."        # Kimi
```

Then run full extraction (AST + semantic LLM):

```powershell
python -m graphify extract . --backend claude
python -m graphify extract . --backend openai
python -m graphify extract . --backend gemini
python -m graphify extract . --backend kimi

# override default model
python -m graphify extract . --backend claude --model claude-3-5-sonnet

# custom output directory
python -m graphify extract . --backend gemini --out C:\output\dir

# skip clustering step
python -m graphify extract . --backend openai --no-cluster
```

**When to use `update` vs `extract`:**

| Command | What it does | API key? |
|---------|-------------|----------|
| `update` | AST extraction only — fast, deterministic | No |
| `extract` | AST + LLM semantic edges — slower, richer | Yes |

For C/C++ code, `update` alone gives excellent results because the AST covers all call edges. Use `extract` when you also want LLM-inferred semantic relationships (e.g. "this function *validates* that struct").

---

## IDE and Agent Integrations

### Claude Code

```powershell
# install skill once
python -m graphify claude install

# use inside Claude Code
/graphify
```

After installing, type `/graphify` in any Claude Code session to get an AI-powered analysis of your codebase graph.

### GitHub Copilot — VS Code

```powershell
# install once
python -m graphify vscode install
# then use Copilot Chat inside VS Code normally
```

### GitHub Copilot — CLI

```powershell
python -m graphify copilot install
```

### Cursor

```powershell
python -m graphify cursor install
```

### Gemini CLI

```powershell
python -m graphify gemini install
```

### Ollama (local LLM — no API key, no internet)

```powershell
python -m graphify extract . --backend ollama
```

---

## Multi-Repo / Graph Merging

```powershell
# merge two graph files into one
python -m graphify merge-graphs g1.json g2.json

# custom output path
python -m graphify merge-graphs g1.json g2.json --out merged.json

# add a project to the global graph
python -m graphify global add graphify-out/graph.json --as my-project

# list all projects in global graph
python -m graphify global list

# remove a project from global graph
python -m graphify global remove my-project

# clone a GitHub repo and extract
python -m graphify clone https://github.com/owner/repo
```

---

## Output — What Gets Created

After running `update` or `extract`:

```
your-source-folder/
└── graphify-out/
    ├── graph.json       ← full graph data (nodes + edges)
    ├── graph.html       ← interactive visualization (open in browser)
    └── report.md        ← cluster summary
```

Open `graph.html` in any browser — no server needed, no internet required.

See [[12_export]] for all output formats (HTML, JSON, Obsidian, Neo4j, SVG, GraphML).

---

## Confidence Levels in graph.json

Every `calls` edge has a `confidence` field:

| Value | Meaning | Source | Reliability |
|-------|---------|--------|-------------|
| `EXTRACTED` | Proven by AST — exact match | `update` command | High — trust this |
| `INFERRED` | Guessed by LLM — check direction | `extract` command | Medium — verify caller/callee |
| `AMBIGUOUS` | Uncertain — low confidence | `extract` command | Low — treat as hint |

**Important for C/C++ code:** If you see call edges with wrong direction (caller and callee swapped), check if `confidence` is `INFERRED`. This happens when the callee is defined in a different file and the LLM had to guess. The AST-only `update` command avoids this problem entirely — it only emits `EXTRACTED` edges.

As of the cross-file fix in `extract.py`, calls between C files are now resolved via a global AST pass (not LLM guessing). This promotes many previous INFERRED edges to EXTRACTED. See [[04_extract_ast]] for details.

---

## Quick Reference Card

| Goal | Command |
|------|---------|
| AST only, no API key | `python -m graphify update .` |
| Full extract with Claude | `python -m graphify extract . --backend claude` |
| Full extract with OpenAI | `python -m graphify extract . --backend openai` |
| Full extract with Gemini | `python -m graphify extract . --backend gemini` |
| View graph HTML | open `graphify-out/graph.html` in browser |
| Shortest path between nodes | `python -m graphify path "A" "B"` |
| Explain a node | `python -m graphify explain "X"` |
| Auto-rebuild on file save | `python -m graphify watch .` |
| Generate tree view | `python -m graphify tree` |
| Rerun clustering only | `python -m graphify cluster-only .` |
| Install for Claude Code | `python -m graphify claude install` |
| Install for VS Code Copilot | `python -m graphify vscode install` |
| Install for Cursor | `python -m graphify cursor install` |
| Merge two graphs | `python -m graphify merge-graphs g1.json g2.json` |
| Add to global graph | `python -m graphify global add graph.json --as tag` |

---

*Back to: [[00_INDEX]]*
