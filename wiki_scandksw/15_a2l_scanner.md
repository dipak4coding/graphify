# A2L Scanner — Calibration File Parsing

Tags: #a2l #measurement #calibration
Links: [[00_INDEX]] | [[03_data_structures]] | [[14_classify]]

---

## What is an A2L File?

An **A2L file** (ASAP2 format) is a calibration description file for automotive ECUs (Engine Control Units / Gearbox Control Units). It lists every **measurable variable** in the ECU's software — its name, data type, memory address, and how to convert the raw value to a physical unit.

For ScanDKSW, the A2L file is used for one key purpose: **identify which defines are variable references** (E_DEFTYPE.VARIABLE). If a define's value matches a variable name in the A2L, that define is a signal getter macro.

---

## A2L File Structure (relevant parts)

```
/begin MEASUREMENT Krs_N_Turb "Turbinendrehzahl"
    SWORD Krs_N_Turb 0 0 -6553.6 6553.5
    ECU_ADDRESS 0x6000F9B2
    /begin IF_DATA CANAPE_EXT 0x64
        LINK_MAP "Krs_N_Turb" 0x6000F9B2 0x0 0x0 0x0 0x1 0xCF 0x0
    /end IF_DATA
/end MEASUREMENT

/begin COMPU_METHOD Krs_N_Turb "Turbinendrehzahl Skalierung"
    LINEAR "%5.1" "rpm"
    COEFFS_LINEAR 0.1 0
/end COMPU_METHOD

/begin COMPU_VTAB KrsZustand "Zustände der Rückschaltung" TAB_VERB 22
    0 "Einlegen"
    1 "Befullen"
    ...
/end COMPU_VTAB
```

---

## `scanA2L()` — The Parser

Located in `Tools.cs`. Uses a state machine approach: three parallel state flags track which block is currently being parsed.

```csharp
int  in_MEASUREMENT  = 0;   // 0=outside, 1=inside (props collected), 2=just entered (next line is type/range)
bool in_COMPU_METHOD = false;
// COMPU_VTAB tracked via: content == null (outside) vs content != null (inside)
```

---

## Parsing MEASUREMENT

```
/begin MEASUREMENT Krs_N_Turb "Turbinendrehzahl"
→ create new MEASUREMENT, set description
→ in_MEASUREMENT = 2

SWORD Krs_N_Turb 0 0 -6553.6 6553.5
→ in_MEASUREMENT==2: parse this line
→ VarType = "SWORD", Compu_Method = "Krs_N_Turb", Min = -6553.6, Max = 6553.5
→ in_MEASUREMENT = 1

LINK_MAP "Krs_N_Turb" 0x6000F9B2 ...
→ A2LName = "Krs_N_Turb" (the canonical name used by CANape)
→ D_A2L.Add("Krs_N_Turb", msrmnt)

/end MEASUREMENT
→ in_MEASUREMENT = 0
```

**`patchIndex()`**: The `LINK_MAP` name may use special dot-notation for array/struct indexing:
```
"KrsTMMRVorhalt_ko._0_" → "KrsTMMRVorhalt_ko[0]"
"Arr._0_._1_"            → "Arr[0][1]"
```
This converts the A2L's `._N_` notation to C-style `[N]` brackets.

**Bit variables**: If `BitMask` is present and `Min==0, Max==1`, the variable is a single bit. The name is extended with `[0x{BitMask}]` to make it unique — but since resolving bit offsets requires the memory address, this is noted as incomplete.

**Duplicate names**: If the same name appears multiple times (some A2L files have this), trailing `_` characters are appended:
```csharp
while (D_A2L.ContainsKey(name))
    name += "_";
D_A2L.Add(name, msrmnt);
```

---

## Parsing COMPU_METHOD

```
/begin COMPU_METHOD Krs_N_Turb "Turbinendrehzahl Skalierung"
→ create COMPU_METHOD, in_COMPU_METHOD = true

LINEAR "%5.1" "rpm"
→ c_mt.type = E_CM_TYPE.LINEAR
→ strip "LINEAR", strip format string, extract unit "rpm"

COEFFS_LINEAR 0.1 0
→ c_mt.factor = 0.1, c_mt.offset = 0.0

/end COMPU_METHOD
→ D_COMPU_METHOD.Add("Krs_N_Turb", c_mt)
```

Three method types:
- **IDENTICAL**: raw value = physical value (just a unit)
- **LINEAR**: `y = factor * x + offset`
- **TAB_VERB**: lookup table → references a COMPU_VTAB

---

## Parsing COMPU_VTAB

```
/begin COMPU_VTAB KrsZustand "Zustände" TAB_VERB 22
→ content = new Dictionary<string, string>()

0 "Einlegen"
→ content["0"] = "Einlegen"

1 "Befullen"
→ content["1"] = "Befullen"
...

/end COMPU_VTAB
→ D_COMPU_VTAB.Add("KrsZustand", new COMPU_VTAB("Zustände", content))
```

---

## Encoding: ISO-8859-1 (Latin-1)

A2L files from this era (AUTOSAR tools circa 2010) use Latin-1 encoding, not UTF-8. German characters like `ü`, `ä`, `ö` would be corrupted if read as UTF-8:

```csharp
Encoding latin1 = Encoding.GetEncoding("ISO-8859-1");
using (StreamReader sr = new StreamReader(dieA2L, latin1))
```

`killSpecialCharacters()` also removes backslash-escaped characters:
```csharp
static string killSpecialCharacters(string _s)
{
    for (int i = _s.IndexOf("\\"); i >= 0; i = _s.IndexOf("\\"))
    {
        string e = _s.Substring(0, i) + _s.Substring(i + 2);
        _s = e;
    }
    return _s;
}
```

This removes `\n`, `\t`, `\"` etc. in string content.

---

## Use in Classification

After scanning the A2L:

```csharp
else if ((pv.Length > 0) && (D_A2L.ContainsKey(pv)))
    p.type = E_DEFTYPE.VARIABLE;
```

If the value of a define (after cast-stripping and de-parenthesising) is found as a key in `D_A2L`, the define is classified as VARIABLE. This is how signal getter macros are identified:

```c
#define KRS_GET_N_TURB  Krs_N_Turb
// "Krs_N_Turb" is in D_A2L → KRS_GET_N_TURB is E_DEFTYPE.VARIABLE
```

---

## CSV Outputs

Three CSV files are written from the A2L data (see [[17_outputs]]):

**A2L.csv** — one row per measurement variable:
```
Name | Kommentar | VarType | Min | Max | CompuMethod | CM-Kommentar | CM-Type | Faktor | Offset | Einheit | VTAB | VT-Kommentar | VT-Tabelle
```

**METHOD.csv** — one row per COMPU_METHOD:
```
Name | Kommentar | Type | Faktor | Offset | VTAB | Einheit
```

**VTAB.csv** — one row per COMPU_VTAB:
```
Name | Kommentar | Tabelle   (multi-line cell with key→value pairs)
```

---

*Next: [[16_can_scanner]]*
