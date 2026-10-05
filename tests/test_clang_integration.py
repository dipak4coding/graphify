"""Clang + A2L pass: toy-project tests (skipped when libclang is unavailable)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

cindex = pytest.importorskip("clang.cindex")
try:
    cindex.Index.create()
except Exception:  # pragma: no cover - libclang shared library missing
    pytest.skip("libclang shared library not loadable", allow_module_level=True)

from graphify.extract import extract  # noqa: E402
from graphify.relations import affected_relations, context_for  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "clang_toy"


@pytest.fixture()
def toy(tmp_path):
    for f in FIXTURE.iterdir():
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "graphify-clang.json").write_text(
        json.dumps({"a2l": "toy.a2l", "extra_args": ["-I."]}), encoding="utf-8")
    return tmp_path


def _run(root: Path):
    paths = sorted(root.glob("*.c"))
    result = extract(paths, root=root)
    return result["nodes"], result["edges"]


def _by_label(nodes, label):
    return next(n for n in nodes if n["label"] == label)


def _edge(nodes, edges, src_label, relation, tgt_label):
    s, t = _by_label(nodes, src_label)["id"], _by_label(nodes, tgt_label)["id"]
    return next((e for e in edges if (e["source"], e["target"], e["relation"]) == (s, t, relation)), None)


def test_read_write_collapse_and_context(toy):
    nodes, edges = _run(toy)
    e = _edge(nodes, edges, "Kku_BerKuehlAnf()", "reads_writes_var", "Kku_Anf")
    assert e is not None, "read+write of one variable must be ONE reads_writes_var edge"
    assert e["context"] == "readwrite"
    assert e["source_location"].startswith("L")
    assert not _edge(nodes, edges, "Kku_BerKuehlAnf()", "reads_var", "Kku_Anf")
    assert not _edge(nodes, edges, "Kku_BerKuehlAnf()", "writes_var", "Kku_Anf")


def test_cross_file_reader(toy):
    nodes, edges = _run(toy)
    assert _edge(nodes, edges, "Kku_Send()", "reads_var", "Kku_Out")


def test_calibration_edges_and_ambiguous_expansion(toy):
    nodes, edges = _run(toy)
    assert _edge(nodes, edges, "Kku_BerKuehlAnf()", "reads_calibration_field", "KkuAppROM.SW_T_Oel")
    assert _edge(nodes, edges, "KkuAppPtr", "exposes_calibration_field", "KkuAppROM.SW_T_Oel")
    amb = _edge(nodes, edges, "Kku_Task10ms()", "reads_calibration_field", "KkuAppROM.Kku_Tab_kl[0]")
    assert amb is not None and amb["confidence"] == "AMBIGUOUS"
    flag = _by_label(nodes, "KkuAppROM.Kku_Flags#2")
    assert flag["metadata"]["bit_mask"] == 2


def test_axis_edges(toy):
    nodes, edges = _run(toy)
    assert _edge(nodes, edges, "KkuAppROM.Kku_Tab_kl", "x_axis", "KkuAppROM.Ax_X")
    assert _edge(nodes, edges, "KkuAppROM.Kku_Tab_kl", "input_x", "Kku_T_Oel")


def test_registration_args_are_not_reads(toy):
    nodes, edges = _run(toy)
    assert not _edge(nodes, edges, "Kku_Init()", "reads_var", "KkuAppROM")


def test_a2l_join_and_searchable_text(toy):
    nodes, edges = _run(toy)
    code = _by_label(nodes, "KkuAppROM.SW_T_Oel")
    assert "Oil temperature threshold" in (code.get("rationale") or "")
    assert code["metadata"]["a2l_range"] == "0.0..150.0"
    assert _edge(nodes, edges, "KkuAppROM.SW_T_Oel", "mapped_to_a2l_characteristic", "Kku_SW_T_Oel")
    assert _by_label(nodes, "Axis_T")["type"] == "a2l_axis"


def test_all_items_stamped_ast_so_update_rebuilds_them(toy):
    nodes, edges = _run(toy)
    assert all(n.get("_origin") == "ast" for n in nodes)
    assert all(e.get("_origin") == "ast" for e in edges)


def test_no_config_means_no_clang_items(toy):
    (toy / "graphify-clang.json").unlink()
    nodes, edges = _run(toy)
    assert not any(e["relation"] in ("reads_var", "reads_calibration_field") for e in edges)


def test_registry_feeds_affected_defaults():
    from graphify.affected import DEFAULT_AFFECTED_RELATIONS
    for rel in affected_relations():
        assert rel in DEFAULT_AFFECTED_RELATIONS
    assert context_for("reads_writes_var") == "readwrite"


def test_cli_flags_enable_pass_without_config(toy, monkeypatch):
    import sys
    from graphify import cli
    from graphify.extractors import clang_c
    (toy / "graphify-clang.json").unlink()
    monkeypatch.setattr(sys, "argv", ["graphify", "update", ".", "--clang", "--a2l", str(toy / "toy.a2l")])
    monkeypatch.setattr(clang_c, "_runtime_override", None)
    cli._consume_clang_flags()
    assert sys.argv == ["graphify", "update", "."]
    nodes, edges = _run(toy)
    assert any(e["relation"] == "reads_calibration_field" for e in edges)
    monkeypatch.setattr(clang_c, "_runtime_override", None)


def test_metadata_lines_and_priority():
    from graphify.relations import metadata_lines, relation_priority
    lines = metadata_lines({"type": "calibration_field", "metadata": {"a2l_range": "0..1", "bit_mask": 2}})
    assert any("calibration_field" in line for line in lines)
    assert any("0..1" in line for line in lines)
    assert relation_priority("writes_var") < relation_priority("reads_var") < relation_priority("calls")


def test_pick_seeds_skips_file_nodes():
    import networkx as nx
    from graphify.serve import _pick_seeds
    G = nx.DiGraph()
    G.add_node("f", label="kku.c", source_file="kku.c")
    G.add_node("v", label="Kku_Anf", source_file="kku.h")
    assert _pick_seeds([(5.0, "f"), (4.9, "v")], G=G) == ["v"]


def _extract_with(toy, extractor, debug=False):
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor=extractor, **({"debug": True} if debug else {}))
    try:
        return _run(toy)
    finally:
        clang_c.set_runtime_config()


def test_extractor_treesitter_skips_clang(toy):
    nodes, edges = _extract_with(toy, "treesitter")
    assert not any(e["relation"] in ("reads_var", "reads_calibration_field") for e in edges)


def test_extractor_clang_replaces_treesitter_symbols(toy):
    nodes, edges = _extract_with(toy, "clang")
    fn = _by_label(nodes, "Kku_BerKuehlAnf()")
    assert fn["metadata"]["source_extractor"] == "clang"  # not enriched tree-sitter, replaced
    assert any(n["label"] == "kku.c" for n in nodes), "file nodes are kept"
    assert _edge(nodes, edges, "Kku_BerKuehlAnf()", "reads_writes_var", "Kku_Anf")
    assert not any(e["relation"] == "references" for e in edges), "tree-sitter reference edges dropped"


def test_extractor_clang_keeps_treesitter_for_unparsable_file(toy):
    (toy / "broken.c").write_text("int Broken_Fn(void) { return missing_symbol(; }\n", encoding="utf-8")
    nodes, edges = _extract_with(toy, "clang")
    assert any(n["label"].startswith("Broken_Fn") for n in nodes)


def test_debug_writes_log_and_report(toy):
    _extract_with(toy, "both", debug=True)
    log = toy / "graphify-out" / "clang_debug.log"
    report = toy / "graphify-out" / "clang_report.json"
    assert log.is_file() and report.is_file()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["extractor"] == "both"
    assert data["calibration_pointers"] == {"KkuAppPtr": "KkuAppROM"}
    assert data["per_file"]["kku.c"]["args_source"].startswith("extra_args")
    assert "unmatched_code" in data["a2l_stats"]
    assert "registrations in kku.c" in log.read_text(encoding="utf-8")


def test_extractor_flag_parsing(monkeypatch):
    import sys
    from graphify import cli
    from graphify.extractors import clang_c
    monkeypatch.setattr(sys, "argv", ["graphify", "update", ".", "--extractor", "clang", "--clang-debug"])
    monkeypatch.setattr(clang_c, "_runtime_override", None)
    cli._consume_clang_flags()
    assert sys.argv == ["graphify", "update", "."]
    assert clang_c._runtime_override["extractor"] == "clang" and clang_c._runtime_override["debug"] is True
    monkeypatch.setattr(clang_c, "_runtime_override", None)


def test_compile_commands_relative_include_rsp_and_build_flags(tmp_path):
    """Preprocessing: relative -I against the command's directory, @rsp expansion, -o/-MD dropped."""
    from graphify.extractors.clang_c import ClangConfig, normalize_args
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "flags.rsp").write_text("-I../inc -DFOO=1", encoding="utf-8")
    cfg = ClangConfig(base_dir=str(tmp_path), drop_args=["-mcpu"], append_args=["-Ihw", "-DX=2"])
    args = normalize_args(
        ["tricore-gcc", "-c", "-o", "a.o", "-MD", "-MF", "a.d", "-mcpu=tc39", "@flags.rsp", "../src/a.c"],
        str(tmp_path / "build"), cfg, tmp_path / "src" / "a.c")
    assert "-o" not in args and "-MD" not in args and "-c" not in args and "-mcpu=tc39" not in args
    assert any(a.endswith("inc") and a.startswith("-I") and (tmp_path / "build").as_posix() in a.replace("\\", "/") for a in args)
    assert "-DFOO=1" in args and "-DX=2" in args
    assert any(a.startswith("-I") and a.replace("\\", "/").endswith(f"{tmp_path.name}/hw") for a in args)
    assert not any(a.endswith("a.c") for a in args)


def test_missing_include_is_reported(toy):
    (toy / "needs_hw.c").write_text('#include "no_such_hw.h"\nint Hw_Fn(void){return 1;}\n', encoding="utf-8")
    _extract_with(toy, "both", debug=True)
    data = json.loads((toy / "graphify-out" / "clang_report.json").read_text(encoding="utf-8"))
    assert data["missing_includes_top"].get("no_such_hw.h") == 1


def test_cli_query_depth_flag(toy, monkeypatch, capsys):
    import sys
    from graphify import cli
    _run(toy)  # builds nothing on disk; use update for a graph.json
    monkeypatch.chdir(toy)
    monkeypatch.setattr(sys, "argv", ["graphify", "update", "."])
    from graphify.extractors import clang_c
    clang_c.set_runtime_config()
    cli.dispatch_command("update")
    capsys.readouterr()
    for depth in ("1", "3"):
        monkeypatch.setattr(sys, "argv", ["graphify", "query", "SW_T_Oel", "--depth", depth,
                                          "--graph", str(toy / "graphify-out" / "graph.json")])
        cli.dispatch_command("query")
        assert f"depth={depth}" in capsys.readouterr().out


def _include_project(tmp_path, extractor):
    from graphify.extractors import clang_c
    (tmp_path / "inc").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "inc" / "hw.h").write_text("extern int Hw_Val;\n", encoding="utf-8")
    (tmp_path / "src" / "a.c").write_text('#include "hw.h"\nint Hw_Val;\nint A_Fn(void){return Hw_Val;}\n', encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(
        json.dumps({"extra_args": [f"-I{(tmp_path / 'inc').as_posix()}"], "extractor": extractor}), encoding="utf-8")
    clang_c.set_runtime_config()
    result = extract(sorted(tmp_path.rglob("*.[ch]")), root=tmp_path)
    return result["nodes"], result["edges"]


@pytest.mark.parametrize("mode", ["both", "clang"])
def test_include_edge_resolved_via_include_path(tmp_path, mode):
    """tree-sitter resolves includes only next to the file; clang uses -I, so a.c -> inc/hw.h must exist."""
    nodes, edges = _include_project(tmp_path, mode)
    ids = {n["source_file"]: n["id"] for n in nodes if n["label"] in ("a.c", "hw.h")}
    imp = [e for e in edges if e["relation"] == "imports" and e["source"] == ids["src/a.c"] and e["target"] == ids["inc/hw.h"]]
    assert imp, f"no resolved imports edge in mode {mode}"
    if mode == "clang":
        assert not any(str(n["id"]).startswith("ext_") for n in nodes)
