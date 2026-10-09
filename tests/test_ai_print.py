"""What the AI sees for variables: storage, definition file, declaration role, address-taken."""
from __future__ import annotations

import json

import graphify.__main__ as mainmod


def _graph(tmp_path):
    data = {
        "directed": False, "multigraph": False, "graph": {},
        "nodes": [
            {"id": "kku_c", "label": "kku.c", "source_file": "kku/kku.c", "community": 0},
            {"id": "kku_h", "label": "kku.h", "source_file": "kku/kku.h", "community": 0},
            {"id": "F", "label": "F()", "type": "function", "source_file": "kku/kku.c",
             "source_location": "L10", "community": 0},
            {"id": "G", "label": "Gvar", "type": "variable", "source_file": "kku/kku.h", "community": 0,
             "metadata": {"storage": "global", "defined_in": "kku/kku.c", "defined_line": 3}},
            {"id": "S", "label": "Svar", "type": "variable", "source_file": "kku/kku.c", "community": 0,
             "metadata": {"storage": "static", "defined_in": "kku/kku.c", "declaration_only": True}},
        ],
        "links": [
            {"source": "kku_h", "target": "G", "relation": "contains", "confidence": "EXTRACTED",
             "metadata": {"role": "declaration"}},
            {"source": "kku_c", "target": "G", "relation": "contains", "confidence": "EXTRACTED",
             "metadata": {"role": "definition"}},
            {"source": "F", "target": "G", "relation": "reads_var", "confidence": "EXTRACTED",
             "source_file": "kku/kku.c", "source_location": "L12", "metadata": {"address_taken": True}},
            {"source": "F", "target": "S", "relation": "writes_var", "confidence": "EXTRACTED",
             "source_file": "kku/kku.c", "source_location": "L13"},
        ],
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(data))
    return p


def _explain(monkeypatch, p, label, capsys):
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "explain", label, "--graph", str(p)])
    mainmod.main()
    return capsys.readouterr().out


def test_explain_variable_shows_storage_definition_role_and_address(monkeypatch, tmp_path, capsys):
    out = _explain(monkeypatch, _graph(tmp_path), "Gvar", capsys)
    assert "Storage:" in out and "global" in out
    assert "Definition:" in out and "kku/kku.c" in out
    assert "role=declaration" in out and "role=definition" in out
    assert "Address:   taken" in out
    assert "address_taken" in out


def test_explain_static_declaration_only_flag(monkeypatch, tmp_path, capsys):
    out = _explain(monkeypatch, _graph(tmp_path), "Svar", capsys)
    assert "static" in out and "Decl. only:" in out
    assert "Address:" not in out


def test_query_text_node_and_edge_lines_carry_the_facts(tmp_path):
    from networkx.readwrite import json_graph

    from graphify.serve import _query_graph_text

    raw = json.loads(_graph(tmp_path).read_text())
    G = json_graph.node_link_graph(raw, edges="links")
    text = _query_graph_text(G, "Gvar", depth=1, token_budget=4000)
    node_line = next(ln for ln in text.splitlines() if ln.startswith("NODE Gvar"))
    assert "storage=global" in node_line and "defined_in=kku/kku.c" in node_line
    assert any(ln.startswith("EDGE") and "role=declaration" in ln for ln in text.splitlines())
    assert any(ln.startswith("EDGE") and "address_taken" in ln for ln in text.splitlines())
