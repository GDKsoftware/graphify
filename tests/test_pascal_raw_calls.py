"""Raw calls reported by the Pascal extractors: qualifier, argument count and
enclosing routines, plus the shared call-site helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphify.extractors.pascal_calls import (
    count_args,
    is_expression_receiver,
    make_raw_call,
    split_callee,
)

from graphify.extractors.base import _file_stem, _make_id

NESTED = Path(__file__).parent / "fixtures" / "pascal_raw_calls" / "Nested.pas"
CALLS = Path(__file__).parent / "fixtures" / "pascal_raw_calls" / "Calls.pas"


def _extractors():
    from graphify.extract import _extract_pascal_regex, extract_pascal

    return [
        pytest.param(extract_pascal, id="tree-sitter"),
        pytest.param(_extract_pascal_regex, id="regex"),
    ]


def _rc(result: dict, callee: str) -> dict | None:
    for rc in result.get("raw_calls", []):
        if rc["callee"].lower() == callee.lower():
            return rc
    return None


def _labels(nodes: list[dict]) -> dict[str, str]:
    return {n["id"]: n["label"] for n in nodes}


def test_split_callee():
    assert split_callee("Vcl.Dialogs.ShowMessage") == ("Vcl.Dialogs", "ShowMessage")
    assert split_callee("a[i] . Foo") == ("a[i]", "Foo")
    assert split_callee("Foo") == (None, "Foo")


def test_is_expression_receiver():
    assert is_expression_receiver("a[i]") and is_expression_receiver("GetX()")
    assert is_expression_receiver("p^")
    assert not is_expression_receiver("Vcl.Dialogs") and not is_expression_receiver(None)


def test_count_args():
    assert count_args(None) == 0 and count_args("") == 0
    assert count_args("f(1, 2), 3") == 2
    assert count_args("A, (B + C), [1, 2]") == 3
    assert count_args("f(1") is None


def test_make_raw_call_schema():
    rc = make_raw_call(
        source_file="x.pas", line=7, caller_nid="n", callee="Foo", qualifier="Self",
        arg_count=1, enclosing_routines=["TA.B"],
    )
    assert rc == {
        "language": "pascal", "source_file": "x.pas", "source_location": "L7",
        "caller_nid": "n", "callee": "Foo", "qualifier": "Self",
        "is_member_call": False, "arg_count": 1, "enclosing_routines": ["TA.B"], "with_stack": [],
    }
    unit_call = make_raw_call(
        source_file="x.pas", line=1, caller_nid="n", callee="Foo", qualifier="Unit1",
        arg_count=0, enclosing_routines=[],
    )
    # Whether `Unit1` is a unit, variable or type is for the resolver to decide.
    assert unit_call["is_member_call"] is False
    expr_call = make_raw_call(
        source_file="x.pas", line=1, caller_nid="n", callee="Foo", qualifier="a[i]",
        arg_count=0, enclosing_routines=[],
    )
    assert expr_call["is_member_call"] is True


@pytest.mark.parametrize("extractor", _extractors())
def test_qualified_call_keeps_qualifier(extractor):
    rc = _rc(extractor(CALLS), "ShowMessage")
    assert rc["qualifier"] == "Vcl.Dialogs" and rc["language"] == "pascal"
    assert rc["arg_count"] == 1


@pytest.mark.parametrize("extractor", _extractors())
def test_callee_keeps_source_case(extractor):
    assert _rc(extractor(CALLS), "ShowMessage")["callee"] == "ShowMessage"


@pytest.mark.parametrize("extractor", _extractors())
def test_arg_count_counts_top_level_only(extractor):
    assert _rc(extractor(CALLS), "Bereken")["arg_count"] == 3
    assert _rc(extractor(CALLS), "Leeg")["arg_count"] == 0


@pytest.mark.parametrize("extractor", _extractors())
def test_nested_routine_has_node_and_enclosing_chain(extractor):
    r = extractor(CALLS)
    assert "Inner()" in _labels(r["nodes"]).values()
    inner = _make_id(_file_stem(CALLS), "TFoo.Q.Inner")
    assert inner in _labels(r["nodes"])
    assert any(
        e["relation"] == "contains" and e["target"] == inner
        and e["source"] == _make_id(_file_stem(CALLS), "TFoo.Q")
        for e in r["edges"]
    )
    assert _rc(r, "InitCap")["enclosing_routines"] == ["TFoo.Q.Inner", "TFoo.Q"]


@pytest.mark.parametrize("extractor", _extractors())
def test_local_variable_shadows_call(extractor):
    assert _rc(extractor(CALLS), "Lokaal") is None


@pytest.mark.parametrize("extractor", _extractors())
def test_inherited_call_is_marked(extractor):
    r = extractor(CALLS)
    assert any(c["qualifier"] == "inherited" and c["callee"].lower() == "q"
               for c in r["raw_calls"])


@pytest.mark.parametrize("extractor", _extractors())
def test_bare_inherited_names_current_method(extractor):
    r = extractor(CALLS)
    inherited = [c for c in r["raw_calls"] if c["qualifier"] == "inherited"]
    assert len(inherited) == 2
    bare = [c for c in inherited if c["arg_count"] == 0]
    assert [c["callee"] for c in bare] == ["Q"]
    assert all(c["is_member_call"] is False for c in inherited)


@pytest.mark.parametrize("extractor", _extractors())
def test_expression_receiver_is_member_call(extractor):
    assert _rc(extractor(CALLS), "Refresh")["is_member_call"] is True


@pytest.mark.parametrize("extractor", _extractors())
def test_routine_after_asm_or_external_routine_stays_top_level(extractor):
    r = extractor(NESTED)
    ids = _labels(r["nodes"])
    for name in ("Next", "Ext", "After", "Last"):
        nid = _make_id(_file_stem(NESTED), name)
        assert nid in ids, name
        assert any(
            e["relation"] == "contains" and e["target"] == nid
            and e["source"] == _make_id(_file_stem(NESTED), "Nested")
            for e in r["edges"]
        ), name
    assert _make_id(_file_stem(NESTED), "Fast.Next") not in ids


@pytest.mark.parametrize("extractor", _extractors())
def test_sibling_after_routine_with_nested_stays_top_level(extractor):
    r = extractor(NESTED)
    assert _make_id(_file_stem(NESTED), "TBar.M.Inner") in _labels(r["nodes"])
    assert _make_id(_file_stem(NESTED), "Last") in _labels(r["nodes"])
    assert _make_id(_file_stem(NESTED), "TBar.M.Last") not in _labels(r["nodes"])


@pytest.mark.parametrize("extractor", _extractors())
def test_bare_inherited_in_nested_routine_names_the_method(extractor):
    r = extractor(NESTED)
    inherited = [c for c in r["raw_calls"] if c["qualifier"] == "inherited"]
    assert [c["callee"] for c in inherited] == ["M"]
    assert inherited[0]["enclosing_routines"] == ["TBar.M.Inner", "TBar.M"]


WITH_BLOK = Path(__file__).parent / "fixtures" / "delphi_scope" / "WithBlok.pas"


def _with_stacks(result: dict) -> list[tuple[str, int | None, list]]:
    return [
        (rc["callee"], rc["source_location"], rc["with_stack"])
        for rc in result["raw_calls"]
        if rc["caller_nid"] == _make_id(_file_stem(WITH_BLOK), "Test")
        and rc["callee"].lower() in ("bar", "doeiets")
    ]


@pytest.mark.parametrize("extract", [p.values[0] for p in _extractors()],
                         ids=["tree-sitter", "regex"])
def test_with_stack_records_expression(extract):
    stacks = [(c, ws) for c, _loc, ws in _with_stacks(extract(WITH_BLOK))]
    assert stacks[0] == ("Bar", [{"expr": "TFoo.Create", "multi": False}])
    assert stacks[1] == ("Bar", [{"expr": "GetObj", "multi": False}])
    assert stacks[2] == ("DoeIets", [{"expr": "frmMain", "multi": False}])
    assert stacks[3] == ("Bar", [{"expr": "A, B", "multi": True}])
    assert stacks[4] == ("Bar", [
        {"expr": "GetObj", "multi": False}, {"expr": "TFoo.Create", "multi": False},
    ])


@pytest.mark.parametrize("extract", [p.values[0] for p in _extractors()],
                         ids=["tree-sitter", "regex"])
def test_with_stack_local_head_is_flagged(extract):
    rc = next(
        rc for rc in extract(WITH_BLOK)["raw_calls"]
        if rc["callee"] == "DoeIets" and rc["caller_nid"].endswith("lokaal")
    )
    assert rc["with_stack"] == [{"expr": "frmMain", "multi": False, "local": True}]


def test_with_stack_parity_between_extractors():
    from graphify.extract import _extract_pascal_regex, extract_pascal

    assert _with_stacks(extract_pascal(WITH_BLOK)) == _with_stacks(_extract_pascal_regex(WITH_BLOK))
    both = [
        [rc["with_stack"] for rc in r["raw_calls"]]
        for r in (extract_pascal(WITH_BLOK), _extract_pascal_regex(WITH_BLOK))
    ]
    assert both[0] == both[1]


def test_with_stack_ends_with_the_statement(tmp_path):
    from graphify.extract import _extract_pascal_regex, extract_pascal

    NL = chr(10)
    src = tmp_path / "Ends.pas"
    src.write_text(
        "unit Ends;" + NL + "interface" + NL + "implementation" + NL + "procedure T;" + NL + "begin" + NL
        + "  with X do if a then Foo(1) else Baz;" + NL + "  Qux;" + NL
        + "  with Y do begin case n of 1: Zed; end; Zap; end;" + NL + "  Last;" + NL + "end;" + NL + "end." + NL
    )
    for extract in (extract_pascal, _extract_pascal_regex):
        got = {rc["callee"]: [w["expr"] for w in rc["with_stack"]] for rc in extract(src)["raw_calls"]}
        assert got["Foo"] == ["X"] and got["Baz"] == ["X"], extract
        assert got["Qux"] == [], extract
        assert got["Zed"] == ["Y"] and got["Zap"] == ["Y"], extract
        assert got["Last"] == [], extract
