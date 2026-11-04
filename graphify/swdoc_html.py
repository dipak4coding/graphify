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
                      "in": [[idx[v], ids(d["outside"]), 1 if d["external"] else 0] for v, d in sorted(sig["inputs"].items(), key=lambda kv: M.label(kv[0]).lower()) if v in idx],
                      "out": [[idx[v], ids(d["outside"])] for v, d in sorted(sig["outputs"].items(), key=lambda kv: M.label(kv[0]).lower()) if v in idx]})

    def acc(f, rels, kinds):
        out = []
        for t in M.targets(f, rels, kinds):
            if t in idx and t not in plumbing:
                out.append([idx[t], _loc(M.edge(f, t, rels) or {})])
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
                       "wr": [[idx[w], _loc(M.edge(w, n, WRITE) or {})] for w in M.sources(n, WRITE, ("function",)) if w in idx],
                       "rd": [[idx[r], _loc(M.edge(r, n, READ) or {})] for r in M.sources(n, READ, ("function",)) if r in idx],
                       "at": 1 if m.get("address_taken") else 0})
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
    return {"title": title, "dataset": dataset, "it": items, "comps": comps}


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
</style></head><body>
<div id="side"><h1><a href="#" id="home"></a></h1><input id="q" placeholder="Search name or description ..." autocomplete="off">
<div id="chips"></div><div id="list"></div></div>
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

// ---------- pages ----------
function pageHome(){
 const nf=IT.filter(x=>x.t==='f').length,nv=IT.filter(x=>x.t==='v').length,nc=IT.filter(x=>x.t==='c').length;
 main.innerHTML=`<h2>${esc(D.title)}</h2><div class="meta">${nf} functions &middot; ${nv} variables &middot; ${nc} calibrations &middot; ${C.length} components`+(D.dataset?` &middot; values from dataset <code>${esc(D.dataset)}</code>`:'')+`</div>
 <h3>Components</h3>`+table(['Component','Functions','Inputs','Outputs'],C.map((c,k)=>[`<a href="#k/${k}">${esc(c.n)}</a>`,c.f.length,c.in.length,c.out.length]))}
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
 if(c.in.length)h+=`<h3>Component inputs</h3>`+sigTable(c.in.map(r=>r[0]),{head:['Written outside by'],row:id=>{const r=c.in.find(q=>q[0]===id);return [r[2]?'<span class="mut">external input (no writer in the code)</span>':list(r[1],4)]}});
 if(c.out.length)h+=`<h3>Component outputs</h3>`+sigTable(c.out.map(r=>r[0]),{head:['Read outside by'],row:id=>[list(c.out.find(q=>q[0]===id)[1],4)]});
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
  if(x.par&&x.par.length)h+=table(['Parameter','Type','Role','Meaning (from comment)'],x.par.map(p=>[`<code>${esc(p.n)}</code>`,`<code>${esc(p.t)}</code>`,esc(p.r),esc(p.d)]));
  const nodes=[{k:'me',label:x.n,cls:'me'}],edges=[],seen=new Set(['me']);
  const nd=(i)=>{const k='n'+i;if(!seen.has(k)){seen.add(k);nodes.push({k,label:IT[i].n,href:'#i/'+i,cls:IT[i].t==='b'?'out':''})}return k};
  (x.cb||[]).slice(0,25).forEach(i=>edges.push([nd(i),'me']));x.cl.slice(0,25).forEach(i=>edges.push(['me',nd(i)]));
  if(nodes.length>1)h+=`<h3>Call flow</h3>`+flow(nodes,edges);
  h+=`<h3>Inputs</h3>`;
  if(x.rd.length)h+=`<h4>Variables read</h4>`+sigTable(x.rd.map(r=>r[0]),{head:['At'],row:id=>[`<code>${esc((x.rd.find(r=>r[0]===id)||[])[1]||'')}</code>`]});
  if(x.ca.length)h+=`<h4>Calibrations read</h4>`+sigTable(x.ca)+mapInputs(x.ca);
  if(!x.rd.length&&!x.ca.length)h+=`<p class="mut">reads no variable or calibration</p>`;
  h+=`<h4>Callers</h4><p>${list(x.cb||[],60)}</p><h3>Outputs</h3>`;
  h+=x.wr.length?`<h4>Variables written</h4>`+sigTable(x.wr.map(r=>r[0]),{head:['At'],row:id=>[`<code>${esc((x.wr.find(r=>r[0]===id)||[])[1]||'')}</code>`]}):'<p class="mut">writes no variable</p>';
  h+=`<h4>Calls</h4><p>${list(x.cl,60)}</p>`}
 else if(x.t==='b'){const cb=CB.get(id)||[];h=head(x,id,' &middot; not part of the exported scope')+docBlock(x)+`<h3>Called by</h3><p>${list(cb,80)}</p>`}
 else if(x.t==='v'){
  h=head(x,id,(x.st?` &middot; ${esc(x.st)}`:'')+(x.di?` &middot; defined in <code>${esc(x.di)}</code>`:''))+docBlock(x);
  h+=`<h3>A2L</h3>`+table(['Description','Range','Value / enum table','Unit'],[[esc(x.d||''),esc(x.r||''),valCell(x,id),esc(x.u||'')]]);
  if(x.at)h+=`<div class="warn">Address taken - may also be written through a pointer.</div>`;
  const fr=(a)=>table(['Function','At','Summary'],a.map(([f,l])=>[A(f),`<code>${esc(l)}</code>`,esc(IT[f].s||'')]));
  h+=`<h3>Written by</h3>`+(x.wr.length?fr(x.wr):'<p class="mut">no writer found (pointer write, inactive #ifdef, outside the compile DB, or external input)</p>')+`<h3>Read by</h3>`+(x.rd.length?fr(x.rd):'<p class="mut">no reader</p>')}
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
function route(){const m=location.hash.match(/^#([ik])\/(\d+)/);if(!m)return pageHome();m[1]==='k'?pageComp(+m[2]):pageItem(+m[2]);main.scrollTop=0}

// ---------- sidebar: search + filter ----------
let kind='';const CH=[['','All'],['f','Functions'],['v','Variables'],['c','Calibrations']];
$('#chips').innerHTML=CH.map(([k,l])=>`<span class="chip${k===kind?' on':''}" data-k="${k}">${l}</span>`).join('');
$('#chips').onclick=e=>{if(e.target.dataset.k!==undefined){kind=e.target.dataset.k;document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',c.dataset.k===kind));side()}};
function side(){const q=$('#q').value.trim().toLowerCase(),L=$('#list');
 if(!q&&!kind){L.innerHTML=C.map((c,k)=>`<a href="#k/${k}">${esc(c.n)} <small>${c.f.length}</small></a>`).join('');return}
 const out=[];for(let i=0;i<IT.length&&out.length<300;i++){const x=IT[i];if(kind?x.t!==kind:x.t==='x')continue;
  if(!q||x.n.toLowerCase().includes(q)||(x.d&&x.d.toLowerCase().includes(q))||(x.s&&x.s.toLowerCase().includes(q))||(x.a2l&&x.a2l.some(a=>a.n.toLowerCase().includes(q))))out.push(i)}
 L.innerHTML=out.map(i=>`<a href="#i/${i}">${esc(IT[i].n)} <small>${KIND[IT[i].t].toLowerCase()}</small></a>`).join('')||'<p class="mut" style="padding:8px">No match.</p>'}
$('#q').oninput=side;window.onhashchange=route;side();route();
</script></body></html>
"""
