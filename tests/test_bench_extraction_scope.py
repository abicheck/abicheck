"""The ownership-rooted closure in ``scripts/bench_extraction_scope.py``.

The benchmark's "closure" row is only a meaningful measurement if the
closure is right: every owned element kept, everything an owned element
needs kept, and a referenced namespace not dragging its siblings back in.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import xml.etree.ElementTree as ET  # nosec B405 - trusted test data
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_extraction_scope.py"
_spec = importlib.util.spec_from_file_location("bench_extraction_scope", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench_extraction_scope"] = bench
_spec.loader.exec_module(bench)

_XML = """<CastXML>
  <File id="f1" name="/proj/include/lib/api.h"/>
  <File id="f2" name="/usr/include/c++/13/string"/>
  <Namespace id="_1" name="::"/>
  <Namespace id="_2" name="std" context="_1" members="_10 _11 _12"/>
  <Namespace id="_3" name="lib" context="_1" members="_20 _21"/>
  <Class id="_10" name="basic_string" context="_2" file="f2" members="_13" size="256"/>
  <Class id="_11" name="unrelated" context="_2" file="f2"/>
  <Struct id="_12" name="base" context="_2" file="f2"/>
  <Field id="_13" name="len" type="_30" context="_10" file="f2"/>
  <Struct id="_20" name="Rec" context="_3" file="f1" members="_22" bases="public:_12"/>
  <Function id="_21" name="f" returns="_31" context="_3" file="f1">
    <Argument name="s" type="_32"/>
  </Function>
  <Field id="_22" name="s" type="_10c" context="_20" file="f1"/>
  <FundamentalType id="_30" name="unsigned long"/>
  <FundamentalType id="_31" name="int"/>
  <ReferenceType id="_32" type="_10c"/>
  <CvQualifiedType id="_10c" type="_10" const="1"/>
  <FundamentalType id="_99" name="never used"/>
</CastXML>"""


def _closure() -> tuple[set[str], int]:
    return bench.ownership_closure(ET.fromstring(_XML), ["/proj/include"])  # nosec B314 - fixture XML built in this test


def test_every_owned_element_is_a_seed_and_kept() -> None:
    keep, seeds = _closure()
    assert seeds == 3  # _20, _21, _22
    assert {"_20", "_21", "_22"} <= keep


def test_everything_an_owned_element_needs_is_kept() -> None:
    keep, _ = _closure()
    # field type through a cv-qualified id, base through an access prefix,
    # argument type through a reference, return type, and the dependency
    # class's own field and that field's type (its layout)
    assert {"_10", "_10c", "_12", "_32", "_31", "_13", "_30"} <= keep
    # enclosing namespaces up to the global one
    assert {"_1", "_2", "_3"} <= keep


def test_a_kept_namespace_does_not_pull_in_its_members() -> None:
    keep, _ = _closure()
    assert "_11" not in keep  # std::unrelated
    assert "_99" not in keep


def test_prune_keeps_exactly_the_closure_and_files() -> None:
    root = ET.fromstring(_XML)  # nosec B314 - fixture XML built in this test
    keep, _ = bench.ownership_closure(root, ["/proj/include"])
    bench.prune_to(root, keep)
    kept_ids = {el.get("id") for el in root}
    assert kept_ids == keep
    assert {"f1", "f2"} <= kept_ids


def test_no_target_root_keeps_nothing_but_files() -> None:
    root = ET.fromstring(_XML)  # nosec B314 - fixture XML built in this test
    keep, seeds = bench.ownership_closure(root, ["/elsewhere"])
    assert seeds == 0
    assert keep == {"f1", "f2"}


@pytest.mark.parametrize(
    ("path", "root", "inside"),
    [
        ("/proj/include/api.h", "/proj/include", True),
        ("/proj/include/api.h", "/proj/include/", True),
        ("/proj/include", "/proj/include", True),
        ("/proj/include-private/api.h", "/proj/include", False),
        ("/proj/includes/api.h", "/proj/include", False),
        ("/proj/inc", "/proj/include", False),
    ],
)
def test_ownership_roots_match_at_directory_boundaries(
    path: str, root: str, inside: bool
) -> None:
    assert bench.under_root(path, root) is inside


def test_a_sibling_directory_sharing_the_root_prefix_is_not_a_seed() -> None:
    xml = _XML.replace(
        '<File id="f2" name="/usr/include/c++/13/string"/>',
        '<File id="f2" name="/proj/include-private/string"/>',
    )
    keep, seeds = bench.ownership_closure(ET.fromstring(xml), ["/proj/include"])  # nosec B314 - fixture XML built in this test
    assert seeds == 3
    assert "_11" not in keep  # declared in the look-alike sibling directory


@pytest.mark.parametrize(
    ("flags", "includes", "defines"),
    [
        (["-Ia", "-DX=1"], ["a"], ["X=1"]),
        (["-I", "a", "-D", "X=1"], ["a"], ["X=1"]),
        (["-I", "a", "-Ib", "-D", "Y", "-DZ=2"], ["a", "b"], ["Y", "Z=2"]),
        (["-std=c++20", "-O2"], [], []),
    ],
)
def test_include_and_define_operands_survive_both_spellings(
    flags: list[str], includes: list[str], defines: list[str]
) -> None:
    assert bench._stripped(flags, "-I") == includes
    assert bench._stripped(flags, "-D") == defines


def test_closure_child_process_writes_the_pruned_document(tmp_path: Path) -> None:
    """The measured closure runs through the script's own child entry point."""
    xml_in = tmp_path / "full.xml"
    xml_in.write_text(_XML)
    xml_out, seeds = tmp_path / "closure.xml", tmp_path / "seeds.json"
    assert (
        bench.main(
            ["--closure-only", str(xml_in), str(xml_out), "/proj/include", str(seeds)]
        )
        == 0
    )
    assert json.loads(seeds.read_text()) == {"seeds": 3}
    kept = {el.get("id") for el in ET.parse(xml_out).getroot()}  # nosec B314 - written by this test
    assert kept == _closure()[0]


def test_a_failed_castxml_run_is_an_error_not_a_stale_measurement(
    tmp_path: Path,
) -> None:
    out = tmp_path / "full.xml"
    out.write_text("<CastXML/>")  # left over from an earlier run
    with pytest.raises(RuntimeError, match="castxml failed"):
        bench._run_castxml([sys.executable, "-c", "import sys; sys.exit(1)"], out)
    assert not out.exists()


@pytest.mark.parametrize("exit_code", [0, 3])
def test_a_platform_without_wait4_reports_unknown_memory_not_zero(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    """Windows has no ``os.wait4``: the run still completes with its real
    exit code, and peak memory is unknown (``None``), never ``0``."""
    monkeypatch.delattr(bench.os, "wait4", raising=False)
    code = f"import sys; sys.stdout.write('x' * 10); sys.exit({exit_code})"
    result = bench.run_measured([sys.executable, "-c", code])
    assert result["returncode"] == exit_code
    assert result["peak_rss_mb"] is None
    assert result["stdout_mb"] == 0.0


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="needs os.wait4")
def test_peak_memory_is_in_megabytes_on_every_posix_platform() -> None:
    """ru_maxrss is KB on Linux and bytes on macOS; either way a child that
    touches 64 MB reports roughly 64-2048 MB, not 1024x off in either
    direction."""
    code = "b = bytearray(64 << 20); b[::4096] = b'x' * len(b[::4096])"
    result = bench.run_measured([sys.executable, "-c", code])
    assert result["returncode"] == 0
    assert 64 <= result["peak_rss_mb"] <= 2048
