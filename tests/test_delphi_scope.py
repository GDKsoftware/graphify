"""Scope-aware call resolution for Delphi/Pascal (unqualified, Self and
inherited calls). Every test runs on both extractors: tree-sitter and regex."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from graphify.extract import extract

FIXTURES = Path(__file__).parent / "fixtures" / "delphi_scope"


@pytest.fixture(params=["tree-sitter", "regex"])
def extractor(request, monkeypatch):
    if request.param == "regex":
        # `extract_pascal` imports tree_sitter_pascal at call time; a None entry
        # makes that import fail, so it falls back to the regex extractor.
        monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    return request.param


@pytest.fixture
def run(extractor, tmp_path):
    def _run(*names: str, **kwargs) -> dict:
        paths = [FIXTURES / n for n in names]
        return extract(paths, cache_root=tmp_path / "cache", parallel=False, **kwargs)
    return _run


def _node(graph: dict, nid: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == nid)


def _file(node: dict) -> str:
    return Path(str(node.get("source_file", ""))).name


def _routine(graph: dict, file: str, label: str) -> dict:
    found = [n for n in graph["nodes"] if _file(n) == file and n.get("label") == label]
    assert len(found) == 1, (file, label, found)
    return found[0]


def _calls_from(graph: dict, file: str, label: str) -> list[dict]:
    src = _routine(graph, file, label)["id"]
    return [e for e in graph["edges"] if e["relation"] == "calls" and e["source"] == src]


def _call(graph: dict, src: tuple[str, str], dst: tuple[str, str]) -> dict | None:
    target = _routine(graph, *dst)["id"]
    return next((e for e in _calls_from(graph, *src) if e["target"] == target), None)


def _target_file(graph: dict, unit: str, label: str) -> str | None:
    """File of the target of the `calls` edge from `Test()` in `unit` to `label`."""
    for e in _calls_from(graph, f"{unit}.pas", "Test()"):
        target = _node(graph, e["target"])
        if target.get("label") == label:
            return _file(target)
    return None


def test_implementation_routine_only_visible_in_own_unit(run):
    g = run("Helpers.pas", "NietInUses.pas", "UnitA.pas")
    edge = _call(g, ("Helpers.pas", "InitCap()"), ("Helpers.pas", "Intern()"))
    assert edge is not None and edge["confidence"] == "EXTRACTED"
    assert edge["rule"] == "5.1/4"


def test_routine_not_in_uses_gives_no_edge(run):
    g = run("Helpers.pas", "NietInUses.pas", "UnitA.pas")
    assert _calls_from(g, "NietInUses.pas", "Test()") == []


def test_last_unit_in_uses_wins(run):
    g = run("UnitA.pas", "UnitB.pas", "UsesAB.pas", "UsesBA.pas")
    assert _target_file(g, "UsesAB", "Doe()") == "UnitB.pas"
    assert _target_file(g, "UsesBA", "Doe()") == "UnitA.pas"


def test_implementation_uses_win_over_interface_uses(run):
    g = run("UnitA.pas", "UnitB.pas", "IfaceImpl.pas")
    assert _target_file(g, "IfaceImpl", "Doe()") == "UnitB.pas"
    edge = _calls_from(g, "IfaceImpl.pas", "Test()")[0]
    assert edge["rule"] == "5.1/5"


def test_nested_routine_wins_over_unit_routine(run):
    g = run("UnitA.pas", "Genest.pas")
    edges = _calls_from(g, "Genest.pas", "Buiten()")
    assert len(edges) == 1
    target = _node(g, edges[0]["target"])
    assert _file(target) == "Genest.pas" and target["label"] == "Doe()"
    assert edges[0]["rule"] == "5.1/1"


def test_published_ancestor_method_is_found(run):
    g = run("Basis.pas", "FormB.pas", "Andere.pas")
    edge = _call(g, ("FormB.pas", "Doe()"), ("Basis.pas", "Verander()"))
    assert edge is not None and edge["confidence"] == "EXTRACTED"
    assert edge["rule"] == "5.1/3"


def test_own_class_method_wins_over_ancestor(run):
    g = run("Basis.pas", "FormA.pas")
    assert _call(g, ("FormA.pas", "Doe()"), ("FormA.pas", "Verander()")) is not None
    assert _call(g, ("FormA.pas", "Doe()"), ("Basis.pas", "Verander()")) is None


def test_private_ancestor_method_from_other_unit_gives_no_edge(run):
    g = run("Basis.pas", "FormA.pas", "FormB.pas", "Andere.pas")
    assert _call(g, ("FormA.pas", "Doe()"), ("Basis.pas", "Geheim()")) is None
    assert _call(g, ("FormB.pas", "Doe()"), ("Basis.pas", "Geheim()")) is None


def test_private_ancestor_method_in_same_unit_is_found(run):
    g = run("Basis.pas")
    edge = _call(g, ("Basis.pas", "Doe()"), ("Basis.pas", "Geheim()"))
    assert edge is not None and edge["confidence"] == "EXTRACTED"


def test_strict_private_is_never_visible_to_descendant(run):
    g = run("Basis.pas")
    assert _call(g, ("Basis.pas", "Doe()"), ("Basis.pas", "HeelGeheim()")) is None


def test_method_of_unrelated_class_is_never_a_candidate(run):
    g = run("Basis.pas", "FormA.pas", "FormB.pas", "Andere.pas")
    for file in ("FormA.pas", "FormB.pas"):
        for e in _calls_from(g, file, "Doe()"):
            assert _file(_node(g, e["target"])) != "Andere.pas"


def test_inherited_skips_own_class(run):
    g = run("Basis.pas", "FormA.pas")
    edges = _calls_from(g, "FormA.pas", "Verander()")
    assert len(edges) == 1
    target = _node(g, edges[0]["target"])
    assert _file(target) == "Basis.pas" and target["label"] == "Verander()"
    assert edges[0]["rule"] == "5.2/2"


def test_overload_picked_by_arg_count(run):
    g = run("Basis.pas", "FormA.pas")
    edge = _call(g, ("FormA.pas", "Doe()"), ("Basis.pas", "Laad()"))
    assert edge is not None and edge["confidence"] == "EXTRACTED"
    assert edge["confidence_score"] == 1.0


def test_overload_without_unique_arity_is_inferred(run):
    from graphify.pascal_resolution import DelphiResolver, build_symbol_table

    g = run("Basis.pas", "FormA.pas")
    table = build_symbol_table(g["nodes"])
    caller = _routine(g, "FormA.pas", "Doe()")
    laad = _routine(g, "Basis.pas", "Laad()")
    rc = {
        "language": "pascal", "caller_nid": caller["id"], "callee": "Laad",
        "qualifier": None, "is_member_call": False, "arg_count": None,
        "enclosing_routines": ["TfrmA.Doe"], "source_file": caller["source_file"],
        "source_location": "L1",
    }
    res = DelphiResolver(table).resolve(rc)
    assert res is not None
    assert (res.target, res.confidence, res.score) == (laad["id"], "INFERRED", 0.85)
    for count in (0, 1, 2):
        res = DelphiResolver(table).resolve({**rc, "arg_count": count})
        assert res is not None and res.confidence == "EXTRACTED", count


def test_typecast_and_event_call_give_no_edge(run):
    g = run("Casts.pas")
    assert _calls_from(g, "Casts.pas", "Klik()") == []


def test_edges_record_rule(run):
    g = run("UnitA.pas", "UnitB.pas", "UsesAB.pas")
    edges = _calls_from(g, "UsesAB.pas", "Test()")
    assert len(edges) == 1
    edge = edges[0]
    assert edge["resolved_by"] == "delphi_scope" and edge["rule"] == "5.1/6"
    assert edge["context"] == "call" and edge["weight"] == 1.0
    assert edge["confidence"] == "EXTRACTED" and edge["confidence_score"] == 1.0
    assert edge["source_location"] == "L13"


def test_incremental_build_keeps_cross_unit_edge(run):
    full = run("UnitA.pas", "UnitB.pas")
    assert any("_pascal_unit" in n for n in full["nodes"])
    g = run("UsesAB.pas", resolution_context_nodes=full["nodes"])
    edges = _calls_from(g, "UsesAB.pas", "Test()")
    assert len(edges) == 1
    assert edges[0]["target"] == _routine(full, "UnitB.pas", "Doe()")["id"]


def test_symbol_file_targets_give_no_edge_yet(run, monkeypatch):
    import json

    from graphify import pascal_resolution

    symbols = json.loads((FIXTURES / "delphi-test.symbols.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: symbols)
    g = run("Casts.pas", "Basis.pas")
    assert _calls_from(g, "Casts.pas", "Klik()") == []
