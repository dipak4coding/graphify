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
import logging
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from graphify.extractors.base import _file_stem
from graphify.ids import make_id
from graphify.relations import context_for

LOG = logging.getLogger("graphify.clang")
CONFIG_NAME = "graphify-clang.json"
ENV_DEBUG = "GRAPHIFY_CLANG_DEBUG"
ENV_EXTRACTOR = "GRAPHIFY_EXTRACTOR"
EXTRACTORS = ("treesitter", "clang", "both")
_EXTRACTOR_ALIASES = {
    "tree-sitter": "treesitter", "treesitter": "treesitter", "ts": "treesitter",
    "clang": "clang", "clang_only": "clang", "clang-only": "clang",
    "both": "both", "hybrid": "both",
}


def normalize_extractor(value) -> str:
    key = str(value).strip().lower()
    if key not in _EXTRACTOR_ALIASES:
        raise ValueError(f"unknown extractor {value!r}; use tree-sitter, clang or both")
    return _EXTRACTOR_ALIASES[key]
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
    drop_args: list = field(default_factory=list)  # compile-command args to remove (prefix match), e.g. ["-mcpu", "--option"]
    append_args: list = field(default_factory=list)  # always appended, e.g. ["-DTRICORE=1", "--target=arm-none-eabi"]
    extractor: str = "both"  # treesitter | clang | both  (clang = replace tree-sitter's C output)
    header_declarations: bool = True  # also add `extern` variables / function prototypes declared in headers (file --contains--> symbol)
    debug: bool = False  # verbose log to stderr + graphify-out/clang_debug.log + clang_report.json
    source: str = "defaults"  # where the config came from (for the debug log)

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
    for key in ("enabled", "compile_commands", "libclang", "a2l", "debug", "header_declarations"):
        if key in data:
            setattr(cfg, key, data[key])
    if "extractor" in data:
        cfg.extractor = normalize_extractor(data["extractor"])
    for key in ("scope", "extra_args", "register_functions", "drop_args", "append_args"):
        if key in data and data[key]:
            setattr(cfg, key, list(data[key]))
    return cfg


def _override() -> dict:
    """CLI flags (set_runtime_config) merged with environment variables."""
    ov = dict(_runtime_override or {})
    env_ex = os.environ.get(ENV_EXTRACTOR)
    if env_ex and "extractor" not in ov:
        ov["extractor"] = normalize_extractor(env_ex)
        if ov["extractor"] != "treesitter":
            ov.setdefault("enabled", True)
    if os.environ.get(ENV_DEBUG, "").lower() in ("1", "true", "yes"):
        ov.setdefault("debug", True)
    return ov


def _apply_override(cfg: ClangConfig, ov: dict) -> ClangConfig:
    """CLI/env wins over the config file; absolute paths from the CLI need no base_dir."""
    for key in ("compile_commands", "libclang", "a2l", "extractor", "debug"):
        if key in ov:
            setattr(cfg, key, ov[key])
    if "enabled" in ov:
        cfg.enabled = bool(ov["enabled"])
    return cfg


def load_config(root: Path | None) -> ClangConfig | None:
    """Config discovery: CLI/env > $GRAPHIFY_CLANG_CONFIG > <root>/graphify-clang.json > ./graphify-clang.json."""
    ov = _override()
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
            cfg.source = str(cand)
            cfg = _apply_override(cfg, ov)
            return cfg if cfg.enabled else None
    if ov:
        cfg = _apply_override(ClangConfig(base_dir=str(Path.cwd()), source="command line / environment only"), ov)
        return cfg if (cfg.enabled or cfg.extractor == "treesitter") else None
    return None


def _setup_logging(cfg: ClangConfig, root: Path) -> Path | None:
    """Debug mode: full trace to stderr AND graphify-out/clang_debug.log. Returns the log path."""
    for h in list(LOG.handlers):
        LOG.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    LOG.propagate = False
    if not cfg.debug:
        LOG.setLevel(logging.WARNING)
        return None
    LOG.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[graphify clang] %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    LOG.addHandler(sh)
    log_path = None
    try:
        from graphify.paths import out_path
        out = out_path()
        out = out if out.is_absolute() else root / out
        out.mkdir(parents=True, exist_ok=True)
        log_path = out / "clang_debug.log"
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(fh)
    except Exception as exc:  # logging must never break extraction
        LOG.warning("could not open debug log file: %s", exc)
    return log_path


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

_PATH_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-F", "-isysroot", "--sysroot=")
_DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ", "-Xclang"}
_DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP", "-MG", "-M", "-MM", "-S", "-E"}


def _expand_response_files(args: list, directory: str) -> list:
    out = []
    for a in args:
        if a.startswith("@") and len(a) > 1:
            rsp = Path(a[1:])
            rsp = rsp if rsp.is_absolute() else Path(directory) / rsp
            try:
                out.extend(shlex.split(rsp.read_text(encoding="utf-8", errors="replace"), posix=(os.name != "nt")))
                continue
            except OSError:
                LOG.warning("response file not readable: %s", rsp)
        out.append(a)
    return out


def normalize_args(args: list, directory: str, cfg: "ClangConfig", source_file: Path) -> list:
    """Turn a build command into libclang parse args.

    * relative include paths are resolved against the compile command's ``directory``
      (the #1 cause of 'file not found' for entries from a build tool),
    * options a parse does not need or that libclang rejects (-o, -MD, -c, ...) are removed,
    * ``drop_args`` / ``append_args`` from the config are applied.
    """
    args = _expand_response_files(list(args), directory)
    base = Path(directory)
    src_norm = os.path.normcase(os.path.normpath(os.path.abspath(source_file)))
    out: list = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _DROP_WITH_VALUE:
            i += 2
            continue
        if a in _DROP_FLAGS or (a.startswith("-o") and len(a) > 2 and not a.startswith("-openmp")) \
                or a.startswith("-Fo") or a.startswith("-MF"):
            i += 1
            continue
        if any(a.startswith(d) for d in cfg.drop_args):
            i += 1
            continue
        matched = next((f for f in _PATH_FLAGS if a.startswith(f)), None)
        if matched:
            val = a[len(matched):]
            if val == "" and i + 1 < len(args) and not matched.endswith("="):  # "-I", "path"
                val = args[i + 1]
                p = Path(val)
                out.extend([matched, str(p if p.is_absolute() else (base / p))])
                i += 2
                continue
            p = Path(val) if val else None
            out.append(matched + (str(p if p.is_absolute() else (base / p)) if p else ""))
            i += 1
            continue
        if not a.startswith("-"):  # compiler executable or the source file itself
            if os.path.normcase(os.path.normpath(os.path.abspath(base / a))) == src_norm or i == 0:
                i += 1
                continue
        out.append(a)
        i += 1
    out += ["-ferror-limit=0", "-Wno-everything"]
    cfg_base = Path(cfg.base_dir)
    for a in cfg.append_args:  # relative include paths here are relative to the config file
        f = next((f for f in _PATH_FLAGS if a.startswith(f) and len(a) > len(f)), None)
        if f and not Path(a[len(f):]).is_absolute():
            a = f + str(cfg_base / a[len(f):])
        out.append(a)
    return out


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
        self.file_info: dict = {}  # rel file -> diagnostics / args source / counts (debug report)
        self.rejected_ptrs: dict = {}  # pointer name -> accesses ignored because not registered
        self.failed_files: list = []
        self.missing_includes: dict = {}  # header -> number of 'file not found' errors
        self.parsed_files: list = []
        self.system_dirs: set = set()
        self.final_decl_only: set = set()
        self.skipped_extern_decls = 0  # extern declarations seen in headers and not turned into nodes
        self.includes: dict = {}  # (including rel file, included rel file) -> line
        self._args_src = "?"
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
        if rel is None:  # outside the scan root: portable id (no machine path), like tree-sitter's ext_* ids
            p = Path(path)
            return _file_stem(Path("ext") / p.parent.name / p.name)
        return _file_stem(rel)

    _SYSTEM_MARKERS = ("/usr/include", "/usr/lib/", "/lib/clang/", "/lib/gcc/", "mingw", "windows kits",
                       "program files", "/msvc/", "/vc/tools/", "/include/c++/")

    def looks_system(self, path) -> bool:
        """Heuristic: compiler / OS headers are not part of the project graph (path based; also -isystem dirs)."""
        p = str(path).replace("\\", "/").lower()
        if any(m in p for m in self._SYSTEM_MARKERS):
            return True
        return any(p.startswith(d) for d in self.system_dirs)

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
            self.rejected_ptrs[base_name] = self.rejected_ptrs.get(base_name, 0) + 1
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

        if kind == K.FUNCTION_DECL and fn_id is None and not cursor.is_definition():
            if self.cfg.header_declarations and cursor.spelling:  # prototype in a header: file --contains--> function
                loc = cursor.location
                fpath = str(loc.file) if loc.file else (sfile or "")
                self.add_node(make_id(self.stem(fpath), cursor.spelling), "function", fpath,
                              name=f"{cursor.spelling}()", line=loc.line, decl_only=True,
                              metadata={"return_type": cursor.result_type.spelling if cursor.result_type else "",
                                        "parameters": self._parameters(cursor), "source_extractor": "clang"})
            else:
                self.skipped_extern_decls += 1
            return
        if kind == K.VAR_DECL and fn_id is None and self._is_global_var(cursor) and not cursor.is_definition():
            if self.cfg.header_declarations:  # `extern T x;` in a header: file --contains--> variable
                self._global_var(cursor, sfile)
            else:
                self.skipped_extern_decls += 1
        elif kind == K.VAR_DECL and fn_id is None and self._is_global_var(cursor):
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
                args, directory = hit
                self._args_src = "compile_commands"
                return normalize_args(args, directory, self.cfg, path)
            _warn_once(f"cc:{path}", f"{path.name} not in compile_commands.json; using extra_args")
            self._args_src = "extra_args (NOT in compile_commands.json)"
        else:
            self._args_src = "extra_args"
        args = list(self.cfg.extra_args)
        if not args:
            args = [f"-I{path.parent}"]
            self._args_src = "default -I<file dir> (no compile flags: includes may be missing)"
        return normalize_args(["clang"] + args, self.cfg.base_dir, self.cfg, path)

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
        rel = self.sf(str(path))
        args = self._args_for(path)
        for i, a in enumerate(args):
            d = a[len("-isystem"):] if a.startswith("-isystem") and len(a) > 8 else (args[i + 1] if a == "-isystem" and i + 1 < len(args) else "")
            if d:
                self.system_dirs.add(str(d).replace("\\", "/").lower().rstrip("/") + "/")
        info = {"args_source": self._args_src, "args": args, "errors": 0, "warnings": 0, "error_samples": []}
        self.file_info[rel] = info
        LOG.debug("parse %s | args from %s | %d arg(s)", rel, self._args_src, len(args))
        LOG.debug("  args: %s", " ".join(args))
        try:
            tu = index.parse(str(path), args=args)
        except self.ci.TranslationUnitLoadError as exc:
            _warn_once(f"tu:{path}", f"clang could not load {path.name}: {exc}")
            LOG.error("FAILED to load %s: %s", rel, exc)
            info["load_error"] = str(exc)
            self.failed_files.append(rel)
            return None
        sev = self.ci.Diagnostic
        for d in tu.diagnostics:
            loc = d.location
            where = f"{self.sf(str(loc.file))}:{loc.line}:{loc.column}" if loc.file else "?"
            if d.severity >= sev.Error:
                info["errors"] += 1
                m = re.search(r"'([^']+)' file not found", d.spelling)
                if m:
                    self.missing_includes[m.group(1)] = self.missing_includes.get(m.group(1), 0) + 1
                    info.setdefault("missing_includes", []).append(m.group(1))
                if len(info["error_samples"]) < 10:
                    info["error_samples"].append(f"{where}: {d.spelling}")
                LOG.warning("clang error %s: %s", where, d.spelling)
            elif d.severity == sev.Warning:
                info["warnings"] += 1
                LOG.debug("clang warning %s: %s", where, d.spelling)
        errors = info["errors"]
        if errors:
            self.parse_errors += errors
            _warn_once(f"diag:{path}", f"{path.name}: {errors} clang error(s) "
                                       f"(first: {info['error_samples'][0]}); results may be partial. "
                                       f"Re-run with --clang-debug for all of them.")
        try:  # real include resolution (honours -I), the part tree-sitter cannot do
            for inc in tu.get_includes():
                if inc.source is None or inc.include is None:
                    continue
                if self.looks_system(inc.source.name) or self.looks_system(inc.include.name):
                    continue
                a, b = self.sf(inc.source.name), self.sf(inc.include.name)
                if a != b:
                    self.includes.setdefault((a, b), inc.location.line)
        except Exception as exc:  # pragma: no cover - libclang quirk
            LOG.debug("get_includes failed for %s: %s", rel, exc)
        self.parsed_files.append(rel)
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
            LOG.debug("registrations in %s: %s", rel, self.ptr_maps[rel] or "none")
        merged = {}
        for rel, mapping in cached_ptr_maps.items():
            if rel not in self.ptr_maps:
                merged.update(mapping)
        for mapping in self.ptr_maps.values():
            merged.update(mapping)
        self.ptr_to_rom = merged
        LOG.info("calibration pointer registrations (ptr -> ROM): %s", merged or "NONE FOUND - no calibration edges can be produced")
        # pass 2: structure walk
        for path, tu in tus:
            self._pending_contains = []
            sfile = str(path)
            n0, e0 = len(self.nodes), len(self.edges)
            for child in tu.cursor.get_children():
                if self._skip(child):
                    continue
                self.walk(child, None, sfile)
            rel = self.sf(sfile)
            self.file_info[rel]["nodes_added"] = len(self.nodes) - n0
            self.file_info[rel]["edges_added"] = len(self.edges) - e0
            LOG.info("walked %s: +%d nodes, +%d edges (before dedup)", rel,
                     len(self.nodes) - n0, len(self.edges) - e0)
        LOG.info("header declarations skipped (header_declarations=false): %d", self.skipped_extern_decls)
        if self.missing_includes:
            top = sorted(self.missing_includes.items(), key=lambda kv: -kv[1])[:15]
            LOG.warning("PREPROCESSING: %d distinct header(s) not found - add their folders as -I in "
                        "compile_commands or append_args. Most frequent: %s", len(self.missing_includes), top)
        if self.rejected_ptrs:
            LOG.debug("member accesses ignored (pointer NOT registered as calibration): %s", self.rejected_ptrs)

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

        self.final_decl_only = {nid for nid in self.decl_only if nid in self.nodes}
        for nid in self.final_decl_only:
            self.nodes[nid]["metadata"]["declaration_only"] = True
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


def _is_file_node(n: dict) -> bool:
    sf = str(n.get("source_file") or "").replace("\\", "/")
    return bool(sf) and str(n.get("label", "")) == sf.rsplit("/", 1)[-1]


def _replace_treesitter(all_nodes, all_edges, nodes, edges, covered: set, root: Path) -> dict:
    """extractor=clang: drop tree-sitter's symbol nodes (and every edge touching them) for the
    files clang parsed, then add clang's. File nodes and file->file edges (imports) stay: they are
    the structure clang does not provide. Files clang could NOT parse are not in ``covered`` and
    keep their tree-sitter symbols (per-file fallback)."""
    def _rel(sf: str) -> str:  # tree-sitter source_file may still be absolute at this point
        try:
            return Path(sf).resolve().relative_to(root).as_posix() if Path(sf).is_absolute() else sf.replace("\\", "/")
        except (ValueError, OSError):
            return sf

    removed_ids = {n["id"] for n in all_nodes
                   if n.get("source_file") and _rel(str(n["source_file"])) in covered and not _is_file_node(n)
                   and n.get("file_type") == "code"}
    kept_nodes = [n for n in all_nodes if n.get("id") not in removed_ids]
    kept_edges = [e for e in all_edges
                  if e.get("source") not in removed_ids and e.get("target") not in removed_ids]
    dropped_edges = len(all_edges) - len(kept_edges)
    all_nodes[:] = kept_nodes
    all_edges[:] = kept_edges

    existing = {n["id"] for n in all_nodes}
    for node in nodes:
        if node["id"] in existing:
            continue
        node["metadata"]["source_extractor"] = "clang"
        all_nodes.append(node)
        existing.add(node["id"])
    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in all_edges}
    e_added = 0
    for edge in edges:
        key = (edge["source"], edge["target"], edge["relation"])
        if key in seen or edge["source"] not in existing or edge["target"] not in existing:
            continue
        seen.add(key)
        all_edges.append(edge)
        e_added += 1
    fmap = _file_id_map(all_nodes, root)
    for node in nodes:  # file --contains--> function/variable (tree-sitter's contains edges were dropped)
        if node["type"] not in ("function", "variable") or not node.get("source_file"):
            continue
        file_id = fmap.get(str(node["source_file"]).replace("\\", "/"))
        if file_id and file_id in existing and file_id != node["id"]:
            key = (file_id, node["id"], "contains")
            if key not in seen:
                seen.add(key)
                all_edges.append({"source": file_id, "target": node["id"], "relation": "contains",
                                  "confidence": "EXTRACTED", "source_file": node["source_file"], "weight": 1.0,
                                  **({"source_location": node["source_location"]} if node.get("source_location") else {})})
                e_added += 1
    return {"nodes_added": len(nodes), "nodes_enriched": 0, "edges_added": e_added,
            "treesitter_nodes_removed": len(removed_ids), "treesitter_edges_removed": dropped_edges}


def _unify_declarations(ex, nodes, edges, all_nodes, covered=None) -> int:
    """A header declaration (decl-only clang node) whose definition is a node tree-sitter (or an earlier
    pass) already made under a different id is the SAME symbol: point the declaration's edges at that node and
    drop the duplicate. Only when the label matches exactly one existing symbol node. ``covered`` (clang mode):
    never merge into a node that is about to be replaced."""
    clang_ids = {n["id"] for n in nodes}
    idx: dict = {}
    for n in all_nodes:
        if n["id"] in clang_ids or _is_file_node(n) or n.get("file_type") != "code" or not n.get("source_file"):
            continue
        if covered is not None and str(n["source_file"]).replace("\\", "/") in covered:
            continue
        idx.setdefault(n.get("label"), []).append(n["id"])
    remap = {}
    for n in nodes:
        if n["id"] in ex.final_decl_only:
            hits = idx.get(n["label"], [])
            if len(hits) == 1:
                remap[n["id"]] = hits[0]
    if not remap:
        return 0
    nodes[:] = [n for n in nodes if n["id"] not in remap]
    for e in edges:
        e["source"] = remap.get(e["source"], e["source"])
        e["target"] = remap.get(e["target"], e["target"])
    edges[:] = [e for e in edges if e["source"] != e["target"]]
    return len(remap)


def _file_id_map(all_nodes, root: Path) -> dict:
    """rel source_file -> id of its tree-sitter file node (ids may still be absolute-path based here)."""
    out = {}
    for n in all_nodes:
        if not _is_file_node(n):
            continue
        sf = str(n.get("source_file") or "")
        try:
            rel = Path(sf).resolve().relative_to(root).as_posix() if Path(sf).is_absolute() else sf.replace("\\", "/")
        except (ValueError, OSError):
            rel = sf.replace("\\", "/")
        out.setdefault(rel, n["id"])
    return out


def _ensure_file_nodes(ex, nodes, all_nodes, root: Path) -> int:
    """One file node per project file clang saw (every included header, in or out of the scan root) so
    header symbols get a `contains` edge and include edges have both ends. Same id scheme tree-sitter
    uses for file nodes (path based; extract()'s id pass canonicalises it)."""
    have = set(_file_id_map(all_nodes, root))
    wanted = {f for pair in ex.includes for f in pair}
    wanted |= {str(n["source_file"]).replace("\\", "/") for n in nodes
               if n["type"] in ("function", "variable") and n.get("source_file")}
    added = 0
    existing_ids = {n.get("id") for n in all_nodes}
    for sf in sorted(wanted - have):
        if ex.looks_system(sf) or not sf.lower().endswith((".h", ".c", ".hpp", ".cpp", ".inc")):
            continue
        absolute = Path(sf) if Path(sf).is_absolute() else (root / sf)
        nid = make_id(str(absolute))
        if nid in existing_ids:
            continue
        all_nodes.append({"id": nid, "label": Path(sf).name, "file_type": "code", "source_file": sf,
                          "source_location": "L1", "metadata": {"source_extractor": "clang", "file_node_created_by": "clang"}})
        existing_ids.add(nid)
        added += 1
    return added


def _clang_import_edges(ex, all_nodes, all_edges, root: Path, replace: bool) -> int:
    """File -> file `imports` edges from clang's include resolution (same -I as the real build).
    replace=True (extractor=clang): tree-sitter `imports` edges of every file clang saw as an
    includer are dropped first (they only resolved includes next to the file), and tree-sitter
    placeholder `ext_*` nodes left without any edge are removed."""
    if not ex.includes:
        return 0
    fmap = _file_id_map(all_nodes, root)
    if replace:
        includers = {fmap[a] for a, _ in ex.includes if a in fmap}
        all_edges[:] = [e for e in all_edges if not (e.get("relation") == "imports" and e.get("source") in includers)]
        used = {e.get("source") for e in all_edges} | {e.get("target") for e in all_edges}
        all_nodes[:] = [n for n in all_nodes if not (str(n.get("id", "")).startswith("ext_") and n["id"] not in used)]
    seen = {(e.get("source"), e.get("target"), e.get("relation")) for e in all_edges}
    added = 0
    for (a, b), line in sorted(ex.includes.items()):
        if a not in fmap or b not in fmap:
            continue
        key = (fmap[a], fmap[b], "imports")
        if key in seen:
            continue
        seen.add(key)
        all_edges.append({"source": fmap[a], "target": fmap[b], "relation": "imports", "context": "import",
                          "confidence": "EXTRACTED", "source_file": a, "source_location": f"L{line}",
                          "weight": 1.0, "metadata": {"resolved_by": "clang"}})
        added += 1
    return added


def _write_report(root: Path, cfg: ClangConfig, ex, stats: dict, c_files: list) -> Path | None:
    try:
        from graphify.paths import out_path
        out = out_path()
        out = out if out.is_absolute() else root / out
        out.mkdir(parents=True, exist_ok=True)
        path = out / "clang_report.json"
        report = {
            "extractor": cfg.extractor, "config_source": cfg.source,
            "compile_commands": str(cfg.resolve(cfg.compile_commands)) if cfg.compile_commands else None,
            "a2l": str(cfg.resolve(cfg.a2l)) if cfg.a2l else None,
            "c_files_requested": len(c_files), "c_files_parsed": ex.files_parsed if ex else 0,
            "failed_to_load": ex.failed_files if ex else [],
            "total_clang_errors": ex.parse_errors if ex else 0,
            "calibration_pointers": ex.ptr_to_rom if ex else {},
            "ignored_unregistered_pointers": ex.rejected_ptrs if ex else {},
            "missing_includes_top": dict(sorted(ex.missing_includes.items(), key=lambda kv: -kv[1])[:40]) if ex else {},
            "per_file": ex.file_info if ex else {},
            "stats": {k: v for k, v in stats.items() if k != "a2l"},
            "a2l_stats": stats.get("a2l"),
        }
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return path
    except Exception as exc:
        LOG.warning("could not write clang_report.json: %s", exc)
        return None


def run_clang_pass(paths, root, all_nodes: list, all_edges: list):
    """Run the clang (+ A2L) pass and merge its output into ``all_nodes`` / ``all_edges``.

    ``cfg.extractor``: ``both`` (default) enriches tree-sitter's graph; ``clang`` replaces
    tree-sitter's symbols for every C file clang parsed; ``treesitter`` skips this pass.
    Returns a stats dict, or None when the pass is not configured / not available.
    """
    root = Path(root).resolve()
    cfg = load_config(root)
    c_files = [Path(p).resolve() for p in paths if Path(p).suffix.lower() == ".c"]
    if cfg is None:
        if c_files:  # say WHY, once: a silent fallback looks like a bug
            _warn_once("noconfig", f"clang pass OFF (tree-sitter only): no {CONFIG_NAME} in {root} or the "
                                   f"current directory, $GRAPHIFY_CLANG_CONFIG unset, no --clang flag. "
                                   f"Run `graphify clang-check` for details.")
        return None
    log_path = _setup_logging(cfg, root)
    LOG.info("extractor=%s | config: %s | root: %s | %d C file(s) in this run", cfg.extractor, cfg.source,
             root, len(c_files))
    if cfg.extractor == "treesitter":
        LOG.info("extractor=tree-sitter chosen: clang pass skipped on purpose")
        if c_files:
            _warn_once("tsonly", "extractor = tree-sitter: clang pass skipped (as requested).")
        return None
    stats: dict = {"files": 0}
    ex = None
    if c_files:
        cindex = _load_cindex(cfg)
        if cindex is None:
            LOG.error("libclang unavailable -> falling back to tree-sitter for ALL files")
            return None
        LOG.info("libclang module: %s", getattr(cindex, "__file__", "?"))
        ex = ClangExtractor(cindex, root, cfg)
        cache_path = _cache_file(root)
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        except (OSError, ValueError):
            cached = {}
        LOG.debug("pointer cache %s: %d file(s) cached", cache_path, len(cached))
        ex.run(c_files, cached)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({**cached, **ex.ptr_maps}, indent=1), encoding="utf-8")
        except OSError:
            pass
        nodes, edges = ex.finalize()
        LOG.info("clang produced %d nodes / %d edges after dedup", len(nodes), len(edges))
        if not ex.files_parsed:
            LOG.error("0 C files parsed: keeping tree-sitter output unchanged")
        else:
            stats["file_nodes_added"] = _ensure_file_nodes(ex, nodes, all_nodes, root)
            if cfg.extractor == "clang":
                covered = set(ex.parsed_files) | {n["source_file"] for n in nodes
                                                   if n["type"] in ("function", "variable") and n.get("source_file")}
                stats["declarations_unified"] = _unify_declarations(ex, nodes, edges, all_nodes, covered)
                if ex.failed_files:
                    _warn_once("clangfallback", "extractor = clang: these C files could not be parsed and keep "
                                                f"their tree-sitter symbols: {', '.join(ex.failed_files[:10])}")
                stats.update(_replace_treesitter(all_nodes, all_edges, nodes, edges, covered, root))
                stats["imports_added"] = _clang_import_edges(ex, all_nodes, all_edges, root, replace=True)
            else:
                stats["declarations_unified"] = _unify_declarations(ex, nodes, edges, all_nodes)
                stats.update(_merge_into(all_nodes, all_edges, nodes, edges, cfg, ex, root))
                stats["imports_added"] = _clang_import_edges(ex, all_nodes, all_edges, root, replace=False)
            LOG.info("declarations merged into existing definitions: %d", stats.get("declarations_unified", 0))
            outside = sorted({f for pair in ex.includes for f in pair if Path(f).is_absolute()})
            if outside:
                LOG.info("project files OUTSIDE the scan root (%d, e.g. %s): their symbols get ext_* ids; scan a higher folder "
                         "if they should be normal nodes", len(outside), outside[:5])
            LOG.info("clang added %d header/file node(s) and %d include edge(s) (%d include pairs seen)",
                     stats["file_nodes_added"], stats["imports_added"], len(ex.includes))
        stats["files"] = ex.files_parsed
        stats["ptr_to_rom"] = len(ex.ptr_to_rom)

    a2l_path = cfg.resolve(cfg.a2l)
    if a2l_path is not None:
        if not a2l_path.is_file():
            _warn_once("noa2l", f"A2L file not found: {a2l_path}")
            LOG.error("A2L file not found: %s", a2l_path)
        else:
            from graphify.extractors.a2l import join_a2l, scan_a2l
            try:
                rel = a2l_path.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = str(a2l_path.resolve())
            parsed = scan_a2l(str(a2l_path))
            LOG.info("A2L parsed: %d measurements, %d characteristics, %d axis_pts",
                     len(parsed.get("measurements", {})), len(parsed.get("characteristics", {})),
                     len(parsed.get("axis_pts", {})))
            stats["a2l"] = join_a2l(all_nodes, all_edges, parsed, rel)
            LOG.info("A2L join: %s", {k: v for k, v in stats["a2l"].items() if not k.startswith("unmatched")})
            if stats["a2l"].get("unmatched_code"):
                LOG.debug("code calibration fields with NO A2L match: %s", stats["a2l"]["unmatched_code"])
            if stats["a2l"].get("unmatched_a2l"):
                LOG.debug("A2L characteristics NOT matched by any code symbol: %s", stats["a2l"]["unmatched_a2l"])

    a2l = stats.get("a2l") or {}
    if c_files and not stats.get("files"):
        _warn_once("nofiles", "clang pass ran but parsed 0 C files (all failed to load); results are tree-sitter only.")
    report = _write_report(root, cfg, ex, stats, c_files) if cfg.debug else None
    print(
        f"[graphify] clang pass ({cfg.extractor}): {stats.get('files', 0)} C file(s), "
        f"+{stats.get('nodes_added', 0)} nodes ({stats.get('nodes_enriched', 0)} enriched)"
        + (f", -{stats.get('treesitter_nodes_removed', 0)} tree-sitter nodes" if cfg.extractor == "clang" else "")
        + f", +{stats.get('edges_added', 0)} edges, +{stats.get('imports_added', 0)} include edges"
        + (f", A2L matched {a2l.get('characteristics', 0)} characteristics / "
           f"{a2l.get('measurements', 0)} measurements" if a2l else "")
        + (f" | debug log: {log_path}, report: {report}" if log_path else ""),
        file=sys.stderr,
    )
    return stats


def _merge_into(all_nodes, all_edges, nodes, edges, cfg: ClangConfig, ex: ClangExtractor, root: Path) -> dict:

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

    # file --contains--> function/variable (deduped against tree-sitter's own contains edges)
    fmap = _file_id_map(all_nodes, root)
    for node in nodes:
        if node["type"] not in ("function", "variable") or node["id"] not in kept_ids or not node.get("source_file"):
            continue
        file_id = fmap.get(str(node["source_file"]).replace("\\", "/"))
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


def check_setup(root: Path | None = None) -> int:
    """`graphify clang-check`: explain, step by step, whether the clang pass will run."""
    root = Path(root or ".").resolve()
    ok = True

    def line(flag, msg):
        print(f"[{'OK ' if flag else 'FAIL'}] {msg}")

    try:
        import clang.cindex as cindex  # noqa: F401
        line(True, f"python package 'libclang' importable (python: {sys.executable})")
    except ImportError:
        line(False, f"python package 'libclang' NOT installed in this Python ({sys.executable}). "
                    f"Install it into the SAME environment as graphify: "
                    f"`{sys.executable} -m pip install libclang`")
        return 1
    cfg = load_config(root)
    if cfg is None:
        line(False, f"no config found: create {root / CONFIG_NAME} (or pass --clang) - see wiki")
        return 1
    line(True, f"config loaded from {cfg.source} (base dir {cfg.base_dir}); extractor = {cfg.extractor}; debug = {cfg.debug}")
    ci = _load_cindex(cfg)
    line(ci is not None, "libclang shared library loads" if ci else "libclang could not be loaded (set \"libclang\" in the config)")
    ok &= ci is not None
    cc = cfg.resolve(cfg.compile_commands)
    if cc is None:
        line(True, "no compile_commands.json configured: using extra_args / -I<file dir> (includes may be missing)")
    else:
        line(cc.is_file(), f"compile_commands.json: {cc}")
        ok &= cc.is_file()
    a2l = cfg.resolve(cfg.a2l)
    if a2l is not None:
        line(a2l.is_file(), f"A2L file: {a2l}")
        ok &= a2l.is_file()
    n_c = sum(1 for _ in root.rglob("*.c"))
    line(n_c > 0, f"{n_c} .c file(s) under {root}")
    print("Ready: the clang pass will run on `graphify update`/`extract`." if ok else
          "Not ready: fix the FAIL lines above.")
    return 0 if ok else 1


def check_file(root: Path, c_file: Path) -> int:
    """`graphify clang-check <root> --file x.c`: parse ONE file and show the exact command,
    every error, and the headers clang could not find (preprocessing problems)."""
    root = Path(root or ".").resolve()
    cfg = load_config(root) or ClangConfig(base_dir=str(root))
    ci = _load_cindex(cfg)
    if ci is None:
        print("[FAIL] libclang not available - run `graphify clang-check` first")
        return 1
    c_file = Path(c_file)
    c_file = (c_file if c_file.is_absolute() else Path.cwd() / c_file).resolve()
    ex = ClangExtractor(ci, root, cfg)
    tu = ex.parse(c_file)
    rel = ex.sf(str(c_file))
    info = ex.file_info.get(rel, {})
    print(f"file:        {rel}")
    print(f"flags from:  {info.get('args_source')}")
    print(f"arguments:   {' '.join(info.get('args', []))}")
    if tu is None:
        print(f"[FAIL] could not load: {info.get('load_error')}")
        return 1
    print(f"errors: {info.get('errors', 0)}   warnings: {info.get('warnings', 0)}")
    for d in tu.diagnostics:
        if d.severity >= ci.Diagnostic.Error:
            loc = d.location
            print(f"  {ex.sf(str(loc.file)) if loc.file else '?'}:{loc.line}: {d.spelling}")
    if ex.missing_includes:
        print("\nHeaders NOT FOUND (preprocessing): add their folders with -I:")
        for h, n in sorted(ex.missing_includes.items(), key=lambda kv: -kv[1]):
            print(f"  {h}  (x{n})")
    return 0 if not info.get("errors") else 2
