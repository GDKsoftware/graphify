"""The symbol table over scanned Pascal units and bundled Delphi symbols (spec 3.2)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from graphify import pascal_symbols
from graphify.extractors.base import _file_stem as file_stem
from graphify.extractors.pascal_unit import build_pascal_unit
from graphify.pascal_symbols import SymbolTable, load_delphi_symbols

FIXTURES = Path(__file__).parent / "fixtures"
DECLS = FIXTURES / "pascal_unit" / "Decls.pas"
SHARED_INC = FIXTURES / "pascal_unit" / "shared.inc"
SYMBOLS = FIXTURES / "delphi_scope" / "delphi-test.symbols.json"
NAMESPACES = ["System", "Vcl"]


def _block(path: Path, **kwargs) -> dict:
    raw = path.read_text(encoding="utf-8")
    block = build_pascal_unit(path, raw, stem=file_stem(path), **kwargs)
    return {**block, "source_file": str(path)}


def _delphi() -> dict:
    return json.loads(SYMBOLS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def table() -> SymbolTable:
    return SymbolTable.from_sources([_block(DECLS)], _delphi(), NAMESPACES)


def test_resolve_unit_name_uses_namespaces(table):
    assert table.resolve_unit_name("Dialogs") == "Vcl.Dialogs"
    assert table.resolve_unit_name("vcl.dialogs") == "Vcl.Dialogs"
    assert table.resolve_unit_name("decls") == "Decls"
    assert table.resolve_unit_name("Nope") is None
    assert table.unit("Forms").name == "Vcl.Forms"
    assert table.unit("Forms").scanned is False
    assert table.unit("Decls").scanned is True


def test_scanned_unit_wins_over_symbol_file():
    scanned = {
        "unit": "Vcl.Dialogs", "module_nid": "m", "interface_uses": [],
        "implementation_uses": [], "includes": [], "types": {}, "nested": {}, "globals": {},
        "proc_types": [], "source_file": "C:/p/Dialogs.pas",
        "routines": {"Own": [{"interface": True, "params": 0, "min_params": 0, "nid": "own"}]},
    }
    t = SymbolTable.from_sources([scanned], _delphi(), NAMESPACES)
    assert t.unit("Vcl.Dialogs").scanned is True
    assert [r.nid for r in t.routines("Vcl.Dialogs", "own", interface_only=True)] == ["own"]
    assert t.routines("Vcl.Dialogs", "ShowMessage", interface_only=True) == []


def test_ancestors_cross_into_symbol_file(table):
    chain = table.ancestors("Decls.TfrmBasis")
    assert chain[:2] == ["Vcl.Forms.TForm", "Vcl.Forms.TCustomForm"]
    assert chain[-1] == "System.TObject"
    assert table.type("decls.tfrmbasis").unit == "Decls"
    assert table.type("Vcl.Forms.TForm").ancestor == "TCustomForm"


def test_members_keep_nid_and_owner(table):
    verander = table.members("Decls.TfrmBasis", "verander")
    assert [m.params for m in verander] == [0, 2]
    assert all(m.owner == "Decls.TfrmBasis" and m.unit == "Decls" for m in verander)
    assert verander[0].nid is not None
    close = table.members("Vcl.Forms.TCustomForm", "Close")[0]
    assert close.nid is None and close.vis == "public" and close.unit == "Vcl.Forms"
    assert close.min_params == 0
    assert table.members("Decls.TfrmBasis", "Close") == []


def test_routines_interface_only_filter(table):
    assert len(table.routines("Decls", "maandnaam", interface_only=True)) == 1
    assert table.routines("Decls", "Intern", interface_only=True) == []
    assert len(table.routines("Decls", "Intern", interface_only=False)) == 1
    shown = table.routines("Dialogs", "ShowMessage", interface_only=True)
    assert [(r.unit, r.params, r.nid) for r in shown] == [("Vcl.Dialogs", 1, None)]


def test_global_var_is_qualified(table):
    assert table.global_var("Decls", "FrmBasis") == "Decls.TfrmBasis"
    assert table.global_var("Vcl.Forms", "Application") == "Vcl.Forms.TApplication"
    assert table.global_var("Decls", "Nope") is None


def test_is_type(table):
    assert table.is_type("Decls", "TButton")
    assert table.is_type("Decls", "tfrmbasis")
    assert table.is_type("Decls", "TObject")
    assert not table.is_type("Decls", "Maandnaam")
    assert not table.is_type("Decls", "ShowMessage")


def test_lookup_type_order(table):
    assert table.lookup_type("Decls", "TForm") == "Vcl.Forms.TForm"
    assert table.lookup_type("Decls", "Vcl.Forms.TForm") == "Vcl.Forms.TForm"
    assert table.lookup_type("Decls", "Forms.TForm") == "Vcl.Forms.TForm"
    assert table.lookup_type("Decls", "TNope") is None


def test_caller_class(table):
    assert table.caller_class("Decls", ["TfrmBasis.Verander"]) == "Decls.TfrmBasis"
    assert table.caller_class(
        "Decls", ["TfrmBasis.Verander.Stap", "TfrmBasis.Verander"]) == "Decls.TfrmBasis"
    assert table.caller_class("Decls", ["Maandnaam"]) is None
    assert table.caller_class("Decls", ["TNope.Foo"]) is None
    assert table.caller_class("Decls", []) is None


def test_nested_and_unit_of_file(table):
    assert [r.params for r in table.nested("Decls", "TfrmBasis.Verander", "stap")] == [0]
    assert table.nested("Decls", "TfrmBasis.Verander", "Nope") == []
    assert table.unit_of_file(str(DECLS)) == "Decls"
    assert table.unit_of_file(str(DECLS).replace("\\", "/").upper()) == "Decls"
    assert table.unit_of_file("C:/elsewhere/X.pas") is None


def test_none_nid_symbols_are_kept():
    block = _block(DECLS, interface_only=True)
    assert block["module_nid"] is None
    t = SymbolTable.from_sources([block], None, ())
    assert t.unit("Decls").module_nid is None
    assert [r.nid for r in t.routines("Decls", "Maandnaam", interface_only=True)] == [None]
    assert t.members("Decls.TfrmBasis", "Verander")[0].nid is None


def test_include_routines_come_from_the_include_block():
    unit = _block(DECLS)
    inc = _block(SHARED_INC)
    inc["routines"] = {k: [{**e, "nid": "from_include"} for e in v]
                       for k, v in inc["routines"].items()}
    assert unit["routines"]["Gedeeld"][0]["nid"] != "from_include"
    t = SymbolTable.from_sources([unit, inc], None, ())
    gedeeld = t.routines("Decls", "Gedeeld", interface_only=False)
    assert [r.nid for r in gedeeld] == ["from_include"]
    assert t.unit("shared") is None  # an include is not a unit


def test_is_proc_typed_across_units():
    # TNotifyEvent lives in System.Classes: the Decls block does not know it.
    block = _block(DECLS)
    assert block["types"]["TfrmBasis"]["members"]["FOnChange"][0]["proc_type"] is False
    delphi = _delphi()
    delphi["units"]["System.Classes"] = {
        "interface_uses": ["System"],
        "types": {},
        "proc_types": ["TNotifyEvent"],
    }
    t = SymbolTable.from_sources([block], delphi, NAMESPACES)
    assert t.is_proc_typed("Decls.TfrmBasis", "FOnChange")
    assert not t.is_proc_typed("Decls.TfrmBasis", "Button1")
    assert not t.is_proc_typed("Decls.TfrmBasis", "Verander")
    # without the symbol-file unit the type stays unknown
    plain = SymbolTable.from_sources([block], _delphi(), NAMESPACES)
    assert not plain.is_proc_typed("Decls.TfrmBasis", "FOnChange")


def test_load_delphi_symbols_without_bundled_files_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pascal_symbols, "_DELPHI_DATA_DIR", tmp_path / "missing")
    assert load_delphi_symbols() is None
    monkeypatch.setattr(pascal_symbols, "_DELPHI_DATA_DIR", tmp_path)
    assert load_delphi_symbols() is None
    assert load_delphi_symbols("13.2") is None


def test_load_delphi_symbols_picks_highest_version(tmp_path, monkeypatch):
    monkeypatch.setattr(pascal_symbols, "_DELPHI_DATA_DIR", tmp_path)
    for version in ("12.3", "13.2", "13.10"):
        data = {"delphi_version": version, "units": {}}
        with gzip.open(tmp_path / f"delphi-{version}.symbols.json.gz", "wt", encoding="utf-8") as f:
            json.dump(data, f)
    assert load_delphi_symbols()["delphi_version"] == "13.10"
    assert load_delphi_symbols("12.3")["delphi_version"] == "12.3"
    assert load_delphi_symbols("11.0") is None


def _unit_block(name: str, source: str, routine: str, nid: str) -> dict:
    return {
        "unit": name, "module_nid": f"m-{nid}", "interface_uses": [],
        "implementation_uses": [], "includes": [], "types": {}, "nested": {},
        "globals": {}, "proc_types": [], "source_file": source,
        "routines": {routine: [{"interface": True, "params": 0, "min_params": 0, "nid": nid}]},
    }


def test_block_without_unit_name_is_not_registered():
    t = SymbolTable.from_sources([_unit_block("", "C:/p/x.pas", "Foo", "foo")], None)
    assert t.unit("") is None
    assert t.unit_of_file("C:/p/x.pas") is None


def test_duplicate_unit_name_keeps_each_file_and_is_ambiguous_by_name():
    first = _unit_block("Dup", "C:/a/Dup.pas", "Foo", "a_foo")
    second = _unit_block("Dup", "C:/b/Dup.pas", "Foo", "b_foo")
    t = SymbolTable.from_sources([first, second], _delphi())
    # Own-unit lookups go through the caller's file and see that file's block.
    own_a = t.unit_of_file("C:/a/Dup.pas")
    own_b = t.unit_of_file(r"c:\b\dup.pas")
    assert own_a and own_b and own_a != own_b
    assert [r.nid for r in t.routines(own_a, "foo", interface_only=False)] == ["a_foo"]
    assert [r.nid for r in t.routines(own_b, "FOO", interface_only=False)] == ["b_foo"]
    # From another unit the name is ambiguous: no unit, no routines.
    assert t.resolve_unit_name("Dup") is None
    assert t.unit("dup") is None
    assert t.routines("Dup", "Foo", interface_only=True) == []


def test_duplicate_unit_in_uses_makes_type_lookups_ambiguous():
    first = {**_unit_block("Dup", "C:/a/Dup.pas", "Foo", "a_foo"),
             "types": {"TThing": {"kind": "class", "ancestor": None, "members": {}}}}
    second = _unit_block("Dup", "C:/b/Dup.pas", "Foo", "b_foo")
    other = {**_unit_block("Other", "C:/o/Other.pas", "Bar", "bar"),
             "types": {"TThing": {"kind": "class", "ancestor": None, "members": {}}}}
    caller = {**_unit_block("Caller", "C:/c/Caller.pas", "Baz", "baz"),
              "interface_uses": ["Other", "Dup"]}
    t = SymbolTable.from_sources([first, second, other, caller], None)
    assert [len(t.duplicates(n)) for n in ("Dup", "dup", "Other")] == [2, 2, 0]
    # `Dup` comes last and one of its files declares TThing: no certain answer.
    assert t.lookup_type("Caller", "TThing") is None
    assert t.declares_type("Dup", "TThing") is True
    assert t.declares_type("Dup", "TNope") is False
