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
| T6 | Hook in `extract()` + config | DONE | hook, `graphify-clang.json`, and CLI flags `--clang --compile-commands --a2l --libclang` on `extract`/`update` (flags override the file; not remembered between runs) |
| T7 | `update` integration | DONE (toy) | second `graphify update` keeps all clang/A2L items |
| T8 | Registry wiring (`serve`, `affected`, `explain`) | DONE | findings 4, 7, 8, 11 |
| T9 | Tests | DONE | `tests/test_clang_integration.py` (12 pass); full suite: same 1233 failures before and after (pre-existing, environment) |
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
| 7 | metadata not printed | DONE | NODE line `kind= range= mask=`; `explain` and `get_node` print kind, A2L link, range, params; `explain` sorts writes > reads > calibration > axis > a2l before degree |
| 8 | question words | DONE | hints, intent terms, aliases from registry ("who writes X" infers context=write) |
| 9 | writer -> reader path | TODO | needs derived `feeds` edge or new command |
| 10 | absolute paths | DONE | ids and `source_file` root-relative |
| 11 | noise seeds (file node) | DONE | `_pick_seeds` drops file nodes when a real symbol also matched (walking `defined_in`/`includes` is moot: clang pass does not emit them) |
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

- Finding 9: writer -> reader path (`feeds` edge or direction-aware command)
- Run on a real-size project (first real `compile_commands.json`)
- `.github/copilot-instructions.md` text, push, optional PR to `current-v8` only if asked (T10)

## 7. First real run fell back to tree-sitter (reported 2026-10-04)

| Likely cause | How the pass now tells you |
|---|---|
| No `graphify-clang.json` / no `--clang` flag | prints `clang pass OFF (tree-sitter only): no graphify-clang.json in <root>` |
| `libclang` not installed in the Python that runs graphify (venv / pipx / uv tool) | `clang-check` shows the exact interpreter and the `pip install` line |
| libclang DLL not found (Windows) | `clang-check` FAIL line; set `"libclang": "C:/.../libclang.dll"` in the config |
| `compile_commands.json` path wrong | `clang-check` FAIL line |

Run `graphify clang-check <project root>` first. Minimal `graphify-clang.json` in the project root:

```json
{
  "compile_commands": "compile_commands.json",
  "a2l": "path/to/file.a2l",
  "libclang": "optional path to libclang.dll / .so"
}
```

## 8. Extractor choice and debugging (added 2026-10-04)

| Option | How | Effect |
|---|---|---|
| `tree-sitter` | `--extractor tree-sitter` / `"extractor": "treesitter"` / `GRAPHIFY_EXTRACTOR=tree-sitter` | clang pass skipped; original Graphify behaviour |
| `both` (default) | `--extractor both` / `--clang` / config present | tree-sitter first, clang enriches and adds |
| `clang` | `--extractor clang` / `"extractor": "clang"` | tree-sitter symbol nodes (and their edges) of every C file clang parsed are replaced by clang's. File nodes and file-to-file `imports` stay. A C file clang cannot parse keeps its tree-sitter symbols and is listed in a warning. Non-C languages stay tree-sitter. |

| Debug | How | Output |
|---|---|---|
| Verbose trace | `--clang-debug` / `"debug": true` / `GRAPHIFY_CLANG_DEBUG=1` | stderr + `graphify-out/clang_debug.log` |
| Report | same switch | `graphify-out/clang_report.json` |

The trace and report show: config source, extractor, per-file compile-flag source (`compile_commands` / `extra_args` / default), every clang error with file:line:col, pointer registrations found, member accesses ignored because the pointer is not registered, per-file node/edge counts, tree-sitter nodes removed (clang mode), A2L parse counts, unmatched code fields and unmatched A2L characteristics.

Known limit of `clang` mode: type-stub nodes with no source file (e.g. typedef names) stay from tree-sitter; a header only partly visible to clang loses tree-sitter symbols that clang never referenced.

## 9. First real run, 38,761 files (2026-10-04)

| Observation | Meaning / action |
|---|---|
| `clang pass OFF` | no `graphify-clang.json` found; user's compile DB is named `compile_commands_full.json` (set `"compile_commands"` accordingly) |
| 214 files with syntax errors | tree-sitter has no preprocessor; macro-heavy / compiler-specific C (headers especially) trips it. Message only named 5 files, so `graphify-out/syntax_errors.txt` now lists all, with counts per extension |
| Headers are extracted by tree-sitter | yes (`.h` is scanned as C). Clang reads headers through the `.c` files that include them, with the real preprocessor |

## 10. Preprocessing problems (reported 2026-10-04: "clang still has issues")

| Cause | Fix in code |
|---|---|
| Relative `-I` in `compile_commands` resolved against the wrong folder (the entry's `directory` was ignored) | include paths (`-I -isystem -iquote -include ...`) are now resolved against the entry's `directory` |
| Build-tool flags libclang chokes on (`-o`, `-MD`, `-MF`, `-c`) | removed |
| `@file.rsp` response files | expanded |
| Cross-compiler-only flags (`-mcpu=...`) | config `drop_args` (prefix match) |
| Missing defines / target / extra include folders | config `append_args` (relative `-I` is relative to the config file) |
| Which header is missing? | `clang_report.json` -> `missing_includes_top`, plus a PREPROCESSING warning in the debug log |
| One file at a time | `graphify clang-check . --file path\to\x.c` prints exact arguments, every error, missing headers |
