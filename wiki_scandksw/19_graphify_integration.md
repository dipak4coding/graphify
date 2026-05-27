# Graphify Integration — Python Port

Tags: #integration #python #graphify
Links: [[00_INDEX]] | [[07_ifdef_state_machine]] | [[13_brute_force_search]]

---

## The Big Decision: Port vs Bridge

When integrating ScanDKSW with graphify, two approaches were considered:

**Option A: Full port** — rewrite all of ScanDKSW in Python  
**Option B: Selective port** — port only the critical parts, use ScanDKSW's CSV output as bridge

**Decision: Option B** — Selective port.

Reason: ScanDKSW's A2L scanner, CAN scanner, type classification, and binary cache are all project-specific and already work. Only the `#if` state machine and brute-force search were needed as a real-time in-process Python pipeline.

---

## What Was Ported

| ScanDKSW feature | Python equivalent | Location |
|------------------|-------------------|----------|
| `IfNesting` byte-flag stack | `CPreprocessor` class | `extract_c_signals.py` |
| `parseIfs()` for `#ifdef`/`#ifndef` | `CPreprocessor.process()` | `extract_c_signals.py` |
| `CFile2List()` comment stripping | `_strip_block_comments()` | `extract_c_signals.py` |
| `collect_all_defines()` (main scan pass) | `collect_all_defines()` | `extract_c_signals.py` |
| Brute-force usage search | `scan_files_for_usages()` | `extract_c_signals.py` |
| `extractClust()` | `_group_from_filename()` | `extract_c_signals.py` |
| `readConfig()` + `auto_detect()` | `auto_detect()` | `scandksw_config.py` |
| Group makelist reading | `read_group_list()`, `read_group_c_files()` | `scandksw_config.py` |
| make.bat parsing | `_parse_predefined_from_make_bat()` | `scandksw_config.py` |

**What was NOT ported**:
- The AST pipeline (Lexer → Optimizer → Parser → Compiler → Calculator) — the Python port uses a simplified expression evaluator for `#if` expressions
- A2L scanner — not needed for graphify's signal flow purpose
- CAN scanner — not needed (graphify traces software-level signals, not CAN)
- Type classification — not needed (graphify only cares about signal finder macros matching the `_GET_/_SET_` pattern)
- Binary cache — Python uses in-memory dicts (fast enough for Python)

---

## `CPreprocessor` — The State Machine Port

```python
class CPreprocessor:
    def __init__(self, predefined=None):
        self._defines = dict(predefined or {})  # name → value (empty string = flag only)
        self._nesting = [0x03]                  # outer scope always active

    @property
    def active(self) -> bool:
        """True when ALL levels are in live code."""
        e = 0x01
        for i in self._nesting:
            e &= i
        return e == 0x01

    def process(self, line: str) -> bool:
        """Process one preprocessor line. Returns current active state."""
        stripped = line.strip()
        
        if stripped.startswith("#ifdef"):
            name = stripped[6:].strip().split()[0]
            val = 0x03 if name in self._defines else 0x00
            self._nesting.insert(0, val if self.active else 0x02)
        
        elif stripped.startswith("#ifndef"):
            name = stripped[7:].strip().split()[0]
            val = 0x00 if name in self._defines else 0x03
            self._nesting.insert(0, val if self.active else 0x02)
        
        elif stripped.startswith("#if"):
            # Simplified: try to evaluate, default true on failure
            expr = stripped[3:].strip()
            result = self._eval_if(expr)
            self._nesting.insert(0, 0x03 if result else 0x00
                                    if self.active else 0x02)
        
        elif stripped.startswith("#elif"):
            expr = stripped[5:].strip()
            result = self._eval_if(expr)
            self._nesting[0] = (0x03 if result else 0x00
                                if self._nesting[0] == 0x00 else 0x02)
        
        elif stripped.startswith("#else"):
            self._nesting[0] = 0x03 if self._nesting[0] == 0x00 else 0x02
        
        elif stripped.startswith("#endif"):
            if len(self._nesting) > 1:
                self._nesting.pop(0)
        
        return self.active
```

**What differs from the C# original**: The Python version uses a simple `_eval_if()` helper rather than the full Lexer→Parser→Compiler→Calculator pipeline. For the automotive codebase in question, almost all `#if` expressions are simple `#ifdef`/`#ifndef` or simple equality checks (`CS_VAR == EN_VALUE`), so a lightweight evaluator suffices.

---

## `collect_all_defines()` — Two-Layer Architecture

The Python integration uses a two-layer approach that matches ScanDKSW exactly:

### Layer 1: Matrix layer (ScanDKSW equivalent)

```python
def collect_all_defines(files, predefined=None):
    """
    ONE forward pass through all files.
    Collects EVERY #define found while active (parsen=true).
    No name filter — everything goes into the dict.
    This is the ScanDKSW matrix layer.
    """
    all_defines = {}
    pp = CPreprocessor(predefined)
    
    for f in files:
        source = _strip_block_comments(f.read_text(encoding='utf-8', errors='replace'))
        group = _group_from_filename(f.name)
        
        for lineno, raw_line in enumerate(source.splitlines(), 1):
            is_preprocessor = raw_line.strip().startswith("#")
            pp.process(raw_line)
            if not pp.active:
                continue
            
            m = _DEFINE_RE.match(raw_line)
            if m:
                name, value = m.group(1), m.group(2).strip()
                if name not in all_defines:
                    all_defines[name] = {
                        "name": name,
                        "value": value,
                        "source_file": str(f),
                        "source_line": lineno,
                        "group": group,
                        "findings": [],
                    }
    
    return all_defines
```

### Layer 2: Brute-force usage scan

```python
def scan_files_for_usages(all_defines, source_files, predefined=None):
    """
    Outer loop: source files and their lines.
    Inner loop: all define names.
    Records every line where a define name appears as substring.
    Exactly mirrors ScanDKSW's else-branch in the main loop.
    """
    pp = CPreprocessor(predefined)
    define_names = list(all_defines.keys())
    
    for c_file in source_files:
        source = _strip_block_comments(c_file.read_text(...))
        consumer_group = _group_from_filename(c_file.name)
        
        for lineno, raw_line in enumerate(source.splitlines(), 1):
            is_preprocessor = raw_line.strip().startswith("#")
            pp.process(raw_line)
            if is_preprocessor or not pp.active:
                continue  # skip preprocessor lines and dead code
            
            for name in define_names:
                if name in raw_line:
                    all_defines[name]["findings"].append({
                        "file": str(c_file),
                        "line": lineno,
                        "group": consumer_group,
                    })
```

### Layer 3: Graph layer (signal-filtered)

```python
# Filter all_defines to only signal-pattern matches
sig_re = re.compile(r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+")
signals = {name: d for name, d in all_defines.items() if sig_re.match(name)}
```

Then run a function-level scan (`scan_c_file_for_signals()`) to get function → signal edges for the graphify graph.

---

## `auto_detect()` — Config Auto-Detection

The major improvement over ScanDKSW: no manual Config.xml needed.

```python
def auto_detect(cwd=None):
    source_root = Path(cwd or ".").resolve()
    project_root = source_root.parent       # one level up

    # Variant from components.{variant}.makelist
    makelists = sorted(source_root.glob("components.*.makelist"))
    if makelists:
        variant = makelists[0].stem[len("components."):]  # "dq381g4"
    else:
        variant = "unknown"

    # Fixed convention paths
    make_bat = project_root / "01_Buildprocess" / "make.bat"
    a2l = project_root / "99_Output" / "Target" / f"dksw_{variant}.a2l"

    # Output inside graphify-out/
    graphify_out = source_root / "graphify-out"
    scandksw_out = graphify_out / "scandksw"
    kommentare_out = graphify_out / "Kommentare"

    # Extract predefined switches from make.bat
    predefined = _parse_predefined_from_make_bat(make_bat)

    return ScanDKSWConfig(
        project_root=project_root,
        source_root=source_root,
        variant=variant,
        components_makelist=makelists[0] if makelists else None,
        make_bat=make_bat if make_bat.exists() else None,
        a2l=a2l if a2l.exists() else None,
        scandksw_out=scandksw_out,
        kommentare_out=kommentare_out,
        predefined=predefined,
    )
```

The `_parse_predefined_from_make_bat()` extracts `-D NAME=VALUE` flags and `SET NAME=VALUE` batch assignments automatically — no manual listing of compiler switches needed.

---

## Output: graphify-compatible JSON

Instead of ScanDKSW's binary `.bin` + CSV pipeline, the Python integration produces:

```json
{
  "nodes": [
    {"id": "module_krs", "label": "KRS", "node_type": "module", ...},
    {"id": "signal_krs_get_n_turb", "label": "KRS_GET_N_TURB", "node_type": "signal", ...},
    {"id": "func_svc_main_c_read_turb", "label": "read_turb", "node_type": "function", ...}
  ],
  "edges": [
    {"source": "module_krs", "target": "signal_krs_get_n_turb", "relation": "produces"},
    {"source": "signal_krs_get_n_turb", "target": "func_svc_main_c_read_turb", "relation": "read_by"},
    {"source": "module_krs", "target": "module_svc", "relation": "feeds_into", "via_signal": "KRS_GET_N_TURB"}
  ],
  "meta": {
    "variant": "dq381g4",
    "signals": 42,
    "groups": 12,
    "findings": 187
  }
}
```

This is the `graphify-out/scandksw/signal_graph.json` file, which graphify can load directly for visualisation.

---

## Running the Integration

```bash
# from the source folder (e.g. 03_SwFunktion/)
python -m graphify --signals .

# or directly
python -m graphify.scandksw_runner .
python -m graphify.scandksw_runner /path/to/03_SwFunktion
python -m graphify.scandksw_runner . --quiet
```

Or from Python:
```python
from graphify.scandksw_runner import run
result = run(Path("03_SwFunktion"), quiet=False)
print(f"{len(result['nodes'])} nodes, {len(result['edges'])} edges")
```

---

## Difference Summary

| Feature | ScanDKSW (C#) | Python port |
|---------|--------------|-------------|
| Configuration | Manual Config.xml | Auto-detected from folder structure |
| `#if` evaluation | Full AST pipeline | Simplified evaluator (sufficient for common patterns) |
| A2L parsing | Full ASAP2 parser | Not ported (not needed for graphify) |
| CAN tracing | Full BIL→SIC chain | Not ported (software-level only) |
| Output | Multiple CSVs + .bin cache | JSON (graphify-compatible) + CSV |
| Type classification | 8 types (CONST, MACRO, etc.) | Not classified (filtered by name pattern) |
| Duplicate detection | Tracked in D_Doubletten | Silent skip (first definition wins) |
| H file inlining | True inline (Listing prepend) | Separate pass per file (defines collected first) |

---

*Back to: [[00_INDEX]]*
