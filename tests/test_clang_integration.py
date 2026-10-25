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
    amb = _edge(nodes, edges, "Kku_Task10ms()", "reads_calibration_field", "KkuAppROM.Kku_Tab_kl")
    assert amb is not None and amb["confidence"] == "EXTRACTED"          # run-time index [i]: one node, certain read
    assert amb["metadata"]["index"] == ["[i]"]
    assert not any(n["label"].startswith("KkuAppROM.Kku_Tab_kl[") for n in nodes)
    flag = _by_label(nodes, "KkuAppROM.Kku_Flags")
    assert "bit_mask" not in flag["metadata"]
    fe = _edge(nodes, edges, "Kku_BerKuehlAnf()", "reads_calibration_field", "KkuAppROM.Kku_Flags")
    assert fe["metadata"]["mask"] == [2]                                  # the mask lives on the read edge


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


@pytest.mark.parametrize("mode", ["both", "clang"])
def test_contains_edges_for_functions_and_variables(toy, mode):
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor=mode)
    try:  # headers must be in the scan too (as in a real run) so they have file nodes
        result = extract(sorted(toy.glob("*.[ch]")), root=toy)
    finally:
        clang_c.set_runtime_config()
    nodes, edges = result["nodes"], result["edges"]
    for sym, f in (("Kku_BerKuehlAnf()", "kku.c"), ("Kku_Anf", "kku.h"), ("Kku_Send()", "kku_out.c")):
        sid = _by_label(nodes, sym)["id"]
        fid = _by_label(nodes, f)["id"]
        assert any(e["relation"] == "contains" and e["source"] == fid and e["target"] == sid for e in edges), (mode, sym)


def _header_decl_project(toy, header_declarations):
    from graphify.extractors import clang_c
    (toy / "other.h").write_text("extern int Other_Unused;\nextern int Other_Used;\nint Other_Proto(int x);\n", encoding="utf-8")
    (toy / "user.c").write_text('#include "other.h"\nint User_Fn(void){return Other_Used;}\n', encoding="utf-8")
    cfg = json.loads((toy / "graphify-clang.json").read_text(encoding="utf-8"))
    cfg.update(extractor="clang", header_declarations=header_declarations)
    (toy / "graphify-clang.json").write_text(json.dumps(cfg), encoding="utf-8")
    clang_c.set_runtime_config()
    result = extract(sorted(toy.glob("*.[ch]")), root=toy)
    return result["nodes"], result["edges"]


def test_header_declarations_get_nodes_and_contains(toy):
    """Everything a header declares (used or not) hangs off its header with `contains`, like the old standalone graph."""
    nodes, edges = _header_decl_project(toy, True)
    hdr = _by_label(nodes, "other.h")["id"]
    for label in ("Other_Unused", "Other_Used", "Other_Proto()"):
        nid = _by_label(nodes, label)["id"]
        assert any(e["relation"] == "contains" and e["source"] == hdr and e["target"] == nid for e in edges), label
    assert _edge(nodes, edges, "User_Fn()", "reads_var", "Other_Used")


def test_header_declarations_can_be_switched_off(toy):
    nodes, edges = _header_decl_project(toy, False)
    labels = {n["label"] for n in nodes}
    assert "Other_Unused" not in labels and "Other_Proto()" not in labels and "Other_Used" in labels


@pytest.mark.parametrize("mode", ["both", "clang"])
def test_every_included_header_gets_a_file_node_even_outside_scan(tmp_path, mode):
    """Headers that were not in the scan (or lie outside the root) still get a file node + contains + imports."""
    from graphify.extractors import clang_c
    proj, outer = tmp_path / "proj", tmp_path / "outer"
    proj.mkdir(); outer.mkdir()
    (outer / "hw.h").write_text("extern int Hw_Val;\nint Hw_Inline(void){return 3;}\n", encoding="utf-8")
    (proj / "a.c").write_text('#include "hw.h"\nint A_Fn(void){return Hw_Val + Hw_Inline();}\n', encoding="utf-8")
    (proj / "graphify-clang.json").write_text(
        json.dumps({"extra_args": [f"-I{outer.as_posix()}"], "extractor": mode}), encoding="utf-8")
    clang_c.set_runtime_config()
    result = extract([proj / "a.c"], root=proj)  # header NOT in the scan list
    nodes, edges = result["nodes"], result["edges"]
    hdr = next(n for n in nodes if n["label"] == "hw.h")
    a = next(n for n in nodes if n["label"] == "a.c")
    inl = _by_label(nodes, "Hw_Inline()")
    var = _by_label(nodes, "Hw_Val")
    for sym in (inl, var):
        assert any(e["relation"] == "contains" and e["source"] == hdr["id"] and e["target"] == sym["id"] for e in edges), sym["label"]
    assert any(e["relation"] == "imports" and e["source"] == a["id"] and e["target"] == hdr["id"] for e in edges)
    ids = {n["id"] for n in nodes}
    assert all(e["source"] in ids and e["target"] in ids for e in edges if e["relation"] == "contains")
    assert not any("tmp" in n["id"] and "outer" in n["id"] for n in nodes), "machine path leaked into ids"


def test_declaration_merges_into_existing_definition_node():
    """Header declaration (clang) + definition only known under another id (tree-sitter): one node, edges follow."""
    from types import SimpleNamespace
    from graphify.extractors.clang_c import _unify_declarations
    all_nodes = [{"id": "vwh_vwh_flagrdlock", "label": "Vwh_FlagRdLock()", "file_type": "code", "source_file": "vwh/vwh.c"}]
    nodes = [{"id": "ext_src_vwh_vwh_flagrdlock", "label": "Vwh_FlagRdLock()", "type": "function", "source_file": "../x/vwh.h"},
             {"id": "u", "label": "U()", "type": "function", "source_file": "u.c"}]
    edges = [{"source": "u", "target": "ext_src_vwh_vwh_flagrdlock", "relation": "calls"}]
    ex = SimpleNamespace(final_decl_only={"ext_src_vwh_vwh_flagrdlock"})
    assert _unify_declarations(ex, nodes, edges, all_nodes) == 1
    assert [n["id"] for n in nodes] == ["u"] and edges[0]["target"] == "vwh_vwh_flagrdlock"
    # ambiguous (two definitions) stays untouched
    all_nodes.append({"id": "vwh2_flagrdlock", "label": "Vwh_FlagRdLock()", "file_type": "code", "source_file": "vwh2/vwh.c"})
    nodes2 = [{"id": "d", "label": "Vwh_FlagRdLock()", "type": "function", "source_file": "../x/vwh.h"}]
    assert _unify_declarations(SimpleNamespace(final_decl_only={"d"}), nodes2, [], all_nodes) == 0


def test_declaration_prefers_definition_in_same_stem_file():
    from types import SimpleNamespace
    from graphify.extractors.clang_c import _unify_declarations
    all_nodes = [{"id": "a", "label": "F()", "file_type": "code", "source_file": "m/vwh.c"},
                 {"id": "b", "label": "F()", "file_type": "code", "source_file": "m/other.c"}]
    nodes = [{"id": "d", "label": "F()", "type": "function", "source_file": "m/vwh.h"}]
    edges = [{"source": "x", "target": "d", "relation": "calls"}]
    assert _unify_declarations(SimpleNamespace(final_decl_only={"d"}), nodes, edges, all_nodes) == 1
    assert edges[0]["target"] == "a"


def test_windows_style_paths_keep_include_edges(tmp_path, monkeypatch):
    """Out-of-root headers spelled with backslashes (Windows) must still get ONE file node and their imports edge."""
    from pathlib import PureWindowsPath
    from graphify.extractors import clang_c
    proj, other = tmp_path / "proj", tmp_path / "other"
    proj.mkdir(); other.mkdir()
    (proj / "mid.h").write_text('#include "deep.h"\nint M(void);\n', encoding="utf-8")
    (other / "deep.h").write_text("int D(void);\nextern int D_Var;\n", encoding="utf-8")
    (proj / "k.c").write_text('#include "mid.h"\nint K(void){return M();}\n', encoding="utf-8")
    (proj / "graphify-clang.json").write_text(
        json.dumps({"extra_args": [f"-I{proj.as_posix()}", f"-I{other.as_posix()}"], "extractor": "clang"}), encoding="utf-8")
    real_name = clang_c.Path.name  # noqa: F841 (documentation only)
    orig_parse = clang_c.ClangExtractor.parse

    def parse_with_backslashes(self, path):
        tu = orig_parse(self, path)
        if tu is not None:  # re-spell the collected include pairs the way libclang does on Windows
            self.includes = {(str(PureWindowsPath(a)) if Path(a).is_absolute() else a,
                              str(PureWindowsPath(b)) if Path(b).is_absolute() else b): ln
                             for (a, b), ln in self.includes.items()}
        return tu

    monkeypatch.setattr(clang_c.ClangExtractor, "parse", parse_with_backslashes)
    clang_c.set_runtime_config()
    result = extract([proj / "k.c"], root=proj)
    nodes, edges = result["nodes"], result["edges"]
    deep = [n for n in nodes if n["label"] == "deep.h"]
    assert len(deep) == 1, [n["id"] for n in deep]
    mid = _by_label(nodes, "mid.h")
    assert any(e["relation"] == "imports" and e["source"] == mid["id"] and e["target"] == deep[0]["id"] for e in edges)


def test_export_html_keeps_edge_direction(tmp_path):
    """graph.json source/target must survive `export html` (no flipped arrows)."""
    import re
    import subprocess
    import sys

    out = tmp_path / "graphify-out"
    out.mkdir()
    nodes = [{"id": i, "label": i, "file_type": "code", "source_file": f"{i}.h"} for i in ("sic_var", "sic_macro")]
    # reverse-sorted so networkx canonical order differs from the true direction
    links = [{"source": "sic_macro", "target": "sic_var", "relation": "imports",
              "confidence": "EXTRACTED", "source_file": "sic_macro.h"}]
    (out / "graph.json").write_text(json.dumps(
        {"directed": False, "multigraph": False, "graph": {}, "nodes": nodes, "links": links}))
    repo = str(Path(__file__).resolve().parents[1])
    env = dict(__import__("os").environ, PYTHONPATH=repo)
    subprocess.run([sys.executable, "-m", "graphify", "export", "html"], cwd=tmp_path, env=env,
                   check=True, capture_output=True)
    html = (out / "graph.html").read_text(encoding="utf-8")
    edges = json.loads(re.search(r"const RAW_EDGES = (\[.*?\]);\n", html, re.S).group(1))
    assert [(e["from"], e["to"]) for e in edges] == [("sic_macro", "sic_var")]


def test_loaded_graph_keeps_true_edge_direction():
    """Every renderer reads _src/_tgt; the shared loader must stamp them (calls, contains, imports)."""
    from graphify.paths import load_node_link_graph

    nodes = [{"id": i} for i in ("a_file", "b_fn", "z_fn")]  # node order != edge direction
    links = [
        {"source": "z_fn", "target": "b_fn", "relation": "calls"},
        {"source": "a_file", "target": "z_fn", "relation": "contains"},
        {"source": "z_fn", "target": "a_file", "relation": "imports"},
    ]
    G = load_node_link_graph({"directed": False, "multigraph": True, "graph": {}, "nodes": nodes, "links": links})
    got = sorted((d["relation"], d["_src"], d["_tgt"]) for _, _, d in G.edges(data=True))
    assert got == sorted((l["relation"], l["source"], l["target"]) for l in links)


def test_read_write_classification_patterns(tmp_path):
    """Writes through ++/--, array elements, struct members and macros must be writes."""
    (tmp_path / "v.h").write_text(
        "typedef struct { int f; int h; } S_t;\n"
        "extern int g_inc, g_cmp, g_arr[4], g_addr, g_ptrw, g_arg, g_ro;\nextern S_t g_st;\n"
        "extern void sink(int *p);\nextern int use(int v);\n")
    (tmp_path / "a.c").write_text(
        '#include "v.h"\n'
        "int g_inc, g_cmp, g_arr[4], g_addr, g_ptrw, g_arg, g_ro;\nS_t g_st;\n"
        "#define SETV(v) do { g_inc = (v); } while (0)\n"
        "void f_incr(void){ g_inc++; }\n"
        "void f_cmpd(void){ g_cmp += 2; }\n"
        "void f_arr(void){ g_arr[2] = 3; }\n"
        "void f_sw(void){ g_st.f = 1; }\n"
        "void f_sr(void){ int x = g_st.h; (void)x; }\n"
        "void f_addr(void){ sink(&g_addr); }\n"
        "void f_ptrw(void){ int *p = &g_ptrw; *p = 7; }\n"
        "void f_arg(void){ use(g_arg); }\n"
        "void f_macro(void){ SETV(4); }\n"
        "void f_ro(void){ int y = g_ro; (void)y; }\n")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    result = extract([tmp_path / "a.c", tmp_path / "v.h"], root=tmp_path)
    nodes, edges = result["nodes"], result["edges"]
    expect = [
        ("f_incr()", "reads_writes_var", "g_inc"),
        ("f_cmpd()", "reads_writes_var", "g_cmp"),
        ("f_arr()", "writes_var", "g_arr"),
        ("f_sw()", "writes_var", "g_st"),
        ("f_sr()", "reads_var", "g_st"),
        ("f_macro()", "writes_var", "g_inc"),
        ("f_arg()", "reads_var", "g_arg"),
        ("f_ro()", "reads_var", "g_ro"),
    ]
    for src, rel, tgt in expect:
        assert _edge(nodes, edges, src, rel, tgt), f"{src} {rel} {tgt}"
    # address taken: cannot be attributed statically, so it is a read flagged address_taken
    for src, tgt in (("f_addr()", "g_addr"), ("f_ptrw()", "g_ptrw")):
        e = _edge(nodes, edges, src, "reads_var", tgt)
        assert e is not None and e["metadata"].get("address_taken") is True, (src, tgt)


def test_static_global_and_extern_variables_are_modelled_differently(tmp_path):
    """static: only its .c. global: header (declaration) AND the defining .c (definition).
    extern never defined: declaration only."""
    (tmp_path / "g.h").write_text("extern int g_glob;\nextern int g_only_decl;\nvoid fa(void); void fb(void);\n")
    (tmp_path / "a.c").write_text('#include "g.h"\nint g_glob;\nstatic int s_cnt;\n'
                                  "void fa(void){ s_cnt++; g_glob = s_cnt; }\n")
    (tmp_path / "b.c").write_text('#include "g.h"\nstatic int s_cnt;\n'
                                  "void fb(void){ s_cnt = 2; int x = g_glob + g_only_decl; (void)x; }\n")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    result = extract([tmp_path / "a.c", tmp_path / "b.c", tmp_path / "g.h"], root=tmp_path)
    nodes, edges = result["nodes"], result["edges"]
    var = {n["id"]: n for n in nodes if n.get("type") == "variable"}
    files = {n["id"]: n["source_file"] for n in nodes if n["label"] in ("a.c", "b.c", "g.h")}

    def holders(vid):
        return sorted((files[e["source"]], e["metadata"]["role"]) for e in edges
                      if e["relation"] == "contains" and e["target"] == vid and e["source"] in files)

    glob = next(n for n in var.values() if n["label"] == "g_glob")
    assert glob["metadata"]["storage"] == "global" and glob["metadata"]["defined_in"] == "a.c"
    assert not glob["metadata"].get("declaration_only")
    assert holders(glob["id"]) == [("a.c", "definition"), ("g.h", "declaration")]

    only = next(n for n in var.values() if n["label"] == "g_only_decl")
    assert only["metadata"].get("declaration_only") is True
    assert holders(only["id"]) == [("g.h", "declaration")]

    statics = [n for n in var.values() if n["label"] == "s_cnt"]
    assert len(statics) == 2, "two statics with one name are two variables"
    for n in statics:
        assert n["metadata"]["storage"] == "static" and not n["metadata"].get("declaration_only")
        assert holders(n["id"]) == [(n["source_file"], "definition")]


def test_constant_index_with_suffix_is_plain_number(tmp_path):
    """`Tab[0u][1UL]` is index text `[0][1]` on the edge (no `u`); the node path has no index at all."""
    (tmp_path / "t.c").write_text(
        "typedef struct { short Tab[2][2]; } CAL;\n"
        "static const CAL Rom = {0};\nconst CAL *Ptr;\nshort Out;\n"
        "void Bios_RegisterCalibrationData(void **p, const void *r, int n);\n"
        "void Init(void) { Bios_RegisterCalibrationData((void **)&Ptr, &Rom, 4); }\n"
        "void Run(void) { Out = Ptr->Tab[0u][1UL] + Ptr->Tab[0x1][0]; }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, _ = _run(tmp_path)
    paths = sorted(n["metadata"]["calibration_path"] for n in nodes if n.get("type") == "calibration_field")
    assert paths == ["Rom.Tab"]                                           # one node, indices off the path
    e = next(e for e in _run(tmp_path)[1] if e["relation"] == "reads_calibration_field")
    assert sorted(e["metadata"]["index"]) == ["[0][1]", "[1][0]"]          # `u`, `UL`, `0x1` are plain numbers


def test_whole_struct_read_expands_to_member_nodes_from_the_header_type(tmp_path):
    """`g(ptr->Feh)` reads a struct: its leaf members (nested struct, bit-fields, array of structs, anonymous
    union) become calibration nodes with exact paths; a direct `ptr->Feh.flag.Bit` is not expanded."""
    (tmp_path / "t.h").write_text(
        "typedef struct { unsigned AdcPSpeicher:1; unsigned Other:1; } FLAGS_T;\n"
        "typedef struct { FLAGS_T flag; short arr[3]; struct { int u; int v; } inner[2];\n"
        "                 union { int a; char b; }; } FEH_T;\n"
        "typedef struct { FEH_T Feh; short Direct; } ROM_T;\n", encoding="utf-8")
    (tmp_path / "t.c").write_text(
        '#include "t.h"\n'
        "static const ROM_T Rom = {0};\nconst ROM_T *Ptr;\nshort Out;\n"
        "void Bios_RegisterCalibrationData(void **p, const void *r, int n);\n"
        "void Init(void) { Bios_RegisterCalibrationData((void **)&Ptr, &Rom, 4); }\n"
        "void Use(FEH_T f) { Out = f.arr[0]; }\n"
        "void Whole(void) { Use(Ptr->Feh); }\n"
        "void One(void) { Out = Ptr->Direct + Ptr->Feh.flag.AdcPSpeicher; }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, edges = _run(tmp_path)
    paths = {n["metadata"]["calibration_path"] for n in nodes if n.get("type") == "calibration_field"}
    assert {"Rom.Feh", "Rom.Feh.flag.AdcPSpeicher", "Rom.Feh.flag.Other", "Rom.Feh.arr", "Rom.Feh.inner.u",
            "Rom.Feh.inner.v", "Rom.Feh.a", "Rom.Feh.b", "Rom.Direct"} <= paths
    ids = {n["label"]: n["id"] for n in nodes}
    whole = {e["target"]: e for e in edges if e["relation"] == "reads_calibration_field" and e["source"] == ids["Whole()"]}
    assert whole[ids["Rom.Feh"]]["confidence"] == "EXTRACTED"
    assert whole[ids["Rom.Feh.flag.Other"]]["confidence"] == "INFERRED"
    assert whole[ids["Rom.Feh.flag.Other"]]["metadata"]["via"] == "struct_type"
    one = {e["target"]: e for e in edges if e["relation"] == "reads_calibration_field" and e["source"] == ids["One()"]}
    assert ids["Rom.Feh.flag.AdcPSpeicher"] in one and ids["Rom.Feh.flag.Other"] not in one       # direct read: no expansion
    assert one[ids["Rom.Feh.flag.AdcPSpeicher"]]["confidence"] == "EXTRACTED"


def test_index_expression_with_own_subscript_and_member_is_one_index(tmp_path):
    """`Ptr->feh_ko[Tab[i].s.FID]`: the nested brackets must not leak `.s.FID]` into the node path."""
    (tmp_path / "t.h").write_text(
        "typedef struct { short feh_ko[100]; } ROM_T;\ntypedef struct { struct { unsigned FID; } s; } E_T;\n", encoding="utf-8")
    (tmp_path / "t.c").write_text(
        '#include "t.h"\nstatic const ROM_T Rom = {0};\nconst ROM_T *Ptr;\nE_T Tab[4];\nshort Out;\n'
        "void Bios_RegisterCalibrationData(void **p, const void *r, int n);\n"
        "void Init(void) { Bios_RegisterCalibrationData((void **)&Ptr, &Rom, 4); }\n"
        "void Use(int i) { Out = Ptr->feh_ko[Tab[i].s.FID]; }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, edges = _run(tmp_path)
    paths = {n["metadata"]["calibration_path"] for n in nodes if n.get("type") == "calibration_field"}
    assert paths == {"Rom.feh_ko"}, paths
    ids = {n["label"]: n["id"] for n in nodes}
    e = [x for x in edges if x["relation"] == "reads_calibration_field" and x["source"] == ids["Use()"]][0]
    assert e["metadata"]["index"] == ["[Tab[i].s.FID]"], e["metadata"]


def test_union_member_read_links_a2l_entry_of_sibling_bits_member_by_mask(tmp_path):
    """Code reads `Ptr->tab[i].Gesamt & (1u << 7)`; the A2L describes `Rom.tab.Bits.Gang_07` (mask 128) and
    `Rom.tab.Bits.Gang_06` (mask 64): only the entry with the same mask is linked."""
    (tmp_path / "t.h").write_text(
        "typedef union { unsigned short Gesamt; struct { unsigned short Gang_06:1; unsigned short Gang_07:1; } Bits; } M_T;\n"
        "typedef struct { M_T tab[4]; } ROM_T;\n", encoding="utf-8")
    (tmp_path / "t.c").write_text(
        '#include "t.h"\nstatic const ROM_T Rom = {0};\nconst ROM_T *Ptr;\nshort Out;\n'
        "void Bios_RegisterCalibrationData(void **p, const void *r, int n);\n"
        "void Init(void) { Bios_RegisterCalibrationData((void **)&Ptr, &Rom, 4); }\n"
        "void Use(int i) { Out = (Ptr->tab[i].Gesamt & (1u << 7)); }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, edges = _run(tmp_path)
    cal = [n for n in nodes if n.get("type") == "calibration_field" and n["metadata"]["calibration_path"] == "Rom.tab.Gesamt"][0]
    assert cal["metadata"]["union_aliases"] == ["Rom.tab.Bits.Gang_06", "Rom.tab.Bits.Gang_07"]
    assert cal["metadata"]["union_masks"] == [128] and not cal["metadata"].get("union_unmasked")


def test_function_header_comment_becomes_summary_params_returns(tmp_path):
    (tmp_path / "t.c").write_text(
        "/**-------------------------------\n"
        "Funktion: Check\n  Ueberpruefen, ob Bedingungen vorliegen.\n<br><br>\n"
        "Parameter:\n  Arg_Target\n    Zielzustand\n<br><br>\nRueckgabe:\n  0 nein\n  1 ja\n"
        "-------------------------------*/\n"
        "static int Check(int Arg_Target) { return Arg_Target; }\n"
        "int NoDoc(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, _ = _run(tmp_path)
    fn = {n["label"]: n for n in nodes if n.get("type") == "function"}
    m = fn["Check()"]["metadata"]
    assert m["summary"] == "Ueberpruefen, ob Bedingungen vorliegen."
    assert m["comment_params"] == {"Arg_Target": "Zielzustand"} and m["comment_returns"] == "0 nein | 1 ja"
    assert m["comment_source"] == "code" and "Funktion: Check" in m["comment"]
    assert "summary" not in fn["NoDoc()"]["metadata"]


def test_doxygen_comment_is_stored_raw_on_functions_variables_and_calibration_members(tmp_path):
    (tmp_path / "t.h").write_text(
        "typedef struct {\n  /** Schwelle in ms */\n  short Thr;\n  short Plain; /* not doxygen */\n  short Trail; ///< trailing doc\n"
        "} FEH_T;\ntypedef struct { FEH_T Feh; /** direct member doc */ short Direct; } ROM_T;\n"
        "/** Zaehler der Fehler */\nextern short Cnt;\n", encoding="utf-8")
    (tmp_path / "t.c").write_text(
        '#include "t.h"\nshort Cnt;\nstatic const ROM_T Rom = {0};\nconst ROM_T *Ptr;\nshort Out;\n'
        "void Bios_RegisterCalibrationData(void **p, const void *r, int n);\n"
        "void Init(void) { Bios_RegisterCalibrationData((void **)&Ptr, &Rom, 4); }\n"
        "/** Liest alles. */\nvoid Use(FEH_T f) { Out = f.Thr + Cnt; }\n"
        "/* plain function comment */\nvoid Plain(void) { Out = Ptr->Direct; }\n"
        "void Whole(void) { Use(Ptr->Feh); }\n", encoding="utf-8")
    (tmp_path / "graphify-clang.json").write_text(json.dumps({"extra_args": ["-I."]}), encoding="utf-8")
    nodes, _ = _run(tmp_path)
    by = {n["label"]: n["metadata"] for n in nodes if n.get("metadata") is not None}
    assert by["Use()"]["doxygen"] == "/** Liest alles. */"
    assert "doxygen" not in by["Plain()"]                                  # plain comment: stays missing
    assert by["Cnt"]["doxygen"] == "/** Zaehler der Fehler */"             # comment on the extern declaration in the header
    assert by["Rom.Direct"]["doxygen"] == "/** direct member doc */"       # direct member access
    assert by["Rom.Feh.Thr"]["doxygen"] == "/** Schwelle in ms */"         # leaf of an expanded struct
    assert by["Rom.Feh.Trail"]["doxygen"] == "///< trailing doc"
    assert "doxygen" not in by["Rom.Feh.Plain"]
