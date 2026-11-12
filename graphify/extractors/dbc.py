"""CAN matrix (.dbc) support.

Two public entry points (same shape as ``a2l``):

* :func:`scan_dbc` - parse a ``.dbc`` file (via ``cantools``) into plain dicts.
* :func:`join_dbc` - add ``can_message`` / ``can_signal`` nodes (+ ``can_has_signal`` edges) to the graph.

The DBC is the only source of CAN names, bit layout and scaling. Nothing is taken from C comments.
Every signal carries ``bits`` = the physical message bit positions it occupies (``byte*8 + bit`` with
bit 0 = LSB of the byte), an unambiguous layout for both byte orders; the code side (config table)
is later compared against it, see :func:`bits_of`.
"""
from __future__ import annotations

from graphify.ids import make_id

EXTENDED_FLAG = 0x80000000


def _load(path: str):
    try:
        import cantools
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DBC support needs the 'cantools' package: pip install cantools") from exc
    last = None
    for enc in ("utf-8", "cp1252"):
        try:
            return cantools.database.load_file(path, database_format="dbc", encoding=enc, strict=False)
        except UnicodeDecodeError as exc:
            last = exc
    raise last  # pragma: no cover


def bits_of(start: int, length: int, byte_order: str) -> list:
    """Physical bit positions (byte*8+bit) of a signal, LSB first.

    ``start`` is the DBC start bit: Intel (``little_endian``) = position of the LSB; Motorola
    (``big_endian``) = position of the MSB in the DBC "sawtooth" numbering (bit 7 of byte 0 is 7,
    bit 0 of byte 0 is 0, then byte 1 starts at 15 ... 8 -> next byte).
    """
    if byte_order == "little_endian":
        return list(range(start, start + length))
    out, byte, bit = [], start // 8, start % 8
    for _ in range(length):                      # MSB first
        out.append(byte * 8 + bit)
        if bit == 0:
            byte, bit = byte + 1, 7
        else:
            bit -= 1
    return out[::-1]                             # LSB first


def scan_dbc(path: str) -> dict:
    """Parse a DBC into ``{"messages": {name: {...}}, "nodes": [...], "path": path}``."""
    db = _load(path)
    messages: dict = {}
    for m in db.messages:
        ext = bool(m.is_extended_frame)
        sigs = []
        for s in m.signals:
            order = s.byte_order
            mux = None
            if s.multiplexer_ids:
                mux = {"ids": list(s.multiplexer_ids), "selector": s.multiplexer_signal}
            sigs.append({
                "name": s.name, "start": s.start, "length": s.length, "byte_order": order,
                "signed": bool(s.is_signed), "float": bool(s.is_float),
                "factor": s.scale, "offset": s.offset, "min": s.minimum, "max": s.maximum,
                "unit": s.unit or "", "comment": (s.comment or "").strip(),
                "choices": {int(k): str(v) for k, v in (s.choices or {}).items()},
                "receivers": list(s.receivers or []),
                "is_multiplexer": bool(s.is_multiplexer), "mux": mux,
                "bits": bits_of(s.start, s.length, order),
            })
        messages[m.name] = {
            "name": m.name, "frame_id": m.frame_id, "extended": ext, "dlc": m.length,
            "senders": list(m.senders or []), "cycle_time": m.cycle_time,
            "comment": (m.comment or "").strip(), "signals": sigs,
        }
    return {"messages": messages, "nodes": [n.name for n in db.nodes], "path": path}


def _id_text(m: dict) -> str:
    return f"0x{m['frame_id']:X}" + (" (ext)" if m["extended"] else "")


def join_dbc(nodes: list, edges: list, dbc: dict, source_file: str) -> dict:
    """Add can_message / can_signal nodes and ``can_has_signal`` edges (in place). Returns statistics."""
    by_id = {n["id"] for n in nodes if n.get("id")}
    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in edges}
    stats = {"messages": 0, "signals": 0, "overlaps": [], "duplicate_signal_names": []}
    names: dict = {}
    for mname, m in dbc["messages"].items():
        mid = make_id("canmsg", mname)
        if mid not in by_id:
            node = {"id": mid, "label": mname, "file_type": "document", "type": "can_message",
                    "source_file": source_file,
                    "metadata": {"source_extractor": "dbc", "frame_id": m["frame_id"], "frame_id_hex": _id_text(m),
                                 "extended": m["extended"], "dlc": m["dlc"], "senders": m["senders"],
                                 "cycle_time_ms": m["cycle_time"], "signal_count": len(m["signals"])}}
            if m["comment"]:
                node["rationale"] = m["comment"]
            nodes.append(node)
            by_id.add(mid)
            stats["messages"] += 1
        used: dict = {}
        for s in m["signals"]:
            sid = make_id("cansig", mname, s["name"])
            names.setdefault(s["name"], []).append(mname)
            for b in s["bits"]:                      # bit overlap inside one message (not for muxed siblings)
                key = (b, tuple(s["mux"]["ids"]) if s["mux"] else None)
                if b in used and used[b][1] == key[1] and used[b][0] != s["name"]:
                    stats["overlaps"].append(f"{mname}: {s['name']} / {used[b][0]} (bit {b})")
                    break
                used.setdefault(b, (s["name"], key[1]))
            if sid in by_id:
                continue
            node = {"id": sid, "label": s["name"], "file_type": "document", "type": "can_signal",
                    "source_file": source_file,
                    "metadata": {"source_extractor": "dbc", "message": mname, "frame_id": m["frame_id"],
                                 "start_bit": s["start"], "length": s["length"], "byte_order": s["byte_order"],
                                 "bits": s["bits"], "signed": s["signed"], "float": s["float"],
                                 "factor": s["factor"], "offset": s["offset"], "min": s["min"], "max": s["max"],
                                 "unit": s["unit"], "enum": {str(k): v for k, v in s["choices"].items()},
                                 "receivers": s["receivers"], "multiplex": s["mux"],
                                 "is_multiplexer": s["is_multiplexer"]}}
            if s["comment"]:
                node["rationale"] = s["comment"]
            nodes.append(node)
            by_id.add(sid)
            stats["signals"] += 1
            if (mid, sid, "can_has_signal") not in seen:
                seen.add((mid, sid, "can_has_signal"))
                edges.append({"source": mid, "target": sid, "relation": "can_has_signal", "confidence": "EXTRACTED",
                              "source_file": source_file, "weight": 1.0, "context": "dbc"})
    stats["duplicate_signal_names"] = sorted(n for n, ms in names.items() if len(ms) > 1)[:200]
    return stats
