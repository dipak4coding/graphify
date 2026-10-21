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


def test_names_with_index_and_flexible_value_layout():
    text = ("variable_ko[3] [FLOAT]  2.5 ; 2.5\n"
            "arr[x][INT(16)]  7 ; 7\n"
            "Curve_A [INT(16),(1,4)]  0 ; 0 :  100 ; 10 :  200 ; 20 :  300 ; 30\n"
            "Curve_B [INT(16),(1,4)]  0 ; 0\n"
            "  100 ; 10\n"
            ":  200 ; 20\n"
            "300 ; 30\n"
            "Curve_C [INT(16),(4)]  1 ; 1 : 2 ; 2 : 3 ; 3 : 4 ; 4\n")
    entries, _, unparsed = params.parse_par(text)
    by = {e.name: e for e in entries}
    assert set(by) == {"variable_ko[3]", "arr[x]", "Curve_A", "Curve_B", "Curve_C"} and not unparsed
    assert by["variable_ko[3]"].dtype == "FLOAT" and by["arr[x]"].phys == [7]
    for n in ("Curve_A", "Curve_B"):
        assert by[n].phys == [0, 10, 20, 30] and not by[n].issues and by[n].kind == "curve"
    assert (by["Curve_C"].rows, by["Curve_C"].cols) == (1, 4) and not by["Curve_C"].issues
    a2l = {"characteristics": {"variable_ko": {"char_type": "VALUE", "axis_references": []}}}
    rep, _ = params.analyze(entries, a2l)
    assert "variable_ko[3]" not in rep["not_in_a2l"]


STRUCT = """\
KusLC_AuswTasteBetaetigt_ko[DS].Keine [UINT(1)]  1 ; Ja
KusLC_AuswTasteBetaetigt_ko[DS].LenkradBeide [UINT(1)]  0 ; Nein
KusLC_AuswTasteBetaetigt_ko[TT].Keine [UINT(1)]  1 ; Ja
KusLC_AuswTasteBetaetigt_ko[TT].LenkradBeide [UINT(1)]  0 ; Nein
"""


def test_array_of_bit_structs_parsed_and_linked():
    entries, _, unparsed = params.parse_par(STRUCT)
    assert not unparsed and len(entries) == 4
    e = entries[0]
    assert e.name == "KusLC_AuswTasteBetaetigt_ko[DS].Keine" and e.dtype == "UINT(1)"
    assert (e.raw, e.phys, e.kind, e.parent) == ([1], ["Ja"], "bit", "KusLC_AuswTasteBetaetigt_ko[DS]")
    # A2L variant 1: one characteristic per member, without the index
    a2l = {"characteristics": {"KusLC_AuswTasteBetaetigt_ko.Keine": {"char_type": "VALUE"},
                               "KusLC_AuswTasteBetaetigt_ko.LenkradBeide": {"char_type": "VALUE"}}}
    rep, enrich = params.analyze(entries, a2l)
    assert rep["not_in_a2l"] == [] and rep["a2l_without_value"] == []
    # A2L variant 2: full name incl. index
    a2l2 = {"characteristics": {"KusLC_AuswTasteBetaetigt_ko[DS].Keine": {"char_type": "VALUE"}}}
    rep2, _ = params.analyze(entries, a2l2)
    assert "KusLC_AuswTasteBetaetigt_ko[DS].Keine" not in rep2["not_in_a2l"] and len(rep2["not_in_a2l"]) == 3


def test_all_a2l_nodes_kept_and_struct_variable_is_not_a_match():
    from graphify.extractors.a2l import join_a2l
    a2l = {"measurements": {}, "compu_methods": {}, "compu_vtabs": {},
           "axis_pts": {"Ax": {"internal_symbol": "Ax"}},
           "characteristics": {
               "variable_ko.x": {"char_type": "VALUE", "internal_symbol": "variable_ko.x", "axis_references": []},
               "variable_ko.y": {"char_type": "VALUE", "internal_symbol": "variable_ko.y", "axis_references": []}}}
    nodes = [{"id": "v", "label": "variable_ko", "type": "variable", "source_file": "k.c", "metadata": {}}]
    edges: list = []
    stats = join_a2l(nodes, edges, a2l, "x.a2l")
    assert "a2l_members" not in nodes[0]["metadata"]
    assert not any(e["source"] == "v" for e in edges)                      # the struct is not linked
    a2l_nodes = {n["label"]: n for n in nodes if n.get("type") in ("a2l_characteristic", "a2l_axis")}
    assert set(a2l_nodes) == {"variable_ko.x", "variable_ko.y", "Ax"}      # every A2L entry is in the graph
    assert all(n["metadata"]["code_matched"] is False for n in a2l_nodes.values())
    assert sorted(stats["unmatched_a2l"]) == ["variable_ko.x", "variable_ko.y"]
    rows = {r["name"]: r for r in params.coverage_rows(a2l, [], nodes)}
    assert rows["variable_ko.x"]["code"] is False
    assert "code has variable 'variable_ko'" in rows["variable_ko.x"]["note"]


def test_code_matched_flag_true_for_real_match():
    from graphify.extractors.a2l import join_a2l
    a2l = {"measurements": {}, "compu_methods": {}, "compu_vtabs": {}, "axis_pts": {},
           "characteristics": {"Gain": {"char_type": "VALUE", "internal_symbol": "Cal.k_gain", "axis_references": []},
                               "Other": {"char_type": "VALUE", "internal_symbol": "Cal.k_other", "axis_references": []}}}
    nodes = [{"id": "c", "label": "Cal.k_gain", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.k_gain"}}]
    join_a2l(nodes, [], a2l, "x.a2l")
    flags = {n["label"]: n["metadata"]["code_matched"] for n in nodes if n.get("type") == "a2l_characteristic"}
    assert flags == {"Gain": True, "Other": False}


def test_one_code_node_links_to_all_elements_by_plain_name():
    from graphify.extractors.a2l import join_a2l
    chars = {}
    for i, (n1, n2) in enumerate((("K1", "P1"), ("K1", "P2"), ("K2", "P1"), ("K2", "P2"))):
        chars[f"Tab_ko[{n1}][{n2}]"] = {"char_type": "VALUE", "internal_symbol": f"Cal.Tab_ko[{i // 2}][{i % 2}]",
                                       "axis_references": [], "min_value": 0.0, "max_value": 10.0 * (i + 1)}
    chars["Cal.var_ko.x"] = {"char_type": "VALUE", "internal_symbol": "Cal.var_ko.x", "axis_references": []}
    chars["Cal.var_ko.y"] = {"char_type": "VALUE", "internal_symbol": "Cal.var_ko.y", "axis_references": []}
    chars["Flag_a"] = {"char_type": "VALUE", "internal_symbol": "Cal.Flags#1", "axis_references": []}
    chars["Flag_b"] = {"char_type": "VALUE", "internal_symbol": "Cal.Flags#2", "axis_references": []}
    a2l = {"measurements": {}, "compu_methods": {}, "compu_vtabs": {}, "axis_pts": {}, "characteristics": chars}
    nodes = [{"id": "t", "label": "Cal.Tab_ko", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.Tab_ko"}},
             {"id": "x", "label": "Cal.var_ko.x", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.var_ko.x"}},
             {"id": "f", "label": "Cal.Flags", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.Flags"}},
             {"id": "n", "label": "Cal.nothing", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.nothing"}}]
    edges: list = []
    stats = join_a2l(nodes, edges, a2l, "x.a2l")
    t = nodes[0]["metadata"]
    assert t["a2l_member_count"] == 4 and "a2l_characteristic_name" not in t
    assert t["a2l_members"] == sorted(n for n in chars if n.startswith("Tab_ko"))      # characteristic names kept as text
    assert t["a2l_range"] == "0.0..40.0"
    links = {e["target"]: e["metadata"] for e in edges if e["source"] == "t"}
    assert links["a2lchar_tab_ko_k2_p1"] == {"characteristic": "Tab_ko[K2][P1]", "index": "[1][0]"}
    assert nodes[1]["metadata"]["a2l_characteristic_name"] == "Cal.var_ko.x"             # struct members stay separate
    assert not any(e["source"] == "x" and e["target"].endswith("var_ko_y") for e in edges)
    assert {e["metadata"].get("mask") for e in edges if e["source"] == "f"} == {1, 2}
    assert stats["unmatched_code"] == ["Cal.nothing"] and stats["unmatched_a2l"] == ["Cal.var_ko.y"]
    rows = {r["name"]: r for r in params.coverage_rows(a2l, [], nodes)}
    assert rows["Tab_ko[K1][P2]"]["code"] is True and rows["Cal.var_ko.y"]["code"] is False


def test_mask_characteristics_keep_index_and_mask_on_links():
    from graphify.extractors.a2l import Characteristic, join_a2l, patch_index
    chars = {}
    for name, mask in (("variable.k1", "0x1"), ("variable.k2", "0x2")):
        c = Characteristic(char_type="VALUE", symbol_link=patch_index("Cal.variable._0_"), bit_mask=mask)
        chars[name] = {"char_type": "VALUE", "internal_symbol": c.internal_symbol, "axis_references": []}
    assert chars["variable.k2"]["internal_symbol"] == "Cal.variable[0]#2"
    a2l = {"measurements": {}, "compu_methods": {}, "compu_vtabs": {}, "axis_pts": {}, "characteristics": chars}
    nodes = [{"id": "v", "label": "Cal.variable", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "Cal.variable"}}]
    edges: list = []
    join_a2l(nodes, edges, a2l, "x.a2l")
    links = sorted((e["metadata"]["characteristic"], e["metadata"]["index"], e["metadata"]["mask"])
                   for e in edges if e["source"] == "v")
    assert links == [("variable.k1", "[0]", 1), ("variable.k2", "[0]", 2)]
    assert nodes[0]["metadata"]["a2l_members"] == ["variable.k1", "variable.k2"]


def test_plain_variable_never_links_to_characteristic_and_single_part_symbol_is_flagged():
    from graphify.extractors.a2l import join_a2l
    a2l = {"measurements": {}, "compu_methods": {}, "compu_vtabs": {}, "axis_pts": {},
           "characteristics": {"Odd": {"char_type": "VALUE", "internal_symbol": "odd_var", "axis_references": []}}}
    nodes = [{"id": "v", "label": "odd_var", "type": "variable", "source_file": "k.c", "metadata": {}}]
    edges: list = []
    join_a2l(nodes, edges, a2l, "x.a2l")
    assert not any(e["source"] == "v" for e in edges) and "a2l_characteristic_name" not in nodes[0]["metadata"]
    rows = {r["name"]: r for r in params.coverage_rows(a2l, [], nodes)}
    assert "single part" in rows["Odd"]["note"]


def test_two_characteristics_with_same_symbol_link_and_different_bit_masks(tmp_path):
    from graphify.extractors.a2l import join_a2l, scan_a2l
    block = ('/begin CHARACTERISTIC SystemReaction.Error_%s\n"Dummy reaction"\n'
             'VALUE 0x10001000 __UWORD_S 0 OffOn 0 1\nBIT_MASK %s\nFORMAT "%%.0"\n'
             'SYMBOL_LINK "DummyROM.ReactionMask" 0\n/end CHARACTERISTIC\n')
    f = tmp_path / "m.a2l"
    f.write_text('/begin PROJECT P ""\n/begin MODULE M ""\n' + block % ("A", "0x1000") + block % ("B", "0x0800")
                 + '/end MODULE\n/end PROJECT\n', encoding="utf-8")
    parsed = scan_a2l(str(f))
    nodes = [{"id": "v", "label": "DummyROM.ReactionMask", "type": "calibration_field", "source_file": "k.c",
              "metadata": {"calibration_path": "DummyROM.ReactionMask"}}]
    edges: list = []
    join_a2l(nodes, edges, parsed, "m.a2l")
    got = sorted((e["metadata"]["characteristic"], e["metadata"]["mask"]) for e in edges if e["source"] == "v")
    assert got == [("SystemReaction.Error_A", 4096), ("SystemReaction.Error_B", 2048)]
