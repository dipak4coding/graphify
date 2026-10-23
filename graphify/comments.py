"""Parse a function header comment (as libclang returns it in ``raw_comment``) into short facts.

Expected style (German or English keywords, all optional)::

    /**----------------------------
    Funktion: Name
      Description text ...
    <br><br>
    Parameter:
      Arg_X
        Description of Arg_X
    <br><br>
    Rueckgabe:
      0 not allowed
      1 allowed
    ----------------------------*/

Result keys: ``summary`` (first sentence/line of the description, <= 200 chars), ``comment`` (cleaned full
text, <= 1500 chars), ``params`` {name: text}, ``returns`` (text). Nothing is invented: a comment without
the sections gives just ``summary`` and ``comment``.
"""
from __future__ import annotations

import re

MAX_COMMENT = 1500
MAX_SUMMARY = 200

_SECTION = re.compile(
    r"^(?P<kw>Funktion|Function|Beschreibung|Description|Parameter|Parameters|Param|"
    r"R(?:ue|ü)ckgabe(?:wert)?|Return(?:s| value)?|Retval)\s*:\s*(?P<rest>.*)$", re.I)
_RULE = re.compile(r"^[\s\-=*_#~/]+$")
_BR = re.compile(r"<\s*br\s*/?\s*>", re.I)


def _clean(raw: str) -> list:
    text = raw.strip()
    text = re.sub(r"^/\*[*!]?", "", text)
    text = re.sub(r"\*+/\s*$", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(///<?|//!<?|//)", "", line) if line.lstrip().startswith("//") else line
        line = re.sub(r"^\s*\*(?!/)\s?", "", line) if re.match(r"^\s*\*(?!/)", line) else line
        line = _BR.sub("", line).rstrip()
        if _RULE.match(line) and line.strip():
            continue
        lines.append(line)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def parse_function_comment(raw: str | None, func_name: str = "") -> dict:
    if not raw or not raw.strip():
        return {}
    lines = _clean(raw)
    if not lines:
        return {}
    base = min((_indent(l) for l in lines if l.strip()), default=0)
    lines = [l[base:] if l.strip() else "" for l in lines]

    blocks: dict = {"description": [], "params": [], "returns": []}
    current = "description"
    seen_section = False
    for line in lines:
        m = _SECTION.match(line) if _indent(line) == 0 else None
        if m:
            kw = m.group("kw").lower()
            rest = m.group("rest").strip()
            if kw in ("funktion", "function"):
                current = "description"
                if rest and rest.rstrip("()") != func_name.rstrip("()"):
                    blocks["description"].append(rest)
            elif kw in ("beschreibung", "description"):
                current = "description"
                if rest:
                    blocks["description"].append(rest)
            elif kw.startswith("param"):
                current = "params"
                if rest:
                    blocks["params"].append(rest)
            else:
                current = "returns"
                if rest:
                    blocks["returns"].append(rest)
            seen_section = True
            continue
        blocks[current].append(line)

    desc = [l.strip() for l in blocks["description"] if l.strip()]
    out: dict = {}
    if desc:
        first = desc[0]
        m = re.match(r"(.+?[.!?])(\s|$)", first)
        out["summary"] = (m.group(1) if m else first)[:MAX_SUMMARY]
    params = _params(blocks["params"])
    if params:
        out["params"] = params
    returns = " | ".join(l.strip() for l in blocks["returns"] if l.strip())
    if returns:
        out["returns"] = returns[:400]
    body = [l for l in lines if l.strip() or True]
    text = "\n".join(re.sub(r"[ \t]+$", "", l) for l in body)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text:
        out["comment"] = text[:MAX_COMMENT]
        out.setdefault("summary", text.splitlines()[0].strip()[:MAX_SUMMARY])
    return out


def _params(block: list) -> dict:
    """`name` alone on a line + indented description lines, or `name - text` / `name: text` on one line."""
    out: dict = {}
    name = None
    name_indent = 0
    for line in block:
        if not line.strip():
            continue
        ind, txt = _indent(line), line.strip()
        inline = re.match(r"^([A-Za-z_]\w*)\s*[:\-–]\s+(.+)$", txt)
        if name is None or ind <= name_indent:
            if inline:
                out[inline.group(1)] = inline.group(2)
                name = None
            elif re.match(r"^[A-Za-z_]\w*$", txt):
                name, name_indent = txt, ind
                out.setdefault(name, "")
            else:
                name = None
            continue
        out[name] = (out[name] + " " + txt).strip()
    return {k: v[:300] for k, v in out.items()}
