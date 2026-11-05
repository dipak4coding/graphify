"""`graphify swdoc`: Obsidian software-documentation vault from the graph."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from graphify import swdoc


def _n(i, label, typ, sf="kku/kku.c", loc="L1", **meta):
    n = {"id": i, "label": label, "type": typ, "source_file": sf, "source_location": loc}
    if meta:
        n["metadata"] = meta
    return n


def _e(s, t, rel, loc="L5", conf="EXTRACTED", **meta):
    e = {"source": s, "target": t, "relation": rel, "confidence": conf, "source_file": "kku/kku.c", "source_location": loc}
    if meta:
        e["metadata"] = meta
    return e


def _graph():
    nodes = [
        _n("F", "Kku_Calc()", "function", loc="L10", has_definition=True, return_type="T_U8",
           parameters=[{"name": "Arg_Modus", "type": "T_U8", "role": "in"}],
           doxygen="/** Berechnet die Kuehlanforderung.\n Zweite Zeile */"),
        _n("W", "Vwh_Oel()", "function", sf="vwh/oh.c", has_definition=True, doxygen="/** Berechnet die Oeltemperatur. */"),
        _n("R", "Kue_Stelle()", "function", sf="kue/kue.c", has_definition=True),
        _n("P", "Bios_Write()", "function", sf="kku/kku.h", declaration_only=True),
        _n("Vin", "Kku_T_Oel", "variable", storage="global", defined_in="vwh/oh.c"),
        _n("Vout", "Kku_Anf", "variable", storage="global", defined_in="kku/kku.c"),
        _n("Ptr", "KkuAppPtr", "variable"), _n("Rom", "KkuAppROM", "variable"),
        _n("C1", "KkuAppROM.SW_T_Oel", "calibration_field", calibration_path="KkuAppROM.SW_T_Oel", accessed_via="KkuAppPtr",
           doxygen="/** Schwelle */"),
        _n("C2", "KkuAppROM.Flags", "calibration_field", calibration_path="KkuAppROM.Flags", accessed_via="KkuAppPtr"),
        _n("A1", "Kku_SW_T_Oel", "a2l_characteristic", sf="x.a2l", char_type="VALUE", min=0.0, max=150.0),
        _n("A2", "Flag_a", "a2l_characteristic", sf="x.a2l", char_type="VALUE", min=0, max=1),
        _n("A3", "Flag_b", "a2l_characteristic", sf="x.a2l", char_type="VALUE", min=0, max=1),
    ]
    edges = [
        _e("F", "Vin", "reads_var"), _e("F", "Vout", "writes_var"), _e("F", "Ptr", "reads_var"),
        _e("W", "Vin", "writes_var", "L88"), _e("R", "Vout", "reads_var", "L130"),
        _e("F", "P", "calls"), _e("R", "F", "calls"),
        _e("F", "C1", "reads_calibration_field"), _e("F", "C2", "reads_calibration_field", index=["[i]"], mask=[2]),
        _e("Ptr", "C1", "exposes_calibration_field"),
        _e("C1", "A1", "mapped_to_a2l_characteristic", characteristic="Kku_SW_T_Oel"),
        _e("C2", "A2", "mapped_to_a2l_characteristic", characteristic="Flag_a", mask=1),
        _e("C2", "A3", "mapped_to_a2l_characteristic", characteristic="Flag_b", mask=2),
    ]
    return {"directed": False, "nodes": nodes, "links": edges}


def _lookup(name):
    return {"Kku_SW_T_Oel": ["Kku_SW_T_Oel [FLOAT] = 95 degC"]}.get(name)


def test_scope_and_note_types():
    r = swdoc.build_vault(_graph(), ["kku/"], lookup=_lookup, dataset="SetA")
    assert r["counts"] == {"functions": 1, "variables": 2, "calibrations": 2, "axes": 0, "border": 3}
    paths = set(r["notes"])
    assert {"functions/Kku_Calc.md", "variables/Kku_T_Oel.md", "variables/Kku_Anf.md", "calibrations/KkuAppROM.SW_T_Oel.md",
            "calibrations/KkuAppROM.Flags.md", "border/Vwh_Oel.md", "border/Kue_Stelle.md", "border/Bios_Write.md",
            "modules/kku.md"} == paths                                  # ROM pointer and ROM struct are plumbing: no notes
    assert "variables/KkuAppPtr.md" not in paths and "border/Bios_Write.md" in paths   # a header prototype is border


def test_function_note_waterfall_content():
    note = swdoc.build_vault(_graph(), ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["functions/Kku_Calc.md"]
    fn = note.split("### 2 · This function")[0]
    assert "| Name | Description | Range | Value / enum table | Unit |" in fn                             # uniform signal tables
    assert "| [[Kku_T_Oel]] |" in fn and "| [[Kku_Anf]] |" in note
    assert "| [[KkuAppROM.SW_T_Oel]]<br>`Kku_SW_T_Oel` | Schwelle | 0.0 … 150.0 | **95 degC** |" in fn   # name+A2L name, description, range, value
    assert "Written by" not in note and "Producer summary" not in note                            # old columns are gone
    assert "[[KkuAppROM.Flags]]<br>`Flag_a` +1" in fn and "2 entries - see [[KkuAppROM.Flags]]" in fn
    assert "KkuAppPtr" not in note.split("## Software documentation")[0]                          # plumbing hidden
    assert "Berechnet die Kuehlanforderung." in note and "`Arg_Modus`" in note
    assert "[[Bios_Write]]" in note and "#### Callers" in note and "[[Kue_Stelle]]" in note


def test_calibration_note_lists_entries_masks_and_readers():
    note = swdoc.build_vault(_graph(), ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["calibrations/KkuAppROM.Flags.md"]
    assert "| `Flag_a` | - | 0x1 | VALUE |" in note and "| `Flag_b` | - | 0x2 | VALUE |" in note
    assert "| [[Kku_Calc]] | `kku/kku.c:L5` | [i] | 0x2 | EXTRACTED |" in note


def test_every_wikilink_resolves_to_a_note():
    r = swdoc.build_vault(_graph(), ["kku/"], lookup=_lookup, dataset="SetA", mermaid=True)
    stems = {Path(p).stem for p in r["notes"]} | {"_HOME"}
    for path, text in r["notes"].items():
        for target in re.findall(r"\[\[([^\]|]+)", text):
            assert target in stems, f"{path}: dangling link [[{target}]]"
    assert "```mermaid" in r["notes"]["functions/Kku_Calc.md"]


def test_struct_member_calibration_points_to_its_struct():
    g = _graph()
    g["nodes"].append(_n("M", "KkuAppROM.Feh.Thr", "calibration_field", calibration_path="KkuAppROM.Feh.Thr",
                         from_struct_type="KkuAppROM.Feh", doxygen="/** Schwelle in ms */"))
    g["nodes"].append(_n("S", "KkuAppROM.Feh", "calibration_field", calibration_path="KkuAppROM.Feh"))
    g["links"] += [_e("F", "M", "reads_calibration_field", conf="INFERRED", via="struct_type"), _e("F", "S", "reads_calibration_field")]
    notes = swdoc.build_vault(g, ["kku/"])["notes"]
    assert "Member of the struct [[KkuAppROM.Feh]]" in notes["calibrations/KkuAppROM.Feh.Thr.md"]
    assert "Schwelle in ms" in notes["calibrations/KkuAppROM.Feh.Thr.md"]
    assert "[[KkuAppROM.Feh.Thr]]" in notes["calibrations/KkuAppROM.Feh.md"]                  # struct lists its members


def test_name_clash_gets_a_suffix_and_empty_scope_is_reported():
    g = _graph()
    g["nodes"].append(_n("F2", "Kku_Anf()", "function", sf="kku/other.c", has_definition=True))   # same name as variable Kku_Anf
    r = swdoc.build_vault(g, ["kku/"])
    names = {Path(p).stem for p in r["notes"]}
    assert "Kku_Anf" in names and "Kku_Anf (kku)" in names             # functions are named first; the variable gets its file as suffix
    assert len([p for p in r["notes"] if Path(p).stem.startswith("Kku_Anf")]) == 2
    assert swdoc.build_vault(g, ["nothing-here"])["empty"] is True


def test_cli_keeps_user_block_never_overwrites_foreign_files_and_prunes_one_module(tmp_path, capsys):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graph()))
    out = tmp_path / "vault"
    assert swdoc.run_swdoc(["kku/", "--graph", str(gp), "--out", str(out)]) == 0
    note = out / "functions" / "Kku_Calc.md"
    note.write_text(note.read_text().replace(swdoc.USER_BLOCK, swdoc.USER_BLOCK + "my text about Kku_Calc\n"))
    foreign = out / "variables" / "Kku_Anf.md"
    foreign.write_text("the user's own note")
    manifest = json.loads((out / swdoc.MANIFEST).read_text())
    manifest["files"].pop("variables/Kku_Anf.md")                      # graphify does not own it
    (out / swdoc.MANIFEST).write_text(json.dumps(manifest))
    assert swdoc.run_swdoc(["kku/", "--graph", str(gp), "--out", str(out)]) == 0
    assert "my text about Kku_Calc" in note.read_text()               # user block survives the re-export
    assert foreign.read_text() == "the user's own note"               # foreign file is never overwritten
    assert "skipped 1 existing" in capsys.readouterr().out
    # a second module in the same vault; --prune of kku keeps it
    g2 = _graph()
    g2["nodes"].append(_n("G", "Kue_Run()", "function", sf="kue/kue.c", has_definition=True))
    gp2 = tmp_path / "graph2.json"
    gp2.write_text(json.dumps(g2))
    assert swdoc.run_swdoc(["kue/", "--graph", str(gp2), "--out", str(out)]) == 0
    assert (out / "functions" / "Kue_Run.md").is_file()
    assert swdoc.run_swdoc(["kku/", "--graph", str(gp), "--out", str(out), "--prune"]) == 0
    assert (out / "functions" / "Kue_Run.md").is_file() and (out / "functions" / "Kku_Calc.md").is_file()
    home = (out / "_HOME.md").read_text()
    assert "[[kku]]" in home and "[[kue]]" in home


def test_export_swdoc_alias_and_usage(monkeypatch, tmp_path, capsys):
    import graphify.__main__ as mainmod
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graph()))
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "export", "swdoc", "kku/", "--graph", str(gp), "--out", str(tmp_path / "v")])
    with pytest.raises(SystemExit) as exc:
        mainmod.main()
    assert exc.value.code == 0 and (tmp_path / "v" / "_HOME.md").is_file()
    assert swdoc.run_swdoc([]) == 1


def test_toy_project_end_to_end(tmp_path):
    """Real clang + A2L + .par graph: the vault has no dangling links and shows the dataset value."""
    pytest.importorskip("clang.cindex")
    from clang import cindex
    try:
        cindex.Index.create()
    except Exception:
        pytest.skip("libclang shared library not loadable")
    from graphify import params
    from graphify.extract import extract
    from graphify.extractors.a2l import scan_a2l
    src = Path(__file__).parent / "fixtures" / "clang_toy"
    for f in src.iterdir():
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"a2l": "toy.a2l", "extra_args": ["-I."]}), encoding="utf-8")
    (tmp_path / "toy.par").write_text("Kku_SW_T_Oel [FLOAT]  95 ; 95\n", encoding="utf-8")
    res = extract(sorted(tmp_path.glob("*.c")), root=tmp_path)
    gp = tmp_path / "out" / "graph.json"
    gp.parent.mkdir()
    gp.write_text(json.dumps({"directed": False, "nodes": res["nodes"], "links": res["edges"]}))
    params.import_par(str(tmp_path / "toy.par"), gp.parent / "params.db", "SetA", scan_a2l(str(tmp_path / "toy.a2l")))
    out = tmp_path / "vault"
    assert swdoc.run_swdoc(["kku", "--graph", str(gp), "--dataset", "SetA", "--out", str(out)]) == 0
    notes = {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    stems = {Path(p).stem for p in notes}
    for path, text in notes.items():
        for target in re.findall(r"\[\[([^\]|]+)", text):
            assert target in stems, f"{path}: dangling link [[{target}]]"
    f = notes["functions/Kku_BerKuehlAnf.md"]
    assert "`Kku_SW_T_Oel`" in f and "0.0 … 150.0" in f and "**95" in f
    assert "border/Ipo_2D_S8.md" in notes and "variables/KkuAppPtr.md" not in notes


def test_mojibake_in_stored_doxygen_is_repaired_on_export():
    g = _graph()
    bad = "/** PrÃ¼fen fÃ¼r ZustÃ¤nde. */"
    g["nodes"][0]["metadata"]["doxygen"] = bad
    note = swdoc.build_vault(g, ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["functions/Kku_Calc.md"]
    assert "Prüfen für Zustände" in note and "Ã" not in note


def test_function_page_shows_unit_enum_and_map_inputs():
    g = _graph()
    g["nodes"] += [
        _n("Cv", "KkuAppROM.Kf", "calibration_field", calibration_path="KkuAppROM.Kf", accessed_via="KkuAppPtr"),
        _n("Ax", "KkuAppROM.Kf_x", "calibration_field", calibration_path="KkuAppROM.Kf_x", accessed_via="KkuAppPtr"),
        _n("Ac", "Kf", "a2l_characteristic", sf="x.a2l", char_type="CURVE", min=0, max=100, unit="%"),
        _n("Aa", "Kf_x", "a2l_axis", sf="x.a2l", min=-40, max=150, unit="degC", input_quantity="Kku_T_Oel"),
        _n("Mode", "Kku_Mode", "variable", storage="global", defined_in="kku/kku.c",
           calibration_ref={"a2l_name": "Kku_Mode", "min": 0, "max": 2, "compu_method": {"type": "TAB_VERB", "unit": ""},
                            "verbal_table": {"0": "Aus", "1": "Ein", "2": "Fehler"}}),
    ]
    g["links"] += [
        _e("F", "Cv", "reads_calibration_field"), _e("F", "Mode", "reads_var"),
        _e("Cv", "Ax", "x_axis"), _e("Cv", "Vin", "input_x"),
        _e("Cv", "Ac", "mapped_to_a2l_characteristic", characteristic="Kf"),
        _e("Ax", "Aa", "mapped_to_a2l_axis_pts", axis="Kf_x"),
    ]
    note = swdoc.build_vault(g, ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["functions/Kku_Calc.md"]
    assert "| [[Kku_Mode]] |  | 0 … 2 | `0=Aus; 1=Ein; 2=Fehler` |  |" in note                  # enum table, no unit
    assert "| [[KkuAppROM.Kf]]<br>`Kf` |" in note and "| 0 … 100 |" in note and "| % |" in note       # curve with unit
    assert "#### Inputs of the maps and curves" in note
    assert "| [[KkuAppROM.Kf]] | X | `KkuAppROM.Kf_x` | [[Kku_T_Oel]] |" in note              # axis + input variable


def test_module_note_call_flow_signal_tables_and_calibrations():
    g = _graph()
    g["nodes"] += [_n("G", "Kku_Hilf()", "function", loc="L50", has_definition=True),
                   _n("Loc", "Kku_Zwischen", "variable", storage="static", defined_in="kku/kku.c")]
    g["links"] += [_e("F", "Loc", "writes_var"), _e("G", "Loc", "reads_var"), _e("F", "G", "calls")]
    note = swdoc.build_vault(g, ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["modules/kku.md"]
    assert "## Call flow" in note and "flowchart LR" in note
    flow = note.split("```mermaid")[1].split("```")[0]
    assert '["Kku_Calc"]' in flow and '["Kku_Hilf"]' in flow and flow.count("-->") == 3   # caller group -> F, F -> G, F -> callee group (header prototype)
    assert "  class f1,f2 internal-link;" in flow or "internal-link;" in flow             # blocks resolve to their notes
    for stem in ("Kku_Calc", "Kku_Hilf"):
        assert f'["{stem}"]' in flow
    assert "Signal matrix" not in note and "Local signals" not in note
    for gone in ("Inputs from other modules", "Variables read outside this module", "connected", "## Interface"):
        assert gone not in note
    assert "### Component inputs" in note and "| [[Kku_T_Oel]] |" in note and "[[Vwh_Oel]]" in note
    assert "### Component outputs" in note and "[[Kue_Stelle]]" in note
    cal = note.split("## Calibrations")[1].split("## ")[0]
    assert "| Name | Description | Range | Value / enum table | Unit | Read by |" in cal
    assert "| [[KkuAppROM.SW_T_Oel]]<br>`Kku_SW_T_Oel` | Schwelle | 0.0 … 150.0 | **95 degC** |" in cal


def test_function_diagram_blocks_are_note_links_too():
    note = swdoc.build_vault(_graph(), ["kku/"], lookup=_lookup, dataset="SetA", mermaid=True)["notes"]["functions/Kku_Calc.md"]
    block = note.split("```mermaid")[1].split("```")[0]
    assert '["Kku_Calc"]' in block and '("Kku_T_Oel")' in block and '{{"KkuAppROM.SW_T_Oel"}}' in block   # note names as block text
    cls = re.search(r"class ([\w,]+) internal-link;", block)
    assert cls and len(cls.group(1).split(",")) >= 5


def test_single_file_merges_everything_with_heading_links(tmp_path, monkeypatch):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graph()), encoding="utf-8")
    out = tmp_path / "vault"
    assert swdoc.run_swdoc(["kku/", "--module", "Software", "--graph", str(gp), "--out", str(out), "--single-file",
                            "--mermaid"]) == 0
    files = sorted(p.name for p in out.rglob("*") if p.is_file() and not p.name.startswith("."))
    assert files == ["Software.md", "_HOME.md"]                                      # ONE note file (plus the home page)
    text = (out / "Software.md").read_text(encoding="utf-8")
    heads = set(re.findall(r"^#{2,3} (.+)$", text, flags=re.M))
    assert {"Overview", "Functions", "Variables", "Calibrations", "Kku_Calc", "Kku_T_Oel", "KkuAppROM.SW_T_Oel"} <= heads
    for target in re.findall(r"\[\[#([^\]|]+)\]\]", text):
        assert target in heads, f"dangling heading link [[#{target}]]"
    assert "[[Kku_Calc]]" not in text and "internal-link" not in text and "Software documentation (to be filled" not in text
    assert text.count("<!-- graphify:user-block") == 1 and text.rstrip().endswith("-->")      # one user block, at the end
    assert "| Name | Description | Range | Value / enum table | Unit |" in text
    allheads = re.findall(r"^#{2,6} (.+)$", text, flags=re.M)
    assert all(allheads.count(h) == 1 for h in ("Functions", "Calibrations", "Overview", "Kku_Calc"))   # unique link targets
    assert "opens its note" not in text


def test_dot_pattern_selects_the_whole_software():
    r = swdoc.build_vault(_graph(), ["."], lookup=_lookup, dataset="SetA")
    assert not r["empty"] and r["counts"]["functions"] >= 3


def test_html_export_is_one_self_contained_file_with_all_items(tmp_path):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graph()), encoding="utf-8")
    assert swdoc.run_swdoc([".", "--module", "Software", "--graph", str(gp), "--out", str(tmp_path / "o"), "--html"]) == 0
    html = (tmp_path / "o" / "Software.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "src=" not in html and "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(blob.replace("<\\/", "</"))
    names = {(it["t"], it["n"]) for it in data["it"]}
    assert {("f", "Kku_Calc"), ("v", "Kku_T_Oel"), ("c", "KkuAppROM.SW_T_Oel"), ("b", "Bios_Write")} <= names
    calc = next(it for it in data["it"] if it["n"] == "Kku_Calc")
    assert calc["rd"] and calc["wr"] and calc["ca"] and calc["cl"] is not None and "doc" in calc
    comps = {c["n"]: c for c in data["comps"]}
    assert {"kku", "kue", "vwh"} <= set(comps) and comps["kku"]["in"] and comps["kku"]["out"]       # component signals


def test_html_pages_render_without_javascript_errors(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graph()), encoding="utf-8")
    swdoc.run_swdoc([".", "--module", "Software", "--graph", str(gp), "--out", str(tmp_path), "--html"])
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        page = b.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        url = (tmp_path / "Software.html").as_uri()
        page.goto(url)
        n = page.evaluate("IT.length")
        for i in range(n):
            page.goto(f"{url}#i/{i}")
            assert page.inner_text("#main h2"), f"page {i} is empty"
        for k in range(page.evaluate("C.length")):
            page.goto(f"{url}#k/{k}")
            assert page.inner_text("#main h2")
        b.close()
    assert not errs, errs


def _seq_graph():
    g = {"directed": False, "links": [], "nodes": []}
    for fid, name, extra in (("Top", "Top()", {}), ("Run", "Run()", {}), ("A", "A()", {}), ("B", "B()", {})):
        g["nodes"].append(_n(fid, name, "function", sf="kku/kku.c", loc="L1", has_definition=True, **extra))
    g["nodes"][0]["metadata"]["call_sequence"] = [{"id": "Run", "line": 5}, {"id": "Run", "line": 6, "ctx": "loop"}]
    g["nodes"][1]["metadata"]["call_sequence"] = [{"id": "B", "line": 12}, {"id": "A", "line": 13, "ctx": "if"}]
    for a, b in (("Top", "Run"), ("Run", "A"), ("Run", "B")):
        g["links"].append(_e(a, b, "calls"))
    return g


def test_calls_in_execution_order_start_points_and_html_trace(tmp_path):
    g = _seq_graph()
    r = swdoc.build_vault(g, ["kku/"])
    run = r["notes"]["functions/Run.md"]
    assert run.index("1. [[B]] `L12`") < run.index("2. [[A]] `L13` - *if*")                     # code order, not alphabetical
    top = r["notes"]["functions/Top.md"]
    assert "2. [[Run]] `L6` - *repeated (loop)*" in top
    mod = r["notes"]["modules/kku.md"]
    sp = mod.split("## Start points")[1].split("## Call flow")[0]
    assert "[[Top]]" in sp and "[[Run]]" not in sp and "3 functions" in sp                       # nobody calls Top; it reaches Run, A, B
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps(g), encoding="utf-8")
    swdoc.run_swdoc([".", "--module", "Software", "--graph", str(gp), "--out", str(tmp_path / "o"), "--html"])
    html = (tmp_path / "o" / "Software.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    names = [it["n"] for it in data["it"]]
    run_it = data["it"][names.index("Run")]
    assert [x[0] for x in run_it["sq"]] == [names.index("B"), names.index("A")] and run_it["sq"][1][2] == "if"
    assert [row[0] for row in data["sp"]] == [names.index("Top")] and data["sp"][0][2] == 3
