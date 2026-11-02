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
graph node (a field, a symbol-file routine, a declaration without a body in the
scan) also ends the search without an edge, so a missing edge is preferred to
a wrong one.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .extractors.pascal_calls import is_expression_receiver
from .pascal_symbols import Member, Routine, SymbolTable, load_delphi_symbols

RESOLVED_BY = "delphi_scope"
_IMPLICIT_UNITS = ("System", "SysInit")
_RANK = {"EXTRACTED": 2, "INFERRED": 1}


@dataclass(frozen=True)
class Resolution:
    target: str
    confidence: str
    score: float
    rule: str


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


class DelphiResolver:
    """Binds Pascal raw calls following the Delphi scope rules (spec 5.1, 5.2 1-2)."""

    def __init__(self, table: SymbolTable) -> None:
        self.table = table

    def resolve(self, rc: dict) -> Resolution | None:
        callee = str(rc.get("callee") or "")
        if not callee:
            return None
        unit = self.table.unit_of_file(str(rc.get("source_file") or ""))
        if unit is None:
            return None
        if rc.get("with_stack"):
            return None  # names inside `with` may be members of the with-object
        qualifier = rc.get("qualifier")
        if is_expression_receiver(qualifier):
            return None
        enclosing: Sequence[str] = rc.get("enclosing_routines") or ()
        arg_count = rc.get("arg_count")
        try:
            if qualifier is None:
                return self._unqualified(unit, enclosing, callee, arg_count)
            if qualifier.casefold() == "self":
                cls = self.table.caller_class(unit, enclosing)
                return self._class_scope(unit, cls, callee, arg_count, own=True, prefix="5.2/1")
            if qualifier.casefold() == "inherited":
                cls = self.table.caller_class(unit, enclosing)
                return self._class_scope(unit, cls, callee, arg_count, own=False, prefix="5.2/2")
        except _Stop:
            return None
        # Other qualifiers (unit, global variable, type, field) are not bound yet.
        return None

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
            if table.routines(resolved, name, interface_only=True):
                # Rule 7 targets come from the symbol file: no edge yet.
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
        if chosen.nid is None:
            raise _Stop  # no graph node (symbol file, declaration without body)
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
    return SymbolTable.from_sources(blocks, load_delphi_symbols())


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
