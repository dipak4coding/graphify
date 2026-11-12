"""DBC extractor: parse, bit layout (Intel + Motorola, checked against cantools' own encoder), graph join."""
import pytest

cantools = pytest.importorskip("cantools")

from graphify.extractors.dbc import bits_of, join_dbc, scan_dbc

DBC = '''VERSION ""

NS_ :

BS_:

BU_: ESP GW TCU


BO_ 1648 ESP_21: 8 ESP
 SG_ ESP_Speed : 16|16@1+ (0.01,0) [0|655] "km/h" GW
 SG_ ESP_Flag : 0|1@1+ (1,0) [0|1] "" GW
 SG_ ESP_Mode : 1|3@1+ (1,0) [0|7] "" GW,TCU
BO_ 304 MO_Msg: 8 GW
 SG_ MO_Torque : 7|12@0+ (0.5,-10) [-10|2038] "Nm" ESP
 SG_ MO_Gear : 27|4@0+ (1,0) [0|15] "" ESP
BO_ 500 TCU_Msg: 8 TCU
 SG_ TCU_Gear : 0|4@1+ (1,0) [0|15] "" GW
BO_ 600 Other_Msg: 8 GW
 SG_ Oth : 0|8@1+ (1,0) [0|255] "" ESP
BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_ "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 1648 20;
CM_ SG_ 1648 ESP_Speed "Vehicle speed";
VAL_ 1648 ESP_Mode 0 "Off" 1 "On" 2 "Error";
'''


@pytest.fixture
def dbc_file(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC, encoding="utf-8")
    return str(p)


def test_bits_intel_and_motorola():
    assert bits_of(16, 16, "little_endian") == list(range(16, 32))
    # Motorola start 7 = MSB at bit 7 of byte 0; 12 bits run through byte 0 and the upper nibble of byte 1
    assert sorted(bits_of(7, 12, "big_endian")) == list(range(0, 8)) + [12, 13, 14, 15]
    assert bits_of(7, 12, "big_endian")[-1] == 7           # LSB first, MSB (start bit) last
    assert sorted(bits_of(27, 4, "big_endian")) == [24, 25, 26, 27]


def test_motorola_bits_match_cantools(dbc_file):
    """The decoded value must come from exactly the bits bits_of() says."""
    db = cantools.database.load_file(dbc_file, database_format="dbc")
    msg = db.get_message_by_name("MO_Msg")
    sig = {s["name"]: s for s in scan_dbc(dbc_file)["messages"]["MO_Msg"]["signals"]}
    for name in ("MO_Torque", "MO_Gear"):
        bits = sig[name]["bits"]
        raw_max = (1 << sig[name]["length"]) - 1
        data = bytearray(8)
        for i, b in enumerate(bits):                 # set each signal bit (LSB first) one by one
            d2 = bytearray(8)
            d2[b // 8] |= 1 << (b % 8)
            s = db.get_message_by_name("MO_Msg").get_signal_by_name(name)
            val = msg.decode(bytes(d2), decode_choices=False, scaling=False)[name]
            assert val == (1 << i), (name, i, b, val)   # bit b is exactly value bit i


def test_scan_fields(dbc_file):
    d = scan_dbc(dbc_file)
    m = d["messages"]["ESP_21"]
    assert m["frame_id"] == 1648 and m["dlc"] == 8 and m["senders"] == ["ESP"]
    sp = next(s for s in m["signals"] if s["name"] == "ESP_Speed")
    assert (sp["start"], sp["length"], sp["factor"], sp["unit"]) == (16, 16, 0.01, "km/h")
    assert sp["comment"] == "Vehicle speed"
    mode = next(s for s in m["signals"] if s["name"] == "ESP_Mode")
    assert mode["choices"] == {0: "Off", 1: "On", 2: "Error"}


def test_join_whole_bus(dbc_file):
    nodes, edges = [], []
    st = join_dbc(nodes, edges, scan_dbc(dbc_file), "t.dbc")
    assert st["messages"] == 4 and st["signals"] == 7 and not st["overlaps"]
    sig = next(n for n in nodes if n["label"] == "MO_Torque")
    assert sig["type"] == "can_signal" and sig["metadata"]["message"] == "MO_Msg" and sig["metadata"]["unit"] == "Nm"
    assert sum(e["relation"] == "has_signal" for e in edges) == 7
    assert join_dbc(nodes, edges, scan_dbc(dbc_file), "t.dbc")["signals"] == 0    # idempotent


def test_ecu_filter_direction_attrs(dbc_file):
    d = scan_dbc(dbc_file, "TCU")
    assert {m: v["direction"] for m, v in d["messages"].items()} == {
        "ESP_21": "RX", "MO_Msg": "-", "TCU_Msg": "TX", "Other_Msg": "-"}
    nodes, edges = [], []
    st = join_dbc(nodes, edges, d, "t.dbc")
    msgs = {n["label"] for n in nodes if n["type"] == "can_message"}
    assert msgs == {"ESP_21", "TCU_Msg"} and (st["TX"], st["RX"], st["unrelated"]) == (1, 1, 2)
    assert {n["type"] for n in nodes} >= {"can_dbc", "can_node", "can_message", "can_signal"}
    rel = {(e["relation"]) for e in edges}
    assert {"defined_in", "sends", "has_signal", "receives"} <= rel
    recv = [e for e in edges if e["relation"] == "receives"]
    assert len(recv) == 1 and recv[0]["target"].endswith("esp_mode")          # only the signal TCU receives
    esp = next(n for n in nodes if n["label"] == "ESP_21")
    assert esp["metadata"]["attr:GenMsgCycleTime"] == 20 and esp["metadata"]["node_kind"] == "CanMessage"


def test_overlap_reported(tmp_path):
    p = tmp_path / "o.dbc"
    p.write_text(DBC.replace("1|3@1+", "0|3@1+"), encoding="utf-8")
    st = join_dbc([], [], scan_dbc(str(p)), "o.dbc")
    assert st["overlaps"]


# ---- CAN chapter in the documentation (Obsidian, single file, html)

def _graph_raw(dbc_file, ecu="TCU"):
    nodes = [{"id": "f1", "label": "Kku_F()", "type": "function", "source_file": "ku/kku/kku.c", "metadata": {}}]
    edges = []
    join_dbc(nodes, edges, scan_dbc(dbc_file, ecu), "t.dbc", node_filter=bool(ecu))
    return {"nodes": nodes, "edges": edges}


def test_can_notes_and_single_file(dbc_file):
    from graphify.swdoc import build_vault, can_notes, single_file
    res = build_vault(_graph_raw(dbc_file), ["ku/kku"])
    assert res["can"]["ecu"] == "TCU" and [m["name"] for m in res["can"]["messages"]] == ["TCU_Msg", "ESP_21"]   # TX first
    notes = can_notes(res["can"])
    assert set(notes) == {"can/CAN.md", "can/messages/ESP_21.md", "can/messages/TCU_Msg.md"}
    esp = notes["can/messages/ESP_21.md"]
    assert "`0x670`" in esp and "0=Off; 1=On; 2=Error" in esp and "**TCU**" in esp and "km/h" in esp and "0.01 · 0" in esp
    assert "[[ESP_21]]" in notes["can/CAN.md"] and "Received by the ECU (RX)" in notes["can/CAN.md"]
    text = single_file(res)
    assert "## CAN messages" in text and "### ESP_21" in text and "[[#ESP_21]]" in text and "[[CAN]]" not in text


def test_can_html(dbc_file):
    from graphify.swdoc import build_vault
    from graphify.swdoc_html import build_data, render
    res = build_vault(_graph_raw(dbc_file), ["ku/kku"], want_notes=False)
    d = build_data(res, "T")
    assert [m["n"] for m in d["can"]["m"]] == ["TCU_Msg", "ESP_21"]
    html = render(d)
    assert "pageCan" in html and "ESP_Mode" in html


def test_no_dbc_no_can_chapter():
    from graphify.swdoc import build_vault, single_file
    raw = {"nodes": [{"id": "f1", "label": "F()", "type": "function", "source_file": "a/a.c", "metadata": {}}], "edges": []}
    res = build_vault(raw, ["a"])
    assert res["can"] is None and "CAN" not in single_file(res)
