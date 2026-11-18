"""Execution order for the software documentation: tasks, timeline, and "same cycle / next cycle" per data-flow step.

There is no schedule table in the code base: a few functions are called by the hardware every N samples and call
everything else. So

* a **task** = a function that no other function calls (entry point, optionally named with its period in a small
  json file: ``{"Kku_Task10ms": "10 ms"}``); everything it reaches runs in that task;
* the **timeline** of a task = its functions expanded in code order: the accesses (reads / writes of variables) and the
  calls of a function are merged by line number, a callee is expanded where it is called (first call only);
* a data-flow step ``x derived_from a`` happens in function F. If a function that writes ``a`` runs **before** F in the
  same task the value is from the **same cycle**; if it runs after F, F sees the value of the **previous cycle**.

Limits: a function that runs several times per cycle counts at its first call; read and write lines of an edge that
was merged (``reads_writes_var``) cannot be told apart; calls through function pointers are ordered like direct calls.
"""
from __future__ import annotations

import re
from collections import defaultdict

from graphify.focus import CALL, READ, WRITE

# timing of one data-flow step
SAME, NEXT, OWN, OTHER, OUTSIDE, UNSCHEDULED = 0, 1, 2, 3, 4, 5
TIMING_TEXT = {SAME: "same cycle", NEXT: "next cycle (the writer runs after the reader)",
               OWN: "own previous value (kept between cycles)", OTHER: "other task (written by a different task)",
               OUTSIDE: "no writer in the code (input from outside)", UNSCHEDULED: "not reached from a task"}
TIMING_SHORT = {SAME: "same cycle", NEXT: "next cycle", OWN: "previous value", OTHER: "other task", OUTSIDE: "outside input",
                UNSCHEDULED: "not scheduled"}
DELAY = {SAME: 0, NEXT: 1, OWN: 1}          # cycles added by a step; other task / outside input add no countable cycles

_MAX_EVENTS = 60000
_MAX_DEPTH = 40


def parse_period(text) -> tuple:
    """'10 ms' -> (10.0, 'ms'); '5' -> (5.0, ''); anything else -> (None, text)."""
    if isinstance(text, dict):
        text = text.get("period") or text.get("label") or ""
    t = str(text or "").strip()
    m = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*(.*)$", t)
    if not m:
        return None, t
    return float(m.group(1).replace(",", ".")), m.group(2).strip()


class Task:
    def __init__(self, entry, name, period_text=""):
        self.entry = entry
        self.name = name
        self.period_text = str(period_text.get("period") if isinstance(period_text, dict) else period_text or "").strip()
        self.period, self.unit = parse_period(period_text)
        self.order: list = []                 # function ids in the order they first run
        self.pos: dict = {}                   # function id -> timeline index where it starts
        self.writes: dict = defaultdict(list)  # variable -> [(time, function)]
        self.first_read: dict = {}            # (variable, function) -> time of the first read
        self.truncated = False

    @property
    def label(self) -> str:
        return f"{self.name} ({self.period_text})" if self.period_text else self.name


class Schedule:
    """Tasks and timelines of a whole graph (not only the exported module): a task usually crosses components."""

    def __init__(self, M, skip_vars=(), config: dict | None = None):
        self.M = M
        self.skip = set(skip_vars)
        self.config = config or {}
        self.approximate = False              # some function has no recorded call order
        self.tasks: list = []
        self._runs_in: dict = defaultdict(list)
        self._wt: dict = defaultdict(set)      # variable -> tasks that write it
        self._item_cache: dict = {}
        self._build()

    # ---- build -------------------------------------------------------------------------

    def _entries(self) -> list:
        M = self.M
        funcs = [i for i in M.nodes if M.t(i) == "function" and (M.meta(i).get("has_definition") or not M.meta(i).get("declaration_only"))]
        called = {e["target"] for e in M.edges if e.get("relation") in CALL and e.get("source") != e.get("target")}
        if self.config:
            wanted = {re.sub(r"\(\)$", "", str(k)).strip(): v for k, v in self.config.items()}
            return [(f, wanted[re.sub(r"\(\)$", "", M.label(f))]) for f in funcs if re.sub(r"\(\)$", "", M.label(f)) in wanted]
        return [(f, "") for f in sorted(funcs, key=lambda x: M.label(x).lower())
                if f not in called and M.targets(f, CALL, ("function",))]

    def _items(self, f) -> list:
        """Accesses and calls of f as (line, rank, kind, payload), in code order."""
        if f in self._item_cache:
            return self._item_cache[f]
        M, items = self.M, []
        for kind, rels in (("r", READ), ("w", WRITE)):
            for v in M.targets(f, rels, ("variable",)):
                if v in self.skip:
                    continue
                e = M.edge(f, v, rels) or {}
                lines = (e.get("metadata") or {}).get("lines") if isinstance(e.get("metadata"), dict) else None
                for ln in (lines or [0])[:12]:
                    items.append((int(ln) if isinstance(ln, int) or str(ln).isdigit() else 0, 0 if kind == "r" else 2, kind, v))
        seq = M.call_sequence(f)
        if not seq:
            callees = M.targets(f, CALL, ("function",))
            if callees:
                self.approximate = True
            seq = [(c, 0, "") for c in sorted(callees, key=lambda x: M.label(x).lower())]
        for callee, line, _ctx in seq:
            items.append((int(line or 0), 1, "c", callee))
        items.sort(key=lambda t: (t[0], t[1]))
        self._item_cache[f] = items
        return items

    def _build(self) -> None:
        M = self.M
        for entry, cfg in self._entries():
            t = Task(entry, re.sub(r"\(\)$", "", M.label(entry)), cfg)
            clock = [0]
            seen: set = set()

            def expand(f, depth):
                if f in seen or depth > _MAX_DEPTH:
                    return
                if clock[0] > _MAX_EVENTS:
                    t.truncated = True
                    return
                seen.add(f)
                t.order.append(f)
                t.pos[f] = clock[0]
                for _ln, _rk, kind, payload in self._items(f):
                    if kind == "c":
                        expand(payload, depth + 1)
                        continue
                    clock[0] += 1
                    if kind == "w":
                        t.writes[payload].append((clock[0], f))
                    else:
                        t.first_read.setdefault((payload, f), clock[0])

            expand(entry, 0)
            self.tasks.append(t)
            for f in t.order:
                self._runs_in[f].append(t)
            for v in t.writes:
                self._wt[v].add(t)

    # ---- queries -----------------------------------------------------------------------

    def runs_in(self, f) -> list:
        return self._runs_in.get(f, [])

    def writer_tasks(self, v) -> list:
        return sorted(self._wt.get(v, ()), key=lambda t: t.name)

    def step_timing(self, a, fns) -> int:
        """Timing of the step 'something is computed from variable a' in the functions fns: the best (smallest) code."""
        best = UNSCHEDULED
        for f in fns:
            for t in self.runs_in(f):
                rt = t.first_read.get((a, f), t.pos.get(f, 0))
                others = [w for w, wf in t.writes.get(a, []) if wf != f]
                if others:
                    code = SAME if min(others) < rt else NEXT
                elif any(wf == f for _w, wf in t.writes.get(a, [])):
                    code = OWN
                elif any(x is not t for x in self._wt.get(a, ())):
                    code = OTHER
                else:
                    code = OUTSIDE
                best = min(best, code)
        return best

    def edge_timing(self, e) -> int:
        """Timing of a derived_from edge (x derived_from a, computed in the functions listed in its metadata)."""
        md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        return self.step_timing(e["target"], [f for f in md.get("functions", []) if f in self.M.nodes])

    @staticmethod
    def delay_text(cycles: int, other_task: bool, task: "Task | None" = None) -> str:
        if not cycles and not other_task:
            return "same cycle"
        parts = []
        if cycles:
            parts.append(f"{cycles} cycle{'s' if cycles != 1 else ''} later")
            if task is not None and task.period:
                total = task.period * cycles
                parts[-1] += f" ({total:g} {task.unit})".replace(" )", ")")
        if other_task:
            parts.append("task change")
        return " + ".join(parts)

    def order_issues(self, funcs=None, cap: int = 200) -> list:
        """Reader runs before the writer in the same task, so it sees the value of the previous cycle:
        [(variable, task, reader, reader time, writer, writer time)]."""
        M, out = self.M, []
        want = set(funcs) if funcs is not None else None
        for t in self.tasks:
            for v, ws in sorted(t.writes.items(), key=lambda kv: M.label(kv[0]).lower()):
                for (v_, f_), rt in t.first_read.items():
                    if v_ != v or (want is not None and f_ not in want):
                        continue
                    others = [(w, wf) for w, wf in ws if wf != f_]
                    if others and min(w for w, _ in others) > rt:
                        w, wf = min(others)
                        out.append((v, t, f_, rt, wf, w))
                        if len(out) >= cap:
                            return out
        return out

    def task_interfaces(self, funcs=None, cap: int = 200) -> list:
        """Variables written in one task and read in another: [(variable, writer tasks, reader tasks)]."""
        M, out = self.M, []
        reads: dict = defaultdict(set)
        for t in self.tasks:
            for (v, f_) in t.first_read:
                if want_ok(funcs, f_):
                    reads[v].add(t)
        for v in sorted(reads, key=lambda x: M.label(x).lower()):
            wt = self._wt.get(v, set())
            if wt and any(t not in wt for t in reads[v]):
                out.append((v, sorted(wt, key=lambda t: t.name), sorted(reads[v] - wt, key=lambda t: t.name)))
                if len(out) >= cap:
                    break
        return out


def want_ok(funcs, f) -> bool:
    return funcs is None or f in funcs
