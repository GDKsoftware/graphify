"""Regression tests for Pascal/Delphi case folding and string literals.

Pascal identifiers are case-insensitive, but the Pascal extractors report a
callee in lower case while the definition keeps its written spelling. Without
case folding for Pascal, a call to ``Maandnaam`` in another unit could never
match its ``Maandnaam()`` definition.

The regex fallback extractor used to scan string literals as code, so SQL text
such as ``'select initcap(a) from dual'`` became a call to ``initcap``, and an
``'end;'`` inside a string could cut a routine body short. Delphi 12 multiline
strings (``'''`` ... ``'''``) were tokenised wrongly as soon as they contained
a quote. Every extractor-level test runs against both the tree-sitter and the
regex extractor.

Uses static fixtures under tests/fixtures/pascal_case_strings/ rather than
tmp_path, for the reason given in tests/test_pascal_resolution.py: the Pascal
project-root walk-up must not escape into unrelated .pas files in the system
temp directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from graphify.extract import _lang_is_case_insensitive, extract

FIXTURES = Path(__file__).parent / "fixtures" / "pascal_case_strings"
HELPERS = FIXTURES / "Helpers.pas"
CALLER = FIXTURES / "Caller.pas"
STRINGS = FIXTURES / "Strings.pas"
MULTILINE = FIXTURES / "MultiLine.pas"
PROJECT = FIXTURES / "Project.dpr"


def _extractors():
    from graphify.extract import _extract_pascal_regex, extract_pascal
    return [extract_pascal, _extract_pascal_regex]


def _called_labels(result: dict, caller_label: str) -> set[str]:
    labels = _labels(result["nodes"])
    return {
        labels.get(e["target"], "")
        for e in result["edges"]
        if e.get("relation") == "calls" and labels.get(e["source"]) == caller_label
    }


def _call_line(result: dict, caller_label: str, target_label: str) -> int | None:
    labels = _labels(result["nodes"])
    for e in result["edges"]:
        if (e.get("relation") == "calls" and labels.get(e["source"]) == caller_label
                and labels.get(e["target"]) == target_label):
            return int(str(e["source_location"]).lstrip("L"))
    return None


def _line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() == text:
            return number
    raise AssertionError(f"{text!r} not found in {path.name}")


def _raw_callees(result: dict) -> set[str]:
    return {str(rc.get("callee", "")).lower() for rc in result.get("raw_calls", [])}


def _labels(nodes: list[dict]) -> dict[str, str]:
    return {n["id"]: str(n.get("label", "")) for n in nodes}


def _call_edge(graph: dict, src_label: str, tgt_label: str):
    labels = _labels(graph["nodes"])
    for e in graph["edges"]:
        if e.get("relation") != "calls":
            continue
        if labels.get(e.get("source")) == src_label and labels.get(e.get("target")) == tgt_label:
            return e
    return None


@pytest.mark.parametrize("ext", [".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc"])
def test_pascal_extensions_are_case_insensitive(ext):
    assert _lang_is_case_insensitive(f"src/unit{ext}")


def test_cross_unit_call_resolves_despite_case():
    graph = extract([HELPERS, CALLER])
    assert _call_edge(graph, "Rapport()", "Maandnaam()") is not None


def test_mask_strings_keeps_length_and_quotes():
    from graphify.extractors.pascal import _pascal_mask_strings
    src = "X('ab''c'); Y;"
    out = _pascal_mask_strings(src)
    assert len(out) == len(src)
    assert out == "X('     '); Y;"


def test_mask_strings_keeps_newlines_in_multiline_string():
    from graphify.extractors.pascal import _pascal_mask_strings
    src = "S := '''\n  it's x\n  ''';\nY;"
    out = _pascal_mask_strings(src)
    assert len(out) == len(src) and out.count("\n") == src.count("\n")
    assert out.endswith("''';\nY;") and "it" not in out


@pytest.mark.parametrize("extractor", _extractors())
def test_call_inside_string_is_not_reported(extractor):
    assert "initcap" not in _raw_callees(extractor(CALLER))


@pytest.mark.parametrize("extractor", _extractors())
def test_doubled_quote_string_is_fully_masked(extractor):
    r = extractor(STRINGS)
    assert "Verander()" not in _called_labels(r, "Doe()")
    assert "initcap" not in _raw_callees(r)


@pytest.mark.parametrize("extractor", _extractors())
def test_end_keyword_in_string_does_not_cut_body(extractor):
    assert {"Volgende()", "Tweede()"} <= _called_labels(extractor(STRINGS), "Doe()")


@pytest.mark.parametrize("extractor", _extractors())
def test_masking_preserves_call_line_numbers(extractor):
    r = extractor(STRINGS)
    assert _call_line(r, "Doe()", "Volgende()") == _line_of(STRINGS, "Log('end;'); Volgende;")
    assert _call_line(r, "Doe()", "Tweede()") == _line_of(STRINGS, "Eerste('x'); Tweede;")


@pytest.mark.parametrize("extractor", _extractors())
def test_multiline_string_content_is_not_code(extractor):
    r = extractor(MULTILINE)
    assert "Verander()" not in _called_labels(r, "Doe2()")
    assert "initcap" not in _raw_callees(r)


@pytest.mark.parametrize("extractor", _extractors())
def test_multiline_string_keeps_following_calls(extractor):
    r = extractor(MULTILINE)
    assert {"Volgende()", "Tweede()"} <= _called_labels(r, "Doe2()")
    assert _call_line(r, "Doe2()", "Tweede()") == _line_of(MULTILINE, "Tweede;")


def test_uses_in_path_survives_masking():
    # Only the regex extractor masks strings; the `'Helpers.pas'` path of a
    # `uses ... in` clause must not lose the unit name to the mask.
    from graphify.extract import _extract_pascal_regex
    r = _extract_pascal_regex(PROJECT)
    assert any(
        e.get("relation") == "imports" and "helpers" in str(e.get("target", "")).lower()
        for e in r["edges"]
    )


def test_string_does_not_create_cross_unit_edge():
    graph = extract([HELPERS, CALLER])
    assert _call_edge(graph, "Rapport()", "InitCap()") is None
    assert _call_edge(graph, "Rapport()", "Maandnaam()") is not None
