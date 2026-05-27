# ScanDKSW — Master Index

Tags: #index #overview
Links: all articles below

---

## What is ScanDKSW?

ScanDKSW is a **C# command-line tool** built specifically for VW/AUTOSAR automotive embedded C codebases.
Its job: scan thousands of C and H files, track every `#define`, evaluate every `#ifdef` dead-code branch, and produce a **signal interface matrix** showing which software group produces which signal and which groups consume it.

It was written because the codebase is too large and too `#ifdef`-heavy to understand by reading alone.

---

## The Big Picture — One Sentence Per Article

| Article | File(s) | One-sentence summary |
|---------|---------|----------------------|
| [[01_overview]] | — | Why ScanDKSW exists, what problem it solves, and how to run it |
| [[02_architecture]] | Program.cs (main) | The full data flow from CLI → Config → scan → CSV output |
| [[03_data_structures]] | Program.cs (structs) | Every struct and enum: `define`, `position`, `undefine`, `cansignal`, `MEASUREMENT` |
| [[04_config_xml]] | Config.xml, Tools.cs `readConfig()` | The XML configuration file — every tag, its meaning, and how it's parsed |
| [[05_cfile2list]] | Tools.cs `CFile2List()` | How a C file is read into a tagged line list with comments stripped |
| [[06_main_scan_loop]] | Program.cs `Main()` | The three-level loop: groups → C files → lines |
| [[07_ifdef_state_machine]] | Program.cs `IfNesting` | The byte-flag nesting stack that eliminates dead code |
| [[08_ast_lexer]] | AST/Lexer.cs | Tokenising a `#if` expression — define substitution happens here |
| [[09_ast_optimizer]] | AST/Optimizer.cs | Token cleanup before parsing — 7 normalisation rules |
| [[10_ast_parser]] | AST/Parser.cs | Recursive descent parser building an expression tree |
| [[11_ast_compiler]] | AST/Compiler.cs | Converting the expression tree to Reverse Polish Notation (HPN) |
| [[12_ast_calculator]] | AST/Calculator.cs | Stack machine evaluating HPN to `true`/`false` |
| [[13_brute_force_search]] | Program.cs (else branch) | How every line is checked against every define — the findings mechanism |
| [[14_classify]] | Program.cs `Classify` | Typing every define as CONST, DEFINE, MACRO, FUNCTION, VARIABLE, etc. |
| [[15_a2l_scanner]] | Tools.cs `scanA2L()` | Parsing the A2L calibration file for MEASUREMENT, COMPU_METHOD, COMPU_VTAB |
| [[16_can_scanner]] | Program.cs CAN section | Tracing CAN signal names from sic.c through BIL_IDX_SIG defines |
| [[17_outputs]] | Program.cs output sections | All CSV files, binary .bin cache files, load/scan mode |
| [[18_tools]] | Tools.cs (utilities) | Helper functions: `extractClust`, `CastAway`, `Klammerweg`, `isSingleConst`, `patchIndex` |
| [[19_graphify_integration]] | extract_c_signals.py, scandksw_runner.py | How ScanDKSW's logic was ported to Python and wired into graphify |

---

## File → Article Map

| Source file | Primary article | Secondary articles |
|-------------|----------------|-------------------|
| `Program.cs` | [[02_architecture]], [[06_main_scan_loop]] | [[03_data_structures]], [[07_ifdef_state_machine]], [[13_brute_force_search]], [[14_classify]], [[16_can_scanner]], [[17_outputs]] |
| `Tools.cs` | [[04_config_xml]], [[05_cfile2list]] | [[15_a2l_scanner]], [[18_tools]] |
| `Arguments.cs` | [[01_overview]] | — |
| `AST/Lexer.cs` | [[08_ast_lexer]] | — |
| `AST/Optimizer.cs` | [[09_ast_optimizer]] | — |
| `AST/Parser.cs` | [[10_ast_parser]] | — |
| `AST/Compiler.cs` | [[11_ast_compiler]] | — |
| `AST/Calculator.cs` | [[12_ast_calculator]] | — |
| `AST/AST.cs` | [[10_ast_parser]] | [[08_ast_lexer]], [[11_ast_compiler]] |
| `Config.xml` | [[04_config_xml]] | — |
| `VASAMM/*.c` | [[01_overview]] | [[13_brute_force_search]] |

---

## Concept Map — How the Pieces Connect

```
CLI args (-config, -quiet, -scan)
        │
        ▼
   Config.xml  ──────────────────────────────────────────────────────────┐
   readConfig()                                                           │
        │ paths, PreDefines, Ignore lists                                 │
        ▼                                                                 │
  A2L scanner                                                             │
  scanA2L()  →  D_A2L, D_COMPU_METHOD, D_COMPU_VTAB                     │
        │                                                                 │
        ▼                                                                 │
  Group list  (components.{variant}.makelist)                            │
        │                                                                 │
        ▼                                                                 │
  For each GROUP                                                          │
    For each C-FILE (from group's .makelist)                             │
      CFile2List()  →  tagged Listing  [filename-lineno-content]        │
        │                                                                 │
        ▼                                                                 │
      Line-by-line:                                                       │
        #define  ──►  D_Defines dictionary (if parsen=true)              │
        #undef   ──►  L_Undefines list                                   │
        #include ──►  inline the H file (via CFile2List) if not in readed│
        #ifdef/if──►  parseIfs() ──► Lexer→Optimizer→Parser→Compiler→   │
                                     Calculator ──► true/false           │
                         pushes byte onto IfNesting stack                │
        #else    ──►  toggle IfNesting[0]                                │
        #elif    ──►  parseIfs() → update IfNesting[0]                  │
        #endif   ──►  pop IfNesting stack                                │
        else     ──►  brute-force: line.Contains(def) for every define  │
                       adds findings to D_Defines[def].findings         │
                                                                         │
  IfNesting AND of all bit0s → parsen flag (dead-code elimination)      │
        │                                                                 │
        ▼                                                                 │
  CAN scanner  (sic.c → BIL_IDX_SIG → CAN signal names)                │
        │                                                                 │
        ▼                                                                 │
  Classify  (type every define: CONST, MACRO, FUNCTION...)              │
        │                                                                 │
        ▼                                                                 │
  CSV outputs:                                                            │
    Defines.csv       (selected defines with type)                       │
    DefinesAll.csv    (ALL defines)                                       │
    DefMatrix.csv     (producer × consumer matrix)                       │
    CANSignale.csv                                                        │
    A2L.csv / VTAB.csv / METHOD.csv                                      │
    TypeDefs.csv / Undefines.csv / Doubletten.csv                        │
```

---

## Key Terms Glossary

| Term | Meaning |
|------|---------|
| `D_Defines` | Dictionary of all `#defines` found — the central data structure |
| `IfNesting` | List of bytes tracking `#if` nesting depth for dead-code elimination |
| `parsen` | Boolean: `true` = current line is in live (non-dead) code |
| `findings` | List of `position` records where a define was used (substring found) |
| `matrix` | List of group names that use a define — derived from findings |
| `extractClust()` | Filename prefix extraction: `KRS_OUTPUTS.c` → `krs` |
| HPN | *Heck-Polnische Notation* = Reverse Polish Notation — the Calculator's format |
| A2L | ASAP2 calibration file — lists all measurable variables in the ECU |
| `readed` | List of already-processed H files — prevents re-including the same header twice |
| PreDefines | Compiler switches pre-loaded from Config.xml before scanning begins |
| Doubletten | Defines with the same name but different values — detected and flagged |

---

## Reading Order for Beginners

1. [[01_overview]] — understand the problem first
2. [[02_architecture]] — see the whole data flow
3. [[03_data_structures]] — understand the data
4. [[05_cfile2list]] — how files become lines
5. [[07_ifdef_state_machine]] — the dead-code heart of the tool
6. [[06_main_scan_loop]] — where everything runs
7. [[13_brute_force_search]] — how usages are found
8. [[08_ast_lexer]] → [[09_ast_optimizer]] → [[10_ast_parser]] → [[11_ast_compiler]] → [[12_ast_calculator]] — the `#if` expression pipeline
9. [[14_classify]] — typing the defines
10. [[17_outputs]] — what the CSVs contain
11. [[19_graphify_integration]] — the Python port

---

*Start here: [[01_overview]]*
