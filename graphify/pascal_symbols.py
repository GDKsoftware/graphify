"""Symbol table over scanned Pascal units and bundled Delphi symbols.

Built from the per-unit `pascal_unit` blocks of the scanned files and, when
available, the symbol file of a Delphi version. All Pascal names compare
case-insensitively; the source spelling is what is stored and returned.
"""
from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_DELPHI_DATA_DIR = Path(__file__).parent / "data" / "delphi"
_SYMBOL_FILE_RE = re.compile(r"^delphi-(.+)\.symbols\.json\.gz$", re.IGNORECASE)
_IMPLICIT_UNIT = "System"


@dataclass(frozen=True)
class Member:
    name: str
    kind: str
    vis: str
    params: int | None
    min_params: int | None
    type: str | None
    proc_type: bool
    nid: str | None
    owner: str
    unit: str
    external: bool = False  # declared in the Delphi symbol file, not in the scan


@dataclass(frozen=True)
class Routine:
    name: str
    unit: str
    interface: bool
    params: int | None
    min_params: int | None
    nid: str | None
    external: bool = False  # declared in the Delphi symbol file, not in the scan


@dataclass(frozen=True)
class UnitSymbols:
    name: str
    interface_uses: tuple[str, ...]
    implementation_uses: tuple[str, ...]
    scanned: bool
    module_nid: str | None
    source_file: str | None


@dataclass(frozen=True)
class TypeSymbols:
    qualified: str
    unit: str
    kind: str
    ancestor: str | None
    members: dict[str, tuple[Member, ...]] = field(default_factory=dict)


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", version))


def load_delphi_symbols(version: str | None = None) -> dict | None:
    """Read the bundled symbol file of a Delphi version, or the highest one.

    Returns None when the data directory is missing or holds no matching file.
    """
    if not _DELPHI_DATA_DIR.is_dir():
        return None
    found: dict[str, Path] = {}
    for p in _DELPHI_DATA_DIR.iterdir():
        m = _SYMBOL_FILE_RE.match(p.name)
        if m and p.is_file():
            found[m.group(1)] = p
    if not found:
        return None
    if version is None:
        chosen = found[max(found, key=_version_key)]
    else:
        chosen = next((p for v, p in found.items() if v.lower() == version.lower()), None)
        if chosen is None:
            return None
    with gzip.open(chosen, "rt", encoding="utf-8") as f:
        return json.load(f)


class SymbolTable:
    """Units, types, routines and globals, queried case-insensitively (spec 3.2)."""

    def __init__(self, namespaces: Sequence[str] = ()) -> None:
        self._namespaces = tuple(namespaces)
        self._units: dict[str, UnitSymbols] = {}
        self._types: dict[str, TypeSymbols] = {}
        self._unit_types: dict[str, dict[str, TypeSymbols]] = {}
        self._routines: dict[str, dict[str, list[Routine]]] = {}
        self._nested: dict[str, dict[str, dict[str, list[Routine]]]] = {}
        self._globals: dict[str, dict[str, str]] = {}
        self._proc_types: dict[str, set[str]] = {}
        self._files: dict[str, str] = {}
        # Unit names declared by more than one scanned file -> their private keys.
        self._ambiguous: dict[str, list[str]] = {}
        # Include file -> the units that include it.
        self._include_owners: dict[str, list[str]] = {}

    # ------------------------------------------------------------ building

    @classmethod
    def from_sources(
        cls,
        scanned: Iterable[dict],
        delphi: dict | None,
        namespaces: Sequence[str] = (),
    ) -> SymbolTable:
        """`scanned` are `pascal_unit` blocks that carry a `source_file`."""
        table = cls(namespaces)
        blocks = list(scanned)
        include_blocks: dict[str, dict] = {}
        unit_blocks: list[dict] = []
        for block in blocks:
            source = block.get("source_file") or ""
            if source.lower().endswith(".inc"):
                include_blocks[Path(source).name.casefold()] = block
            elif block.get("unit"):
                # A block without a unit (or program) name has nothing to register.
                unit_blocks.append(block)
        counts: dict[str, int] = {}
        for block in unit_blocks:
            key = block["unit"].casefold()
            counts[key] = counts.get(key, 0) + 1
        table._ambiguous = {key: [] for key, n in counts.items() if n > 1}
        keys: list[str] = []
        for i, block in enumerate(unit_blocks):
            name = block["unit"]
            if name.casefold() in table._ambiguous:
                # Two scanned files declare this unit. Each keeps its own block
                # under a private key, so lookups in the caller's own unit use
                # the caller's file; by name the unit is ambiguous.
                table._ambiguous[name.casefold()].append(f"{name}#{i}")
                name = f"{name}#{i}"
            keys.append(name.casefold())
            table._add_unit(name, block, scanned=True)
        units = (delphi or {}).get("units") or {}
        for name, block in units.items():
            key = name.casefold()
            if key not in table._units and key not in table._ambiguous:
                table._add_unit(name, block, scanned=False)
        table._apply_includes(list(zip(keys, unit_blocks)), include_blocks)
        return table

    def _add_unit(self, name: str, block: dict, *, scanned: bool) -> None:
        key = name.casefold()
        source = block.get("source_file")
        self._units[key] = UnitSymbols(
            name=name,
            interface_uses=tuple(block.get("interface_uses") or ()),
            implementation_uses=tuple(block.get("implementation_uses") or ()),
            scanned=scanned,
            module_nid=block.get("module_nid") if scanned else None,
            source_file=source,
        )
        if source:
            self._files[_norm_path(source)] = name
        self._proc_types[key] = {p.casefold() for p in block.get("proc_types") or ()}
        self._globals[key] = {g.casefold(): t for g, t in (block.get("globals") or {}).items()}
        self._routines[key] = {
            rname.casefold(): [self._routine(name, rname, e, scanned) for e in entries]
            for rname, entries in (block.get("routines") or {}).items()
        }
        self._nested[key] = {
            enclosing.casefold(): {
                nname.casefold(): [
                    Routine(nname, name, False, e.get("params"), e.get("min_params"), e.get("nid"))
                    for e in entries
                ]
                for nname, entries in children.items()
            }
            for enclosing, children in (block.get("nested") or {}).items()
        }
        by_name: dict[str, TypeSymbols] = {}
        for tname, tdef in (block.get("types") or {}).items():
            qualified = f"{name}.{tname}"
            members = {
                mname.casefold(): tuple(self._member(qualified, name, mname, e, not scanned) for e in entries)
                for mname, entries in (tdef.get("members") or {}).items()
            }
            symbols = TypeSymbols(
                qualified, name, tdef.get("kind") or "class", tdef.get("ancestor"), members,
            )
            by_name[tname.casefold()] = symbols
            self._types[qualified.casefold()] = symbols
        self._unit_types[key] = by_name

    @staticmethod
    def _routine(unit: str, name: str, entry: dict, scanned: bool) -> Routine:
        params = entry.get("params")
        return Routine(
            name, unit, bool(entry.get("interface", True)), params,
            entry.get("min_params", params), entry.get("nid"), not scanned,
        )

    @staticmethod
    def _member(
        owner: str, unit: str, name: str, entry: dict, external: bool = False,
    ) -> Member:
        params = entry.get("params")
        return Member(
            name=name,
            kind=entry.get("kind") or "method",
            vis=entry.get("vis") or "public",
            params=params,
            min_params=entry.get("min_params", params),
            type=entry.get("type"),
            proc_type=bool(entry.get("proc_type", False)),
            nid=entry.get("nid"),
            owner=owner,
            unit=unit,
            external=external,
        )

    def _apply_includes(
        self, unit_blocks: list[tuple[str, dict]], include_blocks: dict[str, dict],
    ) -> None:
        """A unit takes the routines of its include blocks instead of its own copy."""
        for key, block in unit_blocks:
            for include in block.get("includes") or ():
                inc = include_blocks.get(Path(include).name.casefold())
                if inc is None and not Path(include).suffix:
                    inc = include_blocks.get((Path(include).name + ".inc").casefold())
                if inc is None:
                    continue
                self._replace_with_include(key, inc)
                owners = self._include_owners.setdefault(
                    _norm_path(inc.get("source_file") or ""), [],
                )
                if self._units[key].name not in owners:
                    owners.append(self._units[key].name)

    def _replace_with_include(self, unit_key: str, inc: dict) -> None:
        unit = self._units[unit_key]
        routines = self._routines[unit_key]
        for rname, entries in (inc.get("routines") or {}).items():
            existing = routines.get(rname.casefold(), [])
            replaced = {e.get("params") for e in entries}
            flags = {r.params: r.interface for r in existing if r.params in replaced}
            kept = [r for r in existing if r.params not in replaced]
            for entry in entries:
                routine = self._routine(unit.name, rname, entry, True)
                if routine.params in flags:
                    routine = Routine(
                        routine.name, routine.unit, flags[routine.params],
                        routine.params, routine.min_params, routine.nid,
                    )
                kept.append(routine)
            routines[rname.casefold()] = kept

    # ------------------------------------------------------------- queries

    def resolve_unit_name(self, name: str) -> str | None:
        """Exact (case-insensitive) first, then `<namespace>.<name>` in order.

        A unit name that more than one scanned file declares resolves to None;
        `duplicates` returns the private names of its blocks.
        """
        for candidate in self._name_candidates(name):
            if candidate in self._ambiguous:
                return None
            found = self._units.get(candidate)
            if found is not None:
                return found.name
        return None

    def duplicates(self, name: str) -> list[str]:
        """Private unit names of every scanned file declaring `name`, when more
        than one does (the name is then ambiguous); otherwise empty."""
        for candidate in self._name_candidates(name):
            if candidate in self._ambiguous:
                return list(self._ambiguous[candidate])
            if candidate in self._units:
                return []
        return []

    def _name_candidates(self, name: str) -> list[str]:
        return [name.casefold(), *(f"{ns}.{name}".casefold() for ns in self._namespaces)]

    def unit(self, name: str) -> UnitSymbols | None:
        resolved = self.resolve_unit_name(name)
        return self._units[resolved.casefold()] if resolved else None

    def external_target(self, caller_unit: str, unit: str) -> str:
        """Node id of the stub of the symbol-file unit `unit`, seen from `caller_unit`.

        It is the target of the caller's `imports` edge for the uses entry that
        resolves to `unit`, spelled as written. `System` and `SysInit` are
        implicit and need no uses entry. Returns "" when the caller does not
        reach the unit, so no edge is made.
        """
        from .extractors.resolution import _pascal_resolve_unit
        from .ids import make_id

        own = self.unit(caller_unit)
        if own is not None and own.source_file:
            for used in (*own.implementation_uses, *own.interface_uses):
                if (self.resolve_unit_name(used) or "").casefold() == unit.casefold():
                    return _pascal_resolve_unit(Path(own.source_file), used)
        if unit.casefold() in ("system", "sysinit"):
            return make_id(unit)
        return ""

    def unit_of_file(self, source_file: str) -> str | None:
        """Unit of a scanned file. An include file belongs to the unit that
        includes it; when several units include it, it has no unit."""
        key = _norm_path(source_file)
        found = self._files.get(key)
        if found is not None:
            return found
        owners = self._include_owners.get(key, [])
        return owners[0] if len(owners) == 1 else None

    def type(self, qualified: str) -> TypeSymbols | None:
        return self._types.get(qualified.casefold())

    def lookup_type(self, unit: str, name: str) -> str | None:
        """Qualify a type name seen from `unit`: own unit, then its uses, then System."""
        if "." in name:
            prefix, _, short = name.rpartition(".")
            target = self.resolve_unit_name(prefix)
            if target is not None:
                found = self._unit_types[target.casefold()].get(short.casefold())
                return found.qualified if found else None
        for candidates in self._search_order(unit):
            hits = [
                found for c in candidates
                if (found := self._unit_types.get(c.casefold(), {}).get(name.casefold()))
            ]
            if hits:
                # A duplicated unit that declares the name leaves it ambiguous.
                return hits[0].qualified if len(candidates) == 1 else None
        return None

    def _search_order(self, unit: str) -> list[list[str]]:
        """Own unit, implementation uses then interface uses (last first), then System.

        Each entry lists the units behind one name: a single unit, or every
        block of a unit name that several scanned files declare.
        """
        implicit = self.resolve_unit_name(_IMPLICIT_UNIT)
        own = self.unit(unit)
        if own is None:
            return [[implicit]] if implicit else []
        order = [[own.name]]
        for used in (*reversed(own.implementation_uses), *reversed(own.interface_uses)):
            resolved = self.resolve_unit_name(used)
            if resolved is not None:
                order.append([resolved])
            elif dups := self.duplicates(used):
                order.append(dups)
        if implicit is not None:
            order.append([implicit])
        return order

    def ancestors(self, qualified_type: str) -> list[str]:
        """Ancestors of a type, nearest first."""
        chain: list[str] = []
        seen = {qualified_type.casefold()}
        current = self.type(qualified_type)
        while current is not None and current.ancestor:
            parent = self.lookup_type(current.unit, current.ancestor)
            if parent is None or parent.casefold() in seen:
                break
            seen.add(parent.casefold())
            chain.append(parent)
            current = self.type(parent)
        return chain

    def members(self, qualified_type: str, name: str) -> list[Member]:
        """Members of exactly this type; walking ancestors is up to the caller."""
        symbols = self.type(qualified_type)
        if symbols is None:
            return []
        return list(symbols.members.get(name.casefold(), ()))

    def routines(self, unit: str, name: str, *, interface_only: bool) -> list[Routine]:
        resolved = self.resolve_unit_name(unit)
        if resolved is None:
            return []
        found = self._routines[resolved.casefold()].get(name.casefold(), [])
        return [r for r in found if r.interface or not interface_only]

    def nested(self, unit: str, enclosing: str, name: str) -> list[Routine]:
        resolved = self.resolve_unit_name(unit)
        if resolved is None:
            return []
        children = self._nested[resolved.casefold()].get(enclosing.casefold(), {})
        return list(children.get(name.casefold(), ()))

    def global_var(self, unit: str, name: str) -> str | None:
        """Type of a global variable of `unit`, qualified when the type is known."""
        resolved = self.resolve_unit_name(unit)
        if resolved is None:
            return None
        raw = self._globals[resolved.casefold()].get(name.casefold())
        if raw is None:
            return None
        return self.lookup_type(resolved, raw) or raw

    def is_type(self, unit: str, name: str) -> bool:
        return self.lookup_type(unit, name) is not None

    def declares_type(self, unit: str, name: str) -> bool:
        """Whether `unit` itself declares a type (or procedure type) `name`."""
        resolved = self.resolve_unit_name(unit)
        units = [resolved] if resolved is not None else self.duplicates(unit)
        return any(
            name.casefold() in self._unit_types.get(u.casefold(), {})
            or name.casefold() in self._proc_types.get(u.casefold(), ())
            for u in units
        )

    def caller_class(self, unit: str, enclosing: Sequence[str]) -> str | None:
        """Qualified class of the outermost enclosing routine, if it is a method."""
        if not enclosing:
            return None
        outer = min(reversed(enclosing), key=lambda n: n.count("."))
        parts = outer.split(".")
        # `TFoo.Bar`: the type is everything before the method name
        for end in range(len(parts) - 1, 0, -1):
            found = self.lookup_type(unit, ".".join(parts[:end]))
            if found is not None:
                return found
        return None

    def is_proc_typed(self, qualified_type: str, name: str) -> bool:
        """Whether a field or property is of a procedure or method type.

        A block's `proc_type` flag only knows the procedure types of its own unit,
        so the member's type is also resolved from the owner's unit and checked
        against the `proc_types` of the unit that declares it.
        """
        for owner in (qualified_type, *self.ancestors(qualified_type)):
            found = self.members(owner, name)
            if not found:
                continue
            return any(self._member_is_proc_typed(m) for m in found)
        return False

    def _member_is_proc_typed(self, member: Member) -> bool:
        if member.kind not in ("field", "property"):
            return False
        if member.proc_type:
            return True
        if not member.type:
            return False
        return self._is_proc_type_name(member.unit, member.type)

    def _is_proc_type_name(self, unit: str, type_name: str) -> bool:
        """Whether `type_name` seen from `unit` is a procedure type of some unit.

        Procedure types are not entries of `types`, so the same search order as
        `lookup_type` is walked over the `proc_types` sets.
        """
        if "." in type_name:
            prefix, _, short = type_name.rpartition(".")
            target = self.resolve_unit_name(prefix)
            return target is not None and short.casefold() in self._proc_types[target.casefold()]
        for candidates in self._search_order(unit):
            if any(type_name.casefold() in self._proc_types.get(c.casefold(), ())
                   for c in candidates):
                # Ambiguous through a duplicated unit: not known to be one.
                return len(candidates) == 1
        return False
