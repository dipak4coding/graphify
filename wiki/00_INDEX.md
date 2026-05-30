# Graphify — Developer Knowledge Wiki

> **How to use this wiki:** Start here. Read top-to-bottom for your first pass. After that, jump directly to any article using the links below. Every article has [[wikilinks]] pointing to related articles — click them to navigate like a real Obsidian vault.

---

## What is Graphify? (10-second answer)

Graphify turns any folder of code, papers, notes, and images into a **knowledge graph** — a network of nodes (concepts, classes, functions, papers) connected by edges (imports, calls, cites, references). That graph is then analysed, clustered into communities, and exported as an interactive HTML page, a JSON file, and a human-readable audit report.

The end goal: an AI coding assistant (Claude, Copilot, etc.) can **navigate your codebase like a map** instead of reading every file from scratch every time.

---

## Articles (read in this order for a first pass)

### Layer 0 — Concepts
- [[01_what_is_a_graph]] — Nodes, edges, and why a graph beats a file list
- [[02_pipeline_overview]] — The 9-step pipeline from folder to graph

### Layer 1 — Reading the Codebase
- [[03_detect]] — How graphify discovers files and classifies them
- [[04_extract_ast]] — How code structure is read with tree-sitter (no AI needed)
- [[05_extract_semantic]] — How docs and papers are read by Claude subagents
- [[06_cache]] — How graphify avoids re-reading files that haven't changed

### Layer 2 — Building the Graph
- [[07_nodes_and_edges_deep_dive]] — Every node type, every edge type, explained line by line
- [[08_build]] — How raw node/edge dicts become a NetworkX graph

### Layer 3 — Analysing the Graph
- [[09_cluster]] — Community detection with Leiden and Louvain
- [[10_analyze]] — God nodes, surprising connections, suggested questions

### Layer 4 — Outputs
- [[11_report]] — Generating GRAPH_REPORT.md
- [[12_export]] — HTML, JSON, Obsidian vault, Neo4j, SVG, GraphML
- [[13_wiki_generation]] — The built-in wiki generator (wiki.py)

### Layer 5 — Lifecycle & Integration
- [[14_ingest]] — Fetching URLs into the corpus
- [[15_cache_lifecycle]] — Full cache lifecycle and invalidation
- [[16_watch]] — File watching and auto-rebuild
- [[17_incremental_update]] — --update mode and manifest diffing
- [[18_mcp_server]] — Serving the graph to other AI agents via MCP

### Layer 6 — Extending Graphify
- [[19_adding_a_language]] — How to add a new programming language
- [[20_how_to_modify_graphify]] — Your complete modification guide

### Layer 7 — Automotive Embedded C
- [[21_c_signal_flow]] — Signal flow graph for automotive C codebases (CAN signals, #ifdef dead-code elimination, forward/backward trace)

### Layer 8 — Reference
- [[22_usage_guide]] — Complete CLI reference, IDE integrations, confidence levels, setup for local repo

---

## Quick Reference — File → Module Map

| File | What it does |
|------|-------------|
| `detect.py` | Discovers files, classifies them, checks .graphifyignore |
| `extract.py` | AST extraction via tree-sitter for 20+ languages |
| `cache.py` | SHA256-based per-file cache for extraction results |
| `build.py` | Assembles node/edge dicts into a NetworkX graph |
| `cluster.py` | Leiden/Louvain community detection |
| `analyze.py` | God nodes, surprising connections, suggested questions |
| `report.py` | Generates GRAPH_REPORT.md |
| `export.py` | HTML, JSON, Obsidian, Neo4j, SVG, GraphML outputs |
| `wiki.py` | Generates agent-crawlable wiki (index + community articles) |
| `ingest.py` | Fetches URLs (tweets, arXiv, PDFs, pages) into /raw |
| `serve.py` | MCP stdio server for agent access to the graph |
| `watch.py` | File watcher — auto-rebuilds on changes |
| `hooks.py` | Git post-commit hooks |
| `security.py` | URL validation, safe fetch, input sanitisation |
| `__main__.py` | CLI entry point (`graphify install`, `query`, etc.) |
| `skill.md` | Instructions Claude reads when you type `/graphify` |
| `extract_c_signals.py` | Automotive C signal flow extractor — ports ScanDKSW preprocessor to Python |

---

*Generated for the graphify codebase. Navigate with [[wikilinks]] or the article list above.*
