"""`.dproj` reading, the `.graphifyrc` Pascal keys and the Delphi symbol file choice."""
# pyright: reportOptionalSubscript=false, reportOptionalMemberAccess=false, reportOperatorIssue=false
from __future__ import annotations

import gzip
import json
import logging
import shutil
import sys
from pathlib import Path

import pytest

from graphify import pascal_symbols, rcfile
from graphify.extract import extract
from graphify.pascal_dproj import DprojError, delphi_version_for, read_dproj
from graphify.pascal_symbols import load_delphi_symbols

FIXTURES = Path(__file__).parent / "fixtures" / "delphi_scope"


@pytest.fixture(autouse=True)
def _reset_pascal_settings():
    rcfile.set_pascal_settings(None)
    yield
    rcfile.set_pascal_settings(None)


def _write_symbols(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"delphi-{version}.symbols.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"delphi_version": version, "units": {}}, f)
    return path


def _activate(root: Path, text: str) -> None:
    (root / ".graphifyrc").write_text(text, encoding="utf-8")
    rcfile.activate_language_overrides(root)


# ---------------------------------------------------------------- read_dproj


def test_read_dproj_namespaces_and_search_path():
    info = read_dproj(FIXTURES / "Project.dproj")
    assert info.namespaces == ("System", "Xml", "Vcl", "Vcl.Imaging")
    assert info.search_path == (FIXTURES / "../shared", FIXTURES / "libs/common")
    assert info.project_version == "0.0"


def test_unreadable_dproj_raises(tmp_path):
    with pytest.raises(DprojError):
        read_dproj(tmp_path / "missing.dproj")
    bad = tmp_path / "bad.dproj"
    bad.write_text("<Project><oops", encoding="utf-8")
    with pytest.raises(DprojError):
        read_dproj(bad)


def test_delphi_version_for_unknown_or_missing_is_none():
    assert delphi_version_for(None) is None
    assert delphi_version_for("999.9") is None


# -------------------------------------------------------------------- rcfile


def test_rcfile_reads_pascal_keys(tmp_path):
    (tmp_path / ".graphifyrc").write_text(
        "pascal.dproj=Project.dproj\npascal.delphi_version=13.2\n"
        "pascal.delphi_symbols=elders\n",
        encoding="utf-8",
    )
    assert rcfile.load_graphifyrc(tmp_path)["pascal"] == {
        "dproj": "Project.dproj", "delphi_version": "13.2", "delphi_symbols": "elders",
    }
    rcfile.activate_language_overrides(tmp_path)
    settings = rcfile.get_pascal_settings()
    assert settings == rcfile.PascalSettings(tmp_path, "Project.dproj", "13.2", "elders")


def test_pascal_settings_reset_on_next_activation(tmp_path):
    _activate(tmp_path, "pascal.dproj=Project.dproj\n")
    assert rcfile.get_pascal_settings().dproj == "Project.dproj"
    other = tmp_path / "other"
    other.mkdir()
    rcfile.activate_language_overrides(other)
    assert rcfile.get_pascal_settings().dproj is None


# ------------------------------------------------------------- symbol choice


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "pkgdata"
    path.mkdir()
    monkeypatch.setattr(pascal_symbols, "_DELPHI_DATA_DIR", path)
    return path


def test_delphi_version_none_disables_symbols(tmp_path, data_dir):
    _write_symbols(data_dir, "13.2")
    _activate(tmp_path, "pascal.delphi_version=none\n")
    assert load_delphi_symbols() is None
    assert load_delphi_symbols("none") is None


def test_delphi_symbols_key_points_to_file(tmp_path, data_dir):
    _write_symbols(data_dir, "13.2")
    mine = _write_symbols(tmp_path / "elders", "9.9")
    mine.rename(mine.with_name("mijn.symbols.json.gz"))
    _activate(tmp_path, "pascal.delphi_symbols=elders/mijn.symbols.json.gz\n")
    assert load_delphi_symbols()["delphi_version"] == "9.9"
    assert load_delphi_symbols("13.2")["delphi_version"] == "9.9"


def test_delphi_symbols_key_points_to_directory(tmp_path, data_dir):
    _write_symbols(tmp_path / "eigen", "12.3")
    _write_symbols(tmp_path / "eigen", "13.2")
    _write_symbols(data_dir, "11.0")
    _activate(tmp_path, "pascal.delphi_symbols=eigen\npascal.delphi_version=12.3\n")
    assert load_delphi_symbols()["delphi_version"] == "12.3"
    _activate(tmp_path, "pascal.delphi_symbols=eigen\n")
    assert load_delphi_symbols()["delphi_version"] == "13.2"


def test_delphi_symbols_missing_path_warns_and_falls_back(tmp_path, data_dir, caplog):
    _write_symbols(data_dir, "13.2")
    _activate(tmp_path, "pascal.delphi_symbols=bestaat-niet\n")
    with caplog.at_level(logging.WARNING):
        assert load_delphi_symbols()["delphi_version"] == "13.2"
    assert "bestaat-niet" in caplog.text


def test_missing_version_falls_back_to_newest_with_warning(data_dir, caplog):
    _write_symbols(data_dir, "13.2")
    _write_symbols(data_dir, "12.3")
    with caplog.at_level(logging.WARNING):
        assert load_delphi_symbols("11.0")["delphi_version"] == "13.2"
    assert "11.0" in caplog.text


def test_version_from_dproj_project_version(tmp_path, data_dir, monkeypatch):
    _write_symbols(data_dir, "13.2")
    _write_symbols(data_dir, "12.3")
    monkeypatch.setattr("graphify.pascal_dproj._PROJECT_VERSION_TO_DELPHI", {"0.0": "12.3"})
    shutil.copy(FIXTURES / "Project.dproj", tmp_path / "Project.dproj")
    _activate(tmp_path, "pascal.dproj=Project.dproj\n")
    assert load_delphi_symbols()["delphi_version"] == "12.3"


def test_unreadable_dproj_warns_and_acts_like_no_dproj(tmp_path, data_dir, caplog):
    _write_symbols(data_dir, "13.2")
    (tmp_path / "Kapot.dproj").write_text("<Project><oops", encoding="utf-8")
    _activate(tmp_path, "pascal.dproj=Kapot.dproj\n")
    from graphify.pascal_dproj import active_dproj

    with caplog.at_level(logging.WARNING):
        assert active_dproj() is None
        assert load_delphi_symbols()["delphi_version"] == "13.2"
    assert "Kapot.dproj" in caplog.text


# ------------------------------------------------------------------ end to end


@pytest.fixture(params=["tree-sitter", "regex"])
def extractor(request, monkeypatch):
    if request.param == "regex":
        monkeypatch.setitem(sys.modules, "tree_sitter_pascal", None)
    return request.param


def test_dproj_namespace_maps_dialogs_to_vcl_dialogs(extractor, tmp_path):
    project = tmp_path / "project"
    shutil.copytree(FIXTURES, project)
    symbols = json.loads((FIXTURES / "delphi-test.symbols.json").read_text(encoding="utf-8"))
    with gzip.open(tmp_path / "mine.symbols.json.gz", "wt", encoding="utf-8") as f:
        json.dump(symbols, f)
    (project / ".graphifyrc").write_text(
        "pascal.dproj=Project.dproj\npascal.delphi_symbols=../mine.symbols.json.gz\n",
        encoding="utf-8",
    )
    paths = [project / "VclGebruik.pas", project / "ZonderDialogs.pas"]
    graph = extract(paths, cache_root=tmp_path / "cache", root=project, parallel=False)

    def in_file(n: dict) -> bool:
        return Path(str(n.get("source_file", ""))).name == "VclGebruik.pas"

    module = next(n for n in graph["nodes"] if in_file(n) and n["label"].lower() == "vclgebruik")
    doe = next(n for n in graph["nodes"] if in_file(n) and n["label"] == "Doe()")
    stub = next(
        e["target"] for e in graph["edges"]
        if e["relation"] == "imports" and e["source"] == module["id"]
        and e["target"].endswith("dialogs")
    )
    edges = [e for e in graph["edges"] if e["relation"] == "calls" and e["source"] == doe["id"]]
    hit = next(e for e in edges if e["target"] == stub)
    assert (hit["confidence"], hit["rule"]) == ("EXTRACTED", "5.1/6")


def test_include_found_via_unit_search_path(extractor, tmp_path, caplog):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "inc").mkdir()
    shutil.copy(FIXTURES / "IncMain.pas", project / "src" / "IncMain.pas")
    shutil.copy(FIXTURES / "inc_code.inc", project / "inc" / "inc_code.inc")
    (project / "Project.dproj").write_text(
        '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">'
        "<PropertyGroup><DCC_UnitSearchPath>inc;$(DCC_UnitSearchPath)</DCC_UnitSearchPath>"
        "</PropertyGroup></Project>",
        encoding="utf-8",
    )
    from graphify.extractors.pascal import extract_pascal

    unit = project / "src" / "IncMain.pas"

    def body_nid() -> str | None:
        return extract_pascal(unit)["pascal_unit"]["routines"]["B"][0]["nid"]

    with caplog.at_level(logging.WARNING):
        assert "inc_code" not in str(body_nid())  # the body sits in the missing include
    assert "include file not found" in caplog.text

    _activate(project, "pascal.dproj=Project.dproj\n")
    assert "inc_code" in str(body_nid())


# ------------------------------------------------------- cache and robustness


def _unit_with_search_path_include(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "inc").mkdir()
    (project / "inc2").mkdir()
    shutil.copy(FIXTURES / "IncMain.pas", project / "src" / "IncMain.pas")
    for d in ("inc", "inc2"):
        shutil.copy(FIXTURES / "inc_code.inc", project / d / "inc_code.inc")
    return project, project / "src" / "IncMain.pas"


def _dproj(project: Path, name: str, directory: str) -> None:
    (project / name).write_text(
        '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">'
        f"<PropertyGroup><DCC_UnitSearchPath>{directory}</DCC_UnitSearchPath>"
        "</PropertyGroup></Project>",
        encoding="utf-8",
    )


def test_cached_pascal_file_is_reextracted_when_search_path_changes(
    extractor, tmp_path, caplog
):
    project, unit = _unit_with_search_path_include(tmp_path)
    (project / "empty").mkdir()
    cache = tmp_path / "cache"

    def run_warns() -> bool:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            extract([unit], cache_root=cache, root=project, parallel=False)
        return "include file not found" in caplog.text

    assert run_warns()      # no .dproj: include not found, result is cached
    assert not run_warns()  # served from the cache
    _dproj(project, "Project.dproj", "empty")
    (project / ".graphifyrc").write_text("pascal.dproj=Project.dproj\n", encoding="utf-8")
    assert run_warns()      # new search path: the cached entry is not reused
    assert not run_warns()
    _dproj(project, "Project.dproj", "inc")
    assert not run_warns()  # changed path re-extracted, and now finds the include


def test_cache_salt_is_none_without_search_path_or_for_other_languages(tmp_path):
    project, unit = _unit_with_search_path_include(tmp_path)
    assert rcfile.cache_salt(unit) is None
    _dproj(project, "Project.dproj", "inc")
    _activate(project, "pascal.dproj=Project.dproj\n")
    assert "pascal_dirs=" in rcfile.cache_salt(unit)
    assert rcfile.cache_salt(project / "x.py") is None


@pytest.mark.parametrize("content", [b"not gzip at all", None])
def test_corrupt_symbol_file_warns_and_falls_back(tmp_path, data_dir, caplog, content):
    _write_symbols(data_dir, "13.2")
    bad = tmp_path / "bad.symbols.json.gz"
    if content is None:  # valid gzip, invalid JSON
        with gzip.open(bad, "wt", encoding="utf-8") as f:
            f.write("{oops")
    else:
        bad.write_bytes(content)
    _activate(tmp_path, "pascal.delphi_symbols=bad.symbols.json.gz\n")
    with caplog.at_level(logging.WARNING):
        assert load_delphi_symbols()["delphi_version"] == "13.2"
    assert "bad.symbols.json.gz" in caplog.text


def test_corrupt_symbol_file_without_bundled_files_gives_none(tmp_path, data_dir, caplog):
    (tmp_path / "bad.symbols.json.gz").write_bytes(b"junk")
    _activate(tmp_path, "pascal.delphi_symbols=bad.symbols.json.gz\n")
    with caplog.at_level(logging.WARNING):
        assert load_delphi_symbols() is None


def test_pool_hands_its_workers_the_pascal_settings(tmp_path, monkeypatch):
    import concurrent.futures

    from graphify.extract import _worker_init

    project, _unit = _unit_with_search_path_include(tmp_path)
    _dproj(project, "Project.dproj", "inc")
    (project / ".graphifyrc").write_text("pascal.dproj=Project.dproj\n", encoding="utf-8")
    seen: dict = {}

    class RecordingPool(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, max_workers=None, initializer=None, initargs=(), **kw):
            seen["initializer"] = initializer
            seen["initargs"] = initargs
            super().__init__(max_workers=max_workers, initializer=initializer, initargs=initargs)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", RecordingPool)
    files = []
    for i in range(25):  # past the parallel threshold
        f = project / "src" / f"U{i}.pas"
        f.write_text(f"unit U{i};\ninterface\nimplementation\nend.\n", encoding="utf-8")
        files.append(f)
    extract(files, cache_root=tmp_path / "cache", root=project, parallel=True)
    assert seen["initializer"] is _worker_init
    assert seen["initargs"][1].dproj == "Project.dproj"
    assert seen["initargs"][1].root == project
