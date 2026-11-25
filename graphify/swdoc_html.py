"""`graphify swdoc ... --html`: the whole software as ONE self-contained HTML file.

The data (functions, variables, calibrations, components, A2L facts, dataset values) is embedded once as JSON;
the browser builds a page only when it is opened, so the size of the software hardly matters. No external
library: the file works offline and can be mailed or put on a share. Search, sidebar, links between all items
and a clickable call flow are plain JavaScript.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from graphify.a2l_links import link_names, link_range
from graphify.focus import READ, WRITE, CALL
from graphify.inputs import _loc
from graphify import swdoc as S


def _facts(c, i) -> dict:
    """Description, range, unit, enum and value of a variable / calibration / axis node (plain data)."""
    M = c.M
    m = M.meta(i)
    links = [l for l in M.links.get(i, []) if l["kind"] in ("char", "axis")]
    ref = m.get("calibration_ref") if isinstance(m.get("calibration_ref"), dict) else {}
    a2l_id = links[0].get("id") if links else None
    am = M.meta(a2l_id) if a2l_id in M.nodes else {}
    if M.t(i) == "a2l_axis":
        am = m
    unit = am.get("unit") or (ref.get("compu_method") or {}).get("unit") or ""
    enum = am.get("verbal_table") or ref.get("verbal_table")
    enum = {str(k): str(v) for k, v in list(enum.items())[:64]} if isinstance(enum, dict) and enum else None
    rng = (link_range(links).replace("..", " … ") if links else "") or S._fmt_range(am.get("min", ref.get("min")), am.get("max", ref.get("max")))
    val = ""
    if len(links) == 1:
        v = S._value_text(c, links[0]["name"], unit)
        val = None if v is None else v
    d = {"d": S._desc_of(c, i, a2l_id), "r": rng, "u": unit}
    if enum:
        d["e"] = enum
    if val is None:
        d["vt"] = 1                                   # a table: shown on the item page
    elif val:
        d["v"] = val
    return d


def _group(M, i) -> str:
    return S._group_of(M, i)


def build_data(res: dict, title: str, dataset: str = "") -> dict:
    c, M = res["ctx"], res["ctx"].M
    funcs, vars_, cals, axes, border = res["funcs"], res["vars"], res["cals"], res["axes"], res["border"]
    plumbing = res["plumbing"]
    order = list(funcs) + list(border) + list(vars_) + list(cals) + list(axes)
    idx = {n: k for k, n in enumerate(order)}
    fset = set(funcs)

    def ids(xs) -> list:
        return [idx[x] for x in xs if x in idx]

    comp_of: dict = {}
    groups: dict = defaultdict(list)
    for f in funcs:
        g = _group(M, f)
        groups[g].append(f)
    comps = []
    for ci, g in enumerate(sorted(groups)):
        fs = groups[g]
        for f in fs:
            comp_of[f] = ci
        touched = set()
        for f in fs:
            touched |= set(M.targets(f, READ | WRITE, ("variable",))) - plumbing
        sig = S.component_signals(M, fs, sorted(touched))
        comps.append({"n": g, "f": ids(fs),
                      "in": [[idx[v], ids(d["inside"]), 0] for v, d in sorted(sig["inputs"].items(), key=lambda kv: M.label(kv[0]).lower()) if v in idx],
                      "out": [[idx[v], ids(d["inside"])] for v, d in sorted(sig["outputs"].items(), key=lambda kv: M.label(kv[0]).lower()) if v in idx]})

    def acc(f, rels, kinds):
        out = []
        for t in M.targets(f, rels, kinds):
            if t in idx and t not in plumbing:
                e = M.edge(f, t, rels) or {}
                out.append([idx[t], _loc(e), S._when(e)])
        return out

    def call_args(f):                                  # [[callee, [[line, {arg: [variable]}]]]]
        out = []
        for e in M.out[f]:
            md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
            if e.get("relation") == "calls" and md.get("args") and e["target"] in idx:
                sites = [[x.get("line", 0), {j: ids(vs) for j, vs in (x.get("args") or {}).items() if ids(vs)}] for x in md["args"]]
                sites = [x for x in sites if x[1]]
                if sites:
                    out.append([idx[e["target"]], sites])
        return out

    sc = M.sched

    def flow_edges(v):                                 # [[variable, kind, [functions], line]] this variable is derived from
        out = []
        for x, e in sorted(M.derived_of(v), key=lambda t: M.label(t[0]).lower()):
            if x in idx and x not in plumbing:
                md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
                out.append([idx[x], 1 if md.get("control_only") else 0, 1 if e.get("confidence") == "INFERRED" else 0,
                            ids(md.get("functions") or []), _loc(e), sc.edge_timing(e) if sc is not None else -1])
        return out

    items = []
    for n in order:
        t = M.t(n)
        it = {"n": S._stem(M.label(n)) if t != "calibration_field" else str(M.meta(n).get("calibration_path") or M.label(n)),
              "file": M.file(n), "loc": M.loc(n)}
        doc = M.doxygen(n)
        if doc:
            it["doc"] = doc
        if n in fset:
            m = M.meta(n)
            it.update({"t": "f", "c": comp_of[n], "ret": m.get("return_type") or "",
                       "rd": acc(n, READ, ("variable",)), "wr": acc(n, WRITE, ("variable",)),
                       "ca": ids(M.targets(n, {S.CAL_READ}, ("calibration_field",))),
                       "cl": ids(M.targets(n, CALL, ("function",))), "cb": ids(M.sources(n, CALL, ("function",)))})
            it["n"] = re.sub(r"\(\)$", "", M.label(n))
            ca_ = call_args(n)
            if ca_:
                it["ar"] = ca_                             # variables handed to the callees
            sq = [[idx[cid], line, S._ctx_text(ctx)] for cid, line, ctx in M.call_sequence(n) if cid in idx]
            if sq:
                it["sq"] = sq                              # calls in execution order: [callee, line, context]
            if isinstance(m.get("parameters"), list):
                pd = m.get("comment_params") if isinstance(m.get("comment_params"), dict) else {}
                it["par"] = [{"n": p.get("name", ""), "t": p.get("type", ""), "r": p.get("role", ""), "d": pd.get(p.get("name"), "")}
                             for p in m["parameters"] if isinstance(p, dict)]
            sm = M.doc_summary(n)
            if sm:
                it["s"] = sm
        elif t == "function":
            it.update({"t": "b", "n": re.sub(r"\(\)$", "", M.label(n))})
            sm = M.doc_summary(n)
            if sm:
                it["s"] = sm
        elif t == "variable":
            m = M.meta(n)
            it.update({"t": "v", "n": M.label(n), "st": m.get("storage") or "", "di": m.get("defined_in") or "",
                       "wr": [[idx[w], _loc(M.edge(w, n, WRITE) or {}), S._when(M.edge(w, n, WRITE))] for w in M.sources(n, WRITE, ("function",)) if w in idx],
                       "rd": [[idx[r], _loc(M.edge(r, n, READ) or {}), S._when(M.edge(r, n, READ))] for r in M.sources(n, READ, ("function",)) if r in idx],
                       "at": 1 if m.get("address_taken") else 0})
            fs_ = [f for f in m.get("feeds_itself", []) if f in idx]
            if fs_:
                it["fs"] = ids(fs_)                        # computed from its own previous value (state / integrator)
            df = flow_edges(n)
            if df:
                it["df"] = df                              # derived from (the reverse "used to compute" is built in the browser)
            it.update(_facts(c, n))
        elif t == "calibration_field":
            links = [l for l in M.links.get(n, []) if l["kind"] == "char"]
            it.update({"t": "c", "ac": M.meta(n).get("accessed_via") or ""})
            it.update(_facts(c, n))
            it["a2l"] = [{"n": l["name"], "ty": l["char_type"], "lo": l["min"], "hi": l["max"], "ix": l["index"] or "",
                          "mk": S._hex(l["mask"]) if l["mask"] else "", "via": l["via"] or "exact"} for l in links[:200]]
            if c.lookup and links:
                vals = []
                for l in sorted(links, key=lambda x: x["name"])[:S.MAX_VALUE_LINKS]:
                    ln = c.lookup(l["name"])
                    if ln:
                        vals.append("\n".join(x.rstrip() for x in ln))
                if vals:
                    it["vals"] = "\n\n".join(vals)
            readers = []
            for r in M.sources(n, {S.CAL_READ}, ("function",)):
                e = M.edge(r, n, {S.CAL_READ}) or {}
                em = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
                if r in idx:
                    readers.append([idx[r], _loc(e), " ".join(em.get("index") or []),
                                    ", ".join(S._hex(x) for x in (em.get("mask") or [])), e.get("confidence", "")])
            it["rd"] = readers
            mi = []
            for role in ("x", "y", "z"):
                ax = ids(M.targets(n, {role + "_axis"}, ("calibration_field",)))
                inp = ids(M.targets(n, {"input_" + role}, ("variable", "calibration_field")))
                if ax or inp:
                    mi.append([role.upper(), ax, inp])
            if mi:
                it["mi"] = mi
            if M.meta(n).get("from_struct_type"):
                it["parent"] = M.meta(n)["from_struct_type"]
        elif t == "a2l_axis":
            m = M.meta(n)
            it.update({"t": "x", "n": M.label(n), "sym": m.get("internal_symbol", ""), "iq": m.get("input_quantity", ""),
                       "mx": m.get("max_axis_points")})
            it.update(_facts(c, n))
            if c.lookup:
                ln = c.lookup(M.label(n))
                if ln:
                    it["vals"] = "\n".join(x.rstrip() for x in ln)
        else:
            it.setdefault("t", "b")
        items.append(it)
    # parent paths of struct members -> item ids
    by_path = {it["n"]: k for k, it in enumerate(items) if it.get("t") == "c"}
    for it in items:
        if it.get("parent"):
            it["parent"] = by_path.get(it["parent"])
    sp = [[idx[f], len(M.targets(f, CALL, ("function",))), M.reach(f)] for f in S.start_points(M, funcs)]
    sp.sort(key=lambda r: (-r[2], items[r[0]]["n"].lower()))
    tk, oi, ti = [], [], []
    if sc is not None and sc.tasks:
        tidx = {}
        for t in sc.tasks:
            inside = [f for f in t.order if f in fset]
            if inside:
                lanes = [[idx[f], t.depth.get(f, 0), t.pos.get(f, 0), t.end.get(f, 0)] for f in inside[:300]]
                evs = []
                for v in sorted(set(t.writes) | set(t.reads), key=lambda x: M.label(x).lower()):
                    if v in idx:
                        w = [[a, idx[f]] for a, f in t.writes.get(v, []) if f in idx][:20]
                        r = [[a, idx[f]] for a, f in t.reads.get(v, []) if f in idx][:20]
                        if w or r:
                            evs.append([idx[v], w, r])
                tidx[t] = len(tk)
                tk.append([t.name, t.period_text, idx.get(t.entry, -1), ids(inside)[:300], len(t.order), 1 if t.truncated else 0,
                           lanes, evs[:600], max(t.end.values(), default=0)])
        for f in funcs:
            rts = [tidx[t] for t in sc.runs_in(f) if t in tidx]
            if rts:
                items[idx[f]]["rt"] = rts[:8]
        for v, t, r, _rt, w, _w in sc.order_issues(fset, 200):
            if v in idx and t in tidx:
                oi.append([idx[v], tidx[t], idx[r], idx[w]])
        for v, wt, rt in sc.task_interfaces(fset, 200):
            if v in idx:
                ti.append([idx[v], [tidx[t] for t in wt if t in tidx], [tidx[t] for t in rt if t in tidx]])
    can = res.get("can")
    if can:                                       # compact keys: n name, i id, h hex, l dlc, y cycle, d dir, s senders, t text, g signals
        can = {"dbc": can["dbc"], "ecu": can["ecu"], "m": [
            {"n": m["name"], "h": m["hex"], "l": m["dlc"], "y": m["cycle"], "d": m["dir"], "s": m["senders"], "t": m["desc"],
             "a": m["attrs"], "g": [{"n": g["name"], "t": g["desc"], "b": g["start"], "l": g["len"], "o": g["order"],
                                     "sg": g["signed"], "f": g["factor"], "of": g["offset"], "lo": g["min"], "hi": g["max"],
                                     "u": g["unit"], "e": g["enum"], "r": g["rx"], "x": g["mux"],
                                     "v": [[idx.get(lk["var"], -1), lk["label"], lk["index"], lk["getter"]] for lk in g.get("links", [])],
                                     "tx": [[idx.get(t["fn"], -1), t["label"], t["macro"], t["line"], [[idx.get(v, -1), lab] for v, lab in t["sources"]]]
                                            for t in g.get("tx", [])]} for g in m["signals"]]}
            for m in can["messages"]]}
        for k, m in enumerate(can["m"]):                    # variable page: which CAN signal fills it
            for gi, g in enumerate(m["g"]):
                for vi, *_rest in g["v"]:
                    if vi >= 0:
                        items[vi].setdefault("cn", []).append([k, gi])
    return {"title": title, "dataset": dataset, "it": items, "comps": comps, "sp": sp, "can": can,
            "tk": tk, "oi": oi, "ti": ti, "ap": 1 if (sc is not None and sc.approximate) else 0}


def render(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__TITLE__", re.sub(r"[<>&]", "", data["title"])).replace("__DATA__", blob)


def write_html(res: dict, path, title: str, dataset: str = "") -> int:
    from pathlib import Path
    html = render(build_data(res, title, dataset))
    Path(path).write_text(html, encoding="utf-8")
    return len(html.encode("utf-8"))


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fff;--fg:#1d2330;--mut:#64708a;--line:#dfe3ec;--side:#f5f6fa;--acc:#2456c8;--card:#fff;--hl:#eef3ff;--code:#f3f4f8}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e4e7ef;--mut:#9aa4bd;--line:#2a3040;--side:#191d27;--acc:#7ea2ff;--card:#191d27;--hl:#1e2a4a;--code:#1c212d}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg);display:flex;height:100vh}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
#side{width:320px;min-width:260px;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column}
#side h1{font-size:15px;margin:0;padding:12px 14px 6px}#side h1 a{color:var(--fg)}
#q{margin:6px 14px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
#comp{margin:0 14px 6px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;max-width:292px}
#chips{padding:0 14px 6px;display:flex;gap:4px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:var(--bg);color:var(--mut);border-radius:11px;padding:1px 9px;font-size:12px;cursor:pointer}
.chip.on{background:var(--acc);color:#fff;border-color:var(--acc)}
#list{overflow:auto;flex:1;padding:2px 6px 14px}#list a{display:block;padding:3px 8px;border-radius:5px;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#list a:hover{background:var(--hl);text-decoration:none}#list small{color:var(--mut)}
#main{flex:1;overflow:auto;padding:22px 34px 60px}
h2{font-size:22px;margin:0 0 4px}h3{font-size:16px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}h4{font-size:14px;margin:16px 0 6px;color:var(--mut)}
.meta{color:var(--mut);margin-bottom:12px}.tag{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:9px;padding:0 7px;margin-right:6px;color:var(--mut)}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:13px}th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}th{background:var(--side);font-weight:600}
pre{background:var(--code);padding:10px 12px;border-radius:6px;overflow:auto;font:12.5px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap}
code{background:var(--code);padding:0 4px;border-radius:3px;font-size:12.5px}.mut{color:var(--mut)}.warn{border-left:3px solid #d99a00;background:var(--code);padding:6px 10px;margin:8px 0}
.flow{overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--card);margin:8px 0 14px}
.flow rect{fill:var(--hl);stroke:var(--acc);stroke-width:1}.flow .out rect{fill:var(--code);stroke:var(--mut)}.flow .me rect{fill:var(--acc)}.flow .me text{fill:#fff}
.flow text{fill:var(--fg);font:12px system-ui,sans-serif}.flow path{fill:none;stroke:var(--mut);stroke-width:1.1}.flow path.back{stroke-dasharray:4 3}
.enum{font-size:12px;color:var(--mut)}
.tr{margin:4px 0 4px 0;padding-left:0;list-style:none}.tr details{margin-left:16px}.tr summary{cursor:pointer;padding:1px 0}.tr .lf{margin-left:34px;padding:1px 0}
.ctx{font-size:11px;color:var(--mut);border:1px dashed var(--mut);border-radius:8px;padding:0 6px;margin-left:6px}.ln{color:var(--mut);font-size:11px;margin-left:6px}
</style></head><body>
<div id="side"><h1><a href="#" id="home"></a></h1><input id="q" placeholder="Search name or description ..." autocomplete="off">
<select id="comp" title="Show only one component"></select><div id="chips"></div><div id="list"></div></div>
<div id="main"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent),IT=D.it,C=D.comps;
const KIND={f:'Function',v:'Variable',c:'Calibration',x:'Axis',b:'Outside function'};
const $=s=>document.querySelector(s),main=$('#main');
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const A=(id,txt)=>`<a href="#i/${id}">${esc(txt||IT[id].n)}</a>`;
const folder=f=>{const p=(f||'').replace(/\\/g,'/').split('/');p.pop();return p.join('/')||(f||'?').replace(/\.[^.]*$/,'')};
$('#home').textContent=D.title;$('#home').href='#';
// reverse calls for outside functions
const CB=new Map();IT.forEach((x,i)=>{if(x.t==='f')x.cl.forEach(t=>{if(!CB.has(t))CB.set(t,[]);CB.get(t).push(i)})});
function table(head,rows){return `<table><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</table>`}
function valCell(x,id){const o=[];if(x.v!=null&&x.v!=='')o.push(`<b>${esc(x.v)}</b>`);else if(x.vt)o.push(`<span class="mut">table - see ${A(id,'page')}</span>`);
 if(x.e){const k=Object.keys(x.e);o.push(`<span class="enum">${esc(k.slice(0,8).map(q=>q+'='+x.e[q]).join('; ')+(k.length>8?'; +'+(k.length-8)+' more':''))}</span>`)}return o.join('<br>')}
function sigTable(ids,extra){ // Name, Description, Range, Value / enum table, Unit
 if(!ids.length)return '';const head=['Name','Description','Range','Value / enum table','Unit'].concat(extra?extra.head:[]);
 return table(head,ids.slice(0,400).map(id=>{const x=IT[id];let nm=A(id);if(x.t==='c'&&x.a2l&&x.a2l.length)nm+=`<br><code>${esc(x.a2l[0].n)}</code>`+(x.a2l.length>1?' +'+(x.a2l.length-1):'');
  return [nm,esc(x.d||x.s||''),esc(x.r||''),valCell(x,id),esc(x.u||'')].concat(extra?extra.row(id):[])})) + (ids.length>400?`<p class="mut">+${ids.length-400} more</p>`:'')}
// ---------- data flow: variables derived from other variables (built once from the "df" lists) ----------
const FD=new Map();IT.forEach((x,i)=>{(x.df||[]).forEach(d=>{if(!FD.has(d[0]))FD.set(d[0],[]);FD.get(d[0]).push([i,d])})});
const kindTxt=d=>(d[1]?'condition only':'value')+(d[2]?' (inferred)':'');
const TMS=['same cycle','next cycle','previous value','other task','outside input','not scheduled'],TMD=[0,1,1,0,0,0];
const tmTxt=d=>d[5]>=0?TMS[d[5]]:'';
const delayTxt=(c,o)=>(!c&&!o)?'same cycle':[c?c+' cycle'+(c>1?'s':'')+' later':'',o?'task change':''].filter(Boolean).join(' + ');
const HAS_T=D.tk&&D.tk.length>0;
const flowRow=(v,d)=>[A(v),kindTxt(d)].concat(HAS_T?[tmTxt(d)]:[]).concat([list(d[3],4),d[4]?`<code>${esc(d[4])}</code>`:'']);
function traceRows(id,depth,cap){const seen=new Set([id]),out=[],cum=new Map([[id,[0,false]]]);let lvl=[id];
 for(let l=1;l<=depth&&lvl.length&&out.length<cap;l++){const nxt=[];
  for(const s of lvl){for(const [x,d] of (FD.get(s)||[]).slice().sort((a,b)=>IT[a[0]].n.toLowerCase()<IT[b[0]].n.toLowerCase()?-1:1)){if(seen.has(x))continue;seen.add(x);
   const pc=cum.get(s)||[0,false],c=[pc[0]+(d[5]>=0?TMD[d[5]]:0),pc[1]||d[5]===3];cum.set(x,c);
   out.push([l,x,d,s,delayTxt(c[0],c[1])]);nxt.push(x);if(out.length>=cap)return out}}
  lvl=nxt}return out}
function loopRows(id,rows){const par=new Map(rows.map(r=>[r[1],r[3]]));const anc=n=>{const a=new Set([n]);while(par.has(n)&&!a.has(par.get(n))){n=par.get(n);a.add(n)}return a};
 const out=[];[id].concat(rows.map(r=>r[1])).forEach(n=>{const an=anc(n);(FD.get(n)||[]).forEach(([x,d])=>{if(an.has(x)&&out.length<12)out.push([n,x,d])})});return out}
function flowHtml(id){const x=IT[id];let h='';const fd=FD.get(id)||[],T=HAS_T?['Timing']:[];
 if(x.fs&&x.fs.length)h+=`<p class="note"><b>Feeds itself</b>: the value is computed from its own previous value (state, counter or integrator) in ${list(x.fs,4)}.</p>`;
 if(x.df&&x.df.length)h+=`<h3>Derived from</h3><p class="mut">Variables whose value (or whose condition) decides this variable. Local variables and function calls in between are followed.`+(HAS_T?' Timing: does the writer of the source run before (same cycle) or after (next cycle) the function that uses it.':'')+`</p>`+table(['Variable','Kind'].concat(T,['In function','At']),x.df.map(d=>flowRow(d[0],d)));
 if(fd.length){h+=`<h3>Used to compute</h3><p class="mut">Variables computed from this one (directly).</p>`+table(['Variable','Kind'].concat(T,['In function','At']),fd.map(([v,d])=>flowRow(v,d)));
  const tr=traceRows(id,4,80);if(tr.length>fd.length)h+=`<h3>Signal trace (where the value ends up)</h3><p class="mut">Level 1 = computed from this variable, level 2 = computed from level 1, and so on (up to level 4). Delay = cycles added along the shortest path. Plausibility checks, debouncing and filters show up as the functions in the last column.</p>`+table(['Level','Variable','Computed from'].concat(HAS_T?['Timing','Delay so far']:[],['Kind','In function']),tr.map(([l,v,d,s,dl])=>[l,A(v),A(s)].concat(HAS_T?[tmTxt(d),dl]:[],[kindTxt(d),list(d[3],4)])));
  const lp=loopRows(id,tr);if(lp.length)h+=`<h3>Feedback loops</h3><p class="mut">The value comes back to a variable it was computed from. The step that closes the loop is always delayed by at least one call of the writer.</p>`+table(['From','Feeds back into'].concat(T,['In function']),lp.map(([a,v,d])=>[A(a),A(v)].concat(HAS_T?[tmTxt(d)]:[],[list(d[3],4)])))}
 return h}
const whenCell=w=>w?(w==='always'?'always':`<span class="ctx">${esc(w)}</span>`):'';
const list=(ids,max)=>ids.length?ids.slice(0,max||40).map(i=>A(i)).join(', ')+(ids.length>(max||40)?` +${ids.length-(max||40)} more`:''):'<span class="mut">-</span>';

// ---------- layered call flow (SVG) ----------
function flow(nodes,edges){
 const n=nodes.length;if(!n)return '';const ix=new Map(nodes.map((x,i)=>[x.k,i])),adj=nodes.map(()=>[]);
 edges.forEach(([a,b])=>{if(ix.has(a)&&ix.has(b)&&a!==b)adj[ix.get(a)].push(ix.get(b))});
 const st=new Array(n).fill(0),back=new Set();
 const dfs=u=>{st[u]=1;for(const v of adj[u]){if(st[v]===1)back.add(u+','+v);else if(st[v]===0)dfs(v)}st[u]=2};
 for(let u=0;u<n;u++)if(!st[u])dfs(u);
 const rank=new Array(n).fill(0);let ch=true,pass=0;
 while(ch&&pass++<n+2){ch=false;for(let u=0;u<n;u++)for(const v of adj[u])if(!back.has(u+','+v)&&rank[v]<rank[u]+1){rank[v]=rank[u]+1;ch=true}}
 const cols=[];nodes.forEach((x,i)=>{(cols[rank[i]]=cols[rank[i]]||[]).push(i)});
 const pred=nodes.map(()=>[]);adj.forEach((vs,u)=>vs.forEach(v=>{if(!back.has(u+','+v))pred[v].push(u)}));
 const row=new Array(n).fill(0);cols.forEach(c=>c&&c.forEach((i,r)=>row[i]=r));
 for(let sw=0;sw<2;sw++)cols.forEach(c=>{if(!c)return;c.sort((a,b)=>{const m=i=>pred[i].length?pred[i].reduce((s,p)=>s+row[p],0)/pred[i].length:row[i];return m(a)-m(b)});c.forEach((i,r)=>row[i]=r)});
 const W=170,H=26,GX=70,GY=12,X=i=>10+rank[i]*(W+GX),Y=i=>10+row[i]*(H+GY);
 const w=10+cols.length*(W+GX),h=10+Math.max(...cols.map(c=>c?c.length:0))*(H+GY);
 let o=`<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg"><defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L8 4L0 8z" fill="#8892aa" stroke="none"/></marker></defs>`;
 adj.forEach((vs,u)=>vs.forEach(v=>{const x1=X(u)+W,y1=Y(u)+H/2,x2=X(v),y2=Y(v)+H/2,b=back.has(u+','+v),d=Math.max(30,Math.abs(x2-x1)/2);
  o+=b?`<path class="back" d="M${X(u)+W/2} ${Y(u)+H} C ${X(u)+W/2} ${Y(u)+H+40}, ${X(v)+W/2} ${Y(v)-40}, ${X(v)+W/2} ${Y(v)}" marker-end="url(#ar)"/>`
      :`<path d="M${x1} ${y1} C ${x1+d} ${y1}, ${x2-d} ${y2}, ${x2} ${y2}" marker-end="url(#ar)"/>`}));
 nodes.forEach((x,i)=>{const t=x.label.length>26?x.label.slice(0,25)+'...':x.label;
  const g=`<g class="${x.cls||''}"><rect x="${X(i)}" y="${Y(i)}" width="${W}" height="${H}" rx="5"/><text x="${X(i)+8}" y="${Y(i)+17}">${esc(t)}</text><title>${esc(x.label)}</title></g>`;
  o+=x.href?`<a href="${x.href}">${g}</a>`:g});
 return `<div class="flow">${o}</svg></div>`}
const MAXFLOW=140;
// ---------- calls in execution order + lazy execution trace ----------
function seqOf(i){const x=IT[i];return x.sq||(x.cl||[]).map(c=>[c,0,''])}
function seqLine(s){return `${A(s[0])}${s[1]?`<span class="ln">L${s[1]}</span>`:''}${s[2]?`<span class="ctx">${esc(s[2])}</span>`:''}`}
function traceChildren(id,path){
 return seqOf(id).map(s=>{const c=s[0],x=IT[c],rec=path.indexOf(c)>=0,has=x.t==='f'&&seqOf(c).length>0;
  if(rec)return `<div class="lf">&#8635; ${A(c)} <span class="ctx">recursion</span></div>`;
  if(!has)return `<div class="lf">${seqLine(s)}</div>`;
  return `<details data-id="${c}" data-path="${path.concat([c]).join(',')}"><summary>${seqLine(s)}</summary></details>`}).join('')}
function traceBlock(id){return `<div class="tr" id="trace"><details data-id="${id}" data-path="${id}" open><summary><b>${esc(IT[id].n)}</b></summary>${traceChildren(id,[id])}</details></div>`}
main.addEventListener('toggle',e=>{const d=e.target;if(d.tagName!=='DETAILS'||!d.dataset.id||d.dataset.done)return;
 if(d.open){d.dataset.done=1;if(d.children.length<=1)d.insertAdjacentHTML('beforeend',traceChildren(+d.dataset.id,d.dataset.path.split(',').map(Number)))}},true);

// ---------- pages ----------
function pageHome(){
 const nf=IT.filter(x=>x.t==='f').length,nv=IT.filter(x=>x.t==='v').length,nc=IT.filter(x=>x.t==='c').length;
 main.innerHTML=`<h2>${esc(D.title)}</h2><div class="meta">${nf} functions &middot; ${nv} variables &middot; ${nc} calibrations &middot; ${C.length} components`+(D.dataset?` &middot; values from dataset <code>${esc(D.dataset)}</code>`:'')+`</div>
 <h3>Components</h3>`+table(['Component','Functions','Inputs','Outputs'],C.map((c,k)=>[`<a href="#k/${k}">${esc(c.n)}</a>`,c.f.length,c.in.length,c.out.length]))
 +(D.can?`<h3>CAN</h3><p><a href="#can">${D.can.m.length} messages</a> from <code>${esc(D.can.dbc)}</code>${D.can.ecu?' for the ECU <code>'+esc(D.can.ecu)+'</code>':''}</p>`:'')
 +(HAS_T?`<h3>Tasks</h3><p><a href="#t">${D.tk.length} tasks</a> with execution order, data order and data between tasks</p>`:'')
 +`<h3>Start points</h3><p class="mut">Functions that no function in the code calls: tasks, init, interrupts, diagnostics - or unused code. Sorted by how much they reach.</p>`
 +table(['Function','Component','Calls','Reaches'],D.sp.slice(0,300).map(([f,n,r])=>[A(f),esc(C[IT[f].c].n),n,r+' functions']))+(D.sp.length>300?`<p class="mut">+${D.sp.length-300} more</p>`:'')}
const NUM=v=>v==null?'':(typeof v==='number'?+v.toPrecision(10):v);
function canSigTable(m){const E=D.can.ecu;return table(['Name','Description','Start bit / Length / Order','Range','Factor / Offset','Unit','Value / enum table','Receivers','C variable'],
 m.g.map((g,gi)=>{const ks=Object.keys(g.e||{});return [`<a href="#s/${D.can.m.indexOf(m)}/${gi}"><b>${esc(g.n)}</b></a>`+(g.x?` <span class="mut">(mux ${esc(g.x)})</span>`:''),esc(g.t||''),`${g.b} / ${g.l} / ${esc(g.o)}${g.sg?' signed':''}`,
  g.lo!=null&&g.hi!=null?esc(NUM(g.lo)+' ... '+NUM(g.hi)):'',esc(NUM(g.f)+' / '+NUM(g.of)),esc(g.u),
  ks.length?`<span class="enum">${esc(ks.slice(0,8).map(q=>q+'='+g.e[q]).join('; ')+(ks.length>8?'; +'+(ks.length-8)+' more':''))}</span>`:'',
  g.r.map(r=>r===E?`<b>${esc(r)}</b>`:esc(r)).join(', '),(g.v||[]).map(([i,l])=>i>=0?A(i):`<code>${esc(l)}</code>`).join(', ')]}))}
function pageTasks(){let h=`<h2>Tasks and execution order</h2><div class="meta">A task is a function that no other function calls (the hardware calls it every N samples). Everything it calls runs in that task, in code order. A function that runs several times per cycle counts at its first call.`+(D.ap?' The call order of some functions was not recorded in this graph: re-extract for exact order.':'')+`</div>`;
 h+=table(['Task','Period','Functions in the task','Of them shown here'],D.tk.map((t,k)=>[`<a href="#t/${k}">${esc(t[0])}</a>`,esc(t[1]||'-'),t[4]+(t[5]?'+':''),t[3].length]));
 if(D.oi.length)h+=`<h3>Data order: the reader runs before the writer</h3><p class="mut">The reader sees the value of the <b>previous</b> cycle. Fine for state and feedback; check it for signals that should be fresh.</p>`+table(['Variable','Task','Reader (runs first)','Writer (runs later)'],D.oi.map(([v,t,r,w])=>[A(v),esc(D.tk[t][0]),A(r),A(w)]));
 if(D.ti.length)h+=`<h3>Data between tasks</h3><p class="mut">Written in one task, read in another: the reader gets the latest value whenever it runs.</p>`+table(['Variable','Written in','Read in'],D.ti.map(([v,w,r])=>[A(v),esc(w.map(t=>D.tk[t][0]).join(', ')),esc(r.map(t=>D.tk[t][0]).join(', '))]));
 main.innerHTML=h}
function pageTask(k){const t=D.tk[k];if(!t){pageTasks();return}main.innerHTML=`<h2>Task ${esc(t[0])}${t[1]?' ('+esc(t[1])+')':''}</h2><div class="meta">entry ${t[2]>=0?A(t[2]):esc(t[0])} &middot; ${t[4]} functions run in this task`+(t[5]?' (list cut off)':'')+`</div><h3>Timeline</h3><p class="mut">Time runs to the right (code order, one step per variable access). One lane per function; the bar spans the function including its callees. Pick a variable to see where it is written (&#9650;) and first read (&#9679;) per function. An arrow that points back to the left means the reader runs before the writer: it sees the value of the previous cycle.</p><div id="tlc"></div><h3>Execution order</h3><ol>`+t[3].map(i=>`<li>${A(i)}</li>`).join('')+`</ol>`;timeline(t)}
function timeline(t){const c=document.getElementById('tlc');if(!c||!t[6]||!t[6].length)return;const L=t[6],EV=t[7]||[],T=Math.max(t[8],1),mx=Math.max(...L.map(l=>l[1]));
 c.innerHTML=`<div class="meta">Depth <select id="tld">${Array.from({length:mx+1},(_,i)=>`<option value="${i}"${i===Math.min(mx,2)?' selected':''}>${i===mx?'all':'up to '+i}</option>`).join('')}</select> &nbsp; Variable <select id="tlv"><option value="-1">(none)</option>${EV.map((e,i)=>`<option value="${i}">${esc(IT[e[0]].n)}</option>`).join('')}</select></div><div style="overflow:auto;max-height:70vh;border:1px solid var(--line);border-radius:6px"><svg id="tls"></svg></div>`;
 const draw=()=>{const dmax=+document.getElementById('tld').value,ev=EV[+document.getElementById('tlv').value];const rows=L.filter(l=>l[1]<=dmax);const row=new Map();rows.forEach((l,i)=>row.set(l[0],i));
  const LW=230,W=900,RH=20,H=rows.length*RH+24,xs=x=>LW+8+x/T*(W-LW-20);let s=`<svg id="tls" width="${W}" height="${H}" style="font:12px sans-serif" xmlns="http://www.w3.org/2000/svg">`;
  rows.forEach((l,i)=>{const y=i*RH+12,x1=xs(l[2]),x2=Math.max(xs(l[3]),x1+4);s+=`<text x="${6+l[1]*8}" y="${y+RH/2}" fill="currentColor" dominant-baseline="middle">${esc(IT[l[0]].n).slice(0,28-l[1]*2)}</text><line x1="${LW}" x2="${W-6}" y1="${y+RH-1}" y2="${y+RH-1}" stroke="currentColor" opacity=".08"/><a href="#i/${l[0]}"><rect x="${x1}" y="${y+4}" width="${x2-x1}" height="${RH-8}" rx="3" fill="#3b82f6" opacity="${.35+.1*Math.min(l[1],4)}"><title>${esc(IT[l[0]].n)}</title></rect></a>`});
  if(ev){const pt=(a,f)=>row.has(f)?[xs(a),row.get(f)*RH+12+RH/2]:null;const w=ev[1].map(([a,f])=>pt(a,f)),r=ev[2].map(([a,f])=>[pt(a,f),a]);
   const w0=ev[1].length?Math.min(...ev[1].map(x=>x[0])):null;
   r.forEach(([p,a])=>{if(!p)return;const wp=w.find(Boolean);if(wp&&w0!==null){const back=a<=w0;s+=`<path d="M${wp[0]},${wp[1]} L${p[0]},${p[1]}" stroke="${back?'#ef4444':'#22c55e'}" stroke-width="1.5" fill="none" stroke-dasharray="${back?'4 3':''}"><title>${back?'reader runs before the writer (previous cycle)':'same cycle'}</title></path>`}s+=`<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="#06b6d4"/>`});
   w.forEach(p=>{if(p)s+=`<polygon points="${p[0]},${p[1]-6} ${p[0]-5},${p[1]+4} ${p[0]+5},${p[1]+4}" fill="#f59e0b"/>`})}
  document.getElementById('tls').outerHTML=s+'</svg>'};
 document.getElementById('tld').onchange=draw;document.getElementById('tlv').onchange=draw;draw()}
function pageCan(){const K=D.can;let h=`<h2>CAN</h2><div class="meta"><span class="tag">DBC</span>${K.m.length} messages &middot; ${K.m.reduce((a,m)=>a+m.g.length,0)} signals from <code>${esc(K.dbc)}</code>`+(K.ecu?` &middot; messages the ECU <code>${esc(K.ecu)}</code> sends (TX) or receives (RX)`:' &middot; whole bus')+`</div>`;
 [['Sent by the ECU (TX)','TX'],['Received by the ECU (RX)','RX'],['Other messages','']].forEach(([t,d])=>{
  const ms=K.m.map((m,k)=>[m,k]).filter(([m])=>(m.d==='TX'||m.d==='RX'?m.d:'')===d);if(!ms.length)return;
  h+=`<h3>${t}</h3>`+table(['Message','Frame ID','DLC','Cycle [ms]','Sender','Signals','Description'],ms.map(([m,k])=>[`<a href="#m/${k}">${esc(m.n)}</a>`,esc(m.h),m.l,m.y||'-',esc(m.s.join(', ')),m.g.length,esc(m.t||'')]))});
 main.innerHTML=h}
function pageSig(k,gi){const m=D.can&&D.can.m[k],g=m&&m.g[gi];if(!g){main.innerHTML='<p>Unknown signal.</p>';return}
 const ks=Object.keys(g.e||{});
 let h=`<h2>${esc(g.n)}</h2><div class="meta"><span class="tag">CAN signal</span><a href="#can">CAN</a> &middot; message <a href="#m/${k}">${esc(m.n)}</a> (<code>${esc(m.h)}</code>)${m.d==='TX'||m.d==='RX'?' &middot; <b>'+m.d+'</b> for '+esc(D.can.ecu):''}</div>`;
 if(g.t)h+=`<p>${esc(g.t)}</p>`;
 h+=table(['Start bit','Length','Order','Signed','Factor','Offset','Range','Unit'],[[g.b,g.l,esc(g.o),g.sg?'yes':'no',esc(NUM(g.f)),esc(NUM(g.of)),g.lo!=null&&g.hi!=null?esc(NUM(g.lo)+' ... '+NUM(g.hi)):'',esc(g.u)]]);
 if(g.x)h+=`<p class="mut">Multiplexed: ${esc(g.x)}</p>`;
 h+=`<p>Receivers: ${g.r.map(r=>r===D.can.ecu?'<b>'+esc(r)+'</b>':esc(r)).join(', ')||'-'}</p>`;
 if(ks.length)h+=`<h3>Value / enum table</h3>`+table(['Value','Meaning'],ks.slice(0,200).map(q=>[esc(q),esc(g.e[q])]));
 h+=``;
 if((g.tx||[]).length)h+=`<h3>Computed by (the function returns the value of this signal)</h3>`+table(['Function','Macro','Line','Variables the value comes from'],g.tx.map(([f,fl,mc,ln,src])=>[f>=0?A(f):`<code>${esc(fl)}</code>`,`<code>${esc(mc)}</code>`,ln,src.map(([i,l])=>i>=0?A(i):`<code>${esc(l)}</code>`).join(', ')||'-']));
 h+=`<h3>C variable (where the CAN value is stored)</h3>`;
 if(!g.v.length&&!(g.tx||[]).length)h+=`<p class="mut">Not linked to a variable (no row in the signal configuration table, or no getter call found).</p>`;
 g.v.forEach(([i,l,ix,gt])=>{h+=table(['Variable','Signal index','Getter'],[[i>=0?A(i):`<code>${esc(l)}</code> <span class="mut">(not in the exported scope)</span>`,esc(ix),`<code>${esc(gt)}</code>`]]);
  if(i>=0){const x=IT[i];if(x.wr.length)h+=`<h4>Written by</h4><p>${list(x.wr.map(r=>r[0]),40)}</p>`;if(x.rd.length)h+=`<h4>Read by</h4><p>${list(x.rd.map(r=>r[0]),60)}</p>`;
   const tr=traceRows(i,4,80);if(tr.length)h+=`<h3>Derived signals (where the value goes after the variable is filled)</h3><p class="mut">Level 1 = computed from the CAN variable, level 2 = computed from level 1, and so on. Plausibility checks, debouncing and filters show up as the functions in the last column.</p>`+table(['Level','Variable','Computed from'].concat(HAS_T?['Timing','Delay so far']:[],['Kind','In function']),tr.map(([l,v,d,s,dl])=>[l,A(v),A(s)].concat(HAS_T?[tmTxt(d),dl]:[],[kindTxt(d),list(d[3],4)])))}});
 main.innerHTML=h}
function pageMsg(k){const m=D.can.m[k];if(!m){main.innerHTML='<p>Unknown message.</p>';return}
 let h=`<h2>${esc(m.n)}</h2><div class="meta"><span class="tag">CAN message</span><a href="#can">CAN</a> &middot; <code>${esc(m.h)}</code> &middot; DLC ${m.l}`+(m.y?` &middot; ${m.y} ms`:'')+` &middot; sender ${esc(m.s.join(', ')||'-')}`+(m.d==='TX'||m.d==='RX'?` &middot; <b>${m.d}</b> for ${esc(D.can.ecu)}`:'')+`</div>`;
 if(m.t)h+=`<p>${esc(m.t)}</p>`;
 const at=Object.keys(m.a||{});if(at.length)h+=table(['Attribute','Value'],at.map(q=>[esc(q),esc(m.a[q])]));
 h+=`<h3>Signals (${m.g.length})</h3>`+canSigTable(m)+`<p class="mut">Source: the DBC only. Motorola: the start bit is the MSB.</p>`;main.innerHTML=h}
function outsideGroup(i){const g=folder(IT[i].file)||'?';const k=C.findIndex(c=>c.n===g);return {g,k}}
function pageComp(k){
 const c=C[k],fs=new Set(c.f);let h=`<h2>${esc(c.n)}</h2><div class="meta"><span class="tag">Component</span>${c.f.length} functions &middot; ${c.in.length} inputs &middot; ${c.out.length} outputs</div>`;
 h+=`<h3>Call flow</h3>`;
 const nodes=[],edges=[],seen=new Set(),add=(key,label,href,cls)=>{if(!seen.has(key)){seen.add(key);nodes.push({k:key,label,href,cls})}};
 c.f.forEach(i=>add('f'+i,IT[i].n,'#i/'+i));
 const og=(i)=>{const {g,k:ck}=outsideGroup(i);const key='g'+g;add(key,g,ck>=0&&ck!==k?'#k/'+ck:null,'out');return key};
 c.f.forEach(i=>{IT[i].cl.forEach(t=>{if(fs.has(t))edges.push(['f'+i,'f'+t]);else edges.push(['f'+i,og(t)])});(IT[i].cb||[]).forEach(s=>{if(!fs.has(s))edges.push([og(s),'f'+i])})});
 const ded=[...new Set(edges.map(e=>e.join('>')))].map(s=>s.split('>'));
 h+=nodes.length<=MAXFLOW?flow(nodes,ded)+`<p class="mut">Box = function (click to open) &middot; rounded grey = other component &middot; arrow = calls &middot; dashed = recursion / cycle.</p>`:`<div class="warn">${nodes.length} blocks: too many to draw, see the function table.</div>`;
 const sg=a=>a.map(([v,o,e])=>[A(v),...[]]);
 if(c.in.length)h+=`<h3>Component inputs</h3>`+sigTable(c.in.map(r=>r[0]),{head:['Read inside by'],row:id=>[list(c.in.find(q=>q[0]===id)[1],4)]});
 if(c.out.length)h+=`<h3>Component outputs</h3>`+sigTable(c.out.map(r=>r[0]),{head:['Written inside by'],row:id=>[list(c.out.find(q=>q[0]===id)[1],4)]});
 const cal=[...new Set(c.f.flatMap(i=>IT[i].ca))];
 if(cal.length)h+=`<h3>Calibrations</h3>`+sigTable(cal,{head:['Read by'],row:id=>[list(c.f.filter(f=>IT[f].ca.includes(id)),4)]});
 h+=`<h3>Functions</h3>`+table(['Function','Summary','Calls','Reads','Writes'],c.f.map(i=>{const x=IT[i];return [A(i),esc(x.s||''),x.cl.length,x.rd.length+x.ca.length,x.wr.length]}));
 main.innerHTML=h}
function docBlock(x){return x.doc?`<h3>Description</h3><pre>${esc(x.doc)}</pre>`:`<p class="mut">No doxygen comment in the code.</p>`}
function head(x,id,extra){return `<h2>${esc(x.n)}</h2><div class="meta"><span class="tag">${KIND[x.t]}</span>${x.file?`<code>${esc(x.file)}${x.loc?':'+esc(x.loc):''}</code>`:''}${extra||''}</div>`}
function mapInputs(cals){const rows=[];cals.forEach(k=>(IT[k].mi||[]).forEach(([role,ax,inp])=>{
  if(!inp.length)rows.push([A(k),role,list(ax),'<span class="mut">not found</span>','','','']);
  inp.forEach(v=>{const x=IT[v];rows.push([A(k),role,list(ax),A(v),esc(x.d||x.s||''),esc(x.r||''),esc(x.u||'')])})}));
  return rows.length?`<h4>Inputs of the maps and curves</h4>`+table(['Map / curve','Axis','Axis calibration','Input','Description','Range','Unit'],rows):''}
function pageItem(id){
 const x=IT[id];if(!x){main.innerHTML='<p>Unknown item.</p>';return}let h='';
 if(x.t==='f'){const ret=x.ret?` &middot; returns <code>${esc(x.ret)}</code>`:'';const c=C[x.c];
  h=head(x,id,ret+` &middot; component <a href="#k/${x.c}">${esc(c.n)}</a>`)+docBlock(x);
  if(x.rt&&x.rt.length)h+=`<p><b>Runs in task:</b> ${x.rt.map(t=>`<a href="#t">${esc(D.tk[t][0])}${D.tk[t][1]?' ('+esc(D.tk[t][1])+')':''}</a>`).join(', ')}</p>`;
  if(x.par&&x.par.length)h+=table(['Parameter','Type','Role','Meaning (from comment)'],x.par.map(p=>[`<code>${esc(p.n)}</code>`,`<code>${esc(p.t)}</code>`,esc(p.r),esc(p.d)]));
  const nodes=[{k:'me',label:x.n,cls:'me'}],edges=[],seen=new Set(['me']);
  const nd=(i)=>{const k='n'+i;if(!seen.has(k)){seen.add(k);nodes.push({k,label:IT[i].n,href:'#i/'+i,cls:IT[i].t==='b'?'out':''})}return k};
  (x.cb||[]).slice(0,25).forEach(i=>edges.push([nd(i),'me']));x.cl.slice(0,25).forEach(i=>edges.push(['me',nd(i)]));
  if(nodes.length>1)h+=`<h3>Call flow</h3>`+flow(nodes,edges);
  h+=`<h3>Inputs</h3>`;
  const wh=x.rd.concat(x.wr).some(r=>r[2]);
  if(x.rd.length)h+=`<h4>Variables read</h4>`+sigTable(x.rd.map(r=>r[0]),{head:wh?['At','When']:['At'],row:id=>{const r=x.rd.find(q=>q[0]===id)||[];return wh?[`<code>${esc(r[1]||'')}</code>`,whenCell(r[2])]:[`<code>${esc(r[1]||'')}</code>`]}});
  if(x.ca.length)h+=`<h4>Calibrations read</h4>`+sigTable(x.ca)+mapInputs(x.ca);
  if(!x.rd.length&&!x.ca.length)h+=`<p class="mut">reads no variable or calibration</p>`;
  h+=`<h4>Callers</h4><p>${list(x.cb||[],60)}</p><h3>Outputs</h3>`;
  h+=x.wr.length?`<h4>Variables written</h4>`+sigTable(x.wr.map(r=>r[0]),{head:wh?['At','When']:['At'],row:id=>{const r=x.wr.find(q=>q[0]===id)||[];return wh?[`<code>${esc(r[1]||'')}</code>`,whenCell(r[2])]:[`<code>${esc(r[1]||'')}</code>`]}}):'<p class="mut">writes no variable</p>';
  if(x.cl.length){const sq=seqOf(id);h+=`<h4>Calls in execution order</h4><ol>${sq.slice(0,150).map(s=>`<li>${seqLine(s)}</li>`).join('')}</ol>`+(sq.length>150?`<p class="mut">+${sq.length-150} more calls</p>`:'')+(x.sq?'':'<p class="mut">The order was not recorded in this graph (re-run the clang extraction); the list is alphabetical.</p>')
   +(x.ar?`<h4>Variables passed to the callees</h4>`+table(['Callee','At','Arguments'],x.ar.flatMap(([cl,sites])=>sites.map(([ln,a])=>[A(cl),ln?`<code>L${ln}</code>`:'',Object.keys(a).sort((p,q)=>p-q).map(j=>`arg ${+j+1}: ${list(a[j],6)}`).join('; ')]))):'')
   +`<h3>Execution trace</h3><p class="mut">All calls below this function in code order. Open a call to see what it calls. Dashed tags: conditional or repeated calls.</p>`+traceBlock(id)}}
 else if(x.t==='b'){const cb=CB.get(id)||[];h=head(x,id,' &middot; not part of the exported scope')+docBlock(x)+`<h3>Called by</h3><p>${list(cb,80)}</p>`}
 else if(x.t==='v'){
  h=head(x,id,(x.st?` &middot; ${esc(x.st)}`:'')+(x.di?` &middot; defined in <code>${esc(x.di)}</code>`:''))+docBlock(x);
  h+=`<h3>A2L</h3>`+table(['Description','Range','Value / enum table','Unit'],[[esc(x.d||''),esc(x.r||''),valCell(x,id),esc(x.u||'')]]);
  if(x.cn&&D.can)h+=`<h3>CAN signal</h3><p class="mut">Filled by the getter in the signal reader function (signal index and variable from the project's signal configuration).</p>`+table(['Message','Signal','Unit','Value / enum table'],x.cn.map(([k,gi])=>{const m=D.can.m[k],g=m.g[gi],ks=Object.keys(g.e||{});return [`<a href="#m/${k}">${esc(m.n)}</a>`,`<a href="#s/${k}/${gi}"><b>${esc(g.n)}</b></a>`,esc(g.u),ks.length?`<span class="enum">${esc(ks.slice(0,8).map(q=>q+'='+g.e[q]).join('; '))}</span>`:'']}));
  if(x.at)h+=`<div class="warn">Address taken - may also be written through a pointer.</div>`;
  const fr=(a)=>{const w=a.some(r=>r[2]);return table(['Function','At','Summary'].concat(w?['When']:[]),a.map(([f,l,wn])=>[A(f),`<code>${esc(l)}</code>`,esc(IT[f].s||'')].concat(w?[whenCell(wn)]:[])))};
  h+=`<h3>Written by</h3>`+(x.wr.length?fr(x.wr):'<p class="mut">no writer found (pointer write, inactive #ifdef, outside the compile DB, or external input)</p>')+`<h3>Read by</h3>`+(x.rd.length?fr(x.rd):'<p class="mut">no reader</p>')+flowHtml(id)}
 else if(x.t==='c'){
  h=head(x,id,x.ac?` &middot; via <code>${esc(x.ac)}</code>`:'')+docBlock(x);
  if(x.parent!=null)h+=`<div class="warn">Member of the struct ${A(x.parent)} (read as a whole in the code; this member is inferred from the header type).</div>`;
  h+=`<h3>A2L entries</h3>`+(x.a2l.length?table(['Characteristic','Index','Mask','Type','Min','Max','Link'],x.a2l.map(a=>[`<code>${esc(a.n)}</code>`,esc(a.ix||'-'),esc(a.mk||'-'),esc(a.ty),a.lo==null?'':a.lo,a.hi==null?'':a.hi,esc(a.via)])):'<p class="mut">No A2L entry is linked to this calibration.</p>');
  h+=table(['Description','Range','Value / enum table','Unit'],[[esc(x.d||''),esc(x.r||''),valCell(x,id),esc(x.u||'')]]);
  if(x.vals)h+=`<h3>Values${D.dataset?' ('+esc(D.dataset)+')':''}</h3><pre>${esc(x.vals)}</pre>`;
  if(x.mi)h+=mapInputs([id]);
  h+=`<h3>Read by</h3>`+(x.rd.length?table(['Function','At','Index','Mask','Confidence'],x.rd.map(r=>[A(r[0]),`<code>${esc(r[1])}</code>`,esc(r[2]||'-'),esc(r[3]||'-'),esc(r[4])])):'<p class="mut">no reader</p>')}
 else if(x.t==='x'){h=head(x,id,x.sym?` &middot; symbol <code>${esc(x.sym)}</code>`:'')+`<p>A2L axis points &middot; range ${esc(x.r||'-')} ${esc(x.u||'')} &middot; max points ${esc(x.mx==null?'':x.mx)}${x.iq?` &middot; input quantity <code>${esc(x.iq)}</code>`:''}</p>`+(x.d?`<p>${esc(x.d)}</p>`:'')+(x.vals?`<h3>Values</h3><pre>${esc(x.vals)}</pre>`:'')}
 main.innerHTML=h;main.scrollTop=0}
function route(){if(location.hash==='#can'&&D.can){pageCan();main.scrollTop=0;return}
 if(HAS_T){if(location.hash==='#t'){pageTasks();main.scrollTop=0;return}const tm=location.hash.match(/^#t\/(\d+)/);if(tm){pageTask(+tm[1]);main.scrollTop=0;return}}
 const sg=location.hash.match(/^#s\/(\d+)\/(\d+)/);if(sg&&D.can){pageSig(+sg[1],+sg[2]);main.scrollTop=0;return}
 const m=location.hash.match(/^#([ikm])\/(\d+)/);if(!m)return pageHome();m[1]==='k'?pageComp(+m[2]):m[1]==='m'?pageMsg(+m[2]):pageItem(+m[2]);main.scrollTop=0}

// ---------- sidebar: search + filter ----------
let kind='',comp=-1;const CH=[['','All'],['f','Functions'],['v','Variables'],['c','Calibrations']].concat(D.can?[['s','CAN signals']]:[]);
$('#comp').innerHTML='<option value="-1">All components ('+C.length+')</option>'+C.map((c,k)=>`<option value="${k}">${esc(c.n)} (${c.f.length})</option>`).join('');
$('#comp').onchange=e=>{comp=+e.target.value;side()};
// items that belong to a component: its functions and everything they read, write or use as calibration
const CS=C.map(c=>{const st=new Set();c.f.forEach(f=>{st.add(f);const x=IT[f];x.rd.forEach(r=>st.add(r[0]));x.wr.forEach(r=>st.add(r[0]));x.ca.forEach(i=>st.add(i))});return st});
const inComp=i=>comp<0||CS[comp].has(i);
const sigIn=g=>comp<0||g.v.some(v=>v[0]>=0&&CS[comp].has(v[0]));
$('#chips').innerHTML=CH.map(([k,l])=>`<span class="chip${k===kind?' on':''}" data-k="${k}">${l}</span>`).join('');
$('#chips').onclick=e=>{if(e.target.dataset.k!==undefined){kind=e.target.dataset.k;document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',c.dataset.k===kind));side()}};
function side(){const q=$('#q').value.trim().toLowerCase(),L=$('#list');
 if(!q&&!kind&&comp<0){L.innerHTML=C.map((c,k)=>`<a href="#k/${k}">${esc(c.n)} <small>${c.f.length}</small></a>`).join('')+(D.can?`<a href="#can">CAN messages <small>${D.can.m.length}</small></a>`:'')+(HAS_T?`<a href="#t">Tasks and timeline <small>${D.tk.length}</small></a>`+D.tk.map((t,k)=>`<a href="#t/${k}">&nbsp;&nbsp;${esc(t[0])}${t[1]?' ('+esc(t[1])+')':''}</a>`).join(''):'');return}
 const head=comp>=0?`<a href="#k/${comp}"><b>${esc(C[comp].n)}</b> <small>component page</small></a>`:'';
 const cm=[];
 if(D.can&&(!kind||kind==='s')){
  D.can.m.forEach((m,k)=>{
   if(!kind&&(q||comp>=0)&&cm.length<100&&m.g.some(sigIn)&&(!q||m.n.toLowerCase().includes(q)))cm.push(`<a href="#m/${k}">${esc(m.n)} <small>can message</small></a>`);
   m.g.forEach((g,gi)=>{if(cm.length<400&&sigIn(g)&&(q?(g.n.toLowerCase().includes(q)||(g.t||'').toLowerCase().includes(q)||m.n.toLowerCase().includes(q)):(kind==='s'||comp>=0)))
     cm.push(`<a href="#s/${k}/${gi}">${esc(g.n)} <small>can signal &middot; ${esc(m.n)}</small></a>`)})})}
 const out=[];
 if(kind!=='s')for(let i=0;i<IT.length&&out.length<300;i++){const x=IT[i];if(kind?x.t!==kind:x.t==='x')continue;
  if(!inComp(i)||(x.t==='f'&&comp>=0&&x.c!==comp))continue;
  if(!q||x.n.toLowerCase().includes(q)||(x.d&&x.d.toLowerCase().includes(q))||(x.s&&x.s.toLowerCase().includes(q))||(x.a2l&&x.a2l.some(a=>a.n.toLowerCase().includes(q))))out.push(i)}
 L.innerHTML=head+out.map(i=>`<a href="#i/${i}">${esc(IT[i].n)} <small>${KIND[IT[i].t].toLowerCase()}</small></a>`).join('')+cm.join('')||'<p class="mut" style="padding:8px">No match.</p>'}
$('#q').oninput=side;window.onhashchange=route;side();route();
</script></body></html>
"""
