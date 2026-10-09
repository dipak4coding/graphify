"""`graphify explain "<function>" --inputs`: what feeds a function, and who produces it.

To understand a function you need its inputs (the variables and calibrations it reads, its
parameters, its callers) and the functions that produce those inputs. The walk reuses the
upstream data slice of ``graphify focus`` (``slice_graph``): a variable is a pass-through, so
one hop means "the function that writes a variable I read".

Pure function over a node-link dict (``inputs_report``), no graph loading and no printing.
"""
from __future__ import annotations

from collections import defaultdict

from graphify.focus import READ, WRITE, CALL, slice_graph

CAL_READ = "reads_calibration_field"
MAX_WRITERS = 10
MAX_CALLERS = 8
NO_WRITER = "no writer found (pointer write, inactive #ifdef, file outside the compile DB, or external input)"


def _loc(e: dict) -> str:
    sf, ln = e.get("source_file") or "", e.get("source_location") or ""
    return f"{sf}:{ln}" if ln else sf


def _facts(n: dict, address_taken: bool) -> str:
    meta = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
    bits = [n.get("type") or "node"]
    if meta.get("storage"):
        bits.append(str(meta["storage"]))
    if meta.get("defined_in"):
        bits.append(f"defined in {meta['defined_in']}")
    if meta.get("a2l_range"):
        bits.append(f"range {meta['a2l_range']}")
    if meta.get("bit_mask") is not None:
        bits.append(f"mask {meta['bit_mask']}")
    if address_taken:
        bits.append("address taken")
    return ", ".join(bits)


def inputs_report(raw: dict, nid: str, depth: int = 1, fanout: int = 30, budget: int = 2000) -> str:
    """Text report of the inputs of ``nid`` and their producers, ``depth`` producer hops deep.

    Hop 1 is always complete. A deeper hop is printed only if the whole hop fits the token
    budget (about 3 characters per token); otherwise the report says where it stopped."""
    edges = raw.get("links", raw.get("edges", []))
    nodes = {n["id"]: n for n in raw.get("nodes", [])}
    if nid not in nodes:
        return f"Node not found: {nid}"
    typ = {i: n.get("type") for i, n in nodes.items()}
    out_e, in_e = defaultdict(list), defaultdict(list)
    for e in edges:
        out_e[e["source"]].append(e)
        in_e[e["target"]].append(e)

    res = slice_graph(raw, [], flows=("data",), direction="in", depth=depth, fanout=fanout,
                      with_includes=False, seed_ids=[nid])
    hop = {n["id"]: n["focus_hop"] for n in res["nodes"]}
    # stub ids are "stub::<function id>::up"; the function id may itself contain "::"
    stub_for = {n["id"][len("stub::"):-len("::up")]: n for n in res["nodes"]
                if n.get("type") == "stub" and n["id"].startswith("stub::") and n["id"].endswith("::up")}
    me = nodes[nid]

    def label(i: str) -> str:
        return str(nodes[i].get("label", i))

    head = [f"Inputs of {label(nid)}  [{me.get('source_file', '')} {me.get('source_location', '')}]".rstrip()
            + f"  (producer depth {depth})"]
    meta = me.get("metadata") if isinstance(me.get("metadata"), dict) else {}
    params = meta.get("parameters")
    if isinstance(params, list) and params:
        head.append("Params: " + ", ".join(
            f"{p.get('type', '')} {p.get('name', '')} ({p.get('role', '')})".strip()
            for p in params[:8] if isinstance(p, dict)))
    callers = sorted({label(e["source"]) for e in in_e[nid]
                      if e["relation"] in CALL and typ.get(e["source"]) == "function"})
    if callers:
        extra = f" +{len(callers) - MAX_CALLERS} more" if len(callers) > MAX_CALLERS else ""
        head.append(f"Callers ({len(callers)}), argument values come from here: "
                    + ", ".join(callers[:MAX_CALLERS]) + extra)

    def block(fn: str) -> list:
        lines = []
        reads = sorted({e["target"] for e in out_e[fn] if e["relation"] in READ and typ.get(e["target"]) == "variable"},
                       key=lambda v: label(v).lower())
        for v in reads:
            taken = any((e.get("metadata") or {}).get("address_taken") for e in in_e[v] + out_e[v])
            lines.append(f"  {label(v)}  [{_facts(nodes[v], taken)}]")
            if fn in stub_for:
                lines.append(f"      writers collapsed: {stub_for[fn]['label']} - raise --fanout to list them")
                continue
            writers = sorted(((label(w['source']), _loc(w)) for w in in_e[v]
                              if w["relation"] in WRITE and w["source"] != fn and typ.get(w["source"]) == "function"))
            if any(e["relation"] in WRITE and e["target"] == v for e in out_e[fn]):
                lines.append(f"      also written by {label(fn)} itself (read-modify-write)")
            for name, at in writers[:MAX_WRITERS]:
                lines.append(f"      <-- {name}  writes  {at}".rstrip())
            if len(writers) > MAX_WRITERS:
                lines.append(f"      ... +{len(writers) - MAX_WRITERS} more writers")
            if not writers:
                lines.append(f"      {NO_WRITER}")
        cals = sorted({e["target"] for e in out_e[fn] if e["relation"] == CAL_READ})
        for c in cals:
            lines.append(f"  calibration {label(c)}  [{_facts(nodes[c], False)}]")
        if not reads and not cals:
            lines.append("  (reads no variable or calibration)")
        return lines

    sections, stopped = [], None
    for h in range(1, depth + 1):
        funcs = sorted((i for i, hh in hop.items() if hh == h - 1 and typ.get(i) == "function"), key=label)
        if not funcs:
            break
        sec = [f"Hop {h}: " + ("inputs of " + label(nid) if h == 1 else "inputs of the producers found in hop "
                                                                       f"{h - 1}")]
        for fn in funcs:
            if h > 1:
                sec.append(f" {label(fn)}  [{nodes[fn].get('source_file', '')}]")
            sec += block(fn)
        used = sum(len("\n".join(s)) + 1 for s in sections) + len("\n".join(head)) + len("\n".join(sec))
        if h > 1 and used > budget * 3:
            stopped = h - 1
            break
        sections.append(sec)

    out = head + [line for s in sections for line in s]
    if stopped is not None:
        out.append(f"... stopped after hop {stopped}: hop {stopped + 1} exceeds the ~{budget}-token budget "
                   "(raise --budget, or lower --depth)")
    return "\n".join(out)
