"""
ScanDKSW-equivalent signal flow runner for graphify.

Replaces the C# ScanDKSW.exe with a Python pipeline that:
  1. Auto-detects the project config (scandksw_config.auto_detect)
  2. Scans all C files with #ifdef dead-code elimination
  3. Extracts signal define interfaces and function-level reads/writes
  4. Writes results to graphify-out/scandksw/ in graphify-compatible format

Calling convention — identical to running graphify:
    python -m graphify --signals .           # from 03_SwFunktion/
    python -m graphify --signals /path/to/03_SwFunktion

Or from Python:
    from graphify.scandksw_runner import run
    result = run(Path("03_SwFunktion"))
"""

from __future__ import annotations
import json
import time
from pathlib import Path

from .scandksw_config import ScanDKSWConfig, auto_detect, discover_all_c_files
from .extract_c_signals import (
    CPreprocessor,
    collect_all_defines,
    collect_signal_defines,
    scan_files_for_usages,
    build_matrix,
    scan_c_file_for_signals,
    _build_nodes_edges,
    _group_from_filename,
)


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    cwd: Path | None = None,
    signal_pattern: str = r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+",
    quiet: bool = False,
) -> dict:
    """
    Full signal flow extraction. Auto-detects config from folder structure.

    Parameters
    ----------
    cwd            : Source folder (e.g. 03_SwFunktion/). Defaults to current dir.
    signal_pattern : Regex matching signal macro names.
    quiet          : Suppress progress output.

    Returns
    -------
    {"nodes": [...], "edges": [...], "meta": {...}}  — graphify-compatible dict.
    Also writes files to graphify-out/scandksw/.
    """
    t0 = time.time()

    # ── Step 1: Auto-detect config ────────────────────────────────────────────
    cfg = auto_detect(cwd)
    warnings = cfg.validate(raise_on_missing=False)

    if not quiet:
        print(cfg)
        if warnings:
            for w in warnings:
                print(f"  WARNING: {w}")
        print()

    # ── Step 2: Discover all C files via makelist chain ───────────────────────
    group_files = discover_all_c_files(cfg)

    if not quiet:
        total_files = sum(len(v) for v in group_files.values())
        print(f"Groups: {len(group_files)}   C/H files: {total_files}")
        for group, files in sorted(group_files.items()):
            print(f"  {group:<30} {len(files)} files")
        print()

    # If makelist discovery found nothing, fall back to glob
    if not group_files:
        if not quiet:
            print("No makelist groups found — falling back to recursive glob ...")
        all_c_files = list(cfg.source_root.rglob("*.c")) + list(cfg.source_root.rglob("*.h"))
        group_files = {"(all)": all_c_files}

    all_files_flat = [f for files in group_files.values() for f in files]
    source_files = [f for f in all_files_flat if f.suffix == ".c"]
    header_files = [f for f in all_files_flat if f.suffix == ".h"]
    all_files_for_defines = all_files_flat   # ScanDKSW scans both C and H

    # ── Step 3a: Matrix layer — collect ALL defines (ScanDKSW-style) ──────────
    if not quiet:
        print(f"Pass 1: Collecting ALL #defines from {len(all_files_for_defines)} files ...")

    all_defines = collect_all_defines(all_files_for_defines, cfg.predefined)

    if not quiet:
        print(f"  → {len(all_defines)} defines collected")
        print()

    # ── Step 3b: Matrix layer — brute-force usage scan across all files ───────
    if not quiet:
        print(f"Pass 2: Scanning {len(source_files)} source files for define usages ...")

    scan_files_for_usages(all_defines, source_files, cfg.predefined)

    findings_total = sum(len(d["findings"]) for d in all_defines.values())
    if not quiet:
        used = sum(1 for d in all_defines.values() if d["findings"])
        cross = sum(1 for d in all_defines.values()
                    if any(f["group"] != d["group"] for f in d["findings"]))
        print(f"  → {findings_total} usages found")
        print(f"  → {used} defines used at least once")
        print(f"  → {cross} cross-group interfaces (appear in the matrix)")
        print()

    # ── Step 3c: Graph layer — filter to signal pattern for graph nodes ───────
    import re as _re
    sig_re = _re.compile(signal_pattern)
    signals: dict[str, dict] = {}
    for name, d in all_defines.items():
        if not sig_re.match(name):
            continue
        from .extract_c_signals import _extract_module
        signals[name] = {
            "name": name,
            "value": d["value"],
            "source_file": d["source_file"],
            "source_line": d["source_line"],
            "module": _extract_module(name),
            "direction": "output",
        }

    if not quiet:
        modules = sorted({s["module"] for s in signals.values()})
        print(f"Graph layer: {len(signals)} signal defines (pattern match)")
        for mod in modules:
            count = sum(1 for s in signals.values() if s["module"] == mod)
            print(f"  {mod:<20} {count} signals")
        print()

    # ── Step 4: Function-level reads for graph edges ───────────────────────────
    if not quiet:
        print(f"Pass 3: Detecting function-level signal reads ...")

    all_findings_graph: list[dict] = []
    for c_file in source_files:
        findings = scan_c_file_for_signals(c_file, signals, cfg.predefined)
        all_findings_graph.extend(findings)

    if not quiet:
        from collections import Counter
        func_counts = Counter(f["function"] for f in all_findings_graph)
        print(f"  → {len(all_findings_graph)} function-level reads detected")
        print(f"  → {len(func_counts)} functions reference at least one signal")
        print()

    # ── Step 5: Build graphify nodes and edges ────────────────────────────────
    nodes, edges = _build_nodes_edges(signals, all_findings_graph, {})

    # Annotate each function node with its group name
    func_to_group = {}
    for group, files in group_files.items():
        for f in files:
            func_to_group[str(f)] = group

    # ── Step 6: Build group-level module edges (module feeds_into module) ─────
    _add_group_edges(nodes, edges, all_findings_graph, signals, group_files)

    # ── Step 7: Write outputs ─────────────────────────────────────────────────
    cfg.scandksw_out.mkdir(parents=True, exist_ok=True)
    cfg.kommentare_out.mkdir(parents=True, exist_ok=True)

    # Flatten all matrix-layer findings (stored in all_defines by scan_files_for_usages)
    all_findings_matrix = [f for d in all_defines.values() for f in d["findings"]]

    result = {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "variant": cfg.variant,
            "source_root": str(cfg.source_root),
            "signals": len(signals),
            "groups": len(group_files),
            "findings": len(all_findings_matrix),
            "elapsed_sec": round(time.time() - t0, 1),
        },
    }

    # signal_graph.json — graphify-compatible extraction
    signal_json = cfg.scandksw_out / "signal_graph.json"
    signal_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # defines.csv — signal name, module, value, source file/line
    _write_defines_csv(cfg.scandksw_out / "defines.csv", signals)

    # matrix.csv — signal × group matrix (who produces, who reads)
    _write_matrix_csv(cfg.scandksw_out / "matrix.csv", all_defines, all_findings_matrix, group_files)

    if not quiet:
        print(f"Output written to: {cfg.scandksw_out}")
        print(f"  signal_graph.json  ({len(nodes)} nodes, {len(edges)} edges)")
        print(f"  defines.csv        ({len(signals)} signal defines)")
        print(f"  matrix.csv         (producer × consumer matrix)")
        print(f"\nDone in {result['meta']['elapsed_sec']}s")

    return result


# ── Group-level feed edges ─────────────────────────────────────────────────────

def _add_group_edges(
    nodes: list[dict],
    edges: list[dict],
    findings: list[dict],
    signals: dict[str, dict],
    group_files: dict[str, list[Path]],
) -> None:
    """
    Add module-level feeds_into edges.

    If group A produces signal X (owns the #define) and group B reads signal X
    (a function in group B uses the macro), then:
        module_A  --feeds_into-->  module_B

    This gives the high-level signal flow between software components.
    """
    # Build file → group lookup
    file_to_group: dict[str, str] = {}
    for group, files in group_files.items():
        for f in files:
            file_to_group[str(f)] = group

    # For each finding: producer module → consumer group
    seen_group_edges: set[tuple[str, str, str]] = set()

    for f in findings:
        sig_name = f["signal"]
        sig_info = signals.get(sig_name)
        if not sig_info:
            continue
        producer_module = sig_info["module"]  # e.g. "KRS"

        # Consumer group: which group does the reading function's file belong to?
        consumer_group = file_to_group.get(f["file"], "")
        if not consumer_group:
            continue

        # Extract the module prefix of the consumer group (e.g. "krs\krs" → "KRS")
        consumer_parts = consumer_group.replace("\\", "/").split("/")
        consumer_module = consumer_parts[-1].upper()

        if producer_module == consumer_module:
            continue  # skip self-reads

        key = (producer_module, consumer_module, sig_name)
        if key in seen_group_edges:
            continue
        seen_group_edges.add(key)

        from .extract_c_signals import _make_id
        prod_nid = _make_id("module", producer_module)
        cons_nid = _make_id("module", consumer_module)

        # Ensure consumer module node exists
        existing_ids = {n["id"] for n in nodes}
        if cons_nid not in existing_ids:
            nodes.append({
                "id": cons_nid,
                "label": consumer_module,
                "node_type": "module",
                "source_file": f["file"],
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            })

        edges.append({
            "source": prod_nid,
            "target": cons_nid,
            "relation": "feeds_into",
            "via_signal": sig_name,
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "weight": 1.0,
        })


# ── CSV writers ────────────────────────────────────────────────────────────────

def _write_defines_csv(path: Path, signals: dict[str, dict]) -> None:
    """Write defines.csv — one row per signal define."""
    lines = ["Name\tModule\tDirection\tValue\tSource_File\tSource_Line"]
    for name, s in sorted(signals.items()):
        value = s["value"].replace("\t", " ")
        lines.append(
            f"{name}\t{s['module']}\t{s['direction']}\t{value}\t"
            f"{s['source_file']}\t{s['source_line']}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_matrix_csv(
    path: Path,
    signals: dict[str, dict],
    findings: list[dict],
    group_files: dict[str, list[Path]],
) -> None:
    """
    Write matrix.csv — producer/consumer matrix.

    Rows: signal defines (one per row)
    Columns: software groups
    Cell: "P" = producer (defines the signal), "X" = consumer (reads the signal)

    This matches the ScanDKSW matrix.csv format closely.
    """
    # Build file → group lookup
    file_to_group: dict[str, str] = {}
    for group, files in group_files.items():
        for f in files:
            file_to_group[str(f)] = group

    # Group names for column headers (sorted)
    all_groups = sorted(group_files.keys())
    if not all_groups:
        return

    # Short group display names (last segment: "krs\krs" → "krs")
    def short(g: str) -> str:
        return g.replace("\\", "/").split("/")[-1]

    # Build consumer set: signal_name → set of groups that read it
    from collections import defaultdict
    consumers: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        sig = f["signal"]
        grp = file_to_group.get(f["file"], "")
        if grp:
            consumers[sig].add(grp)

    # Build header
    col_headers = [short(g) for g in all_groups]
    header = "Signal\tModule\t" + "\t".join(col_headers)
    lines = [header]

    for sig_name in sorted(signals.keys()):
        sig = signals[sig_name]
        producer_module = sig["module"]
        row = [sig_name, producer_module]

        for group in all_groups:
            grp_short = short(group)
            grp_module = grp_short.upper()
            if grp_module == producer_module:
                row.append("P")  # producer
            elif group in consumers.get(sig_name, set()):
                row.append("X")  # consumer
            else:
                row.append("")

        lines.append("\t".join(row))

    path.write_text("\n".join(lines), encoding="utf-8")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    CLI entry point — same calling convention as graphify:

        python -m graphify.scandksw_runner .
        python -m graphify.scandksw_runner /path/to/03_SwFunktion
        python -m graphify.scandksw_runner . --quiet
    """
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="ScanDKSW-equivalent signal flow extractor (graphify integration)"
    )
    parser.add_argument("root", nargs="?", default=".", help="Source folder (default: current dir)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    parser.add_argument(
        "--pattern", default=r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+",
        help="Regex for signal macro names",
    )
    args = parser.parse_args()

    run(cwd=Path(args.root), signal_pattern=args.pattern, quiet=args.quiet)


if __name__ == "__main__":
    main()
