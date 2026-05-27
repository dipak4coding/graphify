# CAN Scanner — Tracing CAN Signal Names

Tags: #can #signals #sic
Links: [[00_INDEX]] | [[03_data_structures]] | [[17_outputs]]

---

## Purpose

The CAN scanner answers: *"For a given software signal define (e.g. `SVC_GET_FP`), what is the name of the CAN signal on the bus, and which CAN message carries it?"*

This is deeper than the define matrix — it traces from the software abstraction layer down to the actual CAN bus.

---

## The Signal Chain

In VW AUTOSAR codebases, CAN reception typically follows this chain:

```
CAN Bus
  │
  ▼  (signal name, message name)
BIL (Bus Interface Layer)
  │  BIL_IDX_SIG_N_MOT (integer index define)
  │
  ▼  (sic.c)
SIC (Signal Interface Component)
  │  BilSig_Get_Signal_U16(BIL_IDX_SIG_N_MOT, &SicSig_N_Mot)
  │
  ▼  (sic.h)
SVC/KRS (application modules)
  │  #define SVC_GET_N_MOT SicSig_N_Mot.value
  │
  ▼  (software uses this)
```

The CAN scanner traces this chain backward from `sic.c`.

---

## Step-by-Step Algorithm

### Step 1: Parse `sic.c` for BilSig_Get_Signal calls

```c
// in sic.c:
BilSig_Get_Signal_U16(BIL_IDX_SIG_N_MOT, &SicSig_N_Mot);
```

ScanDKSW looks for lines containing `"BilSig_Get_Signal_"`:

```csharp
if (line.Contains("BilSig_Get_Signal_"))
{
    // Extract content between last ( and )
    int ka = line.LastIndexOf('(') + 1;
    int kz = line.LastIndexOf(')');
    string ki = line.Substring(ka, kz - ka);  // "BIL_IDX_SIG_N_MOT, &SicSig_N_Mot"
    
    // Split on comma
    string[] Split = ki.Split(',');
    // Split[0] = "BIL_IDX_SIG_N_MOT" (the index define)
    // Split[1] = "&SicSig_N_Mot"    (the sic signal variable, with &)
    Split[1] = Split[1].Trim().TrimStart('&');  // → "SicSig_N_Mot"
    
    CAN_Sigs.Add(Split[0], Split[1]);
    // Key = "BIL_IDX_SIG_N_MOT", Value = "SicSig_N_Mot"
}
```

Result: `CAN_Sigs = {"BIL_IDX_SIG_N_MOT" → "SicSig_N_Mot", ...}`

### Step 2: For each BIL_IDX_SIG define, find its C file

```csharp
foreach (var Csig in CAN_Sigs)
{
    // "BIL_IDX_SIG_N_MOT" is in D_Defines with pos.file = "bil_idx_sig.h"
    define d = D_Defines[Csig.Key];
    string fileName = d.pos.file;    // "bil_idx_sig.h"
    fileName = fileName.Replace(".h", ".c");  // → "bil_idx_sig.c"
    
    // Search for this file anywhere in the source tree
    string filePath = SearchPath(dieSourcen, fileName);
}
```

### Step 3: Extract CAN signal name and message from the C file

In `bil_idx_sig.c`, there's typically a table entry with a comment containing the CAN signal name:

```c
{BIL_IDX_NODE_TCU, BIL_IDX_MSG_N_MOT_INFO, 2, 0, 16, 1, 0, 1, 0, 0},  // N_Mot
```

ScanDKSW finds the line containing `BIL_IDX_SIG_N_MOT` and extracts:

**CAN signal name** (from `//` comment):
```csharp
int s = _line.IndexOf("//");
CAN_SigName = _line.Substring(s + 2).Trim();  // → "N_Mot"
```

**CAN message** (from struct content, 6th field):
```csharp
// Extract content between { and }
// Split on comma, take field [5]
// If starts with "BIL_IDX_MSG_", strip the prefix
CAN_Botschaft = Split[5].Trim().Substring(12);  // strip "BIL_IDX_MSG_"
// → "N_MOT_INFO"
```

Note: The format assumes exactly 10 comma-separated fields `Split.Length == 10` — this is described as MQBW-specific (Modularer Querbaukasten Wide = VW MQB platform).

### Step 4: Find the SIC define

```csharp
// Looking for a define whose value contains "SicSig_N_Mot.value"
foreach (var def in D_Defines)
{
    if (def.Value.value.Contains(Csig.Value + ".value"))
    {
        SIC_Define = def.Key;  // e.g. "SVC_GET_N_MOT"
        break;
    }
}
```

### Step 5: Record everything

```csharp
D_CANSignale.Add(SIC_Define, 
    new cansignal(SIC_Define, CAN_Botschaft, CAN_SigName));
// Key = "SVC_GET_N_MOT"
// → SIC_Define = "SVC_GET_N_MOT"
// → Botschaft  = "N_MOT_INFO"
// → SignalName = "N_Mot"
```

---

## Output: `CANSignale.csv`

```
SIC_Define       | CAN_Botschaft | SignalName
SVC_GET_N_MOT    | N_MOT_INFO    | N_Mot
SVC_GET_FP       | FP_INFO       | Fahrpedalwinkel
```

This links every software-level signal accessor define to its CAN signal name and message. With this, you can answer: *"When software reads `SVC_GET_FP`, it's receiving the `Fahrpedalwinkel` signal from CAN message `FP_INFO`."*

---

## Limitations and Notes

- **Hardcoded file path**: `sic.c` is found at `dieSourcen + @"/si/sic/src/sic.c"`. This is a project-specific assumption. It's not configurable in Config.xml.

- **MQBW format only**: The 10-field struct parsing `if (Split.Length == 10)` is hardcoded for the MQB Wide platform. Other CAN platforms may have different struct sizes.

- **CAN scanner only runs if `CAN_csv != ""`**: If the `<CAN_csv>` tag is present in Config.xml, the CAN scan runs. If the tag is missing or empty, it's skipped.

- **Error reporting**: If a file can't be found or a signal can't be traced, the tool logs a message but continues. `D_CANSignale` will simply have fewer entries.

---

*Next: [[17_outputs]]*
