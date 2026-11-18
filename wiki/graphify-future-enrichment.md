# Graphify clang: possible future enrichment

Status 2026-11-18. Ideas only, nothing implemented. Effort: S = hours, M = days, L = weeks.

## A. High value, low effort (AST has it already)

| # | Idea | Graph change | Effort |
|---|---|---|---|
| A1 | Variable types, `const` / `volatile` / `static` | node properties, check against A2L datatype | S |
| A2 | Struct / union members with offsets | member nodes, field-level reads and writes | S-M |
| A3 | Function signatures (params, return, in/out) | parameter nodes, `passes_arg` edges | S |
| A4 | Macro definitions and uses | `macro` nodes, `expands_to` edges | M |
| A5 | `#if` / `#ifdef` variant conditions | `guarded_by` edges | M |
| A6 | Enums with values, `switch` cases | enum nodes, `uses_enum` edges | S |
| A7 | extern vs. static, declaration vs. definition | module interface flag | S |

## B. Medium effort (analysis on top of the AST)

| # | Idea | Graph change | Effort | Status |
|---|---|---|---|---|
| B1 | Condition on reads / writes (`if`, `?:`, loop) | edge property `ctx` on `reads_var` / `writes_var` | S-M | **done 2026-11-18** (tracker §59) |
| B2 | Assigned expression (`x = a + b*k`) | `derived_from` edges variable -> variable | M | **done 2026-11-18** (tracker §59) |
| B3 | Call arguments to variables (`f(y)`, `f(&x)`) | argument variables on the `calls` edge + data flow across functions (no separate `passes_arg` edge, see tracker §59) | M | **done 2026-11-18** (tracker §59) |
| B4 | Function pointers and callback tables | resolved `indirect_call` edges | M | **done 2026-11-18** (tracker §59) |
| B5 | Task / cycle assignment from scheduler table | `runs_in` edges | M | |
| B6 | Complexity, lines, nesting per function | node properties | S | |
| B7 | Comments attached to symbols | description property | S | partly done (doxygen / comment meta) |

## C. Higher effort

| # | Idea | Why | Effort |
|---|---|---|---|
| C1 | Control-flow graph per function | MC/DC planning, test cases | L |
| C2 | Interprocedural data flow over the call chain | signal chain sensor -> decision -> actuator, impact analysis | L (builds on B2 + B3) |
| C3 | clang-tidy / static analyzer findings as nodes | quality view next to the graph | M |
| C4 | Dead code, never-read writes | cleanup candidates | M |
| C5 | Constant-folded value ranges | check against A2L min / max | M |

## D. Recommended order

1. A1 + A2 (types, struct members): small, unlocks field-level edges.
2. A5 (variant conditions): matters most for a variant-heavy ECU.
3. B2 + B3 (data flow): joins the read / write edges into signal chains.

## E. Exploration of B1-B3 (code read, 2026-11-18)

Read in `graphify/extractors/clang_c.py` (`walk`, `_walk_control`, `_handle_assignment_write`, `_handle_call_args`, `add_edge`).

| Point | What exists today | Gap | Proposed change |
|---|---|---|---|
| B1 condition | `_ctx` stack (`if`, `else`, `loop`, `switch`, `?:`, `&&`) is maintained by `_walk_control`, but only copied into `call_seq` (order of calls, `ctx` per call) | `reads_var` / `writes_var` edges carry only `lines`, no context. A write inside `if` looks like an unconditional write | In `add_edge` store `ctx` (last 3 entries) per line; edge metadata `ctx: ["if", ""]` (empty = unconditional). Derived flag `always` / `conditional` per edge. Plain edges merge by (src, tgt, relation), so keep a list per line, as for `lines` |
| B2 derived_from | `_handle_assignment_write` finds the written root (`_lvalue_root`) and then walks the RHS, which creates `reads_var` edges for the function | RHS variables are not linked to the LHS variable. Only "function reads a, function writes x" is known, not "x comes from a" | Collect global-variable roots while walking the RHS (a small collector instead of plain `walk`), then add `x --derived_from--> a` edges with line and function in metadata. Also handle `x = f(a)` (derived through the call, flag `via_call`) and `if (a) x = 1` (control dependency, flag `control`) |
| B3 call args | Pointer args (`f(&x)`, array decay) are resolved after all functions are known (`pending_binds`, `param_fx`): x becomes written or read depending on the callee. Value args `f(y)` only create a `reads_var` for y | No edge says "y flows into f parameter i", and no edge from a returned value to the variable it is assigned to | `passes_arg` edge variable -> function with `arg` index and parameter name (from the signature, A3). For `x = f(y)` add `derived_from` x -> y with `via_call`. For pointer args reuse the existing `pending_binds` result |

Open design questions:
- Edge count: `derived_from` can grow fast (every assignment). Option: only between global variables, skip locals, cap per function.
- Locals: chain through locals (`t = a; x = t;`) needs a per-function local alias map, otherwise the chain a -> x breaks. Start with locals collapsed into their sources.
- Graph keeps one edge per (src, tgt, relation): per-line context stays in metadata lists.
- Docs: variable page gets "Derived from" and "Feeds" tables; function page gets a condition column in the read / write tables.

Suggested implementation order: B1 (small, no new edge type) -> B3 (needs A3 signatures) -> B2 (needs local alias handling).


## Update: execution order (done)

- F2 data order view and F3 task assignment: done (tracker §60). Tasks = entry functions; `--tasks` names periods.
- Still open: struct-member level (A2), `graphify trace` CLI, derived_from in query defaults, real-module measurement.
