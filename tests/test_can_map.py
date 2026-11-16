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
