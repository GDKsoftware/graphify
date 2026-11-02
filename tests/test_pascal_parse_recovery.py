"""A unit tree-sitter-pascal cannot parse as a unit still reaches the resolver.

Code that only compiles with one branch of a conditional, such as a `begin`
opened in both the `{$IFDEF}` and the `{$ELSE}` branch, makes the grammar wrap
the whole file in an ERROR node: there is no `unit` node, so the module node
was never made, the unit's symbol block had nowhere to go and every call in
the file was dropped by the resolver.

Static fixtures under tests/fixtures/pascal_parse_recovery/, for the reason
given in tests/test_pascal_resolution.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from graphify.extract import extract

FIXTURES = Path(__file__).parent / "fixtures" / "pascal_parse_recovery"
SPLIT_BEGIN = FIXTURES / "SplitBegin.pas"


def _extractors():
    from graphify.extract import _extract_pascal_regex, extract_pascal
    return [extract_pascal, _extract_pascal_regex]


def _labels(nodes: list[dict]) -> dict[str, str]:
    return {n["id"]: str(n.get("label", "")) for n in nodes}


@pytest.mark.parametrize("extractor", _extractors())
def test_unparsed_unit_keeps_its_module_and_routines(extractor):
    r = extractor(SPLIT_BEGIN)
    labels = set(_labels(r["nodes"]).values())
    assert {"SplitBegin", "TWerk", "Los()", "Stap()", "Start()"} <= labels
    assert any("_pascal_unit" in n for n in r["nodes"])


@pytest.mark.parametrize("extractor", _extractors())
def test_unparsed_unit_keeps_its_calls(extractor, monkeypatch, tmp_path):
    from graphify.extract import _extract_pascal_regex
    if extractor is _extract_pascal_regex:
        monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    graph = extract([SPLIT_BEGIN], cache_root=tmp_path, parallel=False)
    labels = _labels(graph["nodes"])
    calls = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"] if e.get("relation") == "calls"
    }
    assert {("Stap()", "Los()"), ("Start()", "Stap()")} <= calls
