"""Relation-aware module slicing (`graphify focus`) on a hand-built graph; no clang needed."""
from __future__ import annotations

from graphify.focus import slice_graph


def _n(i, label, sf, typ="function"):
    return {"id": i, "label": label, "source_file": sf, "type": typ, "file_type": "code"}


def _e(s, t, rel, **meta):
    e = {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED"}
    if meta:
        e["metadata"] = meta
    return e


def _graph():
    nodes = [
        _n("kku_c", "kku.c", "src/kku/kku.c", None), _n("kku_h", "kku.h", "src/kku/kku.h", None),
        _n("types_h", "types.h", "src/lib/types.h", None), _n("oh_h", "oh.h", "src/vwh/oh.h", None),
        _n("K1", "K1()", "src/kku/kku.c"), _n("K2", "K2()", "src/kku/k2.c"),
        _n("V1", "V1", "src/vwh/oh.h", "variable"), _n("V2", "V2", "src/vwh/oh.h", "variable"),
        _n("V0", "V0", "src/x/ww.c", "variable"), _n("V3", "V3", "src/y/r.c", "variable"),
        _n("Vrw", "Vrw", "src/vwh/oh.h", "variable"),
        _n("W1", "W1()", "src/vwh/w.c"), _n("W2", "W2()", "src/dg/w2.c"), _n("WW", "WW()", "src/x/ww.c"),
        _n("R1", "R1()", "src/y/r.c"), _n("RR2", "RR2()", "src/y/rr.c"),
        _n("CA", "CA()", "src/z/ca.c"), _n("CAA", "CAA()", "src/z/caa.c"),
        _n("C1", "C1()", "src/lib/c.c"), _n("C2", "C2()", "src/lib/c2.c"),
        _n("RW", "RW()", "src/kku/kku.c"), _n("Wx", "Wx()", "src/o/wx.c"), _n("Rx", "Rx()", "src/o/rx.c"),
    ]
    edges = [
        _e("kku_c", "K1", "contains"), _e("kku_c", "RW", "contains"), _e("kku_c", "kku_h", "imports"),
        _e("kku_h", "types_h", "imports"), _e("oh_h", "V1", "contains"), _e("oh_h", "Vrw", "contains"),
        _e("K1", "V1", "reads_var", address_taken=True), _e("K1", "V2", "writes_var"), _e("K1", "C1", "calls"),
        _e("K1", "K2", "calls"),
        _e("W1", "V1", "writes_var"), _e("W2", "V1", "writes_var"), _e("W2", "V0", "reads_var"),
        _e("WW", "V0", "writes_var"),
        _e("R1", "V2", "reads_var"), _e("R1", "V3", "writes_var"), _e("RR2", "V3", "reads_var"),
        _e("CA", "K1", "calls"), _e("CAA", "CA", "calls"), _e("C1", "C2", "calls"),
        _e("RW", "Vrw", "reads_writes_var"), _e("Wx", "Vrw", "writes_var"), _e("Rx", "Vrw", "reads_var"),
    ]
    return {"directed": False, "nodes": nodes, "links": edges}


def _roles(res, role):
    return {n["id"] for n in res["nodes"] if n["focus_role"] == role}


def _edges(res):
    return {(e["source"], e["target"], e["relation"]) for e in res["links"]}


def test_depth1_both_borders_and_pass_through_variables():
    res = slice_graph(_graph(), ["src/kku/"])
    assert _roles(res, "border") == {"W1", "W2", "R1", "CA", "C1", "Wx", "Rx"}
    assert _roles(res, "shared") >= {"V1", "V2", "Vrw"}
    # variables are pass-through, not hops: depth 1 must not reach the writer of V0 or callers of CA
    assert not _roles(res, "border") & {"WW", "RR2", "CAA", "C2"}


def test_headers_outside_module_never_appear_only_direct_includes():
    res = slice_graph(_graph(), ["src/kku/"])
    ids = {n["id"] for n in res["nodes"]}
    assert "oh_h" not in ids, "declaring header of an outside variable must not be pulled in"
    # kku.h is inside the module here, so its direct include types.h is an include
    assert _roles(res, "include") == {"types_h"}
    res2 = slice_graph(_graph(), ["src/kku/kku.c"])
    assert _roles(res2, "include") == {"kku_h"} and "types_h" not in {n["id"] for n in res2["nodes"]}
    assert ("kku_c", "kku_h", "imports") in _edges(res2)
    assert "oh_h" not in {n["id"] for n in res2["nodes"]}
    no_inc = slice_graph(_graph(), ["src/kku/kku.c"], with_includes=False)
    assert _roles(no_inc, "include") == set()


def test_direction_in_and_out():
    r_in = slice_graph(_graph(), ["src/kku/"], direction="in")
    assert _roles(r_in, "border") == {"W1", "W2", "CA", "Wx"}
    r_out = slice_graph(_graph(), ["src/kku/"], direction="out")
    assert _roles(r_out, "border") == {"R1", "C1", "Rx"}


def test_flow_selection():
    data = slice_graph(_graph(), ["src/kku/"], flows=("data",))
    assert _roles(data, "border") == {"W1", "W2", "R1", "Wx", "Rx"}
    calls = slice_graph(_graph(), ["src/kku/"], flows=("calls",))
    assert _roles(calls, "border") == {"CA", "C1"} and not _roles(calls, "shared")


def test_depth2_keeps_going_in_the_same_direction_only():
    res = slice_graph(_graph(), ["src/kku/"], depth=2)
    assert {"WW", "CAA", "RR2", "C2"} <= _roles(res, "border")
    # R1 was reached downstream, so its own upstream (writers of V2) are not followed back;
    # and the writer WW (upstream of W2) is not expanded downstream.
    assert ("WW", "V0", "writes_var") in _edges(res)
    hops = {n["id"]: n["focus_hop"] for n in res["nodes"]}
    assert hops["W1"] == 1 and hops["WW"] == 2 and hops["CAA"] == 2 and hops["RR2"] == 2


def test_reads_writes_var_counts_as_reader_and_writer():
    res = slice_graph(_graph(), ["src/kku/"])
    border = _roles(res, "border")
    assert {"Wx", "Rx"} <= border, "RW() both reads and writes Vrw, so it has upstream writers and downstream readers"


def test_border_functions_are_not_expanded_at_the_last_hop():
    res = slice_graph(_graph(), ["src/kku/"], depth=1)
    assert ("W2", "V0", "reads_var") not in _edges(res) and "V0" not in {n["id"] for n in res["nodes"]}


def test_fanout_collapses_into_a_stub():
    res = slice_graph(_graph(), ["src/kku/"], fanout=1)
    stubs = _roles(res, "stub")
    assert stubs and any(s.startswith("stub::K1::up") for s in stubs)
    assert "W1" not in _roles(res, "border") and "W2" not in _roles(res, "border")


def test_exclude_removes_seeds():
    res = slice_graph(_graph(), ["src/kku/"], exclude=["src/kku/k2"])
    assert "K2" not in _roles(res, "inside")


def test_address_taken_is_flagged_on_the_variable():
    res = slice_graph(_graph(), ["src/kku/"])
    v1 = next(n for n in res["nodes"] if n["id"] == "V1")
    assert v1["metadata"]["address_taken"] is True
    assert res["report"]["address_taken_vars"] == ["V1"]


def test_roles_become_communities_for_colouring():
    res = slice_graph(_graph(), ["src/kku/"])
    by_role = {}
    for n in res["nodes"]:
        by_role.setdefault(n["focus_role"], set()).add(n["community"])
    assert all(len(v) == 1 for v in by_role.values()) and by_role["inside"] != by_role["border"]


def test_variable_defined_in_the_module_belongs_to_it_even_if_declared_in_an_outside_header():
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "V1":                       # declared in src/vwh/oh.h ...
            n["metadata"] = {"defined_in": "src/kku/kku.c"}   # ... but defined in the module
    res = slice_graph(g, ["src/kku/"])
    assert "V1" in _roles(res, "inside")
