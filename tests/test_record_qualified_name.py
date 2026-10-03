"""`compare/record_qualified_name.qualify_record_name` and the detector it feeds.

Bug class: a detector that judges a record's *namespace* read it from the
finding's leaf name, so the namespace was visible only when DWARF contributed
a second, qualified finding. case89's ``inline_body_references_renamed_member``
therefore fired on Linux ``-g`` builds and on no other evidence profile -- the
macOS nightly (a ``-g`` dylib keeps DWARF out of the binary), a release
build, or a stripped one. The invariants:

* the primitive answers only from the records, only uniquely, independently
  of order and of which side a record came from (property tests, a hand-
  written oracle that enumerates the cases rather than reusing the set logic);
* end to end, the verdict and the finding are the same whatever debug
  evidence accompanies the headers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.compare.record_qualified_name import qualify_record_name


@dataclass(frozen=True)
class _Rec:
    name: str
    qualified_name: str | None


_LEAVES = st.sampled_from(["Impl", "descriptor_impl", "S"])
_SCOPES = st.sampled_from([None, "", "a", "a::detail", "b::impl", "c"])


def _rec(leaf: str, scope: str | None) -> _Rec:
    if scope is None:
        return _Rec(leaf, None)
    return _Rec(leaf, f"{scope}::{leaf}" if scope else leaf)


_RECORDS = st.lists(st.builds(_rec, _LEAVES, _SCOPES), max_size=6)


def _oracle(name: str, records: list[_Rec]) -> str:
    """Enumerates the cases; deliberately not the implementation's set logic."""
    if "::" in name:
        return name
    answer: str | None = None
    for rec in records:
        if rec.name != name:
            continue
        spelling = rec.qualified_name if rec.qualified_name else rec.name
        if answer is None:
            answer = spelling
        elif answer != spelling:
            return name  # two scopes: no unique answer
    return answer if answer is not None else name


@given(name=_LEAVES, records=_RECORDS)
def test_matches_the_enumerating_oracle(name: str, records: list[_Rec]) -> None:
    assert qualify_record_name(name, records) == _oracle(name, records)


@given(name=_LEAVES, records=_RECORDS, data=st.data())
def test_order_independent(name: str, records: list[_Rec], data: st.DataObject) -> None:
    shuffled = data.draw(st.permutations(records))
    assert qualify_record_name(name, shuffled) == qualify_record_name(name, records)


@given(name=_LEAVES, records=_RECORDS)
def test_answer_is_the_input_or_a_matching_records_spelling(
    name: str, records: list[_Rec]
) -> None:
    got = qualify_record_name(name, records)
    allowed = {name} | {
        r.qualified_name for r in records if r.name == name and r.qualified_name
    }
    assert got in allowed
    assert got.rsplit("::", 1)[-1] == name  # never renames the record itself


@given(records=_RECORDS)
def test_already_qualified_names_are_untouched(records: list[_Rec]) -> None:
    assert qualify_record_name("x::detail::Impl", records) == "x::detail::Impl"


def test_ambiguous_leaf_is_not_attributed_to_either_scope() -> None:
    recs = [_Rec("Impl", "a::detail::Impl"), _Rec("Impl", "b::Impl")]
    assert qualify_record_name("Impl", recs) == "Impl"


# -- End to end: the finding must not depend on the debug-info profile -------

_CASE = next(Path(__file__).resolve().parents[1].glob("catalog/cases/case89_*"))
_PROFILES = {
    "dwarf": (["-g"], False),
    "no-debug-info": ([], False),
    "stripped": (["-g"], True),
}


@pytest.mark.integration
@pytest.mark.skipif(
    not (shutil.which("castxml") and shutil.which("c++")),
    reason="needs castxml and c++",
)
def test_case89_finding_is_independent_of_debug_evidence(tmp_path: Path) -> None:
    results: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for profile, (flags, strip) in _PROFILES.items():
        work = tmp_path / profile
        work.mkdir()
        for v in ("v1", "v2"):
            lib = work / f"lib{v}.so"
            subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    *flags,
                    "-fPIC",
                    "-shared",
                    f"-I{_CASE}",
                    "-include",
                    str(_CASE / f"{v}.h"),
                    str(_CASE / f"{v}.cpp"),
                    "-o",
                    str(lib),
                ],
                check=True,
            )
            if strip:
                # -S is --strip-debug in GNU strip and the only spelling
                # Apple's strip accepts; -g exists only in GNU strip.
                subprocess.run(["strip", "-S", str(lib)], check=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "abicheck",
                    "dump",
                    str(lib),
                    "-H",
                    str(_CASE / f"{v}.h"),
                    "-o",
                    str(work / f"{v}.json"),
                ],
                check=True,
                capture_output=True,
            )
        out = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                str(work / "v1.json"),
                str(work / "v2.json"),
                "-o",
                "json=-",
            ],
            capture_output=True,
            text=True,
        ).stdout
        report = json.loads(out)
        findings = sorted(
            (c["kind"], c["symbol"])
            for c in report["changes"]
            if c["kind"] == "inline_body_references_renamed_member"
        )
        results[profile] = (report["verdict"], findings)
    expected = ("BREAKING", [("inline_body_references_renamed_member", "descriptor")])
    assert results == dict.fromkeys(_PROFILES, expected), results
