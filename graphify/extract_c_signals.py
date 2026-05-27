"""
C signal flow extractor for graphify.

Ports the ScanDKSW preprocessor state machine to Python and extracts
function-to-signal read/write relationships for automotive embedded C code.

Usage (standalone):
    from graphify.extract_c_signals import extract_signal_flow
    result = extract_signal_flow(
        root_dir=Path("my_project"),
        predefined={"CS_MODUS_DERIVAT": "EN_MODUS_DERIVAT_DQ250"},
        signal_pattern=r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+",
    )

The result is a standard graphify extraction dict:
    {"nodes": [...], "edges": [...]}

Nodes added by this extractor:
  - type="signal"  — a CAN interface define (e.g. KRS_GET_ZUSTAND)
  - type="module"  — a software component group (e.g. KRS)

Edges added:
  - reads   — function reads a signal (uses its getter macro)
  - writes  — function writes/exposes a signal (defines the macro)
  - belongs_to — function or signal belongs to a module group
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Iterator


# ── Preprocessor state machine ────────────────────────────────────────────────
# Direct port of ScanDKSW's IfNesting + parsen logic.
#
# IfNesting[i] is a byte:
#   Bit0 = "currently active" (1 = yes)
#   Bit1 = "was ever active"  (1 = yes, sticky once set)
#
#   0x03 (0b11) = active right now
#   0x02 (0b10) = was active, now in else/elif — skip
#   0x00 (0b00) = never been active


class CPreprocessor:
    """
    Simplified C preprocessor that tracks #if/#ifdef/#else/#elif/#endif nesting
    and decides whether each line is in an active code branch.

    Feed each raw source line to process(line). Read the `active` property
    to know whether the current line should be parsed.

    Does NOT expand macros or handle #include. That is intentional: we only
    need branch elimination, not full preprocessing.
    """

    _IF_RE = re.compile(r"^\s*#\s*if\b", re.IGNORECASE)
    _IFDEF_RE = re.compile(r"^\s*#\s*ifdef\b(.+)", re.IGNORECASE)
    _IFNDEF_RE = re.compile(r"^\s*#\s*ifndef\b(.+)", re.IGNORECASE)
    _ELIF_RE = re.compile(r"^\s*#\s*elif\b(.+)", re.IGNORECASE)
    _ELSE_RE = re.compile(r"^\s*#\s*else\b", re.IGNORECASE)
    _ENDIF_RE = re.compile(r"^\s*#\s*endif\b", re.IGNORECASE)
    _IF_EXPR_RE = re.compile(r"^\s*#\s*if\b(.+)", re.IGNORECASE)

    def __init__(self, predefined: dict[str, str] | None = None):
        # Maps name → value string. Empty string = "defined but no value" (boolean flag).
        self._defines: dict[str, str] = dict(predefined or {})
        # The nesting stack. Start with 0x03 = outer scope is always active.
        self._nesting: list[int] = [0x03]

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True if the current position is in an active code branch."""
        e = 0x01
        for i in self._nesting:
            e &= i
        return e == 0x01

    def add_define(self, name: str, value: str = "") -> None:
        self._defines[name] = value

    def remove_define(self, name: str) -> None:
        self._defines.pop(name, None)

    def process(self, line: str) -> bool:
        """
        Consume one preprocessor line. Updates internal state.
        Returns True if the *next* line will be in an active branch.
        Call this BEFORE reading self.active for the SAME line.
        """
        stripped = line.strip()

        m = self._IFDEF_RE.match(stripped)
        if m:
            sym = m.group(1).strip()
            result = sym in self._defines
            self._push(result)
            return self.active

        m = self._IFNDEF_RE.match(stripped)
        if m:
            sym = m.group(1).strip()
            result = sym not in self._defines
            self._push(result)
            return self.active

        m = self._IF_EXPR_RE.match(stripped)
        if m:
            expr = m.group(1).strip()
            result = self._eval(expr)
            self._push(result)
            return self.active

        m = self._ELIF_RE.match(stripped)
        if m:
            expr = m.group(1).strip()
            result = self._eval(expr)
            current = self._nesting[0]
            if current == 0x00:
                self._nesting[0] = 0x03 if result else 0x00
            else:
                self._nesting[0] = 0x02
            return self.active

        if self._ELSE_RE.match(stripped):
            current = self._nesting[0]
            # Toggle: never-active → active; was-active/active → skip
            self._nesting[0] = 0x03 if current == 0x00 else 0x02
            return self.active

        if self._ENDIF_RE.match(stripped):
            if len(self._nesting) > 1:
                self._nesting.pop(0)
            return self.active

        return self.active

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _push(self, condition_true: bool) -> None:
        """Push a new nesting level. Parent must be active for child to matter."""
        if self.active:
            self._nesting.insert(0, 0x03 if condition_true else 0x00)
        else:
            # Already inactive: new level inherits inactive, but marks bit1 set
            # so #else can't accidentally activate it
            self._nesting.insert(0, 0x02)

    def _eval(self, expr: str) -> bool:
        """
        Evaluate a #if expression against the current defines dictionary.

        Handles:
          defined(X)        → X in defines
          !defined(X)       → X not in defines
          X == VALUE        → defines.get(X) == VALUE
          X != VALUE
          X >= VALUE  (numeric)
          &&, ||
          Numeric literals  → nonzero = true

        Falls back to False on any parse error (conservative: treat unknown
        conditions as inactive, so we don't analyse dead code).
        """
        try:
            return bool(self._eval_expr(expr.strip()))
        except Exception:
            return False  # conservative fallback

    def _eval_expr(self, expr: str):  # noqa: ANN001
        """Recursive descent evaluator for simple #if expressions."""
        expr = expr.strip()

        # Strip outer parens
        if expr.startswith("(") and self._matching_paren(expr) == len(expr) - 1:
            return self._eval_expr(expr[1:-1])

        # OR  (lowest precedence)
        pos = self._find_binary_op(expr, "||")
        if pos is not None:
            l = self._eval_expr(expr[:pos])
            r = self._eval_expr(expr[pos + 2:])
            return bool(l) or bool(r)

        # AND
        pos = self._find_binary_op(expr, "&&")
        if pos is not None:
            l = self._eval_expr(expr[:pos])
            r = self._eval_expr(expr[pos + 2:])
            return bool(l) and bool(r)

        # NOT
        if expr.startswith("!") and not expr.startswith("!="):
            return not bool(self._eval_expr(expr[1:]))

        # defined(X)
        m = re.match(r"^defined\s*\(\s*(\w+)\s*\)$", expr)
        if m:
            return m.group(1) in self._defines

        # !defined(X)  — already handled by the NOT case above, but catch explicit
        m = re.match(r"^!defined\s*\(\s*(\w+)\s*\)$", expr)
        if m:
            return m.group(1) not in self._defines

        # Comparison operators
        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in expr:
                parts = expr.split(op, 1)
                l_str = parts[0].strip()
                r_str = parts[1].strip()
                l_val = self._resolve_value(l_str)
                r_val = self._resolve_value(r_str)
                if l_val is None or r_val is None:
                    # String comparison for enum-like defines
                    l_s = self._resolve_str(l_str)
                    r_s = self._resolve_str(r_str)
                    if op == "==":
                        return l_s == r_s
                    elif op == "!=":
                        return l_s != r_s
                    return False
                if op == "==":
                    return l_val == r_val
                elif op == "!=":
                    return l_val != r_val
                elif op == ">=":
                    return l_val >= r_val
                elif op == "<=":
                    return l_val <= r_val
                elif op == ">":
                    return l_val > r_val
                elif op == "<":
                    return l_val < r_val

        # Bare identifier or numeric
        val = self._resolve_value(expr)
        if val is not None:
            return val != 0.0
        # Bare symbol: true if it's defined (and non-zero/non-empty)
        if re.match(r"^\w+$", expr):
            if expr in self._defines:
                v = self._defines[expr]
                if v == "":
                    return True  # defined without value = true flag
                return self._eval_expr(v)
        return False

    def _resolve_value(self, s: str):
        """Try to resolve s to a numeric value. Returns None if not numeric."""
        s = s.strip()
        # Recurse through defines
        visited = set()
        while s in self._defines and s not in visited:
            visited.add(s)
            s = self._defines[s].strip()
        # Try to parse as int or float
        try:
            if s.startswith("0x") or s.startswith("0X"):
                return float(int(s, 16))
            if s.endswith("u") or s.endswith("U") or s.endswith("L"):
                return float(int(s.rstrip("uUL")))
            return float(s)
        except (ValueError, TypeError):
            return None

    def _resolve_str(self, s: str) -> str:
        """Resolve s through defines to final string value."""
        s = s.strip()
        visited = set()
        while s in self._defines and s not in visited:
            visited.add(s)
            s = self._defines[s].strip()
        return s

    def _find_binary_op(self, expr: str, op: str) -> int | None:
        """Find op at the top level of expr (not inside parentheses)."""
        depth = 0
        i = 0
        while i < len(expr):
            ch = expr[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and expr[i:i + len(op)] == op:
                # Make sure it's not part of a longer op
                after = expr[i + len(op):i + len(op) + 1]
                if op == "&" and after == "&":
                    pass  # handled above as &&
                if op == "|" and after == "|":
                    pass
                return i
            i += 1
        return None

    def _matching_paren(self, expr: str) -> int:
        """Return index of closing paren matching the opening paren at index 0."""
        depth = 0
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        return -1


# ── Define extraction ─────────────────────────────────────────────────────────

_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)\s*(.*?)(?://.*)?$")
_COMMENT_LINE_RE = re.compile(r"^\s*//")
_BLOCK_COMMENT_START = "/*"
_BLOCK_COMMENT_END = "*/"


def _strip_block_comments(source: str) -> str:
    """Remove /* ... */ block comments from source text."""
    result = []
    i = 0
    while i < len(source):
        start = source.find(_BLOCK_COMMENT_START, i)
        if start == -1:
            result.append(source[i:])
            break
        result.append(source[i:start])
        end = source.find(_BLOCK_COMMENT_END, start + 2)
        if end == -1:
            break
        i = end + 2
    return "".join(result)


def _group_from_filename(filename: str) -> str:
    """
    Extract the group/module name from a filename.
    Mirrors ScanDKSW's extractClust() function exactly:
      "KRS_OUTPUTS.c"   → "krs"
      "krs_app.h"       → "krs"
      "svc_main.c"      → "svc"
    Rule: remove extension, take everything before the first underscore, lowercase.
    """
    stem = Path(filename).stem          # "KRS_OUTPUTS"
    prefix = stem.split("_")[0]         # "KRS"
    return prefix.lower()               # "krs"


def collect_all_defines(
    files: list[Path],
    predefined: dict[str, str] | None = None,
) -> dict[str, dict]:
    """
    Collect ALL #define macros from C/H files — no name filter.
    Mirrors ScanDKSW's full define harvest (Stage 1).

    Each entry:
        {
          "name": str,           # e.g. "KRS_GET_ZUSTAND"
          "value": str,          # raw macro value
          "source_file": str,
          "source_line": int,
          "group": str,          # e.g. "krs"  (from filename prefix)
          "findings": [],        # populated later by scan_files_for_usages()
        }
    """
    all_defines: dict[str, dict] = {}
    pp = CPreprocessor(predefined)

    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        source = _strip_block_comments(source)
        str_path = str(f)
        group = _group_from_filename(f.name)

        for lineno, raw_line in enumerate(source.splitlines(), start=1):
            pp.process(raw_line)
            if not pp.active:
                continue
            m = _DEFINE_RE.match(raw_line)
            if not m:
                continue
            name = m.group(1)
            value = m.group(2).strip()
            if name in all_defines:
                continue   # first definition wins (like ScanDKSW's non-Doubletten path)
            all_defines[name] = {
                "name": name,
                "value": value,
                "source_file": str_path,
                "source_line": lineno,
                "group": group,
                "findings": [],   # list of {"file": str, "line": int, "group": str}
            }

    return all_defines


def collect_signal_defines(
    header_files: list[Path],
    signal_pattern: str,
    predefined: dict[str, str] | None = None,
) -> dict[str, dict]:
    """
    Collect only #define macros whose names match signal_pattern.
    Used for the graphify graph layer (signal nodes) — keeps the graph clean.

    For the full ScanDKSW-equivalent matrix layer, use collect_all_defines().

    Each entry:
        {
          "name": str,           # e.g. "KRS_GET_ZUSTAND"
          "value": str,
          "source_file": str,
          "source_line": int,
          "module": str,         # e.g. "KRS"  (signal name prefix)
          "direction": "output",
        }
    """
    sig_re = re.compile(signal_pattern)
    signals: dict[str, dict] = {}
    all_defs = collect_all_defines(header_files, predefined)

    for name, d in all_defs.items():
        if not sig_re.match(name):
            continue
        module = _extract_module(name)
        signals[name] = {
            "name": name,
            "value": d["value"],
            "source_file": d["source_file"],
            "source_line": d["source_line"],
            "module": module,
            "direction": "output",
        }

    return signals

            # Extract module prefix: everything before the first "_GET_" or "_SET_"
            module = _extract_module(name)

            signals[name] = {
                "name": name,
                "value": value,
                "source_file": str_path,
                "source_line": lineno,
                "module": module,
                "direction": "output",
            }

    return signals


def _extract_module(signal_name: str) -> str:
    """
    Extract the module name from a signal name.
    "KRS_GET_ZUSTAND"  → "KRS"
    "SVC_GET_M_MOT_FIL" → "SVC"
    "GSI_GET_GANG_IST" → "GSI"
    """
    for sep in ("_GET_", "_SET_", "_PUT_", "_IS_", "_HAS_"):
        idx = signal_name.find(sep)
        if idx > 0:
            return signal_name[:idx]
    # Fallback: first segment
    return signal_name.split("_")[0]


# ── Cross-group usage scanning (ScanDKSW matrix layer) ────────────────────────

def scan_files_for_usages(
    all_defines: dict[str, dict],
    source_files: list[Path],
    predefined: dict[str, str] | None = None,
) -> None:
    """
    Brute-force usage scan — mirrors ScanDKSW's exact algorithm.

    For every non-preprocessor line in an active code branch:
        for each define name → if name is a substring of the line → record finding

    Findings are written directly into all_defines[name]["findings"].
    Each finding: {"file": str, "line": int, "group": str}

    This is O(lines × defines) — same trade-off ScanDKSW makes intentionally.
    For large codebases this is fast because it's pure string ops, no AST needed.
    """
    pp = CPreprocessor(predefined)
    define_names = list(all_defines.keys())   # snapshot — don't mutate during iteration

    for c_file in source_files:
        try:
            source = c_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        source = _strip_block_comments(source)
        str_path = str(c_file)
        consumer_group = _group_from_filename(c_file.name)

        for lineno, raw_line in enumerate(source.splitlines(), start=1):
            is_preprocessor = raw_line.strip().startswith("#")
            pp.process(raw_line)

            if is_preprocessor or not pp.active:
                continue

            # ScanDKSW's brute-force: check every define name as substring
            for name in define_names:
                if name in raw_line:
                    all_defines[name]["findings"].append({
                        "file": str_path,
                        "line": lineno,
                        "group": consumer_group,
                    })


def build_matrix(
    all_defines: dict[str, dict],
    all_groups: list[str],
) -> dict[str, dict]:
    """
    Build the producer/consumer matrix from all_defines + their findings.
    Mirrors ScanDKSW's Stage 3 matrix construction.

    Returns a dict:
        {
          define_name: {
            "producer_group": str,
            "consumers": {group_name: count},   # groups that use this define
            "is_interface": bool,               # True if used by another group
          }
        }
    """
    matrix: dict[str, dict] = {}

    for name, d in all_defines.items():
        producer = d["group"]
        consumers: dict[str, int] = {}

        for f in d["findings"]:
            consumer = f["group"]
            if consumer != producer:
                consumers[consumer] = consumers.get(consumer, 0) + 1

        matrix[name] = {
            "producer_group": producer,
            "consumers": consumers,
            "is_interface": len(consumers) > 0,
        }

    return matrix


# ── Function body scanning ─────────────────────────────────────────────────────

_FUNC_DEF_RE = re.compile(
    r"^\s*(?:static\s+|inline\s+|extern\s+)*"
    r"(?:[\w\*]+\s+)+?"
    r"(\w+)\s*\([^;]*$"
)
_ASSIGNMENT_RE = re.compile(r"\b(\w+)\s*=(?!=)")


def scan_c_file_for_signals(
    c_file: Path,
    known_signals: dict[str, dict],
    predefined: dict[str, str] | None = None,
) -> list[dict]:
    """
    Scan a C source file with preprocessor dead-code elimination.

    Finds which signals each function reads (uses the getter macro in its body).
    Returns a list of findings:
        [{"function": str, "signal": str, "line": int, "file": str}, ...]
    """
    try:
        source = c_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    source = _strip_block_comments(source)
    str_path = str(c_file)
    lines = source.splitlines()
    pp = CPreprocessor(predefined)

    findings: list[dict] = []
    current_func: str | None = None
    brace_depth = 0
    func_start_depth = 0

    for lineno, raw_line in enumerate(lines, start=1):
        # Feed to preprocessor FIRST — it decides if this branch is active
        pp.process(raw_line)

        # Count braces to track function scope (always, even in dead code,
        # so we don't lose track of depth)
        open_b = raw_line.count("{")
        close_b = raw_line.count("}")

        if not pp.active:
            brace_depth += open_b - close_b
            continue

        stripped = raw_line.strip()

        # Detect function entry (simple heuristic: line has identifier followed
        # by '(' with a '{' somewhere nearby and not a semicolon)
        if brace_depth == 0 and "(" in stripped and "{" in stripped and ";" not in stripped:
            m = _FUNC_DEF_RE.match(raw_line)
            if m:
                current_func = m.group(1)
                func_start_depth = brace_depth

        brace_depth += open_b - close_b

        # When we return to the depth at function start, function ended
        if current_func and brace_depth <= func_start_depth and lineno > 1:
            current_func = None

        # Check signal usage in active code
        if current_func:
            for sig_name, sig_info in known_signals.items():
                if sig_name in raw_line:
                    findings.append({
                        "function": current_func,
                        "signal": sig_name,
                        "line": lineno,
                        "file": str_path,
                    })

    return findings


# ── Graphify node/edge builders ────────────────────────────────────────────────

def _make_id(*parts: str) -> str:
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()


def _build_nodes_edges(
    signals: dict[str, dict],
    findings: list[dict],
    func_nodes: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """
    Convert signals + findings into graphify nodes and edges.

    signals:    output of collect_signal_defines()
    findings:   output of scan_c_file_for_signals() (aggregated across all files)
    func_nodes: {function_name: source_file} — already-known function nodes from AST
                Used to link findings to existing nodes.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_signal_nids: set[str] = set()
    seen_module_nids: set[str] = set()

    # Create signal nodes
    for sig_name, sig in signals.items():
        nid = _make_id("signal", sig_name)
        if nid in seen_signal_nids:
            continue
        seen_signal_nids.add(nid)

        nodes.append({
            "id": nid,
            "label": sig_name,
            "node_type": "signal",
            "source_file": sig["source_file"],
            "source_location": f"L{sig['source_line']}",
            "module": sig["module"],
            "direction": sig["direction"],
            "macro_value": sig["value"],
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
        })

        # Module node
        module = sig["module"]
        mod_nid = _make_id("module", module)
        if mod_nid not in seen_module_nids:
            seen_module_nids.add(mod_nid)
            nodes.append({
                "id": mod_nid,
                "label": module,
                "node_type": "module",
                "source_file": sig["source_file"],
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            })

        # Signal belongs_to module
        edges.append({
            "source": nid,
            "target": mod_nid,
            "relation": "belongs_to",
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "source_file": sig["source_file"],
            "source_location": f"L{sig['source_line']}",
            "weight": 1.0,
        })

    # Create reads edges from findings
    seen_reads: set[tuple] = set()
    for f in findings:
        func_name = f["function"]
        sig_name = f["signal"]
        sig_nid = _make_id("signal", sig_name)

        # Try to match to existing function node
        func_nid = _make_id(func_name)
        if func_name in func_nodes:
            # Use the path-qualified ID from AST extraction if available
            pass  # func_nid stays as simple ID; build.py merges by label

        key = (func_nid, sig_nid)
        if key in seen_reads:
            continue
        seen_reads.add(key)

        edges.append({
            "source": func_nid,
            "target": sig_nid,
            "relation": "reads",
            "confidence": "EXTRACTED",
            "confidence_score": 0.9,  # slightly lower than AST — text match
            "source_file": f["file"],
            "source_location": f"L{f['line']}",
            "weight": 1.0,
        })

    return nodes, edges


# ── Main entry point ───────────────────────────────────────────────────────────

def extract_signal_flow(
    root_dir: Path,
    predefined: dict[str, str] | None = None,
    signal_pattern: str = r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+",
    header_glob: str = "**/*.h",
    source_glob: str = "**/*.c",
    func_nodes: dict[str, str] | None = None,
) -> dict:
    """
    Full signal flow extraction over a C codebase directory.

    Parameters
    ----------
    root_dir      : Root directory to scan (recursive).
    predefined    : Compiler switch definitions for dead-code elimination.
                    e.g. {"CS_MODUS_DERIVAT": "EN_MODUS_DERIVAT_DQ250"}
    signal_pattern: Regex matching signal macro names (anchored to full name).
    header_glob   : Glob for header files (signal definitions).
    source_glob   : Glob for source files (signal usages).
    func_nodes    : Existing {func_name: source_file} dict from AST extraction.

    Returns
    -------
    {"nodes": [...], "edges": [...]}  — graphify-compatible extraction dict.
    """
    root = Path(root_dir)
    predefined = predefined or {}

    # Phase 1: collect signal definitions from headers
    header_files = list(root.glob(header_glob))
    signals = collect_signal_defines(header_files, signal_pattern, predefined)

    # Phase 2: scan C files for signal usages
    c_files = list(root.glob(source_glob))
    all_findings: list[dict] = []
    for c_file in c_files:
        findings = scan_c_file_for_signals(c_file, signals, predefined)
        all_findings.extend(findings)

    # Phase 3: build graphify nodes and edges
    nodes, edges = _build_nodes_edges(signals, all_findings, func_nodes or {})

    return {"nodes": nodes, "edges": edges}


# ── CLI helper ─────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Minimal CLI for standalone use:

        python -m graphify.extract_c_signals \\
            --root ./my_project \\
            --define CS_MODUS_DERIVAT=EN_MODUS_DERIVAT_DQ250 \\
            --define CS_MODUS_HYBRID=EN_MODUS_HYBRID_AUS \\
            --out signal_graph.json
    """
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Extract C signal flow graph")
    parser.add_argument("--root", required=True, help="Root directory of C codebase")
    parser.add_argument(
        "--define", "-D", action="append", default=[],
        metavar="NAME=VALUE",
        help="Predefined compiler symbol (repeat for multiple). VALUE is optional.",
    )
    parser.add_argument("--out", default="signal_graph.json", help="Output JSON file")
    parser.add_argument(
        "--pattern",
        default=r"[A-Z][A-Z0-9]+_GET_[A-Z0-9_]+|[A-Z][A-Z0-9]+_SET_[A-Z0-9_]+",
        help="Regex for signal macro names",
    )
    args = parser.parse_args()

    predefined: dict[str, str] = {}
    for d in args.define:
        if "=" in d:
            k, v = d.split("=", 1)
            predefined[k.strip()] = v.strip()
        else:
            predefined[d.strip()] = ""

    result = extract_signal_flow(
        root_dir=Path(args.root),
        predefined=predefined,
        signal_pattern=args.pattern,
    )

    out = Path(args.out)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Signal graph: {len(result['nodes'])} nodes, {len(result['edges'])} edges → {out}")


if __name__ == "__main__":
    main()
