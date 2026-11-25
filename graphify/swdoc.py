"""`graphify swdoc "<module>"`: software documentation as an Obsidian vault (first draft).

One note per function, variable, calibration and axis of a module, plus a note for the module and a
home note. The function note is a "waterfall": inputs (variables with their writers, calibrations with A2L
facts and values, callers) flow down into the function, outputs (variables with their readers, callees)
flow down out of it. Every name is a [[wikilink]].

Nothing is invented: each fact is read from the graph (clang + A2L) or from the parameter databank. The
text of a note above the ``<!-- graphify:user-block`` line is regenerated on every export; the text below
it is the author's and is kept.

Pure functions over a node-link dict (``build_vault``); ``run_swdoc`` is the command line.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from graphify.a2l_links import code_links, link_names, link_range
from graphify.focus import READ, WRITE, CALL, _norm
from graphify.inputs import _loc

USER_MARK = "<!-- graphify:user-block"
USER_BLOCK = USER_MARK + " - text below this line is kept on re-export -->\n"
AXIS_REL = {"x_axis", "y_axis", "z_axis", "axis_reference", "x_axis_reference", "y_axis_reference"}
CAL_READ = "reads_calibration_field"
MAX_ROWS = 60          # rows of one table before "+N more"
MAX_ENTRIES = 200      # A2L entries listed in one calibration note
MAX_VALUE_LINKS = 20   # parameters whose values are printed in one calibration note
MANIFEST = ".graphify_swdoc_manifest.json"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list, rows: list) -> list:
    if not rows:
        return []
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_esc(c) if not str(c).startswith("\0") else str(c)[1:] for c in r) + " |" for r in rows]
    return out + [""]


def _raw(s: str) -> str:
    """Mark a table cell as already escaped (it contains [[a|b]] or <br>)."""
    return "\0" + s


def _fm(d: dict) -> list:
    out = ["---"]
    for k, v in d.items():
        if v in (None, "", [], False):
            continue
        if isinstance(v, list):
            out.append(f"{k}:")
            out += [f"  - {x}" for x in v]
        elif isinstance(v, bool):
            out.append(f"{k}: true")
        elif isinstance(v, int):
            out.append(f"{k}: {v}")
        else:
            out.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    return out + ["---", ""]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "module"


def _hex(m) -> str:
    try:
        return hex(int(m))
    except (TypeError, ValueError):
        return str(m)


def _stem(label: str) -> str:
    from graphify.export import _obsidian_safe_stem
    return _obsidian_safe_stem(re.sub(r"\(\)$", "", label))


# ---------------------------------------------------------------------------
# the graph as the notes need it
# ---------------------------------------------------------------------------

class _Model:
    sched = None        # graphify.swdoc_sched.Schedule, set by build_vault

    def __init__(self, raw: dict):
        self.nodes = {n["id"]: n for n in raw.get("nodes", []) if n.get("id")}
        self.edges = raw.get("links", raw.get("edges", []))
        self.out, self.inn = defaultdict(list), defaultdict(list)
        for e in self.edges:
            if e.get("source") in self.nodes and e.get("target") in self.nodes:
                self.out[e["source"]].append(e)
                self.inn[e["target"]].append(e)
        self.links = code_links(self.nodes, self.edges)

    def t(self, i):
        return self.nodes[i].get("type")

    def label(self, i) -> str:
        return str(self.nodes[i].get("label", i))

    def meta(self, i) -> dict:
        m = self.nodes[i].get("metadata")
        return m if isinstance(m, dict) else {}

    def file(self, i) -> str:
        return str(self.nodes[i].get("source_file") or "")

    def loc(self, i) -> str:
        return str(self.nodes[i].get("source_location") or "")

    def targets(self, i, rels, types) -> list:
        return sorted({e["target"] for e in self.out[i] if e.get("relation") in rels and self.t(e["target"]) in types},
                      key=lambda x: self.label(x).lower())

    def sources(self, i, rels, types) -> list:
        return sorted({e["source"] for e in self.inn[i] if e.get("relation") in rels and self.t(e["source"]) in types},
                      key=lambda x: self.label(x).lower())

    def edge(self, src, tgt, rels):
        return next((e for e in self.out[src] if e["target"] == tgt and e.get("relation") in rels), None)

    def call_sequence(self, f) -> list:
        """[(callee id, line, context)] of the calls in the body of f in execution order (empty for graphs
        extracted before the order was recorded). Context: '' always, 'if', 'else', 'loop', 'switch', '?:', '&&', '||'."""
        seq = self.meta(f).get("call_sequence")
        if not isinstance(seq, list):
            return []
        known = set(self.targets(f, CALL, ("function",)))
        return [(x["id"], x.get("line", 0), x.get("ctx", "")) for x in seq
                if isinstance(x, dict) and x.get("id") in known]

    def reach(self, f, limit: int = 100000) -> int:
        """Number of distinct functions f calls directly or indirectly."""
        adj = getattr(self, "_cadj", None)
        if adj is None:
            adj = self._cadj = defaultdict(set)
            for e in self.edges:
                if e.get("relation") in CALL and e.get("source") in self.nodes and e.get("target") in self.nodes:
                    adj[e["source"]].add(e["target"])
        seen, todo = {f}, [f]
        while todo and len(seen) < limit:
            for t in adj.get(todo.pop(), ()):
                if t not in seen:
                    seen.add(t)
                    todo.append(t)
        return len(seen) - 1

    def derived_of(self, v) -> list:
        """[(variable it takes its value from, edge)] (derived_from edges from the clang data-flow pass)."""
        return [(e["target"], e) for e in self.out[v] if e.get("relation") == "derived_from" and self.t(e["target"]) == "variable"]

    def feeds_of(self, v) -> list:
        """[(variable computed from v, edge)]."""
        return [(e["source"], e) for e in self.inn[v] if e.get("relation") == "derived_from" and self.t(e["source"]) == "variable"]

    def trace(self, v, depth: int = 4, cap: int = 60) -> list:
        """Everything computed from v, level by level: [(level, variable, edge, from variable)]. Breadth first, each variable once."""
        seen, out, level = {v}, [], [v]
        for lv in range(1, depth + 1):
            nxt = []
            for src in level:
                for x, e in sorted(self.feeds_of(src), key=lambda t: self.label(t[0]).lower()):
                    if x in seen:
                        continue
                    seen.add(x)
                    out.append((lv, x, e, src))
                    nxt.append(x)
                    if len(out) >= cap:
                        return out
            level = nxt
            if not level:
                break
        return out

    def loops(self, v, rows: list, cap: int = 12) -> list:
        """Feedback: steps of the trace that feed a variable already on the path back into it, [(from variable, variable, edge)]."""
        parent = {x: src for _lv, x, _e, src in rows}

        def ancestors(n):
            seen = {n}
            while n in parent and parent[n] not in seen:
                n = parent[n]
                seen.add(n)
            return seen

        out = []
        for n in [v] + [x for _lv, x, _e, _s in rows]:
            anc = ancestors(n)
            for x, e in sorted(self.feeds_of(n), key=lambda t: self.label(t[0]).lower()):
                if x in anc:
                    out.append((n, x, e))
                    if len(out) >= cap:
                        return out
        return out

    def trace_timed(self, v, depth: int = 4, cap: int = 60) -> list:
        """trace() with the timing of every step and the delay summed along the way: [dict(level, id, edge, src, code, delay)]."""
        from graphify.swdoc_sched import DELAY, OTHER, Schedule
        sc = getattr(self, "sched", None)
        cum, out = {v: (0, False)}, []
        for lv, x, e, src in self.trace(v, depth, cap):
            code = sc.edge_timing(e) if sc is not None else None
            cyc, oth = cum.get(src, (0, False))
            if code is not None:
                cyc += DELAY.get(code, 0)
                oth = oth or code == OTHER
            cum[x] = (cyc, oth)
            out.append({"level": lv, "id": x, "edge": e, "src": src, "code": code,
                        "delay": Schedule.delay_text(cyc, oth) if code is not None else ""})
        return out

    def doxygen(self, i) -> str:
        from graphify.comments import fix_mojibake
        return fix_mojibake(str(self.meta(i).get("doxygen") or ""))

    def doc_summary(self, i) -> str:
        """One line describing the node: parsed summary if present, else the first sentence of its doxygen text."""
        from graphify.comments import fix_mojibake, parse_function_comment
        m = self.meta(i)
        if m.get("summary"):
            return fix_mojibake(str(m["summary"]))
        if self.doxygen(i):
            return parse_function_comment(self.doxygen(i), self.label(i)).get("summary", "")
        return ""


# ---------------------------------------------------------------------------
# note builders
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, M: _Model, stems: dict, module: str, lookup=None, dataset: str = "", mermaid: bool = False):
        self.M, self.stems, self.module, self.lookup, self.dataset, self.mermaid = M, stems, module, lookup, dataset, mermaid
        self.plumbing: set = set()       # ROM pointers / ROM structs: access plumbing, not data
        self.cal_by_path: dict = {}      # calibration_path -> node id (calibrations in this export)
        self.children: dict = defaultdict(list)   # calibration_path of a struct -> member node ids

    def link(self, i) -> str:
        """[[note name]] (no alias: a '|' would break a table cell). A node without a note is shown as plain text."""
        if i not in self.stems:
            return "`" + re.sub(r"\(\)$", "", self.M.label(i)) + "`"
        return f"[[{self.stems[i]}]]"

    def vars_of(self, f, rels) -> list:
        return [v for v in self.M.targets(f, rels, ("variable",)) if v not in self.plumbing]

    def at(self, e) -> str:
        loc = _loc(e)
        return f"`{loc}`" if loc else ""

    def many(self, ids: list, limit: int = 8) -> str:
        shown = ", ".join(self.link(i) for i in ids[:limit])
        return shown + (f" +{len(ids) - limit} more" if len(ids) > limit else "")


CTX_TEXT = {"always": "always", "ptr": "through a function pointer", "if": "if", "else": "else", "loop": "repeated (loop)", "switch": "switch", "?:": "?: (one of two)",
            "&&": "only if the left side is true (&&)", "||": "only if the left side is false (||)"}


def _ctx_text(ctx: str) -> str:
    parts = [CTX_TEXT.get(x, x) for x in ctx.split(" > ") if x]
    return " > ".join(parts)


def _when(e) -> str:
    """When an access happens: 'always', or the conditions it sits in. '' for graphs extracted before this was recorded."""
    ctx = (e or {}).get("metadata", {}).get("ctx") if isinstance((e or {}).get("metadata"), dict) else None
    if not ctx:
        return ""
    if "always" in ctx:
        return "always" if len(ctx) == 1 else "always, " + ", ".join(_ctx_text(x) for x in ctx if x != "always")
    return ", ".join(_ctx_text(x) for x in ctx)


def _flow_kind(e) -> str:
    md = (e or {}).get("metadata") if isinstance((e or {}).get("metadata"), dict) else {}
    kind = "condition only" if md.get("control_only") else "value"
    return kind + (" (inferred)" if (e or {}).get("confidence") == "INFERRED" else "")


def _flow_via(c: "_Ctx", e) -> str:
    md = (e or {}).get("metadata") if isinstance((e or {}).get("metadata"), dict) else {}
    fns = [f for f in md.get("functions", []) if f in c.M.nodes]
    return c.many(fns, 4) if fns else ""


def _timing_cell(code) -> str:
    from graphify.swdoc_sched import TIMING_SHORT
    return TIMING_SHORT.get(code, "") if code is not None else ""


def flow_sections(c: "_Ctx", v: str) -> list:
    """Derived from / Used to compute / trace / feedback of a variable (empty when the graph has no data-flow edges for it)."""
    M, out = c.M, []
    sc = M.sched
    der, fed = M.derived_of(v), M.feeds_of(v)
    key = lambda t: M.label(t[0]).lower()
    tm = (lambda e: [_timing_cell(sc.edge_timing(e))]) if sc is not None else (lambda e: [])
    th = ["Timing"] if sc is not None else []
    itself = [f for f in M.meta(v).get("feeds_itself", []) if f in M.nodes]
    if itself:
        out += ["> [!info] Feeds itself", f"> The value is computed from its own previous value (state, counter or integrator) in {c.many(itself, 4)}.", ""]
    if der:
        out += ["### Derived from", "", "*Variables whose value (or whose condition) decides this variable. Locals and function "
                "calls in between are followed. Timing: does the writer of the source run before (same cycle) or after (next cycle) "
                "the function that uses it.*", ""] + _table(["Variable", "Kind"] + th + ["In function", "At"],
            [[_raw(c.link(x)), _flow_kind(e)] + tm(e) + [_raw(_flow_via(c, e)), _raw(c.at(e))] for x, e in sorted(der, key=key)[:MAX_ROWS]])
    if fed:
        out += ["### Used to compute", "", "*Variables computed from this one (directly).*", ""] + _table(
            ["Variable", "Kind"] + th + ["In function", "At"],
            [[_raw(c.link(x)), _flow_kind(e)] + tm(e) + [_raw(_flow_via(c, e)), _raw(c.at(e))] for x, e in sorted(fed, key=key)[:MAX_ROWS]])
        tr = M.trace_timed(v)
        if len(tr) > len(fed):
            out += ["### Signal trace (where the value ends up)", "", "*Level 1 = computed from this variable, level 2 = computed "
                    "from level 1, and so on (up to level 4). Delay = cycles added along the shortest path.*", ""] + _table(
                ["Level", "Variable", "Computed from"] + th + (["Delay so far"] if sc is not None else []) + ["Kind", "In function"],
                [[t["level"], _raw(c.link(t["id"])), _raw(c.link(t["src"]))] + ([_timing_cell(t["code"]), t["delay"]] if sc is not None else [])
                 + [_flow_kind(t["edge"]), _raw(_flow_via(c, t["edge"]))] for t in tr])
        lp = M.loops(v, [(t["level"], t["id"], t["edge"], t["src"]) for t in tr])
        if lp:
            out += ["### Feedback loops", "", "*The value comes back to a variable it was computed from. The step that closes the loop "
                    "is always delayed by at least one call of the writer.*", ""] + _table(
                ["From", "Feeds back into", "Timing", "In function"],
                [[_raw(c.link(a_)), _raw(c.link(x)), _timing_cell(sc.edge_timing(e)) if sc is not None else "", _raw(_flow_via(c, e))]
                 for a_, x, e in lp])
    return out


def tasks_section(c: "_Ctx", funcs: list) -> list:
    """Tasks (entry functions), execution order and data order of the functions of this component."""
    M, sc = c.M, c.M.sched
    if sc is None or not sc.tasks:
        return []
    fs = set(funcs)
    mine = [t for t in sc.tasks if any(f in fs for f in t.order)]
    if not mine:
        return []
    out = ["## Tasks and execution order", "",
           "*A task is a function that no other function calls (the hardware calls it every N samples). Everything it calls runs in that "
           "task, in code order. Timing statements assume this order; a function that runs several times per cycle counts at its first call."
           + (" The call order of some functions was not recorded in this graph: re-extract for exact order." if sc.approximate else "") + "*", ""]
    out += _table(["Task", "Period", "Functions in the task", "Of them in this component"],
                  [[_raw(c.link(t.entry) if t.entry in fs else f"`{t.name}`"), t.period_text or "-", len(t.order),
                    sum(1 for f in t.order if f in fs)] for t in mine[:MAX_ROWS]])
    for t in mine[:8]:
        inside = [f for f in t.order if f in fs]
        out += [f"### Execution order: {t.label}", ""] + [f"{i}. {c.link(f)}" for i, f in enumerate(inside[:40], 1)]
        if len(inside) > 40:
            out += [f"*+{len(inside) - 40} more*"]
        out += [""]
    issues = sc.order_issues(fs, 60)
    if issues:
        out += ["### Data order: the reader runs before the writer", "",
                "*The reader sees the value of the **previous** cycle. Fine for state and feedback; check it for signals that should be fresh.*", ""]
        out += _table(["Variable", "Task", "Reader (runs first)", "Writer (runs later)"],
                      [[_raw(c.link(v)), t.name, _raw(c.link(r)), _raw(c.link(w))] for v, t, r, _rt, w, _wt in issues])
    ifs = sc.task_interfaces(fs, 60)
    if ifs:
        out += ["### Data between tasks", "", "*Written in one task, read in another: the reader gets the latest value whenever it runs.*", ""]
        out += _table(["Variable", "Written in", "Read in"],
                      [[_raw(c.link(v)), ", ".join(t.name for t in wt), ", ".join(t.name for t in rt)] for v, wt, rt in ifs])
    return out


def call_arg_rows(c: "_Ctx", f: str) -> list:
    """[callee, at, arguments] rows: which variables the function hands to each callee (from the `calls` edge metadata)."""
    M, rows = c.M, []
    for e in M.out[f]:
        if e.get("relation") != "calls":
            continue
        md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        for site in md.get("args") or []:
            parts = []
            for j, vs in sorted((site.get("args") or {}).items(), key=lambda kv: int(kv[0])):
                parts.append(f"arg {int(j) + 1}: " + ", ".join(c.link(x) for x in vs if x in M.nodes))
            if parts:
                rows.append([_raw(c.link(e["target"])), f"`L{site.get('line', '')}`", _raw("; ".join(parts))])
    return rows


def call_sequence_lines(c: "_Ctx", f, limit: int = 80) -> list:
    """Numbered list of the calls of f in execution order, conditional / repeated calls marked."""
    seq = c.M.call_sequence(f)
    if not seq:
        return []
    out = []
    for n, (cid, line, ctx) in enumerate(seq[:limit], 1):
        out.append(f"{n}. {c.link(cid)} `L{line}`" + (f" - *{_ctx_text(ctx)}*" if ctx else ""))
    if len(seq) > limit:
        out.append(f"*+{len(seq) - limit} more calls*")
    return out + [""]


def start_points(M: "_Model", funcs: list) -> list:
    """Functions of the scope that no function in the code calls: tasks, init, interrupts, diagnostics, or dead code."""
    called = {e["target"] for e in M.edges if e.get("relation") in CALL and e.get("source") != e.get("target")}
    return [f for f in funcs if f not in called]


def _doc_section(c: _Ctx, i: int) -> list:
    M = c.M
    m = M.meta(i)
    out = ["## Description", ""]
    if M.doxygen(i):
        out += ["> [!quote] Code comment (doxygen, as written)"] + [f"> {ln}" if ln else ">" for ln in
                ["```"] + M.doxygen(i).splitlines() + ["```"]] + [""]
    else:
        out += ["*No doxygen comment in the code.*", ""]
    if m.get("summary") and not m.get("doxygen"):
        out += [f"Summary: {m['summary']}", ""]
    return out


def _footer(c: _Ctx, i, extra: list) -> list:
    M = c.M
    return (["## Software documentation (to be filled later)", ""]
            + _table(["Aspect", "Status"], [["Purpose / requirement ID", "*open*"], ["Design notes", "*open*"],
                                           ["Tests / test cases", "*open*"], ["Change history", "*open*"]])
            + ["## Source facts", ""]
            + _table(["Item", "Value"], [["Node id", f"`{i}`"], ["File", f"`{M.file(i)}:{M.loc(i)}`"]] + extra
                     + [["Generated by", "graphify swdoc - the part above 'My notes' is regenerated on every export"]])
            + ["## My notes", "", USER_BLOCK])



# ---- uniform "signal" rows on the function page: name, description, range, value / enum table, unit

def _enum_text(vt, limit: int = 8) -> str:
    if not isinstance(vt, dict) or not vt:
        return ""
    items = [f"{k}={_esc(v)}" for k, v in vt.items()]
    return "; ".join(items[:limit]) + (f"; +{len(items) - limit} more" if len(items) > limit else "")


def _fmt_range(lo, hi) -> str:
    return f"{lo} … {hi}" if lo is not None and hi is not None and lo not in ("", None) else ""


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _desc_of(c: _Ctx, i, a2l_id=None) -> str:
    """Code comment first sentence, else the A2L description."""
    M = c.M
    d = M.doc_summary(i)
    if not d:
        for nid in (a2l_id, i):
            if nid in M.nodes and M.nodes[nid].get("rationale"):
                d = str(M.nodes[nid]["rationale"]).split(" | ")[0]
                break
    return _short(d)


def _value_text(c: _Ctx, a2l_name: str, unit: str) -> str:
    if not (c.lookup and a2l_name):
        return ""
    lines = c.lookup(a2l_name)
    if not lines:
        return ""
    if len(lines) != 1:
        return None                                      # table: caller points to the note
    m = re.search(r"\]\s*=\s*(.*)$", lines[0].strip())
    val = m.group(1) if m else lines[0].strip()
    if val.startswith("value: not in"):
        return ""
    return val[: -len(unit) - 1] if unit and val.endswith(" " + unit) else val


def _signal_row(c: _Ctx, i, name_cell: str | None = None) -> list:
    """[Name, Description, Range, Value / enum table, Unit] for a variable or calibration node."""
    M = c.M
    m = M.meta(i)
    links = [l for l in M.links.get(i, []) if l["kind"] in ("char", "axis")]
    ref = m.get("calibration_ref") if isinstance(m.get("calibration_ref"), dict) else {}
    a2l_id = links[0].get("id") if links else None
    am = M.meta(a2l_id) if a2l_id in M.nodes else {}
    unit = am.get("unit") or (ref.get("compu_method") or {}).get("unit") or ""
    enum = _enum_text(am.get("verbal_table") or ref.get("verbal_table"))
    if links:
        rng = link_range(links) or _fmt_range(am.get("min"), am.get("max"))
        rng = rng.replace("..", " … ")
    else:
        rng = _fmt_range(ref.get("min"), ref.get("max"))
    val = ""
    if len(links) == 1:
        v = _value_text(c, links[0]["name"], unit)
        val = f"see {c.link(i)}" if v is None else v
    elif len(links) > 1:
        val = f"{len(links)} entries - see {c.link(i)}"
    cell = [x for x in (f"**{val}**" if val else "", f"`{enum}`" if enum else "") if x]
    name = name_cell or c.link(i)
    if links and i in M.nodes and M.t(i) == "calibration_field":
        names = link_names(links) or [l["name"] for l in links]
        name += "<br>`" + names[0] + "`" + (f" +{len(names) - 1}" if len(names) > 1 else "")
    return [_raw(name), _esc(_desc_of(c, i, a2l_id)), rng, _raw("<br>".join(cell)), unit]


SIGNAL_HEAD = ["Name", "Description", "Range", "Value / enum table", "Unit"]


def _map_inputs(c: _Ctx, cals: list) -> list:
    """Rows [map/curve, axis, role, input, description, range, unit] for each map or curve read by the function."""
    M = c.M
    rows = []
    for k in cals:
        for role, ax_rel in (("X", "x_axis"), ("Y", "y_axis"), ("Z", "z_axis")):
            inp = [e["target"] for e in M.out[k] if e.get("relation") == "input_" + role.lower()]
            ax = [e["target"] for e in M.out[k] if e.get("relation") == ax_rel]
            if not inp and not ax:
                continue
            axis_cell = ", ".join(c.link(a) for a in ax) or "-"
            a2l_ax = next((l for a in ax for l in M.links.get(a, []) if l["kind"] == "axis"), None)
            if not inp:                                    # A2L knows the input quantity, the code did not show it
                iq = M.meta(a2l_ax["id"]).get("input_quantity") if a2l_ax and a2l_ax.get("id") in M.nodes else ""
                rows.append([_raw(c.link(k)), role, _raw(axis_cell), _raw(f"`{iq}` *(A2L input quantity)*" if iq else "*not found*"), "", "", ""])
                continue
            for v in inp:
                r = _signal_row(c, v)
                rows.append([_raw(c.link(k)), role, _raw(axis_cell), r[0], r[1], r[2], r[4]])
    return rows


def function_note(c: _Ctx, f: str) -> str:
    M = c.M
    m = M.meta(f)
    name = re.sub(r"\(\)$", "", M.label(f))
    reads = c.vars_of(f, READ)
    writes = c.vars_of(f, WRITE)
    cals = M.targets(f, {CAL_READ}, ("calibration_field",))
    callers = M.sources(f, CALL, ("function",))
    callees = M.targets(f, CALL, ("function",))
    out = _fm({"type": "function", "module": c.module, "source_file": M.file(f), "location": M.loc(f),
               "return_type": m.get("return_type"), "tags": ["graphify/function", f"module/{_slug(c.module)}"],
               "graphify_dataset": c.dataset, "generated": True})
    out += [f"# {name}()", "",
            f"Module: [[{c.module}]] · File: `{M.file(f)}:{M.loc(f)}`"
            + (f" · Returns: `{m['return_type']}`" if m.get("return_type") else ""), ""]
    if M.sched is not None and M.sched.runs_in(f):
        out += ["**Runs in task:** " + ", ".join(t.label for t in M.sched.runs_in(f)[:6])
                + (f" +{len(M.sched.runs_in(f)) - 6} more" if len(M.sched.runs_in(f)) > 6 else ""), ""]
    out += _doc_section(c, f)
    params = m.get("parameters")
    if isinstance(params, list) and params:
        pdoc = m.get("comment_params") if isinstance(m.get("comment_params"), dict) else {}
        out += _table(["Parameter", "Type", "Role", "Meaning (from comment)"],
                      [[f"`{p.get('name', '')}`", f"`{p.get('type', '')}`", p.get("role", ""), pdoc.get(p.get("name"), "")]
                       for p in params if isinstance(p, dict)])
    out += ["## Waterfall", "", "```", " INPUTS    variables (and their writers), calibrations (A2L + value), callers   ▼",
            " THIS FUNCTION", " OUTPUTS   variables written (and their readers), callees                          ▼", "```", ""]

    # ---- inputs
    out += ["### 1 · Inputs", ""]
    wh = any(_when(M.edge(f, v, rel)) for rel in (READ, WRITE) for v in reads + writes)

    def with_when(vs, rel):
        head = SIGNAL_HEAD + (["When"] if wh else [])
        return head, [_signal_row(c, v) + ([_when(M.edge(f, v, rel))] if wh else []) for v in vs[:MAX_ROWS]]

    if reads:
        h_, r_ = with_when(reads, READ)
        out += ["#### Variables read", ""] + _table(h_, r_)
    if cals:
        out += ["#### Calibrations read", ""] + _table(SIGNAL_HEAD, [_signal_row(c, k) for k in cals[:MAX_ROWS]])
        mi = _map_inputs(c, cals)
        if mi:
            out += ["#### Inputs of the maps and curves", ""] + _table(
                ["Map / curve", "Axis", "Axis calibration", "Input variable", "Description", "Range", "Unit"], mi[:MAX_ROWS])
    if callers:
        out += ["#### Callers", "", c.many(callers, 12), ""]
    if not reads and not cals and not callers:
        out += ["*(reads no variable or calibration)*", ""]

    # ---- this function
    out += ["### 2 · This function", "",
            f"- Reads: {len(reads)} variables, {len(cals)} calibrations · Writes: {len(writes)} variables · Calls: {len(callees)}",
            "- Links below are `EXTRACTED` unless marked `INFERRED`", ""]

    # ---- outputs
    out += ["### 3 · Outputs", ""]
    if writes:
        h_, r_ = with_when(writes, WRITE)
        out += ["#### Variables written", ""] + _table(h_, r_)
    if callees:
        seq = call_sequence_lines(c, f)
        out += (["#### Calls (in execution order)", ""] + seq) if seq else ["#### Calls", "", c.many(callees, 20), ""]
        ar = call_arg_rows(c, f)
        if ar:
            out += ["#### Variables passed to the callees", ""] + _table(["Callee", "At", "Arguments"], ar[:MAX_ROWS])
    if not writes and not callees:
        out += ["*(writes no variable, calls nothing)*", ""]

    if c.mermaid:
        out += _mermaid(c, f, reads, cals, writes)
    return "\n".join(out + _footer(c, f, [["Calls / called by", f"{len(callees)} / {len(callers)}"]]))


def _cal_row(c: _Ctx, f: str, k: str) -> list:
    M = c.M
    links = [l for l in M.links.get(k, []) if l["kind"] == "char"]
    e = M.edge(f, k, {CAL_READ}) or {}
    em = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
    idx = " ".join(em.get("index") or [])
    mask = ", ".join(_hex(x) for x in (em.get("mask") or []))
    im = " · ".join(x for x in (idx, mask) if x) or "-"
    via = ", ".join(sorted({l["via"] for l in links if l["via"]})) or "exact"
    if e.get("confidence") == "INFERRED":
        via += " · `INFERRED`"
    axl = [l for l in M.links.get(k, []) if l["kind"] == "axis"]
    if not links and axl:
        names = ", ".join(c.link(l["id"]) if l.get("id") in c.stems else f"`{l['name']}`" for l in axl[:4])
        los = [l["min"] for l in axl if l["min"] is not None]
        his = [l["max"] for l in axl if l["max"] is not None]
        return [_raw(c.link(k)), _raw("axis " + names), im, "AXIS_PTS", f"{min(los)}..{max(his)}" if los and his else "",
                f"see {c.link(k)}" if c.lookup else "", "exact"]
    if not links:
        return [_raw(c.link(k)), "- (not in A2L)", im, "", "", "", ""]
    if len(links) == 1:
        l = links[0]
        name, typ, rng = f"`{l['name']}`", l["char_type"], link_range(links)
        val = ""
        if c.lookup:
            lines = c.lookup(l["name"])
            val = (lines[0].strip() if len(lines) == 1 else f"see {c.link(k)}") if lines else ""
    else:
        names = link_names(links)
        name = f"{len(names)} entries, e.g. " + ", ".join(f"`{n}`" for n in names[:3])
        typ = ", ".join(sorted({l["char_type"] for l in links if l["char_type"]}))
        rng, val = link_range(links), f"see {c.link(k)}"
    return [_raw(c.link(k)), _raw(name), im, typ, rng, val, _raw(via)]


def _mermaid(c: _Ctx, f: str, reads, cals, writes) -> list:
    M = c.M
    ids: dict = {}

    def nid(x) -> str:
        return ids.setdefault(x, f"n{len(ids)}")

    def lab(x) -> str:
        if x in c.stems:                                   # exact note name, so Obsidian's internal-link resolves it
            return c.stems[x].replace('"', "")
        return re.sub(r"[\"\[\]{}()<>|]", "", re.sub(r"\(\)$", "", M.label(x)))[:48]

    lines = ["## Optional diagram", "", "```mermaid", "flowchart TB", f'  {nid(f)}["{lab(f)}"]']
    for v in reads[:12]:
        for w in [x for x in M.sources(v, WRITE, ("function",)) if x != f][:3]:
            lines.append(f'  {nid(w)}["{lab(w)}"] --> {nid(v)}("{lab(v)}")')
        lines.append(f'  {nid(v)}("{lab(v)}") --> {nid(f)}')
    for k in cals[:12]:
        lines.append(f'  {nid(k)}{{{{"{lab(k)}"}}}} --> {nid(f)}')
    for v in writes[:12]:
        lines.append(f'  {nid(f)} --> {nid(v)}("{lab(v)}")')
        for r in [x for x in M.sources(v, READ, ("function",)) if x != f][:3]:
            lines.append(f'  {nid(v)}("{lab(v)}") --> {nid(r)}["{lab(r)}"]')
    linked = [i for x, i in ids.items() if x in c.stems]
    if linked:
        lines.append("  class " + ",".join(linked) + " internal-link;")
    return lines + ["```", ""]


def variable_note(c: _Ctx, v: str) -> str:
    M = c.M
    m = M.meta(v)
    writers, readers = M.sources(v, WRITE, ("function",)), M.sources(v, READ, ("function",))
    out = _fm({"type": "variable", "module": c.module, "source_file": M.file(v), "location": M.loc(v),
               "storage": m.get("storage"), "defined_in": m.get("defined_in"),
               "tags": ["graphify/variable", f"module/{_slug(c.module)}"], "generated": True})
    out += [f"# {M.label(v)}", "", f"Module: [[{c.module}]] · File: `{M.file(v)}:{M.loc(v)}`"
            + (f" · Storage: {m['storage']}" if m.get("storage") else "")
            + (f" · Defined in `{m['defined_in']}`" if m.get("defined_in") else ""), ""]
    out += _doc_section(c, v)
    ln = [l for l in M.links.get(v, []) if l["kind"] == "char"]
    if ln:
        out += ["### A2L (measurable)", ""] + _table(["A2L characteristic", "Type", "Range"],
                                                       [[f"`{l['name']}`", l["char_type"], link_range([l])] for l in ln[:MAX_ROWS]])
    elif m.get("calibration_ref"):
        ref = m["calibration_ref"]
        out += ["### A2L (measurement)", "", f"`{ref.get('a2l_name', '')}` · type {ref.get('var_type', '')} · range {ref.get('min')} … {ref.get('max')}", ""]
    cs = M.sources(v, {"carried_in"}, ("can_signal",))
    if cs:
        rows = []
        for sid in cs:
            sm, e = M.meta(sid), M.edge(sid, v, {"carried_in"}) or {}
            em = e.get("metadata") or {}
            ms, ss = getattr(c, "can_stems", None) or ({}, {})
            msg = sm.get("message") or ""
            rows.append([_raw(f"[[{ms.get(msg, _stem(msg))}]]"), _raw(f"[[{ss[(msg, M.label(sid))]}]]" if (msg, M.label(sid)) in ss else M.label(sid)), sm.get("unit") or "",
                         _enum_text(sm.get("enum") or {}), f"{em.get('index', '')}", f"`{em.get('getter', '')}` L{em.get('getter_line', '')}"])
        out += ["### CAN signal (filled by the getter in the signal reader function)", ""] + _table(
            ["Message", "Signal", "Unit", "Value / enum table", "Signal index", "Getter"], rows)
    for title, ids, rel in (("Written by", writers, WRITE), ("Read by", readers, READ)):
        if ids:
            wh = any(_when(M.edge(w, v, rel)) for w in ids)
            out += [f"### {title}", ""] + _table(["Function", "At", "Summary"] + (["When"] if wh else []),
                    [[_raw(c.link(w)), _raw(c.at(M.edge(w, v, rel) or {})), M.doc_summary(w)] + ([_when(M.edge(w, v, rel))] if wh else [])
                     for w in ids[:MAX_ROWS]])
    out += flow_sections(c, v)
    if m.get("address_taken"):
        out += ["> [!warning] Address taken - may also be written through a pointer", ""]
    return "\n".join(out + _footer(c, v, []))


def calibration_note(c: _Ctx, k: str) -> str:
    M = c.M
    m = M.meta(k)
    links = M.links.get(k, [])
    chars = [l for l in links if l["kind"] == "char"]
    readers = M.sources(k, {CAL_READ}, ("function",))
    path = m.get("calibration_path") or M.label(k)
    out = _fm({"type": "calibration", "module": c.module, "calibration_path": path, "accessed_via": m.get("accessed_via"),
               "a2l_entries": len(chars), "tags": ["graphify/calibration", f"module/{_slug(c.module)}"],
               "graphify_dataset": c.dataset, "generated": True})
    out += [f"# {path}", "", f"Module: [[{c.module}]]" + (f" · Accessed via `{m['accessed_via']}`" if m.get("accessed_via") else "")
            + f" · Seen at `{M.file(k)}:{M.loc(k)}`", ""]
    out += _doc_section(c, k)
    if m.get("from_struct_type"):
        parent = c.cal_by_path.get(m["from_struct_type"])
        out += [f"> [!note] Member of the struct {c.link(parent) if parent else '`' + m['from_struct_type'] + '`'} "
                "(read as a whole in the code; this member is `INFERRED` from the header type)", ""]
    kids = c.children.get(path, [])
    if kids:
        out += ["### Struct members", "", ", ".join(c.link(i) for i in sorted(kids, key=lambda x: M.label(x))[:60]), ""]
    out += ["### A2L entries", ""]
    if not chars:
        out += ["*No A2L entry is linked to this calibration (not in the A2L, or the join found no match).*", ""]
    else:
        rows = []
        for l in sorted(chars, key=lambda x: x["name"])[:MAX_ENTRIES]:
            axes = sorted({a for e in M.out.get(l["id"], []) if e.get("relation") in AXIS_REL
                           for a in [e["target"]] if M.t(a) == "a2l_axis" and a in c.stems})
            rows.append([f"`{l['name']}`", l["index"] or "-", _hex(l["mask"]) if l["mask"] else "-", l["char_type"],
                         "" if l["min"] is None else l["min"], "" if l["max"] is None else l["max"], l["via"] or "exact",
                         _raw(", ".join(c.link(a) for a in axes))])
        out += _table(["Characteristic", "Index", "Mask", "Type", "Min", "Max", "Link", "Axes"], rows)
        if len(chars) > MAX_ENTRIES:
            out += [f"*+{len(chars) - MAX_ENTRIES} more entries - see graph.json*", ""]
        desc = M.nodes[chars[0]["id"]].get("rationale") if chars[0].get("id") in M.nodes else ""
        if desc:
            out += [f"> [!quote] A2L description (first entry)\n> {desc}", ""]
    if c.lookup and chars:
        blocks, n = [], 0
        for l in sorted(chars, key=lambda x: x["name"]):
            lines = c.lookup(l["name"])
            if lines:
                blocks.append("\n".join(x.rstrip() for x in lines))
                n += 1
            if n >= MAX_VALUE_LINKS:
                break
        out += [f"### Values ({c.dataset})", ""]
        out += (["```"] + ["\n\n".join(blocks)] + ["```", ""]) if blocks else ["*No value in this dataset.*", ""]
        if len(chars) > n and n >= MAX_VALUE_LINKS:
            out += [f"*first {MAX_VALUE_LINKS} of {len(chars)} entries - `graphify params show NAME` for the rest*", ""]
    if readers:
        rows = []
        for r in readers[:MAX_ROWS]:
            e = M.edge(r, k, {CAL_READ}) or {}
            em = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
            rows.append([_raw(c.link(r)), _raw(c.at(e)), " ".join(em.get("index") or []) or "-",
                         ", ".join(_hex(x) for x in (em.get("mask") or [])) or "-", e.get("confidence", "")])
        out += ["### Read by", ""] + _table(["Function", "At", "Index", "Mask", "Confidence"], rows)
    return "\n".join(out + _footer(c, k, []))


def axis_note(c: _Ctx, a: str) -> str:
    M = c.M
    m = M.meta(a)
    users = sorted({e["source"] for e in M.inn[a] if e.get("relation") in AXIS_REL and M.t(e["source"]) == "a2l_characteristic"},
                   key=lambda x: M.label(x).lower())
    out = _fm({"type": "axis", "module": c.module, "tags": ["graphify/axis", f"module/{_slug(c.module)}"], "generated": True})
    out += [f"# {M.label(a)}", "", f"A2L axis points · symbol `{m.get('internal_symbol', '')}`"
            f" · range {m.get('min')} … {m.get('max')} · max points {m.get('max_axis_points')}", ""]
    if M.nodes[a].get("rationale"):
        out += [f"> [!quote] A2L description\n> {M.nodes[a]['rationale']}", ""]
    if c.lookup:
        lines = c.lookup(M.label(a))
        if lines:
            out += [f"### Values ({c.dataset})", "", "```"] + [x.rstrip() for x in lines] + ["```", ""]
    if users:
        out += ["### Used by", "", ", ".join(f"`{M.label(u)}`" for u in users[:MAX_ENTRIES]), ""]
        direct = {e["source"] for e in M.inn[a] if e.get("relation") == "mapped_to_a2l_axis_pts"}
        via = {e["source"] for u in users for e in M.inn[u] if e.get("relation") == "mapped_to_a2l_characteristic"}
        cals = sorted(((direct | via) & set(c.stems)), key=lambda x: M.label(x).lower())
        if cals:
            out += ["### Calibrations that use this axis", "", c.many(cals, 40), ""]
    return "\n".join(out + _footer(c, a, []))


def border_note(c: _Ctx, i: str) -> str:
    M = c.M
    out = _fm({"type": "function", "border": True, "source_file": M.file(i), "location": M.loc(i),
               "tags": ["graphify/function", "graphify/border"], "generated": True})
    out += [f"# {re.sub(r'[(][)]$', '', M.label(i))}()", "", f"File: `{M.file(i)}:{M.loc(i)}`", "",
            f"> [!note] Not part of the exported module {c.module} (declared only, or defined elsewhere). Export its module for the full note.", ""]
    out += _doc_section(c, i)
    return "\n".join(out + ["## My notes", "", USER_BLOCK])



# ---------------------------------------------------------------------------
# component view: functions of the module, local signals, component signals
# ---------------------------------------------------------------------------



def _group_of(M: _Model, i) -> str:
    """Outside 'component' of a function: the folder of its source file (a file without folder: its name)."""
    f = M.file(i)
    if not f:
        return "?"
    parent = str(Path(f.replace("\\", "/")).parent)
    g = parent if parent not in ("", ".") else Path(f).stem
    m = M.meta(i)
    if m.get("declaration_only") and not m.get("has_definition"):
        g += " (declared only)"                      # prototype in a header: the definition is elsewhere
    return g


def component_signals(M: _Model, funcs: list, vars_: list) -> dict:
    """Split the variables touched by the module's functions.

    input  : read inside and written outside (or written nowhere: external input)
    output : written inside and read outside
    local  : everything else that is touched inside
    """
    fset = set(funcs)
    inputs, outputs, local = {}, {}, []
    for v in vars_:
        ws, rs = M.sources(v, WRITE, ("function",)), M.sources(v, READ, ("function",))
        wi, ri = [w for w in ws if w in fset], [r for r in rs if r in fset]
        wo, ro = [w for w in ws if w not in fset], [r for r in rs if r not in fset]
        if not wi and not ri:
            continue
        hit = False
        if ri and (wo or not wi):
            inputs[v] = {"inside": ri, "outside": wo, "external": not ws}
            hit = True
        if wi and ro:
            outputs[v] = {"inside": wi, "outside": ro}
            hit = True
        if not hit:
            local.append(v)
    return {"inputs": inputs, "outputs": outputs, "local": local}


MAX_FLOW_EDGES = 80      # call arrows drawn in the call flow
MAX_FLOW_GROUPS = 15    # outside components drawn in the call flow


def _mm(text: str, n: int = 40) -> str:
    return re.sub(r"[\"\[\]{}()<>|#;`]", "", str(text))[:n]


def call_flow(c: _Ctx, funcs: list) -> list:
    """Mermaid call graph: which function of the module calls which one. Callers and callees outside the module
    are collapsed into one box per outside component (folder of the source file)."""
    M = c.M
    fset = set(funcs)
    ids: dict = {}

    def nid(x, pre="n") -> str:
        return ids.setdefault((pre, x), f"{pre}{len(ids)}")

    inner = [(f, x) for f in funcs for x in M.targets(f, CALL, ("function",)) if x in fset and x != f]
    callers: dict = defaultdict(set)          # outside group -> module functions it calls
    callees: dict = defaultdict(set)          # outside group -> module functions calling into it
    for f in funcs:
        for x in M.sources(f, CALL, ("function",)):
            if x not in fset:
                callers[_group_of(M, x)].add(f)
        for x in M.targets(f, CALL, ("function",)):
            if x not in fset:
                callees[_group_of(M, x)].add(f)
    gl = sorted(callers, key=lambda g: (-len(callers[g]), g))[:MAX_FLOW_GROUPS]
    gr = sorted(callees, key=lambda g: (-len(callees[g]), g))[:MAX_FLOW_GROUPS]
    byfile = defaultdict(list)
    for f in funcs:
        byfile[M.file(f) or "?"].append(f)

    L = ["```mermaid", "flowchart LR"]
    for g in gl:
        L.append(f'  {nid(g, "gl")}(["{_mm(g)}"])')
    for fl in sorted(byfile):
        clustered = len(byfile) > 1
        if clustered:
            L.append(f'  subgraph {nid(fl, "sf")}["{_mm(fl, 60)}"]')
        for f in byfile[fl]:
            L.append(f'  {nid(f, "f")}["{c.stems[f].replace(chr(34), "")}"]')
        if clustered:
            L.append("  end")
    for g in gr:
        L.append(f'  {nid(g, "gr")}(["{_mm(g)}"])')
    edges = []
    for g in gl:
        edges += [f'  {nid(g, "gl")} --> {nid(f, "f")}' for f in sorted(callers[g], key=lambda x: M.label(x).lower())]
    edges += [f'  {nid(f, "f")} --> {nid(x, "f")}' for f, x in sorted(set(inner), key=lambda e: (M.label(e[0]).lower(), M.label(e[1]).lower()))]
    for g in gr:
        edges += [f'  {nid(f, "f")} --> {nid(g, "gr")}' for f in sorted(callees[g], key=lambda x: M.label(x).lower())]
    L += edges[:MAX_FLOW_EDGES]
    L.append("  class " + ",".join(nid(f, "f") for f in funcs) + " internal-link;")   # Obsidian: block text = note name
    L.append("```")
    if len(edges) > MAX_FLOW_EDGES:
        L += ["", f"> [!note] {len(edges) - MAX_FLOW_EDGES} more call arrows are not drawn; the function tables list the calls of each function."]
    return L


def _partner(c: _Ctx, fs: list, external: bool = False, limit: int = 4) -> str:
    if external:
        return "*external input (no writer in the code)*"
    return c.many(fs, limit) if fs else "-"


def module_note(c: _Ctx, funcs: list, vars_: list, cals: list, border: list, inside_vars: set) -> str:
    M = c.M
    out = _fm({"type": "module", "tags": ["graphify/module", f"module/{_slug(c.module)}"], "generated": True})
    out += [f"# {c.module}", "", f"[[_HOME]] · {len(funcs)} functions · {len(vars_)} variables · {len(cals)} calibrations", ""]
    sig = component_signals(M, funcs, vars_)
    nin, nout = len(sig["inputs"]), len(sig["outputs"])
    sp = start_points(M, funcs)
    out += ["## Start points", "", "Functions of this component that no function in the code calls (tasks, init, interrupts, diagnostics - or unused code).", ""]
    out += (_table(["Function", "Summary", "Calls", "Reaches"],
                   [[c.link(f), _esc(_short(M.doc_summary(f), 100)), len(M.targets(f, CALL, ("function",))), f"{M.reach(f)} functions"]
                    for f in sorted(sp, key=lambda x: M.label(x).lower())[:MAX_ROWS]])
            if sp else ["*none - every function is called from somewhere*", ""])
    out += ["## Call flow", "", "Arrow = calls. A function block opens its note (click it in reading view). Boxes with rounded ends are other components (callers on the left, callees on the right).", ""]
    out += call_flow(c, funcs) + [""]
    out += tasks_section(c, funcs)
    out += ["## Component signals", "", f"{nin} inputs (from other components) · {nout} outputs (read by other components)", ""]
    if sig["inputs"]:
        out += ["### Component inputs", ""] + _table(SIGNAL_HEAD + ["Read inside by"],
            [_signal_row(c, v) + [_raw(_partner(c, sig["inputs"][v]["inside"]))]
             for v in sorted(sig["inputs"], key=lambda x: M.label(x).lower())[:MAX_ROWS]])
    if sig["outputs"]:
        out += ["### Component outputs", ""] + _table(SIGNAL_HEAD + ["Written inside by"],
            [_signal_row(c, v) + [_raw(_partner(c, sig["outputs"][v]["inside"]))]
             for v in sorted(sig["outputs"], key=lambda x: M.label(x).lower())[:MAX_ROWS]])
    if cals:
        out += ["## Calibrations", "", f"{len(cals)} calibrations read by the functions of this component.", ""] + _table(
            SIGNAL_HEAD + ["Read by"],
            [_signal_row(c, k) + [_raw(c.many(M.sources(k, {CAL_READ}, ("function",)), 4))]
             for k in sorted(cals, key=lambda x: M.label(x).lower())[:MAX_ROWS]])
        if len(cals) > MAX_ROWS:
            out += [f"*+{len(cals) - MAX_ROWS} more calibrations - see the calibration notes*", ""]
    byfile = defaultdict(list)
    for f in funcs:
        byfile[M.file(f)].append(f)
    out += ["## Functions", ""]
    for fl in sorted(byfile):
        out += [f"### `{fl}`", ""] + _table(["Function", "Summary", "Calls", "Reads", "Writes"],
            [[_raw(c.link(f)), M.doc_summary(f), len(M.targets(f, CALL, ("function",))),
              len(c.vars_of(f, READ)) + len(M.targets(f, {CAL_READ}, ("calibration_field",))),
              len(c.vars_of(f, WRITE))] for f in byfile[fl]])
    return "\n".join(out + ["## My notes", "", USER_BLOCK])


# ---------------------------------------------------------------------------
# scope and vault
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CAN chapter (messages and signals of the DBC, nodes `can_message` / `can_signal`)
# ---------------------------------------------------------------------------

def _num(x) -> str:
    if isinstance(x, float) and x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return "" if x is None else (f"{x:g}" if isinstance(x, float) else str(x))


def can_data(M: "_Model") -> dict | None:
    """Plain data of the CAN chapter (shared by the Obsidian notes, the single file and the html), or None."""
    msgs = [i for i in M.nodes if M.t(i) == "can_message"]
    if not msgs:
        return None
    ecu = next((M.label(i) for i in M.nodes if M.t(i) == "can_node" and M.meta(i).get("is_selected_node")), "")
    dbc = next((M.label(i) for i in M.nodes if M.t(i) == "can_dbc"), "")
    out = []
    for m in msgs:
        mm = M.meta(m)
        sigs = []
        for sid in sorted((e["target"] for e in M.out[m] if e.get("relation") == "has_signal" and M.t(e["target"]) == "can_signal"),
                          key=lambda x: (min(M.meta(x).get("bits") or [0]), M.label(x).lower())):
            sm = M.meta(sid)
            order = {"little_endian": "Intel", "big_endian": "Motorola"}.get(sm.get("byte_order"), str(sm.get("byte_order") or ""))
            mux = sm.get("multiplex")
            cv = sorted({e["target"] for e in M.out[sid] if e.get("relation") == "carried_in" and M.t(e["target"]) == "variable"},
                        key=lambda x: M.label(x).lower())
            lk = []
            for v in cv:
                em = (M.edge(sid, v, {"carried_in"}) or {}).get("metadata") or {}
                lk.append({"var": v, "label": M.label(v), "index": em.get("index"), "getter": em.get("getter") or "",
                           "line": em.get("getter_line"), "file": em.get("getter_file") or "",
                           "readers": M.sources(v, READ, ("function",)), "writers": M.sources(v, WRITE, ("function",)),
                           "trace": [{"level": t["level"], "id": t["id"], "label": M.label(t["id"]), "from": M.label(t["src"]),
                                      "kind": _flow_kind(t["edge"]), "timing": _timing_cell(t["code"]), "delay": t["delay"],
                                      "functions": [M.label(f) for f in (t["edge"].get("metadata") or {}).get("functions", []) if f in M.nodes][:4]}
                                     for t in M.trace_timed(v)]})
            txf = []
            for e in M.out[sid]:
                if e.get("relation") == "computed_by" and M.t(e["target"]) == "function":
                    em = e.get("metadata") or {}
                    srcs = sorted((x["target"] for x in M.out[sid] if x.get("relation") == "derived_from" and M.t(x["target"]) == "variable"
                                   and e["target"] in ((x.get("metadata") or {}).get("functions") or [])), key=lambda x: M.label(x).lower())
                    txf.append({"fn": e["target"], "label": M.label(e["target"]), "macro": em.get("macro") or "", "line": em.get("line"),
                                "file": em.get("file") or "", "sources": [(v, M.label(v)) for v in srcs]})
            sigs.append({"name": M.label(sid), "vars": [(v, M.label(v)) for v in cv], "links": lk, "tx": txf, "desc": str(M.nodes[sid].get("rationale") or ""),
                         "start": sm.get("start_bit"), "len": sm.get("length"), "order": order,
                         "signed": bool(sm.get("signed")), "factor": sm.get("factor"), "offset": sm.get("offset"),
                         "min": sm.get("min"), "max": sm.get("max"), "unit": sm.get("unit") or "",
                         "enum": sm.get("enum") or {}, "rx": list(sm.get("receivers") or []),
                         "mux": ("selector" if sm.get("is_multiplexer") else
                                 (f"{mux.get('selector')}={','.join(map(str, mux.get('ids', [])))}" if mux else ""))})
        out.append({"name": M.label(m), "id": mm.get("frame_id"), "hex": mm.get("frame_id_hex") or "",
                    "dlc": mm.get("dlc"), "cycle": mm.get("cycle_time_ms"), "dir": mm.get("direction") or "",
                    "senders": list(mm.get("senders") or []), "desc": str(M.nodes[m].get("rationale") or ""),
                    "attrs": {k[5:]: v for k, v in mm.items() if k.startswith("attr:")}, "signals": sigs})
    rank = {"TX": 0, "RX": 1, "": 2, "-": 3}
    out.sort(key=lambda x: (rank.get(x["dir"], 2), x["name"].lower()))
    return {"dbc": dbc, "ecu": ecu, "messages": out}


def _can_sig_rows(sigs: list, ecu: str, stems: dict | None = None, sig_stems: dict | None = None, msg: str = "") -> list:
    rows = []
    for g in sigs:
        rng = _fmt_range(_num(g["min"]), _num(g["max"])) if g["min"] is not None and g["max"] is not None else ""
        fo = f"{_num(g['factor'])} · {_num(g['offset'])}"
        pos = f"{g['start']} · {g['len']} · {g['order']}" + (" signed" if g["signed"] else "")
        rx = ", ".join(("**" + r + "**") if r == ecu else r for r in g["rx"])
        cv = ", ".join(f"[[{stems[v]}]]" if stems and v in stems else f"`{lab}`" for v, lab in g.get("vars", []))
        nm = f"[[{sig_stems[(msg, g['name'])]}]]" if sig_stems and (msg, g["name"]) in sig_stems else g["name"]
        rows.append([_raw(nm + (f" *(mux {g['mux']})*" if g["mux"] else "")), _short(g["desc"], 120), pos, rng, fo,
                     g["unit"], _enum_text(g["enum"]), rx, _raw(cv) if cv else ""])
    return rows


CAN_SIG_HEAD = ["Name", "Description", "Start bit · Length · Order", "Range", "Factor · Offset", "Unit", "Value / enum table", "Receivers", "C variable"]


def can_stems(d: dict, taken: set | None = None) -> tuple:
    """Unique note names of the CAN messages and signals: ({message: stem}, {(message, signal): stem})."""
    used = set(taken or ())
    msg, sig = {}, {}

    def pick(base: str, suffix: str) -> str:
        cand, n = base, 1
        if cand.lower() in used:
            cand = f"{base} ({suffix})"
        while cand.lower() in used:
            n += 1
            cand = f"{base} ({suffix} {n})"
        used.add(cand.lower())
        return cand

    for m in d["messages"]:
        msg[m["name"]] = pick(_stem(m["name"]), "CAN message")
    for m in d["messages"]:
        for g in m["signals"]:
            sig[(m["name"], g["name"])] = pick(_stem(g["name"]), m["name"])
    return msg, sig


def can_signal_note(d: dict, m: dict, g: dict, mstem: str, var_stems: dict | None = None) -> str:
    out = _fm({"type": "can_signal", "tags": ["graphify/can"], "message": m["name"], "frame_id": m["id"], "generated": True})
    out += [f"# {g['name']}", "", f"[[_HOME]] · [[CAN]] · [[{mstem}]]", ""]
    rng = _fmt_range(_num(g["min"]), _num(g["max"])) if g["min"] is not None and g["max"] is not None else "-"
    rows = [["Message", _raw(f"[[{mstem}]] (`{m['hex']}`)")], ["Start bit · Length · Order", f"{g['start']} · {g['len']} · {g['order']}" + (" · signed" if g["signed"] else "")],
            ["Factor · Offset", f"{_num(g['factor'])} · {_num(g['offset'])}"], ["Range", rng], ["Unit", g["unit"] or "-"],
            ["Receivers", _raw(", ".join(("**" + r + "**") if r == d["ecu"] else r for r in g["rx"]) or "-")]]
    if g["mux"]:
        rows.append(["Multiplex", g["mux"]])
    out += _table(["Item", "Value"], rows)
    if g["desc"]:
        out += ["## Description", "", g["desc"], ""]
    if g["enum"]:
        out += ["## Value / enum table", ""] + _table(["Value", "Meaning"], [[k, v] for k, v in g["enum"].items()][:200])
    if g.get("tx"):
        vs = var_stems or {}
        out += ["## Computed by (the function returns the value of this signal)", ""] + _table(
            ["Function", "Macro", "At", "Variables the value comes from"],
            [[_raw(f"[[{vs[t['fn']]}]]" if t["fn"] in vs else f"`{t['label']}`"), f"`{t['macro']}`", f"{t['file']}:{t['line']}",
              _raw(", ".join(f"[[{vs[v]}]]" if v in vs else f"`{lab}`" for v, lab in t["sources"]) or "-")] for t in g["tx"]])
    out += ["## C variable (where the CAN value is stored)", ""]
    if not g.get("links") and not g.get("tx"):
        out += ["*Not linked to a variable (no row in the signal configuration table, or no getter call found).*", ""]
    for lk in g.get("links", []):
        vs = var_stems or {}
        out += _table(["Variable", "Signal index", "Getter"], [[_raw(f"[[{vs[lk['var']]}]]" if lk["var"] in vs else f"`{lk['label']}`"),
                    f"{lk['index']}", f"`{lk['getter']}` ({lk['file']}:{lk['line']})"]])
        for title, key in (("Written by", "writers"), ("Read by", "readers")):
            fl = [f for f in lk.get(key, [])][:40]
            if fl:
                out += [f"**{title}:** " + ", ".join(f"[[{vs[f]}]]" if f in vs else f"`{f}`" for f in fl), ""]
        if lk.get("trace"):
            timed = any(t.get("timing") for t in lk["trace"])
            out += ["## Derived signals (where the value goes after the variable is filled)", "",
                    "*Level 1 = computed from the CAN variable, level 2 = computed from level 1, and so on. Plausibility checks, "
                    "debouncing and filters show up as the functions in the last column. Timing / delay: same cycle or later, "
                    "from the execution order of the task.*", ""] + _table(
                ["Level", "Variable", "Computed from"] + (["Timing", "Delay so far"] if timed else []) + ["Kind", "In function"],
                [[t["level"], _raw(f"[[{vs[t['id']]}]]" if t["id"] in vs else f"`{t['label']}`"), _raw(f"`{t['from']}`")]
                 + ([t.get("timing", ""), t.get("delay", "")] if timed else [])
                 + [t["kind"], _raw(", ".join(f"`{x.rstrip('()')}`" for x in t["functions"]))] for t in lk["trace"]])
    out += ["> [!info] Source: the DBC (layout, scaling) and the signal configuration of the code (variable).", "",
            "## My notes", "", USER_BLOCK]
    return "\n".join(out)


def can_message_note(d: dict, m: dict, stems: dict | None = None, sig_stems: dict | None = None) -> str:
    out = _fm({"type": "can_message", "tags": ["graphify/can"], "frame_id": m["id"], "direction": m["dir"], "generated": True})
    out += [f"# {m['name']}", "", f"[[_HOME]] · [[CAN]]", ""]
    rows = [["Frame ID", f"`{m['hex']}` ({m['id']})"], ["Length (DLC)", m["dlc"]],
            ["Cycle time", f"{_num(m['cycle'])} ms" if m["cycle"] else "-"], ["Sender", ", ".join(m["senders"]) or "-"]]
    if m["dir"] in ("TX", "RX"):
        rows.append([f"Direction for {d['ecu']}", {"TX": "sent by the ECU", "RX": "received by the ECU"}[m["dir"]]])
    rows += [[f"attr {k}", v] for k, v in m["attrs"].items()]
    out += _table(["Item", "Value"], rows)
    if m["desc"]:
        out += ["## Description", "", m["desc"], ""]
    out += [f"## Signals ({len(m['signals'])})", ""] + _table(CAN_SIG_HEAD, _can_sig_rows(m["signals"], d["ecu"], stems, sig_stems, m["name"]))
    out += ["> [!info] Source: the DBC only. Bit layout is the DBC start bit; Motorola = start bit is the MSB.", "",
            "## My notes", "", USER_BLOCK]
    return "\n".join(out)


def can_notes(d: dict, var_stems: dict | None = None, cstems: tuple | None = None) -> dict:
    """{relative path: text}: can/CAN.md (index), can/messages/<message>.md and can/signals/<signal>.md"""
    stems, sig_stems = cstems or can_stems(d)
    out = _fm({"type": "can", "tags": ["graphify/can"], "generated": True})
    nsig = sum(len(m["signals"]) for m in d["messages"])
    out += ["# CAN", "", "[[_HOME]]", "",
            f"> [!info] {len(d['messages'])} messages · {nsig} signals from `{d['dbc']}`"
            + (f" · only messages the ECU `{d['ecu']}` sends (TX) or receives (RX)" if d["ecu"] else " · whole bus") + "", ""]
    for title, key in (("Sent by the ECU (TX)", "TX"), ("Received by the ECU (RX)", "RX"), ("Other messages", "")):
        ms = [m for m in d["messages"] if (m["dir"] if m["dir"] in ("TX", "RX") else "") == key]
        if not ms:
            continue
        out += [f"## {title}", ""] + _table(
            ["Message", "Frame ID", "DLC", "Cycle [ms]", "Sender", "Signals", "Description"],
            [[_raw(f"[[{stems[m['name']]}]]"), m["hex"], m["dlc"], _num(m["cycle"]) or "-", ", ".join(m["senders"]),
              len(m["signals"]), _short(m["desc"], 80)] for m in ms])
    out += ["## My notes", "", USER_BLOCK]
    notes = {"can/CAN.md": "\n".join(out)}
    for m in d["messages"]:
        notes[f"can/messages/{stems[m['name']]}.md"] = can_message_note(d, m, var_stems, sig_stems)
        for g in m["signals"]:
            notes[f"can/signals/{sig_stems[(m['name'], g['name'])]}.md"] = can_signal_note(d, m, g, stems[m["name"]], var_stems)
    return notes


def build_vault(raw: dict, patterns: list, exclude: list | None = None, lookup=None, dataset: str = "",
                mermaid: bool = False, module: str | None = None, want_notes: bool = True, tasks: dict | None = None) -> dict:
    """-> {"notes": {relative path: text}, "module": name, "counts": {...}, "empty": bool}."""
    M = _Model(raw)
    pats = ["" if p.strip() in (".", "*", "/") else _norm(p) for p in patterns]      # "." / "*" = the whole software
    excl = [_norm(x) for x in (exclude or [])]

    def inside(i) -> bool:
        s = _norm(M.file(i))
        return any(p in s for p in pats) and not any(x in s for x in excl)

    def defined(i) -> bool:      # a header prototype (clang, no definition) is not part of the module
        m = M.meta(i)
        return bool(m.get("has_definition")) or not m.get("declaration_only")

    funcs = sorted((i for i in M.nodes if M.t(i) == "function" and inside(i) and M.file(i) and defined(i)),
                   key=lambda x: M.label(x).lower())
    if not funcs:
        return {"notes": {}, "module": "", "counts": {}, "empty": True}
    module = module or patterns[0].strip("/\\ ") or "module"
    roms = {str(M.meta(i).get("calibration_path") or "").split(".")[0] for i in M.nodes if M.t(i) == "calibration_field"}
    plumbing = {i for i in M.nodes if M.t(i) == "variable" and (
        M.label(i) in roms or any(e.get("relation") == "exposes_calibration_field" for e in M.out[i]))}
    from graphify.swdoc_sched import Schedule
    M.sched = Schedule(M, plumbing, tasks)
    inside_vars = {i for i in M.nodes if M.t(i) == "variable" and inside(i)} - plumbing
    vars_ = set(inside_vars)
    cals = set()
    for f in funcs:
        vars_ |= set(M.targets(f, READ | WRITE, ("variable",))) - plumbing
        cals |= set(M.targets(f, {CAL_READ}, ("calibration_field",)))
    axes = set()
    for k in cals:
        for l in M.links.get(k, []):
            if l["kind"] == "axis" and l.get("id") in M.nodes:
                axes.add(l["id"])
            elif l.get("id") in M.nodes:
                axes |= {e["target"] for e in M.out[l["id"]] if e.get("relation") in AXIS_REL and M.t(e["target"]) == "a2l_axis"}
    border = set()
    for f in funcs:
        border |= set(M.targets(f, CALL, ("function",))) | set(M.sources(f, CALL, ("function",)))
    for v in vars_:
        border |= set(M.sources(v, READ | WRITE, ("function",)))
    border -= set(funcs)

    # unique note names (case-insensitive); a clash gets the file name or the type as a suffix
    stems, used = {}, set()
    for group, kind in ((funcs, "function"), (sorted(vars_), "variable"), (sorted(cals), "calibration"),
                        (sorted(axes), "axis"), (sorted(border), "function")):
        for i in sorted(group, key=lambda x: (M.label(x).lower(), x)):
            base = _stem(M.label(i)) if kind != "calibration" else _stem(M.meta(i).get("calibration_path") or M.label(i))
            cand = base
            if cand.lower() in used:
                tail = Path(M.file(i)).stem if M.file(i) else kind
                cand = f"{base} ({tail})"
            n = 1
            while cand.lower() in used:
                cand = f"{base} ({kind} {n})"
                n += 1
            used.add(cand.lower())
            stems[i] = cand
    mod_stem = _stem(module)
    if mod_stem.lower() in used:
        mod_stem += " (module)"
    c = _Ctx(M, stems, mod_stem, lookup, dataset, mermaid)
    c.plumbing = plumbing
    can = can_data(M)
    cstems = can_stems(can, used) if can else None
    c.can_stems = cstems
    for k in cals:
        p = M.meta(k).get("calibration_path")
        if p:
            c.cal_by_path[p] = k
            if M.meta(k).get("from_struct_type"):
                c.children[M.meta(k)["from_struct_type"]].append(k)
    for lst in c.children.values():
        lst.sort(key=lambda x: M.label(x).lower())
    if not want_notes:           # the HTML export needs the scope and the context, not the markdown
        return {"notes": {}, "module": mod_stem, "empty": False, "ctx": c, "funcs": funcs, "vars": sorted(vars_),
                "cals": sorted(cals), "axes": sorted(axes), "border": sorted(border), "plumbing": plumbing,
                "can": can, "stems": stems, "cstems": cstems, "counts": {"functions": len(funcs), "variables": len(vars_), "calibrations": len(cals),
                           "axes": len(axes), "border": len(border)}}
    notes = {}
    for f in funcs:
        notes[f"functions/{stems[f]}.md"] = function_note(c, f)
    for v in sorted(vars_):
        notes[f"variables/{stems[v]}.md"] = variable_note(c, v)
    for k in sorted(cals):
        notes[f"calibrations/{stems[k]}.md"] = calibration_note(c, k)
    for a in sorted(axes):
        notes[f"axes/{stems[a]}.md"] = axis_note(c, a)
    for b in sorted(border):
        notes[f"border/{stems[b]}.md"] = border_note(c, b)
    notes[f"modules/{mod_stem}.md"] = module_note(c, funcs, sorted(vars_), sorted(cals), sorted(border), inside_vars)
    return {"notes": notes, "module": mod_stem, "empty": False, "can": can, "stems": stems, "cstems": cstems,
            "counts": {"functions": len(funcs), "variables": len(vars_), "calibrations": len(cals), "axes": len(axes),
                       "border": len(border)}}



# ---------------------------------------------------------------------------
# one single file for the whole software
# ---------------------------------------------------------------------------

_HEAD = re.compile(r"^(#{1,6}) +(.*?)\s*$")


def _demote(lines: list, by: int) -> list:
    """Shift markdown headings down by `by` levels outside code fences (level 7+ becomes a bold line)."""
    out, fence = [], False
    for ln in lines:
        if ln.startswith("```"):
            fence = not fence
        m = None if fence else _HEAD.match(ln)
        if not m:
            out.append(ln)
            continue
        lvl = len(m.group(1)) + by
        out.append(("#" * lvl + " " + m.group(2)) if lvl <= 6 else f"**{m.group(2)}**")
    return out


def _body(text: str, cut: str | None, drop_h1: bool = True) -> list:
    """Note text without frontmatter, H1 and the per-note footer."""
    lines = text.split("\n")
    if lines and lines[0] == "---":
        lines = lines[lines.index("---", 1) + 1:] if "---" in lines[1:] else lines
    while lines and not lines[0].strip():
        lines.pop(0)
    if drop_h1 and lines and lines[0].startswith("# "):
        lines = lines[1:]
    if cut:
        for i, ln in enumerate(lines):
            if ln.startswith(cut):
                lines = lines[:i]
                break
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def single_file(res: dict, title: str = "Software", dataset: str = "") -> str:
    """Merge the notes of build_vault() into ONE markdown file.

    # title
    ## Overview      (module note: call flow, component signals, calibrations, function tables)
    ## Functions     ### <note name> per function ...
    ## Variables / Calibrations / Axes / Functions outside the scope
    ## My notes
    Every [[note]] link becomes [[#note]] (a link to the heading in this file). The per-note footers (software
    documentation placeholders, source facts, user block) are dropped: the file has one user block at the end."""
    notes = res["notes"]
    titles = {}                                    # note stem -> its heading
    for rel in notes:
        titles[Path(rel).stem] = rel
    mod_rel = next(r for r in notes if r.startswith("modules/"))
    sections = [("Functions", "functions/", "## Software documentation"), ("Variables", "variables/", "## Software documentation"),
                ("Calibrations", "calibrations/", "## Software documentation"), ("Axes", "axes/", "## Software documentation"),
                ("Functions outside the scope", "border/", "## My notes")]

    out = [f"# {title}", ""]
    counts = res.get("counts", {})
    out += [f"> [!info] One file for the whole software, generated by `graphify swdoc --single-file`. "
            f"{counts.get('functions', 0)} functions · {counts.get('variables', 0)} variables · "
            f"{counts.get('calibrations', 0)} calibrations · {counts.get('axes', 0)} axes"
            + (f" · values from dataset `{dataset}`" if dataset else "") + ". Links jump to the heading in this file.", "",
            "## Contents", ""]
    toc = ["- [[#Overview]]"] + [f"- [[#{h}]] ({sum(1 for r in notes if r.startswith(pre))})" for h, pre, _ in sections
                                 if any(r.startswith(pre) for r in notes)]
    if res.get("can"):
        toc.append(f"- [[#CAN messages]] ({len(res['can']['messages'])})")
    out += toc + ["", "## Overview", ""]
    ov = [{"## Functions": "## Function overview", "## Calibrations": "## Calibration overview"}.get(ln, ln)
          for ln in _body(notes[mod_rel], "## My notes")]          # keep every heading of the file unique
    out += [ln.replace(" A function block opens its note (click it in reading view).", "") for ln in _demote(ov, 1)] + [""]
    for head, pre, cut in sections:
        rels = sorted((r for r in notes if r.startswith(pre)), key=lambda r: Path(r).stem.lower())
        if not rels:
            continue
        out += [f"## {head}", ""]
        for r in rels:
            out += [f"### {Path(r).stem}", ""] + _demote(_body(notes[r], cut), 3) + [""]
    can = res.get("can")
    if can:
        out += ["## CAN messages", ""]
        cn = can_notes(can, res.get("stems"), res.get("cstems"))
        for rel in sorted(cn, key=lambda r: (r != "can/CAN.md", Path(r).stem.lower())):
            body = [ln for ln in _body(cn[rel], "## My notes") if not ln.startswith("[[_HOME]]")]
            if rel == "can/CAN.md":
                out += _demote(body, 2) + [""]
            else:
                out += [f"### {Path(rel).stem}", ""] + _demote(body, 3) + [""]
                titles[Path(rel).stem] = rel
    out += ["## My notes", "", USER_BLOCK]
    text = "\n".join(out)
    # links: [[Stem]] -> [[#Stem]]; the home link and the module link have no heading of their own
    mod_stem = Path(mod_rel).stem

    def link(m) -> str:
        name = m.group(1)
        if name == mod_stem:
            return "[[#Overview]]"
        return f"[[#{name}]]" if name in titles else m.group(0)

    text = re.sub(r"\[\[([^\]#|]+)\]\]", link, text)
    text = re.sub(r"^\[\[_HOME\]\] · ", "", text, flags=re.M)
    text = re.sub(r"^  class [\w,]+ internal-link;\n", "", text, flags=re.M)      # a diagram block cannot open a heading
    fm = "\n".join(_fm({"type": "software", "tags": ["graphify/software"], "generated": True}))
    return fm + "\n" + text


def _home(modules: dict) -> str:
    out = _fm({"type": "home", "tags": ["graphify/home"], "generated": True})
    out += ["# Software documentation", "", "Generated by `graphify swdoc`. One note per module:", ""]
    out += [f"- [[{m}]]" for m in sorted(modules)] + ["", "## My notes", "", USER_BLOCK]
    return "\n".join(out)


class _Vault:
    """Writes the notes; never overwrites a file graphify did not create; keeps the user block."""

    def __init__(self, out: Path):
        self.out = out
        try:
            data = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        files = data.get("files", {})
        self.owned_module = dict(files) if isinstance(files, dict) else {}
        self.owned = set(self.owned_module)
        self.modules = dict(data.get("modules", {}))
        self.written, self.skipped = set(), []

    def write(self, rel: str, text: str, module: str = "") -> None:
        from graphify.paths import write_text_atomic
        target = self.out / rel
        if target.exists():
            if rel not in self.owned:
                self.skipped.append(rel)
                return
            old = target.read_text(encoding="utf-8", errors="replace")
            if USER_MARK in old and USER_MARK in text:
                text = text[:text.index(USER_MARK)] + old[old.index(USER_MARK):]
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, text)
        self.written.add(rel)
        self.owned_module[rel] = module

    def finish(self, module: str, prune: bool) -> int:
        """--prune removes only notes of THIS module that were not generated again (other modules stay)."""
        pruned = 0
        if prune:
            for rel in sorted(self.owned - self.written):
                if self.owned_module.get(rel) == module and (self.out / rel).is_file():
                    (self.out / rel).unlink()
                    self.owned_module.pop(rel, None)
                    pruned += 1
        self.modules[module] = module
        self.write("_HOME.md", _home(self.modules))
        (self.out / MANIFEST).write_text(json.dumps({"files": self.owned_module, "modules": self.modules}, indent=1),
                                         encoding="utf-8")
        return pruned


def _read_tasks(spec):
    """--tasks: a json file {"Entry_Function": "10 ms"} or inline 'Entry_Function=10 ms,Other=20 ms'. None = error."""
    if not spec:
        return {}
    p = Path(spec)
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected an object {function: period}")
            return data
        out = {}
        for part in spec.split(","):
            name, _, per = part.partition("=")
            if name.strip():
                out[name.strip()] = per.strip()
        if not out:
            raise ValueError("no 'Function=period' pairs")
        return out
    except (OSError, ValueError) as exc:
        print(f"error: --tasks {spec}: {exc}", file=sys.stderr)
        return None


def run_swdoc(argv: list) -> int:
    usage = ('Usage: graphify swdoc "<path substring>" [--exclude P] [--module NAME] [--graph PATH] [--out DIR]\n'
             '         [--dataset SET] [--db PATH] [--mermaid] [--prune] [--single-file] [--html] [--tasks FILE|"Name=10 ms,Name2=20 ms"]\n'
             '  e.g. graphify swdoc ku/kku --dataset SetA --mermaid\n'
             '       graphify swdoc . --module Software --single-file --dataset SetA   (whole software in ONE markdown file)\n'
             '       graphify swdoc . --module Software --html --dataset SetA          (whole software as ONE html file)\n'
             '  Writes an Obsidian vault (open the folder as a vault): functions/, variables/, calibrations/, axes/,\n'
             '  border/, modules/, _HOME.md. Text under "My notes" is kept on re-export. --prune removes notes of this\n'
             '  tool that are no longer generated.')
    if not argv or argv[0] in ("-h", "--help"):
        print(usage, file=sys.stderr)
        return 1
    opts = {"include": [], "exclude": [], "graph": None, "out": None, "dataset": None, "db": None, "module": None,
            "mermaid": False, "prune": False, "single": False, "html": False}
    i, args = 0, list(argv)
    while i < len(args):
        a, nxt = args[i], args[i + 1] if i + 1 < len(args) else None
        if a == "--exclude" and nxt:
            opts["exclude"].append(nxt); i += 1
        elif a in ("--graph", "--out", "--dataset", "--db", "--module", "--tasks") and nxt:
            opts[a[2:]] = nxt; i += 1
        elif a == "--mermaid":
            opts["mermaid"] = True
        elif a == "--prune":
            opts["prune"] = True
        elif a == "--single-file":
            opts["single"] = True
        elif a == "--html":
            opts["html"] = True
        elif not a.startswith("--"):
            opts["include"].append(a)
        i += 1
    if not opts["include"]:
        print("error: give a path substring\n" + usage, file=sys.stderr)
        return 1
    from graphify.paths import out_path
    gp = Path(opts["graph"]) if opts["graph"] else Path(out_path()) / "graph.json"
    if not gp.is_file():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    raw = json.loads(gp.read_text(encoding="utf-8"))
    lookup, dataset, conn = None, "", None
    if opts["dataset"] or opts["db"]:
        from graphify import params
        dbp = Path(opts["db"]) if opts["db"] else params.default_db_path(gp)
        try:
            conn = params.open_db(dbp, create=False)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        dataset, err = params.resolve_dataset(conn, opts["dataset"])
        if err:
            print(err, file=sys.stderr)
            return 1
        lookup = lambda name: params.describe(conn, dataset, name, max_cells=64, indent="", with_hint=False)  # noqa: E731
    tasks = _read_tasks(opts.get("tasks"))
    if tasks is None:
        return 1
    res = build_vault(raw, opts["include"], opts["exclude"], lookup, dataset or "", opts["mermaid"], opts["module"],
                      want_notes=not opts["html"], tasks=tasks)
    if res["empty"]:
        print(f"No function with a source file containing {opts['include']}.")
        return 0
    out = Path(opts["out"]) if opts["out"] else gp.parent / "swdoc"
    out.mkdir(parents=True, exist_ok=True)
    if opts["html"]:
        from graphify.swdoc_html import write_html
        name = res["module"]
        target = out / f"{name}.html"
        size = write_html(res, target, opts["module"] or name, dataset or "")
        n = res["counts"]
        print(f"swdoc {opts['include']}: ONE html file {target.name} ({size / 1e6:.1f} MB; functions {n['functions']}, "
              f"variables {n['variables']}, calibrations {n['calibrations']}, axes {n['axes']}) -> {out}")
        if size > 40_000_000:
            print("  warning: more than 40 MB - the file opens slowly; export one component at a time instead")
        if conn is not None:
            conn.close()
        return 0
    v = _Vault(out)
    if opts["single"]:
        name = res["module"]
        text = single_file(res, title=opts["module"] or name, dataset=dataset or "")
        v.write(f"{name}.md", text, name)
        pruned = v.finish(name, False)
        n = res["counts"]
        size = (out / f"{name}.md").stat().st_size if (out / f"{name}.md").is_file() else 0
        print(f"swdoc {opts['include']}: ONE file {name}.md ({size / 1e6:.1f} MB; functions {n['functions']}, variables {n['variables']}, "
              f"calibrations {n['calibrations']}, axes {n['axes']}) -> {out}")
        if size > 5_000_000:
            print("  warning: more than 5 MB - Obsidian becomes slow on one file this large; per-module export (without --single-file) is lighter")
        if v.skipped:
            print(f"  skipped {len(v.skipped)} existing file(s) that graphify did not create: " + ", ".join(v.skipped[:5]))
        if conn is not None:
            conn.close()
        return 0
    for rel, text in res["notes"].items():
        v.write(rel, text, res["module"])
    if res.get("can"):
        for rel, text in can_notes(res["can"], res.get("stems"), res.get("cstems")).items():
            v.write(rel, text, "CAN")
        v.modules["CAN"] = "CAN"
    pruned = v.finish(res["module"], opts["prune"])
    if conn is not None:
        conn.close()
    n = res["counts"]
    print(f"swdoc {opts['include']}: {len(res['notes'])} notes (functions {n['functions']}, variables {n['variables']}, "
          f"calibrations {n['calibrations']}, axes {n['axes']}, border {n['border']}) -> {out}")
    if dataset:
        print(f"  values from dataset '{dataset}'")
    if v.skipped:
        print(f"  skipped {len(v.skipped)} existing file(s) that graphify did not create: " + ", ".join(v.skipped[:5]))
    if pruned:
        print(f"  pruned {pruned} note(s) that are no longer generated")
    print(f"  open {out} as a vault in Obsidian; start at _HOME")
    return 0
