# Classify — Typing Every Define

Tags: #classify #types #defines
Links: [[00_INDEX]] | [[03_data_structures]] | [[13_brute_force_search]] | [[17_outputs]]

---

## Purpose

After all defines are collected and usages found, the **Classify phase** assigns a type (`E_DEFTYPE`) to every define. This tells you *what kind of thing* a define is — a numeric constant, an alias for another define, a macro code block, a variable reference, etc.

This typing is used when writing `Defines.csv` — different types are handled differently in the output.

---

## The 8 Types

```csharp
public enum E_DEFTYPE : byte
{
    UNRESOLVED,   // not yet classified
    CONST,        // numeric constant
    DEFINE,       // alias for another define (or empty)
    MACRO,        // code block starting with {
    VARIABLE,     // references an A2L variable
    FUNCTION,     // wraps a function call
    DECLARATION,  // storage qualifier (const, static, extern)
    POINTER,      // pointer or NULL
    TOIGNORE,     // filtered (BIL_* prefix)
}
```

---

## The Classification Algorithm

The Classify block iterates over all define keys (using a snapshot `_Keys` so the dictionary can be modified safely):

```csharp
List<string> _Keys = new List<string>(D_Defines.Keys);
foreach (string key in _Keys)
{
    define p = D_Defines[key];
    string pv = p.value;
    
    // Step 1: Remove cast prefixes like "(T_U8)"
    pv = CastAway(pv, D_TypeDefs);
    
    // Step 2: Remove outer parentheses
    pv = Klammerweg(pv);
    
    // Step 3: Classify based on pv
    if (p.type == E_DEFTYPE.UNRESOLVED)
    {
        // Check in order:
        if (pv.Length == 0 || D_Defines.ContainsKey(pv))  → DEFINE
        else if (pv.StartsWith("{"))                       → MACRO
        else if (isSingleConst(pv))                        → CONST
        else if (isShiftedConst(pv))                       → CONST
        else if (D_A2L.ContainsKey(pv))                    → VARIABLE
        else if (pv.EndsWith("()") && countKlammer==1)     → FUNCTION
        else if (key.EndsWith(")") && !starts("("))        → FUNCTION
        else if (SetDeclaration.Contains(pv))              → DECLARATION
        else if (pv.StartsWith("&") || pv.ToUpper()=="NULL") → POINTER
        else if (key.StartsWith("BIL_"))                   → TOIGNORE
        // else: stays UNRESOLVED
        
        D_Defines[key] = p;
    }
}
```

---

## Rule-by-Rule Detail

### DEFINE

```csharp
if (pv.Length == 0 || D_Defines.ContainsKey(pv))
    p.type = E_DEFTYPE.DEFINE;
```

Two cases:
1. **Empty value** (`pv == ""`): `#define __freeimpl__` — a flag define with no value. It's a "pure switch".
2. **Value is another define name**: `#define ALIAS KRS_GET_N_TURB` — the value `"KRS_GET_N_TURB"` is itself a key in `D_Defines`. This is an alias.

### MACRO

```csharp
else if (pv.StartsWith("{"))
    p.type = E_DEFTYPE.MACRO;
```

Example: `#define INIT_BLOCK() { a=0; b=0; }` — value starts with `{`, making it a code block.

### CONST

```csharp
else if (isSingleConst(pv))
    p.type = E_DEFTYPE.CONST;
```

`isSingleConst()` checks if the entire value is a single numeric literal:
- Starts with digit, `+`, or `-`
- Contains only chars from `"0123456789.,abcdeflux"` (covers hex `0xAB`, floats `3.14`, unsigned `100u`)

Examples: `#define TIMEOUT 100`, `#define MASK 0xFF`, `#define FACTOR 2.5f`

**Shifted constants** (bit-shifted values):
```csharp
else if (countKlammer(pv)==0 && (pv.Contains("<<") || pv.Contains(">>")))
{
    // split at << or >>
    // both parts must be isSingleConst
    p.type = E_DEFTYPE.CONST;
}
```

Example: `#define BIT3 (1 << 3)` → after `Klammerweg` removes outer parens → `pv = "1 << 3"` → both `"1"` and `"3"` are single consts → CONST.

### VARIABLE

```csharp
else if (D_A2L.ContainsKey(pv))
    p.type = E_DEFTYPE.VARIABLE;
```

If the value exactly matches a variable name in the A2L file, it's a **variable reference**. This is how signal getter macros are identified:

```c
#define KRS_GET_N_TURB  Krs_N_Turb   // value = "Krs_N_Turb"
```

If `"Krs_N_Turb"` is in `D_A2L` (from the A2L file), then `KRS_GET_N_TURB` is classified as VARIABLE.

This is the type of most interest for the signal flow matrix — VARIABLE defines are the signal interfaces.

### FUNCTION

```csharp
else if (pv.EndsWith("()") && countKlammer(pv) == 1)
    p.type = E_DEFTYPE.FUNCTION;

else if (key.EndsWith(")") && !key.StartsWith("(") && countKlammer(key) == 1)
    p.type = E_DEFTYPE.FUNCTION;
```

Two checks:
1. **Value is a no-argument function call**: `#define GET_TIME getSystemTime()` → value ends with `()`
2. **Key has argument list**: `#define MAX(a,b) (...)` → key ends with `)`, has exactly one bracket pair

### DECLARATION

```csharp
else if (SetDeclaration.Contains(pv))
    p.type = E_DEFTYPE.DECLARATION;
```

`SetDeclaration` comes from Config.xml. For example:
```xml
<SetDeclaration>
  <Declaration Name="const"/>
  <Declaration Name="static"/>
  <Declaration Name="extern"/>
</SetDeclaration>
```

`#define KRS_CONST const` → value `"const"` is in `SetDeclaration` → DECLARATION.

### POINTER

```csharp
else if (pv.StartsWith("&") || pv.ToUpper() == "NULL" || key == "NULL")
    p.type = E_DEFTYPE.POINTER;
```

Examples: `#define MY_PTR &someGlobal`, `#define NULL ((void*)0)` (after cast stripping becomes `"0"` — but `key == "NULL"` catches it).

### TOIGNORE

```csharp
else if (key.StartsWith("BIL_"))
    p.type = E_DEFTYPE.TOIGNORE;
```

`BIL_*` defines are from the bus interface layer — platform code that's not relevant for the application-level signal flow. Hardcoded filter. The comment says this should be moved to Config.xml (`#OPL-0001`).

---

## CastAway and Klammerweg Pre-processing

Before classification, two helpers normalize the value:

**`CastAway(pv, D_TypeDefs)`**:
Removes type cast prefixes like `(T_U8)`:
```csharp
foreach (string k in a_D_TC.Keys)  // e.g. k = "T_U8"
    a_pv = a_pv.Replace("(" + k + ")", "");
return a_pv.Trim();
```
So `(T_U8)Krs_N_Turb` → `Krs_N_Turb`

**`Klammerweg(pv)`**:
Removes the **outermost matching parentheses** (repeatedly):
```csharp
while (starts with "(" AND ends with ")" AND they're matching):
    a_pv = a_pv.Substring(1, a_pv.Length - 2).Trim();
```
So `((1 << 3))` → `(1 << 3)` → `1 << 3`

---

## Define Chain Resolution

After the first pass, defines that are DEFINE-type (aliases) can be further resolved:

```csharp
int c_alt = 0;
int c_neu = 0;

// Count DEFINE-type defines
foreach (string key in _Keys)
    if (D_Defines[key].type == E_DEFTYPE.DEFINE) c_alt++;

c_neu = c_alt + 1;  // prime the loop

while (c_neu > c_alt)  // keep resolving until stable
{
    c_alt = 0; c_neu = 0;
    foreach (string key in _Keys)
    {
        define p = D_Defines[key];
        if (p.type == E_DEFTYPE.DEFINE)
        {
            c_alt++;
            string pv = CastAway(p.value, D_TypeDefs);
            pv = Klammerweg(pv);
            
            if (D_Defines.ContainsKey(pv) && pv != "")
            {
                // Follow the chain: copy the type from the target define
                E_DEFTYPE chainType = D_Defines[pv].type;
                if (chainType != E_DEFTYPE.UNRESOLVED && chainType != E_DEFTYPE.DEFINE)
                {
                    p.type = chainType;  // adopt the final type
                    D_Defines[key] = p;
                }
                else
                    c_neu++;  // still unresolved, count it
            }
        }
    }
}
```

This iterates until no more DEFINE-type aliases can be resolved to their final type. For example:
```
A → DEFINE (value="B")
B → CONST  (value="42")
```
After one iteration: A inherits CONST from B.

---

*Next: [[15_a2l_scanner]]*
