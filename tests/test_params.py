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


def _nodes():
    return [
        {"id": "c1", "label": "k_gain", "type": "calibration_field", "source_file": "k.c", "source_location": "L3",
         "metadata": {"a2l_characteristic_name": "Gain", "calibration_path": "k_gain"}},
        {"id": "c2", "label": "k_orphan", "type": "calibration_field", "source_file": "k.c", "source_location": "L4",
         "metadata": {"calibration_path": "k_orphan"}},
        {"id": "v1", "label": "DUMMY_MAP", "type": "variable", "source_file": "k.c", "source_location": "L5",
         "metadata": {"calibration_path": "DUMMY_MAP"}},
        {"id": "doc", "label": "Gain", "type": "a2l_characteristic", "file_type": "document", "metadata": {}},
    ]


def test_coverage_rows_three_columns_and_status():
    a2l = json.loads(json.dumps(A2L))
    a2l["characteristics"]["Dummy_Map"]["internal_symbol"] = "Dummy_Map"
    entries, _, _ = params.parse_par(PAR)
    rows = {r["name"]: r for r in params.coverage_rows(a2l, entries, _nodes())}
    assert (rows["Gain"]["a2l"], rows["Gain"]["par"], rows["Gain"]["code"], rows["Gain"]["status"]) == (True, True, True, "ok")
    assert rows["Mode"]["status"] == "not found in code"
    assert rows["Sw"]["par"] is True and rows["Sw"]["status"] == "not found in code"      # bits count for the parent
    assert rows["Lonely"]["status"] == "A2L only"
    assert rows["Extra"]["status"] == "PAR only"
    assert rows["k_orphan"]["status"] == "code only" and rows["k_orphan"]["code"] is True
    assert "looks the same" in rows["Dummy_Map"]["note"]       # code DUMMY_MAP vs A2L Dummy_Map: join missed it
    assert rows["Ax_X"]["kind"] == "AXIS"


def test_extraction_step_writes_databank_and_reports(tmp_path):
    par = tmp_path / "a.par"
    par.write_text(PAR, encoding="utf-8")
    out = tmp_path / "out"
    r = params.extraction_step(str(par), A2L, _nodes(), out, "setA")
    assert r["totals"]["in_par"] == 7 and (out / "params.db").is_file()
    csv_text = (out / "param_coverage.csv").read_text(encoding="utf-8-sig")
    assert csv_text.startswith("name;kind;a2l;par;code;status") and "Gain;CHAR;yes;yes;yes;ok" in csv_text
    assert (out / "param_coverage.json").is_file() and (out / "params_import_setA.json").is_file()
    # without an A2L the values are stored but no coverage report is written
    r2 = params.extraction_step(str(par), None, _nodes(), tmp_path / "out2", "setB")
    assert "csv" not in r2 and (tmp_path / "out2" / "params.db").is_file()


def test_cli_consumes_par_flags(monkeypatch):
    from graphify import cli
    from graphify.extractors import clang_c
    monkeypatch.setattr(cli.sys, "argv", ["graphify", "update", ".", "--par", "x.par", "--par-name", "setZ"])
    seen = {}
    monkeypatch.setattr(clang_c, "set_runtime_config", lambda **kw: seen.update(kw))
    cli._consume_clang_flags()
    assert seen["par_name"] == "setZ" and seen["par"].endswith("x.par") and cli.sys.argv == ["graphify", "update", "."]
