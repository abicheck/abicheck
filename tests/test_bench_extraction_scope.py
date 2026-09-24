"""The ownership-rooted closure in ``scripts/bench_extraction_scope.py``.

The benchmark's "closure" row is only a meaningful measurement if the
closure is right: every owned element kept, everything an owned element
needs kept, and a referenced namespace not dragging its siblings back in.
"""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

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
    return bench.ownership_closure(ET.fromstring(_XML), ["/proj/include"])


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
    root = ET.fromstring(_XML)
    keep, _ = bench.ownership_closure(root, ["/proj/include"])
    bench.prune_to(root, keep)
    kept_ids = {el.get("id") for el in root}
    assert kept_ids == keep
    assert {"f1", "f2"} <= kept_ids


def test_no_target_root_keeps_nothing_but_files() -> None:
    root = ET.fromstring(_XML)
    keep, seeds = bench.ownership_closure(root, ["/elsewhere"])
    assert seeds == 0
    assert keep == {"f1", "f2"}
