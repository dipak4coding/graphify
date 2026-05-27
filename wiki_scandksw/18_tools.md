# Tools — Utility Functions

Tags: #tools #utilities #helpers
Links: [[00_INDEX]] | [[05_cfile2list]] | [[14_classify]]

---

## Overview

`Tools.cs` is the second half of the `partial class Program`. It contains all the helper functions. Some are large (like `scanA2L()`), some are tiny one-purpose utilities. This article covers the utility helpers not already covered in other articles.

---

## `extractClust()` — Group Name from Filename

```csharp
static string extractClust(string a_file)
{
    // Remove extension
    int i = a_file.IndexOf('.');
    if (i >= 0) a_file = a_file.Substring(0, i);
    
    // Take only up to first underscore
    i = a_file.IndexOf('_');
    if (i >= 0) a_file = a_file.Substring(0, i);
    
    return a_file.ToLower();
}
```

**Examples**:
| Input | Output |
|-------|--------|
| `KRS_OUTPUTS.c` | `krs` |
| `svc_main.c` | `svc` |
| `gsi_diag.c` | `gsi` |
| `krs_app.h` | `krs` |
| `bil_idx_sig.h` | `bil` |

**Purpose**: Maps every filename to its software group name. This is how ScanDKSW determines which group a define belongs to (producer) and which groups use it (consumers). The convention is that all files in a group start with the group prefix followed by `_`.

**Python equivalent** (`_group_from_filename()` in `extract_c_signals.py`):
```python
def _group_from_filename(filename: str) -> str:
    stem = Path(filename).stem   # strip extension
    i = stem.find('_')
    if i > 0:
        return stem[:i].lower()
    return stem.lower()
```

---

## `CastAway()` — Remove Type Cast Prefixes

```csharp
static string CastAway(string a_pv, Dictionary<string, string> a_D_TC)
{
    foreach (string k in a_D_TC.Keys)
        a_pv = a_pv.Replace("(" + k + ")", "");
    return a_pv.Trim();
}
```

Removes type cast expressions from define values. `D_TypeDefs` provides the list of known type names.

**Examples**:
| Input | After CastAway |
|-------|---------------|
| `(T_U8)Krs_N_Turb` | `Krs_N_Turb` |
| `(T_U16)42` | `42` |
| `(void*)0` | `0` (if `void*` is a typedef) |
| `3.14` | `3.14` (unchanged, no cast) |

---

## `Klammerweg()` — Remove Outer Parentheses

```csharp
private static string Klammerweg(string a_pv)
{
    int a_pv_l = a_pv.Length + 1;
    
    while (a_pv_l != a_pv.Length)  // repeat until no change
    {
        a_pv_l = a_pv.Length;
        if (a_pv.StartsWith("(") && a_pv.EndsWith(")"))
        {
            // Check if the outer ( and ) are PAIRED
            // i.e., the first ( is closed by the last )
            int count_Ebene = 1;
            int i = 1;
            for (; i < a_pv.Length; i++)
            {
                if (a_pv[i] == '(') count_Ebene++;
                else if (a_pv[i] == ')')
                {
                    count_Ebene--;
                    if (count_Ebene <= 0 && i < a_pv.Length - 1)
                        break;  // first ( closes before the last ) → not paired
                }
            }
            if (i == a_pv.Length)  // loop completed → outer parens are paired
                a_pv = a_pv.Substring(1, a_pv.Length - 2).Trim();
        }
    }
    return a_pv;
}
```

**Why the complexity?** Simple `.TrimStart('(').TrimEnd(')')` would incorrectly strip `(A) + (B)` to `A) + (B`. The loop checks that the opening `(` is closed by the closing `)`, not by an earlier `)`.

**Examples**:
| Input | Output |
|-------|--------|
| `(42)` | `42` |
| `((1 << 3))` | `1 << 3` |
| `(A) + (B)` | `(A) + (B)` (outer parens not paired) |
| `(void*)0` | `(void*)0` (after CastAway removes the cast, but standalone parens remain) |

---

## `countKlammer()` — Count Opening Parentheses

```csharp
static int countKlammer(string a_s)
{
    int count_ka = 0;
    foreach (char c in a_s)
        if (c == '(') count_ka++;
    return count_ka;
}
```

Simple: counts `(` characters. Used in Classify to determine if a define is a function (`countKlammer == 1`).

---

## `isSingleConst()` — Is This a Single Numeric Literal?

```csharp
static bool isSingleConst(string a_SC)
{
    bool retur = false;
    if (char.IsDigit(a_SC[0]) || a_SC[0] == '+' || a_SC[0] == '-')
    {
        string strCompare = "0123456789.,abcdeflux";
        int i = 1;
        for (; i < a_SC.Length; i++)
            if (!strCompare.Contains(a_SC[i].ToString().ToLower()))
                break;
        retur = (i >= a_SC.Length);  // all characters valid?
    }
    return retur;
}
```

The character set `"0123456789.,abcdeflux"` covers:
- Decimal digits: `0-9`
- Hex digits: `a-f` (already in above) plus implicit `A-F` via `.ToLower()`
- Float markers: `.` (decimal point), `,` (German decimal comma)
- Hex prefix: `x` (for `0x`)
- Unsigned suffix: `u`
- Long suffix: `l`

**Examples**:
| Input | Result |
|-------|--------|
| `100` | true |
| `0xFF` | true |
| `3.14f` | true |
| `100u` | true |
| `Krs_N_Turb` | false (K is not in charSet) |
| `1 << 3` | false (space not in charSet) |

---

## `extractMaker()` — Include Paths from Make.bat

```csharp
static List<string> extractMaker(string a_derMaker, string a_cfile, string a_ctc)
```

Reads `Make.bat` to find the compiler invocation for a specific C file, then extracts the `--include-directory=` paths.

**Algorithm**:
1. Search for: `echo ***** Kompiliere und assembliere {cfile}`
2. Find the next compiler line starting with `{ctc}` (the compiler executable path)
3. Extract the `--include-directory=path1,path2,...` value
4. Split on `,` and return list of paths

This is the only way to get the **platform-level include paths** — paths like `C:\TASKING\include` that are passed via the compiler command line but not mentioned in any source file's `#include`.

If the file isn't found in Make.bat (no compiler entry for it), returns `null` and the group's own `/src` folder is used as fallback.

---

## `myPathConcat()` — Path Concatenation

```csharp
static string myPathConcat(string Directory, string Path)
{
    if (!Path.Contains(":"))  // not absolute?
        if (Path.StartsWith("\\") || Path.StartsWith("/"))
            Path = Directory + Path;
        else
            Path = Directory + "\\" + Path;
    return Path;
}
```

Three cases:
1. `Path` contains `:` → absolute path → return as-is
2. `Path` starts with `\` or `/` → join directly (no extra separator)
3. `Path` is relative → join with `\\`

---

## `SearchPath()` — Recursive File Search

```csharp
static string SearchPath(string a_Directory, string a_filename)
{
    // Check files in current directory
    string[] fileEntries = Directory.GetFiles(a_Directory);
    foreach (string fileName in fileEntries)
        if (fileName.EndsWith(a_filename)) return fileName;
    
    // Recurse into subdirectories
    string[] subdirectoryEntries = Directory.GetDirectories(a_Directory);
    foreach (string subdirectory in subdirectoryEntries)
    {
        string r = SearchPath(subdirectory, a_filename);
        if (r != "") return r;
    }
    return "";
}
```

Used in the CAN scanner to find `.c` files by name without knowing their exact location. Searches the entire source tree.

---

## `patchIndex()` — A2L Index Notation Conversion

Converts A2L dot-underscore index notation to C bracket notation:

```csharp
// "._0_" → "[" + digits + "]"
// "_._" → "]["  (separate indices)
// "_" at end → "]"
```

Examples:
- `"KrsTMMR._0_"` → `"KrsTMMR[0]"`
- `"Arr._0_._1_"` → `"Arr[0][1]"`

Used when reading `LINK_MAP` entries in the A2L scanner.

---

## `killSpecialCharacters()` and `killCharacters()`

```csharp
static string killSpecialCharacters(string _s)
{
    // Remove backslash-escaped character sequences
    // "ab\nc" → "abc"  (removes \ and the following char)
    for (int i = _s.IndexOf("\\"); i >= 0; i = _s.IndexOf("\\"))
    {
        string e = _s.Substring(0, i) + _s.Substring(i + 2);
        _s = e;
    }
    return _s;
}

static string killCharacters(string _s, string _c)
{
    // Remove all occurrences of a specific character
    for (int i = _s.IndexOf(_c); i >= 0; i = _s.IndexOf(_c))
    {
        string e = _s.Substring(0, i) + _s.Substring(i + 1);
        _s = e;
    }
    return _s;
}
```

`killSpecialCharacters` removes `\x` escape sequences from A2L strings (Latin-1 encoded text with backslash escapes). `killCharacters` is a general single-character removal — appears to be a helper but not extensively used in the current codebase.

---

## Console Output Helpers

Four variants of console output functions:

| Function | Console | Protocol file | Quiet mode |
|----------|---------|---------------|------------|
| `myWrite(s)` | always | always | always |
| `myWriteLine(s)` | always | always | always |
| `myWriteQuiet(s)` | only if !quiet | always | suppressed |
| `myWriteLineQuiet(s)` | only if !quiet | always | suppressed |

The "Quiet" variants suppress verbose progress output when `-quiet` is passed on the command line. But they always write to the protocol file (if one is configured) — so you get a full log even with `-quiet`.

**TextBuf buffering**: If the protocol file isn't yet open (hasn't been created), output is buffered in `TextBuf` and flushed when the file becomes available. This ensures even the early startup messages end up in the protocol file.

---

*Next: [[19_graphify_integration]]*
