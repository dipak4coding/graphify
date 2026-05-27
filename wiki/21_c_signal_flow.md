# C Signal Flow Graph — Automotive Embedded Codebase

Tags: #extension #automotive #c #signal-flow
Links: [[00_INDEX]] | [[04_extract_ast]] | [[20_how_to_modify_graphify]]

---

## The Problem This Solves

In automotive embedded C code (e.g. VW/AUTOSAR style gearbox controller):
- Code is split into **software groups** — folders like `KRS/`, `SVC/`, `GSI/`
- Groups **do not call each other directly** with function calls
- Instead they communicate via **signal macros** defined in header files

```
KRS group produces:                    GSI group reads:
#define KRS_GET_ZUSTAND (KrsZustand)   if (KRS_GET_ZUSTAND == KRS_EINLEGEN) ...
```

The `#define KRS_GET_ZUSTAND` IS the interface. It's the CAN signal path in code form.

Your goal: given a CAN output signal (e.g. `KRS_GET_AKTIV`), trace backwards through all functions that contribute to its value. Or given an input signal (e.g. `SVC_GET_M_MOT_FIL` — engine torque), trace forward through every function that reads it.

---

## The Signal Naming Convention

In the VASAMM / VW codebase the pattern is:

```
{MODULE}_{GET|SET}_{SIGNAL_NAME}
```

Examples:
| Signal macro           | Module  | Direction | Meaning |
|------------------------|---------|-----------|---------|
| `KRS_GET_AKTIV`        | KRS     | output    | Is downshift active? |
| `KRS_GET_ZUSTAND`      | KRS     | output    | Current downshift state |
| `SVC_GET_M_MOT_FIL`    | SVC     | output    | Filtered engine torque |
| `SVN_GET_NAN_FIL`      | SVN     | output    | Filtered input shaft speed |
| `GSI_GET_GANG_IST()`   | GSI     | output    | Actual gear |

From the perspective of a reading module (e.g. KRS):
- Every signal in `KRS_INPUTS.c` is an **external input** — KRS reads these from other modules
- Every signal in `KRS_OUTPUTS.c` (`KRS_GET_*` macros) is an **output** — KRS produces these for others

---

## How the Integration Works

### graphify/extract_c_signals.py — New Module

Three-phase pipeline:

```
Phase 1: Collect signal definitions from header files
    ↓ #define scanning with dead-code elimination
    ↓ {signal_name → module, value, source_file}

Phase 2: Scan C files for signal usages
    ↓ Preprocessor state machine (ports ScanDKSW's IfNesting)
    ↓ Function body tracking via brace-depth counting
    ↓ Substring match: signal name found in active function body

Phase 3: Build graphify nodes and edges
    → signal nodes (node_type="signal")
    → module nodes (node_type="module")
    → reads edges (function → reads → signal)
    → belongs_to edges (signal → belongs_to → module)
```

### The Preprocessor State Machine (Ported from ScanDKSW)

ScanDKSW uses a nesting stack (`IfNesting`) to track which `#if/#ifdef` branch is active:

```python
# Each entry is a byte:
#   0x03 = active right now (bit0=1, bit1=1)
#   0x02 = was active, now in else/elif → skip
#   0x00 = never been active → skip (including its else)

class CPreprocessor:
    def __init__(self, predefined):
        self._defines = dict(predefined)
        self._nesting = [0x03]  # outer scope always active

    @property
    def active(self) -> bool:
        e = 0x01
        for i in self._nesting:
            e &= i
        return e == 0x01
```

When you call `pp.process("#if CS_MODUS_HYBRID != EN_MODUS_HYBRID_AUS")`:
1. The condition `CS_MODUS_HYBRID != EN_MODUS_HYBRID_AUS` is evaluated
2. If False → push `0x00` onto the nesting stack
3. If True → push `0x03` onto the nesting stack
4. `pp.active` then reflects whether the following code should be parsed

The key insight: lines inside a **false `#if` block** are **invisible** to signal detection. This eliminates dead code for project-specific variants.

---

## Using the Signal Extractor

### Standalone (Python)

```python
from graphify.extract_c_signals import extract_signal_flow
from pathlib import Path

# Project-specific compiler switches that select the active code paths
predefined = {
    "CS_MODUS_DERIVAT": "EN_MODUS_DERIVAT_DQ250",
    "CS_MODUS_HYBRID":  "EN_MODUS_HYBRID_AUS",
    "CS_MODUS_SUPPORT_CAN": "EN_MODUS_SUPPORT_CAN_MQB_BASELINE",
}

result = extract_signal_flow(
    root_dir=Path("my_project/src"),
    predefined=predefined,
    # Pattern matches signal macros: MODULE_GET_SIGNAL or MODULE_SET_SIGNAL
    signal_pattern=r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+",
)

print(f"Signal nodes: {sum(1 for n in result['nodes'] if n['node_type']=='signal')}")
print(f"Module nodes: {sum(1 for n in result['nodes'] if n['node_type']=='module')}")
print(f"Reads edges:  {sum(1 for e in result['edges'] if e['relation']=='reads')}")
```

### Command Line

```bash
python3 -m graphify.extract_c_signals \
    --root ./my_project/src \
    --define CS_MODUS_DERIVAT=EN_MODUS_DERIVAT_DQ250 \
    --define CS_MODUS_HYBRID=EN_MODUS_HYBRID_AUS \
    --out signal_graph.json
```

### Merging with the main graphify graph

```bash
# Step 1: Run standard graphify to get the call graph
/graphify .

# Step 2: Run the signal extractor to get the signal flow
python3 -m graphify.extract_c_signals \
    --root . \
    --define CS_MODUS_DERIVAT=EN_MODUS_DERIVAT_DQ250 \
    --out graphify-out/signal_graph.json

# Step 3: Merge both graphs (Python)
import json, networkx as nx
from networkx.readwrite import json_graph
from graphify.build import build_from_json

G_main   = build_from_json(json.loads(open("graphify-out/graph.json").read()))
sig_data = json.loads(open("graphify-out/signal_graph.json").read())
G_sig    = build_from_json(sig_data)

G_merged = G_main.copy()
G_merged.update(G_sig)  # adds signal nodes + edges without removing existing ones
```

---

## Graph Schema — What You Get

### Signal Node

```json
{
  "id": "signal_krs_get_zustand",
  "label": "KRS_GET_ZUSTAND",
  "node_type": "signal",
  "module": "KRS",
  "direction": "output",
  "macro_value": "(KrsZustand)",
  "source_file": "krs/src/krs_app.h",
  "source_location": "L81",
  "confidence": "EXTRACTED",
  "confidence_score": 1.0
}
```

### Module Node

```json
{
  "id": "module_krs",
  "label": "KRS",
  "node_type": "module"
}
```

### Reads Edge (function reads a signal)

```json
{
  "source": "krs_bewertung",
  "target": "signal_svc_get_m_mot_fil",
  "relation": "reads",
  "confidence": "EXTRACTED",
  "confidence_score": 0.9,
  "source_file": "krs/src/krs_main.c",
  "source_location": "L247"
}
```

---

## Tracing Signals in the Graph

### Query: Which functions read signal X?

```python
import networkx as nx
from networkx.readwrite import json_graph
import json

G = json_graph.node_link_graph(json.loads(open("graphify-out/graph.json").read()), edges="links")

signal = "signal_svc_get_m_mot_fil"

readers = [
    G.nodes[pred].get("label", pred)
    for pred in G.predecessors(signal)
    if G.edges[pred, signal].get("relation") == "reads"
]
print(f"Functions that read SVC_GET_M_MOT_FIL: {readers}")
```

### Query: Trace a CAN output backward (what contributes to it?)

```python
def trace_backward(G, signal_nid, depth=3):
    """BFS backward from a signal to find all upstream functions."""
    visited = set()
    frontier = [signal_nid]
    for _ in range(depth):
        next_frontier = []
        for nid in frontier:
            for pred in G.predecessors(nid):
                if pred not in visited:
                    visited.add(pred)
                    next_frontier.append(pred)
                    label = G.nodes[pred].get("label", pred)
                    rel = G.edges[pred, nid].get("relation", "?")
                    print(f"  {label}  --{rel}-->  {G.nodes[nid].get('label', nid)}")
        frontier = next_frontier

# Trace who contributes to KRS_GET_AKTIV
trace_backward(G, "signal_krs_get_aktiv", depth=4)
```

### Query: Trace a CAN input forward (who processes it?)

```python
def trace_forward(G, signal_nid, depth=4):
    """BFS forward from a signal — find all downstream functions."""
    visited = set()
    frontier = [signal_nid]
    for _ in range(depth):
        next_frontier = []
        for nid in frontier:
            for succ in G.successors(nid):
                if succ not in visited:
                    visited.add(succ)
                    next_frontier.append(succ)
                    label = G.nodes[succ].get("label", succ)
                    rel = G.edges[nid, succ].get("relation", "?")
                    print(f"  {G.nodes[nid].get('label', nid)}  --{rel}-->  {label}")
        frontier = next_frontier

# Trace how SVC_GET_M_MOT_FIL flows through the system
trace_forward(G, "signal_svc_get_m_mot_fil", depth=4)
```

---

## Configuring for Your Project

### Step 1: Find the compiler switch names

Look at your project's `Config.xml` or build system. Switches typically look like:
```xml
<PreDefines>
  <define name="CS_MODUS_DERIVAT" value="EN_MODUS_DERIVAT_DQ250"/>
  <define name="CS_MODUS_HYBRID" value="EN_MODUS_HYBRID_AUS"/>
</PreDefines>
```

### Step 2: Identify your signal naming convention

Common patterns:
- `MODULE_GET_SIGNAL` — getter macro (most common)
- `MODULE_SET_SIGNAL` — setter macro
- `MODULE_IS_FLAG`    — boolean flag getter

Adjust the `signal_pattern` regex in `extract_c_signals.py`:
```python
signal_pattern = r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+"
# Or for multiple patterns:
signal_pattern = r"[A-Z][A-Z0-9]+_(?:GET|SET|IS|HAS)_[A-Z0-9_]+"
```

### Step 3: Point at your header files

Signals are defined in header files (`.h`). By default the extractor scans `**/*.h`.
For the VASAMM project, the outputs are in `*_OUTPUTS.c` files:
```python
result = extract_signal_flow(
    root_dir=Path("VASAMM"),
    header_glob="*_OUTPUTS.c",  # custom: outputs defined in C files here
    source_glob="**/*.c",
    predefined=predefined,
)
```

---

## Architecture Decision: Why This Approach?

**Alternative 1: Run ScanDKSW.exe and parse its CSV**
- Pro: ScanDKSW already does this exactly
- Con: Windows-only (`.exe`), requires manual CSV parsing, tight coupling to external tool

**Alternative 2: Full Python C preprocessor**
- Pro: Complete correctness, handles all edge cases
- Con: Huge engineering effort (months), replicates a compiler's preprocessor

**Chosen approach: Lightweight Python port of the key ScanDKSW logic**
- Port only what we need: the `#if/#ifdef` state machine (80 lines)
- Text-based substring matching for signal usage (same as ScanDKSW)
- No tokenisation, no full AST for C code
- Same trade-off ScanDKSW makes: pragmatic but effective

ScanDKSW's own architecture doc confirms this is intentional:
> "The tool does not use a full C parser. Instead it uses text substring matching to find define usages."

---

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Substring matching | False positives if signal name appears in comments | Low in practice; ScanDKSW has same limitation |
| No macro expansion | Can't follow aliases (A→B→signal) more than 1 hop | Add define resolution loop if needed |
| Brace-depth function detection | Misses some function boundaries | Combine with tree-sitter AST for function nodes |
| No full #include expansion | Won't follow included headers transitively | Point `header_glob` at all relevant directories |

---

*Next: [[20_how_to_modify_graphify]] — Modification guide*  
*Related: [[04_extract_ast]] — Standard AST extraction*
