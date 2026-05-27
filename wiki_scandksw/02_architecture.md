# Architecture — The Full Data Flow

Tags: #architecture #dataflow
Links: [[00_INDEX]] | [[01_overview]] | [[03_data_structures]]

---

## Helicopter View

ScanDKSW is a **single-pass forward-only scanner** with a mini compiler bolted on for `#if` evaluation.

It does NOT build a full C AST. It processes lines one at a time, top to bottom. The only tree structure it builds is the temporary expression tree for evaluating `#if` conditions.

```
Config.xml  ─────────────────────────────────►  paths, switches, ignore lists
                │
                ▼
        A2L file scanner  ───────────────────►  D_A2L, D_COMPU_METHOD, D_COMPU_VTAB
                │
                ▼
        Group list (components.makelist)
                │
        ┌───────▼──────────────────────────────┐
        │  FOR EACH GROUP                       │
        │    FOR EACH C-FILE                    │
        │      CFile2List()  ─── tagged listing │
        │        │                              │
        │        ▼                              │
        │      FOR EACH LINE                    │
        │        #define  ──►  D_Defines        │
        │        #undef   ──►  L_Undefines      │
        │        #include ──►  inline H file    │
        │        #if*     ──►  IfNesting push   │
        │        #elif    ──►  IfNesting flip   │
        │        #else    ──►  IfNesting toggle │
        │        #endif   ──►  IfNesting pop    │
        │        else     ──►  brute-force      │
        │                       search findings │
        └──────────────────────────────────────┘
                │
                ▼
        CAN scanner (sic.c path)  ──────────►  D_CANSignale
                │
                ▼
        Classify define types  ──────────────►  E_DEFTYPE on each define
                │
                ▼
        CSV writers  ──────────────────────►  Defines.csv, DefMatrix.csv, ...
```

---

## The Central Data Structure: `D_Defines`

Everything revolves around this one dictionary:

```csharp
Dictionary<string, define> D_Defines
```

- **Key**: the define name (e.g. `"KRS_GET_N_TURB"`)
- **Value**: a `define` struct containing:
  - `name` — the macro name
  - `value` — what it expands to (e.g. `"Krs_N_Turb"`)
  - `pos` — where it was defined (`file`, `line`)
  - `type` — classified type (set in Classify phase)
  - `findings` — list of `position` records where this define was found (used)
  - `matrix` — list of group names that use it

At the start, `D_Defines` is pre-populated from Config.xml's `<PreDefines>` section (compiler switches like `__freeimpl__`). During scanning, every `#define` encountered while `parsen=true` is added.

---

## The Three Phases

### Phase 1: Scan (`-scan=scan`)

The scanner walks:
1. The top-level `components.{variant}.makelist` → list of group paths (`krs\krs`, `svc\svc`, …)
2. Each group's `{group}.makelist` → list of `.c` files
3. Each `.c` file → `CFile2List()` → tagged listing
4. Each line in the listing → dispatch to the right handler

During this phase:
- `D_Defines` is built (all `#define` found while `parsen=true`)
- `L_Undefines` is built
- `D_TypeDefs` is built (`typedef` aliases)
- `findings` on each define are populated (brute-force search)
- `D_CANSignale` is built (CAN phase runs after the main scan)

At the end, all dictionaries are serialised to `.bin` files in `.bins/`.

### Phase 2: Load (`-scan=load`)

Deserialises from `.bin` files. Skips the file scanning entirely. Used for fast re-runs when the sources haven't changed but you want to re-classify or re-output.

### Phase 3: Classify + Output

Always runs (both scan and load mode).

1. **Classify**: Assigns `E_DEFTYPE` to every define (CONST, DEFINE, MACRO, FUNCTION, etc.)
2. **Output**: Writes all CSV files

---

## Partial Class Split

`Program` is declared as `partial class` — split across two files:

| File | Contains |
|------|---------|
| `Program.cs` | `Main()`, all structs, enums, and member variables |
| `Tools.cs` | `readConfig()`, `CFile2List()`, `scanA2L()`, `parseIfs()`, `extractMaker()`, all helper functions |

This is just a code organisation decision — there is no functional difference. At compile time they merge into one class.

---

## Thread Safety

ScanDKSW is **single-threaded**. There is no parallelism. One file at a time, one line at a time, one define lookup at a time. This is by design — the forward-only inline expansion of `#include` files would be complex to parallelise correctly.

---

## Error Handling Strategy

Exceptions are mostly caught at the group level:

```csharp
try { 
    // process entire group + its files 
} catch (Exception e) {
    myWriteLine("Fehler bei den Gruppen aufgetreten: " + e.Message);
    Console.ReadKey();
    return;  // abort entire program
}
```

This means: if one group fails (e.g. a file is missing), the entire scan stops. There is no skip-and-continue. The tool is strict because it's meant for a known, stable codebase.

---

## The `#if` Sub-Pipeline

For every `#if` / `#elif` expression, a mini compiler runs:

```
Raw string (e.g. "#if CS_MODUS_DERIVAT == EN_MODUS_DERIVAT_DQ381G4")
    │
    ▼
Lexer     → Token list  (numbers, operators, identifiers, defined(), ...)
    │       [also substitutes define values inline]
    ▼
Optimizer → Cleaned token list  (remove double negations, redundant +, etc.)
    │
    ▼
Parser    → Expression tree  (binaer, unaer, ternaer, function nodes)
    │
    ▼
Compiler  → HPN list  (Reverse Polish Notation)
    │
    ▼
Calculator → true or false  (stack machine evaluation)
```

The result (true/false) decides whether a new byte `0x03` (active) or `0x00` (inactive) is pushed onto `IfNesting`. See [[07_ifdef_state_machine]] for the byte-flag details.

---

## Memory Model

All data lives in static class-level variables:

```csharp
private static Dictionary<string, define> D_Defines;
private static Dictionary<string, define> D_Doubletten;
private static List<undefine> L_Undefines;
private static Dictionary<string, cansignal> D_CANSignale;
private static Dictionary<string, MEASUREMENT> D_A2L;
private static Dictionary<string, COMPU_VTAB> D_COMPU_VTAB;
private static Dictionary<string, COMPU_METHOD> D_COMPU_METHOD;
private static Dictionary<string, string> D_TypeDefs;
private static Dictionary<string, string> D_Comments;
```

There are also ignore lists (`IgnDoubletten`, `IgnGroups`, `IgnFiles`, `IgnDefines`, `SetDeclaration`) loaded from Config.xml and used during filtering in the output phase.

---

*Next: [[03_data_structures]]*
