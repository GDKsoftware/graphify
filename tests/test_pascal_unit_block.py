"""The per-unit symbol block built from Pascal declarations (spec 4.1)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from graphify.extractors.base import _file_stem as file_stem
from graphify.extractors.base import _make_id as make_id
from graphify.extractors.pascal_unit import build_pascal_unit, pascal_unit_includes

FIXTURES = Path(__file__).parent / "fixtures" / "pascal_unit"
DECLS = FIXTURES / "Decls.pas"
SHARED_INC = FIXTURES / "shared.inc"
STEM = file_stem(DECLS)


@pytest.fixture(scope="module")
def block():
    return build_pascal_unit(DECLS, DECLS.read_text(encoding="utf-8"), stem=STEM)


def _build(tmp_path, source, name="U.pas", **kwargs):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return build_pascal_unit(path, source, stem=file_stem(path), **kwargs)


def test_block_lists_uses_per_section(block):
    assert block["interface_uses"] == ["Classes", "Forms", "StdCtrls"]
    assert block["implementation_uses"] == ["Helpers"]


def test_first_unlabelled_section_of_a_form_is_published(block):
    m = block["types"]["TfrmBasis"]["members"]
    assert m["Button1"][0] == {"vis": "published", "kind": "field", "type": "TButton",
                               "proc_type": False}


def test_visibility_sections(block):
    m = block["types"]["TfrmBasis"]["members"]
    assert m["Geheim"][0]["vis"] == "private"
    assert m["HeelGeheim"][0]["vis"] == "strict private"
    assert [v["params"] for v in m["Verander"]] == [0, 2]
    assert [v["min_params"] for v in m["Verander"]] == [0, 1]
    assert m["Verander"][0]["nid"] == make_id(STEM, "TfrmBasis.Verander")
    assert m["Helper"][0]["kind"] == "property" and m["Helper"][0]["type"] == "THelper"


def test_event_field_is_proc_type(block):
    assert block["types"]["TfrmBasis"]["members"]["FOnChange"][0]["kind"] == "field"
    assert "TCallback" in block["proc_types"]


def test_record_default_is_public(block):
    assert block["types"]["TPunt"]["kind"] == "record"
    assert block["types"]["TPunt"]["members"]["X"][0]["vis"] == "public"


def test_routines_interface_flag_and_nid(block):
    assert block["routines"]["Maandnaam"][0]["interface"] is True
    assert block["routines"]["Intern"][0]["interface"] is False
    assert block["routines"]["Maandnaam"][0]["nid"] == make_id(STEM, "Maandnaam")
    assert len(block["routines"]["Maandnaam"]) == 1


def test_globals_and_ancestor(block):
    assert block["globals"] == {"frmBasis": "TfrmBasis"}
    assert block["types"]["TfrmBasis"]["ancestor"] == "TForm"


def test_nested_routine_in_block(block):
    assert "Stap" in block["nested"]["TfrmBasis.Verander"]
    assert block["nested"]["TfrmBasis.Verander"]["Stap"][0]["nid"] == make_id(
        STEM, "TfrmBasis.Verander.Stap")


def test_include_routine_belongs_to_including_unit(block):
    r = block["routines"]["Gedeeld"][0]
    assert r["interface"] is False and r["nid"] == make_id(file_stem(SHARED_INC), "Gedeeld")
    assert block["includes"] == ["shared.inc"]
    assert block["module_nid"] == make_id(STEM, "Decls")
    assert block["unit"] == "Decls"


def test_missing_include_warns_and_continues(tmp_path, caplog):
    src = ("unit U;\ninterface\nimplementation\n{$I nope.inc}\n{$I also.inc}\n"
           "procedure P;\nbegin\nend;\nend.\n")
    with caplog.at_level(logging.WARNING):
        b = _build(tmp_path, src)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "nope.inc" in warnings[0].getMessage()
    assert "P" in b["routines"] and b["includes"] == ["nope.inc", "also.inc"]


def test_include_found_in_include_dirs_and_interface_section(tmp_path):
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    (inc_dir / "decl.inc").write_text("procedure FromInc(A: Integer);\n", encoding="utf-8")
    src = "unit U;\ninterface\n{$INCLUDE decl.inc}\nimplementation\nend.\n"
    b = _build(tmp_path, src, include_dirs=[inc_dir])
    r = b["routines"]["FromInc"][0]
    assert r["interface"] is True and r["params"] == 1
    assert r["nid"] == make_id(file_stem(inc_dir / "decl.inc"), "FromInc")


def test_interface_only_drops_private_and_implementation():
    b = build_pascal_unit(DECLS, DECLS.read_text(encoding="utf-8"), stem=STEM,
                          interface_only=True)
    assert "Intern" not in b["routines"] and "Gedeeld" not in b["routines"]
    members = b["types"]["TfrmBasis"]["members"]
    assert "Geheim" not in members and "HeelGeheim" not in members
    assert "Verander" in members and "FOnChange" in members
    assert b["implementation_uses"] == []
    assert b["module_nid"] is None and b["nested"] == {}
    assert all("nid" not in r for rs in b["routines"].values() for r in rs)
    assert all("nid" not in v for vs in members.values() for v in vs)


def test_includes_are_found_in_raw_text_only_outside_comments():
    raw = "unit U;\n{$I a.inc}\n// {$I skipped.inc}\n(*$INCLUDE 'b.inc'*)\n{$I+}\n{$IFDEF X}\n"
    assert pascal_unit_includes(raw) == ["a.inc", "b.inc"]


def test_robust_class_forms(tmp_path):
    src = """unit U;
interface
type
  TMeta = class of TBase;
  TFwd = class;
  TBase = class sealed(Vcl.Forms.TForm)
  private
    FRec: record A: Integer; B: Integer; end;
  public
    type TInner = class
      X: Integer;
    end;
    procedure Run(const Cb: reference to function(V: Integer): Integer; D: Integer = Foo(1, 2)); virtual;
    class function Make: TBase; static;
    class var Counter: Integer;
    constructor Create(AOwner: TComponent); override;
    property Items[I: Integer]: string read GetItem write SetItem; default;
    FHandler: procedure(Sender: TObject) of object;
  end;
  IThing = interface(IInterface)
    ['{00000000-0000-0000-0000-000000000000}']
    procedure Do1;
  end;
  TEmpty = class(Exception);
implementation
end.
"""
    b = _build(tmp_path, src)
    t = b["types"]
    assert "TMeta" not in t and "TFwd" not in t
    base = t["TBase"]
    assert base["ancestor"] == "Vcl.Forms.TForm"
    m = base["members"]
    assert m["FRec"][0]["vis"] == "private" and m["FRec"][0]["type"] == "record"
    assert m["Run"][0]["params"] == 2 and m["Run"][0]["min_params"] == 1
    assert m["Make"][0]["vis"] == "public" and m["Counter"][0]["kind"] == "field"
    assert m["Create"][0]["kind"] == "method"
    assert m["Items"][0]["kind"] == "property"
    assert m["FHandler"][0]["proc_type"] is True
    assert "X" not in m and "TInner" not in m
    assert t["IThing"]["kind"] == "interface"
    assert t["IThing"]["members"]["Do1"][0]["vis"] == "public"
    assert t["TEmpty"]["ancestor"] == "Exception" and t["TEmpty"]["members"] == {}


def test_published_default_follows_typeinfo_directive(tmp_path):
    src = ("unit U;\ninterface\ntype\n  TPlain = class(TObject)\n    FA: Integer;\n  end;\n"
           "{$M+}\n  TInfo = class(TObject)\n    FB: Integer;\n  end;\n{$M-}\n"
           "  TComp = class(TComponent)\n    FC: Integer;\n  end;\nimplementation\nend.\n")
    t = _build(tmp_path, src)["types"]
    assert t["TPlain"]["members"]["FA"][0]["vis"] == "public"
    assert t["TInfo"]["members"]["FB"][0]["vis"] == "published"
    assert t["TComp"]["members"]["FC"][0]["vis"] == "published"


def test_routine_with_complex_header_and_overloads_match_interface(tmp_path):
    src = """unit U;
interface
procedure Over(A: Integer); overload;
procedure Over(A: string; B: Integer = 0); overload;
function Make(const Cb: reference to function(X: Integer): Integer): Integer;
var
  G1, G2: Integer;
  G3: array[0..3] of Byte = (1, 2, 3, 4);
implementation
procedure Over(A: Integer);
var
  Local: Integer;
begin
  case A of 1: ; end;
end;
procedure Over(A: string; B: Integer = 0);
begin
end;
function Make(const Cb: reference to function(X: Integer): Integer): Integer;
begin
  Result := 0;
end;
procedure Hidden(const Cb: reference to function(X: Integer): Integer);
  var Inner: Integer;
begin
end;
var
  Later: string;
end.
"""
    b = _build(tmp_path, src)
    over = b["routines"]["Over"]
    assert [(r["params"], r["min_params"], r["interface"]) for r in over] == [
        (1, 1, True), (2, 1, True)]
    assert b["routines"]["Make"][0]["interface"] is True
    assert b["routines"]["Hidden"][0]["interface"] is False
    assert b["globals"] == {"G1": "Integer", "G2": "Integer", "G3": "array[0..3] of Byte",
                            "Later": "string"}


# --- the block on the nodes of both extractors -------------------------------

from graphify.extractors.pascal import _extract_pascal_regex, extract_pascal  # noqa: E402

EXTRACTORS = [pytest.param(extract_pascal, id="tree-sitter"), pytest.param(_extract_pascal_regex, id="regex")]


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_module_node_carries_unit_block(extractor):
    result = extractor(DECLS)
    module = next(n for n in result["nodes"] if n["id"] == make_id(STEM, "Decls"))
    assert module["_pascal_unit"]["unit"] == "Decls"
    assert result["pascal_unit"]["unit"] == "Decls"


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_marker_sits_on_file_node_without_unit_header(extractor, tmp_path):
    path = tmp_path / "Plain.inc"
    path.write_text("procedure Foo;\nbegin\nend;\n", encoding="utf-8")
    result = extractor(path)
    file_node = next(n for n in result["nodes"] if n["label"] == "Plain.inc")
    assert "_pascal_unit" in file_node


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_method_nodes_carry_visibility(extractor):
    result = extractor(DECLS)
    geheim = next(n for n in result["nodes"] if n["label"] == "Geheim()")
    assert geheim["visibility"] == "private"
    verander = next(n for n in result["nodes"] if n["label"] == "Verander()")
    assert verander["visibility"] == "public"


def test_pascal_unit_marker_survives_export(tmp_path):
    from graphify.build import build
    from graphify.cluster import cluster
    from graphify.export import to_graphml, to_json
    from graphify.extract import extract

    graph = build([extract([DECLS])])
    communities = cluster(graph)
    to_json(graph, communities, str(tmp_path / "graph.json"))
    to_graphml(graph, communities, str(tmp_path / "graph.graphml"))
    assert "_pascal_unit" not in (tmp_path / "graph.graphml").read_text(encoding="utf-8")


def test_context_marker_allow_lists_include_pascal_unit():
    root = Path(__file__).parent.parent / "graphify"
    for name in ("cli.py", "watch.py"):
        assert '"_pascal_unit"' in (root / name).read_text(encoding="utf-8"), name


NESTED = Path(__file__).parent / "fixtures" / "pascal_raw_calls" / "Nested.pas"


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_class_declared_in_implementation_gets_node_and_method_ids(extractor):
    stem = file_stem(NESTED)
    ids = {n["id"] for n in extractor(NESTED)["nodes"]}
    assert make_id(stem, "TBar") in ids
    assert make_id(stem, "TBar.M") in ids
    assert make_id(stem, "M") not in ids and not any(i.endswith("_nested_m") for i in ids)


def test_uses_are_deduplicated_case_insensitively(tmp_path):
    source = (
        "unit U;\ninterface\nuses\n  System.Collections,\n  WinUtils,\n  winutils;\n"
        "implementation\nuses\n  Foo, FOO, Bar;\nend.\n"
    )
    block = _build(tmp_path, source)
    assert block["interface_uses"] == ["System.Collections", "WinUtils"]
    assert block["implementation_uses"] == ["Foo", "Bar"]


def test_block_node_ids_follow_the_id_remap(tmp_path):
    from graphify.extract import extract

    (tmp_path / "A.pas").write_text("unit A;\ninterface\nimplementation\nend.\n", encoding="utf-8")
    (tmp_path / "B.pas").write_text("unit B;\ninterface\nimplementation\nend.\n", encoding="utf-8")
    result = extract([tmp_path / "A.pas", tmp_path / "B.pas"])
    module_ids = {n["id"] for n in result["nodes"] if n["label"] in ("A", "B")}
    block_ids = {n["_pascal_unit"]["module_nid"] for n in result["nodes"] if "_pascal_unit" in n}
    assert block_ids == module_ids
