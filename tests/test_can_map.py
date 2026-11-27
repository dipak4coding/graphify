"""CAN signal -> variable: config table row (index + comment) -> getter call in the reader function -> variable."""
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("cantools")
pytest.importorskip("clang.cindex")

from graphify.can_map import join_can
from graphify.extractors.dbc import join_dbc, scan_dbc

DBC = '''VERSION ""
NS_ :
BS_:
BU_: ESP TCU
BO_ 1648 ESP_21: 8 ESP
 SG_ ESP_Speed : 16|16@1+ (0.01,0) [0|655] "km/h" TCU
 SG_ ESP_Mode : 1|3@1+ (1,0) [0|7] "" TCU
BO_ 304 MO_Msg: 8 ESP
 SG_ MO_Torque : 7|12@0+ (0.5,-10) [-10|2038] "Nm" TCU
VAL_ 1648 ESP_Mode 0 "Off" 1 "On";
'''
BIL_H = '''#define BIL_IDX_SIG_N_MOT    3
#define BIL_IDX_SIG_V_FZG    (BIL_IDX_SIG_N_MOT + 1)
#define BIL_IDX_SIG_MODE     9
typedef struct { unsigned short idx; unsigned char a; unsigned char b; } BilSigCfg_t;
'''
BIL_C = '''#include "bil.h"
const BilSigCfg_t BilSig_Data_CfgSig_ci[] =
{
  { BIL_IDX_SIG_N_MOT , 1, 2 },   // MO_Torque
  { BIL_IDX_SIG_V_FZG , 1, 2 },   // ESP_Speed  // second comment
  { BIL_IDX_SIG_MODE  , 1, 2 },   // ESP_Mode
  { 12                , 1, 2 }    // Not_In_Dbc
};
'''
SIC_C = '''#include "bil.h"
typedef struct { unsigned short value; } SicU16_t;
SicU16_t SicSig_N_Mot, SicSig_V_Fzg;
unsigned char SicSig_Mode;
extern void BilSig_Get_Signal_U16(int idx, SicU16_t *p);
extern void BilSig_Get_Signal_U8(int idx, unsigned char *p);
void Sic_HolenSignale(void)
{
  BilSig_Get_Signal_U16(BIL_IDX_SIG_N_MOT, &SicSig_N_Mot);
  BilSig_Get_Signal_U16(BIL_IDX_SIG_V_FZG, &(SicSig_V_Fzg));
  BilSig_Get_Signal_U8(BIL_IDX_SIG_MODE, &SicSig_Mode);
  BilSig_Get_Signal_U8(77, &SicSig_Mode);
}
'''


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "bil.h").write_text(BIL_H)
    (tmp_path / "bil.c").write_text(BIL_C)
    (tmp_path / "sic.c").write_text(SIC_C)
    (tmp_path / "t.dbc").write_text(DBC)
    return tmp_path


def _run(proj, **kw):
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*", **kw)
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "bil.c", proj / "sic.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    return nodes, edges


def _links(nodes, edges):
    lab = {n["id"]: n["label"] for n in nodes}
    return {(lab[e["source"]], lab[e["target"]]) for e in edges if e["relation"] == "carried_in"}


def test_signal_linked_to_getter_variable(proj, monkeypatch):
    monkeypatch.chdir(proj)
    nodes, edges = _run(proj)
    # index macros, `(A + 1)` and `&(var)` are resolved by clang; the second comment (`// second comment`) is ignored
    assert _links(nodes, edges) == {("MO_Torque", "SicSig_N_Mot"), ("ESP_Speed", "SicSig_V_Fzg"), ("ESP_Mode", "SicSig_Mode")}
    e = next(e for e in edges if e["relation"] == "carried_in" and e["source"].endswith("esp_mode"))
    assert e["metadata"]["index"] == 9 and e["metadata"]["getter"] == "BilSig_Get_Signal_U8" and e["metadata"]["match"] == "exact"


def test_mapping_files_and_diagnostics(proj, monkeypatch):
    monkeypatch.chdir(proj)
    _run(proj)
    data = json.loads(next(proj.glob("**/can_mapping.json")).read_text(encoding="utf-8"))
    d = data["diagnostics"]
    assert [r["comment"] for r in d["rows_not_in_dbc"]] == ["Not_In_Dbc"]
    assert [g["index"] for g in d["getters_without_row"]] == [77]
    assert data["stats"]["linked_signals"] == 3
    csv = next(proj.glob("**/can_mapping.csv")).read_text(encoding="utf-8-sig").splitlines()
    assert csv[0].startswith("dbc_signal;message;frame_id") and any(l.startswith("ESP_Mode;ESP_21;1648") for l in csv)


def test_configurable_names(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "bil.c").write_text(BIL_C.replace("BilSig_Data_CfgSig_ci", "My_Table"))
    (proj / "sic.c").write_text(SIC_C.replace("Sic_HolenSignale", "My_Reader"))
    assert _links(*_run(proj)) == set()                                   # default names are not there
    assert len(_links(*_run(proj, can_table="My_Table", can_reader="My_Reader"))) == 3


def test_join_name_rules():
    sig = lambda n, msg: {"id": "s_" + n, "label": n, "type": "can_signal", "metadata": {"message": msg, "direction": "RX"}}
    var = {"id": "v1", "label": "SicSig_X", "type": "variable", "source_file": "sic.c"}
    nodes, edges = [sig("ESP_Speed", "M1"), var], []
    rows = [{"index": 1, "name": "esp speed", "file": "bil.c", "line": 4}, {"index": 2, "name": "", "file": "bil.c", "line": 5},
            {"index": None, "name": "x", "first": "FOO", "file": "bil.c", "line": 6}]
    res = join_can(nodes, edges, rows, [{"index": 1, "var": "v1", "var_name": "SicSig_X", "getter": "G", "file": "sic.c", "line": 9}])
    assert res["mapping"][0]["status"] == "normalized" and len(edges) == 1       # case / separators ignored, reported as normalized
    assert res["stats"]["rows_without_name"] == 1 and res["stats"]["rows_without_index"] == 1


def test_documentation_shows_variable(proj, monkeypatch):
    monkeypatch.chdir(proj)
    nodes, edges = _run(proj)
    from graphify.swdoc import build_vault, can_notes
    res = build_vault({"nodes": nodes, "edges": edges}, ["."])
    msg = can_notes(res["can"], res["stems"])["can/messages/ESP_21.md"]
    assert "[[SicSig_Mode]]" in msg and "[[SicSig_V_Fzg]]" in msg
    var = res["notes"]["variables/SicSig_Mode.md"]
    assert "### CAN signal" in var and "ESP_Mode" in var and "BilSig_Get_Signal_U8" in var
    from graphify.swdoc_html import build_data
    d = build_data(build_vault({"nodes": nodes, "edges": edges}, ["."], want_notes=False), "T")
    assert any(g["v"] and g["v"][0][0] >= 0 for m in d["can"]["m"] for g in m["g"])


def test_signal_pages_and_links(proj, monkeypatch):
    monkeypatch.chdir(proj)
    nodes, edges = _run(proj)
    from graphify.swdoc import build_vault, can_notes, single_file
    res = build_vault({"nodes": nodes, "edges": edges}, ["."])
    notes = can_notes(res["can"], res["stems"], res["cstems"])
    assert "can/signals/ESP_Mode.md" in notes
    sig = notes["can/signals/ESP_Mode.md"]
    assert "0 | Off" in sig and "[[SicSig_Mode]]" in sig and "`BilSig_Get_Signal_U8`" in sig and "| 9 |" in sig
    assert "[[ESP_Mode]]" in notes["can/messages/ESP_21.md"]                       # signal name in the message table is a link
    assert "[[ESP_Mode]]" in res["notes"]["variables/SicSig_Mode.md"]              # and on the variable page
    text = single_file(res)
    assert "### ESP_Mode" in text and "[[#ESP_Mode]]" in text


def test_html_signal_page_and_component_filter(proj, monkeypatch):
    monkeypatch.chdir(proj)
    nodes, edges = _run(proj)
    from graphify.swdoc import build_vault
    from graphify.swdoc_html import build_data, render
    d = build_data(build_vault({"nodes": nodes, "edges": edges}, ["."], want_notes=False), "T")
    g = next(g for m in d["can"]["m"] for g in m["g"] if g["n"] == "ESP_Mode")
    assert g["v"][0][0] >= 0 and g["v"][0][2] == 9 and g["v"][0][3] == "BilSig_Get_Signal_U8"
    html = render(d)
    assert 'id="comp"' in html and "pageSig" in html and "#s/" in html


# ---- SokSig: comments in front of the elements (numbered 0,1,2..), array of {enum, &variable} ------------------

SOK_H = '''enum { SOK_N_MOT = 0, SOK_V_FZG, SOK_MODE, SOK_MISSING = 7 };
'''
SOK_C = '''#include "sok.h"
typedef struct { int a; int b; } SokCfg_t;
const SokCfg_t SokSig_Data_CfgSig_ci[] =
{
  // MO_Torque
  { 1, 2 },   // trailing text of the first element, not its name
  /* ESP_Speed */
  { 3, 4 },
  // ESP_Mode
  { 5, 6 },
  // Unmapped_Sig
  { 7, 8 }
};
unsigned short SokVal_Torque, SokVal_Speed;
unsigned char SokVal_Mode;
typedef struct { int e; void *p; } SokMap_t;
const SokMap_t Sic_Data_CfgSokSigU8_ci[] =
{
  { SOK_N_MOT, &SokVal_Torque },
  { SOK_V_FZG, (void *)&SokVal_Speed },
  { SOK_MODE,  &SokVal_Mode },
  { SOK_MISSING, &SokVal_Mode }
};
'''


def _run_sok(proj, **kw):
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*", **kw)
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "sok.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    return nodes, edges


def test_soksig_comments_numbered_and_mapped_to_variables(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_C)
    (proj / "t.dbc").write_text(DBC.replace("ESP_Mode", "ESP_Mode"))
    nodes, edges = _run_sok(proj)
    assert _links(nodes, edges) == {("MO_Torque", "SokVal_Torque"), ("ESP_Speed", "SokVal_Speed"), ("ESP_Mode", "SokVal_Mode")}
    e = next(e for e in edges if e["relation"] == "carried_in" and e["source"].endswith("esp_mode"))
    assert e["metadata"]["index"] == 2 and e["metadata"]["getter"] == "Sic_Data_CfgSokSigU8_ci"
    data = json.loads(next(proj.glob("**/can_mapping.json")).read_text(encoding="utf-8"))
    d = data["diagnostics"]
    assert [r["comment"] for r in d["rows_not_in_dbc"]] == ["Unmapped_Sig"]          # position 3, no enum row
    assert [g["index"] for g in d["getters_without_row"]] == [7]                      # enum 7 has no element
    assert {r["config_index"] for r in data["mapping"] if r["dbc_signal"] == "MO_Torque"} == {0}


def test_soksig_names_are_configurable(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_C.replace("SokSig_Data_CfgSig_ci", "My_Names").replace("Sic_Data_CfgSokSigU8_ci", "My_Map"))
    assert _links(*_run_sok(proj)) == set()
    assert len(_links(*_run_sok(proj, can_sok_table="My_Names", can_sok_map="My_Map"))) == 3


# ---- TX: return MACRO(value, <dbc signal name>) ---------------------------------------------------------------

TX_C = '''#define SocSig_Out(x,y) (x)
enum { OFF_VAL = 0x10 };
int Soc_Raw, Soc_Cal, Soc_Unused;
int Helper(void) { return Soc_Cal + 1; }
int SocSig_BestTorque(void) { int t = Soc_Raw * 2; return SocSig_Out(t + Helper(), MO_Torque); }
int SocSig_BestSpeed(void) { if (Soc_Raw) { return SocSig_Out(Soc_Raw, ESP_Speed); } return SocSig_Out(0, ESP_Speed); }
int SocSig_BestOther(void) { return SocSig_Out(Soc_Raw, No_Such_Signal); }
int SocSig_BestConst(void) { if (Soc_Raw) { return SocSig_Out(5, ESP_Mode); } return SocSig_Out(OFF_VAL, ESP_Mode); }
int NotMatching(void) { return SocSig_Out(Soc_Unused, ESP_Mode); }
'''


def test_tx_return_macro_links_signal_function_and_sources(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "tx.c").write_text(TX_C)
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "tx.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    lab = {n["id"]: n["label"] for n in nodes}
    rel = lambda r: {(lab[e["source"]], lab[e["target"]]) for e in edges if e["relation"] == r and e["source"] in lab and e["target"] in lab}
    assert rel("computed_by") == {("MO_Torque", "SocSig_BestTorque()"), ("ESP_Speed", "SocSig_BestSpeed()"), ("ESP_Mode", "SocSig_BestConst()")}
    d = {x for x in rel("derived_from") if x[0] in ("MO_Torque", "ESP_Speed")}
    assert d == {("MO_Torque", "Soc_Raw"), ("MO_Torque", "Soc_Cal"), ("ESP_Speed", "Soc_Raw")}   # local t and the helper are followed
    csv = next(proj.glob("**/can_tx_mapping.csv")).read_text(encoding="utf-8-sig").splitlines()
    assert any(l.startswith("MO_Torque;MO_Msg") and "SocSig_Out" in l for l in csv)
    assert any(l.startswith("No_Such_Signal") and l.endswith("not_in_dbc") for l in csv)


def test_tx_function_pattern_is_configurable(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "tx.c").write_text(TX_C)
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*", can_tx_functions="NotMatching")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "tx.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    lab = {n["id"]: n["label"] for n in nodes}
    assert {(lab[e["source"]], lab[e["target"]]) for e in edges if e["relation"] == "computed_by"} == {("ESP_Mode", "NotMatching()")}


def test_tx_in_documentation(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "tx.c").write_text(TX_C)
    from graphify import swdoc
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "tx.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    res = swdoc.build_vault({"nodes": nodes, "edges": edges}, ["."])
    notes = swdoc.can_notes(res["can"], res["stems"], res["cstems"])
    sig = next(v for k, v in notes.items() if k.startswith("can/signals/") and "MO_Torque" in k)
    assert "## Computed by" in sig and "SocSig_BestTorque" in sig and "SocSig_Out" in sig and "Soc_Raw" in sig
    g = next(g for m in res["can"]["messages"] for g in m["signals"] if g["name"] == "ESP_Speed")
    assert [t["label"] for t in g["tx"]] == ["SocSig_BestSpeed()"] and [l for _v, l in g["tx"][0]["sources"]] == ["Soc_Raw"]


def test_tx_constant_value_when_no_variable(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "tx.c").write_text(TX_C)
    from graphify.extractors import clang_c
    clang_c.set_runtime_config(extractor="clang", dbc=str(proj / "t.dbc"), dbc_node="*")
    nodes, edges = [], []
    try:
        clang_c.run_clang_pass([proj / "tx.c"], proj, nodes, edges)
    finally:
        clang_c.set_runtime_config()
    e = next(e for e in edges if e["relation"] == "computed_by" and e["source"].endswith("esp_mode") and "bestconst" in e["target"])
    assert sorted(e["metadata"]["constants"]) == ["16", "5"] and e["metadata"]["lines"]
    line = next(l for l in next(proj.glob("**/can_tx_mapping.csv")).read_text(encoding="utf-8-sig").splitlines() if l.startswith("ESP_Mode") and "BestConst" in l)
    assert "5 16" in line or "16 5" in line
    assert not any(l.split(";")[9] for l in next(proj.glob("**/can_tx_mapping.csv")).read_text(encoding="utf-8-sig").splitlines()[1:] if l.startswith("MO_Torque"))


# ---- RX arrays: Sic_Data_CfgSigU8_ci / U16, designated initializers, headers, scan report --------------------

RX_H = """enum { IDX_SIG_N = 0, IDX_SIG_V, IDX_SIG_W };
extern unsigned char RxU8; extern unsigned short RxU16, RxW;
const struct { int i; void *p; } Sic_Data_CfgSigU8_ci[] = { { IDX_SIG_N, &RxU8 } };
"""
RX_C = """#include "rx.h"
unsigned char RxU8; unsigned short RxU16, RxW;
const struct { int idx; int a; } BilSig_Data_CfgSig_ci[] =
{
  { IDX_SIG_N, 1 },   // MO_Torque
  { IDX_SIG_V, 2 },   // ESP_Speed
  { IDX_SIG_W, 3 }    // ESP_Mode
};
void *const Sic_Data_CfgSigU16_ci[] = { [IDX_SIG_V] = (void *)&RxU16, [IDX_SIG_W] = &RxW };
"""


def test_rx_arrays_u8_u16_designated_and_header(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "rx.h").write_text(RX_H)
    (proj / "sok.c").write_text(RX_C)
    nodes, edges = _run_sok(proj)
    assert _links(nodes, edges) == {("MO_Torque", "RxU8"), ("ESP_Speed", "RxU16"), ("ESP_Mode", "RxW")}
    scan = json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))
    assert {a["array"] for a in scan["arrays"]} == {"Sic_Data_CfgSigU8_ci", "Sic_Data_CfgSigU16_ci"}
    assert {n["name"] for n in scan["not_found"]} >= {"SokSig_Data_CfgSig_ci", "Sic_Data_CfgSokSigU8_ci"}


def test_scan_report_names_skipped_rows(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_C.replace("{ SOK_N_MOT, &SokVal_Torque },", "{ SOK_N_MOT },").replace("{ SOK_MODE,  &SokVal_Mode },", "{ SOK_MODE,  3 },"))
    _run_sok(proj)
    scan = json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))
    sk = [x["why"] for a in scan["arrays"] if a["role"] == "sok_map" for x in a["skipped"]]
    assert any("expected" in w for w in sk) and any("not the address of a variable" in w for w in sk)


SOK_AFTER_C = """#include "sok.h"
typedef struct { void *p; int byte; int bit; int n; int sg; } SokCfg2_t;
#define PTR_MSG_DATA(m) ((void *)(m))
#define UNSIGNED 0
static const SokCfg2_t SokSig_Data_CfgSig_ci[4] =
{
  // Pointer to message data                  Start      Start     Number   Signed
  //                                          byte       bit       of bits

  // ### Message_A ###
  {
    PTR_MSG_DATA(1),
    7,
    5,
    3,
    UNSIGNED
  }, // MO_Torque

  {
    PTR_MSG_DATA(1),
    8,
    0,
    4,
    UNSIGNED
  }, // ESP_Speed
  { PTR_MSG_DATA(2), 1, 2, 3, UNSIGNED }, /* ESP_Mode */
};
unsigned short SokVal_Torque, SokVal_Speed;
unsigned char SokVal_Mode;
const struct { int e; void *p; } Sic_Data_CfgSokSigU8_ci[] =
{
  { SOK_N_MOT, &SokVal_Torque },
  { SOK_V_FZG, &SokVal_Speed },
  { SOK_MODE,  &SokVal_Mode },
};
"""


def test_soksig_comment_behind_the_element_and_header_comments(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_AFTER_C)
    nodes, edges = _run_sok(proj)
    assert _links(nodes, edges) == {("MO_Torque", "SokVal_Torque"), ("ESP_Speed", "SokVal_Speed"), ("ESP_Mode", "SokVal_Mode")}
    scan = json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))
    assert next(a for a in scan["arrays"] if a["role"] == "sok_table")["comment_style"] == "after"


def test_soksig_comment_style_can_be_forced(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_C)
    assert len(_links(*_run_sok(proj, can_sok_comment="before"))) == 3
    assert _links(*_run_sok(proj, can_sok_comment="after")) == set()


REAL_HEAD = """#define SOK_PTR_DATA_MSG(m) ((void *)(m))
#define SOK_IDX_RX_MSG_DUMMY_01 1
#define UNSIGNED 0
#define DUMMY_VARIANT_02 2
typedef struct { void *p; int byte; int bit; int n; int sg; } T_SOKSIG_DATA_CFGSIG;
#define SOK_ANZ_RX_SIGNALE 3
"""
REAL_BODY = """static const T_SOKSIG_DATA_CFGSIG SokSig_Data_CfgSig_ci[SOK_ANZ_RX_SIGNALE] = // !!! Dummy - max 32
{
  //  Pointer to dummy message data                      Start-     Start-    Number  Signed
  //                                                    byte       bit       Bits

  // ### DUMMY_MSG_01 ###

  {SOK_PTR_DATA_MSG(SOK_IDX_RX_MSG_DUMMY_01),             7,         5,        3,      UNSIGNED}, // MO_Torque
  {SOK_PTR_DATA_MSG(SOK_IDX_RX_MSG_DUMMY_01),             8,         0,        4,      UNSIGNED}, // ESP_Speed

  #if (DUMMY_VARIANT != DUMMY_VARIANT_02)

  {SOK_PTR_DATA_MSG(SOK_IDX_RX_MSG_DUMMY_01),             9,         6,        2,      UNSIGNED}, // ESP_Mode

  #endif // DUMMY_VARIANT

}; // SokSig_Data_CfgSig_ci
unsigned short SokVal_Torque, SokVal_Speed;
unsigned char SokVal_Mode;
enum { SOK_N_MOT, SOK_V_FZG, SOK_MODE };
const struct { int e; void *p; } Sic_Data_CfgSokSigU8_ci[] = { { SOK_N_MOT, &SokVal_Torque }, { SOK_V_FZG, &SokVal_Speed }, { SOK_MODE, &SokVal_Mode } };
"""


def test_soksig_real_layout_with_variant_and_without(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.c").write_text(REAL_HEAD + "#define DUMMY_VARIANT 1\n" + REAL_BODY)
    assert len(_links(*_run_sok(proj))) == 3
    (proj / "sok.c").write_text(REAL_HEAD + "#define DUMMY_VARIANT 2\n" + REAL_BODY)
    assert _links(*_run_sok(proj)) == {("MO_Torque", "SokVal_Torque"), ("ESP_Speed", "SokVal_Speed")}   # element 2 is compiled out


def test_soksig_table_is_read_from_text_when_clang_cannot_parse(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.c").write_text(REAL_BODY.replace("static const T_SOKSIG_DATA_CFGSIG", "static const T_UNKNOWN"))
    assert len(_links(*_run_sok(proj))) == 3
    scan = json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))
    t = next(a for a in scan["arrays"] if a["role"] == "sok_table")
    assert t["from_text"] and "NOT evaluated" in t["warning"] and t["clang_errors"] and t["comment_style"] == "after"


def test_soksig_enum_starting_at_one_is_position_plus_one(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text("enum { SOK_NONE = 0, SOK_N_MOT = 1, SOK_V_FZG, SOK_MODE };\n")
    body = SOK_AFTER_C.replace("{ SOK_N_MOT, &SokVal_Torque },", "{ SOK_N_MOT, &SokVal_Torque },")
    (proj / "sok.c").write_text(body)
    nodes, edges = _run_sok(proj)
    assert _links(nodes, edges) == {("MO_Torque", "SokVal_Torque"), ("ESP_Speed", "SokVal_Speed"), ("ESP_Mode", "SokVal_Mode")}
    scan = json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))
    assert scan["sok_enum_offset"]["value"] == 1
    # forcing 0 reproduces the one-line shift: element 1 gets the variable of enum 1
    assert _links(*_run_sok(proj, can_sok_offset="0")) == {("ESP_Speed", "SokVal_Torque"), ("ESP_Mode", "SokVal_Speed")}


def test_soksig_enum_from_zero_has_no_offset(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    (proj / "sok.c").write_text(SOK_AFTER_C)
    _run_sok(proj)
    assert json.loads(next(proj.glob("**/can_scan.json")).read_text(encoding="utf-8"))["sok_enum_offset"]["value"] == 0


def test_soksig_table_with_windows_line_endings(proj, monkeypatch):
    """CRLF files: clang offsets are bytes of the file, so the file must be read without newline translation."""
    monkeypatch.chdir(proj)
    (proj / "sok.h").write_text(SOK_H)
    body = REAL_HEAD + "#define DUMMY_VARIANT 1\n" + REAL_BODY
    body = "// header line\n" * 25 + body.replace("(Sic_Data", "(Sic_Data")
    (proj / "sok.c").write_bytes(body.replace("\n", "\r\n").encode("latin-1"))
    assert _links(*_run_sok(proj)) == {("MO_Torque", "SokVal_Torque"), ("ESP_Speed", "SokVal_Speed"), ("ESP_Mode", "SokVal_Mode")}
