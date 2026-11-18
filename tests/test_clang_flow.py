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


# ---- execution order: tasks, timing of each step, data order, feedback -----------------------------------

SCHED_C = """
int HwIn, Raw, Plaus, Deb, Ctrl, Late, Slow, State, Fb1, Fb2, Ow1, Ow2;
void Read(void) { Raw = HwIn; }
void Plaus1(void) { Plaus = Raw > 0 ? Raw : 0; }
void Debounce(void) { Deb = Plaus; State = State + Raw; }
void UseLate(void) { Late = Ctrl; }
void Produce(void) { Ctrl = Deb + 1; }
void LoopA(void) { Fb1 = Fb2 + 1; }
void LoopB(void) { Fb2 = Fb1; }
void Overwrite(void) { int t = Raw; t = Plaus; Ow1 = t; }
void Task10(void) { Read(); Plaus1(); Debounce(); UseLate(); Produce(); LoopA(); LoopB(); Overwrite(); }
void SlowUse(void) { Slow = Deb; }
void Task20(void) { SlowUse(); }
"""


@pytest.fixture(scope="module")
def sched_graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched")
    (root / "s.c").write_text(SCHED_C)
    old = os.getcwd()
    os.chdir(root)
    clang_c.set_runtime_config(extractor="clang")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([root / "s.c"], root, nodes, edges)
    finally:
        clang_c.set_runtime_config()
        os.chdir(old)
    return {"directed": False, "nodes": nodes, "links": edges}


def _res(raw, tasks=None, notes=False):
    from graphify import swdoc
    return swdoc.build_vault(raw, ["."], tasks=tasks, want_notes=notes)


def test_overwritten_local_drops_its_old_source(sched_graph):
    lab = {n["id"]: n["label"] for n in sched_graph["nodes"]}
    d = {(lab[e["source"]], lab[e["target"]]) for e in sched_graph["links"] if e["relation"] == "derived_from"}
    assert ("Ow1", "Plaus") in d and ("Ow1", "Raw") not in d      # t = Raw; t = Plaus; Ow1 = t
    assert ("Fb1", "Fb2") in d and ("Fb2", "Fb1") in d


def test_feeds_itself_flag(sched_graph):
    state = next(n for n in sched_graph["nodes"] if n["label"] == "State")
    assert state["metadata"]["feeds_itself"]


def test_tasks_are_the_functions_nobody_calls(sched_graph):
    sc = _res(sched_graph)["ctx"].M.sched
    assert {t.name for t in sc.tasks} == {"Task10", "Task20"}
    t10 = next(t for t in sc.tasks if t.name == "Task10")
    assert [sc.M.label(f).rstrip("()") for f in t10.order][:5] == ["Task10", "Read", "Plaus1", "Debounce", "UseLate"]


def test_tasks_option_names_tasks_and_restricts_them(sched_graph):
    sc = _res(sched_graph, {"Task10": "10 ms"})["ctx"].M.sched
    assert [t.label for t in sc.tasks] == ["Task10 (10 ms)"]


def test_timing_of_every_step(sched_graph):
    from graphify import swdoc_sched as ss
    M = _res(sched_graph)["ctx"].M
    sc = M.sched
    lab = {M.label(i): i for i in M.nodes}
    code = {(M.label(e["source"]), M.label(e["target"])): sc.edge_timing(e) for e in M.edges if e["relation"] == "derived_from"}
    assert code[("Plaus", "Raw")] == ss.SAME and code[("Deb", "Plaus")] == ss.SAME and code[("Ctrl", "Deb")] == ss.SAME
    assert code[("Late", "Ctrl")] == ss.NEXT                       # UseLate runs before Produce writes Ctrl
    assert code[("Slow", "Deb")] == ss.OTHER                       # written in Task10, read in Task20
    assert code[("Raw", "HwIn")] == ss.OUTSIDE                     # nobody writes HwIn in the code
    assert code[("State", "Raw")] == ss.SAME
    assert lab


def test_trace_delay_is_summed_along_the_path(sched_graph):
    M = _res(sched_graph)["ctx"].M
    raw = next(i for i in M.nodes if M.label(i) == "Raw")
    rows = {M.label(t["id"]): t for t in M.trace_timed(raw)}
    assert rows["Plaus"]["delay"] == "same cycle" and rows["Ctrl"]["delay"] == "same cycle"
    assert rows["Late"]["delay"] == "1 cycle later"
    assert rows["Slow"]["delay"] == "task change"


def test_data_order_and_task_interfaces(sched_graph):
    M = _res(sched_graph)["ctx"].M
    sc = M.sched
    issues = {(M.label(v), M.label(r).rstrip("()"), M.label(w).rstrip("()")) for v, _t, r, _rt, w, _wt in sc.order_issues()}
    assert ("Ctrl", "UseLate", "Produce") in issues and ("Fb2", "LoopA", "LoopB") in issues
    assert not any(v == "Plaus" for v, _r, _w in issues)
    ifs = {M.label(v) for v, _w, _r in sc.task_interfaces()}
    assert "Deb" in ifs


def test_feedback_loop_is_listed(sched_graph):
    M = _res(sched_graph)["ctx"].M
    fb1 = next(i for i in M.nodes if M.label(i) == "Fb1")
    rows = [(t["level"], t["id"], t["edge"], t["src"]) for t in M.trace_timed(fb1)]
    assert [(M.label(a), M.label(b)) for a, b, _e in M.loops(fb1, rows)] == [("Fb2", "Fb1")]


def test_notes_and_html_show_tasks_and_timing(sched_graph, tmp_path):
    import json as _json
    import re
    from graphify import swdoc
    notes = _res(sched_graph, {"Task10": "10 ms", "Task20": "20 ms"}, notes=True)["notes"]
    mod = next(v for k, v in notes.items() if k.startswith("modules/"))
    assert "## Tasks and execution order" in mod and "Execution order: Task10 (10 ms)" in mod
    assert "reader runs before the writer" in mod and "Data between tasks" in mod
    ctrl = next(v for k, v in notes.items() if k.endswith("variables/Ctrl.md"))
    assert "next cycle" in ctrl
    raw = next(v for k, v in notes.items() if k.endswith("variables/Raw.md"))
    assert "1 cycle later" in raw and "Feedback loops" not in raw
    assert "Feeds itself" in next(v for k, v in notes.items() if k.endswith("variables/State.md"))
    assert "**Runs in task:** Task10 (10 ms)" in next(v for k, v in notes.items() if k.endswith("functions/Read.md"))
    gp = tmp_path / "g.json"
    gp.write_text(_json.dumps(sched_graph), encoding="utf-8")
    assert swdoc.run_swdoc([".", "--module", "Software", "--graph", str(gp), "--out", str(tmp_path / "o"), "--html",
                            "--tasks", "Task10=10 ms,Task20=20 ms"]) == 0
    html = (tmp_path / "o" / "Software.html").read_text(encoding="utf-8")
    data = _json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert [t[:2] for t in data["tk"]] == [["Task10", "10 ms"], ["Task20", "20 ms"]] and data["oi"] and data["ti"]
    names = [it["n"] for it in data["it"]]
    assert data["it"][names.index("Late")]["df"][0][5] == 1
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
        page.goto(f"{url}#t")
        txt = page.inner_text("#main")
        assert "Task10" in txt and "reader runs before the writer" in txt and "Data between tasks" in txt
        page.goto(f"{url}#t/0")
        assert "Execution order" in page.inner_text("#main")
        page.goto(f"{url}#i/{names.index('Raw')}")
        assert "1 cycle later" in page.inner_text("#main")
        page.goto(f"{url}#i/{names.index('Fb1')}")
        assert "Feedback loops" in page.inner_text("#main")
        b.close()
    assert not errs, errs
