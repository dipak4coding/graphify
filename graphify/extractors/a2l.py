"""ASAP2 (.a2l) calibration description support.

Ported from the user's ``a2l_extractor.py`` (itself a port of ScanDKSW's
``scanA2L()``) and ``merge_into_graphify.apply_a2l``. The line-based state
machine is kept deliberately close to the original, quirks included
(LINK_MAP-derived measurement names, ``[BITMASK]`` suffixes, ``_`` suffix on
name collisions, multi-line descriptions, whole-table trailing-index stripping).

Two public entry points:

* :func:`scan_a2l` - parse a ``.a2l`` file into plain dicts.
* :func:`join_a2l` - join the parsed data onto C symbols already in the graph
  (variables / calibration fields produced by the clang pass), adding
  ``calibration_ref`` metadata, searchable ``rationale`` text, and A2L
  characteristic / axis nodes.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from graphify.ids import make_id

# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class Measurement:
    description: str = ""
    A2LName: str = ""
    VarType: str = ""
    BitMask: str = ""
    Compu_Method: str = ""
    MinValue: float = float("-inf")
    MaxValue: float = float("inf")
    a2l_characteristic_name: str = ""


@dataclass
class CompuMethod:
    description: str = ""
    unit: str = ""
    type: str = "UNKNOWN"  # IDENTICAL | LINEAR | TAB_VERB | UNKNOWN
    factor: float = 1.0
    offset: float = 0.0
    compu_vtab: str = ""


@dataclass
class CompuVtab:
    description: str = ""
    content: dict = field(default_factory=dict)


def canonical_bitmask(raw: str):
    """BIT_MASK text (``0x02``, ``4``) -> canonical DECIMAL string, or None."""
    if not raw:
        return None
    try:
        return str(int(raw, 0))
    except ValueError:
        return None


@dataclass
class Characteristic:
    description: str = ""
    char_type: str = ""  # VALUE | CURVE | MAP | CUBOID | ...
    address: str = ""
    record_layout: str = ""
    max_diff: str = ""
    compu_method: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    symbol_link: str = ""
    link_map_symbol: str = ""
    axis_references: list = field(default_factory=list)
    bit_mask: str = ""

    @property
    def internal_symbol(self) -> str:
        base = self.symbol_link or self.link_map_symbol
        if not base:
            return base
        mask = canonical_bitmask(self.bit_mask)
        return f"{base}#{mask}" if mask is not None else base

    @property
    def symbol_mismatch(self) -> bool:
        return bool(self.symbol_link and self.link_map_symbol and self.symbol_link != self.link_map_symbol)


@dataclass
class AxisPts:
    description: str = ""
    address: str = ""
    input_quantity: str = ""
    record_layout: str = ""
    max_diff: str = ""
    compu_method: str = ""
    max_axis_points: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    symbol_link: str = ""
    link_map_symbol: str = ""

    @property
    def internal_symbol(self) -> str:
        return self.symbol_link or self.link_map_symbol

    @property
    def symbol_mismatch(self) -> bool:
        return bool(self.symbol_link and self.link_map_symbol and self.symbol_link != self.link_map_symbol)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def kill_special_characters(s: str) -> str:
    """Drop every backslash-escaped pair (backslash and the char after it)."""
    while True:
        i = s.find("\\")
        if i < 0:
            return s
        s = s[:i] + s[i + 2:]


def patch_index(s: str) -> str:
    """CANape ``._0_._1_`` array notation -> ``[0][1]``."""
    result = []
    in_brace = False
    while s:
        if in_brace:
            if s.startswith("_._"):
                result.append("][")
                s = s[3:]
            elif s.startswith("_"):
                result.append("]")
                in_brace = False
                s = s[1:]
            else:
                result.append(s[0])
                s = s[1:]
        elif s.startswith("._"):
            result.append("[")
            in_brace = True
            s = s[2:]
        else:
            result.append(s[0])
            s = s[1:]
    return "".join(result)


# For these types only ONE address (the table base) is defined in the A2L, so a
# trailing "[0]" on the symbol is an export artefact, not real addressing.
_WHOLE_TABLE_TYPES = {"CURVE", "MAP", "CUBOID", "CUBE_4", "CUBE_5"}
_TRAILING_INDEX_RE = re.compile(r"(\[\d+\])+$")


def strip_trailing_indices(symbol: str) -> str:
    return _TRAILING_INDEX_RE.sub("", symbol)


def _extract_quoted(line: str) -> Optional[str]:
    f = line.find('"')
    l = line.rfind('"')
    if l > f >= 0:
        return line[f + 1:l]
    return None


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def scan_a2l(a2l_path: str) -> dict:
    """Parse an ASAP2 file. Returns dict of dicts: measurements, compu_methods,
    compu_vtabs, characteristics, axis_pts."""
    measurements: dict = {}
    compu_methods: dict = {}
    compu_vtabs: dict = {}
    characteristics: dict = {}
    axis_pts: dict = {}

    vtab_content = None
    vtab_name = ""
    vtab_descript = ""

    in_compu_method = False
    cm_name = ""
    cm = CompuMethod()

    in_measurement = 0  # 0 outside, 2 just opened, 1 inside body
    msrmnt = Measurement()

    in_characteristic = 0
    chr_name = ""
    chr_obj = Characteristic()
    in_axis_descr = False

    in_axis_pts = 0
    axp_name = ""
    axp_obj = AxisPts()

    pending_description_for = None
    pending_desc_buffer = None

    with open(a2l_path, "r", encoding="ISO-8859-1", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            if pending_description_for is not None:
                if pending_desc_buffer is None:
                    qpos = line.find('"')
                    if qpos < 0:
                        pending_description_for = None
                    else:
                        q = _extract_quoted(line)
                        if q is not None:
                            descript = kill_special_characters(q)
                            if pending_description_for == "characteristic":
                                chr_obj.description = descript
                            elif pending_description_for == "axis_pts":
                                axp_obj.description = descript
                            pending_description_for = None
                            continue
                        pending_desc_buffer = [line[qpos + 1:]]
                        continue
                else:
                    qpos = line.find('"')
                    if qpos >= 0:
                        pending_desc_buffer.append(line[:qpos])
                        full = kill_special_characters(" ".join(s for s in pending_desc_buffer if s))
                        if pending_description_for == "characteristic":
                            chr_obj.description = full
                        elif pending_description_for == "axis_pts":
                            axp_obj.description = full
                        pending_description_for = None
                        pending_desc_buffer = None
                        continue
                    pending_desc_buffer.append(line)
                    continue

            # ---- COMPU_VTAB ----
            if vtab_content is None:
                if line.startswith("/begin COMPU_VTAB"):
                    body = line[17:].strip()
                    sp = body.find(" ")
                    vtab_name = body[:sp] if sp >= 0 else body
                    q = _extract_quoted(body)
                    vtab_descript = kill_special_characters(q) if q is not None else ""
                    vtab_content = {}
            else:
                if line.startswith("/end COMPU_VTAB"):
                    compu_vtabs.setdefault(vtab_name, CompuVtab(description=vtab_descript, content=vtab_content))
                    vtab_content = None
                else:
                    n = line.find(" ")
                    if n >= 0:
                        key = line[:n]
                        q = _extract_quoted(line)
                        vtab_content[key] = kill_special_characters(q) if q is not None else ""

            # ---- COMPU_METHOD ----
            if not in_compu_method:
                if line.startswith("/begin COMPU_METHOD"):
                    body = line[20:].strip()
                    sp = body.find(" ")
                    cm_name = body[:sp].strip() if sp >= 0 else body
                    q = _extract_quoted(body)
                    cm = CompuMethod(description=kill_special_characters(q) if q is not None else "")
                    in_compu_method = True
            else:
                if line.startswith("/end COMPU_METHOD"):
                    compu_methods.setdefault(cm_name, cm)
                    in_compu_method = False
                elif line.startswith(("TAB_VERB", "IDENTICAL", "LINEAR")):
                    if line.startswith("TAB_VERB"):
                        q_len, cm.type = 8, "TAB_VERB"
                    elif line.startswith("IDENTICAL"):
                        q_len, cm.type = 9, "IDENTICAL"
                    else:
                        q_len, cm.type = 6, "LINEAR"
                    rest = line[q_len:].strip()
                    n = rest.find(" ")
                    if n >= 0:
                        rest = rest[n + 1:].strip()
                    if rest.startswith('"') and rest.endswith('"'):
                        cm.unit = rest[1:-1]
                elif line.startswith("COEFFS_LINEAR"):
                    rest = line[13:].strip()
                    n = rest.find(" ")
                    if n >= 0:
                        try:
                            cm.factor = float(rest[:n].strip())
                            cm.offset = float(rest[n + 1:].strip())
                        except ValueError:
                            pass
                elif line.startswith("COMPU_TAB_REF"):
                    n = line.find(" ")
                    if n >= 0:
                        cm.compu_vtab = line[n + 1:].strip()

            # ---- MEASUREMENT ----
            if in_measurement == 0:
                if line.startswith("/begin MEASUREMENT"):
                    body = line[len("/begin MEASUREMENT"):].strip()
                    sp = body.find(" ")
                    header_name = body[:sp].strip() if sp >= 0 else body.strip()
                    q = _extract_quoted(line)
                    msrmnt = Measurement(
                        description=kill_special_characters(q) if q is not None else "",
                        a2l_characteristic_name=header_name,
                    )
                    in_measurement = 2
            else:
                if in_measurement == 2:
                    parts = line.split(" ")
                    if len(parts) >= 6:
                        try:
                            msrmnt.VarType = parts[0]
                            msrmnt.Compu_Method = parts[1]
                            msrmnt.MinValue = float(parts[4])
                            msrmnt.MaxValue = float(parts[5])
                        except (ValueError, IndexError):
                            pass
                    in_measurement = 1
                else:
                    if line.startswith("BIT_MASK"):
                        parts = line.split(" ")
                        if len(parts) >= 2:
                            msrmnt.BitMask = parts[1]
                    elif line.startswith("LINK_MAP"):
                        q = _extract_quoted(line)
                        if q is not None:
                            name = patch_index(q)
                            msrmnt.A2LName = name
                            if (
                                name in measurements
                                and msrmnt.MinValue == 0
                                and msrmnt.MaxValue == 1
                                and msrmnt.BitMask != ""
                            ):
                                name = f"{name}[{msrmnt.BitMask}]"
                            while name in measurements:
                                name += "_"
                            measurements[name] = msrmnt
                    elif line.startswith("/end MEASUREMENT"):
                        in_measurement = 0

            # ---- CHARACTERISTIC ----
            if in_characteristic == 0:
                if line.startswith("/begin CHARACTERISTIC"):
                    body = line[len("/begin CHARACTERISTIC"):].strip()
                    sp = body.find(" ")
                    chr_name = body[:sp].strip() if sp >= 0 else body
                    q = _extract_quoted(body)
                    if q is not None:
                        chr_obj = Characteristic(description=kill_special_characters(q))
                    else:
                        chr_obj = Characteristic()
                        pending_description_for = "characteristic"
                        qpos = body.find('"')
                        pending_desc_buffer = [body[qpos + 1:]] if qpos >= 0 else None
                    in_axis_descr = False
                    in_characteristic = 2
            else:
                if in_characteristic == 2:
                    parts = line.split(" ")
                    if len(parts) >= 7:
                        chr_obj.char_type = parts[0]
                        chr_obj.address = parts[1]
                        chr_obj.record_layout = parts[2]
                        chr_obj.max_diff = parts[3]
                        chr_obj.compu_method = parts[4]
                        try:
                            chr_obj.min_value = float(parts[5])
                            chr_obj.max_value = float(parts[6])
                        except ValueError:
                            pass
                    in_characteristic = 1
                else:
                    if line.startswith("/begin AXIS_DESCR"):
                        in_axis_descr = True
                    elif line.startswith("/end AXIS_DESCR"):
                        in_axis_descr = False
                    elif in_axis_descr and line.startswith("AXIS_PTS_REF"):
                        parts = line.split(" ")
                        if len(parts) >= 2:
                            chr_obj.axis_references.append(parts[1].strip())
                    elif line.startswith("BIT_MASK"):
                        parts = line.split(" ")
                        if len(parts) >= 2:
                            chr_obj.bit_mask = parts[1].strip()
                    elif line.startswith("SYMBOL_LINK"):
                        q = _extract_quoted(line)
                        if q is not None:
                            symbol = patch_index(q)
                            if chr_obj.char_type in _WHOLE_TABLE_TYPES:
                                symbol = strip_trailing_indices(symbol)
                            chr_obj.symbol_link = symbol
                    elif line.startswith("LINK_MAP"):
                        q = _extract_quoted(line)
                        if q is not None:
                            symbol = patch_index(q)
                            if chr_obj.char_type in _WHOLE_TABLE_TYPES:
                                symbol = strip_trailing_indices(symbol)
                            chr_obj.link_map_symbol = symbol
                    elif line.startswith("/end CHARACTERISTIC"):
                        name = chr_name
                        while name in characteristics:
                            name += "_"
                        characteristics[name] = chr_obj
                        in_characteristic = 0

            # ---- AXIS_PTS ----
            if in_axis_pts == 0:
                if line.startswith("/begin AXIS_PTS"):
                    body = line[len("/begin AXIS_PTS"):].strip()
                    sp = body.find(" ")
                    axp_name = body[:sp].strip() if sp >= 0 else body
                    q = _extract_quoted(body)
                    if q is not None:
                        axp_obj = AxisPts(description=kill_special_characters(q))
                    else:
                        axp_obj = AxisPts()
                        pending_description_for = "axis_pts"
                        qpos = body.find('"')
                        pending_desc_buffer = [body[qpos + 1:]] if qpos >= 0 else None
                    in_axis_pts = 2
            else:
                if in_axis_pts == 2:
                    parts = line.split(" ")
                    if len(parts) >= 8:
                        axp_obj.address = parts[0]
                        axp_obj.input_quantity = parts[1]
                        axp_obj.record_layout = parts[2]
                        axp_obj.max_diff = parts[3]
                        axp_obj.compu_method = parts[4]
                        axp_obj.max_axis_points = parts[5]
                        try:
                            axp_obj.min_value = float(parts[6])
                            axp_obj.max_value = float(parts[7])
                        except ValueError:
                            pass
                    in_axis_pts = 1
                else:
                    if line.startswith("SYMBOL_LINK"):
                        q = _extract_quoted(line)
                        if q is not None:
                            axp_obj.symbol_link = strip_trailing_indices(patch_index(q))
                    elif line.startswith("LINK_MAP"):
                        q = _extract_quoted(line)
                        if q is not None:
                            axp_obj.link_map_symbol = strip_trailing_indices(patch_index(q))
                    elif line.startswith("/end AXIS_PTS"):
                        name = axp_name
                        while name in axis_pts:
                            name += "_"
                        axis_pts[name] = axp_obj
                        in_axis_pts = 0

    return {
        "measurements": {k: asdict(v) for k, v in measurements.items()},
        "compu_methods": {k: asdict(v) for k, v in compu_methods.items()},
        "compu_vtabs": {k: asdict(v) for k, v in compu_vtabs.items()},
        "characteristics": {
            k: {**asdict(v), "internal_symbol": v.internal_symbol, "symbol_mismatch": v.symbol_mismatch}
            for k, v in characteristics.items()
        },
        "axis_pts": {
            k: {**asdict(v), "internal_symbol": v.internal_symbol, "symbol_mismatch": v.symbol_mismatch}
            for k, v in axis_pts.items()
        },
    }


# --------------------------------------------------------------------------
# Join onto graph nodes
# --------------------------------------------------------------------------

MAX_IMPLICIT_INDEX_DEPTH = 4  # up to a 4D table


def resolve_a2l_key(join_key: str, table: dict):
    """Exact key first, then APPEND ``[0]``... (whole-table code access vs indexed
    A2L symbol), then STRIP trailing indices (loop-expanded code access vs bare
    A2L table symbol). Returns ``(key, used_fallback)`` or ``(None, False)``."""
    if join_key in table:
        return join_key, False
    candidate = join_key
    for _ in range(MAX_IMPLICIT_INDEX_DEPTH):
        candidate += "[0]"
        if candidate in table:
            return candidate, True
    stripped = _TRAILING_INDEX_RE.sub("", join_key)
    if stripped != join_key and stripped in table:
        return stripped, True
    return None, False


def resolve_calibration_ref(measurement: dict, compu_methods: dict, compu_vtabs: dict) -> dict:
    ref = {
        "a2l_name": measurement.get("A2LName", ""),
        "var_type": measurement.get("VarType", ""),
        "min": measurement.get("MinValue"),
        "max": measurement.get("MaxValue"),
    }
    if measurement.get("BitMask"):
        ref["bit_mask"] = measurement["BitMask"]
    cm = compu_methods.get(measurement.get("Compu_Method", ""))
    if cm:
        ref["compu_method"] = {"type": cm.get("type"), "unit": cm.get("unit")}
        if cm.get("type") == "LINEAR":
            ref["compu_method"]["factor"] = cm.get("factor")
            ref["compu_method"]["offset"] = cm.get("offset")
        elif cm.get("type") == "TAB_VERB" and cm.get("compu_vtab"):
            vtab = compu_vtabs.get(cm["compu_vtab"])
            if vtab:
                ref["verbal_table"] = vtab.get("content", {})
    return ref


def _axis_relation_name(char_type: str, index: int) -> str:
    if char_type == "MAP":
        if index == 0:
            return "x_axis_reference"
        if index == 1:
            return "y_axis_reference"
        return f"axis_reference_{index}"
    return "axis_reference"


def _symbol_index(table: dict) -> dict:
    return {v["internal_symbol"]: (k, v) for k, v in table.items() if v.get("internal_symbol")}


def _add_rationale(node: dict, text: str) -> None:
    """Make A2L text searchable: ``rationale`` is part of the query search text."""
    text = (text or "").strip()
    if not text:
        return
    old = (node.get("rationale") or "").strip()
    if text in old:
        return
    node["rationale"] = f"{old} | {text}" if old else text


def _fmt_range(lo, hi) -> str:
    if lo is None or hi is None:
        return ""
    return f"{lo}..{hi}"


def join_a2l(nodes: list, edges: list, a2l: dict, a2l_source_file: str) -> dict:
    """Join parsed A2L data onto graph nodes (in place).

    Only nodes of ``type`` variable / calibration_field are considered. The join
    key is ``metadata.calibration_path`` (the technical symbol path) with the
    node label as fallback. Adds A2L characteristic / axis nodes and edges, writes
    ``metadata.calibration_ref`` and makes the A2L description searchable via
    ``rationale``. Returns match statistics.
    """
    measurements = a2l.get("measurements", {})
    compu_methods = a2l.get("compu_methods", {})
    compu_vtabs = a2l.get("compu_vtabs", {})
    characteristics = a2l.get("characteristics", {})
    axis_pts = a2l.get("axis_pts", {})
    sym_to_char = _symbol_index(characteristics)
    sym_to_axis = _symbol_index(axis_pts)

    by_id = {n["id"]: n for n in nodes if n.get("id")}
    seen_edges = {(e.get("source"), e.get("target"), e.get("relation")) for e in edges}
    stats = {"measurements": 0, "characteristics": 0, "axis_pts": 0, "fallback": 0}

    def _edge(src, tgt, relation):
        key = (src, tgt, relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({
            "source": src, "target": tgt, "relation": relation, "confidence": "EXTRACTED",
            "source_file": a2l_source_file, "weight": 1.0, "context": "a2l",
        })

    def _axis_node(axis_name: str, axp: Optional[dict]) -> str:
        axis_id = make_id("a2laxis", axis_name)
        if axis_id not in by_id:
            meta = {"source_extractor": "a2l"}
            node = {
                "id": axis_id, "label": axis_name, "file_type": "document", "type": "a2l_axis",
                "source_file": a2l_source_file, "metadata": meta,
            }
            if axp:
                meta.update({
                    "min": axp.get("min_value"), "max": axp.get("max_value"),
                    "max_axis_points": axp.get("max_axis_points"),
                    "internal_symbol": axp.get("internal_symbol", ""),
                })
                _add_rationale(node, axp.get("description", ""))
            nodes.append(node)
            by_id[axis_id] = node
        return axis_id

    for node in [n for n in nodes if n.get("type") in ("variable", "calibration_field")]:
        meta = node.setdefault("metadata", {})
        join_key = meta.get("calibration_path") or node.get("label")
        if not join_key:
            continue

        mkey, mfb = resolve_a2l_key(join_key, measurements)
        if mkey is not None:
            m = measurements[mkey]
            meta["calibration_ref"] = resolve_calibration_ref(m, compu_methods, compu_vtabs)
            _add_rationale(node, m.get("description", ""))
            if mfb:
                meta["a2l_implicit_index_fallback"] = True
                stats["fallback"] += 1
            stats["measurements"] += 1

        ckey, cfb = resolve_a2l_key(join_key, sym_to_char)
        if ckey is not None:
            cname, cobj = sym_to_char[ckey]
            meta["a2l_characteristic_name"] = cname
            if cfb:
                meta["a2l_implicit_index_fallback"] = True
                stats["fallback"] += 1
            char_id = make_id("a2lchar", cname)
            if char_id not in by_id:
                cnode = {
                    "id": char_id, "label": cname, "file_type": "document", "type": "a2l_characteristic",
                    "source_file": a2l_source_file,
                    "metadata": {
                        "source_extractor": "a2l",
                        "char_type": cobj.get("char_type", ""),
                        "min": cobj.get("min_value"), "max": cobj.get("max_value"),
                        "internal_symbol": ckey,
                    },
                }
                _add_rationale(cnode, cobj.get("description", ""))
                nodes.append(cnode)
                by_id[char_id] = cnode
                for i, axis_name in enumerate(cobj.get("axis_references") or []):
                    axis_id = _axis_node(axis_name, axis_pts.get(axis_name))
                    _edge(char_id, axis_id, _axis_relation_name(cobj.get("char_type", ""), i))
            # description also on the code node, so "oil temperature threshold" finds it
            _add_rationale(node, cobj.get("description", ""))
            rng = _fmt_range(cobj.get("min_value"), cobj.get("max_value"))
            if rng:
                meta["a2l_range"] = rng
            _edge(node["id"], char_id, "mapped_to_a2l_characteristic")
            stats["characteristics"] += 1

        akey, afb = resolve_a2l_key(join_key, sym_to_axis)
        if akey is not None:
            aname, aobj = sym_to_axis[akey]
            meta["a2l_axis_pts_name"] = aname
            if afb:
                meta["a2l_implicit_index_fallback"] = True
                stats["fallback"] += 1
            axis_id = _axis_node(aname, aobj)
            _add_rationale(node, aobj.get("description", ""))
            _edge(node["id"], axis_id, "mapped_to_a2l_axis_pts")
            stats["axis_pts"] += 1

    stats["totals"] = {
        "measurements": len(measurements), "characteristics": len(characteristics), "axis_pts": len(axis_pts),
    }
    return stats
