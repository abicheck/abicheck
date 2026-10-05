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

"""Deterministic call-count complexity gate beyond ``compare()`` itself.

``test_compare_call_complexity.py`` covers the comparison; these are the
stages around it that also scale with input and run on every real use:

* **serialization** -- snapshot to JSON and back, over every workload shape;
* **every report format** (JSON, Markdown, SARIF, HTML, JUnit) over the
  ``DiffResult`` a workload produces, so the renderers are measured on the
  findings real detectors emit rather than hand-built ones;
* **release reconciliation** (``reconcile_member_sets``: one public
  contract, many providers) as the *member count* grows -- the axis the
  "one public surface, many providers" model exists to keep linear.

Same oracle as the compare gate (``_call_counts.superlinear_call_sites``):
no first-party function's call count may grow faster than
``(size ratio)^1.5``. Exact, so it runs in the unit lane.
"""

from __future__ import annotations

import itertools
import json

import pytest
from _call_counts import profile_call_counts, superlinear_call_sites
from _compare_workloads import WORKLOADS

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.html_report import generate_html_report
from abicheck.junit_report import to_junit_xml
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.reporter import to_json, to_markdown
from abicheck.sarif import to_sarif_str
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.workflows.release_public_surface import reconcile_member_sets
from abicheck.workflows.release_surface_acquisition import surface_from_snapshots

SMALL, LARGE = 50, 200
_tags = itertools.count()


def _assert_subquadratic(
    label: str, small: dict[str, int], large: dict[str, int], ratio: float
) -> None:
    assert sum(large.values()) > 0, f"{label}: no first-party calls measured"
    offenders = superlinear_call_sites(small, large, ratio)
    assert not offenders, (
        f"{label}: call counts grew faster than (size ratio)^1.5 -- likely O(n^2):\n  "
        + "\n  ".join(o.describe() for o in offenders[:10])
    )


@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_serialization_round_trip_grows_subquadratically(workload: str) -> None:
    def counts(n: int) -> dict[str, int]:
        _, snap = WORKLOADS[workload](n, f"ser{next(_tags)}_")

        def round_trip() -> None:
            loaded = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
            assert len(loaded.declarations.functions) == len(
                snap.declarations.functions
            )

        return profile_call_counts(round_trip)

    _assert_subquadratic(
        f"serialization/{workload}", counts(SMALL), counts(LARGE), LARGE / SMALL
    )


_RENDERERS = {
    "json": to_json,
    "markdown": to_markdown,
    "sarif": to_sarif_str,
    "html": lambda r: generate_html_report(r, lib_name="libshape.so"),
    "junit": to_junit_xml,
}


@pytest.mark.parametrize("renderer", sorted(_RENDERERS))
@pytest.mark.parametrize(
    "workload", ["signature_churn", "type_churn", "add_remove", "enum_churn"]
)
def test_report_rendering_grows_subquadratically(workload: str, renderer: str) -> None:
    render = _RENDERERS[renderer]

    def counts(n: int) -> dict[str, int]:
        result = compare(*WORKLOADS[workload](n, f"rep{next(_tags)}_"))
        assert len(result.changes) >= n // 10, (
            "the workload must produce findings proportional to its size"
        )
        return profile_call_counts(lambda: render(result))

    _assert_subquadratic(
        f"{renderer}/{workload}", counts(SMALL), counts(LARGE), LARGE / SMALL
    )


def _release(members: int, tag: str, *, drop: int = 0) -> dict[str, AbiSnapshot]:
    """*members* libraries, each declaring and exporting its own 8 symbols
    from the shared product header -- except the first *drop* symbols of
    every member, which are declared but not exported (a missing-export
    finding per member, so reconciliation has real work on every member)."""
    out = {}
    for m in range(members):
        name = f"{tag}lib{m}.so"
        declares = [f"{tag}m{m}_api_{i}" for i in range(8)]
        snap = AbiSnapshot(
            library=name,
            version="1.0",
            from_headers=True,
            elf=ElfMetadata(symbols=[ElfSymbol(name=s) for s in declares[drop:]]),
        )
        snap.declarations.functions = [
            Function(
                name=s,
                mangled=s,
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
                is_extern_c=True,
                source_header="/inc/product.h",
            )
            for s in declares
        ]
        out[name] = snap
    return out


def test_release_reconciliation_grows_subquadratically_in_members() -> None:
    def counts(members: int) -> dict[str, int]:
        tag = f"rel{next(_tags)}_"
        old = _release(members, tag)
        new = _release(members, tag, drop=2)

        def reconcile() -> None:
            result = reconcile_member_sets(
                new_members=new,
                new_surface=surface_from_snapshots(
                    new, acquisition_key="new", side="new"
                ),
                old_members=old,
                old_surface=surface_from_snapshots(
                    old, acquisition_key="old", side="old"
                ),
            )
            missing = [
                c for c in result.findings if c.kind == ChangeKind.PUBLIC_NOT_EXPORTED
            ]
            # Non-vacuity: two dropped exports per member, all reported.
            assert len(missing) == 2 * members, len(missing)

        return profile_call_counts(reconcile)

    small_m, large_m = 5, 20
    _assert_subquadratic(
        "release reconciliation", counts(small_m), counts(large_m), large_m / small_m
    )
