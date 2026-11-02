"""One stub node per external Delphi unit, however the uses entry spells it.

`uses Dialogs` (completed by the namespaces to `Vcl.Dialogs`) and
`uses Vcl.Dialogs` name the same unit from the Delphi symbol file. Both the
`imports` edges and the resolver's `calls` edges go to one node,
`make_id("Vcl.Dialogs")`, labelled with the full unit name. A unit that is in
neither the scan nor the symbol file keeps its own stub, and a scanned unit
is never redirected to a stub. Every test runs on both extractors.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from graphify.extract import extract

FIXTURES = Path(__file__).parent / "fixtures" / "delphi_stubs"
KORT, LANG, GRAFIEK = FIXTURES / "Kort.pas", FIXTURES / "Lang.pas", FIXTURES / "Vcl.Grafiek.pas"

SYMBOLS = {
    "units": {
        "System": {"routines": {}},
        "Vcl.Dialogs": {"interface_uses": ["System"], "routines": {"ShowMessage": [{"params": 1}]}},
        # Also scanned (Vcl.Grafiek.pas): the scanned unit wins and is never a stub.
        "Vcl.Grafiek": {"routines": {"Teken": [{"params": 0}]}},
    },
}


@pytest.fixture(params=["tree-sitter", "regex"])
def graph(request, monkeypatch, tmp_path):
    from graphify import pascal_resolution

    if request.param == "regex":
        monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: SYMBOLS)
    monkeypatch.setattr(pascal_resolution, "_namespaces", lambda *a, **k: ["System", "Vcl"])
    return extract([KORT, LANG, GRAFIEK], cache_root=tmp_path / "cache", parallel=False)


def _module(graph: dict, file: Path) -> str:
    return next(
        n["id"] for n in graph["nodes"]
        if Path(str(n.get("source_file", ""))).name == file.name
        and n.get("label") == file.stem
    )


def _routine(graph: dict, file: Path, label: str) -> str:
    return next(
        n["id"] for n in graph["nodes"]
        if Path(str(n.get("source_file", ""))).name == file.name and n.get("label") == label
    )


def _targets(graph: dict, source: str, relation: str) -> list[str]:
    return [e["target"] for e in graph["edges"] if e["source"] == source and e["relation"] == relation]


def test_both_spellings_import_one_stub(graph):
    for file in (KORT, LANG):
        assert _targets(graph, _module(graph, file), "imports").count("vcl_dialogs") == 1, file


def test_both_spellings_call_the_same_stub(graph):
    for file in (KORT, LANG):
        assert "vcl_dialogs" in _targets(graph, _routine(graph, file, "Doe()"), "calls"), file


def test_the_stub_carries_the_full_unit_name_and_no_short_stub_is_left(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    stub = nodes["vcl_dialogs"]
    assert stub["label"] == "Vcl.Dialogs" and stub["pascal_external"] is True
    assert "dialogs" not in nodes
    assert all(e["target"] != "dialogs" for e in graph["edges"])
    labels = [n["label"] for n in graph["nodes"] if n.get("pascal_external")]
    assert len(labels) == len(set(labels))


def test_unit_outside_scan_and_symbol_file_keeps_its_own_stub(graph):
    assert "onbekend" in _targets(graph, _module(graph, KORT), "imports")


def test_scanned_unit_is_never_redirected_to_a_stub(graph):
    teken = _routine(graph, GRAFIEK, "Teken()")
    for file in (KORT, LANG):
        assert teken in _targets(graph, _routine(graph, file, "Doe()"), "calls"), file
    assert all(e["target"] != "vcl_grafiek" for e in graph["edges"])
    lang_imports = _targets(graph, _module(graph, LANG), "imports")
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert any(nodes.get(t, {}).get("source_file") for t in lang_imports)


def test_incremental_build_keeps_one_stub(graph, tmp_path):
    # Re-extract Kort.pas against the graph of the full build: its edges go to
    # the stub the unchanged Lang.pas already points at, and no short stub returns.
    kort_file = KORT.name
    context_nodes = [
        n for n in graph["nodes"] if Path(str(n.get("source_file", ""))).name != kort_file
    ]
    context_edges = [
        e for e in graph["edges"] if Path(str(e.get("source_file", ""))).name != kort_file
    ]
    g = extract(
        [KORT], cache_root=tmp_path / "cache2", parallel=False,
        resolution_context_nodes=context_nodes, resolution_context_edges=context_edges,
    )
    module = _module(g, KORT)
    assert _targets(g, module, "imports").count("vcl_dialogs") == 1
    assert "vcl_dialogs" in _targets(g, _routine(g, KORT, "Doe()"), "calls")
    ids = {n["id"] for n in g["nodes"]}
    assert "dialogs" not in ids
    assert all(e["target"] != "dialogs" for e in g["edges"])


@pytest.mark.parametrize("regex", [False, True])
def test_both_spellings_in_one_unit_give_one_imports_edge(regex, monkeypatch, tmp_path):
    from graphify import pascal_resolution

    if regex:
        monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: SYMBOLS)
    monkeypatch.setattr(pascal_resolution, "_namespaces", lambda *a, **k: ["System", "Vcl"])
    beide = FIXTURES / "Beide.pas"
    g = extract([beide], cache_root=tmp_path / "cache", parallel=False)
    assert _targets(g, _module(g, beide), "imports").count("vcl_dialogs") == 1
