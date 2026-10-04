"""Single registry of relation names beyond Graphify's tree-sitter vocabulary.

Several modules used to hard-code relation names (``affected.py`` defaults,
``serve.py`` question hints, intent words, context filters, ``analyze.py``).
Enrichment passes (the clang C pass, the A2L join) add relations such as
``reads_var`` or ``reads_calibration_field``; keeping them in ONE table means a
new relation is described once and every consumer picks it up.

This module is dependency-free on purpose (imported by extractors, ``serve``,
``affected``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationInfo:
    name: str
    context: str | None  # value written to edge["context"] (drives query context filters)
    kind: str  # read | write | readwrite | calibration | axis | a2l | call | other
    description: str
    hints: tuple = ()  # question words that select this relation's context
    affected: bool = False  # included in the default `graphify affected` traversal


_R = RelationInfo

RELATIONS: dict[str, RelationInfo] = {r.name: r for r in (
    _R("reads_var", "read", "read", "function reads a global variable",
       ("read", "reads", "reader", "readers", "reading"), True),
    _R("writes_var", "write", "write", "function writes a global variable",
       ("write", "writes", "written", "writer", "writers", "writing", "assign", "assigns", "assigned"), True),
    _R("reads_writes_var", "readwrite", "readwrite",
       "function both reads and writes the same global variable (one edge, because the graph keeps one edge per node pair)",
       (), True),
    _R("reads_calibration_field", "calibration", "calibration",
       "function reads a calibration (applicative) field",
       ("calibration", "calibrations", "calibrated", "applicative", "tunable"), True),
    _R("exposes_calibration_field", "calibration", "calibration",
       "calibration pointer variable exposes a calibration field", (), True),
    _R("x_axis", "axis", "axis", "map/curve to its X axis calibration", ("axis", "axes", "breakpoint", "breakpoints"), True),
    _R("y_axis", "axis", "axis", "map to its Y axis calibration", (), True),
    _R("z_axis", "axis", "axis", "cuboid to its Z axis calibration", (), True),
    _R("input_x", "axis", "axis", "map/curve to the variable feeding its X input", (), True),
    _R("input_y", "axis", "axis", "map to the variable feeding its Y input", (), True),
    _R("input_z", "axis", "axis", "cuboid to the variable feeding its Z input", (), True),
    _R("length_x", "axis", "axis", "map/curve to its X axis length", (), False),
    _R("length_y", "axis", "axis", "map to its Y axis length", (), False),
    _R("length_z", "axis", "axis", "cuboid to its Z axis length", (), False),
    _R("mapped_to_a2l_characteristic", "a2l", "a2l", "code symbol to its A2L CHARACTERISTIC",
       ("a2l", "asap2"), False),
    _R("mapped_to_a2l_axis_pts", "a2l", "a2l", "code symbol to its A2L AXIS_PTS", (), False),
    _R("x_axis_reference", "a2l", "a2l", "A2L characteristic to its X AXIS_PTS", (), False),
    _R("y_axis_reference", "a2l", "a2l", "A2L characteristic to its Y AXIS_PTS", (), False),
    _R("axis_reference", "a2l", "a2l", "A2L curve to its AXIS_PTS", (), False),
)}

# A filter on one of these contexts must also keep edges of the listed contexts
# (a "readwrite" edge answers both "who reads X" and "who writes X").
CONTEXT_IMPLIES: dict[str, tuple] = {
    "read": ("readwrite",),
    "write": ("readwrite",),
}


def context_for(relation: str) -> str | None:
    info = RELATIONS.get(relation)
    return info.context if info else None


def affected_relations() -> tuple:
    """Relation names the default `affected` traversal should follow (enrichment ones)."""
    return tuple(r.name for r in RELATIONS.values() if r.affected)


def context_hints() -> tuple:
    """((context, (hint words...)), ...) for question-word -> context-filter inference."""
    by_context: dict[str, list] = {}
    for r in RELATIONS.values():
        if r.context and r.hints:
            by_context.setdefault(r.context, []).extend(r.hints)
    return tuple((ctx, tuple(dict.fromkeys(words))) for ctx, words in by_context.items())


def intent_terms() -> frozenset:
    """Verb-shaped question words that name a relation, not a symbol to look up."""
    words = set()
    for r in RELATIONS.values():
        if r.kind in ("read", "write", "readwrite"):
            words.update(r.hints)
    words.update({"set", "sets", "modified", "modifies", "modify"})
    return frozenset(words)


# ---- presentation helpers (explain / get_node) ------------------------------

_PRIORITY = {"write": 0, "readwrite": 0, "read": 1, "calibration": 2, "axis": 3, "a2l": 4}


def relation_priority(relation: str) -> int:
    """Sort key for `explain`: writes, reads, calibration, axis, a2l first; everything else after."""
    return _PRIORITY.get(context_for(relation) or "", 9)


_META_KEYS = (
    ("calibration_path", "Calibration path"), ("calibration_ref", "Calibration ref"),
    ("a2l_characteristic_name", "A2L characteristic"), ("a2l_axis_pts_name", "A2L axis"),
    ("a2l_range", "Range"), ("bit_mask", "Bit mask"), ("accessed_via", "Accessed via"),
    ("return_type", "Returns"),
)


def metadata_lines(node: dict, indent: str = "  ") -> list:
    """Printable enrichment facts for a node: kind, A2L link, range, parameters, ..."""
    out = []
    if node.get("type"):
        out.append(f"{indent}Kind:      {node['type']}")
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    for key, label in _META_KEYS:
        if meta.get(key) not in (None, ""):
            out.append(f"{indent}{label + ':':<10} {meta[key]}")
    params = meta.get("parameters")
    if isinstance(params, list) and params:
        shown = ", ".join(f"{p.get('type', '')} {p.get('name', '')} ({p.get('role', '')})".strip()
                          for p in params[:8] if isinstance(p, dict))
        out.append(f"{indent}Params:    {shown}")
    return out
