# The `#ifdef` State Machine — Dead Code Elimination

Tags: #ifdef #statemachine #deadcode
Links: [[00_INDEX]] | [[06_main_scan_loop]] | [[08_ast_lexer]]

---

## The Problem: Dead Code

C source files contain large sections wrapped in `#ifdef`:

```c
#ifdef CS_MODUS_DERIVAT_DQ381G4
  #define KRS_GET_GANG_SOLL Krs_Gang_Soll_e
#else
  #define KRS_GET_GANG_SOLL Krs_Gang_Soll_DQ250_e
#endif
```

If the project is compiled for DQ381G4, the first define is active and the second is dead code. ScanDKSW must know which defines are actually in effect for the selected variant — otherwise you'd collect both defines, get a duplicate error, and have wrong findings.

**Solution**: Track the nesting of `#if`/`#ifdef`/`#else`/`#elif`/`#endif` directives using a stack of bytes, where each byte encodes whether the current nesting level is active.

---

## The Byte Flag Encoding

Each byte in the `IfNesting` list represents one level of nesting.

```
Hex: Bit 1 0 | Meaning
0x00:     0 0 | was never active — dead code branch
0x02:     1 0 | was once active, now inactive (else branch after active if)
0x03:     1 1 | currently active — live code
```

**Bit 0** = "currently active"  
**Bit 1** = "was ever active at this level"

The rule: **once a branch has been active, subsequent branches at the same level cannot become active** (like a real C preprocessor). This is what bit 1 encodes.

### State transitions

```
#if  → evaluates condition:
        true  → push 0x03  (now active)
        false → push 0x00  (never active)

       but only if parsen=true (we are in live outer code)
       if parsen=false → push 0x02  (was-active dummy, keeps nesting correct)

#else → toggle:
        0x00 → 0x03   (was never active → now becomes active)
        0x02 → 0x02   (was active → stays inactive)
        0x03 → 0x02   (is active → becomes inactive)
        
        Code: IfNesting[0] = (IfNesting[0] == 0x00) ? (byte)0x03 : (byte)0x02;

#elif → if result=1:
          0x00 → 0x03  (never active → now active)
          0x02 → 0x02  (already had active branch → stays off)
          0x03 → 0x02  (was active → now off, elif takes over)
        if result=0:
          0x00 → 0x00  (still never active)
          0x02 → 0x02  (stays off)
          0x03 → 0x02  (was active → now off)

        Code: IfNesting[0] = (IfNesting[0] == 0x00)
                                ? (parseIfs(...) ? (byte)0x03 : (byte)0x00)
                                : (byte)0x02;

#endif → pop IfNesting (removes one level)
```

---

## The `parsen` Flag

```csharp
bool parsen = true;
```

This is a **derived value** — computed after every line:

```csharp
byte e = 0x01;
foreach (byte i in IfNesting)
    e &= i;
parsen = (e == 0x01) ? true : false;
```

Translation: AND all the `bit0` values of every byte in the stack. If all bit0s are `1`, then we are in live code at every level → `parsen = true`. If any level's bit0 is `0`, we are in dead code → `parsen = false`.

**Initial state**:
```csharp
List<byte> IfNesting = new List<byte> { (byte)0x03 };
```

The list starts with one entry: `0x03` (the "always active" outer scope). This means at the start of scanning, `parsen = true`.

---

## Concrete Example

Source code:
```c
// CS_MODUS_DERIVAT_DQ381G4 is in PreDefines
#ifdef CS_MODUS_DERIVAT_DQ381G4   // line A
  #define SOLL_VAR Soll_DQ381    // line B
#else                              // line C
  #define SOLL_VAR Soll_Generic  // line D
#endif                             // line E
```

Step-by-step:

| Line | Action | IfNesting (head first) | parsen |
|------|--------|------------------------|--------|
| start | init | [0x03] | true |
| A `#ifdef CS_...` | CS_ is in D_Defines → true → push 0x03 | [0x03, 0x03] | true |
| B `#define SOLL_VAR Soll_DQ381` | parsen=true → add to D_Defines | [0x03, 0x03] | true |
| C `#else` | toggle: 0x03 → 0x02 | [0x02, 0x03] | **false** |
| D `#define SOLL_VAR Soll_Generic` | parsen=false → **skipped** | [0x02, 0x03] | false |
| E `#endif` | pop | [0x03] | true |

Result: only `SOLL_VAR = "Soll_DQ381"` is in `D_Defines`. The dead-code branch is eliminated.

---

## Nested `#ifdef`

```c
#ifdef FEATURE_A
  #ifdef FEATURE_B
    #define X 1
  #endif
  #define Y 2
#endif
```

With `FEATURE_A` defined but `FEATURE_B` not defined:

| Directive | Push/Pop | IfNesting | parsen |
|-----------|---------|-----------|--------|
| `#ifdef FEATURE_A` | push 0x03 | [0x03, 0x03] | true |
| `#ifdef FEATURE_B` | push 0x00 | [0x00, 0x03, 0x03] | **false** |
| `#define X 1` | — | — | false → skipped |
| `#endif` | pop | [0x03, 0x03] | true |
| `#define Y 2` | — | — | true → **collected** |
| `#endif` | pop | [0x03] | true |

Result: only `Y = 2` collected. `X` is dead code.

---

## Dead `#if` Blocks and Nesting Depth

When `parsen=false` and we encounter a new `#if`, ScanDKSW still **pushes a dummy byte** to keep the stack depth correct:

```csharp
if (parsen)
    IfNesting.Insert(0, parseIfs(l_line, D_Defines, p) ? (byte)0x03 : (byte)0x00);
else
    IfNesting.Insert(0, (byte)0x02);  // dummy — keeps depth count correct
```

Why? Because every `#if` must be paired with an `#endif`. If you skipped the push, the matching `#endif` would pop the wrong level.

---

## `parseIfs()` — Evaluating the Condition

```csharp
static bool parseIfs(string a_line, Dictionary<string, define> a_Defines, position a_p)
```

**Fast path for `#ifdef` and `#ifndef`**:
```csharp
if (a_line.StartsWith("#ifdef"))
{
    a_line = a_line.Substring(6).Trim();  // remove "#ifdef"
    _not = false;
    return !(a_Defines.ContainsKey(a_line) == _not);
    // for #ifdef: true if name IS in D_Defines
}
```

No AST pipeline needed for `#ifdef` — just a dictionary lookup.

**Full pipeline for `#if` and `#elif`**:
```csharp
a_line = Regex.Replace(a_line, @"s", "");  // remove spaces (!)
try {
    Lexer lxr = new Lexer(a_line, a_Defines);
    Optimizer optmzr = new Optimizer(lxr.Tokens);
    Parser prsr = new Parser(optmzr.Tokens);
    Compiler cmplr = new Compiler(prsr.Result);
    Calculator clcl8r = new Calculator(cmplr.Hpn);
    object _ergebnis = clcl8r.calc(a_Defines);
    if (!(_ergebnis is bool))
        throw new Exception("Berechnung gibt keine bool zurück!");
    return (bool)_ergebnis;
}
catch (Exception ex) {
    countFehler1++;   // count but continue
    // ... countFehler2-5 for specific exception types ...
    return true;  // default: assume active on parse failure
}
```

**Error handling**: If the `#if` expression can't be evaluated (e.g. uses a complex expression ScanDKSW doesn't support), the error is counted and **`true` is returned** (assume active). This is a conservative default — better to collect too many defines than to miss some.

The space removal `Regex.Replace(a_line, @"s", "")` is suspicious — it removes all `s` characters! This appears to be a bug where `@"\s"` (whitespace regex) was intended but was written as `@"s"`. In practice it hasn't caused crashes because the Lexer handles whitespace anyway.

---

## Python Port

In `extract_c_signals.py`, this is implemented as the `CPreprocessor` class:

```python
class CPreprocessor:
    def __init__(self, predefined=None):
        self._defines = dict(predefined or {})
        self._nesting = [0x03]  # outer scope always active

    @property
    def active(self) -> bool:
        e = 0x01
        for i in self._nesting:
            e &= i
        return e == 0x01

    def process(self, line: str) -> bool:
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
            # simplified: try to evaluate, default true on failure
            ...
        elif stripped.startswith("#elif"):
            self._nesting[0] = ...
        elif stripped.startswith("#else"):
            self._nesting[0] = 0x03 if self._nesting[0] == 0x00 else 0x02
        elif stripped.startswith("#endif"):
            if len(self._nesting) > 1:
                self._nesting.pop(0)
        return self.active
```

---

*Next: [[08_ast_lexer]]*
