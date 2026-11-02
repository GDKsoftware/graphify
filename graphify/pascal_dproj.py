"""Reads the parts of a Delphi `.dproj` that Pascal call resolution needs.

The default namespaces (`DCC_Namespace`) complete a unit name from a `uses`
clause (`Dialogs` -> `Vcl.Dialogs`). The unit search path
(`DCC_UnitSearchPath`) is used only to find `{$I}` include files; it never
changes which units a `uses` clause imports.
"""
from __future__ import annotations

import logging
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .rcfile import get_pascal_settings

_LOG = logging.getLogger(__name__)

# `ProjectVersion` of a .dproj -> Delphi version. Only values checked against a
# project file made by that Delphi version belong here. Empty on purpose: no
# such file could be verified, so the newest bundled symbol file is used.
_PROJECT_VERSION_TO_DELPHI: dict[str, str] = {}


class DprojError(Exception):
    """The `.dproj` cannot be read or parsed."""


@dataclass(frozen=True)
class DprojInfo:
    namespaces: tuple[str, ...]
    search_path: tuple[Path, ...]
    project_version: str | None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _split(value: str) -> list[str]:
    entries = (part.strip() for part in value.split(";"))
    return [e for e in entries if e and "$(" not in e]


def _dedupe(items: list[str], *, fold: bool) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold() if fold else item
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def read_dproj(path: Path) -> DprojInfo:
    """Namespaces and unit search path of a `.dproj`; raises `DprojError`."""
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise DprojError(f"cannot read {path}: {exc}") from exc

    namespaces: list[str] = []
    search: list[str] = []
    version: str | None = None
    for element in root.iter():
        name = _local_name(element.tag)
        text = element.text or ""
        if name == "DCC_Namespace":
            namespaces.extend(_split(text))
        elif name == "DCC_UnitSearchPath":
            search.extend(_split(text.replace("\\", "/")))
        elif name == "ProjectVersion" and version is None and text.strip():
            version = text.strip()

    base = path.parent
    return DprojInfo(
        namespaces=tuple(_dedupe(namespaces, fold=True)),
        search_path=tuple(base / entry for entry in _dedupe(search, fold=False)),
        project_version=version,
    )


def delphi_version_for(project_version: str | None) -> str | None:
    if project_version is None:
        return None
    return _PROJECT_VERSION_TO_DELPHI.get(project_version)


_cache: dict[tuple[str, int], DprojInfo | None] = {}
_cache_lock = threading.Lock()


def active_dproj() -> DprojInfo | None:
    """The `.dproj` named by the active `pascal.dproj` key, or None.

    Silent without the key. An unreadable file logs one warning per file
    version and acts as if the key was not set.
    """
    settings = get_pascal_settings()
    if not settings.dproj:
        return None
    path = Path(settings.dproj)
    if not path.is_absolute() and settings.root is not None:
        path = settings.root / path
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = -1
    key = (str(path), stamp)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        info: DprojInfo | None = read_dproj(path)
    except DprojError as exc:
        _LOG.warning("ignoring pascal.dproj: %s", exc)
        info = None
    with _cache_lock:
        _cache[key] = info
    return info
