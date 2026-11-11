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
def run(extractor, tmp_path, monkeypatch):
    from graphify import pascal_resolution

    # The package ships a real symbol file; these tests use fixture symbols only.
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: None)

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


def test_symbol_file_target_needs_the_unit_in_uses(run, monkeypatch):
    import json

    from graphify import pascal_resolution

    symbols = json.loads((FIXTURES / "delphi-test.symbols.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: symbols)
    g = run("Casts.pas", "Basis.pas")
    # `Close` is TCustomForm.Close, reached through `Vcl.Forms` in the uses; the
    # typecast receiver and the event call stay without an edge.
    edges = _calls_from(g, "Casts.pas", "Klik()")
    assert [(e["target"], e["rule"]) for e in edges] == [("vcl_forms", "5.1/3")]


def _with_symbols(monkeypatch):
    import json

    from graphify import pascal_resolution

    symbols = json.loads((FIXTURES / "delphi-test.symbols.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(pascal_resolution, "load_delphi_symbols", lambda *a, **k: symbols)


def test_duplicated_unit_in_uses_stops_the_search(run):
    # `Dup` is declared by two files and comes last in the uses: the compiler
    # would take its `Doe`, which one is ambiguous, so UnitA.Doe is never right.
    g = run("UnitA.pas", "dup_a/Dup.pas", "dup_b/Dup.pas", "UsesDup.pas")
    assert _calls_from(g, "UsesDup.pas", "Test()") == []


def test_call_in_include_resolves_in_including_unit(run):
    g = run("IncMain.pas", "inc_code.inc")
    edge = _call(g, ("inc_code.inc", "B()"), ("IncMain.pas", "A()"))
    assert edge is not None and edge["rule"] == "5.1/4"
    assert _call(g, ("IncMain.pas", "C()"), ("inc_code.inc", "B()")) is not None


def test_include_shared_by_several_units_gives_no_edge(run):
    g = run("IncOne.pas", "IncTwo.pas", "inc_shared.inc")
    assert _calls_from(g, "inc_shared.inc", "B()") == []


def test_own_type_shadows_routine_of_used_unit(run):
    g = run("ShadowLib.pas", "Shadow.pas")
    assert _calls_from(g, "Shadow.pas", "Cast()") == []


def test_own_field_shadows_routine_of_used_unit(run):
    g = run("ShadowLib.pas", "Shadow.pas")
    assert _calls_from(g, "Shadow.pas", "Event()") == []


def test_symbol_file_ancestor_member_shadows_routine_of_used_unit(run, monkeypatch):
    _with_symbols(monkeypatch)
    g = run("ShadowLib.pas", "Shadow.pas")
    # The ancestor's `Close` wins over ShadowLib's: the edge goes to the unit stub.
    assert [e["target"] for e in _calls_from(g, "Shadow.pas", "Sluit()")] == ["vcl_forms"]
    assert _calls_from(g, "Shadow.pas", "Cast()") == []
    assert _calls_from(g, "Shadow.pas", "Event()") == []


def _member(name: str, params: int, min_params: int | None = None, nid: str | None = None):
    from graphify.pascal_symbols import Member

    return Member(
        name=name, kind="method", vis="public", params=params,
        min_params=params if min_params is None else min_params, type=None,
        proc_type=False, nid=nid or f"{name}/{params}", owner="U.T", unit="U",
    )


def test_outcome_picks_the_overload_by_arity():
    from graphify.pascal_resolution import DelphiResolver, _arity_fits

    zero, one, two = _member("Laad", 0), _member("Laad", 1), _member("Laad", 2)
    assert _arity_fits(one, 1) and not _arity_fits(one, 2)
    optional = _member("Laad", 3, min_params=1)
    assert _arity_fits(optional, 1) and _arity_fits(optional, 3) and not _arity_fits(optional, 0)
    for count, chosen in ((0, zero), (1, one), (2, two)):
        res = DelphiResolver._outcome([zero, one, two], count, "5.1/2")
        assert (res.target, res.confidence, res.score) == (chosen.nid, "EXTRACTED", 1.0)
    # Two overloads fit one argument: the first candidate, INFERRED.
    res = DelphiResolver._outcome([optional, one], 1, "5.1/2")
    assert (res.target, res.confidence, res.score) == (optional.nid, "INFERRED", 0.85)
    res = DelphiResolver._outcome([two, one], None, "5.1/2")
    assert (res.target, res.confidence) == (two.nid, "INFERRED")


def _qualified_run(run):
    return run("Helpers.pas", "Main.pas", "DataMod.pas")


def test_unit_qualified_call(run):
    g = _qualified_run(run)
    edge = _call(g, ("DataMod.pas", "Laad()"), ("Helpers.pas", "InitCap()"))
    assert edge is not None and edge["confidence"] == "EXTRACTED"
    assert edge["rule"] == "5.2/3"
    target = _routine(g, "Helpers.pas", "InitCap()")["id"]
    assert [e for e in _calls_from(g, "DataMod.pas", "Laad()") if e["target"] == target] == [edge]


def test_global_variable_qualifier(run):
    g = _qualified_run(run)
    edge = _call(g, ("DataMod.pas", "Laad()"), ("Main.pas", "DoeIets()"))
    assert edge is not None and edge["rule"] == "5.2/4"


def test_type_qualifier(run):
    g = _qualified_run(run)
    edge = _call(g, ("DataMod.pas", "Laad()"), ("Main.pas", "Maak()"))
    assert edge is not None and edge["rule"] == "5.2/5"


def test_typed_field_qualifier(run):
    g = _qualified_run(run)
    edge = _call(g, ("DataMod.pas", "Laad()"), ("Main.pas", "Help()"))
    assert edge is not None and edge["rule"] == "5.2/6"


def test_self_qualifier(run):
    g = _qualified_run(run)
    edge = _call(g, ("DataMod.pas", "Laad()"), ("DataMod.pas", "Prive()"))
    assert edge is not None and edge["rule"] == "5.2/1"
    assert _call(g, ("DataMod.pas", "Laad()"), ("DataMod.pas", "Laad()")) is None


def test_unknown_qualifier_gives_no_edge(run):
    g = _qualified_run(run)
    targets = {_node(g, e["target"]).get("label") for e in _calls_from(g, "DataMod.pas", "Laad()")}
    assert targets == {"InitCap()", "DoeIets()", "Maak()", "Help()", "Prive()"}


def test_private_member_of_variable_type_from_other_unit_gives_no_edge(run):
    g = _qualified_run(run)
    assert _call(g, ("DataMod.pas", "Prive()"), ("Main.pas", "Verborgen()")) is None


def test_unit_variable_and_unit_type_qualifier(run):
    g = _qualified_run(run)
    prive = ("DataMod.pas", "Prive()")
    var_edge = _call(g, prive, ("Main.pas", "DoeIets()"))
    type_edge = _call(g, prive, ("Main.pas", "Maak()"))
    assert var_edge is not None and var_edge["rule"] == "5.2/3"
    assert type_edge is not None and type_edge["rule"] == "5.2/3"


def test_duplicated_unit_qualifier_gives_no_edge(run):
    g = run("dup_a/Dup.pas", "dup_b/Dup.pas", "UsesDupQual.pas")
    assert _calls_from(g, "UsesDupQual.pas", "Test()") == []


def test_parameter_named_like_global_gives_no_edge(run):
    g = run("Main.pas", "QualLocal.pas")
    assert _calls_from(g, "QualLocal.pas", "ParamVrij()") == []


def test_local_named_like_type_gives_no_edge(run):
    g = run("Main.pas", "QualLocal.pas")
    assert _calls_from(g, "QualLocal.pas", "Lokaal()") == []


def test_field_named_like_global_gives_no_edge(run):
    # In Delphi the field wins over the global; spec order 4-6 would pick the
    # global. Deliberately more conservative: when both match, no edge at all.
    g = run("Main.pas", "QualLocal.pas")
    assert _calls_from(g, "QualLocal.pas", "Veld()") == []


# ---------------------------------------------- symbol-file targets (external)


@pytest.fixture
def vcl(run, monkeypatch):
    from graphify import pascal_resolution

    _with_symbols(monkeypatch)
    monkeypatch.setattr(pascal_resolution, "_namespaces", lambda *a, **k: ["System", "Vcl"],
                        raising=False)
    return run("VclGebruik.pas", "ZonderDialogs.pas")


def _imports_target(graph: dict, file: str, unit: str) -> str:
    """Target of the `imports` edge of the module in `file` to the symbol-file
    unit `unit` (its full name: the stub is named after it, however the uses
    entry spells it)."""
    from graphify.extract import _make_id

    stub = _make_id(unit)
    module = next(
        n for n in graph["nodes"]
        if _file(n) == file and n.get("label", "").lower() == Path(file).stem.lower()
    )
    hits = [
        e for e in graph["edges"]
        if e["relation"] == "imports" and e["source"] == module["id"] and e["target"] == stub
    ]
    assert hits, (file, unit)
    return hits[0]["target"]


def _edge_to(graph: dict, file: str, label: str, target: str) -> dict | None:
    return next((e for e in _calls_from(graph, file, label) if e["target"] == target), None)


def test_vcl_routine_resolves_to_unit_stub(vcl):
    target = _imports_target(vcl, "VclGebruik.pas", "Vcl.Dialogs")
    edge = _edge_to(vcl, "VclGebruik.pas", "Doe()", target)
    assert edge is not None
    assert (edge["confidence"], edge["rule"]) == ("EXTRACTED", "5.1/6")
    node = _node(vcl, target)
    assert node["pascal_external"] is True and "dialogs" in node["label"].lower()


def test_ancestor_from_symbol_file(vcl):
    target = _imports_target(vcl, "VclGebruik.pas", "Vcl.Forms")
    edge = _edge_to(vcl, "VclGebruik.pas", "Doe()", target)
    assert edge is not None and edge["rule"] == "5.1/3"


def test_global_from_symbol_file(vcl):
    forms = _imports_target(vcl, "VclGebruik.pas", "Vcl.Forms")
    edge = _edge_to(vcl, "VclGebruik.pas", "Wacht()", forms)
    assert edge is not None
    assert (edge["confidence"], edge["rule"]) == ("EXTRACTED", "5.2/4")


def test_system_routine_without_uses(vcl):
    from graphify.ids import make_id

    # `IntToStr` and `UpperCase` are both System routines of the test symbol file.
    edge = _edge_to(vcl, "VclGebruik.pas", "Doe()", make_id("System"))
    assert edge is not None
    assert (edge["confidence"], edge["rule"]) == ("EXTRACTED", "5.1/7")
    assert _node(vcl, make_id("System"))["pascal_external"] is True


def test_vcl_routine_not_in_uses_gives_no_edge(vcl):
    assert _calls_from(vcl, "ZonderDialogs.pas", "Probeer()") == []


def test_external_node_is_created_or_flagged():
    from graphify.pascal_resolution import _add_external_nodes

    nodes = [{"id": "forms", "label": "Forms", "source_file": ""}]
    _add_external_nodes({"forms": "Vcl.Forms", "system": "System"}, nodes)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["forms"]["pascal_external"] is True
    assert by_id["system"] == {
        "id": "system", "label": "System", "file_type": "code", "source_file": "",
        "pascal_external": True,
    }


def test_regex_and_tree_sitter_report_the_same_raw_calls(monkeypatch):
    from graphify.extractors.pascal import extract_pascal

    path = FIXTURES / "ParityRtl.pas"

    def callees() -> list[tuple]:
        result = extract_pascal(path)
        return sorted((c["callee"], c.get("qualifier")) for c in result["raw_calls"])

    tree_sitter = callees()
    monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    regex = callees()
    assert regex == tree_sitter
    names = {c for c, _ in regex}
    assert {"IntToStr", "Format", "Free", "Create", "Assigned"} <= names


# ------------------- unit-level consts and globals shadow routines of used units


def _const_run(run):
    return run("ConstLib.pas", "ConstShadowLib.pas", "ConstUser.pas", "ConstUsed.pas")


@pytest.mark.parametrize("label", [
    "TestConst()",      # own-unit interface const
    "TestVar()",        # own-unit interface global variable
    "TestImplConst()",  # own-unit implementation const
    "TestStatement()",  # bare `Foo;` statement naming an own-unit const
    "TestLocal()",      # local const
])
def test_own_unit_value_shadows_routine_of_used_unit(run, label):
    g = _const_run(run)
    assert _calls_from(g, "ConstUser.pas", label) == []


def test_used_unit_interface_global_shadows_earlier_routine(run):
    g = _const_run(run)
    assert _calls_from(g, "ConstUsed.pas", "TestGlobal()") == []


def test_used_unit_implementation_const_does_not_shadow(run):
    g = _const_run(run)
    edge = _call(g, ("ConstUsed.pas", "TestImplConst()"), ("ConstLib.pas", "Quux()"))
    assert edge is not None and edge["rule"] == "5.1/6"


def test_enumeration_value_is_not_a_call(run):
    g = _const_run(run)
    assert _calls_from(g, "ConstUsed.pas", "TestEnum()") == []


# ------------------------------------------------------------------ with (5.3)


def _with_targets(graph: dict) -> list[tuple[str, dict]]:
    src = _routine(graph, "WithBlok.pas", "Test()")["id"]
    return [
        (str(_node(graph, e["target"]).get("id")), e)
        for e in graph["edges"] if e["relation"] == "calls" and e["source"] == src
    ]


def test_with_known_type_is_inferred_095(run):
    g = run("Main.pas", "WithBlok.pas")
    hits = {t: e for t, e in _with_targets(g)}
    bar = next(t for t in hits if t.endswith("tfoo_bar"))
    assert hits[bar]["confidence"] == "INFERRED" and hits[bar]["confidence_score"] == 0.95
    assert hits[bar]["rule"].startswith("5.3")
    doe = next(t for t in hits if t.endswith("doeiets"))
    assert hits[doe]["confidence"] == "INFERRED" and hits[doe]["confidence_score"] == 0.95


def test_with_unknown_type_gives_no_edge(run):
    g = run("Main.pas", "WithBlok.pas")
    # `with GetObj do Bar` must not fall back to the unit-level routine Bar.
    assert not [t for t, _e in _with_targets(g) if t.endswith("withblok_bar")]


def test_with_multi_gives_no_edge(run):
    g = run("Main.pas", "WithBlok.pas")
    targets = [t for t, _e in _with_targets(g)]
    # Five `with` calls: only the first Bar and DoeIets bind.
    assert len(targets) == 2, targets


def test_nested_with_gives_no_edge(run):
    g = run("Main.pas", "WithBlok.pas")
    bars = [e for t, e in _with_targets(g) if t.endswith("tfoo_bar")]
    assert len(bars) == 1
    assert bars[0]["source_location"] == "L29"


def test_with_on_local_variable_gives_no_edge(run):
    g = run("Main.pas", "WithBlok.pas")
    assert _calls_from(g, "WithBlok.pas", "Lokaal()") == []
