# Overview — What ScanDKSW Is and Why It Exists

Tags: #overview #concept
Links: [[00_INDEX]] | [[02_architecture]]

---

## The Problem It Solves

You have an automotive embedded C codebase for a VW dual-clutch gearbox (DQ series). It has:

- **Dozens of software groups** (KRS, SVC, GSI, KWA, SIC, BIL, …) — each a sub-folder with its own `.makelist`
- **Thousands of `#define` macros** — constants, signal interfaces, feature flags
- **Heavy `#ifdef` conditional compilation** — the same file compiles differently for DQ381G4 vs DQ250 vs other variants
- **Signal interfaces defined as macros**: e.g. `KRS_GET_N_TURB` is a macro that reads the turbine speed

**The question you need to answer:** *Which group produces signal X? Which groups consume it? Who feeds into whom?*

You cannot answer this by reading the code. There are too many files, too many `#ifdef` branches, and the signal interfaces are plain `#define` macros — invisible to ordinary text search.

**ScanDKSW answers this question** by:
1. Simulating the C preprocessor (evaluating `#ifdef` with the actual compiler switches)
2. Collecting every `#define`
3. Finding every place each define is used (which file, which line, which group)
4. Producing a **matrix CSV**: rows = defines, columns = software groups, cells = P (producer) or X (consumer)

---

## The Name

**Scan** = it scans the source code  
**DKSW** = *Doppelkupplungsgetriebe Software* (German: dual-clutch gearbox software)

---

## How to Run It

```bat
ScanDKSW.exe -config="C:\path\to\Config.xml" -scan="scan" -quiet
```

**Arguments** (parsed by `Arguments.cs`):

| Argument | Meaning |
|----------|---------|
| `-config=path` | Path to Config.xml — **required** |
| `-scan=scan` | Do a full scan of all source files (default) |
| `-scan=load` | Skip scanning — load from `.bin` cache files (fast re-run) |
| `-quiet` | Suppress verbose line-by-line output to console |
| `-Protokoll=path` | Optional log file path |
| `-ResetTypes` | Reset all define types to UNRESOLVED (forces re-classify on load) |

---

## What It Produces

All outputs go to the folder specified by `<DestinationFolder>` in Config.xml.

| File | Contents |
|------|---------|
| `Defines.csv` | Selected defines (filtered by type, groups, files) with type and findings |
| `DefinesAll.csv` | Every single `#define` found, no filter |
| `DefMatrix.csv` | Matrix: define × group, P/X marks |
| `Doubletten.csv` | Defines declared more than once (with different or identical values) |
| `Undefines.csv` | Every `#undef` found — what was undefined, where originally defined |
| `CANSignale.csv` | CAN signal names traced through BIL_IDX_SIG defines |
| `A2L.csv` | All MEASUREMENT variables from the A2L calibration file |
| `VTAB.csv` | COMPU_VTAB tables (enum-style value descriptions) |
| `METHOD.csv` | COMPU_METHOD entries (scaling formulas) |
| `TypeDefs.csv` | All `typedef` aliases found during the scan |
| `Comments.csv` | Free text comments loaded from external comment files |
| `.bins/` | Binary-serialised dictionaries (for `-scan=load` fast re-run) |

---

## The VASAMM Sample Files

In the `VASAMM/` folder you'll find three real-looking C files from the KRS module:

**`KRS_OUTPUTS.c`** — defines what KRS produces for other groups:
```c
#define KRS_GET_N_TURB        Krs_N_Turb          // turbine speed
#define KRS_GET_ZUST_KRS      Krs_Zustand_e        // KRS state
```
These `KRS_GET_*` macros are the signal interfaces. Other groups read them.

**`KRS_INPUTS.c`** — lists signals KRS reads from other groups:
```c
// SVC_GET_FP — accelerator pedal
// GSI_GET_N_AB — output speed
```
(These are just comments/documentation — no defines here, the actual defines come from other groups' OUTPUTS files.)

**`KRS_DIENSTE.c`** — service/utility defines KRS exports.

These files demonstrate the naming convention: `{MODULE}_GET_{SIGNAL}` = getter macro produced by MODULE, consumed by others.

---

## What ScanDKSW Does NOT Do

- It does NOT produce a graphical diagram (that's graphify's job)
- It does NOT parse C syntax or function bodies (no AST for C code)
- It does NOT understand structs, function calls, or pointer arithmetic
- The AST pipeline (`Lexer → Parser → Compiler → Calculator`) is **only for evaluating `#if` expressions**, not for understanding the C code itself
- It does NOT parse the H files for type information (except `typedef` aliases)

---

## Relationship to graphify

ScanDKSW was built standalone. Graphify is a separate Python tool for codebase knowledge graphs. Our integration:

- Python port of the `#if` state machine → `CPreprocessor` class in `extract_c_signals.py`
- Python port of the brute-force define search → `scan_files_for_usages()`
- Python port of `extractClust()` → `_group_from_filename()`
- The full pipeline is orchestrated by `scandksw_runner.py`
- Output goes into `graphify-out/scandksw/` instead of `ScanDKSW/`

See [[19_graphify_integration]] for details.

---

*Next: [[02_architecture]]*
