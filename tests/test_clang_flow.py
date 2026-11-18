"""Data flow (derived_from), call argument variables, control context and function pointers (clang pass)."""
import os

import pytest

pytest.importorskip("clang.cindex")

from graphify.extractors import clang_c

C = 'typedef void (*cb_t)(int);\nint SigX, SigY, SigZ, SigW, Mode, Unrelated, SigV, SigU, Result;\nstatic int Cnt;\n\nstatic int Scale(int a, int k) { int r = a * k; return r; }\nstatic int Filter(int a) {\n  int t1 = Scale(a, 3);\n  int t2 = t1 + 1;\n  return t2;\n}\nstatic void Store(int v, int *out) { *out = v; }\nstatic void SetW(int v) { SigW = v; }\n\nvoid Calc(void) {\n  int t1 = SigX * 2;\n  int t2 = t1 + 5;\n  int t3 = t2;\n  int t4 = Filter(t3);\n  int t5 = t4;\n  int t6 = t5 - 1;\n  int t7 = t6;\n  int t8 = t7;\n  int t9 = t8;\n  int t10 = t9 > 100 ? t9 : 100;\n  SigY = t10;\n}\nvoid Cond(void) {\n  if (Mode > 2) { SigZ = 1; }\n  else { SigZ = 0; }\n  for (int i = 0; i < 3; i++) { Cnt = Cnt + SigX; }\n}\nvoid ViaOut(void) {\n  int tmp;\n  Store(SigX, &tmp);\n  SigV = tmp;\n  SetW(SigX + 1);\n}\nvoid Plain(void) { Unrelated = 7; }\n\nvoid A(int a); void B(int a);\nstruct E { int id; cb_t init; cb_t run; };\nconst struct E tab[] = { {1, A, B}, {2, .run = B, .init = A} };\ncb_t g_cb = A;\nstruct E s;\nvoid A(int a) { SigU = a; }\nvoid B(int a) { Result = a; }\nvoid Reg(cb_t p) { p(9); }\nvoid User(int i) {\n  tab[i].run(i);\n  g_cb(1);\n  s.init = B;\n  s.init(2);\n  Reg(A);\n}\n\nextern int Ext_Abs(int a);\nint Abs2, Rec2;\nint Rec(int a) { if (a) { return Rec(a - 1) + SigX; } return 0; }\nvoid Misc(void) { Abs2 = Ext_Abs(SigX); Rec2 = Rec(2); }\n'


EXTRA = """
int Out2, Out3;
void Next(void) { Out2 = SigY + 1; Out3 = Out2; }
"""

_NODES: list = []


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("flow")
    (root / "flow.c").write_text(C + EXTRA)
    old = os.getcwd()
    os.chdir(root)
    clang_c.set_runtime_config(extractor="clang")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([root / "flow.c"], root, nodes, edges)
    finally:
        clang_c.set_runtime_config()
        os.chdir(old)
    _NODES[:] = nodes
    lab = {n["id"]: n["label"] for n in nodes}
    return lab, edges


def _rel(graph, relation):
    lab, edges = graph
    return {(lab[e["source"]], lab[e["target"]]): e for e in edges if e["relation"] == relation}


def test_ten_locals_over_three_functions(graph):
    d = _rel(graph, "derived_from")
    e = d[("SigY", "SigX")]                      # SigX -> t1..t10 in Calc, Filter(), Scale() -> SigY
    assert e["metadata"]["control_only"] is False and e["confidence"] == "EXTRACTED"
    assert e["metadata"]["via"] and e["metadata"]["functions"]


def test_out_parameter_and_callee_global_write(graph):
    d = _rel(graph, "derived_from")
    assert ("SigV", "SigX") in d                 # Store(SigX, &tmp); SigV = tmp
    assert ("SigW", "SigX") in d                 # SetW(SigX + 1) writes SigW inside the callee


def test_control_dependency_is_marked(graph):
    d = _rel(graph, "derived_from")
    assert d[("SigZ", "Mode")]["metadata"]["control_only"] is True
    assert d[("Cnt", "SigX")]["metadata"]["control_only"] is False


def test_no_false_edges(graph):
    d = _rel(graph, "derived_from")
    assert not any(t == "Unrelated" or s == "Unrelated" for s, t in d)
    assert ("SigX", "SigY") not in d             # direction: the result derives from the source, never backwards


def test_unknown_callee_is_inferred_and_recursion_terminates(graph):
    d = _rel(graph, "derived_from")
    assert d[("Abs2", "SigX")]["confidence"] == "INFERRED"
    assert ("Rec2", "SigX") in d


def test_call_argument_variables(graph):
    c = _rel(graph, "calls")
    assert c[("Calc()", "Filter()")]["metadata"]["args"][0]["args"] == {"0": [next(i for i, l in graph[0].items() if l == "SigX")]}


def test_access_context(graph):
    lab, edges = graph
    ctx = {(lab[e["source"]], lab[e["target"]], e["relation"]): e["metadata"].get("ctx") for e in edges}
    assert ctx[("Calc()", "SigY", "writes_var")] == ["always"]
    assert ctx[("Cond()", "SigZ", "writes_var")] == ["if", "else"]
    assert ctx[("Cond()", "SigX", "reads_var")] == ["loop"]


def test_function_pointers(graph):
    ind = {(s, t): e["metadata"]["via"] for (s, t), e in _rel(graph, "indirect_call").items()}
    assert ind[("User()", "B()")] == ["tab.run", "s.init"]     # the field decides: tab.run holds B, not A
    assert ind[("User()", "A()")] == ["g_cb"]
    assert ind[("Reg()", "A()")] == ["parameter 0"]            # Reg(A) hands A to the parameter that Reg calls
    assert ("User()", "tab") not in ind


def test_indirect_calls_in_call_sequence(graph):
    user = next(n for n in _NODES if n["label"] == "User()")
    seq = user["metadata"]["call_sequence"]
    targets = {s["id"]: s.get("ctx", "") for s in seq}
    lab = graph[0]
    assert {lab[i]: c for i, c in targets.items()}["B()"] == "ptr"       # marked as a call through a pointer


# ---- documentation (Obsidian notes, single file, html) ------------------------------------------------

def _raw(graph):
    lab, edges = graph
    return {"directed": False, "nodes": list(_NODES), "links": edges}


def test_notes_show_flow_conditions_and_arguments(graph):
    from graphify import swdoc
    notes = swdoc.build_vault(_raw(graph), ["."])["notes"]
    sigx = next(v for k, v in notes.items() if k.endswith("variables/SigX.md"))
    assert "### Used to compute" in sigx and "### Signal trace" in sigx
    sigy = next(v for k, v in notes.items() if k.endswith("variables/SigY.md"))
    assert "### Derived from" in sigy and "[[SigX]]" in sigy and "| value |" in sigy
    sigz = next(v for k, v in notes.items() if k.endswith("variables/SigZ.md"))
    assert "condition only" in sigz and "| if, else |" in sigz            # written under if / else
    cond = next(v for k, v in notes.items() if k.endswith("functions/Cond.md"))
    assert "When" in cond and "repeated (loop)" in cond
    calc = next(v for k, v in notes.items() if k.endswith("functions/Calc.md"))
    assert "Variables passed to the callees" in calc and "arg 1: [[SigX]]" in calc
    user = next(v for k, v in notes.items() if k.endswith("functions/User.md"))
    assert "through a function pointer" in user                             # B() / A() reached through tab.run, g_cb ...


def test_html_flow_data_and_pages(graph, tmp_path):
    import json as _json
    import re
    from graphify import swdoc
    gp = tmp_path / "g.json"
    gp.write_text(_json.dumps(_raw(graph)), encoding="utf-8")
    assert swdoc.run_swdoc([".", "--module", "Software", "--graph", str(gp), "--out", str(tmp_path / "o"), "--html"]) == 0
    html = (tmp_path / "o" / "Software.html").read_text(encoding="utf-8")
    data = _json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    names = [it["n"] for it in data["it"]]
    sigy = data["it"][names.index("SigY")]
    assert [d[0] for d in sigy["df"]] == [names.index("SigX")] and sigy["df"][0][1] == 0
    cond = data["it"][names.index("Cond")]
    assert any(r[2] == "repeated (loop)" for r in cond["rd"]) and any(r[2] == "if, else" for r in cond["wr"])
    assert data["it"][names.index("Calc")]["ar"]
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        page = b.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        url = (tmp_path / "o" / "Software.html").as_uri()
        for i in range(len(names)):
            page.goto(f"{url}#i/{i}")
            assert page.inner_text("#main h2")
        page.goto(f"{url}#i/{names.index('SigX')}")
        txt = page.inner_text("#main")
        assert "Used to compute" in txt and "Signal trace" in txt and "SigY" in txt
        page.goto(f"{url}#i/{names.index('SigZ')}")
        assert "condition only" in page.inner_text("#main")
        b.close()
    assert not errs, errs
