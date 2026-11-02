"""pascal — moved verbatim from graphify/extract.py."""
from __future__ import annotations

import copy
import re
from graphify.extractors.base import _file_stem, _make_id, _read_source_bytes, _read_source_text
from graphify.extractors.pascal_calls import (
    count_args,
    is_local_receiver,
    make_raw_call,
    with_item,
    split_callee,
)
from graphify.extractors.resolution import _pascal_resolve_class, _pascal_resolve_unit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Delphi 12+ multiline string: an odd run of 3+ quotes ending its line, closed
# by the same run at the start of a later line (indentation allowed). Listed
# before the single-line form, which would otherwise read the opening `'''` as
# a quote plus an escaped quote and lose sync at the first `'` in the text.
_PAS_TOKEN_RE = re.compile(
    r"(?P<mlq>'''(?:'')*)(?!')[ \t]*\r?\n.*?\r?\n[ \t]*(?P=mlq)(?!')"
    r"|'(?:''|[^'])*'"
    r"|\{[^}]*\}"
    r"|\(\*.*?\*\)"
    r"|//[^\n]*",
    re.DOTALL,
)

_PAS_MODULE_RE = re.compile(
    r"\b(unit|program|library)\s+([A-Za-z_][\w.]*)\s*;",
    re.IGNORECASE,
)

_PAS_USES_RE = re.compile(
    r"\buses\b\s*([^;]+);",
    re.IGNORECASE | re.DOTALL,
)

_PAS_TYPE_HEADER_RE = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)(?:\s*<[^>]+>)?\s*=\s*(?:packed\s+)?"
    r"(?P<kind>class|interface|record)\b"
    r"(?:\s*\(\s*(?P<bases>[^)]*)\s*\))?",
    re.IGNORECASE,
)

_PAS_END_SEMI_RE = re.compile(r"\bend\s*;", re.IGNORECASE)

_PAS_METHOD_DECL_RE = re.compile(
    r"\b(?:procedure|function|constructor|destructor|operator)\s+"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*\([^)]*\))?"
    r"(?:\s*:\s*[\w<>,\s.]+)?"
    r"\s*;",
    re.IGNORECASE,
)

_PAS_IMPL_HEADER_RE = re.compile(
    r"\b(?:procedure|function|constructor|destructor|operator)\s+"
    r"(?P<qual>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)"
    r"(?:\s*<[^>]+>)?"
    r"(?:\s*\((?P<params>[^)]*)\))?"
    r"(?:\s*:\s*[\w<>,\s.]+)?"
    r"\s*;",
    re.IGNORECASE,
)

# Directive after a header that means the routine has no body here.
_PAS_NO_BODY_RE = re.compile(
    r"\s*(?:[a-z]+\s*;\s*)*?(?:forward|external)\b", re.IGNORECASE
)
_PAS_BEGIN_RE = re.compile(r"\b(begin|asm)\b", re.IGNORECASE)
_PAS_DECL_SECTION_RE = re.compile(
    r"\b(?:var|const)\b(.*?)(?=\b(?:var|const|type|label|procedure|function|"
    r"constructor|destructor|operator|threadvar|resourcestring)\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_PAS_DECL_NAMES_RE = re.compile(r"\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*[:=]")
_PAS_PARAM_MODIFIER_RE = re.compile(r"\b(?:var|const|out|constref)\b", re.IGNORECASE)

_PAS_BEGIN_END_TOKEN_RE = re.compile(
    r"\b(begin|end|case|try|asm|record)\b", re.IGNORECASE
)

# A call site: bare `inherited`, `inherited Name`, or a (qualified) name whose
# receiver may be an expression (`Items[0].Refresh`, `GetX().Foo`, `p^.Foo`),
# followed by `(` or `;`.
_PAS_CALL_RE = re.compile(
    r"(?<![\w.])(?:"
    r"(?P<bare>inherited)(?=\s*(?:;|\bend\b|\belse\b|\buntil\b))"
    r"|(?:(?P<inh>inherited)\s+)?"
    r"(?P<callee>[A-Za-z_]\w*"
    r"(?:\s*(?:\[[^\]]*\]|\([^()]*\)|\^)*\s*\.\s*[A-Za-z_]\w*)*)"
    r"\s*(?P<open>[(;])"
    r")",
    re.IGNORECASE,
)

# Language words only. RTL routines and intrinsics (IntToStr, Format, Free, Create,
# Assigned, Exit, ...) are reported as calls like the tree-sitter extractor does;
# the scope resolver decides whether they bind.
_PAS_KEYWORDS = frozenset({
    "begin", "end", "if", "then", "else", "while", "do", "for", "to",
    "downto", "repeat", "until", "case", "of", "try", "finally", "except",
    "with", "inherited", "result", "var", "const", "type", "nil", "true",
    "false", "uses", "unit", "program",
    "library", "interface", "implementation", "initialization", "finalization",
    "procedure", "function", "constructor", "destructor", "operator", "class",
    "record", "object", "array", "string", "integer", "boolean", "real", "char",
})

def _pascal_strip_comments(text: str) -> str:
    """Strip Pascal comments ({}, (* *), //) while preserving newlines."""
    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok.startswith("'"):
            return tok
        return "".join(c if c == "\n" else " " for c in tok)
    return _PAS_TOKEN_RE.sub(_sub, text)

def _pascal_mask_strings(text: str) -> str:
    """Blank the contents of Pascal string literals, keeping their delimiters.

    Every character between the opening and closing quote run becomes a space,
    except line breaks, so offsets and line numbers stay valid. Comments are
    left untouched; run ``_pascal_strip_comments`` first.
    """
    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if not tok.startswith("'"):
            return tok
        q = len(m.group("mlq") or "'")
        inner = "".join(c if c in "\r\n" else " " for c in tok[q:-q])
        return tok[:q] + inner + tok[-q:]
    return _PAS_TOKEN_RE.sub(_sub, text)

def _pascal_is_member_call(callee_text: str) -> bool:
    """True for a call made on another object or unit (`Query.Refresh`).

    Without the receiver's type the target cannot be told apart from any
    same-named method elsewhere, so cross-file passes must not guess it.
    `Self.Foo` stays an ordinary call on the caller's own class.
    """
    head, dot, _rest = callee_text.partition(".")
    return bool(dot) and head.strip().lower() != "self"

def _pascal_split_sections(text: str) -> tuple[str, int, str, int]:
    """Split into (iface_text, iface_offset, impl_text, impl_offset).
    Files without interface/implementation sections (dpr/lpr/inc) return
    the whole text as impl with offset 0.
    """
    iface_m = re.search(r"\binterface\b", text, re.IGNORECASE)
    impl_m = re.search(r"\bimplementation\b", text, re.IGNORECASE)
    if iface_m and impl_m:
        iface_off = iface_m.end()
        impl_off = impl_m.end()
        end_m = re.search(
            r"\b(initialization|finalization)\b", text[impl_off:], re.IGNORECASE
        )
        impl_end = impl_off + end_m.start() if end_m else len(text)
        return text[iface_off:impl_m.start()], iface_off, text[impl_off:impl_end], impl_off
    return "", 0, text, 0

def _pascal_split_uses(s: str) -> list[str]:
    """Split a uses list string, handling 'Foo in ''bar.pas''' syntax."""
    out = []
    for chunk in s.split(","):
        name = re.split(r"\s+in\s+", chunk.strip(), maxsplit=1, flags=re.IGNORECASE)[0]
        name = name.strip().strip(";")
        if name and re.match(r"[A-Za-z_][\w.]*$", name):
            out.append(name)
    return out

def _pascal_split_bases(s: str) -> list[str]:
    """Split inheritance list, handling generics like TList<T, U>."""
    out, depth, buf = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            name = re.sub(r"<.*$", "", "".join(buf).strip())
            if name:
                out.append(name)
            buf = []
        else:
            buf.append(ch)
    name = re.sub(r"<.*$", "", "".join(buf).strip())
    if name:
        out.append(name)
    return [n for n in out if re.match(r"[A-Za-z_]\w*$", n)]

def _pascal_find_body(text: str, start: int) -> tuple[int, int]:
    """Find balanced begin..end after start. Returns (body_start, body_end).
    Returns (0, 0) if no begin found.
    """
    m = re.search(r"\bbegin\b", text[start:], re.IGNORECASE)
    if not m:
        return (0, 0)
    body_start = start + m.end()
    return (body_start, _pascal_block_end(text, body_start))

def _pascal_block_end(text: str, body_start: int) -> int:
    """Offset of the `end` that closes the block opened just before body_start."""
    depth = 1
    for tok in _PAS_BEGIN_END_TOKEN_RE.finditer(text, body_start):
        kw = tok.group(1).lower()
        if kw in ("begin", "case", "try", "asm", "record"):
            depth += 1
        elif kw == "end":
            depth -= 1
            if depth == 0:
                return tok.start()
    return len(text)

_PAS_WITH_RE = re.compile(r"\bwith\b(?P<expr>.*?)\bdo\b", re.IGNORECASE | re.DOTALL)
_PAS_WITH_END_TOKEN_RE = re.compile(
    r"\b(begin|case|try|asm|record|repeat|end|until|except|finally|if|else)\b|[;()\[\]]",
    re.IGNORECASE,
)
_PAS_BEGIN_AT_RE = re.compile(r"\s*begin\b", re.IGNORECASE)


def _pascal_with_end(text: str, start: int) -> int:
    """Offset where the statement of a `with ... do` that starts at ``start`` ends.

    `do begin` runs to its matching `end`. Any other statement runs to the next
    `;` or `else` outside nested begin/case/try/repeat blocks and parentheses;
    an `else` that belongs to an `if` inside the statement does not end it.
    """
    bm = _PAS_BEGIN_AT_RE.match(text, start)
    if bm:
        return _pascal_block_end(text, bm.end())
    depth = parens = ifs = 0
    for tok in _PAS_WITH_END_TOKEN_RE.finditer(text, start):
        t = tok.group(0).lower()
        if t in ("(", "["):
            parens += 1
        elif t in (")", "]"):
            parens -= 1
        elif parens > 0:
            continue
        elif t in ("begin", "case", "try", "asm", "record", "repeat"):
            depth += 1
        elif t in ("end", "until"):
            if depth == 0:
                return tok.start()
            depth -= 1
        elif depth > 0:
            continue
        elif t == ";":
            return tok.start()
        elif t == "if":
            ifs += 1
        elif t == "else":
            if ifs == 0:
                return tok.start()
            ifs -= 1
        elif t in ("except", "finally"):
            return tok.start()
    return len(text)


def _pascal_is_multi_with(expr: str) -> bool:
    """Whether the expression list of a `with` has more than one expression."""
    depth = 0
    for ch in expr:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            return True
    return False


def _pascal_with_ranges(body_text: str, shadow: set[str]) -> list[tuple[int, int, dict]]:
    """(start, end, with_stack item) of every `with` statement in a routine body."""
    ranges = []
    for m in _PAS_WITH_RE.finditer(body_text):
        expr = m.group("expr")
        item = with_item(expr, multi=_pascal_is_multi_with(expr), shadow=shadow)
        ranges.append((m.end(), _pascal_with_end(body_text, m.end()), item))
    return ranges


@dataclass
class _PascalRoutine:
    """A routine found in the implementation section, nested ones included."""
    qualified: str
    name: str
    header_start: int
    header_end: int
    params: str
    parent: "_PascalRoutine | None" = None
    decl_end: int = 0
    body_start: int = 0
    body_end: int = 0
    end_pos: int = 0
    children: list["_PascalRoutine"] = field(default_factory=list)


def _pascal_scan_routine(text: str, hm: re.Match, parent: _PascalRoutine | None) -> _PascalRoutine:
    name = hm.group("qual")
    qualified = f"{parent.qualified}.{name}" if parent else name
    r = _PascalRoutine(qualified, name, hm.start(), hm.end(), hm.group("params") or "", parent)
    cur = hm.end()
    r.end_pos = cur
    if _PAS_NO_BODY_RE.match(text, cur):
        return r
    while True:
        nh = _PAS_IMPL_HEADER_RE.search(text, cur)
        bm = _PAS_BEGIN_RE.search(text, cur)
        if bm is None:
            return r  # no body at all: must not swallow the routines after it
        header_first = nh is not None and nh.start() < bm.start()
        # A qualified header (`TFoo.Bar`) is never nested: this one has no body.
        if nh is not None and header_first and "." in nh.group("qual"):
            return r
        if nh is not None and header_first:
            child = _pascal_scan_routine(text, nh, r)
            r.children.append(child)
            cur = r.end_pos = child.end_pos
            continue
        r.decl_end = bm.start()
        if bm.group(1).lower() == "asm":
            # Assembler body: skipped (no calls read from it), but it must end
            # the routine so the next header is not taken as nested.
            r.end_pos = _pascal_block_end(text, bm.end())
        else:
            r.body_start, r.body_end = _pascal_find_body(text, cur)
            r.end_pos = r.body_end
        return r


def _pascal_scan_routines(text: str) -> list[_PascalRoutine]:
    """Top-level routines of ``text``; nested routines hang off ``children``."""
    routines: list[_PascalRoutine] = []
    cur = 0
    while True:
        hm = _PAS_IMPL_HEADER_RE.search(text, cur)
        if not hm:
            return routines
        r = _pascal_scan_routine(text, hm, None)
        routines.append(r)
        cur = max(r.end_pos, hm.end())


def _pascal_local_names(text: str, r: _PascalRoutine) -> set[str]:
    """Lowercase parameter and local `var`/`const` names declared by ``r``.

    Declarations that sit inside a nested routine belong to that routine.
    """
    names: set[str] = set()
    for param in r.params.split(";"):
        head, colon, _type = param.partition(":")
        if colon:
            cleaned = _PAS_PARAM_MODIFIER_RE.sub(" ", head)
            names.update(n.strip().lower() for n in cleaned.split(",") if n.strip())
    if r.body_start:
        decl = list(text[r.header_end:r.decl_end])
        for child in r.children:
            for i in range(child.header_start - r.header_end, child.end_pos - r.header_end):
                if 0 <= i < len(decl):
                    decl[i] = " "
        for section in _PAS_DECL_SECTION_RE.finditer("".join(decl)):
            for stmt in section.group(1).split(";"):
                m = _PAS_DECL_NAMES_RE.match(stmt)
                if m:
                    names.update(n.strip().lower() for n in m.group(1).split(","))
    return names


def _pascal_call_args(body_text: str, open_pos: int) -> tuple[str, bool]:
    """Text between the balanced `(`...`)` at ``open_pos`` and whether it closed."""
    depth = 0
    for i in range(open_pos, len(body_text)):
        ch = body_text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth == 0:
                return body_text[open_pos + 1:i], True
    return body_text[open_pos + 1:], False


def _attach_pascal_unit(path: Path, raw: str, stem: str, nodes: list[dict], result: dict) -> None:
    """Put the unit symbol block on the module node (file node when the file
    has no unit header) and the declared visibility on every method node."""
    from graphify.extractors.pascal_unit import build_pascal_unit
    from graphify.pascal_dproj import active_dproj

    # The `.dproj` search path only locates `{$I}` files; `imports` edges are unaffected.
    dproj = active_dproj()
    include_dirs = dproj.search_path if dproj else ()
    block = build_pascal_unit(path, raw, stem=stem, include_dirs=include_dirs)
    # Two independent copies: the node marker is persisted, the result copy is
    # read in-process; extract() keeps both in step with the final node ids.
    result["pascal_unit"] = copy.deepcopy(block)
    by_id = {n["id"]: n for n in nodes}
    target = by_id.get(block["module_nid"])
    if target is not None:
        target["_pascal_unit"] = block
    for info in block["types"].values():
        for entries in info["members"].values():
            for entry in entries:
                node = by_id.get(entry.get("nid")) if entry["kind"] == "method" else None
                if node is not None and "visibility" not in node:
                    node["visibility"] = entry["vis"]


def _extract_pascal_regex(path: Path) -> dict:
    """Regex fallback for Pascal/Delphi extraction when tree-sitter-pascal
    is unavailable. Produces the same node/edge schema as the tree-sitter pass.
    """
    try:
        raw = _read_source_text(path, warn=False)
    except Exception as exc:
        return {"nodes": [], "edges": [], "error": str(exc)}

    str_path = str(path)
    stem = _file_stem(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    def _add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({
                "id": nid,
                "label": label,
                "file_type": "code",
                "source_file": str_path,
                "source_location": f"L{line}",
            })

    def _add_edge(src: str, tgt: str, relation: str, line: int, context: str | None = None) -> None:
        # A class method declared in the interface section and defined in the
        # implementation section both emit a `method` edge to the same node, so
        # dedup on (src, tgt, relation) to keep the graph from carrying doubled
        # method/contains/inherits edges (mirrors _add_node's seen_ids guard).
        key = (src, tgt, relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edge: dict = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": "EXTRACTED",
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": 1.0,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    def _lineno(text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    file_nid = _make_id(str_path)
    _add_node(file_nid, path.name, 1)

    # Mask string contents too, so text inside a literal (SQL such as
    # `initcap(...)`, or an `end;`) is never read as code by the regexes below.
    stripped = _pascal_mask_strings(_pascal_strip_comments(raw))

    # Module header
    module_nid = file_nid
    mod_m = _PAS_MODULE_RE.search(stripped)
    if mod_m:
        mod_name = mod_m.group(2)
        module_nid = _make_id(stem, mod_name)
        _add_node(module_nid, mod_name, _lineno(stripped, mod_m.start()))
        _add_edge(file_nid, module_nid, "contains", _lineno(stripped, mod_m.start()))

    iface_text, iface_off, impl_text, impl_off = _pascal_split_sections(stripped)

    # Uses clauses
    for section_text, section_off in ((iface_text, iface_off), (impl_text, impl_off)):
        for um in _PAS_USES_RE.finditer(section_text):
            line = _lineno(stripped, section_off + um.start())
            for unit_name in _pascal_split_uses(um.group(1)):
                tgt_nid = _pascal_resolve_unit(path, unit_name)
                _add_edge(module_nid, tgt_nid, "imports", line, context="import")

    # Type declarations (classes / interfaces) in the interface section and,
    # when the file has sections, in the implementation section too.
    type_sections = (
        [(iface_text, iface_off), (impl_text, impl_off)] if iface_text else [(stripped, 0)]
    )
    impl_type_spans: list[tuple[int, int]] = []
    for search_text, search_off in type_sections:
        pos = 0
        while pos < len(search_text):
            hm = _PAS_TYPE_HEADER_RE.search(search_text, pos)
            if not hm:
                break
            type_name = hm.group("name")
            bases_raw = hm.group("bases") or ""
            line = _lineno(stripped, search_off + hm.start())
            cls_nid = _make_id(stem, type_name)
            _add_node(cls_nid, type_name, line)
            _add_edge(module_nid, cls_nid, "contains", line)

            for base_name in _pascal_split_bases(bases_raw):
                same_file_nid = _make_id(stem, base_name)
                if same_file_nid in seen_ids:
                    # Base class already declared earlier in this same file --
                    # reuse its real node instead of the cross-file/stub lookup
                    # below (which assumes one-class-per-file and would create a
                    # duplicate node for a base class that shares this file).
                    base_nid = same_file_nid
                else:
                    resolved = _pascal_resolve_class(path, base_name)
                    if resolved:
                        # Cross-file base class found on disk -- its real node
                        # arrives via THAT file's own extraction. Do not add a
                        # duplicate stub here: it would carry this file's
                        # source_file (wrong -- it belongs to the base class's
                        # own file) and collide with the real node under
                        # cross-file id disambiguation, producing two different
                        # salted ids for what should be one class (breaks
                        # cross-file `inherits`-chain resolution downstream).
                        base_nid = resolved
                    else:
                        base_nid = _make_id(base_name)
                        if base_nid not in seen_ids:
                            _add_node(base_nid, base_name, line)
                _add_edge(cls_nid, base_nid, "inherits", line)

            # Find class body (up to next end;)
            end_m = _PAS_END_SEMI_RE.search(search_text, hm.end())
            body_text = search_text[hm.end():end_m.start()] if end_m else ""
            body_off = search_off + hm.end()

            # Forward method declarations inside the class body
            for mm in _PAS_METHOD_DECL_RE.finditer(body_text):
                mname = mm.group("name")
                mline = _lineno(stripped, body_off + mm.start())
                method_nid = _make_id(cls_nid, mname)
                _add_node(method_nid, f"{mname}()", mline)
                _add_edge(cls_nid, method_nid, "method", mline)

            if iface_text and search_text is impl_text:
                impl_type_spans.append((hm.start(), end_m.end() if end_m else len(search_text)))
            pos = end_m.end() if end_m else len(search_text)

    # The member declarations of a class declared in the implementation section
    # are not routine headers: blank them (keeping offsets and newlines).
    for span_start, span_end in impl_type_spans:
        blanked = "".join(c if c == "\n" else " " for c in impl_text[span_start:span_end])
        impl_text = impl_text[:span_start] + blanked + impl_text[span_end:]

    # Implementation headers (procedure/function/constructor/destructor),
    # nested routines included.
    impl_records: list[tuple[str, int, str, str, str]] = []
    # (proc_nid, line, body_text, container, name_lower)
    # proc_nid -> (qualified names from the routine outwards, names that shadow
    # a call, source-case routine name)
    impl_scope: dict[str, tuple[list[str], set[str], str]] = {}

    def _add_routine(
        r: _PascalRoutine, parent_nid: str | None, shadow: set[str], outer_method: str = "",
    ) -> None:
        line = _lineno(stripped, impl_off + r.header_start)
        if parent_nid is not None:
            container, relation = parent_nid, "contains"
            label, name_lower, method_name = f"{r.name}()", r.name.lower(), r.name
        elif "." in r.qualified:
            cls_part, method_part = r.qualified.split(".", 1)
            cls_nid = _make_id(stem, cls_part)
            container = cls_nid if cls_nid in seen_ids else module_nid
            relation = "method" if cls_nid in seen_ids else "contains"
            label, name_lower, method_name = f"{method_part}()", method_part.lower(), method_part
        else:
            container, relation = module_nid, "contains"
            label, name_lower, method_name = f"{r.qualified}()", r.qualified.lower(), r.qualified
        proc_nid = _make_id(stem, r.qualified)
        _add_node(proc_nid, label, line)
        _add_edge(container, proc_nid, relation, line)

        body_text = impl_text[r.body_start:r.body_end] if r.body_start else ""
        # Count call lines from where the body starts, not from the header:
        # `var`/`const` sections between them would otherwise shift every call.
        body_line = _lineno(stripped, impl_off + r.body_start) if r.body_start else line
        impl_records.append((proc_nid, body_line, body_text, container, name_lower))
        shadow = shadow | _pascal_local_names(impl_text, r)
        chain: list[str] = []
        outer: _PascalRoutine | None = r
        while outer is not None:
            chain.append(outer.qualified)
            outer = outer.parent
        impl_scope[proc_nid] = (chain, shadow, method_name if parent_nid is None else outer_method)
        for child in r.children:
            _add_routine(child, proc_nid, shadow, method_name if parent_nid is None else outer_method)

    for routine in _pascal_scan_routines(impl_text):
        _add_routine(routine, None, set())

    # Every call is reported as a raw call; graphify.pascal_resolution binds
    # them after all files are extracted, with the Delphi scope rules.
    raw_calls: list[dict] = []
    for caller_nid, body_line, body_text, _container, _name_lower in impl_records:
        chain, shadow, method_name = impl_scope[caller_nid]
        with_ranges = _pascal_with_ranges(body_text, shadow)
        for cm in _PAS_CALL_RE.finditer(body_text):
            call_line = body_line + body_text.count("\n", 0, cm.start())
            arg_count: int | None = 0
            if cm.group("open") == "(":
                arg_text, closed = _pascal_call_args(body_text, cm.end() - 1)
                arg_count = count_args(arg_text) if closed else None
            if cm.group("bare"):
                qualifier, callee_name = "inherited", method_name
            else:
                qualifier, callee_name = split_callee(cm.group("callee"))
                if cm.group("inh") and qualifier is None:
                    qualifier = "inherited"
            if qualifier != "inherited":
                name_lower = callee_name.lower()
                if name_lower in _PAS_KEYWORDS:
                    continue
                # A parameter or local of the caller (or an enclosing routine)
                # is not a routine call.
                if qualifier is None and name_lower in shadow:
                    continue
            raw_calls.append(make_raw_call(
                source_file=str_path, line=call_line, caller_nid=caller_nid,
                callee=callee_name, qualifier=qualifier, arg_count=arg_count,
                enclosing_routines=chain,
                local_receiver=is_local_receiver(qualifier, shadow),
                # innermost `with` first: the one that starts last
                with_stack=[
                    item for _s, _e, item in sorted(
                        (r for r in with_ranges if r[0] <= cm.start() < r[1]),
                        key=lambda r: r[0], reverse=True,
                    )
                ],
            ))

    result = {
        "nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0,
        "raw_calls": raw_calls,
    }
    _attach_pascal_unit(path, raw, stem, nodes, result)
    return result

def extract_pascal(path: Path) -> dict:
    """Extract units, classes, procedures, uses-imports, and calls from Pascal/Delphi files.

    Produces nodes for:
    - The file itself
    - unit / program / library declarations
    - class and interface type declarations
    - procedure / function implementations (including qualified TClass.Method names)

    Produces edges for:
    - file --contains--> module
    - module --imports--> other file node (via uses clause, resolved to path-based IDs)
    - class --inherits--> base class
    - class/module --contains--> method forward declaration
    - class/module --contains--> procedure/function implementation

    Reports every call in a routine body as a raw call; graphify.pascal_resolution
    binds them to `calls` edges after all files are extracted.

    Uses tree-sitter-pascal when available; falls back to a regex-based extractor
    (_extract_pascal_regex) when it isn't installed or fails to parse, so Pascal
    extraction works out of the box without an extra pip install.
    """
    try:
        import tree_sitter_pascal as tspascal
        from tree_sitter import Language, Parser
    except ImportError:
        return _extract_pascal_regex(path)

    try:
        language = Language(tspascal.language())
        parser = Parser(language)
        source = _read_source_bytes(path)
        tree = parser.parse(source)
        root = tree.root_node
    except Exception:
        return _extract_pascal_regex(path)

    stem = _file_stem(path)
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    proc_bodies: list[tuple[str, Any, str, str]] = []
    # (proc_nid, body_node, container, name_lower)

    def _read(node) -> str:  # type: ignore[no-untyped-def]
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({
                "id": nid, "label": label, "file_type": "code",
                "source_file": str_path, "source_location": f"L{line}",
            })

    def add_edge(
        src: str, tgt: str, relation: str, line: int,
        confidence: str = "EXTRACTED", weight: float = 1.0,
        context: str | None = None,
    ) -> None:
        # A class method declared in the interface section and defined in the
        # implementation section both emit a `method` edge to the same node, so
        # dedup on (src, tgt, relation) to keep the graph from carrying doubled
        # method/contains/inherits edges (mirrors add_node's seen_ids guard).
        key = (src, tgt, relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edge: dict[str, Any] = {
            "source": src, "target": tgt, "relation": relation,
            "confidence": confidence, "source_file": str_path,
            "source_location": f"L{line}", "weight": weight,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, 1)
    module_nid = file_nid

    def _proc_name(header_node) -> str | None:  # type: ignore[no-untyped-def]
        name_node = header_node.child_by_field_name("name")
        if name_node:
            return _read(name_node)
        for child in header_node.children:
            if child.type in ("identifier", "genericDot", "genericTpl"):
                return _read(child)
        return None

    # proc_nid -> (qualified names from the routine outwards, lowercase names of
    # parameters and locals that shadow a call, source-case outermost method name)
    proc_scope: dict[str, tuple[list[str], set[str], str]] = {}

    def _named(node) -> list:  # type: ignore[no-untyped-def]
        return [c for c in node.children if c.is_named and c.type != "comment"]

    def _local_names(node, header) -> set[str]:  # type: ignore[no-untyped-def]
        """Lowercase parameter, local `var` and `const` names declared by a routine."""
        names: set[str] = set()
        for args in header.children:
            if args.type == "declArgs":
                for arg in args.children:
                    names.update(
                        _read(i).lower() for i in arg.children if i.type == "identifier"
                    )
        for section in node.children:
            if section.type in ("declVars", "declConsts"):
                for decl in section.children:
                    names.update(
                        _read(i).lower() for i in decl.children if i.type == "identifier"
                    )
        return names

    def add_routine(node, parent_nid: str, outer) -> None:  # type: ignore[no-untyped-def]
        # outer: None for a top-level routine, else the enclosing routine's
        # (qualified, nid, shadow, outermost method name, chain).
        line = node.start_point[0] + 1
        header = next((c for c in node.children if c.type == "declProc"), None)
        name = _proc_name(header) if header else None
        if not header or not name:
            for child in node.children:
                walk(child, parent_nid)
            return
        body_node = next((c for c in node.children if c.type == "block"), None)
        container, relation = parent_nid, "contains"
        label = method_name = name
        if outer is not None:
            qualified = f"{outer[0]}.{name}"
            method_name = outer[3]
        else:
            qualified = name
            if "." in name:
                parts = name.split(".", 1)
                cls_nid = _make_id(stem, parts[0])
                if cls_nid in seen_ids:
                    container, relation = cls_nid, "method"
                label = method_name = parts[-1]
        proc_nid = _make_id(stem, qualified)
        add_node(proc_nid, f"{label}()", line)
        add_edge(container, proc_nid, relation, line)
        shadow = (outer[2] if outer else set()) | _local_names(node, header)
        chain = [qualified] + (outer[4] if outer else [])
        proc_scope[proc_nid] = (chain, shadow, method_name)
        if body_node:
            proc_bodies.append((proc_nid, body_node, container, label.lower()))
        for child in node.children:
            if child.type == "defProc":
                add_routine(child, proc_nid, (qualified, proc_nid, shadow, method_name, chain))

    def walk(node, parent_nid: str) -> None:  # type: ignore[no-untyped-def]
        nonlocal module_nid
        t = node.type
        line = node.start_point[0] + 1

        if t in ("unit", "program", "library"):
            name_node = next((c for c in node.children if c.type == "moduleName"), None)
            mod_name = _read(name_node) if name_node else path.stem
            mod_nid = _make_id(stem, mod_name)
            add_node(mod_nid, mod_name, line)
            add_edge(file_nid, mod_nid, "contains", line)
            module_nid = mod_nid
            for child in node.children:
                walk(child, mod_nid)
            return

        if t == "declUses":
            for child in node.children:
                if child.type == "moduleName":
                    mod_name = _read(child)
                    tgt_nid = _pascal_resolve_unit(path, mod_name)
                    add_edge(parent_nid, tgt_nid, "imports", line, context="import")
            return

        if t == "declType":
            type_name = None
            kind_node = None
            enum_node = None
            for child in node.children:
                if child.type == "identifier" and type_name is None:
                    type_name = _read(child)
                elif child.type in ("declClass", "declIntf", "declHelper") and kind_node is None:
                    kind_node = child
                elif child.type == "type" and enum_node is None:
                    # An enumerated type (`TColor = (clRed, clGreen);`) nests its
                    # values under type -> declEnum -> declEnumValue.
                    enum_node = next(
                        (gc for gc in child.children if gc.type == "declEnum"), None
                    )
            if type_name and enum_node is not None:
                # A Pascal enumerated type is a named type whose values are its
                # cases. Without this branch declType matched only class/interface/
                # helper kinds, so the whole enum type AND its values fell through
                # and were dropped. Emit the type node plus a `case_of` edge per
                # value — the Pascal parity of Java #1719 / C# / Swift / Scala enums.
                enum_nid = _make_id(stem, type_name)
                add_node(enum_nid, type_name, line)
                add_edge(parent_nid, enum_nid, "contains", line)
                for value in enum_node.children:
                    if value.type != "declEnumValue":
                        continue
                    value_name_node = next(
                        (c for c in value.children if c.type == "identifier"), None
                    )
                    if value_name_node is None:
                        continue
                    value_name = _read(value_name_node)
                    if not value_name:
                        continue
                    value_line = value.start_point[0] + 1
                    value_nid = _make_id(enum_nid, value_name)
                    add_node(value_nid, value_name, value_line)
                    add_edge(enum_nid, value_nid, "case_of", value_line)
                return
            if type_name and kind_node:
                cls_nid = _make_id(stem, type_name)
                add_node(cls_nid, type_name, line)
                add_edge(parent_nid, cls_nid, "contains", line)
                for child in kind_node.children:
                    if child.type == "typeref":
                        base_name = _read(child)
                        base_nid = _make_id(stem, base_name)
                        if base_nid not in seen_ids:
                            # Try cross-file resolution (TFooBar → FooBar.pas)
                            resolved = _pascal_resolve_class(path, base_name)
                            if resolved:
                                # Cross-file base class found on disk -- its
                                # real node arrives via THAT file's own
                                # extraction. Do not add a duplicate stub
                                # here: it would carry this file's
                                # source_file (wrong) and collide with the
                                # real node under cross-file id
                                # disambiguation, producing two different
                                # salted ids for what should be one class.
                                base_nid = resolved
                            else:
                                base_nid = _make_id(base_name)
                                if base_nid not in seen_ids:
                                    # Stub for RTL/external base classes.
                                    add_node(base_nid, base_name, line)
                        add_edge(cls_nid, base_nid, "inherits", line)
                for child in kind_node.children:
                    walk(child, cls_nid)
                return
            for child in node.children:
                walk(child, parent_nid)
            return

        if t == "declProcFwd":
            header = next((c for c in node.children if c.type == "declProc"), None)
            if header:
                name = _proc_name(header)
                if name and "." not in name:
                    method_nid = _make_id(parent_nid, name)
                    add_node(method_nid, f"{name}()", line)
                    add_edge(parent_nid, method_nid, "method", line)
            return

        if t == "declProc" and node.parent is not None and node.parent.type == "implementation":
            # A routine without a body here (`external`, `forward`): it still
            # gets a node, and the routines after it stay top-level.
            name = _proc_name(node)
            if name and "." not in name:
                proc_nid = _make_id(stem, name)
                add_node(proc_nid, f"{name}()", line)
                add_edge(parent_nid, proc_nid, "contains", line)
            return

        if t == "defProc":
            add_routine(node, parent_nid, None)
            return

        for child in node.children:
            walk(child, parent_nid)

    walk(root, file_nid)

    # Second pass: report every call inside procedure/function bodies as a raw
    # call; graphify.pascal_resolution binds them with the Delphi scope rules.
    raw_calls: list[dict] = []

    def _emit_or_report(
        caller_nid: str, callee: str, qualifier: str | None, line: int, arg_count: int | None,
        withs: list[dict],
    ) -> None:
        # A parameter or local of the caller (or an enclosing routine) is not
        # a routine call.
        if qualifier is None and callee.lower() in proc_scope[caller_nid][1]:
            return
        raw_calls.append(make_raw_call(
            source_file=str_path, line=line, caller_nid=caller_nid, callee=callee,
            qualifier=qualifier, arg_count=arg_count,
            enclosing_routines=proc_scope[caller_nid][0],
            local_receiver=is_local_receiver(qualifier, proc_scope[caller_nid][1]),
            with_stack=withs,
        ))

    def _call_site(
        caller_nid: str, callee_text: str, line: int, arg_count: int | None,
        withs: list[dict],
    ) -> None:
        qualifier, callee = split_callee(callee_text)
        callee = re.sub(r"<.*$", "", callee).strip()
        if re.fullmatch(r"[A-Za-z_]\w*", callee):
            _emit_or_report(caller_nid, callee, qualifier, line, arg_count, withs)

    def _inherited_site(  # type: ignore[no-untyped-def]
        caller_nid: str, node, line: int, arg_count: int | None, withs: list[dict],
    ) -> None:  # type: ignore[no-untyped-def]
        # `inherited Foo` names the method; a bare `inherited` repeats the name
        # of the current (outermost enclosing) method.
        ident = next((c for c in node.children if c.type == "identifier"), None)
        callee = _read(ident) if ident else proc_scope[caller_nid][2]
        _emit_or_report(caller_nid, callee, "inherited", line, arg_count, withs)

    def _with_item(node, caller_nid: str) -> dict:  # type: ignore[no-untyped-def]
        """`with_stack` item of a `with` node: the expressions between `with` and `do`."""
        exprs = []
        for child in node.children:
            if child.type == "kDo":
                break
            if child.is_named and child.type != "kWith":
                exprs.append(child)
        text = source[exprs[0].start_byte:exprs[-1].end_byte].decode("utf-8", "replace") if exprs else ""
        return with_item(text, multi=len(exprs) > 1, shadow=proc_scope[caller_nid][1])

    def walk_calls(  # type: ignore[no-untyped-def]
        node, caller_nid: str, withs: list[dict] | None = None,
    ) -> None:
        withs = withs or []
        if node.type == "defProc":
            return  # a nested routine is its own caller
        line = node.start_point[0] + 1
        if node.type == "exprCall":
            named = _named(node)
            callee_node = next((c for c in named if c.type != "exprArgs"), None)
            args = next((c for c in named if c.type == "exprArgs"), None)
            arg_count = len(_named(args)) if args is not None else 0
            if callee_node is not None:
                if callee_node.type == "inherited":
                    _inherited_site(caller_nid, callee_node, line, arg_count, withs)
                else:
                    _call_site(caller_nid, _read(callee_node), line, arg_count, withs)
        elif node.type == "statement":
            # A call without parentheses: `Reset;`, `Unit1.Foo.Bar;`, `inherited;`.
            # tree-sitter has no exprCall wrapper for these.
            named = _named(node)
            if len(named) == 1:
                only = named[0]
                if only.type == "identifier":
                    _emit_or_report(caller_nid, _read(only), None, line, 0, withs)
                elif only.type == "exprDot":
                    _call_site(caller_nid, _read(only), line, 0, withs)
                elif only.type == "inherited":
                    _inherited_site(caller_nid, only, line, 0, withs)
        elif node.type == "assignment":
            # `F := TFoo.Create;` calls without parentheses on the right-hand
            # side; the regex extractor reports them too.
            named = _named(node)
            rhs = named[-1] if len(named) >= 2 else None
            if rhs is not None and rhs.type == "identifier":
                _emit_or_report(caller_nid, _read(rhs), None, line, 0, withs)
            elif rhs is not None and rhs.type == "exprDot":
                _call_site(caller_nid, _read(rhs), line, 0, withs)
        if node.type == "with":
            # The expressions are evaluated outside the block; the statement
            # after `do` sees the new innermost with-object.
            inner = [_with_item(node, caller_nid), *withs]
            after_do = False
            for child in node.children:
                walk_calls(child, caller_nid, inner if after_do else withs)
                after_do = after_do or child.type == "kDo"
            return
        for child in node.children:
            walk_calls(child, caller_nid, withs)

    for proc_nid, body_node, _container, _name_lower in proc_bodies:
        walk_calls(body_node, proc_nid)

    result = {
        "nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0,
        "raw_calls": raw_calls,
    }
    _attach_pascal_unit(path, _read_source_text(path, warn=False), stem, nodes, result)
    return result

