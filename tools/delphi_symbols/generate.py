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
import sys
from pathlib import Path

from graphify.extractors.base import _file_stem, _read_source_text
from graphify.extractors.pascal_unit import build_pascal_unit

_LOG = logging.getLogger(__name__)

GENERATOR_VERSION = "1"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "graphify" / "data" / "delphi"
_VISIBLE = ("protected", "strict protected", "public", "published")
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
        tdef["members"] = {
            name: kept
            for name, entries in tdef.get("members", {}).items()
            if (kept := [dict(e) for e in entries if e.get("vis") in _VISIBLE])
        }
    for entries in block.get("routines", {}).values():
        for e in entries:
            e.pop("nid", None)
    for tdef in block.get("types", {}).values():
        for entries in tdef["members"].values():
            for e in entries:
                e.pop("nid", None)
    return block


def generate(source_root: Path, version: str, generated_at: str | None = None) -> dict:
    """The symbol file content for the units under `source_root`."""
    source_root = Path(source_root)
    include_dirs = _include_dirs(source_root)
    files = sorted(
        (p for p in source_root.rglob("*") if p.suffix.lower() == ".pas" and p.is_file()),
        key=lambda p: p.relative_to(source_root).as_posix().casefold(),
    )
    units: dict[str, dict] = {}
    chosen: dict[str, Path] = {}
    for path in files:
        try:
            raw = _read_source_text(path, warn=False)
            block = build_pascal_unit(
                path, raw, stem=_file_stem(path), interface_only=True, include_dirs=include_dirs,
            )
        except (OSError, UnicodeDecodeError) as exc:
            _LOG.warning("skipping %s: %s", path, exc)
            continue
        name = block.get("unit")
        if not name or not _has_unit_header(raw):
            continue
        key = name.casefold()
        if key in chosen:
            _LOG.warning("duplicate unit %s: keeping %s, ignoring %s", name, chosen[key], path)
            continue
        units[name] = _strip(block)
        chosen[key] = path
    return {
        "delphi_version": version,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated_at or datetime.date.today().isoformat(),
        "units": units,
    }


def _has_unit_header(raw: str) -> bool:
    from graphify.extractors import pascal as _pas

    stripped = _pas._pascal_mask_strings(_pas._pascal_strip_comments(raw))
    m = _pas._PAS_MODULE_RE.search(stripped)
    return bool(m) and m.group(1).lower() == "unit"


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
