"""Parameter databank: .par parsing, A2L cross-check, SQLite, printing, explain --inputs --dataset."""
from __future__ import annotations

import json

import graphify.__main__ as mainmod
from graphify import params

PAR = """\
; some header, not standard
Project: DQ381
Gain [FLOAT]  1.5 ; 1.5
Mode [UINT(8)]  1 ; Sport
Sw.bit1 [UINT(1)]  1 ; 1
Sw.bit2 [UINT(1)]  0 ; 0
Ax_X [INT(16),(1,3)]  0 ; 0
:  100 ; 10
:  200 ; 20
Ax_Y [INT(16),(1,2)]  0 ; 0
:  50 ; 5
Dummy_Map [INT(16),(2,3)]  0 ; 0
:  100 ; 10
:  200 ; 20
:  300 ; 30
:  400 ; 40
:  500 ; 50
Extra [FLOAT]  9 ; 9
this line is garbage
"""

A2L = {
    "characteristics": {
        "Gain": {"char_type": "VALUE", "compu_method": "CM_LIN", "axis_references": []},
        "Mode": {"char_type": "VALUE", "compu_method": "CM_MODE", "axis_references": []},
        "Sw": {"char_type": "VALUE", "compu_method": "", "axis_references": []},
        "Dummy_Map": {"char_type": "MAP", "compu_method": "CM_LIN", "axis_references": ["Ax_X", "Ax_Y"]},
        "Lonely": {"char_type": "VALUE", "compu_method": "", "axis_references": []},
    },
    "axis_pts": {"Ax_X": {"compu_method": "CM_LIN"}, "Ax_Y": {"compu_method": "CM_LIN"}},
    "compu_methods": {"CM_LIN": {"type": "LINEAR", "unit": "km/h"},
                      "CM_MODE": {"type": "TAB_VERB", "compu_vtab": "VT"}},
    "compu_vtabs": {"VT": {"content": {"0": "Eco", "1": "Comfort"}}},
}


def test_parse_entries_kinds_and_shapes():
    entries, header, unparsed = params.parse_par(PAR)
    by = {e.name: e for e in entries}
    assert by["Gain"].kind == "scalar" and by["Gain"].phys == [1.5]
    assert by["Mode"].kind == "enum" and by["Mode"].raw == [1] and by["Mode"].phys == ["Sport"]
    assert by["Sw.bit1"].kind == "bit" and by["Sw.bit1"].parent == "Sw" and by["Sw.bit1"].bit == 1
    assert by["Ax_X"].kind == "curve" and by["Ax_X"].phys == [0, 10, 20]
    m = by["Dummy_Map"]
    assert (m.kind, m.rows, m.cols) == ("map", 2, 3) and m.phys == [0, 10, 20, 30, 40, 50]
    assert any("garbage" in t for _, t in unparsed) and any("header" in h for h in header)
    assert all(not e.issues for e in entries)


def test_analyze_reports_crosscheck():
    entries, _, _ = params.parse_par(PAR)
    rep, enrich = params.analyze(entries, A2L)
    assert rep["not_in_a2l"] == ["Extra"]
    assert rep["a2l_without_value"] == ["Lonely"]
    assert rep["shape_vs_axis"] == []          # 3 columns = X axis, 2 rows = Y axis
    assert rep["enum_issues"] == [{"name": "Mode", "raw": 1, "par": "Sport", "a2l": "Comfort",
                                   "problem": "label differs"}]
    assert enrich["Dummy_Map"]["axes"] == ["Ax_X", "Ax_Y"] and enrich["Gain"]["unit"] == "km/h"


def test_shape_mismatch_detected():
    bad = PAR.replace("Ax_Y [INT(16),(1,2)]  0 ; 0\n:  50 ; 5", "Ax_Y [INT(16),(1,3)]  0 ; 0\n:  50 ; 5\n:  60 ; 6")
    entries, _, _ = params.parse_par(bad)
    rep, _ = params.analyze(entries, A2L)
    assert rep["shape_vs_axis"] and rep["shape_vs_axis"][0]["axis"] == "Ax_Y"


def test_import_show_and_describe(tmp_path, capsys):
    par = tmp_path / "a.par"
    par.write_text(PAR, encoding="utf-8")
    db = tmp_path / "params.db"
    r = params.import_par(str(par), db, "setA", A2L, "x.a2l")
    assert r["entries"] == 8 + 0 and r["not_in_a2l_count"] == 1
    assert json.loads((tmp_path / "params_import_setA.json").read_text())["dataset"] == "setA"
    conn = params.open_db(db, create=False)
    assert params.describe(conn, "setA", "Gain") == ["  Gain [FLOAT] = 1.5 km/h"]
    assert params.describe(conn, "setA", "Mode")[0].endswith("= Sport (raw 1)")
    text = "\n".join(params.describe(conn, "setA", "Dummy_Map"))
    assert "map 2x3" in text and "X axis Ax_X, Y axis Ax_Y" in text and "50" in text
    small = "\n".join(params.describe(conn, "setA", "Dummy_Map", max_cells=4))
    assert "min 0 max 50" in small and "graphify params show Dummy_Map" in small
    assert params.describe(conn, "setA", "Nope") is None
    # re-import replaces
    assert params.import_par(str(par), db, "setA")["replaced_existing"] is True
    assert params.resolve_dataset(conn, None) == ("setA", None)
    assert params.run_params(["show", "Gain", "--db", str(db)]) == 0
    assert "Gain" in capsys.readouterr().out
    assert params.run_params(["show", "Nope", "--db", str(db)]) == 1


def test_explain_inputs_prints_values(monkeypatch, tmp_path, capsys):
    par = tmp_path / "a.par"
    par.write_text(PAR, encoding="utf-8")
    params.import_par(str(par), tmp_path / "params.db", "setA", A2L)
    data = {
        "directed": False, "multigraph": False, "graph": {},
        "nodes": [
            {"id": "F", "label": "F()", "type": "function", "source_file": "k.c", "source_location": "L1", "community": 0},
            {"id": "C", "label": "k_gain", "type": "calibration_field", "source_file": "k.c", "community": 0,
             "metadata": {"a2l_characteristic_name": "Gain"}},
        ],
        "links": [{"source": "F", "target": "C", "relation": "reads_calibration_field",
                   "confidence": "EXTRACTED"}],
    }
    g = tmp_path / "graph.json"
    g.write_text(json.dumps(data))
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "explain", "F", "--inputs", "--graph", str(g),
                                              "--dataset", "setA"])
    try:
        mainmod.main()
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "calibration k_gain" in out and "Gain [FLOAT] = 1.5 km/h" in out
