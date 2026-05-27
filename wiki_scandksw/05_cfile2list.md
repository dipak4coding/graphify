# CFile2List — Reading a C File into a Tagged Line List

Tags: #preprocessing #filereading
Links: [[00_INDEX]] | [[06_main_scan_loop]] | [[07_ifdef_state_machine]]

---

## The Problem

The main scan loop needs to process lines from multiple files — the original C file plus any H files inlined via `#include`. Instead of opening and closing files during the scan, ScanDKSW converts each file into a **flat list of strings** up front, then processes the list.

This makes `#include` handling easy: when an `#include` is encountered, the included file is converted to another list and **prepended** to the current list.

---

## The Tagged Line Format

Every entry in the list has the form:

```
filename-linenumber-content
```

Examples:
```
krs_app.c-1-#include "krs.h"
krs_app.c-15-#define KRS_VERSION 0x0100
krs_app.c-42-Krs_N_Turb = SVC_GET_N_TURB();
krs.h-3-#ifndef KRS_H
krs.h-4-#define KRS_H
```

The `position` is extracted in the main loop by finding the first two `-` separators:
```csharp
int minus = line.IndexOf('-');
string datei = line.Substring(0, minus);           // "krs_app.c"
line = line.Substring(minus + 1);
minus = line.IndexOf('-');
string zeile = line.Substring(0, minus);           // "42"
line = line.Substring(minus + 1);                  // the actual content
```

---

## The `CFile2List()` Function

Located in `Tools.cs`:

```csharp
static List<string> CFile2List(string a_cFilePfad)
{
    List<string> _Listing = new List<string>();
    string name = Path.GetFileName(a_cFilePfad);  // just filename, no path

    using (StreamReader sr = new StreamReader(a_cFilePfad))
    {
        int found;
        bool inKommentar = false;
        int zeile = 1;
        string line;

        while ((line = sr.ReadLine()) != null)
        {
            // ... comment stripping ...
            line = line.Trim();
            while (line.Contains("  ")) 
                line = line.Replace("  ", " ");  // collapse multiple spaces

            if (line.Length > 0)
                _Listing.Add(name + "-" + zeile + "-" + line);
            
            zeile++;
        }
    }
    return _Listing;
}
```

Key points:
- **`Path.GetFileName()`** — stores only the filename (e.g. `"krs_app.c"`), not the full path. This is used in the `position` struct.
- **Empty lines are skipped** — `if (line.Length > 0)` — reduces the list size significantly.
- **`zeile` counts all lines** including empty ones, so line numbers match the original file.
- **Double spaces are collapsed** — `"  "` → `" "` — normalises whitespace.

---

## Comment Stripping — Line by Line

This is the most complex part of `CFile2List`. Comments are stripped before the line is added to the list.

### Two types of comments

1. **Line comment**: `// comment to end of line`
2. **Block comment**: `/* comment spanning multiple lines */`

### State machine approach

```csharp
bool inKommentar = false;  // are we inside a /* ... */ block?
```

**When inside a block comment** (`inKommentar = true`):
```csharp
found = line.IndexOf(@"*/");
if (found >= 0) {          // found the closing "*/"
    line = line.Substring(found + 2);  // keep everything after "*/"
    inKommentar = false;
}
else
    line = "";             // no closing found → discard entire line
```

**When NOT inside a block comment**:

Step 1 — strip line comment:
```csharp
found = line.IndexOf(@"//");
if (found >= 0)
    line = line.Substring(0, found);  // keep only what's before "//"
```

Step 2 — strip inline block comments (loop for multiple `/* */` on one line):
```csharp
do {
    found = line.IndexOf(@"/*");
    if (found >= 0) {
        string rest = line.Substring(found + 2);
        line = line.Substring(0, found);           // everything before "/*"
        found = rest.IndexOf(@"*/");
        if (found >= 0)
            line += rest.Substring(found + 2);     // re-attach after "*/"
        else
            inKommentar = true;                    // no close → block started
    }
} while (line.Contains(@"/*"));                   // repeat if more "/*" remain
```

### Example trace

Input file:
```c
/* This is a
   block comment */
int x = 5; /* inline */ int y = 6; // line comment
```

Processing:
- Line 1: `inKommentar=false`, finds `/*`, no `*/` → `line=""`, set `inKommentar=true` → skipped (empty)
- Line 2: `inKommentar=true`, finds `*/` at pos 17 → `line=" "` (after `*/`), set `inKommentar=false` → skipped after trim (empty)
- Line 3: finds `//` first → `line = "int x = 5; /* inline */ int y = 6; "`, then finds `/*` → before: `"int x = 5; "`, rest `" inline */ int y = 6; "` → finds `*/` in rest → re-attach `" int y = 6; "` → line = `"int x = 5;  int y = 6;"` → collapse spaces → `"int x = 5; int y = 6;"`

Result in listing: `"krs_app.c-3-int x = 5; int y = 6;"`

---

## `#include` Expansion — Inlining H Files

When the main loop encounters an `#include` line (while `parsen=true`):

```csharp
else if (line.StartsWith("#include", true, null))
{
    if (parsen)
    {
        // Extract filename from "#include "krs.h""
        string l_datei = Split[1].Trim('"', '<', '>');   // → "krs.h"
        string cIncludePfad = gruppenPfad + @"/src/" + l_datei;

        string name = Split[Split.Length - 1];  // just filename
        if (!readed.Contains(name))
        {
            readed.Add(name);  // mark as read — never inline again

            // ... resolve path, handle ".." segments ...

            List<string> include = CFile2List(cIncludePfad);  // RECURSIVE!
            Listing.RemoveAt(0);    // remove the #include line
            include.AddRange(Listing);   // append remaining lines
            Listing = include;      // replace Listing with: included + rest
        }
        else
        {
            myWriteLineQuiet(l_datei + " wurde bereits eingelesen!");
            Listing.RemoveAt(0);   // skip it silently
        }
    }
}
```

**Key insight**: `Listing` is a `List<string>`. After expansion, `Listing` contains the included file's lines followed by the remainder of the current file. The main `while(Listing.Count > 0)` loop then processes the included file's content as if it were inline.

**`readed` list**: This is the **de-duplication** mechanism. Once a filename has been included, it's never included again from any file. This matches the real C preprocessor's include guard behaviour (`#ifndef FOO_H ... #define FOO_H ... #endif`), but more aggressively — even without include guards, ScanDKSW never re-processes the same H file.

**`..` path resolution**: The code manually resolves `../` relative paths:
```csharp
for (int k = 0; k < Split.Length; k++) {
    if (Split[k] == "..") {
        Split[k] = "";              // remove ".."
        int l = k - 1;
        while (l > 0 && Split[l] == "") l--;
        if (l > 0) Split[l] = "";  // remove the parent folder
    }
}
```

---

## `removerHeader()` — Strip the Tag During CAN Scan

The CAN scanner also processes a tagged listing. It uses a helper:

```csharp
static string removerHeader(string a_line)
{
    int minus = a_line.IndexOf('-');
    a_line = a_line.Substring(minus + 1);
    minus = a_line.IndexOf('-');
    a_line = a_line.Substring(minus + 1);
    return a_line;
}
```

This extracts just the content part from the tagged line format.

---

*Next: [[06_main_scan_loop]]*
