"""A2L facts of a code node, found through its links (single source of truth).

A calibration node in the graph holds code facts only. Everything about the A2L entry (limits, type,
axes, description) lives on the ``a2l_characteristic`` / ``a2l_axis`` node; the edge
``mapped_to_a2l_characteristic`` / ``mapped_to_a2l_axis_pts`` carries the link facts (index, mask, via,
suffix, and the A2L name as text). This module collects them for whoever needs to print or look up
values. Dependency-free on purpose.
"""
from __future__ import annotations

LINK_RELATIONS = {"mapped_to_a2l_characteristic": "char", "mapped_to_a2l_axis_pts": "axis"}


def code_links(nodes, edges) -> dict:
    """code node id -> list of links ``{name, kind, index, mask, via, min, max, char_type, id}`` (kind = char | axis).

    ``nodes``: iterable of node dicts, or a dict id -> node dict. ``edges``: iterable of edge dicts
    (``source``/``target``/``relation``/``metadata``)."""
    by_id = nodes if isinstance(nodes, dict) else {n["id"]: n for n in nodes if n.get("id")}
    out: dict = {}
    for e in edges:
        kind = LINK_RELATIONS.get(e.get("relation"))
        if kind is None:
            continue
        em = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        tgt = by_id.get(e.get("target")) or {}
        tm = tgt.get("metadata") if isinstance(tgt.get("metadata"), dict) else {}
        name = em.get("characteristic") or em.get("axis") or tgt.get("label")
        if not name:
            continue
        out.setdefault(e.get("source"), []).append({
            "name": name, "kind": kind, "index": em.get("index", ""), "mask": em.get("mask"),
            "via": em.get("via", ""), "min": tm.get("min"), "max": tm.get("max"),
            "char_type": tm.get("char_type", ""), "id": e.get("target")})
    return out


def link_names(links: list, kind: str = "char") -> list:
    return sorted({l["name"] for l in links or [] if l["kind"] == kind})


def single_key(links: list):
    """The A2L name to look a value up by: the only characteristic, else the only axis, else None."""
    chars, axes = link_names(links, "char"), link_names(links, "axis")
    if len(chars) == 1:
        return chars[0]
    if len(axes) == 1:
        return axes[0]
    return None


def link_range(links: list) -> str:
    """`lo..hi` over the linked characteristics (same text as the former ``a2l_range``), or ''."""
    los = [l["min"] for l in links or [] if l["kind"] == "char" and l["min"] is not None]
    his = [l["max"] for l in links or [] if l["kind"] == "char" and l["max"] is not None]
    return f"{min(los)}..{max(his)}" if los and his else ""


def graph_links(G, nid) -> list:
    """Links of one code node in a loaded graph (networkx Graph, DiGraph or Multi*). A2L nodes give []."""
    if G.nodes[nid].get("type") in ("a2l_characteristic", "a2l_axis"):
        return []
    edges = []
    for u, v, data in G.edges(nid, data=True):
        if data.get("relation") in LINK_RELATIONS:
            edges.append({"source": nid, "target": v if u == nid else u, "relation": data["relation"],
                          "metadata": data.get("metadata")})
    if not edges:
        return []
    return code_links({e["target"]: G.nodes[e["target"]] | {"id": e["target"]} for e in edges}, edges).get(nid, [])
