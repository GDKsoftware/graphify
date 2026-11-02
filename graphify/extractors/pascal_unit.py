"""Per-unit symbol block for Pascal/Delphi: uses per section, types with
members and visibility, routines, globals, procedure types and includes.

The builder is text based (comments and strings are masked first), so it also
works on units that tree-sitter cannot parse and on the interface sections of
the Delphi RTL/VCL sources. Node ids follow the extractors in ``pascal.py``:
``_make_id(stem, "Type.Method")``, ``_make_id(stem, Name)`` and
``_make_id(stem, "Outer.Inner")``.

The helpers of ``pascal.py`` are reached through the module object at call time
so that ``pascal.py`` can import this module without a circular import.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from graphify.extractors import pascal as _pas
from graphify.extractors.base import _file_stem, _make_id, _read_source_text

_LOG = logging.getLogger(__name__)

# Section words of a class body. One regex for all six, `strict` forms first.
_SECTION_RE = re.compile(
    r"\b(strict\s+private|strict\s+protected|private|protected|public|published)\b",
    re.IGNORECASE,
)
_PUBLISHED_ANCESTORS = frozenset(
    {"tform", "tframe", "tdatamodule", "tcomponent", "tpersistent"}
)

_INCLUDE_RE = re.compile(
    r"(?:\{|\(\*)\$(?:INCLUDE|I)\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s}*]+))",
    re.IGNORECASE,
)
_TYPEINFO_RE = re.compile(r"(?:\{|\(\*)\$(?:M\s*([+-])|TYPEINFO\s+(ON|OFF)\b)", re.IGNORECASE)

_ROUTINE_RE = re.compile(
    r"(?:class\s+)?(?:procedure|function|constructor|destructor|operator)\s+"
    r"(?P<name>&?[A-Za-z_]\w*(?:\s*<[^>]*>)?(?:\.&?[A-Za-z_]\w*(?:\s*<[^>]*>)?)*)",
    re.IGNORECASE,
)
_PROC_TYPE_RE = re.compile(
    r"\b([A-Za-z_]\w*)(?:\s*<[^>]*>)?\s*=\s*(?:reference\s+to\s+)?(?:procedure|function)\b",
    re.IGNORECASE,
)
_INLINE_PROC_RE = re.compile(r"(?:reference\s+to\s+)?(?:procedure|function)\b", re.IGNORECASE)
_FIELD_RE = re.compile(
    r"(?P<names>&?[A-Za-z_]\w*(?:\s*,\s*&?[A-Za-z_]\w*)*)\s*:(?!=)\s*(?P<type>.+)",
    re.DOTALL,
)
_PROPERTY_RE = re.compile(
    r"property\s+(?P<name>&?[A-Za-z_]\w*)\s*(?:\[.*?\])?\s*(?::\s*(?P<type>.*?))?"
    r"(?=\s+(?:read|write|index|default|stored|implements|nodefault|dispid)\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# What may follow `class`/`record` in a type header besides the base list.
_TYPE_TAIL_RE = re.compile(
    r"\s*(?:(?:sealed|abstract)\b|helper\b(?=\s*(?:\(|for\b)))?"
    r"\s*(?:\(\s*(?P<bases>[^)]*)\s*\))?"
    r"(?:\s*for\s+[A-Za-z_][\w.]*(?:<[^>]*>)?)?",
    re.IGNORECASE,
)
_CLASS_OF_RE = re.compile(r"\s*of\b", re.IGNORECASE)
_GUID_RE = re.compile(r"\s*\[[^\]]*\]")

_STMT_TOKEN_RE = re.compile(
    r"[;()\[\]]|\b(?:end|record)\b|=\s*(?:packed\s+)?(?:class|object|interface)\b",
    re.IGNORECASE,
)
_NOT_OPENER_RE = re.compile(r"\s*(?:of\b|;|\([^)]*\)\s*;)", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z_]\w*")

_DIRECTIVES = frozenset({
    "overload", "inline", "assembler", "stdcall", "cdecl", "register", "pascal",
    "safecall", "reintroduce", "platform", "deprecated", "experimental", "static",
    "varargs", "dynamic", "virtual", "override", "abstract", "final", "export",
    "far", "near", "local", "unsafe", "delayed", "message", "dispid", "winapi",
})
_LOCAL_SECTIONS = frozenset({"var", "const", "type", "label", "threadvar", "resourcestring"})


@dataclass
class _Routine:
    name: str
    params: int
    min_params: int
    has_params: bool
    children: list["_Routine"] = field(default_factory=list)


def pascal_unit_includes(raw: str) -> list[str]:
    """File names of the `{$I x}` / `{$INCLUDE x}` directives, in order."""
    return [name for name, _pos in _include_directives(raw)]


def _directives(raw: str) -> list[tuple[int, str]]:
    """Compiler directives (`{$...}`, `(*$...*)`) of the raw text with their offset.

    The comment tokenizer is used so a directive inside a `//` comment or a
    string is not seen.
    """
    return [
        (m.start(), m.group(0))
        for m in _pas._PAS_TOKEN_RE.finditer(raw)
        if m.group(0).startswith(("{$", "(*$"))
    ]


def _include_directives(raw: str) -> list[tuple[str, int]]:
    found = []
    for pos, text in _directives(raw):
        m = _INCLUDE_RE.match(text)
        if not m:
            continue
        name = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if name and not name.startswith("%"):
            found.append((name, pos))
    return found


def build_pascal_unit(
    path: Path,
    raw: str,
    *,
    stem: str,
    interface_only: bool = False,
    include_dirs: Sequence[Path] = (),
) -> dict:
    """Build the `pascal_unit` symbol block of one unit (spec 4.1)."""
    stripped = _pas._pascal_mask_strings(_pas._pascal_strip_comments(raw))
    mod_m = _pas._PAS_MODULE_RE.search(stripped)
    unit_name = mod_m.group(2) if mod_m else path.stem
    module_nid: str | None = None
    if not interface_only:
        module_nid = _make_id(stem, unit_name) if mod_m else _make_id(str(path))

    iface_text, iface_off, impl_text, impl_off = _pas._pascal_split_sections(stripped)
    has_sections = impl_off > 0
    if interface_only and not has_sections:
        iface_text, iface_off, impl_text, impl_off = impl_text, 0, "", 0
    elif interface_only:
        impl_text = ""

    typeinfo = [
        (pos, (m.group(1) == "+") if m.group(1) else (m.group(2).upper() == "ON"))
        for pos, text in _directives(raw)
        if (m := _TYPEINFO_RE.match(text))
    ]
    proc_types = list(dict.fromkeys(
        m.group(1) for m in _PROC_TYPE_RE.finditer(iface_text + "\n" + impl_text)
    ))
    proc_lower = {p.lower() for p in proc_types}

    types: dict[str, dict] = {}
    nid_stem = None if interface_only else stem
    iface_types, iface_masked = _scan_types(iface_text, iface_off, typeinfo, proc_lower, nid_stem)
    types.update(iface_types)
    impl_masked = impl_text
    if impl_text:
        impl_types, impl_masked = _scan_types(impl_text, impl_off, typeinfo, proc_lower, nid_stem)
        types.update(impl_types)

    iface_routines, iface_globals = _scan_decls(iface_masked, bodies=False)
    impl_routines, impl_globals = _scan_decls(impl_masked, bodies=True)
    # (stem, routine) pairs; a routine of an include carries the include's stem.
    decls = [(stem, r) for r in iface_routines]
    impls = [(stem, r) for r in impl_routines]

    include_names = _include_directives(raw)
    impl_start = impl_off - len("implementation") if has_sections else 0
    missing: list[str] = []
    for name, pos in include_names:
        in_iface = (iface_off <= pos < impl_start) if has_sections else interface_only
        _collect_include(
            name, path, include_dirs, in_iface, decls, impls, missing, {path}, interface_only,
        )
    if missing:
        _LOG.warning("%s: include file not found, skipped: %s", path, ", ".join(missing))

    if interface_only:
        _drop_private(types)

    routines = _merge_routines(decls, impls, with_nid=not interface_only, stem=stem)
    nested: dict[str, dict[str, list[dict]]] = {}
    if not interface_only:
        for rstem, r in impls:
            _collect_nested(r, r.name, rstem, nested)

    globals_ = dict(iface_globals)
    if not interface_only:
        globals_.update(impl_globals)

    return {
        "unit": unit_name,
        "module_nid": module_nid,
        "interface_uses": _uses(iface_text),
        "implementation_uses": [] if interface_only else _uses(impl_text),
        "includes": [name for name, _pos in include_names],
        "types": types,
        "routines": routines,
        "nested": nested,
        "globals": globals_,
        "proc_types": proc_types,
    }


def _uses(section_text: str) -> list[str]:
    names: list[str] = []
    for um in _pas._PAS_USES_RE.finditer(section_text):
        names.extend(_pas._pascal_split_uses(um.group(1)))
    return names


def _collect_include(
    name: str, owner: Path, include_dirs: Sequence[Path], in_iface: bool,
    decls: list, impls: list, missing: list[str], chain: set[Path], interface_only: bool,
) -> None:
    found = _find_include(name, owner, include_dirs)
    if found is None:
        missing.append(name)
        return
    if found in chain:
        return
    try:
        raw = _read_source_text(found, warn=False)
    except (OSError, UnicodeDecodeError):
        missing.append(name)
        return
    text = _pas._pascal_mask_strings(_pas._pascal_strip_comments(raw))
    _types, text = _scan_types(text, 0, [], set(), None)
    stem = _file_stem(found)
    if in_iface:
        routines, _globals = _scan_decls(text, bodies=False)
        decls.extend((stem, r) for r in routines)
    elif not interface_only:
        routines, _globals = _scan_decls(text, bodies=True)
        impls.extend((stem, r) for r in routines)
    for sub_name, _pos in _include_directives(raw):
        _collect_include(
            sub_name, found, include_dirs, in_iface, decls, impls, missing,
            chain | {found}, interface_only,
        )


def _find_include(name: str, owner: Path, include_dirs: Sequence[Path]) -> Path | None:
    names = [name] if Path(name).suffix else [name, name + ".pas"]
    for directory in (owner.parent, *include_dirs):
        for candidate in names:
            p = Path(directory) / candidate
            if p.is_file():
                return p
    return None


def _merge_routines(decls: list, impls: list, *, with_nid: bool, stem: str) -> dict:
    """Pair interface declarations with implementations; overloads by arity."""
    out: dict[str, list[dict]] = {}
    keys: dict[str, str] = {}
    pending: dict[str, list[dict]] = {}

    def entry_of(r: _Routine, interface: bool) -> dict:
        return {"interface": interface, "params": r.params, "min_params": r.min_params}

    for dstem, r in decls:
        key = keys.setdefault(r.name.lower(), r.name)
        e = entry_of(r, True)
        if with_nid:
            e["nid"] = _make_id(dstem, r.name)
        out.setdefault(key, []).append(e)
        pending.setdefault(key, []).append(e)
    for istem, r in impls:
        if "." in r.name:
            continue  # method implementation: described by its class
        key = keys.setdefault(r.name.lower(), r.name)
        waiting = pending.get(key, [])
        match = next((e for e in waiting if e["params"] == r.params), None)
        if match is None and not r.has_params:
            match = waiting[0] if waiting else None
        if match is not None:
            waiting.remove(match)
            match["nid"] = _make_id(istem, r.name)
            continue
        e = entry_of(r, False)
        e["nid"] = _make_id(istem, r.name)
        out.setdefault(key, []).append(e)
    return out


def _collect_nested(r: _Routine, qualified: str, stem: str, out: dict) -> None:
    for child in r.children:
        out.setdefault(qualified, {}).setdefault(child.name, []).append({
            "params": child.params,
            "min_params": child.min_params,
            "nid": _make_id(stem, f"{qualified}.{child.name}"),
        })
        _collect_nested(child, f"{qualified}.{child.name}", stem, out)


# ---------------------------------------------------------------- text helpers

def _find_depth0(s: str, ch: str) -> int:
    depth = 0
    for i, c in enumerate(s):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif c == ch and depth == 0:
            return i
    return -1


def _split_depth0(s: str, ch: str) -> list[str]:
    parts: list[str] = []
    while True:
        i = _find_depth0(s, ch)
        if i < 0:
            parts.append(s)
            return parts
        parts.append(s[:i])
        s = s[i + 1:]


def _balanced(s: str, start: int) -> tuple[str, int]:
    """Content of the parenthesis opened at ``s[start]`` and the offset after it."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
    return s[start + 1:], len(s)


def _count_params(params: str) -> tuple[int, int]:
    """(number of parameters, number without a default value)."""
    total = required = 0
    for group in _split_depth0(params, ";"):
        group = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", group).strip()
        if not group:
            continue
        colon = _find_depth0(group, ":")
        head = group[:colon] if colon >= 0 else group
        head = _pas._PAS_PARAM_MODIFIER_RE.sub(" ", head)
        count = len([n for n in head.split(",") if n.strip()])
        total += count
        if colon < 0 or _find_depth0(group[colon + 1:], "=") < 0:
            required += count
    return total, required


def _normalize(s: str) -> str:
    return " ".join(s.split())


def _skip_filler(text: str, pos: int) -> int:
    n = len(text)
    while pos < n and (text[pos].isspace() or text[pos] in ";."):
        pos += 1
    return pos


def _peek_word(text: str, pos: int) -> str:
    m = _WORD_RE.match(text, pos)
    return m.group(0).lower() if m else ""


def _next_statement(text: str, pos: int, *, stop_at_end: bool = False) -> tuple[int, int, bool]:
    """End of the statement starting at ``pos``: (end, offset after it, closed).

    A statement ends at a `;` outside parentheses, brackets and nested
    record/class bodies. With ``stop_at_end`` an unmatched `end` ends it too
    (``closed`` is then true), which is how a class body is delimited.
    """
    paren = nest = 0
    for m in _STMT_TOKEN_RE.finditer(text, pos):
        tok = m.group(0).lower()
        if tok in ("(", "["):
            paren += 1
        elif tok in (")", "]"):
            paren = max(0, paren - 1)
        elif tok == ";":
            if paren == 0 and nest == 0:
                return m.start(), m.end(), False
        elif tok == "end":
            if nest > 0:
                nest -= 1
            elif stop_at_end and paren == 0:
                return m.start(), m.end(), True
        elif tok == "record" or not _NOT_OPENER_RE.match(text, m.end()):
            nest += 1
    return len(text), len(text), False


# ------------------------------------------------------------------ declarations

def _scan_decls(text: str, *, bodies: bool) -> tuple[list[_Routine], dict[str, str]]:
    """Top-level routines and global variables of a declaration stream.

    With ``bodies`` the text is an implementation section: routine bodies and
    their local declarations are skipped, nested routines are recorded.
    """
    routines: list[_Routine] = []
    globs: dict[str, str] = {}
    pos, n, mode = 0, len(text), ""
    while True:
        pos = _skip_filler(text, pos)
        if pos >= n:
            break
        word = _peek_word(text, pos)
        if word in ("begin", "asm", "initialization", "finalization"):
            break
        if text[pos] == "[":
            pos = _balanced_bracket(text, pos)
            continue
        if word == "end":
            pos += 3
            continue
        if _ROUTINE_RE.match(text, pos):
            r, pos = _parse_routine(text, pos, bodies)
            routines.append(r)
            mode = ""
            continue
        end, nxt, _ = _next_statement(text, pos)
        stmt, pos = text[pos:end], nxt
        class_var = re.match(r"class\s+var\b", stmt, re.IGNORECASE)
        if word in ("var", "threadvar") or class_var:
            mode = "var"
            stmt = stmt[class_var.end() if class_var else len(word):]
        elif word in ("const", "resourcestring", "type", "label", "uses", "exports"):
            mode = "skip"
        if mode == "var":
            _add_variables(stmt, globs)
    return routines, globs


def _balanced_bracket(text: str, pos: int) -> int:
    depth = 0
    for i in range(pos, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _add_variables(stmt: str, out: dict[str, str]) -> None:
    m = _FIELD_RE.fullmatch(stmt.strip())
    if not m:
        return
    type_text = m.group("type")
    eq = _find_depth0(type_text, "=")
    if eq >= 0:
        type_text = type_text[:eq]
    type_text = _normalize(type_text)
    if not type_text:
        return
    for name in re.split(r"\s*,\s*", m.group("names")):
        out[name.lstrip("&")] = type_text


def _parse_routine(text: str, pos: int, bodies: bool) -> tuple[_Routine, int]:
    m = _ROUTINE_RE.match(text, pos)
    end, nxt, _ = _next_statement(text, pos)
    rest = text[m.end():end].lstrip()
    name = re.sub(r"\s*<[^>]*>", "", m.group("name")).replace("&", "")
    params_text = None
    if rest.startswith("("):
        params_text, _after = _balanced(rest, 0)
    total, required = _count_params(params_text) if params_text is not None else (0, 0)
    r = _Routine(name, total, required, params_text is not None)
    pos = nxt
    local_seen = False
    while True:
        p = _skip_filler(text, pos)
        if p >= len(text):
            return r, p
        word = _peek_word(text, p)
        if word in ("forward", "external"):
            _e, pos, _c = _next_statement(text, p)
            return r, pos
        if not local_seen and word in _DIRECTIVES:
            _e, pos, _c = _next_statement(text, p)
            continue
        nested = _ROUTINE_RE.match(text, p)
        if nested:
            if not bodies or "." in nested.group("name"):
                return r, p
            child, pos = _parse_routine(text, p, bodies)
            r.children.append(child)
            local_seen = True
            continue
        if not bodies:
            return r, p
        if word in ("begin", "asm"):
            stop = _pas._pascal_block_end(text, p + len(word))
            return r, min(len(text), stop + 3)
        _e, pos, _c = _next_statement(text, p)
        local_seen = True


# ------------------------------------------------------------------------ types

def _scan_types(
    text: str, base: int, typeinfo: list[tuple[int, bool]], proc_lower: set[str],
    nid_stem: str | None,
) -> tuple[dict[str, dict], str]:
    """Class, record and interface types of ``text`` and ``text`` with them blanked."""
    types: dict[str, dict] = {}
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        hm = _pas._PAS_TYPE_HEADER_RE.search(text, pos)
        if not hm:
            break
        pos = hm.end()
        tail = _TYPE_TAIL_RE.match(text, hm.end())
        body_pos = tail.end() if tail else hm.end()
        bases = hm.group("bases")
        if bases is None and tail:
            bases = tail.group("bases")
        if _CLASS_OF_RE.match(text, body_pos):
            continue
        after = _skip_filler_no_dot(text, body_pos)
        if after < len(text) and text[after] == ";":
            if bases is None:
                continue  # forward declaration
            types[hm.group("name")] = _new_type(hm, bases, {})
            spans.append((hm.start(), after))
            pos = after
            continue
        guid = _GUID_RE.match(text, body_pos) if hm.group("kind").lower() == "interface" else None
        stmt_pos = guid.end() if guid else body_pos
        statements: list[str] = []
        cur = stmt_pos
        while True:
            end, nxt, closed = _next_statement(text, cur, stop_at_end=True)
            statements.append(text[cur:end])
            cur = nxt
            if closed or nxt >= len(text):
                break
        if not closed:
            continue
        abs_pos = base + hm.start()
        in_typeinfo = next((on for p, on in reversed(typeinfo) if p < abs_pos), False)
        kind = hm.group("kind").lower()
        tdef = _new_type(hm, bases, {})
        tdef["members"] = _parse_members(
            statements, tdef, in_typeinfo, proc_lower,
            (nid_stem, hm.group("name")) if nid_stem is not None else None,
        )
        types[hm.group("name")] = tdef
        spans.append((hm.start(), cur))
        pos = cur
    return types, _blank(text, spans)


def _skip_filler_no_dot(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _dotted_bases(bases: str) -> list[str]:
    first = _split_depth0(bases, ",")[0] if bases.strip() else ""
    first = re.sub(r"<.*$", "", first.strip())
    return [first] if re.fullmatch(r"[A-Za-z_][\w.]*", first) else []


def _new_type(hm: re.Match, bases: str | None, members: dict) -> dict:
    ancestors = _pas._pascal_split_bases(bases or "") or _dotted_bases(bases or "")
    return {
        "kind": hm.group("kind").lower(),
        "ancestor": ancestors[0] if ancestors else None,
        "members": members,
    }


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(re.sub(r"[^\n]", " ", text[start:end]))
        last = end
    out.append(text[last:])
    return "".join(out)


def _is_proc_type(type_text: str, proc_lower: set[str]) -> bool:
    if _INLINE_PROC_RE.match(type_text):
        return True
    head = re.split(r"[<\s]", type_text, maxsplit=1)[0]
    return head.split(".")[-1].lower() in proc_lower


def _parse_members(
    statements: list[str], tdef: dict, typeinfo: bool, proc_lower: set[str],
    nid_of: tuple[str, str] | None,
) -> dict[str, list[dict]]:
    ancestor = tdef["ancestor"]
    published = tdef["kind"] == "class" and (
        typeinfo or (ancestor or "").split(".")[-1].lower() in _PUBLISHED_ANCESTORS
    )
    vis = "published" if published else "public"
    members: dict[str, list[dict]] = {}
    skipping = False

    def add(name: str, entry: dict) -> None:
        name = name.lstrip("&")
        if nid_of is not None and entry["kind"] == "method":
            entry["nid"] = _make_id(nid_of[0], f"{nid_of[1]}.{name}")
        members.setdefault(name, []).append(entry)

    for stmt in statements:
        s = stmt.strip()
        while s:
            m = _SECTION_RE.match(s)
            if m:
                vis = _normalize(m.group(1).lower())
                skipping = False
                s = s[m.end():].lstrip()
            elif s.startswith("["):
                s = s[_balanced_bracket(s, 0):].lstrip()
            else:
                break
        if not s:
            continue
        word = _peek_word(s, 0)
        class_var = re.match(r"class\s+var\b", s, re.IGNORECASE)
        if class_var or word in ("var", "threadvar"):
            skipping = False
            s = s[class_var.end() if class_var else len(word):].strip()
        elif word in ("const", "type", "case"):
            skipping = True
            continue
        rm = _ROUTINE_RE.match(s)
        if rm:
            skipping = False
            name = re.sub(r"\s*<[^>]*>", "", rm.group("name"))
            if "." in name:
                continue  # interface method resolution clause
            rest = s[rm.end():].lstrip()
            total, required = (_count_params(_balanced(rest, 0)[0]) if rest.startswith("(")
                               else (0, 0))
            add(name, {"vis": vis, "kind": "method", "params": total, "min_params": required})
            continue
        s = re.sub(r"^class\s+(?=property\b)", "", s, flags=re.IGNORECASE)
        pm = _PROPERTY_RE.match(s)
        if pm:
            skipping = False
            entry = {"vis": vis, "kind": "property"}
            ptype = _normalize(pm.group("type") or "")
            if ptype:
                entry["type"] = ptype
            entry["proc_type"] = bool(ptype) and _is_proc_type(ptype, proc_lower)
            add(pm.group("name"), entry)
            continue
        if skipping:
            continue
        fm = _FIELD_RE.fullmatch(s)
        if not fm:
            continue
        type_text = fm.group("type")
        eq = _find_depth0(type_text, "=")
        if eq >= 0:
            type_text = type_text[:eq]
        type_text = _normalize(type_text)
        if re.match(r"(?:packed\s+)?record\b", type_text, re.IGNORECASE):
            type_text = "record"
        for name in re.split(r"\s*,\s*", fm.group("names")):
            add(name, {"vis": vis, "kind": "field", "type": type_text,
                       "proc_type": _is_proc_type(type_text, proc_lower)})
    return members


def _drop_private(types: dict[str, dict]) -> None:
    for tdef in types.values():
        tdef["members"] = {
            name: kept
            for name, entries in tdef["members"].items()
            if (kept := [e for e in entries if e["vis"] not in ("private", "strict private")])
        }
