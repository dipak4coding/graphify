"""Optional clang (libclang) semantic pass for C, integrated into ``extract()``.

Tree-sitter stays the base layer for C: it is fast, needs no compile flags and
tolerates files that do not compile. This pass adds what only a real compiler
front-end knows:

* which function **reads** / **writes** which global variable
  (``reads_var`` / ``writes_var`` / ``reads_writes_var``),
* **calibration** access through registered pointers
  (``reads_calibration_field``, ``exposes_calibration_field``) incl. bit-flag
  fields and loop-index expansion (AMBIGUOUS),
* interpolation **axis / input** links (``Ipo_2D`` / ``Ipo_3D`` / ``Ipo_4D``),
* calls to declaration-only (external) functions, parameter lists with
  input / output-candidate roles, return types.

It is a port of the user's ``extract_ast_graph.py`` + ``merge_into_graphify.py``
with the schema fixes found while integrating (see
``wiki/graphify-enrichment-compat.md``): every node and edge carries
``source_location``, edges carry ``context``, ids are minted exactly like the
tree-sitter extractor's, and a function that both reads and writes one variable
yields a single ``reads_writes_var`` edge (the graph keeps one edge per node pair).

Activation: a ``graphify-clang.json`` config next to the scan root (or
``GRAPHIFY_CLANG_CONFIG``), or ``graphify extract --clang ...``. Without libclang
or a config the pass is skipped and extraction is unchanged.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from graphify.extractors.base import _file_stem
from graphify.ids import make_id
from graphify.relations import context_for

CONFIG_NAME = "graphify-clang.json"
ENV_CONFIG = "GRAPHIFY_CLANG_CONFIG"
ENV_LIBCLANG = "GRAPHIFY_LIBCLANG"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ClangConfig:
    enabled: bool = True
    compile_commands: str | None = None  # path to compile_commands.json
    libclang: str | None = None  # path to libclang shared library (optional)
    a2l: str | None = None  # path to the .a2l calibration file (optional)
    scope: list = field(default_factory=list)  # substrings; limits ADDED clang-only nodes
    extra_args: list = field(default_factory=list)  # fallback clang args (-I.., -include ..)
    register_functions: list = field(
        default_factory=lambda: ["Bios_RegisterCalibrationData", "RegisterCalibrationData"]
    )
    base_dir: str = "."  # directory the relative paths above resolve against

    def resolve(self, value: str | None) -> Path | None:
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else (Path(self.base_dir) / p)


_runtime_override: dict | None = None


def set_runtime_config(**kwargs) -> None:
    """Set by the CLI (``graphify extract --clang ...``); wins over the config file."""
    global _runtime_override
    _runtime_override = {k: v for k, v in kwargs.items() if v is not None}


def _config_from_dict(data: dict, base_dir: Path) -> ClangConfig:
    cfg = ClangConfig(base_dir=str(base_dir))
    for key in ("enabled", "compile_commands", "libclang", "a2l"):
        if key in data:
            setattr(cfg, key, data[key])
    for key in ("scope", "extra_args", "register_functions"):
        if key in data and data[key]:
            setattr(cfg, key, list(data[key]))
    return cfg


def load_config(root: Path | None) -> ClangConfig | None:
    """Config discovery: CLI override > $GRAPHIFY_CLANG_CONFIG > <root>/graphify-clang.json > ./graphify-clang.json."""
    if _runtime_override:
        cfg = _config_from_dict(_runtime_override, Path.cwd())
        cfg.enabled = bool(_runtime_override.get("enabled", True))
        return cfg
    candidates = []
    env = os.environ.get(ENV_CONFIG)
    if env:
        candidates.append(Path(env))
    if root:
        candidates.append(Path(root) / CONFIG_NAME)
    candidates.append(Path.cwd() / CONFIG_NAME)
    for cand in candidates:
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"[graphify] clang config {cand} unreadable: {exc}", file=sys.stderr)
                return None
            cfg = _config_from_dict(data, cand.resolve().parent)
            return cfg if cfg.enabled else None
    return None


_warned: set = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"[graphify] {msg}", file=sys.stderr)


def _load_cindex(cfg: ClangConfig):
    try:
        import clang.cindex as cindex
    except ImportError:
        _warn_once("nolibclang", "clang pass skipped: python package 'libclang' is not installed "
                                  "(pip install libclang). Using tree-sitter only.")
        return None
    lib = os.environ.get(ENV_LIBCLANG) or (str(cfg.resolve(cfg.libclang)) if cfg.libclang else None)
    if lib:
        try:
            cindex.Config.set_library_file(lib)
        except Exception:  # already initialised - the first setting wins
            pass
    try:
        cindex.Index.create()
    except Exception as exc:
        _warn_once("libclang_load", f"clang pass skipped: libclang could not be loaded ({exc}). "
                                    "Set libclang in graphify-clang.json. Using tree-sitter only.")
        return None
    return cindex


def _load_compile_commands(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    by_file = {}
    for entry in entries:
        directory = Path(entry.get("directory") or ".")
        file_path = Path(entry["file"])
        if not file_path.is_absolute():
            file_path = directory / file_path
        key = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
        if "arguments" in entry:
            args = list(entry["arguments"])
        else:
            args = shlex.split(entry["command"], posix=(os.name != "nt"))
        by_file[key] = (args, str(directory))
    return by_file


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_IDX_PLACEHOLDER = "{IDX}"

_IPO_LOOKUP_ROLES = {
    "Ipo_2D": ["x_axis", "curve_data", "input_x", "length_x"],
    "Ipo_3D": ["x_axis", "y_axis", "map_data", "input_x", "input_y", "length_x", "length_y"],
    "Ipo_4D": ["x_axis", "y_axis", "z_axis", "cuboid_data", "input_x", "input_y", "input_z",
               "length_x", "length_y", "length_z"],
}
_IPO_AXIS_ROLES = {"x_axis", "y_axis", "z_axis"}
_IPO_DATA_ROLES = {"curve_data", "map_data", "cuboid_data"}
_ASSIGNMENT_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
_INT_BINOPS = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
               "<<": lambda a, b: a << b}
_MAX_LINES_PER_EDGE = 8


class ClangExtractor:
    def __init__(self, cindex, root: Path, cfg: ClangConfig):
        self.ci = cindex
        self.K = cindex.CursorKind
        self.T = cindex.TypeKind
        self.root = root
        self.cfg = cfg
        self.register_funcs = set(cfg.register_functions)
        self.cc_index = None
        cc = cfg.resolve(cfg.compile_commands)
        if cc is not None:
            if cc.is_file():
                self.cc_index = _load_compile_commands(cc)
            else:
                _warn_once("nocc", f"compile_commands.json not found at {cc}; using extra_args / defaults")
        self.nodes: dict = {}
        self.edges: dict = {}  # (src, tgt, rel) -> edge dict
        self.decl_only: set = set()
        self.ptr_to_rom: dict = {}
        self.ptr_maps: dict = {}  # rel file -> {ptr: rom} (for the incremental cache)
        self.parse_errors = 0
        self.files_parsed = 0
        self._pass_pt = {self.K.UNEXPOSED_EXPR, self.K.PAREN_EXPR, self.K.CSTYLE_CAST_EXPR,
                         self.K.UNARY_OPERATOR, self.K.CXX_UNARY_EXPR}
        self._array_kinds = {self.T.CONSTANTARRAY, self.T.INCOMPLETEARRAY, self.T.VARIABLEARRAY,
                             self.T.DEPENDENTSIZEDARRAY}

    # ---- ids / paths -------------------------------------------------------

    def _rel(self, path: str):
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root)
        except (ValueError, OSError):
            return None

    def stem(self, path: str) -> str:
        rel = self._rel(path)
        return _file_stem(rel if rel is not None else Path(path))

    def sf(self, path: str) -> str:
        rel = self._rel(path)
        return rel.as_posix() if rel is not None else str(path)

    # ---- graph building ------------------------------------------------------

    def add_node(self, node_id, kind, source_file, name=None, line=None, metadata=None,
                 definition=False, decl_only=False):
        meta = dict(metadata or {})
        existing = self.nodes.get(node_id)
        if existing is None:
            node = {
                "id": node_id,
                "label": name or node_id,
                "file_type": "code",
                "type": kind,
                "source_file": self.sf(source_file) if source_file else "",
                "metadata": meta,
            }
            if line:
                node["source_location"] = f"L{line}"
            self.nodes[node_id] = node
            if decl_only:
                self.decl_only.add(node_id)
            return node_id
        existing["metadata"].update({k: v for k, v in meta.items() if k not in existing["metadata"] or definition})
        if definition and source_file:
            existing["source_file"] = self.sf(source_file)
            if line:
                existing["source_location"] = f"L{line}"
            self.decl_only.discard(node_id)
        elif line and "source_location" not in existing:
            existing["source_location"] = f"L{line}"
        return node_id

    def add_edge(self, src, tgt, relation, source_file, line=None, confidence="EXTRACTED", metadata=None):
        if src == tgt:
            return
        key = (src, tgt, relation)
        edge = self.edges.get(key)
        if edge is None:
            edge = {
                "source": src, "target": tgt, "relation": relation, "confidence": confidence,
                "source_file": self.sf(source_file) if source_file else "",
                "weight": 1.0, "metadata": dict(metadata or {}),
            }
            ctx = context_for(relation)
            if ctx:
                edge["context"] = ctx
            if line:
                edge["source_location"] = f"L{line}"
                edge["metadata"]["lines"] = [line]
            self.edges[key] = edge
        else:
            if line:
                lines = edge["metadata"].setdefault("lines", [])
                if line not in lines and len(lines) < _MAX_LINES_PER_EDGE:
                    lines.append(line)
            if confidence == "AMBIGUOUS" or edge["confidence"] == "AMBIGUOUS":
                edge["confidence"] = "AMBIGUOUS"

    # ---- clang helpers -----------------------------------------------------------

    def _unwrap(self, c):
        while c.kind in self._pass_pt:
            kids = list(c.get_children())
            if not kids:
                return c
            c = kids[0]
        return c

    def _operator_str(self, cursor):
        children = list(cursor.get_children())
        if len(children) != 2:
            return None
        lhs, rhs = children
        toks = [t for t in cursor.get_tokens()
                if t.extent.start.offset >= lhs.extent.end.offset and t.extent.end.offset <= rhs.extent.start.offset]
        return "".join(t.spelling for t in toks)

    def _eval_int(self, cursor):
        if cursor.kind in self._pass_pt:
            kids = list(cursor.get_children())
            return self._eval_int(kids[0]) if kids else None
        if cursor.kind == self.K.INTEGER_LITERAL:
            toks = [t.spelling for t in cursor.get_tokens()]
            if not toks:
                return None
            try:
                return int(toks[0].rstrip("uUlL"), 0)
            except ValueError:
                return None
        if cursor.kind == self.K.DECL_REF_EXPR:
            ref = cursor.referenced
            if ref is not None and ref.kind == self.K.ENUM_CONSTANT_DECL:
                return ref.enum_value
            return None
        if cursor.kind == self.K.BINARY_OPERATOR:
            op = self._operator_str(cursor)
            kids = list(cursor.get_children())
            if len(kids) != 2 or op not in _INT_BINOPS:
                return None
            lhs, rhs = self._eval_int(kids[0]), self._eval_int(kids[1])
            if lhs is None or rhs is None:
                return None
            return _INT_BINOPS[op](lhs, rhs)
        return None

    def _param_role(self, ptype):
        canonical = ptype.get_canonical()
        if canonical.kind == self.T.POINTER:
            is_const = canonical.get_pointee().is_const_qualified()
        elif canonical.kind in self._array_kinds:
            is_const = canonical.is_const_qualified()
        else:
            return "input"
        return "input" if is_const else "output_candidate"

    def _parameters(self, cursor):
        out = []
        for child in cursor.get_children():
            if child.kind == self.K.PARM_DECL:
                out.append({
                    "name": child.spelling or "",
                    "type": child.type.spelling if child.type else "",
                    "role": self._param_role(child.type) if child.type else "input",
                })
        return out

    def _find_base_decl_ref(self, cursor):
        if cursor.kind == self.K.DECL_REF_EXPR:
            return cursor
        for child in cursor.get_children():
            found = self._find_base_decl_ref(child)
            if found is not None:
                return found
        return None

    def _find_array_size(self, cursor):
        size = cursor.type.get_array_size()
        if size >= 0:
            return size
        if cursor.kind in self._pass_pt:
            kids = list(cursor.get_children())
            if kids:
                return self._find_array_size(kids[0])
        return -1

    def _resolve_access_path(self, cursor):
        """-> (base DECL_REF_EXPR | None, path suffix, expand | None); see extract_ast_graph.py."""
        K = self.K
        if cursor.kind == K.DECL_REF_EXPR:
            return cursor, "", None
        if cursor.kind == K.MEMBER_REF_EXPR:
            kids = list(cursor.get_children())
            if not kids:
                return None, "", None
            base, base_path, expand = self._resolve_access_path(kids[0])
            return base, f"{base_path}.{cursor.spelling}", expand
        if cursor.kind == K.ARRAY_SUBSCRIPT_EXPR:
            kids = list(cursor.get_children())
            if len(kids) < 2:
                return None, "", None
            base, base_path, expand = self._resolve_access_path(kids[0])
            idx = kids[1]
            if idx.kind == K.INTEGER_LITERAL:
                toks = [t.spelling for t in idx.get_tokens()]
                return base, f"{base_path}[{toks[0] if toks else '?'}]", expand
            folded = self._eval_int(idx)
            if folded is not None:
                return base, f"{base_path}[{folded}]", expand
            if expand is not None:
                return base, f"{base_path}[?]", expand
            size = self._find_array_size(kids[0])
            if size > 0:
                toks = [t.spelling for t in idx.get_tokens()]
                return base, f"{base_path}[{_IDX_PLACEHOLDER}]", (_IDX_PLACEHOLDER, size, " ".join(toks) or "?")
            return base, f"{base_path}[?]", None
        if cursor.kind in self._pass_pt:
            kids = list(cursor.get_children())
            if kids:
                return self._resolve_access_path(kids[0])
            return None, "", None
        return None, "", None

    # ---- symbol nodes -----------------------------------------------------------

    def _global_var(self, ref, fallback_file):
        """Node id for a file-scope variable referenced via DECL_REF_EXPR `ref`."""
        defn = None
        try:
            defn = ref.get_definition()
        except Exception:
            pass
        decl = defn if defn is not None else (ref.canonical if ref.canonical is not None else ref)
        loc = decl.location
        vfile = str(loc.file) if loc.file else fallback_file
        node_id = make_id(self.stem(vfile), decl.spelling)
        self.add_node(node_id, "variable", vfile, name=decl.spelling, line=loc.line,
                      definition=defn is not None, decl_only=defn is None)
        return node_id

    def _is_global_var(self, ref):
        return (ref is not None and ref.kind == self.K.VAR_DECL and ref.semantic_parent is not None
                and ref.semantic_parent.kind == self.K.TRANSLATION_UNIT)

    # ---- handlers ----------------------------------------------------------------

    def _handle_calibration_access(self, cursor, fn_id, sfile):
        if fn_id is None:
            return False
        base, path, expand = self._resolve_access_path(cursor)
        if base is None or not path or "." not in path:
            return False
        ref = base.referenced
        if ref is None or ref.kind != self.K.VAR_DECL:
            return False
        canonical = ref.canonical if ref.canonical is not None else ref
        base_name = canonical.spelling
        if base_name not in self.ptr_to_rom:
            return False  # ordinary struct: only a REGISTERED calibration pointer proves calibration
        line = cursor.location.line
        base_id = self._global_var(ref, sfile)
        self.add_edge(fn_id, base_id, "reads_var", sfile, line)
        logical_base = self.ptr_to_rom[base_name]

        def emit(concrete_path, confidence, extra=None):
            cal_path = f"{logical_base}{concrete_path}"
            cal_id = make_id("calfield", cal_path)
            meta = {"calibration_path": cal_path, "accessed_via": base_name, "source_extractor": "clang"}
            if extra:
                meta.update(extra)
            self.add_node(cal_id, "calibration_field", sfile, name=cal_path, line=line, metadata=meta)
            self.add_edge(fn_id, cal_id, "reads_calibration_field", sfile, line, confidence)
            self.add_edge(base_id, cal_id, "exposes_calibration_field", sfile, line, confidence)

        if expand is not None:
            placeholder, size, index_expr = expand
            for i in range(size):
                emit(path.replace(placeholder, str(i)), "AMBIGUOUS",
                     {"ambiguous_index": True, "index_expression": index_expr, "possible_indices": size})
        else:
            emit(path, "AMBIGUOUS" if "?" in path else "EXTRACTED")
        return True

    def _handle_bitmask_access(self, cursor, fn_id, sfile):
        if fn_id is None or self._operator_str(cursor) != "&":
            return False
        kids = list(cursor.get_children())
        if len(kids) != 2:
            return False
        for member_side, mask_side in (kids, list(reversed(kids))):
            base, path, expand = self._resolve_access_path(member_side)
            if base is None or "." not in path or expand is not None:
                continue
            mask = self._eval_int(mask_side)
            if mask is None:
                continue
            ref = base.referenced
            if ref is None or ref.kind != self.K.VAR_DECL:
                continue
            canonical = ref.canonical if ref.canonical is not None else ref
            if canonical.spelling not in self.ptr_to_rom:
                continue
            line = cursor.location.line
            base_id = self._global_var(ref, sfile)
            self.add_edge(fn_id, base_id, "reads_var", sfile, line)
            cal_path = f"{self.ptr_to_rom[canonical.spelling]}{path}#{mask}"
            cal_id = make_id("calfield", cal_path)
            self.add_node(cal_id, "calibration_field", sfile, name=cal_path, line=line, metadata={
                "calibration_path": cal_path, "accessed_via": canonical.spelling,
                "bit_mask": mask, "source_extractor": "clang"})
            self.add_edge(fn_id, cal_id, "reads_calibration_field", sfile, line)
            self.add_edge(base_id, cal_id, "exposes_calibration_field", sfile, line)
            return True
        return False

    def _resolve_cal_field(self, arg):
        expr = self._unwrap(arg)
        base, path, expand = self._resolve_access_path(expr)
        if base is None or "." not in path or expand is not None:
            return None
        ref = base.referenced
        if ref is None or ref.kind != self.K.VAR_DECL:
            return None
        canonical = ref.canonical if ref.canonical is not None else ref
        if canonical.spelling not in self.ptr_to_rom:
            return None
        cal_path = f"{self.ptr_to_rom[canonical.spelling]}{path}"
        return make_id("calfield", cal_path), cal_path

    def _resolve_var_or_cal(self, arg, sfile):
        cal = self._resolve_cal_field(arg)
        if cal is not None:
            return cal[0], cal[1], "calibration_field"
        expr = self._unwrap(arg)
        if expr.kind != self.K.DECL_REF_EXPR:
            return None
        ref = expr.referenced
        if not self._is_global_var(ref):
            return None
        return self._global_var(ref, sfile), ref.spelling, "variable"

    def _handle_ipo(self, cursor, fn_id, sfile):
        if fn_id is None:
            return
        callee = cursor.referenced
        if callee is None or not callee.spelling:
            return
        roles = next((rl for prefix, rl in _IPO_LOOKUP_ROLES.items() if callee.spelling.startswith(prefix)), None)
        if roles is None:
            return
        args = list(cursor.get_children())[1:]
        if len(args) < len(roles):
            return
        line = cursor.location.line
        resolved = {}
        for arg, role in zip(args, roles):
            if role in _IPO_AXIS_ROLES or role in _IPO_DATA_ROLES:
                res = self._resolve_cal_field(arg)
                if res is not None:
                    resolved[role] = res
        data_role = next((r for r in roles if r in _IPO_DATA_ROLES), None)
        if data_role is None or data_role not in resolved:
            return
        map_id, map_path = resolved[data_role]
        self.add_node(map_id, "calibration_field", sfile, name=map_path, line=line,
                      metadata={"calibration_path": map_path, "source_extractor": "clang"})
        for axis_role in ("x_axis", "y_axis", "z_axis"):
            if axis_role in resolved:
                ax_id, ax_path = resolved[axis_role]
                self.add_node(ax_id, "calibration_field", sfile, name=ax_path, line=line,
                              metadata={"calibration_path": ax_path, "source_extractor": "clang"})
                self.add_edge(map_id, ax_id, axis_role, sfile, line)
        for role in ("input_x", "input_y", "input_z", "length_x", "length_y", "length_z"):
            if role not in roles:
                continue
            idx = roles.index(role)
            if idx >= len(args):
                continue
            res = self._resolve_var_or_cal(args[idx], sfile)
            if res is None:
                continue
            nid, label, kind = res
            self.add_node(nid, kind, sfile, name=label)
            self.add_edge(map_id, nid, role, sfile, line)

    def _handle_assignment_write(self, cursor, fn_id, sfile):
        if fn_id is None:
            return False
        op = self._operator_str(cursor)
        if op not in _ASSIGNMENT_OPS:
            return False
        kids = list(cursor.get_children())
        if len(kids) != 2:
            return False
        lhs, rhs = kids
        target = self._unwrap(lhs)
        handled_lhs = False
        if target.kind == self.K.DECL_REF_EXPR:
            ref = target.referenced
            if self._is_global_var(ref):
                line = cursor.location.line
                var_id = self._global_var(ref, sfile)
                self.add_edge(fn_id, var_id, "writes_var", sfile, line)
                if op != "=":
                    self.add_edge(fn_id, var_id, "reads_var", sfile, line)
                handled_lhs = True
        self.walk(rhs, fn_id, sfile)
        if not handled_lhs:
            self.walk(lhs, fn_id, sfile)
        return True

    # ---- walk ---------------------------------------------------------------------

    def walk(self, cursor, fn_id=None, sfile=None):
        K = self.K
        kind = cursor.kind
        if kind in (K.MEMBER_REF_EXPR, K.ARRAY_SUBSCRIPT_EXPR):
            if self._handle_calibration_access(cursor, fn_id, sfile):
                return
        if kind == K.BINARY_OPERATOR:
            if self._handle_bitmask_access(cursor, fn_id, sfile):
                return
            if self._handle_assignment_write(cursor, fn_id, sfile):
                return
        if kind == K.COMPOUND_ASSIGNMENT_OPERATOR:
            if self._handle_assignment_write(cursor, fn_id, sfile):
                return

        if kind == K.FUNCTION_DECL and cursor.is_definition():
            loc = cursor.location
            fpath = str(loc.file) if loc.file else (sfile or "")
            func_id = make_id(self.stem(fpath), cursor.spelling)
            ret = cursor.result_type.spelling if cursor.result_type else ""
            self.add_node(func_id, "function", fpath, name=f"{cursor.spelling}()", line=loc.line,
                          metadata={"return_type": ret, "has_definition": True,
                                    "parameters": self._parameters(cursor), "source_extractor": "clang"},
                          definition=True)
            for child in cursor.get_children():
                self.walk(child, func_id, fpath)
            return

        if kind == K.CALL_EXPR and fn_id is not None:
            callee = cursor.referenced
            if callee is not None and callee.spelling:
                defn = None
                try:
                    defn = callee.get_definition()
                except Exception:
                    pass
                target = defn if defn is not None else callee
                loc = target.location
                cfile = str(loc.file) if loc.file else (sfile or "")
                callee_id = make_id(self.stem(cfile), callee.spelling) if cfile else make_id("unknown", callee.spelling)
                self.add_node(callee_id, "function", cfile, name=f"{callee.spelling}()", line=loc.line,
                              definition=defn is not None, decl_only=defn is None,
                              metadata={"source_extractor": "clang"})
                self.add_edge(fn_id, callee_id, "calls", sfile, cursor.location.line)
            self._handle_ipo(cursor, fn_id, sfile)
            if cursor.spelling in self.register_funcs:
                return  # &ptr / &rom are registration plumbing, not data reads

        if kind == K.DECL_REF_EXPR and fn_id is not None:
            ref = cursor.referenced
            if self._is_global_var(ref):
                var_id = self._global_var(ref, sfile)
                self.add_edge(fn_id, var_id, "reads_var", sfile, cursor.location.line)

        if kind == K.VAR_DECL and fn_id is None and self._is_global_var(cursor):
            var_id = self._global_var(cursor, sfile)
            # keep the variable linked to its file like Graphify links functions: file --contains--> var
            self._pending_contains.append((cursor, var_id))

        for child in cursor.get_children():
            self.walk(child, fn_id, sfile)

    # ---- translation units ------------------------------------------------------------

    def _args_for(self, path: Path):
        if self.cc_index is not None:
            hit = self.cc_index.get(os.path.normcase(os.path.normpath(os.path.abspath(path))))
            if hit:
                args, _directory = hit
                if args and not args[0].startswith("-"):
                    args = args[1:]
                if args and os.path.normcase(os.path.normpath(os.path.abspath(args[-1]))) == \
                        os.path.normcase(os.path.normpath(os.path.abspath(path))):
                    args = args[:-1]
                return [a for a in args if a != "-c"]
            _warn_once(f"cc:{path}", f"{path.name} not in compile_commands.json; using extra_args")
        args = list(self.cfg.extra_args)
        if not args:
            args = [f"-I{path.parent}"]
        return args

    def _scan_registrations(self, tu_cursor) -> dict:
        found: dict = {}

        def visit(cursor):
            if cursor.kind == self.K.CALL_EXPR and cursor.spelling in self.register_funcs:
                args = list(cursor.get_children())[1:]
                if len(args) >= 2:
                    ptr = self._find_base_decl_ref(args[0])
                    rom = self._find_base_decl_ref(args[1])
                    if ptr is not None and rom is not None:
                        found[ptr.spelling] = rom.spelling
            if cursor.kind == self.K.VAR_DECL:
                for child in cursor.get_children():
                    if child.kind == self.K.TYPE_REF:
                        continue
                    rom = self._find_base_decl_ref(child)
                    if rom is not None and rom.spelling != cursor.spelling:
                        found[cursor.spelling] = rom.spelling
                    break
            for child in cursor.get_children():
                visit(child)

        visit(tu_cursor)
        return found

    @staticmethod
    def _skip(cursor) -> bool:
        loc = cursor.location
        if loc.file is None:
            return True
        try:
            return bool(loc.is_in_system_header)
        except AttributeError:
            return False

    def parse(self, path: Path):
        if not path.is_file():
            return None
        index = self.ci.Index.create()
        try:
            tu = index.parse(str(path), args=self._args_for(path))
        except self.ci.TranslationUnitLoadError as exc:
            _warn_once(f"tu:{path}", f"clang could not load {path.name}: {exc}")
            return None
        errors = [d for d in tu.diagnostics if d.severity >= self.ci.Diagnostic.Error]
        if errors:
            self.parse_errors += len(errors)
            _warn_once(f"diag:{path}", f"{path.name}: {len(errors)} clang error(s) "
                                       f"(first: {errors[0].spelling}); results may be partial")
        return tu

    def run(self, c_files: list, cached_ptr_maps: dict) -> None:
        tus = []
        for path in c_files:
            tu = self.parse(path)
            if tu is not None:
                tus.append((path, tu))
                self.files_parsed += 1
        # pass 1: calibration pointer registrations across ALL parsed files + cached ones
        for path, tu in tus:
            rel = self.sf(str(path))
            self.ptr_maps[rel] = self._scan_registrations(tu.cursor)
        merged = {}
        for rel, mapping in cached_ptr_maps.items():
            if rel not in self.ptr_maps:
                merged.update(mapping)
        for mapping in self.ptr_maps.values():
            merged.update(mapping)
        self.ptr_to_rom = merged
        # pass 2: structure walk
        for path, tu in tus:
            self._pending_contains = []
            sfile = str(path)
            for child in tu.cursor.get_children():
                if self._skip(child):
                    continue
                self.walk(child, None, sfile)

    # ---- finalize -----------------------------------------------------------------------

    def finalize(self):
        """-> (nodes, edges): unify decl-only symbols, collapse read+write pairs."""
        by_name: dict = {}
        for nid, n in self.nodes.items():
            if n["type"] in ("function", "variable") and nid not in self.decl_only:
                by_name.setdefault((n["type"], n["label"]), []).append(nid)
        remap = {}
        for nid in list(self.decl_only):
            n = self.nodes[nid]
            defs = by_name.get((n["type"], n["label"]), [])
            if len(defs) == 1:
                remap[nid] = defs[0]
        for nid in remap:
            self.nodes.pop(nid, None)

        edges: dict = {}
        for (src, tgt, rel), e in self.edges.items():
            src, tgt = remap.get(src, src), remap.get(tgt, tgt)
            if src == tgt or src not in self.nodes or tgt not in self.nodes:
                continue
            key = (src, tgt, rel)
            if key in edges:
                prev = edges[key]
                for ln in e["metadata"].get("lines", []):
                    lines = prev["metadata"].setdefault("lines", [])
                    if ln not in lines and len(lines) < _MAX_LINES_PER_EDGE:
                        lines.append(ln)
                if e["confidence"] == "AMBIGUOUS":
                    prev["confidence"] = "AMBIGUOUS"
            else:
                e = dict(e, source=src, target=tgt)
                edges[key] = e

        # one edge per node pair survives graph loading: merge reads_var + writes_var
        for (src, tgt, rel) in list(edges):
            if rel != "reads_var":
                continue
            w_key = (src, tgt, "writes_var")
            if w_key not in edges:
                continue
            r, w = edges.pop((src, tgt, "reads_var")), edges.pop(w_key)
            lines = sorted(set(r["metadata"].get("lines", [])) | set(w["metadata"].get("lines", [])))
            merged = dict(r, relation="reads_writes_var", context=context_for("reads_writes_var"))
            merged["metadata"] = {"lines": lines[:_MAX_LINES_PER_EDGE], "merged_from": ["reads_var", "writes_var"]}
            if lines:
                merged["source_location"] = f"L{lines[0]}"
            edges[(src, tgt, "reads_writes_var")] = merged

        return list(self.nodes.values()), list(edges.values())


# ---------------------------------------------------------------------------
# Hook called from extract()
# ---------------------------------------------------------------------------


def _cache_file(root: Path) -> Path:
    from graphify.paths import out_path
    base = out_path("cache")
    if not base.is_absolute():
        base = root / base
    return base / "clang_ptr_rom.json"


def run_clang_pass(paths, root, all_nodes: list, all_edges: list):
    """Run the clang (+ A2L) pass and merge its output into ``all_nodes`` / ``all_edges``.

    Returns a stats dict, or None when the pass is not configured / not available.
    """
    root = Path(root).resolve()
    cfg = load_config(root)
    if cfg is None:
        return None
    c_files = [Path(p).resolve() for p in paths if Path(p).suffix.lower() == ".c"]
    stats: dict = {"files": 0}
    if c_files:
        cindex = _load_cindex(cfg)
        if cindex is None:
            return None
        ex = ClangExtractor(cindex, root, cfg)
        cache_path = _cache_file(root)
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        except (OSError, ValueError):
            cached = {}
        ex.run(c_files, cached)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({**cached, **ex.ptr_maps}, indent=1), encoding="utf-8")
        except OSError:
            pass
        nodes, edges = ex.finalize()
        stats.update(_merge_into(all_nodes, all_edges, nodes, edges, cfg, ex))
        stats["files"] = ex.files_parsed
        stats["ptr_to_rom"] = len(ex.ptr_to_rom)

    a2l_path = cfg.resolve(cfg.a2l)
    if a2l_path is not None:
        if not a2l_path.is_file():
            _warn_once("noa2l", f"A2L file not found: {a2l_path}")
        else:
            from graphify.extractors.a2l import join_a2l, scan_a2l
            rel = None
            try:
                rel = a2l_path.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = str(a2l_path.resolve())
            stats["a2l"] = join_a2l(all_nodes, all_edges, scan_a2l(str(a2l_path)), rel)

    a2l = stats.get("a2l") or {}
    print(
        f"[graphify] clang pass: {stats.get('files', 0)} C file(s), +{stats.get('nodes_added', 0)} nodes "
        f"({stats.get('nodes_enriched', 0)} enriched), +{stats.get('edges_added', 0)} edges"
        + (f", A2L matched {a2l.get('characteristics', 0)} characteristics / "
           f"{a2l.get('measurements', 0)} measurements" if a2l else ""),
        file=sys.stderr,
    )
    return stats


def _merge_into(all_nodes, all_edges, nodes, edges, cfg: ClangConfig, ex: ClangExtractor) -> dict:
    from graphify.extract import _file_node_id

    existing = {n["id"]: n for n in all_nodes if n.get("id")}
    scope = [s.lower() for s in cfg.scope]
    added = enriched = 0
    kept_ids = set(existing)
    for node in nodes:
        nid = node["id"]
        if nid in existing:
            tgt = existing[nid]
            if not tgt.get("type"):
                tgt["type"] = node["type"]
            meta = tgt.setdefault("metadata", {})
            for k, v in node["metadata"].items():
                meta.setdefault(k, v)
            if not tgt.get("source_location") and node.get("source_location"):
                tgt["source_location"] = node["source_location"]
            enriched += 1
            continue
        if scope:
            hay = f"{node.get('source_file', '')} {node.get('label', '')}".lower()
            if not any(s in hay for s in scope):
                continue
        node["metadata"]["source_extractor"] = "clang_only"
        all_nodes.append(node)
        kept_ids.add(nid)
        added += 1

    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in all_edges}
    e_added = 0
    for edge in edges:
        key = (edge["source"], edge["target"], edge["relation"])
        if key in seen or edge["source"] not in kept_ids or edge["target"] not in kept_ids:
            continue
        seen.add(key)
        all_edges.append(edge)
        e_added += 1

    # file --contains--> variable (functions already have it from tree-sitter)
    for node in nodes:
        if node["type"] != "variable" or node["id"] not in kept_ids or not node.get("source_file"):
            continue
        sf = Path(node["source_file"])
        file_id = _file_node_id(sf) if not sf.is_absolute() else None
        if file_id and file_id in kept_ids and file_id != node["id"]:
            key = (file_id, node["id"], "contains")
            if key not in seen:
                seen.add(key)
                all_edges.append({
                    "source": file_id, "target": node["id"], "relation": "contains",
                    "confidence": "EXTRACTED", "source_file": node["source_file"], "weight": 1.0,
                    **({"source_location": node["source_location"]} if node.get("source_location") else {}),
                })
                e_added += 1
    return {"nodes_added": added, "nodes_enriched": enriched, "edges_added": e_added}
