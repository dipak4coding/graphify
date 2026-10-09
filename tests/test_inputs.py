"""`graphify explain F --inputs`: inputs of a function and the producers of those inputs."""
from __future__ import annotations

import json

import graphify.__main__ as mainmod
from graphify.inputs import inputs_report


def _n(i, label, typ="function", sf="a.c", **meta):
    n = {"id": i, "label": label, "type": typ, "source_file": sf, "source_location": "L1", "community": 0}
    if meta:
        n["metadata"] = meta
    return n


def _e(s, t, rel, loc="L5", **meta):
    e = {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED",
         "source_file": s + ".c", "source_location": loc}
    if meta:
        e["metadata"] = meta
    return e


def _graph():
    nodes = [
        _n("F", "F()", sf="kku/kku.c", parameters=[{"type": "int", "name": "x", "role": "in"}]),
        _n("V1", "V1", "variable", sf="vwh/oh.h", storage="global", defined_in="vwh/oh.c", a2l_range="0..100"),
        _n("V2", "V2", "variable"), _n("V3", "V3", "variable", storage="static"),
        _n("Cal", "Cal", "calibration_field", a2l_range="1..9"),
        _n("W1", "W1()", sf="vwh/w1.c"), _n("W2", "W2()", sf="vwh/w2.c"), _n("W3", "W3()"),
        _n("V0", "V0", "variable"), _n("WW", "WW()", sf="x/ww.c"),
        _n("CA", "CA()"), _n("Other", "Other()"),
    ]
    edges = [
        _e("F", "V1", "reads_var", "L12", address_taken=True), _e("F", "V2", "reads_var"),
        _e("F", "V3", "reads_writes_var"), _e("F", "Cal", "reads_calibration_field"),
        _e("W1", "V1", "writes_var", "L44"), _e("W2", "V1", "writes_var", "L9"),
        _e("W3", "V3", "writes_var"),
        _e("W1", "V0", "reads_var"), _e("WW", "V0", "writes_var", "L77"),
        _e("CA", "F", "calls"),
        _e("F", "Other", "calls"),                       # a callee is not an input
    ]
    return {"directed": False, "nodes": nodes, "links": edges}


def test_hop1_lists_inputs_writers_and_calibration():
    out = inputs_report(_graph(), "F")
    assert "Inputs of F()" in out and "Params: int x (in)" in out
    assert "Callers (1)" in out and "CA()" in out
    assert "V1  [variable, global, defined in vwh/oh.c, range 0..100, address taken]" in out
    assert "<-- W1()  writes  W1.c:L44" in out and "<-- W2()  writes  W2.c:L9" in out
    assert "calibration Cal" in out and "range 1..9" in out
    assert "Other()" not in out, "callees are outputs, not inputs"


def test_variable_without_writer_and_read_modify_write():
    out = inputs_report(_graph(), "F")
    v2 = out.split("  V2  [")[1].split("\n")[1]
    assert "no writer found" in v2
    assert "also written by F() itself (read-modify-write)" in out
    assert "<-- W3()" in out


def test_depth_adds_producer_inputs_only_when_asked():
    assert "WW()" not in inputs_report(_graph(), "F", depth=1)
    out = inputs_report(_graph(), "F", depth=2)
    assert "Hop 2" in out and "<-- WW()  writes  WW.c:L77" in out


def test_budget_stops_at_a_complete_hop():
    out = inputs_report(_graph(), "F", depth=2, budget=1)
    assert "stopped after hop 1" in out and "Hop 2" not in out
    assert "<-- W1()" in out, "hop 1 is never cut"


def test_fanout_collapses_writers():
    g = _graph()
    for i in range(5):
        g["nodes"].append(_n(f"X{i}", f"X{i}()"))
        g["links"].append(_e(f"X{i}", "V2", "writes_var"))
    out = inputs_report(g, "F", fanout=2)
    assert "writers collapsed" in out and "raise --fanout" in out


def _run(monkeypatch, tmp_path, capsys, *args):
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(_graph()))
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "explain", *args, "--graph", str(p)])
    try:
        mainmod.main()
    except SystemExit:
        pass
    return capsys.readouterr()


def test_cli_inputs_for_function(monkeypatch, tmp_path, capsys):
    cap = _run(monkeypatch, tmp_path, capsys, "F", "--inputs", "--depth", "2")
    assert "Inputs of F()" in cap.out and "Hop 2" in cap.out and "Node: F()" not in cap.out


def test_cli_inputs_on_a_variable_falls_back_to_plain_explain(monkeypatch, tmp_path, capsys):
    cap = _run(monkeypatch, tmp_path, capsys, "V1", "--inputs")
    assert "Node: V1" in cap.out and "applies to functions" in cap.err
