"""Parameter databank: calibration values from ``.par`` files, kept outside ``graph.json``.

A dataset (one ``.par`` file = one parameter set) is imported into a small SQLite file
(``params.db`` next to the graph). The graph only holds the A2L parameter name
(``metadata.a2l_characteristic_name``); that name is the key into the databank.

``.par`` syntax handled (one entry per parameter, names are the A2L characteristic names)::

    Name [FLOAT]  1.5 ; 1.5                 scalar:  raw ; physical
    Mode [UINT(8)]  1 ; Sport              enum:    raw is a number, physical is text
    Sw.bit1 [UINT(1)]  1 ; 1               bit of the parent variable Sw
    Curve [INT(16),(1,4)]  0 ; 0           curve:   1 row, 4 columns
    :  100 ; 10                            continuation line: next value
    Map [INT(16),(2,3)]  0 ; 0             map:     2 rows, 3 columns, row by row
    :  100 ; 10 ...

Lines that are not entries (file header, comments, anything unknown) are kept as header
lines or reported as unparsed; they never abort the import. With ``--a2l`` the import is
cross-checked against the A2L and the findings go into ``params_import_<name>.json``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# name may itself contain index brackets (variable_ko[3]); the type spec is the bracket group that is
# followed by the first value (or by nothing). Non-greedy name + backtracking finds it.
_ENTRY = re.compile(r"^(?P<name>[^\s:;]\S*?)\s*\[(?P<spec>[^\]]*)\]\s*:?\s*(?P<rest>(?:(?:[-+]?\d|\.\d).*)?)$")
_CONT = re.compile(r"^:\s*(?P<rest>.*)$")
_SHAPE = re.compile(r",\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)")
_ONE_BIT = re.compile(r"^U?INT\(1\)$", re.IGNORECASE)
_BIT = re.compile(r"^(?P<parent>.+)\.bit(?P<bit>\d+)$", re.IGNORECASE)
MAX_LIST = 200   # findings per category kept in the import report


@dataclass
class Entry:
    name: str
    dtype: str = ""
    rows: int = 1
    cols: int = 1
    raw: list = field(default_factory=list)
    phys: list = field(default_factory=list)
    line: int = 0
    kind: str = "scalar"          # scalar | enum | bit | curve | map
    parent: str = ""
    bit: int | None = None
    issues: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _num(tok: str):
    t = tok.strip()
    try:
        return int(t, 0)
    except ValueError:
        pass
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def _pair(rest: str):
    """``raw ; physical`` -> (raw number, physical number | text), or None if raw is not a number."""
    rest = rest.strip().rstrip(",").strip()
    if not rest:
        return None
    raw_s, _, phys_s = rest.partition(";")
    raw = _num(raw_s)
    if raw is None:
        return None
    phys_s = phys_s.strip().rstrip(",;").strip()
    if not phys_s:
        return raw, raw
    n = _num(phys_s)
    return raw, (n if n is not None else phys_s.strip('"'))


def _pairs(rest: str):
    """One line may carry several values separated by ':' (``0 ; 0 : 100 ; 10``). -> list of
    (raw, phys), or None when any piece is not a ``raw ; physical`` pair."""
    pieces = [x for x in rest.split(":") if x.strip()]
    out = []
    for piece in pieces:
        p = _pair(piece)
        if p is None:
            one = _pair(rest)           # ':' may belong to a text value; try the line as one pair
            return [one] if one is not None else None
        out.append(p)
    return out or None


def _finish(e: Entry) -> Entry:
    m = _BIT.match(e.name)
    if m:
        e.kind, e.parent, e.bit = "bit", m["parent"], int(m["bit"])
    elif _ONE_BIT.match(e.dtype) and "." in e.name and e.rows * e.cols == 1:
        e.kind, e.parent = "bit", e.name.rsplit(".", 1)[0]     # struct bit member: Arr[DS].Member [UINT(1)]
    elif any(isinstance(p, str) for p in e.phys):
        e.kind = "enum"
    elif e.rows * e.cols == 1:
        e.kind = "scalar"
    elif e.rows == 1 or e.cols == 1:
        e.kind = "curve"
    else:
        e.kind = "map"
    if len(e.phys) != e.rows * e.cols:
        e.issues.append(f"value count {len(e.phys)} != {e.rows}x{e.cols}")
    return e


def parse_par(text: str):
    """-> (entries, header_lines, unparsed [(line_no, text)]). Never raises on odd lines."""
    entries, header, unparsed = [], [], []
    cur = None
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        m = _ENTRY.match(s)
        if m is None and cur is not None:
            c = _CONT.match(s)
            ps = _pairs(c["rest"] if c else s)          # continuation: ': raw ; phys' or just 'raw ; phys'
            if ps is not None:
                for r_, p_ in ps:
                    cur.raw.append(r_)
                    cur.phys.append(p_)
                continue
        if m:
            spec = m["spec"]
            sm = _SHAPE.search(spec)
            rows, cols = 1, 1
            if sm:
                rows, cols = (int(sm.group(1)), int(sm.group(2))) if sm.group(2) else (1, int(sm.group(1)))
            cur = Entry(name=m["name"], dtype=_SHAPE.sub("", spec).strip(), rows=rows, cols=cols, line=ln)
            entries.append(cur)
            for r_, p_ in (_pairs(m["rest"]) or []):
                cur.raw.append(r_)
                cur.phys.append(p_)
        elif not entries:
            header.append(s)
        else:
            unparsed.append((ln, s))
    return [_finish(e) for e in entries], header, unparsed


# ---------------------------------------------------------------------------
# A2L cross-check
# ---------------------------------------------------------------------------

def _normkey(k):
    n = _num(str(k))
    return (str(int(n)) if isinstance(n, float) and n.is_integer() else str(n)) if n is not None else str(k)


def a2l_candidates(e) -> list:
    """Names under which the A2L may know a .par entry, most specific first."""
    strip = lambda t: re.sub(r"\[[^\]]*\]", "", t)
    out = [e.name, strip(e.name)]
    if e.kind == "bit":
        out += [e.parent, strip(e.parent)]
    seen, res = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            res.append(c)
    return res


def a2l_key_for(e, chars: dict, axp: dict):
    for c in a2l_candidates(e):
        if c in chars or c in axp:
            return c
    return None


def analyze(entries: list, a2l: dict):
    """Cross-check entries against a scanned A2L. -> (report parts, {name: enrichment})."""
    chars, axp = a2l.get("characteristics", {}), a2l.get("axis_pts", {})
    cms, vtabs = a2l.get("compu_methods", {}), a2l.get("compu_vtabs", {})
    by_name = {e.name: e for e in entries}
    rep = {k: [] for k in ("not_in_a2l", "kind_vs_a2l", "axis_missing", "shape_vs_axis", "enum_issues")}
    enrich, covered = {}, set()
    expect_kind = {"VALUE": {"scalar", "enum", "bit"}, "CURVE": {"curve"}, "MAP": {"map"}}

    for e in entries:
        k2 = a2l_key_for(e, chars, axp)
        key = k2 if k2 in chars else None
        if key is None:
            if k2 in axp:
                cm = cms.get(axp[k2].get("compu_method", ""), {})
                enrich[e.name] = {"char_type": "AXIS_PTS", "unit": cm.get("unit", ""), "axes": []}
            else:
                rep["not_in_a2l"].append(e.name)
            continue
        c = chars[key]
        covered.add(key)
        ct, cm = c.get("char_type", ""), cms.get(c.get("compu_method", ""), {})
        axes = list(c.get("axis_references") or [])
        enrich[e.name] = {"char_type": ct, "unit": cm.get("unit", ""), "axes": axes,
                          "min": c.get("min_value"), "max": c.get("max_value")}
        if ct in expect_kind and e.kind not in expect_kind[ct] and e.kind != "bit":
            rep["kind_vs_a2l"].append({"name": e.name, "a2l": ct, "par": f"{e.kind} {e.rows}x{e.cols}"})
        expected = [(0, e.cols), (1, e.rows)] if ct == "MAP" else [(0, e.rows * e.cols)] if ct == "CURVE" else []
        for idx, n in expected:
            if idx >= len(axes):
                continue            # inline axis without an AXIS_PTS reference
            ae = by_name.get(axes[idx])
            if ae is None:
                rep["axis_missing"].append({"name": e.name, "axis": axes[idx]})
            elif len(ae.phys) != n:
                rep["shape_vs_axis"].append({"name": e.name, "axis": axes[idx], "axis_points": len(ae.phys),
                                             "expected": n, "par_shape": f"{e.rows}x{e.cols}",
                                             "axis_role": "X (columns)" if idx == 0 and ct == "MAP"
                                             else "Y (rows)" if idx == 1 else "X"})
        if cm.get("type") == "TAB_VERB" and cm.get("compu_vtab") in vtabs:
            table = {_normkey(k): v for k, v in vtabs[cm["compu_vtab"]].get("content", {}).items()}
            for r, p in zip(e.raw, e.phys):
                if not isinstance(p, str):
                    continue
                want = table.get(_normkey(r))
                if want is None:
                    rep["enum_issues"].append({"name": e.name, "raw": r, "problem": "raw value not in A2L table"})
                elif want != p:
                    rep["enum_issues"].append({"name": e.name, "raw": r, "par": p, "a2l": want,
                                               "problem": "label differs"})
    rep["a2l_without_value"] = sorted(n for n in chars if n not in covered)
    return rep, enrich


# ---------------------------------------------------------------------------
# SQLite databank
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dataset(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, project TEXT, variant TEXT, version TEXT,
  source_file TEXT, sha256 TEXT, imported_at TEXT, header TEXT, a2l_file TEXT);
CREATE TABLE IF NOT EXISTS parameter(
  dataset_id INTEGER NOT NULL, name TEXT NOT NULL, kind TEXT, dtype TEXT, rows INTEGER, cols INTEGER,
  parent TEXT, bit INTEGER, raw TEXT, phys TEXT, axes TEXT, unit TEXT, char_type TEXT, line INTEGER,
  PRIMARY KEY(dataset_id, name));
"""


def default_db_path(graph_path=None) -> Path:
    if not graph_path:
        from graphify.paths import default_graph_json
        graph_path = default_graph_json()
    return Path(graph_path).resolve().parent / "params.db"


def open_db(path, create: bool = True) -> sqlite3.Connection:
    p = Path(path)
    if not create and not p.is_file():
        raise FileNotFoundError(f"parameter databank not found: {p} (import a .par first: graphify params import)")
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def import_dataset(conn, name: str, entries: list, meta: dict, enrich: dict | None = None) -> dict:
    """Store (or replace) one dataset. Returns {'replaced': bool, 'duplicates': [names]}."""
    enrich = enrich or {}
    old = conn.execute("SELECT id FROM dataset WHERE name=?", (name,)).fetchone()
    if old:
        conn.execute("DELETE FROM parameter WHERE dataset_id=?", (old["id"],))
        conn.execute("DELETE FROM dataset WHERE id=?", (old["id"],))
    cur = conn.execute(
        "INSERT INTO dataset(name,project,variant,version,source_file,sha256,imported_at,header,a2l_file) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (name, meta.get("project"), meta.get("variant"), meta.get("version"), meta.get("source_file"),
         meta.get("sha256"), datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "\n".join(meta.get("header", [])), meta.get("a2l_file")))
    did, seen, dups = cur.lastrowid, set(), []
    for e in entries:
        if e.name in seen:
            dups.append(e.name)
            continue
        seen.add(e.name)
        x = enrich.get(e.name, {})
        conn.execute(
            "INSERT INTO parameter VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, e.name, e.kind, e.dtype, e.rows, e.cols, e.parent, e.bit, json.dumps(e.raw),
             json.dumps(e.phys), json.dumps(x.get("axes", [])), x.get("unit", ""), x.get("char_type", ""), e.line))
    conn.commit()
    return {"replaced": bool(old), "duplicates": dups}


def list_datasets(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM parameter p WHERE p.dataset_id=d.id) AS parameters "
        "FROM dataset d ORDER BY d.name")]


def resolve_dataset(conn, name: str | None):
    """-> (name, None) or (None, message). With one dataset in the databank the name may be omitted."""
    names = [r["name"] for r in conn.execute("SELECT name FROM dataset ORDER BY name")]
    if name:
        return (name, None) if name in names else (None, f"No dataset '{name}'. Available: {', '.join(names) or 'none'}")
    if len(names) == 1:
        return names[0], None
    return None, f"Give --dataset NAME. Available: {', '.join(names) or 'none (import a .par first)'}"


def get_parameter(conn, dataset: str, name: str):
    r = conn.execute("SELECT p.* FROM parameter p JOIN dataset d ON d.id=p.dataset_id "
                     "WHERE d.name=? AND p.name=?", (dataset, name)).fetchone()
    if r is None:
        return None
    d = dict(r)
    for k in ("raw", "phys", "axes"):
        d[k] = json.loads(d[k] or "[]")
    return d


def find_parameters(conn, dataset: str, text: str, limit: int = 10) -> list:
    return [r["name"] for r in conn.execute(
        "SELECT p.name FROM parameter p JOIN dataset d ON d.id=p.dataset_id "
        "WHERE d.name=? AND p.name LIKE ? ORDER BY p.name LIMIT ?", (dataset, f"%{text}%", limit))]


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _grid(rows: list, indent: str) -> list:
    width = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [indent + "  ".join(c.rjust(width[i]) for i, c in enumerate(r)).rstrip() for r in rows]


def describe(conn, dataset: str, name: str, max_cells: int = 64, indent: str = "  ", with_hint: bool = True):
    """Printable lines for one parameter of a dataset, or None if the dataset has no such entry.

    Scalars and enums are one line. Curves and maps are printed as a table with their axis
    values (rows = Y, columns = X) up to ``max_cells`` cells; larger ones as a summary."""
    p = get_parameter(conn, dataset, name)
    if p is None:
        return None
    unit = f" {p['unit']}" if p["unit"] else ""
    raw, phys, kind = p["raw"], p["phys"], p["kind"]
    if kind in ("scalar", "enum", "bit") and len(phys) == 1:
        extra = f" (raw {_fmt(raw[0])})" if raw[0] != phys[0] else ""
        return [f"{indent}{name} [{p['dtype']}] = {_fmt(phys[0])}{extra}{unit}"]
    rows, cols = p["rows"], p["cols"]
    head = f"{indent}{name} [{p['dtype']}] {kind} {rows}x{cols}{unit}"
    if len(phys) != rows * cols:
        return [head + f"  (value count {len(phys)} does not match the shape)"]
    axes = p["axes"]

    def axis_vals(idx, n):
        if idx < len(axes):
            a = get_parameter(conn, dataset, axes[idx])
            if a and len(a["phys"]) == n:
                return axes[idx], [_fmt(v) for v in a["phys"]]
        return None, [str(i) for i in range(n)]

    x_name, xs = axis_vals(0, cols)
    y_name, ys = axis_vals(1, rows) if kind == "map" else (None, [])
    nums = [v for v in phys if isinstance(v, (int, float))]
    if len(phys) > max_cells:
        rng = f", min {_fmt(min(nums))} max {_fmt(max(nums))}" if nums else ""
        ax = ", ".join(f"{r}={n}" for r, n in (("X", x_name), ("Y", y_name)) if n)
        hint = f"; full table: graphify params show {name}" if with_hint else ""
        return [head + rng + (f"; axes {ax}" if ax else "") + hint]
    cells = [_fmt(v) for v in phys]
    out = [head + (f"  [X axis {x_name}]" if x_name and kind == "curve" else "")]
    if kind == "map":
        out[0] += f"  [X axis {x_name or '-'}, Y axis {y_name or '-'}]"
        table = [["Y\\X"] + xs] + [[ys[r]] + cells[r * cols:(r + 1) * cols] for r in range(rows)]
    elif rows == 1:
        table = [["X"] + xs, ["value"] + cells]
    else:
        table = [["X", "value"]] + [[ys_ or str(i), c] for i, (ys_, c) in
                                    enumerate(zip(axis_vals(0, rows)[1], cells))]
    return out + _grid(table, indent + "  ")


def make_lookup(conn, dataset: str, max_cells: int = 64, indent: str = "      "):
    """callable(a2l name) -> lines, for embedding values in explain output."""
    def lookup(key: str) -> list:
        lines = describe(conn, dataset, key, max_cells=max_cells, indent=indent)
        return lines if lines is not None else [f"{indent}value: not in dataset '{dataset}'"]
    return lookup


# ---------------------------------------------------------------------------
# import driver and CLI
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _load_a2l(path: str) -> dict:
    if str(path).lower().endswith(".json"):
        return json.loads(Path(path).read_text(encoding="utf-8"))
    from graphify.extractors.a2l import scan_a2l
    return scan_a2l(path)


def import_par(par_path: str, db_path, name: str, a2l: dict | None = None, a2l_file: str = "",
               project: str = "", variant: str = "", version: str = "") -> dict:
    p = Path(par_path)
    text = _read_text(p)
    entries, header, unparsed = parse_par(text)
    rep, enrich = analyze(entries, a2l) if a2l else ({}, {})
    conn = open_db(db_path)
    try:
        stored = import_dataset(conn, name, entries, {
            "project": project, "variant": variant, "version": version, "source_file": str(p),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "header": header, "a2l_file": a2l_file}, enrich)
    finally:
        conn.close()
    kinds: dict = {}
    for e in entries:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    report = {
        "dataset": name, "source": str(p), "a2l": a2l_file or None, "replaced_existing": stored["replaced"],
        "entries": len(entries), "by_kind": kinds, "header_lines": header[:MAX_LIST],
        "duplicate_names": stored["duplicates"][:MAX_LIST],
        "unparsed_lines": [{"line": ln, "text": t} for ln, t in unparsed[:MAX_LIST]],
        "unparsed_count": len(unparsed),
        "value_count_issues": [{"name": e.name, "line": e.line, "issue": e.issues[0]} for e in entries if e.issues][:MAX_LIST],
    }
    for k, v in rep.items():
        report[k] = v[:MAX_LIST]
        report[f"{k}_count"] = len(v)
    out = Path(db_path).parent / f"params_import_{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    report["report_file"] = str(out)
    return report


def _opts(args: list, names: tuple) -> tuple:
    opts, rest, i = {}, [], 0
    while i < len(args):
        if args[i] in names and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return opts, rest


def run_params(argv: list) -> int:
    usage = ("Usage: graphify params import FILE.par --name DATASET [--a2l FILE.a2l|.json] [--project P] "
             "[--variant V] [--version X] [--db PATH]\n"
             "       graphify params show NAME [--dataset DATASET] [--max-cells N] [--db PATH]\n"
             "       graphify params list [--db PATH]")
    if not argv or argv[0] in ("-h", "--help"):
        print(usage, file=sys.stderr)
        return 1
    sub, args = argv[0], argv[1:]
    o, rest = _opts(args, ("--name", "--a2l", "--project", "--variant", "--version", "--db", "--dataset",
                           "--max-cells"))
    db = o.get("--db") or default_db_path()
    if sub == "import":
        if not rest or not o.get("--name"):
            print("error: params import needs FILE.par and --name DATASET\n" + usage, file=sys.stderr)
            return 1
        if not Path(rest[0]).is_file():
            print(f"error: file not found: {rest[0]}", file=sys.stderr)
            return 1
        a2l = _load_a2l(o["--a2l"]) if o.get("--a2l") else None
        r = import_par(rest[0], db, o["--name"], a2l, o.get("--a2l", ""), o.get("--project", ""),
                       o.get("--variant", ""), o.get("--version", ""))
        print(f"dataset '{r['dataset']}': {r['entries']} entries "
              f"({', '.join(f'{k}={v}' for k, v in sorted(r['by_kind'].items()))})"
              + (" - replaced the existing dataset" if r["replaced_existing"] else ""))
        print(f"  header lines: {len(r['header_lines'])}, unparsed lines: {r['unparsed_count']}, "
              f"duplicate names: {len(r['duplicate_names'])}, value-count problems: {len(r['value_count_issues'])}")
        if a2l:
            print("  vs A2L: " + ", ".join(f"{k}={r[k + '_count']}" for k in
                  ("not_in_a2l", "a2l_without_value", "kind_vs_a2l", "axis_missing", "shape_vs_axis", "enum_issues")))
        print(f"  databank: {db}\n  report:   {r['report_file']}")
        return 0
    try:
        conn = open_db(db, create=False)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if sub == "list":
        for d in list_datasets(conn):
            print(f"{d['name']}: {d['parameters']} parameters  project={d['project'] or '-'} "
                  f"variant={d['variant'] or '-'} version={d['version'] or '-'}  {d['imported_at']}  {d['source_file']}")
        return 0
    if sub == "show":
        if not rest:
            print("error: params show needs a parameter name\n" + usage, file=sys.stderr)
            return 1
        ds, err = resolve_dataset(conn, o.get("--dataset"))
        if err:
            print(err, file=sys.stderr)
            return 1
        mc = int(o.get("--max-cells", 100000))
        lines = describe(conn, ds, rest[0], max_cells=mc, indent="", with_hint=False)
        if lines is None:
            near = find_parameters(conn, ds, rest[0])
            print(f"No parameter '{rest[0]}' in dataset '{ds}'." + (f" Similar: {', '.join(near)}" if near else ""))
            return 1
        print(f"dataset {ds}")
        print("\n".join(lines))
        return 0
    print(f"error: unknown params command '{sub}'\n" + usage, file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# coverage report: every parameter in A2L / PAR / CODE
# ---------------------------------------------------------------------------

def _norm_name(n: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", str(n)).replace(".", "_").replace("->", "_").lower()


def coverage_rows(a2l: dict, entries: list, nodes: list) -> list:
    """One row per parameter name seen in A2L, PAR or code.

    A2L = characteristic or axis-points name in the A2L. PAR = entry in the .par (a bit
    entry ``Parent.bitN`` counts for ``Parent``). CODE = a code node joined to that A2L name
    (``metadata.a2l_characteristic_name`` / ``a2l_axis_pts_name``), or, for names the A2L does
    not know, an unmatched ``calibration_field`` node."""
    chars, axp = a2l.get("characteristics", {}), a2l.get("axis_pts", {})
    a2l_kind = {**{n: "CHAR" for n in chars}, **{n: "AXIS" for n in axp}}
    par_names = {}
    for e in entries:
        k = a2l_key_for(e, chars, axp)
        par_names.setdefault(k or (e.parent if e.kind == "bit" and _BIT.match(e.name) else e.name), e)
    code_by_a2l: dict = {}
    code_only: dict = {}
    code_keys: dict = {}              # normalised code key -> label, for near-miss hints
    var_names = {n.get("label") for n in nodes if n.get("type") == "variable" and n.get("file_type") != "document"}
    for n in nodes:
        if n.get("type") not in ("variable", "calibration_field") or n.get("file_type") == "document":
            continue
        m = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
        where = f"{n.get('label', '')} @ {n.get('source_file', '')}:{n.get('source_location', '')}".rstrip(":")
        linked = ({k for k in (m.get("a2l_characteristic_name"), m.get("a2l_axis_pts_name")) if k}
                  | set(m.get("a2l_members") or []) | set(m.get("a2l_axis_members") or []))
        for k in linked:
            code_by_a2l.setdefault(k, []).append(where)
        key = m.get("calibration_path") or n.get("label")
        if key and not linked:
            code_keys.setdefault(_norm_name(key), key)
            if n.get("type") == "calibration_field":
                code_only.setdefault(key, []).append(where)
    par_norm = {_norm_name(k): k for k in par_names}
    a2l_norm = {_norm_name(k): k for k in a2l_kind}
    rows = []
    for name in sorted(set(a2l_kind) | set(par_names) | set(code_only)):
        in_a2l, in_par = name in a2l_kind, name in par_names
        where = code_by_a2l.get(name) or code_only.get(name) or []
        in_code = bool(where)
        notes = []
        if in_a2l and not in_code:
            sym = (chars.get(name) or axp.get(name) or {}).get("internal_symbol", "")
            if sym:
                notes.append(f"A2L symbol: {sym}")
                plain_sym = re.sub(r"\[[^\]]*\]", "", sym.split("#")[0])
                root = re.split(r"[.\[]", sym, maxsplit=1)[0]
                if "." in plain_sym and root in var_names:           # struct member: only the struct is in the code
                    notes.append(f"code has variable '{root}', but no access to this member was found")
            hit = code_keys.get(_norm_name(sym or name))
            if hit:
                notes.append(f"code symbol '{hit}' looks the same (case/index differs) - join missed it")
        if in_par and not in_a2l:
            hit = a2l_norm.get(_norm_name(name))
            if hit:
                notes.append(f"A2L has '{hit}' (spelling differs)")
        if in_code and not in_a2l:
            hit = a2l_norm.get(_norm_name(name))
            if hit:
                notes.append(f"A2L has '{hit}' (spelling differs)")
            elif name in par_names:
                notes.append("in PAR but A2L has no entry")
        if in_a2l and in_par and in_code:
            status = "ok"
        elif in_a2l and in_code:
            status = "no value in PAR"
        elif in_a2l and in_par:
            status = "not found in code"
        elif in_a2l:
            status = "A2L only"
        elif in_par and in_code:
            status = "not in A2L"
        elif in_par:
            status = "PAR only"
        else:
            status = "code only"
        rows.append({"name": name, "kind": a2l_kind.get(name, ""), "a2l": in_a2l, "par": in_par, "code": in_code,
                     "status": status, "code_nodes": "; ".join(where[:3]) + (f" (+{len(where) - 3})" if len(where) > 3 else ""),
                     "note": " | ".join(notes)})
    return rows


def write_coverage(out_dir, rows: list, dataset: str = "") -> dict:
    """Write ``param_coverage.csv`` and ``param_coverage.json``; return status counts."""
    import csv
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["name", "kind", "a2l", "par", "code", "status", "code_nodes", "note"]
    with open(out / "param_coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([("yes" if r[c] else "no") if c in ("a2l", "par", "code") else r[c] for c in cols])
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    totals = {"parameters": len(rows), "in_a2l": sum(r["a2l"] for r in rows), "in_par": sum(r["par"] for r in rows),
              "in_code": sum(r["code"] for r in rows)}
    (out / "param_coverage.json").write_text(
        json.dumps({"dataset": dataset, "totals": totals, "by_status": counts, "rows": rows}, indent=1,
                   ensure_ascii=False), encoding="utf-8")
    return {"totals": totals, "by_status": counts, "csv": str(out / "param_coverage.csv")}


def extraction_step(par_path, a2l, nodes: list, out_dir, name: str = "", db_path=None) -> dict:
    """Used by the clang pass: import the .par into the databank, then write the coverage report.

    ``a2l`` is the scanned A2L (``scan_a2l``) or None. Never raises on a bad .par."""
    p = Path(par_path)
    name = name or p.stem
    text = _read_text(p)
    entries, header, unparsed = parse_par(text)
    rep, enrich = analyze(entries, a2l) if a2l else ({}, {})
    db = Path(db_path) if db_path else Path(out_dir) / "params.db"
    conn = open_db(db)
    try:
        import_dataset(conn, name, entries, {"source_file": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                             "header": header}, enrich)
    finally:
        conn.close()
    res = {"dataset": name, "entries": len(entries), "unparsed": len(unparsed), "databank": str(db)}
    if a2l:
        res.update(write_coverage(out_dir, coverage_rows(a2l, entries, nodes), name))
        res["import_checks"] = {k: len(v) for k, v in rep.items()}
        (Path(out_dir) / f"params_import_{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.json").write_text(
            json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return res
