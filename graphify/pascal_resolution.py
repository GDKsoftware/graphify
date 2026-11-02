"""Scope-aware call resolution for Pascal/Delphi.

The Pascal extractors report every call as a raw call (callee, qualifier,
argument count, enclosing routines) and bind none of them. This pass binds
them after all files are extracted, with the scope rules of the Delphi
compiler over a `SymbolTable` built from the per-unit `_pascal_unit` blocks
(and, when bundled, the Delphi symbol file):

1. a routine nested in the current or an enclosing routine;
2. a member of the caller's own class;
3. a member of an ancestor, nearest first, as far as visibility allows;
4. a routine of the caller's own unit;
5. an interface routine of a unit in the implementation `uses`, last first;
6. an interface routine of a unit in the interface `uses`, last first;
7. `System` and `SysInit`.

The first level that knows the name decides. A name that is not visible at any
level gets no edge: Pascal calls are never bound by name alone. A hit without a
graph node (a field, a declaration without a body in the scan) also ends the
search without an edge, so a missing edge is preferred to a wrong one. A hit in
the Delphi symbol file (or in `System`/`SysInit`) has no node of its own: the
edge goes to the stub node of its unit, the target of the caller's `imports`
edge (spec 5.5).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .extractors.pascal_calls import is_expression_receiver
from .pascal_dproj import active_dproj
from .pascal_symbols import Member, Routine, SymbolTable, load_delphi_symbols

RESOLVED_BY = "delphi_scope"
_IMPLICIT_UNITS = ("System", "SysInit")
_RANK = {"EXTRACTED": 2, "INFERRED": 1}
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_CREATE_RE = re.compile(r"([A-Za-z_]\w*)\s*\.\s*Create\s*(?:\(.*\))?", re.IGNORECASE | re.DOTALL)


def _namespaces() -> list[str]:
    """Default namespaces that complete a unit name from a `uses` clause.

    The `DCC_Namespace` entries of the active `.dproj`; empty without one.
    """
    info = active_dproj()
    return list(info.namespaces) if info else []


@dataclass(frozen=True)
class Resolution:
    target: str
    confidence: str
    score: float
    rule: str
    # Set for a hit in the symbol file: the unit whose stub node is the target.
    external_unit: str | None = None


class _Stop(Exception):
    """A level knows the name but yields no edge; the search ends there."""


def _is_method(candidate: Member | Routine) -> bool:
    return not isinstance(candidate, Member) or candidate.kind == "method"


def _visible_from_descendant(member: Member, caller_unit: str) -> bool:
    """Spec 5.6 for a member of an ancestor, seen from a descendant's method."""
    if member.vis == "strict private":
        return False
    if member.vis == "private":
        return member.unit.casefold() == caller_unit.casefold()
    return True


def _member_visible(member: Member, caller_unit: str, caller_chain: set[str]) -> bool:
    """Spec 5.6 for `Obj.Member`; `caller_chain` is the caller's class and its ancestors."""
    if member.vis in ("public", "published"):
        return True
    owner = member.owner.casefold()
    own_class = bool(caller_chain) and owner in caller_chain
    same_unit = member.unit.casefold() == caller_unit.casefold()
    if member.vis == "strict private":
        return own_class and same_unit
    if member.vis == "private":
        return same_unit
    if member.vis == "strict protected":
        return own_class
    if member.vis == "protected":
        return same_unit or own_class
    return False


class DelphiResolver:
    """Binds Pascal raw calls following the Delphi scope rules (spec 5.1, 5.2)."""

    def __init__(self, table: SymbolTable) -> None:
        self.table = table
        # Stub nodes the edges point to: id -> unit name.
        self.externals: dict[str, str] = {}

    def resolve(self, rc: dict) -> Resolution | None:
        callee = str(rc.get("callee") or "")
        if not callee:
            return None
        unit = self.table.unit_of_file(str(rc.get("source_file") or ""))
        if unit is None:
            return None
        qualifier = rc.get("qualifier")
        if rc.get("is_member_call") or is_expression_receiver(qualifier):
            return None
        enclosing: Sequence[str] = rc.get("enclosing_routines") or ()
        arg_count = rc.get("arg_count")
        with_stack = rc.get("with_stack") or ()
        try:
            if with_stack:
                res = self._resolve_in_with(unit, enclosing, with_stack, qualifier, callee, arg_count)
            else:
                res = self._resolve_scoped(unit, enclosing, qualifier, callee, arg_count)
        except _Stop:
            return None
        if res is None or res.external_unit is None:
            return res
        target = self.table.external_target(unit, res.external_unit)
        if not target:
            return None  # the caller does not reach the unit: no edge
        self.externals[target] = res.external_unit
        return replace(res, target=target)

    def _resolve_scoped(
        self, unit: str, enclosing: Sequence[str], qualifier: str | None, callee: str,
        arg_count: int | None,
    ) -> Resolution | None:
        if qualifier is None:
            return self._unqualified(unit, enclosing, callee, arg_count)
        if qualifier.casefold() == "self":
            cls = self.table.caller_class(unit, enclosing)
            return self._class_scope(unit, cls, callee, arg_count, own=True, prefix="5.2/1")
        if qualifier.casefold() == "inherited":
            cls = self.table.caller_class(unit, enclosing)
            return self._class_scope(unit, cls, callee, arg_count, own=False, prefix="5.2/2")
        return self._qualified(unit, enclosing, qualifier, callee, arg_count)

    # ------------------------------------------------- inside `with` (5.3)

    def _resolve_in_with(
        self, unit: str, enclosing: Sequence[str], with_stack: Sequence[dict],
        qualifier: str | None, callee: str, arg_count: int | None,
    ) -> Resolution | None:
        """Spec 5.3. The name may be a member of the with-object, so only a
        single `with` of a known type gives an edge.

        A qualified call is not looked at either: its head may be a member of the
        with-object too. Only `Self` and `inherited` are certain not to be, and
        they resolve as outside a `with`. Rules: 5.3/1 is a member of the type of
        the `with` expression, 5.3/2 a name that the type lacks and 5.1 finds.
        """
        if qualifier is not None:
            if qualifier.casefold() in ("self", "inherited"):
                return self._resolve_scoped(unit, enclosing, qualifier, callee, arg_count)
            return None
        if len(with_stack) != 1 or with_stack[0].get("multi") or with_stack[0].get("local"):
            return None
        with_type = self._with_type(unit, enclosing, str(with_stack[0].get("expr") or ""))
        if with_type is None:
            return None
        cls = self.table.caller_class(unit, enclosing)
        hit = self._type_scope(unit, cls, with_type, callee, arg_count, "5.3/1")
        if hit is None:
            hit = self._unqualified(unit, enclosing, callee, arg_count)
            if hit is not None:
                hit = replace(hit, rule="5.3/2")
        if hit is None:
            return None
        # The type may be known only in part, so even a unique hit is inferred.
        if hit.confidence == "EXTRACTED":
            return replace(hit, confidence="INFERRED", score=0.95)
        return hit

    def _with_type(self, unit: str, enclosing: Sequence[str], expr: str) -> str | None:
        """Qualified type of a `with` expression, or None when it is not known.

        Known are `TFoo.Create`, a global variable, a type name and a field of
        the caller's class with a declared type. Raises `_Stop` where the head
        names several things that are not told apart (as for a qualifier).
        """
        table = self.table
        created = _CREATE_RE.fullmatch(expr)
        if created:
            return table.lookup_type(unit, created.group(1))
        if not _IDENT_RE.fullmatch(expr):
            return None
        cls = table.caller_class(unit, enclosing)
        own_field = self._own_field(unit, cls, expr)
        global_type = self._global_type(unit, expr)
        type_name = table.lookup_type(unit, expr)
        known = global_type if global_type is not None else type_name
        if known is not None:
            if own_field is not None:
                raise _Stop
            return known
        if own_field is None:
            return None
        if own_field.kind not in ("field", "property") or own_field.proc_type or not own_field.type:
            return None
        if table.is_proc_typed(own_field.owner, own_field.name):
            return None
        return table.lookup_type(own_field.unit, own_field.type)

    # ------------------------------------------------------------ the rules

    def _unqualified(
        self, unit: str, enclosing: Sequence[str], name: str, arg_count: int | None,
    ) -> Resolution | None:
        table = self.table
        for routine in enclosing:
            found = table.nested(unit, routine, name)
            if found:
                return self._outcome(found, arg_count, "5.1/1")
        cls = table.caller_class(unit, enclosing)
        hit = self._class_scope(unit, cls, name, arg_count, own=True, prefix="5.1")
        if hit is not None:
            return hit
        own = table.unit(unit)
        if own is None:
            return None
        hit = self._unit_scope(own.name, name, arg_count, "5.1/4", interface_only=False)
        if hit is not None:
            return hit
        for uses, rule in ((own.implementation_uses, "5.1/5"), (own.interface_uses, "5.1/6")):
            for used in reversed(uses):
                resolved = table.resolve_unit_name(used)
                if resolved is None:
                    self._check_duplicates(used, name)
                    continue
                hit = self._unit_scope(resolved, name, arg_count, rule, interface_only=True)
                if hit is not None:
                    return hit
        for implicit in _IMPLICIT_UNITS:
            resolved = table.resolve_unit_name(implicit)
            if resolved is None:
                continue
            if table.declares_type(resolved, name):
                raise _Stop
            found = table.routines(resolved, name, interface_only=True)
            if found:
                return self._outcome(found, arg_count, "5.1/7")
            if table.declares_value(resolved, name, interface_only=True):
                raise _Stop
        return None

    def _class_scope(
        self, unit: str, cls: str | None, name: str, arg_count: int | None,
        *, own: bool, prefix: str,
    ) -> Resolution | None:
        """Rules 2 (own class, all visibilities) and 3 (ancestors, nearest first).

        `prefix` "5.1" numbers the rules 5.1/2 and 5.1/3; a 5.2 prefix is used
        as the rule itself (`Self` is 5.2/1, `inherited` 5.2/2).
        """
        if cls is None:
            return None
        table = self.table
        if own:
            found = table.members(cls, name)
            if found:
                return self._outcome(found, arg_count, "5.1/2" if prefix == "5.1" else prefix)
        for ancestor in table.ancestors(cls):
            found = [m for m in table.members(ancestor, name) if _visible_from_descendant(m, unit)]
            if found:
                return self._outcome(found, arg_count, "5.1/3" if prefix == "5.1" else prefix)
        return None

    def _check_duplicates(self, used: str, name: str) -> None:
        """A unit name declared by several scanned files: if any of them knows
        `name`, which one the compiler would take is unknown, so stop."""
        table = self.table
        for dup in table.duplicates(used):
            if (
                table.declares_type(dup, name)
                or table.routines(dup, name, interface_only=True)
                or table.global_var(dup, name) is not None
                or table.declares_value(dup, name, interface_only=True)
            ):
                raise _Stop

    def _unit_scope(
        self, unit: str, name: str, arg_count: int | None, rule: str, *, interface_only: bool,
    ) -> Resolution | None:
        table = self.table
        if table.declares_type(unit, name):
            raise _Stop  # a typecast (spec 5.4)
        found = table.routines(unit, name, interface_only=interface_only)
        if found:
            return self._outcome(found, arg_count, rule)
        if table.declares_value(unit, name, interface_only=interface_only):
            raise _Stop  # a variable, constant or enumeration value, not a routine
        return None

    # ------------------------------------------------ qualified calls (5.2)

    def _qualified(
        self, unit: str, enclosing: Sequence[str], qualifier: str, name: str,
        arg_count: int | None,
    ) -> Resolution | None:
        """Spec 5.2, points 3-7: unit, global variable, type, typed field."""
        table = self.table
        parts = qualifier.split(".")
        for end in range(len(parts), 0, -1):
            prefix = ".".join(parts[:end])
            if table.duplicates(prefix):
                raise _Stop  # which unit the compiler takes is unknown
            resolved = table.resolve_unit_name(prefix)
            if resolved is not None:
                return self._in_unit(unit, enclosing, resolved, parts[end:], name, arg_count)
        if len(parts) > 1:
            return None  # a member chain (`A.B.Foo`) on something that is not a unit
        cls = table.caller_class(unit, enclosing)
        own_field = self._own_field(unit, cls, qualifier)
        global_type = self._global_type(unit, qualifier)
        type_name = table.lookup_type(unit, qualifier)
        if global_type is not None:
            if own_field is not None:
                raise _Stop  # a field of the class hides the global: not told apart
            return self._type_scope(unit, cls, global_type, name, arg_count, "5.2/4")
        if type_name is not None:
            if own_field is not None:
                raise _Stop
            return self._type_scope(unit, cls, type_name, name, arg_count, "5.2/5")
        if own_field is not None:
            return self._field_scope(unit, cls, own_field, name, arg_count)
        return None  # point 7: not known, so no edge

    def _in_unit(
        self, caller_unit: str, enclosing: Sequence[str], target: str, rest: list[str],
        name: str, arg_count: int | None,
    ) -> Resolution | None:
        table = self.table
        if not rest:
            own = target.casefold() == caller_unit.casefold()
            return self._unit_scope(target, name, arg_count, "5.2/3", interface_only=not own)
        if len(rest) > 1:
            return None
        cls = table.caller_class(caller_unit, enclosing)
        var_type = table.global_var(target, rest[0])
        if var_type is not None:
            return self._type_scope(caller_unit, cls, var_type, name, arg_count, "5.2/3")
        if table.declares_type(target, rest[0]):
            qualified = table.lookup_type(target, rest[0])
            if qualified is not None:
                return self._type_scope(caller_unit, cls, qualified, name, arg_count, "5.2/3")
        return None

    def _global_type(self, unit: str, name: str) -> str | None:
        """Type of the global variable `name` as seen from `unit` (rules 4-6 order).

        Raises `_Stop` when a duplicated unit that is in scope declares it, or
        when the variable is known but its type is not.
        """
        table = self.table
        own = table.unit(unit)
        if own is None:
            return None
        levels: list[str] = [own.name]
        for used in (*reversed(own.implementation_uses), *reversed(own.interface_uses)):
            resolved = table.resolve_unit_name(used)
            if resolved is None:
                for dup in table.duplicates(used):
                    if table.global_var(dup, name) is not None:
                        raise _Stop
                continue
            levels.append(resolved)
        for level in levels:
            found = table.global_var(level, name)
            if found is not None:
                if table.type(found) is None:
                    raise _Stop  # the type is not in the table
                return found
        return None

    def _own_field(self, unit: str, cls: str | None, name: str) -> Member | None:
        """Field or property `name` of the caller's class or an ancestor, if any."""
        if cls is None:
            return None
        table = self.table
        for owner in (cls, *table.ancestors(cls)):
            found = [
                m for m in table.members(owner, name)
                if owner == cls or _visible_from_descendant(m, unit)
            ]
            if found:
                return found[0]
        return None

    def _field_scope(
        self, unit: str, cls: str | None, member: Member, name: str, arg_count: int | None,
    ) -> Resolution | None:
        """Point 6: search the declared type of a field or property."""
        if member.kind not in ("field", "property") or member.proc_type or not member.type:
            raise _Stop
        if self.table.is_proc_typed(member.owner, member.name):
            raise _Stop
        qualified = self.table.lookup_type(member.unit, member.type)
        if qualified is None:
            raise _Stop
        return self._type_scope(unit, cls, qualified, name, arg_count, "5.2/6")

    def _type_scope(
        self, unit: str, cls: str | None, qualified: str, name: str,
        arg_count: int | None, rule: str,
    ) -> Resolution | None:
        """Search a type and its ancestors with the visibility of spec 5.6."""
        table = self.table
        if table.type(qualified) is None:
            return None
        caller_chain = {c.casefold() for c in ([cls, *table.ancestors(cls)] if cls else [])}
        for owner in (qualified, *table.ancestors(qualified)):
            found = [
                m for m in table.members(owner, name)
                if _member_visible(m, unit, caller_chain)
            ]
            if found:
                return self._outcome(found, arg_count, rule)
        return None

    # -------------------------------------------------- candidates to an edge

    @staticmethod
    def _outcome(
        candidates: Sequence[Member | Routine], arg_count: int | None, rule: str,
    ) -> Resolution:
        """Spec 5.5. Raises `_Stop` when the level yields no edge."""
        if not all(_is_method(c) for c in candidates):
            raise _Stop  # a field or property, procedure-typed or not (spec 5.4)
        if len(candidates) == 1:
            chosen, confidence, score = candidates[0], "EXTRACTED", 1.0
        else:
            fits = [
                c for c in candidates
                if arg_count is not None and _arity_fits(c, arg_count)
            ]
            if len(fits) == 1:
                chosen, confidence, score = fits[0], "EXTRACTED", 1.0
            else:
                chosen, confidence, score = candidates[0], "INFERRED", 0.85
        if chosen.external:
            return Resolution("", confidence, score, rule, external_unit=chosen.unit)
        if chosen.nid is None:
            raise _Stop  # no graph node (declaration without a body in the scan)
        return Resolution(chosen.nid, confidence, score, rule)


def _arity_fits(candidate: Member | Routine, arg_count: int) -> bool:
    params = candidate.params
    if params is None:
        return True
    low = candidate.min_params if candidate.min_params is not None else params
    return low <= arg_count <= params


def build_symbol_table(nodes: list[dict]) -> SymbolTable:
    """Symbol table from the `_pascal_unit` markers on `nodes` and the Delphi symbols."""
    blocks = [
        {**n["_pascal_unit"], "source_file": str(n.get("source_file") or "")}
        for n in nodes
        if isinstance(n.get("_pascal_unit"), dict)
    ]
    return SymbolTable.from_sources(blocks, load_delphi_symbols(), _namespaces())


def resolve_pascal_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Bind every Pascal raw call by the Delphi scope rules; one edge per pair.

    `all_nodes` holds the context nodes on an incremental build too, so the
    table also knows the units that were not re-extracted.
    """
    raw_calls = [
        rc
        for result in per_file if isinstance(result, dict)
        for rc in result.get("raw_calls", []) or []
        if isinstance(rc, dict) and rc.get("language") == "pascal"
    ]
    if not raw_calls:
        return
    table = build_symbol_table(all_nodes)
    resolver = DelphiResolver(table)
    node_file = {n.get("id"): n.get("source_file") for n in all_nodes}
    existing: dict[tuple, dict] = {
        (e.get("source"), e.get("target")): e
        for e in all_edges
        if e.get("relation") == "calls"
    }
    for rc in raw_calls:
        caller = rc.get("caller_nid")
        if not caller:
            continue
        # The caller node's file is the spelling the table was built from.
        source_file = node_file.get(caller) or rc.get("source_file")
        res = resolver.resolve({**rc, "source_file": source_file})
        if res is None or res.target == caller:
            continue
        pair = (caller, res.target)
        edge = existing.get(pair)
        if edge is not None:
            if _RANK.get(res.confidence, 0) > _RANK.get(str(edge.get("confidence")), 0) or (
                res.confidence == edge.get("confidence")
                and res.score > float(edge.get("confidence_score") or 0.0)
            ):
                edge.update(
                    confidence=res.confidence, confidence_score=res.score,
                    resolved_by=RESOLVED_BY, rule=res.rule,
                )
            continue
        edge = {
            "source": caller,
            "target": res.target,
            "relation": "calls",
            "context": "call",
            "confidence": res.confidence,
            "confidence_score": res.score,
            "resolved_by": RESOLVED_BY,
            "rule": res.rule,
            "source_file": rc.get("source_file", ""),
            "source_location": rc.get("source_location"),
            "weight": 1.0,
        }
        existing[pair] = edge
        all_edges.append(edge)
    _add_external_nodes(resolver.externals, all_nodes)


def _add_external_nodes(externals: dict[str, str], all_nodes: list[dict]) -> None:
    """Make sure every stub an edge points to is a node, flagged `pascal_external`.

    A node that exists already (a unit file in the scan) only gets the flag when
    it is a stub itself, so a real file node stays a real file node.
    """
    known = {n.get("id"): n for n in all_nodes}
    for nid, unit in externals.items():
        node = known.get(nid)
        if node is None:
            all_nodes.append({
                "id": nid, "label": unit, "file_type": "code", "source_file": "",
                "pascal_external": True,
            })
        elif not node.get("source_file"):
            node["pascal_external"] = True
