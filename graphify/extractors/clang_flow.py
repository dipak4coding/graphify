"""Data flow and function pointers for the clang pass (B2 / B3 / B4).

* ``derived_from``  variable --> variable it takes its value from (assignment, call argument, return value,
  out parameter; locals are followed inside a function and are not shown). A condition (``if (a) x = 1``) gives
  a *control* dependency, marked ``control_only`` when no data path exists.
* ``indirect_call`` function --> function reached through a function pointer (initialised table, assignment,
  function passed as argument, local pointer).
* ``calls`` edges get ``metadata.args``: which global variables are handed to the callee, per argument.

Everything is recorded per function while the AST is walked (``record_function``) and resolved at the end with
function summaries (what a function's return value / out parameters / global writes depend on), so a value that
travels through several functions and local variables ends up as one edge between the two global variables.
Limits: no pointer aliasing (``q = &t``), arrays and structs count as one variable, a callee outside the parsed
files is treated conservatively (its result depends on all arguments, marked ``inferred``).
"""
from __future__ import annotations

import logging

from graphify.ids import make_id
from graphify.relations import context_for

LOG = logging.getLogger("graphify.clang")

_ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
_KNOWN_OUT = {"memcpy": {0}, "memmove": {0}, "memset": {0}, "strcpy": {0}, "strncpy": {0}, "strcat": {0},
              "strncat": {0}, "sprintf": {0}, "snprintf": {0}}
_MAX_ROUNDS = 8
_MAX_LOOP_ITER = 6
_MAX_LINES = 8
_MAX_FUNCS = 8
_MAX_VIA = 5
_MAX_ARG_SITES = 6


class _Call:
    __slots__ = ("callee", "name", "args", "line", "stack", "argctl", "addr")

    def __init__(self):
        self.callee = None
        self.name = ""
        self.args = []          # [(frozenset data terms, frozenset control terms)]
        self.line = 0
        self.stack = frozenset()   # control terms of the enclosing conditions
        self.argctl = frozenset()
        self.addr = set()       # argument indexes that are `&x` / an array variable


class _Fn:
    __slots__ = ("params", "assigns", "calls")

    def __init__(self):
        self.params = {}        # (name, line, col) of a PARM_DECL -> index
        self.assigns = []       # (target, data terms, control terms, line)
        self.calls = []


class FlowRecorder:
    def __init__(self, ex):
        self.ex = ex
        self.fns: dict = {}
        self.holders: dict = {}     # holder key -> [(field or None, function id)]
        self._icall_seen: set = set()
        self.icalls: list = []      # {"fn", "key", "field", "line", "ctx"}
        self.errors = 0
        self._cur = None
        self._fid = None
        self._file = ""
        self._stack: list = []      # control term sets of the enclosing conditions
        self._seq: list = []        # list the assignments are appended to (a loop body gets its own list)
        self._cx: list = []         # their names ("if", "else", "loop", "switch")
        self._fp_cache: dict = {}
        self.summaries: dict = {}

    # ---- helpers -----------------------------------------------------------------------

    def _ctl_now(self) -> frozenset:
        out = set()
        for s in self._stack:
            out |= s
        return frozenset(out)

    def _term_of_ref(self, ref):
        ex, K = self.ex, self.ex.K
        if ref is None:
            return None
        if ref.kind == K.PARM_DECL:
            return ("l", ex._pkey(ref))
        if ref.kind == K.VAR_DECL:
            if ex._is_global_var(ref):
                return ("g", ex._global_var(ref, self._file))
            return ("l", ex._pkey(ref))
        return None

    def _fn_id(self, callee):
        ex = self.ex
        defn = None
        try:
            defn = callee.get_definition()
        except Exception:
            pass
        target = defn if defn is not None else callee
        loc = target.location
        cfile = str(loc.file) if loc.file else self._file
        return make_id(ex.stem(cfile), callee.spelling) if cfile else make_id("unknown", callee.spelling)

    def _fn_target(self, expr):
        """Function whose address `expr` takes (`Foo`, `&Foo`, with casts), else None."""
        ex, K = self.ex, self.ex.K
        e = ex._peel(expr)
        if e.kind == K.UNARY_OPERATOR and ex._c_op_kind(e, "clang_getCursorUnaryOperatorKind") == 5:
            kids = list(e.get_children())
            if len(kids) == 1:
                e = ex._peel(kids[0])
        if e.kind == K.DECL_REF_EXPR and e.referenced is not None and e.referenced.kind == K.FUNCTION_DECL:
            return self._fn_id(e.referenced)
        return None

    def _add_assign(self, tgt, data, ctl, line, strong=False):
        """Assignments are kept in execution (code) order. `strong`: an unconditional write to a whole local variable,
        which replaces what the variable held before; every other write only adds a source."""
        self._seq.append((tgt, frozenset(data), frozenset(ctl), line, strong and not self._stack))

    # ---- lvalues -----------------------------------------------------------------------

    def _root_term(self, expr):
        """(term of the global / local variable an lvalue stores into, index terms) - follows `.m` and `[i]` on
        arrays and structs, stops at pointers."""
        ex, K = self.ex, self.ex.K
        c, extras = expr, []
        for _ in range(32):
            c = ex._peel(c)
            if c.kind == K.DECL_REF_EXPR:
                t = self._term_of_ref(c.referenced)
                if t is None:
                    return None, set()
                idx = set()
                for x in extras:
                    d, k = self.ev(x)
                    idx |= d | k
                return t, idx
            if c.kind not in (K.ARRAY_SUBSCRIPT_EXPR, K.MEMBER_REF_EXPR):
                return None, set()
            kids = list(c.get_children())
            if not kids:
                return None, set()
            base = ex._peel(kids[0])
            if base.type.get_canonical().kind == ex.T.POINTER:
                return None, set()
            extras.extend(kids[1:])
            c = base
        return None, set()

    def _target(self, lhs):
        ex = self.ex
        t, idx = self._root_term(lhs)
        if t is not None:
            return t, idx
        if ex._cur_params:
            chain = ex._deref_chain(lhs)                    # `*p = v`, `p->m = v`, `p[i] = v`
            if chain is not None:
                idx = set()
                for x in chain[1]:
                    d, k = self.ev(x)
                    idx |= d | k
                return ("pout", chain[0]), idx
        return None, set()

    def _out_target(self, arg):
        """Variable an argument may be written through: `&x`, `&x.m`, `&arr[i]`, or an array variable."""
        ex, K = self.ex, self.ex.K
        b = ex._peel(arg)
        if b.kind == K.UNARY_OPERATOR and ex._c_op_kind(b, "clang_getCursorUnaryOperatorKind") == 5:
            kids = list(b.get_children())
            if len(kids) == 1:
                t, _idx = self._root_term(kids[0])
                return t
            return None
        if b.kind == K.DECL_REF_EXPR and b.referenced is not None and b.referenced.kind == K.VAR_DECL \
                and b.type.get_canonical().kind in ex._array_kinds:
            return self._term_of_ref(b.referenced)
        return None

    # ---- function pointers -------------------------------------------------------------

    def _holder_of(self, expr):
        """(holder key, field) of a function pointer expression: a global / local / parameter variable or a member
        of one (`tab[i].run`, `s.cb`, `cb`), else None."""
        ex, K = self.ex, self.ex.K
        c, field = expr, None
        for _ in range(32):
            c = ex._peel(c)
            if c.kind == K.UNARY_OPERATOR and ex._c_op_kind(c, "clang_getCursorUnaryOperatorKind") == 6:
                kids = list(c.get_children())                # (*cb)(x)
                if len(kids) != 1:
                    return None
                c = kids[0]
                continue
            if c.kind == K.MEMBER_REF_EXPR:
                if field is None:
                    field = c.spelling or None
                kids = list(c.get_children())
                if not kids:
                    return None
                if ex._peel(kids[0]).type.get_canonical().kind == ex.T.POINTER:
                    return None
                c = kids[0]
                continue
            if c.kind == K.ARRAY_SUBSCRIPT_EXPR:
                kids = list(c.get_children())
                if not kids:
                    return None
                c = kids[0]
                continue
            break
        if c.kind != K.DECL_REF_EXPR or c.referenced is None:
            return None
        ref = c.referenced
        if ref.kind == K.PARM_DECL:
            j = self._cur.params.get(ex._pkey(ref)) if self._cur is not None else None
            return (("p", self._fid, j), field) if j is not None else None
        if ref.kind == K.VAR_DECL:
            if ex._is_global_var(ref):
                return (("g", ex._global_var(ref, self._file)), field)
            return (("l", self._fid, ex._pkey(ref)), field)
        return None

    def _store_fnptr(self, lhs, rhs):
        fn = self._fn_target(rhs)
        if fn is None:
            return
        h = self._holder_of(lhs)
        if h is not None:
            self.holders.setdefault(h[0], []).append((h[1], fn))

    def _has_fnptr(self, t, depth=0) -> bool:
        ex = self.ex
        try:
            t = t.get_canonical()
            key = t.spelling
            if key in self._fp_cache:
                return self._fp_cache[key]
            self._fp_cache[key] = False                      # recursion guard
            res = False
            if t.kind == ex.T.POINTER:
                res = t.get_pointee().get_canonical().kind in (ex.T.FUNCTIONPROTO, ex.T.FUNCTIONNOPROTO)
            elif t.kind in ex._array_kinds:
                res = self._has_fnptr(t.element_type, depth + 1)
            elif t.kind == ex.T.RECORD and depth < 5:
                res = any(self._has_fnptr(f.type, depth + 1) for f in t.get_fields())
            self._fp_cache[key] = res
            return res
        except Exception:
            return False

    def record_global_init(self, cursor, sfile):
        """`T g = Foo;` / `const S tab[] = { {1, A, B}, {.run = C} };` at file scope: functions stored in a variable."""
        ex, K = self.ex, self.ex.K
        if not self._has_fnptr(cursor.type):
            return
        kids = [k for k in cursor.get_children() if k.kind != K.TYPE_REF]
        if not kids:
            return
        self._file = sfile
        vid = ex._global_var(cursor, sfile)
        try:
            self._scan_init(kids[-1], ("g", vid), cursor.type, None)
        except Exception as exc:                              # never break the extraction
            self.errors += 1
            LOG.debug("function pointer initialiser of %s skipped: %s", cursor.spelling, exc)

    def _scan_init(self, expr, key, ctype, field):
        ex, K = self.ex, self.ex.K
        e = ex._peel(expr)
        if e.kind == K.INIT_LIST_EXPR:
            t = ctype.get_canonical() if ctype is not None else None
            fields, elem = [], None
            if t is not None:
                if t.kind in ex._array_kinds:
                    elem = t.element_type
                elif t.kind == ex.T.RECORD:
                    fields = list(t.get_fields())
            by_name = {f.spelling: f.type for f in fields}
            for i, ch in enumerate(e.get_children()):
                grand = list(ch.get_children())
                fname, sub, ftype = None, ch, elem
                if grand and grand[0].kind == K.MEMBER_REF and len(grand) >= 2:    # `.run = B`
                    fname, sub = grand[0].spelling, grand[-1]
                    ftype = by_name.get(fname)
                elif fields and i < len(fields):
                    fname, ftype = fields[i].spelling, fields[i].type
                self._scan_init(sub, key, ftype, fname or field)
            return
        fn = self._fn_target(e)
        if fn is not None:
            self.holders.setdefault(key, []).append((field, fn))

    # ---- expressions -------------------------------------------------------------------

    def ev(self, c):
        """(data terms, control terms) of an expression; records the calls and assignments inside it."""
        ex, K = self.ex, self.ex.K
        kind = c.kind
        if kind == K.DECL_REF_EXPR:
            t = self._term_of_ref(c.referenced)
            return ({t} if t else set()), set()
        if kind == K.CALL_EXPR:
            return self._call(c)
        if kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR):
            if ex._operator_str(c) in _ASSIGN:
                return self._assign(c)
        if kind == K.CONDITIONAL_OPERATOR:
            kids = list(c.get_children())
            if len(kids) == 3:
                cd, cc = self.ev(kids[0])
                ad, ac = self.ev(kids[1])
                bd, bc = self.ev(kids[2])
                return ad | bd, cd | cc | ac | bc
        data, ctl = set(), set()
        for ch in c.get_children():
            d, k = self.ev(ch)
            data |= d
            ctl |= k
        return data, ctl

    def _assign(self, c):
        ex = self.ex
        kids = list(c.get_children())
        if len(kids) != 2:
            return set(), set()
        lhs, rhs = kids
        op = ex._operator_str(c)
        rd, rc = self.ev(rhs)
        self._store_fnptr(lhs, rhs)
        tgt, idx = self._target(lhs)
        if tgt is None:
            self.ev(lhs)
            return rd, rc
        data = set(rd)
        if op != "=" and tgt[0] in ("g", "l"):
            data.add(tgt)
        direct = ex._peel(lhs).kind == self.ex.K.DECL_REF_EXPR       # whole variable, not `s.m` / `a[i]`
        self._add_assign(tgt, data, rc | idx | self._ctl_now(), c.location.line,
                         strong=direct and tgt[0] == "l")
        return rd, rc

    def _call(self, c):
        ex, K = self.ex, self.ex.K
        kids = list(c.get_children())
        callee = c.referenced
        direct = callee is not None and callee.kind == K.FUNCTION_DECL
        rec = _Call()
        rec.line = c.location.line
        rec.stack = self._ctl_now()
        n = len(self._cur.calls)
        self._cur.calls.append(rec)
        if direct:
            rec.callee = self._fn_id(callee)
            rec.name = callee.spelling
        elif kids:
            h = self._holder_of(kids[0])
            if h is not None:
                sig = (self._fid, h[0], h[1], rec.line)
                if sig not in self._icall_seen:
                    self._icall_seen.add(sig)
                    self.icalls.append({"fn": self._fid, "key": h[0], "field": h[1], "line": rec.line,
                                        "ctx": " > ".join(self._cx[-3:]), "file": self._file})
        argctl = set()
        args = []
        for i, a in enumerate(kids[1:]):
            d, k = self.ev(a)
            args.append((frozenset(d), frozenset(k)))
            argctl |= k
            out = self._out_target(a)
            if out is not None:
                rec.addr.add(i)
                self._add_assign(out, {("o", n, i)}, set(self._ctl_now()), rec.line)
            if direct:
                fn = self._fn_target(a)
                if fn is not None:                               # Register(Foo): the parameter of Register holds Foo
                    self.holders.setdefault(("p", rec.callee, i), []).append((None, fn))
        rec.args = args
        rec.argctl = frozenset(argctl)
        return {("c", n)}, set()

    # ---- statements --------------------------------------------------------------------

    def visit(self, c):
        ex, K = self.ex, self.ex.K
        kind = c.kind
        kids = list(c.get_children())
        if kind in (K.COMPOUND_STMT, K.DECL_STMT, K.CASE_STMT, K.DEFAULT_STMT, K.LABEL_STMT):
            for ch in kids:
                self.visit(ch)
        elif kind == K.VAR_DECL:
            inits = [k for k in kids if k.kind != K.TYPE_REF]
            if inits and not ex._is_global_var(c):
                init = inits[-1]
                d, k = self.ev(init)
                tgt = ("l", ex._pkey(c))
                self._add_assign(tgt, d, k | self._ctl_now(), c.location.line, strong=True)
                fn = self._fn_target(init)
                if fn is not None:
                    self.holders.setdefault(("l", self._fid, ex._pkey(c)), []).append((None, fn))
        elif kind == K.IF_STMT:
            if not kids:
                return
            cd, cc = self.ev(kids[0])
            self._push(cd | cc, "if")
            if len(kids) > 1:
                self.visit(kids[1])
            self._pop()
            if len(kids) > 2:
                self._push(cd | cc, "else")
                self.visit(kids[2])
                self._pop()
        elif kind in (K.WHILE_STMT, K.FOR_STMT):
            if not kids:
                return
            head = set()
            for h in kids[:-1]:
                d, k = self.ev(h)
                head |= d | k
            self._loop_body(head, kids[-1])
        elif kind == K.DO_STMT:
            if len(kids) < 2:
                return
            d, k = self.ev(kids[-1])
            self._loop_body(d | k, kids[0])
        elif kind == K.SWITCH_STMT:
            if not kids:
                return
            d, k = self.ev(kids[0])
            self._push(d | k, "switch")
            for ch in kids[1:]:
                self.visit(ch)
            self._pop()
        elif kind == K.RETURN_STMT:
            if kids:
                d, k = self.ev(kids[0])
                self._add_assign(("ret",), d, k | self._ctl_now(), c.location.line)
        elif kind in (K.BREAK_STMT, K.CONTINUE_STMT, K.NULL_STMT, K.GOTO_STMT):
            return
        else:
            self.ev(c)

    def _loop_body(self, head, body):
        """A loop body is recorded as a nested list: the resolver runs it twice, so a value written late in one
        iteration reaches a read early in the next."""
        nested: list = []
        outer = self._seq
        outer.append(("LOOP", nested))
        self._seq = nested
        self._push(head, "loop")
        try:
            self.visit(body)
        finally:
            self._pop()
            self._seq = outer

    def _push(self, terms, name):
        self._stack.append(set(terms))
        self._cx.append(name)

    def _pop(self):
        self._stack.pop()
        self._cx.pop()

    def record_function(self, cursor, fn_id, sfile):
        """Call from the walk while the function's own parameter table is active."""
        ex, K = self.ex, self.ex.K
        rec = _Fn()
        params = [ch for ch in cursor.get_children() if ch.kind == K.PARM_DECL]
        rec.params = {ex._pkey(p): j for j, p in enumerate(params)}
        self.fns[fn_id] = rec
        self._cur, self._fid, self._file, self._stack, self._cx = rec, fn_id, sfile, [], []
        self._seq = rec.assigns
        try:
            for ch in cursor.get_children():
                if ch.kind == K.COMPOUND_STMT:
                    self.visit(ch)
        except Exception as exc:                              # never break the extraction
            self.errors += 1
            LOG.debug("data flow of %s skipped: %s", cursor.spelling, exc)
        finally:
            self._cur = None

    # ---- resolution --------------------------------------------------------------------

    def _bind(self, call, j, ctl, via, inf, expand):
        """Items of argument `j` of `call` (the callee's parameter j)."""
        if j >= len(call.args):
            return set()
        d, k = call.args[j]
        out = set()
        for it in expand(d, ctl):
            out.add((it[0], it[1], via or it[2], inf or it[3]))
        for it in expand(k, True):
            out.add((it[0], it[1], via or it[2], inf or it[3]))
        return out

    def _resolve(self, fid, S):
        """-> (summary, effects, call argument variables) of one function, using the callee summaries S."""
        ex = self.ex
        rec = self.fns[fid]
        loc = {key: {(("p", j), False, "", False)} for key, j in rec.params.items()}

        def canon(cid):
            return ex._canon(cid) if cid is not None else None

        def expand(terms, ctl):
            out = set()
            for t in terms:
                out |= expand_one(t, ctl)
            return out

        def lift(items, call, ctl, name, inf):
            """Callee summary items (terms g / p) -> items in this function."""
            out = set()
            for (st, sc, sv, si) in items:
                if st[0] == "g":
                    out.add((st, ctl or sc, sv or name, inf or si))
                else:
                    out |= self._bind(call, st[1], ctl or sc, sv or name, inf or si, expand)
            return out

        def expand_one(t, ctl):
            k = t[0]
            if k == "g":
                return {(t, ctl, "", False)}
            if k == "p":
                return {(t, ctl, "", False)}
            if k == "l":
                return {(it[0], ctl or it[1], it[2], it[3]) for it in loc.get(t[1], ())}
            call = rec.calls[t[1]]
            cid = canon(call.callee)
            sm = S.get(cid) if cid is not None else None
            if k == "c":
                if sm is not None:
                    out = lift(sm["ret"], call, ctl, call.name, False)
                else:                                          # unknown callee: result depends on every argument
                    out = set()
                    for j in range(len(call.args)):
                        out |= self._bind(call, j, ctl, call.name, True, expand)
                    return out
                out |= {(it[0], True, it[2], it[3]) for it in expand(call.argctl, True)}
                return out
            i = t[2]                                           # ("o", call, arg): value written into argument i
            if sm is not None:
                return lift(sm["out"].get(i, ()), call, ctl, call.name, False)
            if call.name in _KNOWN_OUT and i in _KNOWN_OUT[call.name]:
                out = set()
                for j in range(len(call.args)):
                    if j not in _KNOWN_OUT[call.name]:
                        out |= self._bind(call, j, ctl, call.name, True, expand)
                return out
            return set()

        ret, out_p = set(), {}
        effects: dict = {}

        def run(items, depth=0):
            for it in items:
                if it[0] == "LOOP":
                    if depth < 3:                       # repeat until nothing new arrives: a value written late in one
                        for _i in range(_MAX_LOOP_ITER):    # iteration reaches a read early in the next (delay lines)
                            before = {k: frozenset(v) for k, v in loc.items()}
                            run(it[1], depth + 1)
                            if {k: frozenset(v) for k, v in loc.items()} == before:
                                break
                    continue
                tgt, data, ctl, line, strong = it
                vals = expand(data, False) | expand(ctl, True)
                if tgt[0] == "l":
                    if strong:
                        loc[tgt[1]] = set(vals)         # overwrite: the old sources are gone
                    elif vals:
                        loc.setdefault(tgt[1], set()).update(vals)
                    continue
                if not vals:
                    continue
                if tgt[0] == "g":
                    effects.setdefault(tgt[1], set()).update((v, line) for v in vals)
                elif tgt[0] == "pout":
                    out_p.setdefault(tgt[1], set()).update(vals)
                else:
                    ret.update(vals)

        run(rec.assigns)
        # global writes inside callees: x <- parameter j of the callee becomes x <- the argument
        for call in rec.calls:
            cid = canon(call.callee)
            sm = S.get(cid) if cid is not None else None
            if sm is None:
                continue
            site_ctl = expand(call.stack, True)
            for x, items in sm["gw"].items():
                vals = lift(items, call, False, call.name, False)
                vals |= {(it[0], True, it[2] or call.name, it[3]) for it in site_ctl}
                if vals:
                    effects.setdefault(x, set()).update((v, call.line) for v in vals)
        gw = {x: frozenset(it for it, _ln in vals if it[0][0] == "p") for x, vals in effects.items()}
        gw = {x: v for x, v in gw.items() if v}
        summary = {"ret": frozenset(it for it in ret if it[0][0] in ("g", "p")),
                   "out": {j: frozenset(v) for j, v in out_p.items()}, "gw": gw}
        # variables handed to each call (for the `calls` edge)
        call_args = []
        for call in rec.calls:
            if call.callee is None:
                continue
            per = {}
            for j, (d, _k) in enumerate(call.args):
                if j in call.addr:                             # `&x`: the variable itself, not what a local got from the call
                    vs = sorted({tt[1] for tt in d if tt[0] == "g"})
                else:
                    vs = sorted({it[0][1] for it in expand(d, False) if it[0][0] == "g"})
                if vs:
                    per[j] = vs
            if per:
                call_args.append((call.callee, call.line, per))
        return summary, effects, call_args

    def resolve_all(self):
        S: dict = {}
        res: dict = {}
        for _ in range(_MAX_ROUNDS):
            changed = False
            for fid in self.fns:
                try:
                    summary, effects, cargs = self._resolve(fid, S)
                except Exception as exc:
                    self.errors += 1
                    LOG.debug("data flow resolution of %s failed: %s", fid, exc)
                    continue
                res[fid] = (effects, cargs)
                if S.get(fid) != summary:
                    S[fid] = summary
                    changed = True
            if not changed:
                break
        self.summaries = S
        return res

    # ---- output ------------------------------------------------------------------------

    def edges(self, remap, nodes):
        """-> (derived_from edges, call argument sites {(caller, callee): [...]}, indirect calls, variables that feed themselves)."""
        ex = self.ex
        res = self.resolve_all()

        def rid(x):
            return remap.get(x, x)

        agg: dict = {}
        arg_sites: dict = {}
        self_feed: dict = {}
        for fid, (effects, cargs) in res.items():
            f = rid(fid)
            if f not in nodes:
                continue
            for x, vals in effects.items():
                xr = rid(x)
                if xr not in nodes:
                    continue
                for (item, line) in vals:
                    term, ctl, via, inf = item
                    if term[0] != "g":
                        continue
                    ar = rid(term[1])
                    if ar == xr:
                        if not ctl and f not in self_feed.setdefault(xr, []) and len(self_feed[xr]) < _MAX_FUNCS:
                            self_feed[xr].append(f)            # x = x + a: state / integrator
                        continue
                    if ar not in nodes:
                        continue
                    a = agg.setdefault((xr, ar), {"fns": [], "lines": [], "data": False, "ctl": False,
                                                  "via": [], "inf": True, "file": nodes[f].get("source_file", "")})
                    if ctl:
                        a["ctl"] = True
                    else:
                        a["data"] = True
                    if not inf:
                        a["inf"] = False
                    if f not in a["fns"] and len(a["fns"]) < _MAX_FUNCS:
                        a["fns"].append(f)
                    if line and line not in a["lines"] and len(a["lines"]) < _MAX_LINES:
                        a["lines"].append(line)
                    if via and via not in a["via"] and len(a["via"]) < _MAX_VIA:
                        a["via"].append(via)
            for callee, line, per in cargs:
                key = (f, rid(ex._canon(callee)))
                lst = arg_sites.setdefault(key, [])
                if len(lst) < _MAX_ARG_SITES:
                    lst.append({"line": line, "args": {str(j): [rid(v) for v in vs] for j, vs in per.items()}})
        derived = []
        for (x, a), v in agg.items():
            meta = {"functions": v["fns"], "lines": v["lines"], "control_only": not v["data"]}
            if v["via"]:
                meta["via"] = v["via"]
            e = {"source": x, "target": a, "relation": "derived_from",
                 "confidence": "INFERRED" if v["inf"] else "EXTRACTED", "source_file": v["file"], "weight": 1.0,
                 "metadata": meta}
            ctx = context_for("derived_from")
            if ctx:
                e["context"] = ctx
            if v["lines"]:
                e["source_location"] = f"L{v['lines'][0]}"
            derived.append(e)
        return derived, arg_sites, self._indirect(remap, nodes), self_feed

    def _indirect(self, remap, nodes):
        """-> [(caller, target, line, ctx, holder label)] for every function pointer call that could be resolved."""
        ex = self.ex

        def rid(x):
            return remap.get(x, x)

        holders: dict = {}
        for key, items in self.holders.items():
            k = ("p", rid(ex._canon(key[1])), key[2]) if key[0] == "p" else key
            holders.setdefault(k, []).extend(items)
        out = []
        for ic in self.icalls:
            key = ic["key"]
            if key[0] == "p":
                key = ("p", rid(ex._canon(key[1])), key[2])
            field = ic["field"]
            targets = []
            for (hf, fn) in holders.get(key, ()):
                if field is None or hf is None or hf == field:
                    t = rid(ex._canon(fn))
                    if t not in targets:
                        targets.append(t)
            caller = rid(ic["fn"])
            label = self._holder_label(key, field, nodes)
            for t in targets:
                if t in nodes and caller in nodes and t != caller:
                    out.append((caller, t, ic["line"], ic["ctx"], label, ic["file"]))
        return out

    @staticmethod
    def _holder_label(key, field, nodes):
        base = ""
        if key[0] == "g":
            base = nodes.get(key[1], {}).get("label", key[1])
        elif key[0] == "l":
            base = key[2][0]
        else:
            base = f"parameter {key[2]}"
        return f"{base}.{field}" if field else base
