"""`graphify focus <pattern>`: cut a module slice out of the full graph.

A slice is a *view* of an already-extracted graph (nothing is re-parsed):

* **inside**   nodes whose ``source_file`` matches the module pattern(s)
* **border**   outside functions linked to the module by the chosen flow(s)
* **shared**   variables / calibration fields that connect inside and border functions
* **include**  files directly included by inside files (``--no-includes`` drops them)
* **stub**     "... N more" placeholder when one expansion would exceed ``--fanout``

Flows and what a *hop* is (arrows in graph.json always run function -> variable):

* ``calls``        caller -> callee
* ``data``         function -> variable -> function. The variable is a pass-through and
                   does NOT count as a hop. upstream = writers of variables a function
                   reads; downstream = readers of variables it writes.
* ``calibration``  calibration fields the inside functions read (+ A2L / axis neighbours)
                   and the other functions reading the same fields
* ``all``          legacy behaviour: plain N-hop expansion over every relation

Direction: ``in`` = upstream chain (callers, writers), ``out`` = downstream chain
(callees, readers), ``both``. A node found while going upstream keeps going upstream.
Border nodes at the last hop are shown but not expanded. ``contains`` / ``imports`` are
never followed outward.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CALL = {"calls", "indirect_call"}
READ = {"reads_var", "reads_writes_var"}
WRITE = {"writes_var", "reads_writes_var"}
CAL_READ = {"reads_calibration_field"}
CAL_ATTACH = {"exposes_calibration_field", "x_axis", "y_axis", "z_axis", "input_x", "input_y", "input_z",
              "x_axis_reference", "y_axis_reference", "axis_reference",
              "mapped_to_a2l_characteristic", "mapped_to_a2l_axis_pts"}
FLOW_ALIASES = {"both": {"calls", "data"}}
ROLE_COMMUNITY = {"inside": 0, "border": 1, "shared": 2, "include": 3, "attached": 4, "stub": 5}
ROLE_LABELS = {0: "inside the module", 1: "border: outside function", 2: "shared variable / calibration",
               3: "directly included file", 4: "calibration / A2L attached", 5: "collapsed (fan-out cap)"}


def _norm(s) -> str:
    return str(s or "").replace("\\", "/").lower()


def _is_file_node(n: dict) -> bool:
    if n.get("type") not in (None, "file"):
        return False
    sf = _norm(n.get("source_file"))
    return bool(sf) and _norm(n.get("label")) == sf.rsplit("/", 1)[-1]


def slice_graph(raw: dict, include, exclude=(), flows=("calls", "data"), direction="both", depth=1,
                fanout=30, match="path", with_includes=True) -> dict:
    """Pure function: node-link dict in, ``{"nodes", "links", "report", "roles"}`` out."""
    include = [_norm(p) for p in include if p]
    exclude = [_norm(p) for p in exclude if p]
    flows = set(flows)
    dirs = {"in": ["up"], "out": ["down"], "both": ["up", "down"]}[direction]
    edges = raw.get("links", raw.get("edges", []))
    nodes = {n["id"]: n for n in raw.get("nodes", [])}
    typ = {i: n.get("type") for i, n in nodes.items()}
    out_e: dict = defaultdict(list)
    in_e: dict = defaultdict(list)
    for e in edges:
        out_e[e["source"]].append(e)
        in_e[e["target"]].append(e)

    def is_seed(n: dict) -> bool:
        sf, lab = _norm(n.get("source_file")), _norm(n.get("label"))
        dfile = _norm((n.get("metadata") or {}).get("defined_in"))
        files = [sf] + ([dfile] if dfile and dfile != sf else [])  # a variable belongs to the module that DEFINES it
        hay = files if match == "path" else [lab] if match == "label" else files + [lab]
        return any(p in h for p in include for h in hay) and not any(x in sf for x in exclude)

    seeds = {i for i, n in nodes.items() if is_seed(n)}
    roles: dict = {i: "inside" for i in seeds}
    hop: dict = {i: 0 for i in seeds}
    why: dict = defaultdict(list)       # border id -> readable reasons
    keep_e: list = []                   # edge dicts (identity-deduped below)
    stubs: list = []

    def add_edges(es):
        keep_e.extend(es)

    def neighbours(nid: str, dirn: str):
        """[(function id, [edges walked], via variable id | None, reason)]"""
        t, res = typ.get(nid), []
        if t == "function":
            if "calls" in flows:
                if dirn == "up":
                    res += [(e["source"], [e], None, f"calls {nodes[nid].get('label')}")
                            for e in in_e[nid] if e["relation"] in CALL and typ.get(e["source"]) == "function"]
                else:
                    res += [(e["target"], [e], None, f"called by {nodes[nid].get('label')}")
                            for e in out_e[nid] if e["relation"] in CALL and typ.get(e["target"]) == "function"]
            if "data" in flows:
                if dirn == "up":     # writers of variables nid reads
                    for e in out_e[nid]:
                        if e["relation"] in READ and typ.get(e["target"]) == "variable":
                            v = e["target"]
                            res += [(w["source"], [e, w], v, f"writes {nodes[v].get('label')}")
                                    for w in in_e[v] if w["relation"] in WRITE and w["source"] != nid
                                    and typ.get(w["source"]) == "function"]
                else:                # readers of variables nid writes
                    for e in out_e[nid]:
                        if e["relation"] in WRITE and typ.get(e["target"]) == "variable":
                            v = e["target"]
                            res += [(r["source"], [e, r], v, f"reads {nodes[v].get('label')}")
                                    for r in in_e[v] if r["relation"] in READ and r["source"] != nid
                                    and typ.get(r["source"]) == "function"]
        elif t == "variable" and "data" in flows:   # a module-owned variable as the starting point
            if dirn == "up":
                res += [(w["source"], [w], None, f"writes {nodes[nid].get('label')}")
                        for w in in_e[nid] if w["relation"] in WRITE and typ.get(w["source"]) == "function"]
            else:
                res += [(r["source"], [r], None, f"reads {nodes[nid].get('label')}")
                        for r in in_e[nid] if r["relation"] in READ and typ.get(r["source"]) == "function"]
        return [x for x in res if x[0] != nid]

    # the module's own data interface: variables its functions read (in) / write (out)
    if "data" in flows:
        for f in [i for i in seeds if typ.get(i) == "function"]:
            for e in out_e[f]:
                if typ.get(e["target"]) == "variable" and (
                        ("up" in dirs and e["relation"] in READ) or ("down" in dirs and e["relation"] in WRITE)):
                    add_edges([e])
                    roles.setdefault(e["target"], "shared")
                    hop.setdefault(e["target"], 0)

    frontier = {(i, d) for i in seeds if typ.get(i) in ("function", "variable") for d in dirs}
    visited = set(frontier)
    for h in range(1, depth + 1):
        nxt: set = set()
        for nid, dirn in sorted(frontier):
            nb = neighbours(nid, dirn)
            distinct = {x[0] for x in nb}
            if len(distinct) > fanout:
                sid = f"stub::{nid}::{dirn}"
                kind = "writers/callers" if dirn == "up" else "readers/callees"
                stubs.append({"id": sid, "label": f"... {len(distinct)} {kind} (> fanout {fanout})",
                              "type": "stub", "source_file": "", "file_type": "code"})
                roles[sid], hop[sid] = "stub", h
                stubs[-1]["_edge"] = ({"source": sid, "target": nid, "relation": "collapsed", "confidence": "EXTRACTED"}
                                      if dirn == "up" else
                                      {"source": nid, "target": sid, "relation": "collapsed", "confidence": "EXTRACTED"})
                continue
            for fn, es, via, reason in nb:
                add_edges(es)
                if via and via not in roles:
                    roles[via], hop[via] = "shared", h
                if fn not in roles:
                    roles[fn], hop[fn] = "border", h
                if roles[fn] == "border":
                    why[fn].append(f"{'upstream' if dirn == 'up' else 'downstream'} h{h}: {reason}")
                    if (fn, dirn) not in visited:
                        visited.add((fn, dirn))
                        nxt.add((fn, dirn))
        frontier = nxt

    # calibration: fields read by inside functions, their A2L / axis neighbours, and peer readers
    if "calibration" in flows:
        for f in [i for i in seeds if typ.get(i) == "function"]:
            for e in out_e[f]:
                if e["relation"] in CAL_READ:
                    c = e["target"]
                    add_edges([e])
                    roles.setdefault(c, "attached")
                    hop.setdefault(c, 1)
                    if direction in ("in", "both"):
                        peers = [p for p in in_e[c] if p["relation"] in CAL_READ and p["source"] != f
                                 and typ.get(p["source"]) == "function"]
                        if len({p["source"] for p in peers}) <= fanout:
                            for p in peers:
                                add_edges([p])
                                if p["source"] not in roles:
                                    roles[p["source"]], hop[p["source"]] = "border", 1
                                    why[p["source"]].append(f"reads same calibration {nodes[c].get('label')}")
        for c in [i for i, r in list(roles.items()) if typ.get(i) in ("calibration_field", "variable")]:
            for e in out_e[c] + in_e[c]:
                if e["relation"] in CAL_ATTACH:
                    o = e["target"] if e["source"] == c else e["source"]
                    add_edges([e])
                    roles.setdefault(o, "attached")
                    hop.setdefault(o, 1)

    # structure: only the module's own files, and only the files they directly include
    for f in [i for i in seeds if _is_file_node(nodes[i])]:
        for e in out_e[f]:
            if e["relation"] == "contains" and e["target"] in seeds:
                add_edges([e])
            elif e["relation"] == "imports" and with_includes:
                add_edges([e])
                if e["target"] not in roles:
                    roles[e["target"]], hop[e["target"]] = "include", 1

    # edges between kept nodes: everything inside the module, flow edges that touch the module
    flow_rel = (CALL if "calls" in flows else set()) | (READ | WRITE if "data" in flows else set()) \
        | (CAL_READ if "calibration" in flows else set())
    for e in edges:
        s, t = e["source"], e["target"]
        if s in roles and t in roles:
            if (s in seeds and t in seeds) or ((s in seeds or t in seeds) and e["relation"] in flow_rel):
                add_edges([e])
    for sb in stubs:
        keep_e.append(sb.pop("_edge"))

    uniq, seen = [], set()
    for e in keep_e:
        k = (e["source"], e["target"], e["relation"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)

    out_nodes = []
    for nid, role in roles.items():
        n = dict(nodes.get(nid) or next(s for s in stubs if s["id"] == nid))
        n["focus_role"], n["focus_hop"] = role, hop.get(nid, 0)
        n["community"] = ROLE_COMMUNITY[role]
        n["community_name"] = ROLE_LABELS[ROLE_COMMUNITY[role]]
        if typ.get(nid) == "variable" and any((e.get("metadata") or {}).get("address_taken")
                                              for e in out_e.get(nid, []) + in_e.get(nid, [])):
            n["metadata"] = dict(n.get("metadata") or {}, address_taken=True)
        out_nodes.append(n)

    border = [{"id": i, "label": nodes[i].get("label"), "source_file": nodes[i].get("source_file"),
               "hop": hop[i], "reasons": why[i]} for i, r in roles.items() if r == "border"]
    return {"nodes": out_nodes, "links": uniq, "roles": roles, "seeds": seeds,
            "report": {"border": sorted(border, key=lambda b: (b["hop"], str(b["source_file"]), str(b["label"]))),
                       "address_taken_vars": sorted(n["label"] for n in out_nodes
                                                    if (n.get("metadata") or {}).get("address_taken")),
                       "stubs": [s["label"] for s in stubs]}}


def _load_config(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_focus(argv: list[str]) -> int:
    usage = ('Usage: graphify focus "<path substring>" [--exclude P] [--flow calls,data,calibration|both|all]\n'
             '         [--direction in|out|both] [--depth N] [--fanout N] [--match path|label|both]\n'
             '         [--no-includes] [--config focus.json] [--graph PATH] [--out DIR]\n'
             '  e.g. graphify focus ku/kku --flow data --direction in\n'
             '  --flow all = legacy N-hop expansion over every relation (--max-degree applies)')
    if not argv or argv[0] in ("-h", "--help"):
        print(usage, file=sys.stderr)
        return 1
    opts = {"include": [], "exclude": [], "flow": "both", "direction": "both", "depth": 1, "fanout": 30,
            "match": "path", "includes": True, "graph": None, "out": None}
    args = list(argv)
    cfg_path = next((args[i + 1] for i, a in enumerate(args) if a == "--config" and i + 1 < len(args)), None)
    if cfg_path:
        c = _load_config(cfg_path)
        for k in ("exclude", "flow", "direction", "depth", "fanout", "match", "includes", "graph", "out"):
            if k in c:
                opts[k] = c[k]
        opts["include"] = list(c.get("include", []))
    if args and not args[0].startswith("--"):
        opts["include"].insert(0, args.pop(0))
    i = 0
    while i < len(args):
        a, nxt = args[i], args[i + 1] if i + 1 < len(args) else None
        if a == "--exclude" and nxt:
            opts["exclude"].append(nxt); i += 1
        elif a == "--include" and nxt:
            opts["include"].append(nxt); i += 1
        elif a in ("--flow", "--direction", "--match", "--graph", "--out") and nxt:
            opts[a[2:]] = nxt; i += 1
        elif a in ("--depth", "--fanout") and nxt:
            opts[a[2:]] = int(nxt); i += 1
        elif a == "--no-includes":
            opts["includes"] = False
        i += 1
    if not opts["include"]:
        print("error: give a path pattern or an 'include' list in --config\n" + usage, file=sys.stderr)
        return 1
    if opts["direction"] not in ("in", "out", "both") or opts["match"] not in ("path", "label", "both"):
        print("error: --direction must be in|out|both and --match path|label|both", file=sys.stderr)
        return 1
    flows: set = set()
    for tok in str(opts["flow"]).split(","):
        tok = tok.strip()
        flows |= FLOW_ALIASES.get(tok, {tok})
    if flows - {"calls", "data", "calibration", "all"}:
        print(f"error: unknown --flow {sorted(flows - {'calls', 'data', 'calibration', 'all'})}", file=sys.stderr)
        return 1
    if "all" in flows:
        return _run_legacy([opts["include"][0]] + [a for a in args if a not in opts["include"]])

    from graphify.paths import out_path
    gp = Path(opts["graph"]) if opts["graph"] else Path(out_path()) / "graph.json"
    if not gp.is_file():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    raw = json.loads(gp.read_text(encoding="utf-8"))
    res = slice_graph(raw, opts["include"], opts["exclude"], flows, opts["direction"], opts["depth"],
                      opts["fanout"], opts["match"], opts["includes"])
    if not res["seeds"]:
        print(f"No node with {opts['match']} containing {opts['include']}.")
        return 0

    slug = re.sub(r"[^a-z0-9]+", "_", _norm(opts["include"][0])).strip("_") or "focus"
    out_dir = Path(opts["out"]) if opts["out"] else gp.parent / "focus" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    sub = {k: v for k, v in raw.items() if k not in ("nodes", "links", "edges")}
    sub["nodes"], sub["links"] = res["nodes"], res["links"]
    (out_dir / "graph.json").write_text(json.dumps(sub), encoding="utf-8")
    used = {ROLE_COMMUNITY[r] for r in res["roles"].values()}
    (out_dir / ".graphify_labels.json").write_text(
        json.dumps({str(k): v for k, v in ROLE_LABELS.items() if k in used}), encoding="utf-8")
    (out_dir / "focus_report.json").write_text(json.dumps(res["report"], indent=1), encoding="utf-8")

    roles = Counter(res["roles"].values())
    rels = Counter(e.get("relation") for e in res["links"])
    print(f"focus {opts['include']} flow={','.join(sorted(flows))} direction={opts['direction']} depth={opts['depth']}: "
          f"{len(res['nodes'])} nodes / {len(res['links'])} edges")
    print("  nodes by role: " + ", ".join(f"{k}={v}" for k, v in roles.most_common()))
    print("  edges by relation: " + ", ".join(f"{k}={v}" for k, v in rels.most_common()))
    b = res["report"]["border"]
    print(f"  border functions (outside the module): {len(b)}")
    for x in b[:15]:
        print(f"    h{x['hop']} {x['label']}  [{x['source_file']}]  <- {'; '.join(x['reasons'][:2])}")
    if len(b) > 15:
        print(f"    ... {len(b) - 15} more in focus_report.json")
    if res["report"]["address_taken_vars"]:
        print("  variables whose address is taken (may be written through a pointer): "
              + ", ".join(res["report"]["address_taken_vars"][:15]))
    if res["report"]["stubs"]:
        print(f"  collapsed by --fanout {opts['fanout']}: {len(res['report']['stubs'])}")
    print(f"  wrote {out_dir / 'graph.json'}  ->  graphify export html --graph \"{out_dir / 'graph.json'}\"")
    return 0


def _run_legacy(argv: list[str]) -> int:
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
