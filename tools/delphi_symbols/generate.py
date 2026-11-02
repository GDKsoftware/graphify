"""Generate a Delphi symbol file from the RTL/VCL sources.

Build-time only; nothing here ships in the wheel. Usage (from the repo root)::

    python -m tools.delphi_symbols <source_root> --version 13.2 [--out PATH] [--generated-at YYYY-MM-DD]

Each `.pas` file under the source root goes through
`build_pascal_unit(..., interface_only=True)`. The result keeps the interface
section only: declarations, no bodies, no node ids, and only the members a
caller outside the unit can reach.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import logging
import re
import sys
from pathlib import Path

from graphify.extractors.base import _file_stem, _read_source_text
from graphify.extractors.pascal_unit import build_pascal_unit

_LOG = logging.getLogger(__name__)

GENERATOR_VERSION = "1"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "graphify" / "data" / "delphi"
_VISIBLE = ("protected", "strict protected", "public", "published")
# Directories that hold test frameworks, examples and demos, not RTL/VCL units.
_SKIP_DIR_NAMES = frozenset({
    "examples", "example", "demos", "demo", "samples", "sample",
    "test", "tests", "unittests", "contrib",
})
_SKIP_TOP_TREES = frozenset({"dunit", "dunitx"})
_COMMENT_RE = re.compile(r"\{[^}]*\}|\(\*.*?\*\)|//[^\n]*", re.DOTALL)
_HINT = r"(?:platform|library|experimental|deprecated(?:\s*'(?:[^']|'')*')?)"
_UNIT_HEADER_RE = re.compile(
    rf"^\s*unit\s+([A-Za-z_][\w.]*)(?:\s+{_HINT})*\s*;", re.IGNORECASE | re.MULTILINE,
)
# Per-scan fields that mean nothing in a symbol file.
_DROP_UNIT_KEYS = ("module_nid", "includes")


def _include_dirs(source_root: Path) -> list[Path]:
    """Every directory under the root that holds an include file, sorted."""
    dirs = {p.parent for p in source_root.rglob("*") if p.suffix.lower() == ".inc"}
    return sorted(dirs, key=lambda d: d.as_posix().casefold())


def _strip(block: dict) -> dict:
    for key in _DROP_UNIT_KEYS:
        block.pop(key, None)
    for tdef in block.get("types", {}).values():
        members = {}
        for name, entries in tdef.get("members", {}).items():
            kept = [
                {k: v for k, v in e.items() if k != "nid"}
                for e in entries
                if e.get("vis") in _VISIBLE
            ]
            if kept:
                members[name] = kept
        tdef["members"] = members
    for entries in block.get("routines", {}).values():
        for e in entries:
            e.pop("nid", None)
    return block


class _MissingIncludeCounter(logging.Filter):
    """Counts the builder's "include file not found" warnings and keeps them out of the log."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if "include file not found" not in record.getMessage():
            return True
        self.count += 1
        _LOG.debug("%s", record.getMessage())
        return False


def _skipped(rel: Path) -> bool:
    parts = [p.casefold() for p in rel.parts]
    if parts[0] in _SKIP_TOP_TREES:
        return True
    return any(p in _SKIP_DIR_NAMES for p in parts[:-1])


def _unit_name(raw: str) -> str | None:
    """The name in the `unit` header of a source, or None for a program, library or package."""
    m = _UNIT_HEADER_RE.search(_COMMENT_RE.sub(" ", raw))
    return m.group(1) if m else None


def generate(source_root: Path, version: str, generated_at: str | None = None) -> dict:
    """The symbol file content for the units under `source_root`.

    Files under test, example and demo directories are skipped (`_SKIP_DIR_NAMES`,
    `_SKIP_TOP_TREES`). Of a unit name declared twice, the shallowest file wins,
    then the first by casefolded relative path; a warning is logged when the two
    declarations differ.
    """
    source_root = Path(source_root)
    include_dirs = _include_dirs(source_root)
    candidates = []
    for p in source_root.rglob("*"):
        if p.suffix.lower() != ".pas" or not p.is_file():
            continue
        rel = p.relative_to(source_root)
        if not _skipped(rel):
            candidates.append((len(rel.parts), rel.as_posix().casefold(), p))
    candidates.sort(key=lambda c: (c[0], c[1]))
    counter = _MissingIncludeCounter()
    builder_log = logging.getLogger("graphify.extractors.pascal_unit")
    builder_log.addFilter(counter)
    units: dict[str, dict] = {}
    chosen: dict[str, tuple[str, Path]] = {}
    try:
        for _depth, _rel, path in candidates:
            try:
                raw = _read_source_text(path, warn=False)
            except (OSError, UnicodeDecodeError) as exc:
                _LOG.warning("skipping %s: %s", path, exc)
                continue
            name = _unit_name(raw)
            if name is None:
                continue
            block = _strip(build_pascal_unit(
                path, raw, stem=_file_stem(path), interface_only=True, include_dirs=include_dirs,
            ))
            key = name.casefold()
            if key in chosen:
                if units[chosen[key][0]] != block:
                    _LOG.warning(
                        "duplicate unit %s differs: keeping %s, ignoring %s",
                        name, chosen[key][1], path,
                    )
                continue
            units[name] = block
            chosen[key] = (name, path)
    finally:
        builder_log.removeFilter(counter)
    if counter.count:
        _LOG.warning("%d include files were not found and skipped", counter.count)
    return {
        "delphi_version": version,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated_at or datetime.date.today().isoformat(),
        "units": units,
    }


def write(symbols: dict, out_path: Path) -> None:
    """Write the symbol file: sorted compact JSON in a gzip stream without a timestamp."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(symbols, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(out_path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.delphi_symbols", description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--version", required=True, help="Delphi version, e.g. 13.2")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--generated-at", default=None, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.source_root.is_dir():
        print(f"not a directory: {args.source_root}", file=sys.stderr)
        return 2
    out = args.out or _DATA_DIR / f"delphi-{args.version}.symbols.json.gz"
    symbols = generate(args.source_root, args.version, args.generated_at)
    write(symbols, out)
    print(f"{len(symbols['units'])} units -> {out}")
    return 0
