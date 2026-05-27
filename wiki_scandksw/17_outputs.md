# Outputs — CSV Files and Binary Cache

Tags: #outputs #csv #cache
Links: [[00_INDEX]] | [[14_classify]] | [[02_architecture]]

---

## Output Directory Structure

```
{DestPath}/                    ← e.g. "...\ScanDKSW"
├── Config.xml                 ← copy of the config used for this run
├── Protokoll.txt              ← optional log file (if -Protokoll= specified)
├── .bins/                     ← binary cache files
│   ├── D_Defines.bin
│   ├── D_Doubletten.bin
│   ├── L_Undefines.bin
│   ├── D_TypeDefs.bin
│   └── D_CANSignale.bin
├── .csvs/                     ← all CSV outputs
│   ├── Defines.csv
│   ├── DefinesAll.csv
│   ├── DefMatrix.csv
│   ├── Doubletten.csv
│   ├── Undefines.csv
│   ├── CANSignale.csv
│   ├── A2L.csv
│   ├── VTAB.csv
│   ├── METHOD.csv
│   ├── TypeDefs.csv
│   └── Comments.csv
└── Comments/                  ← free-text comment files output
```

---

## Binary Cache — `.bin` Files

After a full scan (`-scan=scan`), the main data structures are serialised to binary files using C#'s `BinaryFormatter`:

```csharp
var binaryFormatter = new BinaryFormatter();
var fi = new FileInfo(BinFilePath + @"\D_Defines.bin");
using (var binaryFile = fi.Create())
{
    binaryFormatter.Serialize(binaryFile, D_Defines);
    binaryFile.Flush();
}
```

On subsequent runs with `-scan=load`, these files are deserialised:

```csharp
using (var binaryFile = fi.OpenRead())
    D_Defines = (Dictionary<string, define>)binaryFormatter.Deserialize(binaryFile);
```

**Why this matters**: A full scan of a large codebase can take minutes. The `-scan=load` mode skips all file I/O and uses the cached results. This is useful when you:
- Want to change the Config.xml ignore lists and re-output
- Want to try a different Classify configuration
- Want to regenerate the CSVs without re-scanning

**Requirement**: The `define` struct has `[Serializable]` attribute — this is essential for `BinaryFormatter` to work. All types in the struct (`position`, `List<position>`, `List<string>`) must also be serialisable.

---

## `Defines.csv` — Selected Defines

The **primary output**. Contains defines filtered by:
- Not in `IgnGroups` (group filter)
- Not in `IgnFiles` (file filter)
- Not in `IgnDefines` (define name filter)
- Not of type `TOIGNORE`

Columns:
```
Name | Wert | Typ | Datei | Zeile | Findings(Count) | Matrix(Groups)
```

The exact format in the actual output loop uses tab-separation. Each row represents one define with its type classification, source location, usage count, and which groups use it.

---

## `DefinesAll.csv` — Every Define

Same format as `Defines.csv` but with **no filtering** — contains every single define collected, including header guards, BIL_ defines, etc. Useful for debugging why a specific define wasn't in the filtered output.

---

## `DefMatrix.csv` — Producer/Consumer Matrix

This is the most important output for signal flow analysis.

**Format**: Rows = defines, Columns = software groups

```
Define Name        | Group1 | Group2 | Group3 | ...
KRS_GET_N_TURB     |   P    |   X    |        | X
KRS_GET_GANG_SOLL  |   P    |        |   X    |
SVC_GET_N_MOT      |        |   P    |   X    | X
```

- **P** = producer — the group that contains the `#define` declaration
- **X** = consumer — a group where this define name was found used (substring found in a code line)
- Empty = no relationship

The producer group is determined from `define.pos.file` → `extractClust()`.
The consumer groups are determined from `define.findings` → `extractClust()` on each finding's file.

Groups are listed in alphabetical order. Groups from `IgnGroups` are excluded from the columns.

---

## `Doubletten.csv` — Duplicate Defines

Contains defines that appeared more than once:

| Key suffix | Meaning |
|------------|---------|
| `NAME<0>O` | Original definition |
| `NAME<N>D` | Duplicate with **same** value (D = Dopplung) |
| `NAME<N>M` | Duplicate with **different** value (M = Mehrfach) |

Example: `#define NULL` appears in multiple headers:
```
NULL<0>O  → original: defined as "(void*)0" in stdlib.h
NULL<1>D  → duplicate: same value "(void*)0" in another header
NULL<2>D  → another duplicate
```

Defines listed in `IgnoreDoubletten` in Config.xml are still recorded here but don't cause an exception.

---

## `Undefines.csv` — `#undef` Records

```
Name | Original_File | Original_Line | Undef_File | Undef_Line
```

One row per `#undef` encountered. Useful for finding where feature flags are intentionally turned off.

---

## `CANSignale.csv` — CAN Signal Mapping

```
SIC_Define | CAN_Botschaft | SignalName
```

Maps software-level signal accessor defines to their CAN signal and message names. See [[16_can_scanner]].

---

## `A2L.csv`, `METHOD.csv`, `VTAB.csv`

These export the parsed A2L data for use in Excel/other tools. See [[15_a2l_scanner]] for format details.

---

## `TypeDefs.csv` — typedef Aliases

```
TypeName | BaseType
```

All `typedef` aliases found during scanning. Used for `CastAway()` in classify, and exported here for inspection.

---

## All Outputs Use Tab Separation

All CSVs use **tab** (`\t`) as the column separator, not comma. This is noted in the code:
```csharp
// Das Trennzeichen kann auch mit 'sep=<Trennzeichen>' in der ersten Zeile der CSV-
// Datei explizit angegeben werden, z. B. 'sep=,' für Komma oder 'sep=;' für das Semikolon.
// sw.WriteLine("sep=\t");  // commented out
```

When opening in Excel, use "Data → Text to Columns" with tab separator, or the file will appear as a single column.

---

## Sorted Output

All dictionaries are sorted alphabetically before writing:

```csharp
var sortedDict = D_A2L
    .OrderBy(pair => pair.Key)
    .ToDictionary(pair => pair.Key, pair => pair.Value);
```

This makes the CSVs easier to read and diff between runs.

---

## The Config.xml Copy

```csharp
File.Copy(dieConfig, DestPath + "\\Config.xml", true);
```

A copy of the Config.xml used for this run is placed in the output folder. This ensures you can always tell which configuration produced a given set of outputs — essential for reproducibility across different project variants.

---

*Next: [[18_tools]]*
