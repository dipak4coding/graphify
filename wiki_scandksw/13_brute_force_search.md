# Brute-Force Search — Finding Define Usages

Tags: #search #findings #matrix
Links: [[00_INDEX]] | [[06_main_scan_loop]] | [[14_classify]]

---

## The Core Idea

After collecting all `#define`s, ScanDKSW needs to find where each define is **used** — which files, which lines, which software group.

The approach is deliberately simple: **check every non-preprocessor line against every known define name as a substring**.

```csharp
else   // not #define, #undef, #include, #if, etc.
{
    if (parsen)
    {
        foreach (string def in D_Defines.Keys)
        {
            if (line.Contains(def))
            {
                D_Defines[def].findings.Add(p);
            }
        }
    }
    Listing.RemoveAt(0);
}
```

That's the entire search logic. Two nested loops: outer = lines, inner = all define names.

---

## Why Brute Force?

An alternative would be: tokenise the C code, parse into an AST, walk the AST for identifier references. That would be:
- More precise (no false positives from substring matches)
- Much more complex to implement (full C parser needed)
- Slower for the initial implementation

The substring approach has one class of false positive: if a define name is a substring of another identifier, you get a false hit. For example, if `KRS` is a define and `KRS_APP` is a function call — `line.Contains("KRS")` would hit both.

In practice for this codebase, the define names are long enough and distinctive enough (e.g. `KRS_GET_N_TURB`) that false positives are rare and acceptable.

---

## What Gets Recorded

When a define name is found in a line:

```csharp
D_Defines[def].findings.Add(p);
```

`p` is the `position` struct containing:
- `file` — the filename (e.g. `"svc_main.c"`)
- `line` — the line number

This is added to the `findings` list on the define's entry in `D_Defines`.

At the end, `findings` tells you: *"Define X was used on these lines in these files."*

---

## The `matrix` Field

After scanning, the `matrix` field on each define is built from `findings`:

```csharp
// (conceptual — actual matrix building happens in the output phase)
foreach (position p in define.findings)
{
    string group = extractClust(p.file);  // e.g. "krs_app.c" → "krs"
    if (!define.matrix.Contains(group))
        define.matrix.Add(group);
}
```

`extractClust()` extracts the module prefix:
- `"svc_main.c"` → `"svc"`
- `"krs_app.c"` → `"krs"`
- `"gsi_diag.c"` → `"gsi"`

See [[18_tools]] for `extractClust()` implementation.

---

## The Producer-Consumer Relationship

A define's **producer** is the group whose file declares the `#define`. This comes from `define.pos.file`:
```
define.pos.file = "krs_app.c" → producer group = "krs"
```

The define's **consumers** are all groups that appear in `define.findings`:
```
findings contain "svc_main.c" and "gsi_diag.c"
→ consumer groups = "svc", "gsi"
```

This producer-consumer relationship is what the **matrix CSV** shows:

```
Signal           | krs | svc | gsi | ...
KRS_GET_N_TURB   |  P  |  X  |  X  | 
KRS_GET_GANG     |  P  |     |  X  |
```

- **P** = producer (group that defined it)
- **X** = consumer (group that uses it)

---

## What Lines Are Searched

Only lines that fall in the `else` branch of the dispatcher — i.e., lines that are **not** preprocessor directives:

```csharp
if (line.StartsWith("#define")) { ... }
else if (line.StartsWith("#undef")) { ... }
else if (line.StartsWith("#include")) { ... }
else if (line.StartsWith("#if")) { ... }
// ...
else {  // <-- this is where brute-force search happens
    if (parsen)
    {
        foreach (string def in D_Defines.Keys)
            if (line.Contains(def))
                D_Defines[def].findings.Add(p);
    }
    Listing.RemoveAt(0);
}
```

So the search only runs on:
- Regular C code: function calls, variable assignments, declarations
- NOT on preprocessor lines (these are handled by dedicated branches)
- Only on lines where `parsen = true` (live code branches)

---

## Performance Consideration

For a large codebase with:
- N defines in `D_Defines` (potentially 50,000+)
- M lines of source code (potentially 100,000+)

This is O(N × M) operations. The `string.Contains()` call is fast in C# (optimised to use CPU string search instructions), but the nested loop is inherently expensive for large codebases.

The `-scan=load` mode exists partly because of this — once you've done the scan and saved the `.bin` files, you can reload the results without re-scanning.

---

## Forward-Only Limitation

Because ScanDKSW does a single forward pass, there's a potential ordering issue:

If a C file uses a define **before** that define's `#define` statement is encountered (in the forward scanning order), the usage is **missed**.

In practice, this never happens because:
1. `#include` files (which contain `#define` statements) are always at the **top** of C files
2. When an H file is inlined via `#include`, all its defines are added to `D_Defines` before the C file's code starts
3. Therefore, by the time the code lines are processed, all relevant defines are already known

---

*Next: [[14_classify]]*
