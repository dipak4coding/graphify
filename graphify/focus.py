"""`graphify focus <pattern>`: cut a module-sized subgraph out of the big graph and report how connected it is."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _norm(s) -> str:
    return str(s or "").replace("\\", "/").lower()


def run_focus(argv: list[str]) -> int:
    if not argv:
        print('Usage: graphify focus "<path or label substring>" [--depth N] [--max-degree N] '
              '[--graph PATH] [--out DIR]\n  e.g. graphify focus ku/kku --depth 1', file=sys.stderr)
        return 1
    pattern = _norm(argv[0])
    depth, max_degree, graph, out = 1, 100, None, None
    rest = argv[1:]
    for i, a in enumerate(rest):
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        if a == "--depth" and nxt:
            depth = int(nxt)
        elif a == "--max-degree" and nxt:
            max_degree = int(nxt)
        elif a == "--graph" and nxt:
            graph = nxt
        elif a == "--out" and nxt:
            out = nxt
    from graphify.paths import out_path
    gp = Path(graph) if graph else Path(out_path()) / "graph.json"
    if not gp.is_file():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    raw = json.loads(gp.read_text(encoding="utf-8"))
    edges = raw.get("links", raw.get("edges", []))
    nodes = {n["id"]: n for n in raw.get("nodes", [])}

    seeds = {nid for nid, n in nodes.items()
             if pattern in _norm(n.get("source_file")) or pattern in _norm(n.get("label"))}
    if not seeds:
        print(f"No node with source_file/label containing '{pattern}'.")
        return 0

    adj: dict = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    keep, frontier = set(seeds), set(seeds)
    for _ in range(depth):
        nxt: set = set()
        for nid in frontier:
            if nid not in seeds and len(adj[nid]) > max_degree:
                continue  # hub (e.g. a types header): shown, not expanded
            nxt |= adj[nid] - keep
        keep |= nxt
        frontier = nxt

    sub_edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    slug = re.sub(r"[^a-z0-9]+", "_", pattern).strip("_") or "focus"
    out_dir = Path(out) if out else gp.parent / "focus" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    sub = {k: v for k, v in raw.items() if k not in ("nodes", "links", "edges")}
    sub["nodes"] = [nodes[i] for i in keep if i in nodes]
    sub["links"] = sub_edges
    (out_dir / "graph.json").write_text(json.dumps(sub), encoding="utf-8")

    seed_edges = [e for e in sub_edges if e["source"] in seeds and e["target"] in seeds]
    to_outside = [e for e in sub_edges if (e["source"] in seeds) != (e["target"] in seeds)]
    file_seeds = [i for i in seeds if _norm(nodes[i].get("label")) == _norm(nodes[i].get("source_file")).rsplit("/", 1)[-1]]
    has_imp = {e["source"] for e in edges if e.get("relation") == "imports"} | \
              {e["target"] for e in edges if e.get("relation") == "imports"}
    no_imp = sorted(nodes[i].get("source_file") or i for i in file_seeds if i not in has_imp)
    ext_nodes = [i for i in keep if str(i).startswith("ext_")]

    print(f"focus '{pattern}': {len(seeds)} matching node(s) ({len(file_seeds)} file nodes), "
          f"{len(keep)} nodes / {len(sub_edges)} edges within {depth} hop(s), hubs (> {max_degree} links) not expanded")
    print(f"  edges inside the module: {len(seed_edges)} | module <-> outside: {len(to_outside)}")
    print("  inside-module relations: " + ", ".join(f"{k}={v}" for k, v in
                                                   Counter(e.get('relation') for e in seed_edges).most_common()))
    print("  module -> outside relations: " + ", ".join(f"{k}={v}" for k, v in
                                                       Counter(e.get('relation') for e in to_outside).most_common()))
    print(f"  placeholder ext_* nodes: {len(ext_nodes)} | file nodes with NO imports edge: {len(no_imp)}")
    for sf in no_imp[:15]:
        print(f"    no imports: {sf}")
    by_label: dict = defaultdict(list)
    for i in keep:
        n = nodes.get(i)
        if n and n.get("type") in ("function", "variable"):
            by_label[n.get("label")].append(i)
    dups = {k: v for k, v in by_label.items() if len(v) > 1}
    print(f"  symbols with the same label more than once: {len(dups)}")
    for k, v in list(dups.items())[:8]:
        print(f"    {k}: " + " | ".join(f"{i} ({nodes[i].get('source_file')})" for i in v))
    print(f"  wrote {out_dir / 'graph.json'}  ->  graphify export html --graph \"{out_dir / 'graph.json'}\"")
    return 0
