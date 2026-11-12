"""DBC extractor: parse, bit layout (Intel + Motorola, checked against cantools' own encoder), graph join."""
import pytest

cantools = pytest.importorskip("cantools")

from graphify.extractors.dbc import bits_of, join_dbc, scan_dbc

DBC = '''VERSION ""

NS_ :

BS_:

BU_: ESP GW

BO_ 1648 ESP_21: 8 ESP
 SG_ ESP_Speed : 16|16@1+ (0.01,0) [0|655] "km/h" GW
 SG_ ESP_Flag : 0|1@1+ (1,0) [0|1] "" GW
 SG_ ESP_Mode : 1|3@1+ (1,0) [0|7] "" GW
BO_ 304 MO_Msg: 8 GW
 SG_ MO_Torque : 7|12@0+ (0.5,-10) [-10|2038] "Nm" ESP
 SG_ MO_Gear : 27|4@0+ (1,0) [0|15] "" ESP
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


def test_join_nodes_edges(dbc_file):
    nodes, edges = [], []
    st = join_dbc(nodes, edges, scan_dbc(dbc_file), "t.dbc")
    assert st["messages"] == 2 and st["signals"] == 5 and not st["overlaps"]
    sig = next(n for n in nodes if n["label"] == "MO_Torque")
    assert sig["type"] == "can_signal" and sig["metadata"]["message"] == "MO_Msg" and sig["metadata"]["unit"] == "Nm"
    assert sum(e["relation"] == "can_has_signal" for e in edges) == 5
    # idempotent
    assert join_dbc(nodes, edges, scan_dbc(dbc_file), "t.dbc")["signals"] == 0


def test_overlap_reported(tmp_path):
    p = tmp_path / "o.dbc"
    p.write_text(DBC.replace("1|3@1+", "0|3@1+"), encoding="utf-8")
    st = join_dbc([], [], scan_dbc(str(p)), "o.dbc")
    assert st["overlaps"]
