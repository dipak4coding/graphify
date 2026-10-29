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
    assert "[[Vwh_Oel]] `kku/kku.c:L88`" in note and "Berechnet die Oeltemperatur." in note       # writer + its summary
    assert "[[Kue_Stelle]] `kku/kku.c:L130`" in note                                              # reader of the output
    assert "| [[KkuAppROM.SW_T_Oel]] | `Kku_SW_T_Oel` | - | VALUE | 0.0..150.0 | Kku_SW_T_Oel [FLOAT] = 95 degC | exact |" in note
    assert "2 entries, e.g. `Flag_a`, `Flag_b`" in note and "[i] · 0x2" in note                     # many A2L entries, index and mask
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
    assert "`Kku_SW_T_Oel` | - | VALUE | 0.0..150.0 | Kku_SW_T_Oel [FLOAT] = 95" in f
    assert "border/Ipo_2D_S8.md" in notes and "variables/KkuAppPtr.md" not in notes


def test_mojibake_in_stored_doxygen_is_repaired_on_export():
    g = _graph()
    bad = "/** PrÃ¼fen fÃ¼r ZustÃ¤nde. */"
    g["nodes"][0]["metadata"]["doxygen"] = bad
    note = swdoc.build_vault(g, ["kku/"], lookup=_lookup, dataset="SetA")["notes"]["functions/Kku_Calc.md"]
    assert "Prüfen für Zustände" in note and "Ã" not in note
