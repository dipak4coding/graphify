# Main Scan Loop — Three Levels Deep

Tags: #scanloop #mainloop
Links: [[00_INDEX]] | [[05_cfile2list]] | [[07_ifdef_state_machine]] | [[13_brute_force_search]]

---

## Overview

The scan loop is the heart of `Main()` in `Program.cs`. It has exactly three levels:

```
Level 1: foreach GROUP  in the components makelist
  Level 2: foreach C-FILE  in the group's makelist
    Level 3: while LINE  in the tagged Listing
              dispatch to handler
```

---

## Level 1: Groups

```csharp
foreach (string gruppe in dieGruppen)
{
    try
    {
        // ... level 2 and 3 ...
    }
    catch (Exception e)
    {
        myWriteLine("Fehler bei den Gruppen aufgetreten: " + e.Message);
        Console.ReadKey();
        return;  // entire program stops
    }
}
```

`dieGruppen` is a `List<string>` loaded from the top-level `components.{variant}.makelist`. Each entry looks like `"krs\krs"` — two parts: the sub-folder name and the component name.

**Why this format?** The path `krs\krs` means:
- Folder: `{dieSourcen}\krs\krs\`
- Makelist: `{dieSourcen}\krs\krs\krs.makelist`
- Source files: `{dieSourcen}\krs\krs\src\*.c`

The split:
```csharp
Split = gruppe.Split('\\');   // ["krs", "krs"]
if (Split.Length != 2) throw new Exception("Gruppenpfad hat falsches Format !?");
string gruppenMakelist = gruppenPfad + @"\" + Split[1] + ".makelist";
// → "...\03_SwFunktion\krs\krs\krs.makelist"
```

---

## Level 2: C Files

```csharp
List<string> dieCFiles = new List<string>();
using (StreamReader sr = new StreamReader(gruppenMakelist))
{
    while ((line = sr.ReadLine()) != null)
    {
        line = line.Trim();
        if (line.EndsWith(@"\"))
            line = line.Substring(0, line.Length - 1);
        line = line.Trim();
        if (line.StartsWith("SRC_FILES"))
            line = line.Substring(9);
        line = line.Trim();
        if (line.StartsWith("="))
            line = line.Substring(1);
        line = line.Trim();

        Split = line.Split(' ');
        foreach (string s in Split)
            if (s != "") dieCFiles.Add(s);
    }
}
```

The per-group makelist format:
```makefile
SRC_FILES = \
    krs_app.c \
    krs_strat.c \
    krs_diag.c
```

The parser strips `SRC_FILES`, `=`, continuation `\`, and splits on spaces. Result: `["krs_app.c", "krs_strat.c", "krs_diag.c"]`.

**Then for each C file**:

```csharp
foreach (string cFile in dieCFiles)
{
    // Get include paths from Make.bat for this specific file
    IncludePaths = extractMaker(derMaker, cFile, derCompiler);
    if (IncludePaths == null)
        IncludePaths = new List<string>();
    IncludePaths.Add(gruppenPfad + @"/src");  // always add the group's src folder

    // Build full path
    cFilePfad = gruppenPfad + @"/src/" + cFile;

    if (!File.Exists(cFilePfad))
        throw new Exception("C-File >" + cFilePfad + "< nicht gefunden!");

    // Convert to tagged listing (comment-stripped)
    List<string> Listing = CFile2List(cFilePfad);

    // Track that this file has been read
    if (!readed.Contains(cFile))
        readed.Add(cFile);
    
    // ... Level 3 ...
}
```

**`readed` list**: Initialised once before the group loop starts. Accumulates across all files in all groups. This means: if `krs_app.c` includes `krs_types.h`, and later `svc_main.c` also includes `krs_types.h`, the second include is silently skipped. The defines from `krs_types.h` are already in `D_Defines` from the first time.

---

## Level 3: Line Dispatch

```csharp
while (Listing.Count > 0 && !Stop)
{
    line = Listing[0];
    CatchLine = line;  // keep for error reporting

    // Extract position tag
    int minus = line.IndexOf('-');
    string datei = line.Substring(0, minus);
    line = line.Substring(minus + 1);
    minus = line.IndexOf('-');
    string zeile = line.Substring(0, minus);
    line = line.Substring(minus + 1);

    position p = new position(datei, Convert.ToInt32(zeile));

    // Dispatch:
    if      (line.StartsWith("#define", ...))  → handle define
    else if (line.StartsWith("#undef", ...))   → handle undef
    else if (line.StartsWith("#include", ...)) → inline H file
    else if (line.StartsWith("#if", ...) || ...)  → push IfNesting
    else if (line.StartsWith("#else", ...))    → toggle IfNesting
    else if (line.StartsWith("#elif", ...))    → update IfNesting
    else if (line.StartsWith("#endif", ...))   → pop IfNesting
    else if (line.StartsWith("#error", ...))   → skip
    else if (line.StartsWith("typedef", ...))  → record typedef
    else                                       → brute-force search

    // After every line:
    byte e = 0x01;
    foreach (byte i in IfNesting) e &= i;
    parsen = (e == 0x01);
}
```

**Important**: The `parsen` flag is **recalculated after every line** by ANDing all the `bit0` values in `IfNesting`. This means the `parsen` state is always up-to-date before the next line is processed.

---

## Multi-Line `#define` Handling

C allows `#define` to span multiple lines using a backslash continuation:

```c
#define LONG_MACRO(a, b) \
    ((a) > (b) ? (a) : (b))
```

ScanDKSW handles this with a consume loop:

```csharp
string l_line = line.TrimEnd('\\');
while (line.EndsWith("\\"))
{
    Listing.RemoveAt(0);   // consume the continuation line
    line = Listing[0];
    // strip the position tag from the continuation line
    int minus_ = line.IndexOf('-');
    line = line.Substring(minus_ + 1);
    minus_ = line.IndexOf('-');
    line = line.Substring(minus_ + 1);

    l_line += line.TrimEnd('\\');  // accumulate
}
```

This collapses the multi-line define into a single string before parsing it.

---

## `#define` Parsing Detail

After stripping `"#define"` and trimming:

```csharp
line = l_line.Substring(7);   // remove "#define"
line = line.Trim();

int brace = line.IndexOf("(");
int space = line.IndexOf(" ");
int split = space;

// Macro with args: "#define MAX(a,b) ..."  → brace comes before space
if ((brace >= 0) && (((space >= 0) && (brace < space)) || (space < 0)))
    split = line.IndexOf(")") + 1;

if (split >= 0) {
    d = line.Substring(0, split);       // name (e.g. "MAX(a,b)")
    c = line.Substring(split).Trim();   // value (e.g. "((a)>(b)?(a):(b))")
} else {
    d = line;   // name only
    c = "";     // no value
}
```

**Duplicate detection**:
```csharp
if (D_Defines.ContainsKey(d))
{
    if (D_Defines[d].value != c)  // same name, different value → Doublette
    {
        if (IgnDoubletten.Contains(d))
            myWriteLine("doppelte Definition ignoriert");
        else
            throw new Exception("Define >" + d + "< ist doppelt definiert!");
    }
    else
        countMehrfach++;  // same name, same value → just a duplicate include
    
    // Add to D_Doubletten with suffix: "NAME<0>O" (original), "NAME<1>D/M"
}
else
{
    D_Defines.Add(d, l_def);
}
```

The suffix encoding for `D_Doubletten`:
- `<0>O` — original definition
- `<N>D` — duplicate with same value (D = Dopplung/duplicate)
- `<N>M` — duplicate with different value (M = Mehrfach/multiple)

---

## `typedef` Handling

```csharp
else if (line.StartsWith("typedef", true, null))
{
    if (parsen)
    {
        // Skip complex typedefs: struct, const struct, union, enum
        if (!line.StartsWith("typedef struct", ...)
            && !line.StartsWith("typedef union", ...)
            && !line.StartsWith("typedef enum", ...))
        {
            string l_line = line.TrimEnd(';');
            Split = l_line.Split(' ');
            string k = Split[Split.Length - 1];  // last token = new name
            if (!k.EndsWith(")"))  // skip function pointer typedefs
            {
                string v = Split[1];  // base type
                for (int i = 2; i < Split.Length - 1; i++)
                    v += " " + Split[i];
                if (!D_TypeDefs.ContainsKey(k))
                    D_TypeDefs.Add(k, v);
            }
        }
    }
}
```

Examples:
- `typedef unsigned char T_U8;` → `D_TypeDefs["T_U8"] = "unsigned char"`
- `typedef T_U8 (*T_FUNC)(void);` → skipped (ends with `)`)
- `typedef struct { ... } MyStruct;` → skipped (struct typedef)

---

## The `Stop` Flag

```csharp
bool Stop = false;
```

This is a debug mechanism. You can set `Stop = true` in code to pause processing after the first file or first group. It's never set by any logic in the current code — it's a leftover debugging aid.

---

*Next: [[07_ifdef_state_machine]]*
