"""Regression tests for Pascal/Delphi case-insensitive cross-file call resolution.

Pascal identifiers are case-insensitive, but the Pascal extractors report a
callee in lower case while the definition keeps its written spelling. Without
case folding for Pascal, a call to ``Maandnaam`` in another unit could never
match its ``Maandnaam()`` definition.

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
