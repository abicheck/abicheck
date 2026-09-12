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

"""``schema_staleness_status``'s content-identity invariant.

Bug class: an early return justified by "comparing a value against itself
can never produce a false pairwise finding" was implemented as *object*
identity (``old is new``), so the identical argument silently failed to
cover the shape a user actually hits -- ONE stored snapshot file loaded
twice (``compare baseline.abi.json baseline.abi.json``, or a CI job
re-checking an unchanged cached baseline), which reported
``schema_staleness_status == "degraded"`` and flipped ``assurance.status``
complete -> partial. Stated here as an invariant over generated inputs
(several schema vintages, clean and degraded), not a fixture pinned to the
one observed snapshot -- plus the load-bearing negative, without which the
fix could silently become "never taint anything".
"""

from __future__ import annotations

import json
import warnings
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from abicheck.policy.analysis_assurance_degraded_facts import (
    degraded_reliability_facts,
)
from abicheck.policy.analysis_assurance_schema_staleness import (
    schema_staleness_status,
)
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import same_persisted_content

_FIXTURES = Path(__file__).parent / "fixtures" / "schema"

#: Schema vintages spanning both sides of the invariant: current-ish (no
#: degraded fact at all) through several genuinely stale ones.
_VINTAGES = (45, 38, 25, 18, 9, 4)


def _status(a: Any, b: Any) -> tuple[str, list[str]]:
    """`schema_staleness_status` as production calls it.

    The content-identity answer is supplied by the caller — `policy` may
    not import `storage` — so a test that omitted it would exercise the
    narrower object-identity path and prove nothing about the real one.
    `test_every_compute_call_site_passes_the_storage_projection` is what
    holds production to the same wiring.
    """
    return schema_staleness_status(
        a, b, same_content=partial(same_persisted_content, a, b)
    )


def _load(fixture: str, **overrides: Any):
    d = json.loads((_FIXTURES / fixture).read_text())
    d.update(overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return snapshot_from_dict(d)


@pytest.mark.parametrize("vintage", _VINTAGES)
@pytest.mark.parametrize("fixture", ["v4.json", "v5.json"])
def test_content_identical_reload_matches_self_pairing(
    fixture: str, vintage: int
) -> None:
    """The invariant: for any snapshot ``s``, the status of two
    *independently loaded, content-equal* copies of ``s`` equals the status
    of ``(s, s)``. Holds for clean and degraded inputs alike -- the
    soundness argument ("no pairwise finding is possible") is about equal
    content, never about which Python object holds it."""
    a = _load(fixture, schema_version=vintage, from_headers=True, ast_producer="clang")
    b = _load(fixture, schema_version=vintage, from_headers=True, ast_producer="clang")
    assert a is not b
    assert a == b
    assert _status(a, b) == _status(a, a)
    assert _status(a, b)[0] == "clean"


@pytest.mark.parametrize("vintage", _VINTAGES)
def test_reload_invariant_is_exercised_on_genuinely_degraded_inputs(
    vintage: int,
) -> None:
    """Guard against the invariant above passing vacuously: at least the
    stale vintages must really carry degraded facts, or the parametrization
    only ever tested the clean path."""
    a = _load(
        "v4.json", schema_version=vintage, from_headers=True, ast_producer="clang"
    )
    if vintage >= 45:
        assert not degraded_reliability_facts(a)
    else:
        assert degraded_reliability_facts(a), vintage


@pytest.mark.parametrize("stale_vintage", [38, 25, 18, 9, 4])
def test_differing_vintages_still_report_degraded(stale_vintage: int) -> None:
    """Load-bearing negative: two snapshots of the SAME library at
    DIFFERENT schema vintages, one degraded, are a real two-sided
    comparison a stale fact can distort -- they must still report
    ``degraded``. Without this the fix could silently become "never taint
    anything", and two independent extractions of one binary differing in
    schema vintage is exactly the case this field exists to report."""
    stale = _load(
        "v4.json", schema_version=stale_vintage, from_headers=True, ast_producer="clang"
    )
    current = _load(
        "v4.json", schema_version=45, from_headers=True, ast_producer="clang"
    )
    assert stale != current
    assert degraded_reliability_facts(stale)
    status, notes = _status(stale, current)
    assert status == "degraded", notes
    # ... and symmetrically, with the stale side as NEW.
    assert _status(current, stale)[0] == "degraded"


def test_self_compare_of_stored_stale_snapshot_is_complete_end_to_end(
    tmp_path: Path,
) -> None:
    """Through the real public ``compare`` path (AGENTS.md: validate the
    user-facing result): the exact reported shape -- one pre-v45 stored
    snapshot file given as BOTH operands -- must read ``assurance.status ==
    "complete"``, since a CI job gating on that field was newly failing on
    an unchanged cached baseline."""
    from click.testing import CliRunner

    from abicheck.cli import main

    d = json.loads((_FIXTURES / "v4.json").read_text())
    d.update(schema_version=25, from_headers=True, ast_producer="clang")
    path = tmp_path / "baseline.abi.json"
    path.write_text(json.dumps(d))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert degraded_reliability_facts(snapshot_from_dict(d))

    result = CliRunner().invoke(
        main, ["compare", str(path), str(path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assurance = payload["analysis_assurance"]
    assert assurance["schema_staleness_status"] == "clean", assurance
    assert assurance["status"] == "complete", assurance


def test_two_distinct_stored_snapshots_still_taint_end_to_end(tmp_path: Path) -> None:
    """The end-to-end mirror of the negative above: two DIFFERENT stored
    files, one stale, must still report a non-complete assurance."""
    from click.testing import CliRunner

    from abicheck.cli import main

    base = json.loads((_FIXTURES / "v4.json").read_text())
    old_d = {
        **base,
        "schema_version": 25,
        "from_headers": True,
        "ast_producer": "clang",
    }
    new_d = {**old_d, "schema_version": 45}
    old_p, new_p = tmp_path / "old.abi.json", tmp_path / "new.abi.json"
    old_p.write_text(json.dumps(old_d))
    new_p.write_text(json.dumps(new_d))

    result = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--format", "json"]
    )
    payload = json.loads(result.output)
    assurance = payload["analysis_assurance"]
    assert assurance["schema_staleness_status"] == "degraded", assurance
    assert assurance["status"] != "complete", assurance


def test_content_identical_compare_still_warns_it_can_detect_nothing(
    tmp_path: Path,
) -> None:
    """The load-bearing pairing behind `_same_content`'s one residual
    (Codex review, PR #1228): an `AbiSnapshot` is a lossy capture, so equal
    content proves the recorded evidence is equal, not that the two
    artifacts are. That residual is disclosed by
    `confidence.note_if_same_binary_compared`, which fires from the very
    same signal and makes the stronger claim. If that warning ever stops
    firing, `schema_staleness_status`'s "clean" would be the run's only
    word on a comparison that cannot detect anything — so this test pins
    the two together rather than leaving the coupling implicit."""
    from click.testing import CliRunner

    from abicheck.cli import main

    d = json.loads((_FIXTURES / "v4.json").read_text())
    d.update(schema_version=25, from_headers=True, ast_producer="clang")
    path = tmp_path / "baseline.abi.json"
    path.write_text(json.dumps(d))

    result = CliRunner().invoke(
        main, ["compare", str(path), str(path), "--format", "json"]
    )
    payload = json.loads(result.output)
    assert payload["analysis_assurance"]["status"] == "complete"
    warnings_out = payload.get("coverage_warnings", [])
    assert any("byte-identical" in w for w in warnings_out), warnings_out
    assert any("cannot detect a change" in w for w in warnings_out), warnings_out


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param("entity_id", id="compare_false_field"),
        pytest.param("library", id="ordinary_field"),
        pytest.param("nested_return_type", id="nested_declaration_field"),
    ],
)
def test_any_persisted_difference_defeats_content_identity(mutate: str) -> None:
    """Generalizes past the one reported field: a difference in an
    ordinary field, in a `compare=False` field, or nested inside a
    declaration must each defeat content identity. `entity_id` is the
    reported case (it is `field(compare=False)`, so plain `==` misses it,
    yet it IS persisted); the other two are independently-chosen siblings,
    so the answer cannot be a special-case for one field name."""
    from abicheck.model.identity import EntityId, EntityKind
    from abicheck.storage.snapshot_encode import same_persisted_content

    a = _load("v4.json", schema_version=25, from_headers=True)
    b = _load("v4.json", schema_version=25, from_headers=True)
    assert same_persisted_content(a, b), "fixture must start out identical"

    if mutate == "entity_id":
        assert b.functions, "fixture must carry a function to perturb"
        b.functions[0].entity_id = EntityId(
            scope=(), kind=EntityKind.FUNCTION, leaf_name="perturbed"
        )
    elif mutate == "library":
        b.library = f"{b.library}-other"
    else:
        assert b.functions, "fixture must carry a function to perturb"
        b.functions[0].return_type = f"{b.functions[0].return_type} const"

    assert not same_persisted_content(a, b)
    assert _status(a, b)[0] == "degraded"


def test_content_identity_fails_closed_on_an_unencodable_snapshot() -> None:
    """ "Not provably the same content" rather than an exception: the one
    consumer is a status field on an already-degraded path, so an encoder
    failure must cost an over-cautious `degraded`, never a comparison that
    used to complete."""
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    class _Unencodable:
        def __repr__(self) -> str:  # pragma: no cover - only for failure output
            return "<unencodable>"

    a = _load("v4.json", schema_version=25, from_headers=True)
    b = _load("v4.json", schema_version=25, from_headers=True)
    assert same_persisted_content(a, b)
    assert b.functions, "fixture must carry a function to perturb"
    b.functions[0].return_type = _Unencodable()  # type: ignore[assignment]
    with pytest.raises(TypeError):
        snapshot_content_digest(b)
    assert same_persisted_content(a, b) is False
    assert _status(a, b)[0] == "degraded"


def test_every_compute_call_site_passes_the_storage_projection() -> None:
    """The exhaustiveness half, and the reason this question is answered in
    `storage` at all (Codex review, PR #1229 -- the P1, plus the four P2s
    that followed it, each a nested projection a `model`-side
    reimplementation got wrong).

    `schema_staleness_status` cannot compute content identity itself:
    `policy` may not import `storage`. So it takes the answer as a
    callable, which means an un-threaded call site would silently fall back
    to the narrower object-identity test -- reintroducing the original
    defect on that path, with nothing failing anywhere. This AST-scans
    every `compute_analysis_assurance(...)` call under `abicheck/` and
    requires `same_content=`."""
    import ast

    root = Path(__file__).resolve().parent.parent / "abicheck"
    sites: list[tuple[str, int, bool]] = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name != "compute_analysis_assurance":
                continue
            sites.append(
                (
                    str(path.relative_to(root)),
                    node.lineno,
                    any(kw.arg == "same_content" for kw in node.keywords),
                )
            )

    assert sites, "the scan found no call site at all -- it is vacuous"
    missing = [(f, ln) for f, ln, ok in sites if not ok]
    assert not missing, (
        "compute_analysis_assurance() called without same_content= at "
        f"{missing}; pass storage.snapshot_encode.same_persisted_content, or "
        "that path falls back to object identity and a content-identical "
        "reload reports degraded again"
    )


def test_the_persisted_projection_is_not_reimplemented_outside_storage() -> None:
    """The companion to the call-site guard: the reason the class closed is
    that nothing outside `storage` describes what the codec persists any
    more. A new copy of that knowledge is how the four P2s happened."""
    root = Path(__file__).resolve().parent.parent / "abicheck"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.parts[-2] != "storage"
        and "RUNTIME_ONLY_FIELDS" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders
