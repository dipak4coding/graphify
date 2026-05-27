# Data Structures — Every Struct and Enum

Tags: #data #structs
Links: [[00_INDEX]] | [[02_architecture]] | [[06_main_scan_loop]]

---

## Overview

All data structures are defined at the top of `Program.cs`, before the `Program` class. They are the vocabulary for everything else.

---

## `position` — Where Something Is

```csharp
public struct position
{
    public string file { get; }   // filename only (no path), e.g. "krs_app.c"
    public int line { get; }       // line number in that file, 1-based

    public override string ToString() => $"-{file}-{line}-";
}
```

**Purpose**: records exactly where a `#define` was declared, or where it was used.

**Appears in**:
- `define.pos` — where the define was declared
- `define.findings` — list of positions where the define was found used
- `undefine.oldpos` — where the define was originally declared
- `undefine.undpos` — where the `#undef` appeared

**Tagged line format**: `CFile2List()` produces lines like `"krs_app.c-42-#define KRS_GET_N_TURB Krs_N_Turb"`. The position is extracted by splitting on `-`:
```csharp
int minus = line.IndexOf('-');
string datei = line.Substring(0, minus);           // "krs_app.c"
line = line.Substring(minus + 1);
minus = line.IndexOf('-');
string zeile = line.Substring(0, minus);           // "42"
line = line.Substring(minus + 1);                  // "#define KRS_GET_N_TURB Krs_N_Turb"
position p = new position(datei, Convert.ToInt32(zeile));
```

---

## `define` — The Central Record

```csharp
[Serializable]
public struct define
{
    public string name { get; }              // e.g. "KRS_GET_N_TURB"
    public string value { get; }             // e.g. "Krs_N_Turb"
    public position pos { get; }             // where it was declared
    public E_DEFTYPE type { get; set; }      // classified type (starts as UNRESOLVED)
    public List<position> findings { get; set; }  // everywhere it was found used
    public List<string> matrix { get; set; }     // which groups use it
}
```

**The `[Serializable]` attribute** means this struct (and everything inside it) can be written to a `.bin` file using `BinaryFormatter` for the `-scan=load` cache mechanism.

**`name` vs `value`**: For `#define KRS_GET_N_TURB Krs_N_Turb`:
- `name` = `"KRS_GET_N_TURB"` — the macro identifier, used as the dictionary key
- `value` = `"Krs_N_Turb"` — what the macro expands to

For a bare define with no value like `#define __freeimpl__`:
- `value` = `""` (empty string)

**`findings`**: Every time a line (not a preprocessor directive) contains the define name as a substring, a `position` is added here. This is how the brute-force usage scan works — see [[13_brute_force_search]].

**Macro defines** (with arguments): e.g. `#define KRS_GET_N_TURB(x) ((x) * 2)`:
- `name` = `"KRS_GET_N_TURB(x)"` — includes the argument list up to the closing `)`
- `value` = `"((x) * 2)"`

The split point detection:
```csharp
int brace = line.IndexOf("(");
int space = line.IndexOf(" ");
int split = space;

// if "(" appears before " " → it's a macro with args → take up to ")"
if ((brace >= 0) && (((space >= 0) && (brace < space)) || (space < 0)))
    split = line.IndexOf(")") + 1;
```

---

## `E_DEFTYPE` — Define Type Classification

```csharp
public enum E_DEFTYPE : byte
{
    UNRESOLVED,   // not yet classified (default)
    CONST,        // numeric constant: #define TIMEOUT 100
    DEFINE,       // alias for another define: #define ALIAS OTHER_DEFINE
    MACRO,        // code block: #define INIT() { a=0; b=0; }
    VARIABLE,     // maps to an A2L variable: #define KRS_N_TURB Krs_N_Turb
    FUNCTION,     // wraps a function call: #define GET_VAL() getVal()
    DECLARATION,  // storage qualifier: #define STATIC static
    POINTER,      // pointer/NULL: #define MY_PTR &someVar
    TOIGNORE,     // filtered out: BIL_* defines
}
```

Each define starts as `UNRESOLVED` and gets classified in the **Classify phase** — see [[14_classify]].

---

## `undefine` — Tracking `#undef`

```csharp
[Serializable]
public struct undefine : IComparable<undefine>
{
    public string name { get; }      // which define was un-defined
    public position oldpos { get; }  // where it was originally defined
    public position undpos { get; }  // where the #undef appeared
}
```

When `#undef NAME` is encountered:
1. The define is removed from `D_Defines`
2. An `undefine` record is added to `L_Undefines`

This preserves the audit trail: you can see both where something was defined and where it was later un-defined.

---

## `cansignal` — A CAN Signal Record

```csharp
[Serializable]
public struct cansignal
{
    public string SIC_Define { get; }   // the SIC define name (key in D_Defines)
    public string SignalName { get; }   // CAN signal name (from comment in C file)
    public string Botschaft { get; }    // CAN message name (from BIL_IDX_MSG_* in C file)
}
```

Used in `D_CANSignale` (key = SIC define name). Built by the CAN scanner phase — see [[16_can_scanner]].

---

## A2L Structures

These are **not serialisable** (no `[Serializable]` attribute) — they are re-loaded from the A2L file every run.

### `MEASUREMENT`

```csharp
public struct MEASUREMENT
{
    public string description { get; }    // human-readable description from A2L
    public string A2LName { get; set; }   // canonical name used in A2L (may differ from key)
    public string VarType { get; set; }   // C type: SWORD, UBYTE, etc.
    public string BitMask { get; set; }   // for bit-field variables
    public string Compu_Method { get; set; }  // name of scaling formula
    public double MinValue { get; set; }
    public double MaxValue { get; set; }
}
```

### `COMPU_METHOD`

```csharp
public struct COMPU_METHOD
{
    public string description { get; }
    public string unit { get; set; }       // physical unit, e.g. "rpm"
    public E_CM_TYPE type { get; set; }    // IDENTICAL, LINEAR, TAB_VERB, UNKNOWN
    public double factor { get; set; }     // for LINEAR: y = factor*x + offset
    public double offset { get; set; }
    public string compu_vtab { get; set; } // for TAB_VERB: key into D_COMPU_VTAB
}
```

### `COMPU_VTAB`

```csharp
public struct COMPU_VTAB
{
    public string description { get; }
    public Dictionary<string, string> content { get; }  // integer → text description
}
```

Example content: `{ "0" → "Einlegen", "1" → "Befullen", "2" → "Warten-Synchron", ... }` — this is the verbal description table for an enum-like measurement.

### `E_CM_TYPE`

```csharp
public enum E_CM_TYPE : byte
{
    IDENTICAL,  // value passed through unchanged, just with a unit
    LINEAR,     // y = factor * x + offset
    TAB_VERB,   // lookup table: integer → text
    UNKNOWN,    // not yet determined
}
```

---

## Key Collections Summary

| Variable | Type | Purpose |
|----------|------|---------|
| `D_Defines` | `Dictionary<string, define>` | All active `#define`s, the central store |
| `D_Doubletten` | `Dictionary<string, define>` | Duplicate defines (same name, different value) |
| `L_Undefines` | `List<undefine>` | All `#undef` records |
| `D_CANSignale` | `Dictionary<string, cansignal>` | CAN signals keyed by SIC define name |
| `D_A2L` | `Dictionary<string, MEASUREMENT>` | All A2L measurement variables |
| `D_COMPU_VTAB` | `Dictionary<string, COMPU_VTAB>` | Verbal tables from A2L |
| `D_COMPU_METHOD` | `Dictionary<string, COMPU_METHOD>` | Scaling methods from A2L |
| `D_TypeDefs` | `Dictionary<string, string>` | `typedef` aliases: new_name → base_type |
| `D_Comments` | `Dictionary<string, string>` | Free comments from external files |
| `IgnDoubletten` | `List<string>` | Define names where duplicate values are expected and ignored |
| `IgnGroups` | `List<string>` | Groups to exclude from output (e.g. "bios", "version") |
| `IgnFiles` | `List<string>` | Files to exclude from output |
| `IgnDefines` | `List<string>` | Define names to exclude from output (header guards like `KRS_H`) |
| `SetDeclaration` | `List<string>` | Values that classify a define as DECLARATION |

---

*Next: [[04_config_xml]]*
