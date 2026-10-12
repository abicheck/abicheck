# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A declared-header set that changes -- shrinks included -- is API evolution.

Bug class: header-set evolution was treated as extraction drift. oneDAL
2026.0 deleted 122 public headers (apriori, adaboost, ...) with flags,
defines and include topology unchanged; ``compare`` refused with "not
comparable" (exit 16) and a "manifest/CLI-flag drift" hint, because every
header-set waiver of the comparability gate was stated over *growth* only.
A removed public header is evidence of an API removal (AGENTS.md "Absent is
not removed" / "Record before disposing") and must reach the diff engine.

The invariant stated here, over an exhaustive small domain rather than the
reported input:

  *For every* pair of declared-header sets (old, new) extracted under the
  same configuration -- pure removal, removal plus addition, growth, a side
  collapsing to a single header, every header removed, a removal that moves
  the common header root -- the pair is comparable; and *for every* such
  pair, a genuine extraction-configuration difference (macros, language
  standard, an external include directory's content) riding alongside it is
  still refused, as is a header still declared on both sides but parsed on
  only one.

The oracle is the configuration itself: the tests build both sides with the
real ``compute_extraction_contract`` from inputs that differ ONLY in the
header set (expected comparable) or ALSO in one named configuration input
(expected refused) -- never from the gate's own helpers.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.comparability import (
    IncludeDir,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot, Function, Visibility

_UNIVERSE = ("a.h", "b.h", "c.h", "d.h")
# A nested universe, so a removal can move the common header root (oneDAL's
# include/daal/algorithms/<algo>/ layout).
_NESTED = (
    "algos/apriori/apriori.h",
    "algos/adaboost/adaboost.h",
    "core/core.h",
    "core/types.h",
)


def _write_headers(root: Path, names) -> list[Path]:
    out = []
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"// {name}\nint f_{abs(hash(name)) % 997}(void);\n")
        out.append(p)
    return sorted(out)


def _contract(root: Path, names, *, include_root: bool = True, **config):
    """One side's contract, as the real ``-H <dir>`` dump builds it: every
    discovered header is both declared (parsed) and public, and the
    header-owning directory is a declared include (resolve_inferred_header_roots)."""
    root.mkdir(parents=True, exist_ok=True)
    headers = _write_headers(root, names)
    # No header, no inferred header root -- as resolve_inferred_header_roots.
    includes = [IncludeDir(root)] if include_root and names else []
    includes += config.pop("extra_includes", [])
    return compute_extraction_contract(
        l2_frontend_ran=True,
        declared_headers=headers,
        public_header_paths=headers,
        declared_includes=includes,
        **config,
    )


def _snap(contract, version: str, functions=()) -> AbiSnapshot:
    return AbiSnapshot(
        library="libonedal.so",
        version=version,
        contract=contract,
        functions=list(functions),
    )


def _subsets(universe):
    return [
        combo
        for r in range(len(universe) + 1)
        for combo in itertools.combinations(universe, r)
    ]


def _changed_pairs(universe):
    subsets = _subsets(universe)
    return [(o, n) for o in subsets for n in subsets if set(o) != set(n) and o]


def _shape(old, new) -> str:
    removed, added = set(old) - set(new), set(new) - set(old)
    if not new:
        return "all-removed"
    if removed and added:
        return "removal+addition"
    return "pure-removal" if removed else "pure-addition"


# ---------------------------------------------------------------------------
# Half 1: every header-set change under one configuration is comparable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("universe", [_UNIVERSE, _NESTED], ids=["flat", "nested"])
def test_every_header_set_change_under_one_configuration_is_comparable(
    tmp_path, universe
):
    pairs = _changed_pairs(universe)
    # The domain really covers every shape the defect report names.
    assert {_shape(o, n) for o, n in pairs} == {
        "pure-removal",
        "removal+addition",
        "pure-addition",
        "all-removed",
    }
    refused = []
    for i, (old_names, new_names) in enumerate(pairs):
        old = _snap(_contract(tmp_path / f"{i}" / "old", old_names), "2025.9")
        new = _snap(_contract(tmp_path / f"{i}" / "new", new_names), "2026.0")
        try:
            check_contracts_comparable(old, new)
        except Exception as exc:  # noqa: BLE001 -- collected for one report
            refused.append((old_names, new_names, type(exc).__name__))
    assert refused == []


def test_onedal_shaped_removal_of_whole_algorithm_directories_is_comparable(tmp_path):
    """The reported shape: whole algorithm directories deleted, the rest kept."""
    kept = [f"daal/algorithms/kmeans/kmeans_{i}.h" for i in range(3)] + ["daal/daal.h"]
    gone = [
        f"daal/algorithms/{algo}/{algo}_{i}.h"
        for algo in ("apriori", "adaboost")
        for i in range(3)
    ]
    flags = {"macro_ops": [("-D", "DAAL_EXPORT")], "language_standard": "c++17"}
    old = _snap(_contract(tmp_path / "old", kept + gone, **flags), "2025.9")
    new = _snap(_contract(tmp_path / "new", kept, **flags), "2026.0")
    assert check_contracts_comparable(old, new) is None


# ---------------------------------------------------------------------------
# Half 2: genuine drift alongside the same shapes is still refused
# ---------------------------------------------------------------------------

_SHAPES = {
    "pure-removal": (("a.h", "b.h", "c.h"), ("a.h", "c.h")),
    "removal+addition": (("a.h", "b.h", "c.h"), ("a.h", "d.h")),
    "all-but-one-removed": (("a.h", "b.h", "c.h"), ("a.h",)),
    "all-removed": (("a.h", "b.h"), ()),
    "unchanged": (("a.h", "b.h"), ("a.h", "b.h")),
}


def _drift(tmp_path: Path, kind: str):
    """(old_config, new_config) differing in exactly one configuration input."""
    if kind == "defines":
        return {"macro_ops": [("-D", "X=1")]}, {"macro_ops": [("-D", "X=2")]}
    if kind == "standard":
        return {"language_standard": "c++17"}, {"language_standard": "c++20"}
    ext_old, ext_new = tmp_path / "ext_old", tmp_path / "ext_new"
    for d, body in ((ext_old, "int x;"), (ext_new, "long x;")):
        d.mkdir(parents=True, exist_ok=True)
        (d / "dep.h").write_text(body)
    return (
        {
            "extra_includes": [IncludeDir(ext_old)],
            "depfile_resolved_paths": [ext_old / "dep.h"],
        },
        {
            "extra_includes": [IncludeDir(ext_new)],
            "depfile_resolved_paths": [ext_new / "dep.h"],
        },
    )


@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize("drift", ["defines", "standard", "external-include"])
def test_configuration_drift_alongside_a_header_set_change_is_still_refused(
    tmp_path, shape, drift
):
    old_names, new_names = _SHAPES[shape]
    old_cfg, new_cfg = _drift(tmp_path, drift)
    old = _snap(_contract(tmp_path / "old", old_names, **old_cfg), "1")
    new = _snap(_contract(tmp_path / "new", new_names, **new_cfg), "2")
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


@pytest.mark.parametrize("shape", ["pure-removal", "removal+addition"])
def test_a_header_still_declared_but_no_longer_parsed_is_still_refused(tmp_path, shape):
    """Re-routing in the removal direction: ``x.h`` is parsed (``-H``) on the
    old side but only public-header provenance on the new side, so its content
    would be compared against nothing -- the reason the sequence carve-outs
    require scope corroboration, which a removal must not dilute."""
    old_names, new_names = _SHAPES[shape]
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    old_h = _write_headers(old_root, (*old_names, "x.h"))
    new_h = _write_headers(new_root, new_names)
    new_x = _write_headers(new_root, ("x.h",))
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True, declared_headers=old_h, public_header_paths=old_h
        ),
        "1",
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=new_h,
            public_header_paths=[*new_h, *new_x],
        ),
        "2",
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_a_reordered_surviving_header_is_still_refused_alongside_a_removal(tmp_path):
    old_h = _write_headers(tmp_path / "old", ("a.h", "b.h", "c.h"))
    new_h = _write_headers(tmp_path / "new", ("a.h", "c.h"))
    old = _snap(
        compute_extraction_contract(l2_frontend_ran=True, declared_headers=old_h), "1"
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True, declared_headers=list(reversed(new_h))
        ),
        "2",
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# Through the public comparison: the removed declarations are reported
# ---------------------------------------------------------------------------


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


@pytest.mark.parametrize("shape", ["pure-removal", "removal+addition", "all-removed"])
def test_compare_runs_and_reports_the_removed_headers_declarations(tmp_path, shape):
    old_names, new_names = _SHAPES[shape]
    decl = {n: _fn(n.removesuffix(".h") + "_api") for n in ("a.h", "b.h", "c.h", "d.h")}
    old = _snap(
        _contract(tmp_path / "old", old_names), "1", [decl[n] for n in old_names]
    )
    new = _snap(
        _contract(tmp_path / "new", new_names), "2", [decl[n] for n in new_names]
    )
    result = compare(old, new)
    removed = {decl[n].mangled for n in set(old_names) - set(new_names)}
    reported = {c.symbol for c in result.changes if c.kind.value == "func_removed"}
    assert removed <= reported
    assert result.verdict.value == "BREAKING"
