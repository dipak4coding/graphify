"""Connect CAN signals (DBC) to the C variables that carry them.

Simple rule (the project's signal configuration):

* The table ``BilSig_Data_CfgSig_ci[]`` has one struct per signal. Its FIRST member is the signal index
  (a ``#define``, resolved by clang); the comment after the struct (``// name``) is the CAN signal name.
* In ``Sic_HolenSignale()`` a getter is called per signal: ``getter(<index>, &<variable>)``. The second
  argument is the variable that receives the CAN value.
* So: DBC signal  --(name = comment)-->  table row  --(index)-->  getter call  --(argument)-->  variable.

The result is an edge ``can_signal --carried_in--> variable`` (the A2L approach: nodes are not merged, the
facts sit on the edge), a mapping CSV / JSON and a diagnostics list. Names of the table and the reader
function are configuration (``can_table`` / ``can_reader``). The CAN name comes from the comment, as the
user decided for now; the DBC only confirms it (a comment that is not a DBC signal is reported).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

RELATION = "carried_in"
CSV_COLUMNS = ["dbc_signal", "message", "frame_id", "direction", "config_index", "config_comment", "config_file",
               "config_line", "getter", "getter_file", "getter_line", "variable", "variable_file", "status"]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


def join_can(nodes: list, edges: list, rows: list, calls: list, source_file: str = "") -> dict:
    """Add ``carried_in`` edges (in place). -> {"mapping": [...], "diagnostics": {...}, "stats": {...}}."""
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    sigs = [n for n in nodes if n.get("type") == "can_signal"]
    by_name, by_norm = {}, {}
    for n in sigs:
        by_name.setdefault(n["label"], []).append(n)
        by_norm.setdefault(_norm(n["label"]), []).append(n)
    vars_by_label: dict = {}
    for n in nodes:
        if n.get("type") == "variable":
            vars_by_label.setdefault(n.get("label"), []).append(n)

    def var_node(call: dict):
        v = by_id.get(call.get("var"))
        if v is not None:
            return v
        cands = vars_by_label.get(call.get("var_name"), [])
        defs = [c for c in cands if not (c.get("metadata") or {}).get("declaration_only")]
        return (defs or cands or [None])[0]

    # two index spaces: the BilSig table (first member = index) and the SokSig table (position = index), never mixed
    calls_by_idx: dict = {}
    for c in calls:
        if c.get("index") is not None and c.get("var_name"):
            calls_by_idx.setdefault((c.get("src", ""), c["index"]), []).append(c)
    rows_by_idx: dict = {}
    for r in rows:
        if r.get("index") is not None:
            rows_by_idx.setdefault((r.get("src", ""), r["index"]), []).append(r)

    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in edges}
    mapping, diag = [], {"rows_without_index": [], "rows_without_name": [], "rows_not_in_dbc": [], "rows_without_getter": [],
                         "getters_without_row": [], "getters_not_resolved": [], "duplicate_index": [],
                         "ambiguous_dbc_name": [], "rx_signals_without_row": []}
    linked_sigs: set = set()

    for (_src, idx), rs in sorted(rows_by_idx.items()):
        if len(rs) > 1:
            diag["duplicate_index"].append({"index": idx, "rows": [f"{r['file']}:{r['line']}" for r in rs]})
    for r in rows:
        where = f"{r['file']}:{r['line']}"
        if r.get("index") is None:
            diag["rows_without_index"].append({"at": where, "first_member": r.get("first", "")})
            continue
        if not r.get("name"):
            diag["rows_without_name"].append({"at": where, "index": r["index"]})
            continue
        cands = by_name.get(r["name"]) or by_norm.get(_norm(r["name"])) or []
        status = "exact" if by_name.get(r["name"]) else "normalized"
        if not cands:
            diag["rows_not_in_dbc"].append({"at": where, "index": r["index"], "comment": r["name"]})
            continue
        if len(cands) > 1:
            diag["ambiguous_dbc_name"].append({"comment": r["name"], "messages": [c["metadata"].get("message") for c in cands]})
            status = "ambiguous"
        gcalls = calls_by_idx.get((r.get("src", ""), r["index"]), [])
        if not gcalls:
            diag["rows_without_getter"].append({"at": where, "index": r["index"], "comment": r["name"]})
        for sg in cands:
            m = sg.get("metadata") or {}
            base = {"dbc_signal": sg["label"], "message": m.get("message"), "frame_id": m.get("frame_id"),
                    "direction": m.get("direction", ""), "config_index": r["index"], "config_comment": r["name"],
                    "config_file": r["file"], "config_line": r["line"]}
            if not gcalls:
                mapping.append({**base, "getter": "", "getter_file": "", "getter_line": "", "variable": "",
                                "variable_file": "", "status": status + "_no_getter"})
                continue
            for c in gcalls:
                v = var_node(c)
                st = status if len(gcalls) == 1 else "multiple_getters"
                mapping.append({**base, "getter": c["getter"], "getter_file": c["file"], "getter_line": c["line"],
                                "variable": c["var_name"], "variable_file": (v or {}).get("source_file", ""), "status": st})
                if v is None:
                    diag["getters_not_resolved"].append({"index": c["index"], "variable": c["var_name"]})
                    continue
                linked_sigs.add(sg["id"])
                key = (sg["id"], v["id"], RELATION)
                if key not in seen:
                    seen.add(key)
                    edges.append({"source": sg["id"], "target": v["id"], "relation": RELATION, "confidence": "EXTRACTED",
                                  "source_file": r["file"], "weight": 1.0, "context": "can_config",
                                  "metadata": {"index": r["index"], "config_comment": r["name"], "getter": c["getter"],
                                               "getter_line": c["line"], "getter_file": c["file"],
                                               "config_line": r["line"], "match": status}})
    for (src, idx), cs in sorted(calls_by_idx.items()):
        if (src, idx) not in rows_by_idx:
            for c in cs:
                diag["getters_without_row"].append({"index": idx, "variable": c["var_name"], "at": f"{c['file']}:{c['line']}"})
    for c in calls:
        if c.get("note"):
            diag["getters_not_resolved"].append({"index": c.get("index"), "at": f"{c['file']}:{c['line']}", "note": c["note"]})
    for n in sigs:
        if n["id"] not in linked_sigs and (n.get("metadata") or {}).get("direction") == "RX":
            diag["rx_signals_without_row"].append({"signal": n["label"], "message": n["metadata"].get("message")})
    stats = {"rows": len(rows), "getter_calls": len(calls), "linked_signals": len(linked_sigs),
             "mapping_rows": len(mapping), **{k: len(v) for k, v in diag.items()}}
    return {"mapping": mapping, "diagnostics": diag, "stats": stats}


def write_outputs(result: dict, out_dir) -> tuple:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cp, jp = out / "can_mapping.csv", out / "can_mapping.json"
    with open(cp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(result["mapping"])
    jp.write_text(json.dumps({"stats": result["stats"], "diagnostics": result["diagnostics"], "mapping": result["mapping"]},
                             indent=1, ensure_ascii=False), encoding="utf-8")
    return cp, jp


TX_COLUMNS = ["dbc_signal", "message", "frame_id", "direction", "function", "macro", "file", "line", "sources", "constant", "status"]


def join_can_tx(nodes: list, edges: list, tx: list, out_dir=None) -> dict:
    """TX side: ``return MACRO(value, <dbc signal name>)`` -> edges ``can_signal --computed_by--> function`` and
    ``can_signal --derived_from--> variable`` (the variables the function's return value comes from). A constant scalar value
    (``return MACRO(5, Sig)``) is kept as ``constants`` on the ``computed_by`` edge."""
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    by_name, by_norm = {}, {}
    for n in nodes:
        if n.get("type") == "can_signal":
            by_name.setdefault(n["label"], []).append(n)
            by_norm.setdefault(_norm(n["label"]), []).append(n)
    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in edges}
    # one entry per (function, label): several return statements are merged (lines, constants, sources)
    groups: dict = {}
    for t in tx:
        g = groups.setdefault((t["function"], t["label"]), {**t, "lines": [], "constants": [], "sources": []})
        g["lines"].append(t["line"])
        if t.get("constant") is not None and t["constant"] not in g["constants"]:
            g["constants"].append(t["constant"])
        g["sources"] = sorted(set(g["sources"]) | set(t.get("sources", [])))
    rows, not_in_dbc, linked = [], [], set()
    for t in groups.values():
        fn = by_id.get(t["function"])
        cands = by_name.get(t["label"]) or by_norm.get(_norm(t["label"])) or []
        exact = bool(by_name.get(t["label"]))
        if fn is None:
            continue
        if not cands:
            not_in_dbc.append({"label": t["label"], "function": fn.get("label"), "at": f"{t['file']}:{t['line']}"})
            continue
        status = ("exact" if exact else "normalized") if len(cands) == 1 else "ambiguous"
        srcs = [v for v in t.get("sources", []) if v in by_id]
        if not srcs and t["constants"]:
            status += "+constant"
        for sg in cands:
            m = sg.get("metadata") or {}
            linked.add(sg["id"])
            rows.append({"dbc_signal": sg["label"], "message": m.get("message"), "frame_id": m.get("frame_id"),
                         "direction": m.get("direction", ""), "function": fn.get("label"), "macro": t["macro"],
                         "file": t["file"], "line": min(t["lines"]), "sources": " ".join(by_id[v].get("label", "") for v in srcs),
                         "constant": " ".join(t["constants"]), "status": status})
            meta = {"macro": t["macro"], "line": min(t["lines"]), "lines": sorted(set(t["lines"])), "file": t["file"], "match": status,
                    "constants": list(t["constants"])}
            for tgt, rel, md in [(fn["id"], "computed_by", meta)] + [
                    (v, "derived_from", {"functions": [fn["id"]], "lines": sorted(set(t["lines"])), "via": [t["macro"]], "control_only": False,
                                         "can_tx": True}) for v in srcs]:
                key = (sg["id"], tgt, rel)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": sg["id"], "target": tgt, "relation": rel, "confidence": "EXTRACTED" if rel == "computed_by" else "INFERRED",
                              "source_file": t["file"], "weight": 1.0, "context": "can_tx" if rel == "computed_by" else "flow",
                              "metadata": md, "source_location": f"L{min(t['lines'])}"})
    stats = {"functions": len(groups), "linked_signals": len(linked), "not_in_dbc": len(not_in_dbc),
             "constant_only": sum(1 for r in rows if not r["sources"] and r["constant"])}
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "can_tx_mapping.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TX_COLUMNS, delimiter=";", extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            for d in not_in_dbc:
                w.writerow({"dbc_signal": d["label"], "function": d["function"], "file": d["at"], "status": "not_in_dbc"})
    return {"mapping": rows, "not_in_dbc": not_in_dbc, "stats": stats}
