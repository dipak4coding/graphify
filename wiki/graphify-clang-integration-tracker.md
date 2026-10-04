# Clang + A2L integration: tracker

Branch: `feat/clang-integration` (from `docs/graphify-wiki`). Updated 2026-10-04.
Legend: DONE / PARTIAL / TODO / DECIDED.

## 1. Work packages

| ID | Topic | Status | Where |
|---|---|---|---|
| T1 | Branch + this tracker | DONE | `wiki/graphify-clang-integration-tracker.md` |
| T2 | Read sources, find hook points | DONE | `extract.py` hook before "Relativize source_file" |
| T3 | Test env + fixtures | DONE | `tests/fixtures/clang_toy/` (kku.c/.h, kku_out.c, toy.a2l); libclang via `pip install libclang` |
| T4 | Clang module | DONE (prototype) | `graphify/extractors/clang_c.py` |
| T5 | A2L scan + join | DONE (prototype) | `graphify/extractors/a2l.py` |
| T6 | Hook in `extract()` + config | PARTIAL | hook + `graphify-clang.json` done; CLI flags `--clang --compile-commands --a2l` TODO |
| T7 | `update` integration | DONE (toy) | second `graphify update` keeps all clang/A2L items |
| T8 | Registry wiring (`serve`, `affected`) | PARTIAL | see section 3 |
| T9 | Tests | DONE | `tests/test_clang_integration.py` (9 pass); full suite: same 1233 failures before and after (pre-existing, environment) |
| T10 | Docs, Copilot instructions, push | TODO | |

## 2. The 12 findings (from `graphify-enrichment-compat.md`)

| # | Finding | Status | How |
|---|---|---|---|
| 1 | read+write edge lost | DONE | clang module emits one `reads_writes_var`; `read`/`write` context filters also keep `readwrite` |
| 2 | no `source_location` | DONE | nodes `L<n>`, edges `at=file:line` + `metadata.lines` |
| 3 | no `context` | DONE | `relations.context_for()` stamps every edge |
| 4 | `affected` ignores relations | DONE | `affected.py` defaults extended from registry (verified) |
| 5 | `update` wipes items | DONE | items are stamped `_origin="ast"` by `extract()`; rebuilt per file |
| 6 | A2L text not searchable | DONE | A2L description copied to `rationale` (verified: "oil temperature threshold") |
| 7 | metadata not printed | PARTIAL | NODE line shows `kind= range= mask=`; `explain`/`get_node` metadata + relation-priority sort TODO |
| 8 | question words | DONE | hints, intent terms, aliases from registry ("who writes X" infers context=write) |
| 9 | writer -> reader path | TODO | needs derived `feeds` edge or new command |
| 10 | absolute paths | DONE | ids and `source_file` root-relative |
| 11 | noise seeds (file node) | TODO | skip File nodes in `_pick_seeds` |
| 12 | hubs not expanded | DOCUMENTED | use `explain`/`get_neighbors`; add to Copilot instructions |

## 3. Design decisions

| Topic | Decision |
|---|---|
| Role of clang | Optional second pass; tree-sitter stays the base layer |
| Activation | `graphify-clang.json` in scan root, or `GRAPHIFY_CLANG_CONFIG`; absent config or libclang = skipped, extraction unchanged |
| Failure mode | Pass failure prints one line and extraction continues |
| `_origin` | clang and A2L items use `"ast"` so tier logic needs no change |
| Merge rules | Existing tree-sitter node: gap-fill `type`/metadata (existing keys win). New clang-only node: `source_extractor=clang_only`, honors `scope`. Edges added only when both endpoints exist |
| Calibration proof | Only pointers registered via `Bios_RegisterCalibrationData` (configurable) count as calibration access |
| Registry | `graphify/relations.py` is the one place for relation name, context, hint words, affected set |
| Ptr cache | `graphify-out/cache/clang_ptr_rom.json` so incremental runs know registrations from unchanged files |

## 4. Verified on the toy (end to end through `graphify update`)

| Check | Result |
|---|---|
| Clang pass | 2 C files, +13 nodes, +23 edges |
| A2L join | 4 characteristic matches, 1 measurement |
| `query "oil temperature threshold"` | finds A2L characteristic and code field |
| `query "who writes Kku_Anf"` | context=write inferred, `reads_writes_var` edge with `at=kku.c:L18` |
| `affected Kku_Anf` | lists the readers/writers |
| `diagnose multigraph` | 38 raw edges in, none lost |
| Registration args | no spurious `reads_var` on the ROM struct |

## 5. Known limitations

| Limitation | Note |
|---|---|
| Header-only change | `update` does not re-parse C files unless a `.c` changed; use `update --force` |
| Stale A2L entries | removed only by a full extract |
| `kind=` for tree-sitter-only nodes | tree-sitter nodes have no `type`, so only enrichment nodes show it |
| Only `.c` files are translation units | headers are parsed through their `.c` |
| Not yet run on a real-size project | prototype verified on the toy only |

## 6. Open items

- CLI flags `--clang`, `--compile-commands`, `--a2l` (T6)
- `explain` / `get_node` metadata + priority sort (finding 7)
- Skip File nodes as seeds (finding 11)
- `.github/copilot-instructions.md` text, push, optional PR to `current-v8` only if asked (T10)
