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
    if reads:
        out += ["#### Variables read", ""] + _table(SIGNAL_HEAD, [_signal_row(c, v) for v in reads[:MAX_ROWS]])
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
        out += ["#### Variables written", ""] + _table(SIGNAL_HEAD, [_signal_row(c, v) for v in writes[:MAX_ROWS]])
    if callees:
        out += ["#### Calls", "", c.many(callees, 20), ""]
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
    for title, ids, rel in (("Written by", writers, WRITE), ("Read by", readers, READ)):
        if ids:
            out += [f"### {title}", ""] + _table(["Function", "At", "Summary"],
                    [[_raw(c.link(w)), _raw(c.at(M.edge(w, v, rel) or {})), M.doc_summary(w)] for w in ids[:MAX_ROWS]])
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


def module_note(c: _Ctx, funcs: list, vars_: list, cals: list, border: list, inside_vars: set) -> str:
    M = c.M
    out = _fm({"type": "module", "tags": ["graphify/module", f"module/{_slug(c.module)}"], "generated": True})
    out += [f"# {c.module}", "", f"[[_HOME]] · {len(funcs)} functions · {len(vars_)} variables · {len(cals)} calibrations", ""]
    byfile = defaultdict(list)
    for f in funcs:
        byfile[M.file(f)].append(f)
    out += ["## Functions", ""]
    for fl in sorted(byfile):
        out += [f"### `{fl}`", ""] + _table(["Function", "Summary", "Calls", "Reads", "Writes"],
            [[_raw(c.link(f)), M.doc_summary(f), len(M.targets(f, CALL, ("function",))),
              len(c.vars_of(f, READ)) + len(M.targets(f, {CAL_READ}, ("calibration_field",))),
              len(c.vars_of(f, WRITE))] for f in byfile[fl]])
    ins = [v for v in vars_ if v not in inside_vars]
    outs = [v for v in vars_ if v in inside_vars and any(r not in funcs for r in M.sources(v, READ, ("function",)))]
    out += ["## Interface", ""]
    if ins:
        out += ["### Inputs from other modules (variables)", "", c.many(sorted(ins, key=lambda x: M.label(x).lower()), 80), ""]
    if outs:
        out += ["### Variables read outside this module", "", c.many(sorted(outs, key=lambda x: M.label(x).lower()), 80), ""]
    if cals:
        out += ["### Calibrations", "", c.many(sorted(cals, key=lambda x: M.label(x).lower()), 120), ""]
    if border:
        out += ["### Functions outside the module that are connected", "", c.many(sorted(border, key=lambda x: M.label(x).lower()), 80), ""]
    return "\n".join(out + ["## My notes", "", USER_BLOCK])


# ---------------------------------------------------------------------------
# scope and vault
# ---------------------------------------------------------------------------

def build_vault(raw: dict, patterns: list, exclude: list | None = None, lookup=None, dataset: str = "",
                mermaid: bool = False, module: str | None = None) -> dict:
    """-> {"notes": {relative path: text}, "module": name, "counts": {...}, "empty": bool}."""
    M = _Model(raw)
    pats, excl = [_norm(p) for p in patterns], [_norm(x) for x in (exclude or [])]

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
    for k in cals:
        p = M.meta(k).get("calibration_path")
        if p:
            c.cal_by_path[p] = k
            if M.meta(k).get("from_struct_type"):
                c.children[M.meta(k)["from_struct_type"]].append(k)
    for lst in c.children.values():
        lst.sort(key=lambda x: M.label(x).lower())
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
    return {"notes": notes, "module": mod_stem, "empty": False,
            "counts": {"functions": len(funcs), "variables": len(vars_), "calibrations": len(cals), "axes": len(axes),
                       "border": len(border)}}


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


def run_swdoc(argv: list) -> int:
    usage = ('Usage: graphify swdoc "<path substring>" [--exclude P] [--module NAME] [--graph PATH] [--out DIR]\n'
             '         [--dataset SET] [--db PATH] [--mermaid] [--prune]\n'
             '  e.g. graphify swdoc ku/kku --dataset SetA --mermaid\n'
             '  Writes an Obsidian vault (open the folder as a vault): functions/, variables/, calibrations/, axes/,\n'
             '  border/, modules/, _HOME.md. Text under "My notes" is kept on re-export. --prune removes notes of this\n'
             '  tool that are no longer generated.')
    if not argv or argv[0] in ("-h", "--help"):
        print(usage, file=sys.stderr)
        return 1
    opts = {"include": [], "exclude": [], "graph": None, "out": None, "dataset": None, "db": None, "module": None,
            "mermaid": False, "prune": False}
    i, args = 0, list(argv)
    while i < len(args):
        a, nxt = args[i], args[i + 1] if i + 1 < len(args) else None
        if a == "--exclude" and nxt:
            opts["exclude"].append(nxt); i += 1
        elif a in ("--graph", "--out", "--dataset", "--db", "--module") and nxt:
            opts[a[2:]] = nxt; i += 1
        elif a == "--mermaid":
            opts["mermaid"] = True
        elif a == "--prune":
            opts["prune"] = True
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
    res = build_vault(raw, opts["include"], opts["exclude"], lookup, dataset or "", opts["mermaid"], opts["module"])
    if res["empty"]:
        print(f"No function with a source file containing {opts['include']}.")
        return 0
    out = Path(opts["out"]) if opts["out"] else gp.parent / "swdoc"
    out.mkdir(parents=True, exist_ok=True)
    v = _Vault(out)
    for rel, text in res["notes"].items():
        v.write(rel, text, res["module"])
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
