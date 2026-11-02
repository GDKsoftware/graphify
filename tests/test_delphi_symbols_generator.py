"""The Delphi symbol file generator (tools/delphi_symbols)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from graphify.pascal_symbols import SymbolTable, load_delphi_symbols
from graphify.rcfile import PascalSettings, set_pascal_settings
from tools.delphi_symbols.generate import generate, write

SRC = Path(__file__).parent / "fixtures" / "delphi_symbols_src"
ROOT = Path(__file__).resolve().parent.parent


def _gen() -> dict:
    return generate(SRC, "13.2", generated_at="2026-01-01")


def test_generator_keeps_interface_only() -> None:
    symbols = _gen()
    assert symbols["delphi_version"] == "13.2"
    assert symbols["generator_version"] == "1"
    assert symbols["generated_at"] == "2026-01-01"
    system = symbols["units"]["System"]
    assert "WriteLine" in system["routines"]
    assert "Internal" not in system["routines"]
    members = system["types"]["TObject"]["members"]
    assert "FHidden" not in members
    assert "Create" in members
    dialogs = symbols["units"]["Vcl.Dialogs"]
    assert dialogs["interface_uses"] == ["System"]
    assert dialogs["implementation_uses"] == []
    assert "DoShow" in dialogs["types"]["TDialog"]["members"]


def test_generator_strips_per_scan_fields() -> None:
    text = json.dumps(_gen())
    assert '"nid"' not in text
    assert "module_nid" not in text
    assert '"includes"' not in text


def test_generator_output_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.gz", tmp_path / "b.gz"
    write(_gen(), a)
    write(_gen(), b)
    assert a.read_bytes() == b.read_bytes()


def test_generated_file_loads_into_symbol_table(tmp_path: Path) -> None:
    write(_gen(), tmp_path / "delphi-13.2.symbols.json.gz")
    set_pascal_settings(PascalSettings(delphi_symbols=str(tmp_path)))
    try:
        data = load_delphi_symbols("13.2")
    finally:
        set_pascal_settings(PascalSettings())
    assert data is not None
    table = SymbolTable.from_sources([], data)
    assert table.unit("Vcl.Dialogs") is not None
    with gzip.open(tmp_path / "delphi-13.2.symbols.json.gz", "rt", encoding="utf-8") as f:
        assert json.load(f)["units"]["System"]["unit"] == "System"


def test_package_data_includes_symbol_files() -> None:
    assert "data/delphi/*.symbols.json.gz" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
