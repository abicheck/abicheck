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

"""ADR-078: an informational, no-material-change finding never gates.

Bug class ``policy.informational_outcome_gates``: a finding whose whole
content is "nothing a consumer can act on changed" was bucketed as a
``quality_issues`` residue, so ``--severity-preset strict`` (or any
``quality_issues: error``) failed the build on it.

The invariant is exercised through the public CLI over every informational
kind, every built-in preset, the legacy scheme and the whole
``quality_issues``/``addition`` level domain. The oracle is independent of
the implementation: the expected exit code of a run whose only finding is a
COMPATIBLE kind is read off the *configured level of the category the kind's
own catalog flags place it in* -- ``is_addition`` -> ``addition``,
``is_informational`` -> never, otherwise ``quality_issues`` -- and the
control rows (non-informational kinds) prove the harness can gate at all.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.change_registry import REGISTRY
from abicheck.checker_types import Change
from abicheck.cli import main
from abicheck.detector_registry import _DetectorEntry, registry
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import (
    ChangeKindMeta,
    ChangeKindRegistry,
    Verdict,
)
from abicheck.policy.severity import (
    SEVERITY_PRESETS,
    IssueCategory,
    SeverityConfig,
    SeverityLevel,
    classify_change,
    compute_exit_code,
    compute_gate_decision,
    gate_contribution_for_change,
)
from abicheck.serialization import snapshot_to_json

#: Independent statement of D2's membership (ADR-078), not read from
#: ``INFORMATIONAL_KINDS``: a change to the catalog flag must be a
#: deliberate edit here too.
EXPECTED_INFORMATIONAL = frozenset(
    {
        "declaration_coordinates_shifted",
        "declaration_identity_unchanged",
        "public_surface_grew",
        "public_surface_shrank",
    }
)

_COMPATIBLE = sorted(
    e.kind for e in REGISTRY.entries.values() if e.default_verdict == Verdict.COMPATIBLE
)
_PRESETS = ("default", "strict", "info-only")


def _oracle_category(kind: str) -> str | None:
    """Which configurable category gates *kind*, from its own catalog flags."""
    entry = REGISTRY.entries[kind]
    if entry.is_addition:
        return "addition"
    if kind in EXPECTED_INFORMATIONAL:
        return None
    return "quality_issues"


@pytest.fixture()
def inject(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ChangeKind]]:
    """Make every ``compare`` emit exactly the kinds appended to the list."""
    registry.ensure_loaded()
    kinds: list[ChangeKind] = []
    entry = _DetectorEntry(
        "test_injected_informational",
        lambda _o, _n: [
            Change(kind=k, symbol=f"ns::thing_{i}", description="injected")
            for i, k in enumerate(kinds)
        ],
        None,
        10**9,
    )
    monkeypatch.setattr(registry, "_detectors", [*registry._detectors, entry])
    yield kinds


def _compare(tmp_path: Path, *args: str) -> tuple[int, dict]:
    f = Function(
        name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC
    )
    paths = []
    for side in ("old", "new"):
        p = tmp_path / f"{side}.json"
        p.write_text(
            snapshot_to_json(
                AbiSnapshot(library="libfoo.so", version=side, functions=[f])
            ),
            encoding="utf-8",
        )
        paths.append(str(p))
    res = CliRunner().invoke(main, ["compare", *paths, "-o", "json=-", *args])
    return res.exit_code, json.loads(res.stdout)


def _config(tmp_path: Path, **levels: str) -> str:
    cfg = tmp_path / "sev.abicheck.yml"
    cfg.write_text(
        "severity:\n" + "".join(f"  {k}: {v}\n" for k, v in levels.items()),
        encoding="utf-8",
    )
    return str(cfg)


def test_catalog_membership_matches_the_adr() -> None:
    flagged = {e.kind for e in REGISTRY.entries.values() if e.is_informational}
    assert flagged == EXPECTED_INFORMATIONAL


@pytest.mark.parametrize("kind", sorted(EXPECTED_INFORMATIONAL))
@pytest.mark.parametrize("preset", [None, *_PRESETS])
def test_informational_kind_never_gates_through_the_cli(
    tmp_path: Path, inject: list[ChangeKind], kind: str, preset: str | None
) -> None:
    inject.append(ChangeKind(kind))
    args = ["--severity-preset", preset] if preset else []
    code, doc = _compare(tmp_path, *args)
    assert code == 0
    # Recorded, not disposed of (ADR-067): the finding is still reported.
    assert [c["kind"] for c in doc["changes"]] == [kind]
    assert doc["summary"]["informational"] == 1
    assert doc["summary"]["quality_issues"] == 0
    assert doc["summary"]["compatible_additions"] == 0
    if preset is not None:
        sev = doc["severity"]
        assert sev["categories"]["informational"] == {"severity": "info", "count": 1}
        assert "informational" not in sev["config"]
        assert sev["blocking_categories"] == []
        assert sev["exit_code"] == 0


@pytest.mark.parametrize(
    ("quality", "addition"),
    list(itertools.product(("error", "warning", "info"), repeat=2)),
)
def test_every_quality_addition_level_pair(
    tmp_path: Path, inject: list[ChangeKind], quality: str, addition: str
) -> None:
    """Exhaustive over the two COMPATIBLE-facing levels, mixed findings.

    One informational, one quality and one addition finding: the exit code
    is 1 exactly when the quality or addition level is ``error`` -- the
    informational finding never moves it.
    """
    inject.extend(
        [
            ChangeKind("declaration_identity_unchanged"),
            ChangeKind("visibility_leak"),
            ChangeKind("func_added"),
        ]
    )
    code, doc = _compare(
        tmp_path,
        "--config",
        _config(tmp_path, quality_issues=quality, addition=addition),
    )
    expected = 1 if "error" in (quality, addition) else 0
    assert code == expected
    assert doc["summary"]["informational"] == 1
    assert doc["summary"]["quality_issues"] == 1
    assert doc["summary"]["compatible_additions"] == 1
    assert "informational" not in doc["severity"]["blocking_categories"]


@pytest.mark.parametrize("kind", _COMPATIBLE)
def test_strict_gates_a_compatible_kind_iff_its_category_is_configurable(
    tmp_path: Path, inject: list[ChangeKind], kind: str
) -> None:
    """Every COMPATIBLE kind under ``strict``: the control half of the class.

    Non-informational COMPATIBLE kinds still exit 1 under strict -- the fix
    did not widen into "COMPATIBLE never gates".
    """
    inject.append(ChangeKind(kind))
    code, doc = _compare(tmp_path, "--severity-preset", "strict")
    expected_cat = _oracle_category(kind)
    assert code == (0 if expected_cat is None else 1)
    assert doc["severity"]["blocking_categories"] == (
        [] if expected_cat is None else [expected_cat]
    )


def test_markdown_report_discloses_it_outside_quality_issues(
    tmp_path: Path, inject: list[ChangeKind]
) -> None:
    inject.append(ChangeKind("declaration_coordinates_shifted"))
    f = Function(
        name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC
    )
    paths = []
    for side in ("old", "new"):
        p = tmp_path / f"{side}.json"
        p.write_text(
            snapshot_to_json(
                AbiSnapshot(library="libfoo.so", version=side, functions=[f])
            ),
            encoding="utf-8",
        )
        paths.append(str(p))
    res = CliRunner().invoke(
        main, ["compare", *paths, "-o", "markdown=-", "--severity-preset", "strict"]
    )
    assert res.exit_code == 0
    assert "Informational (no material change)" in res.stdout
    assert "declaration_coordinates_shifted" in res.stdout
    assert (
        "| Informational (no material change) | ℹ️ `INFO` | 1 | no exit impact |"
        in res.stdout
    )
    assert "Quality Issues ❌" not in res.stdout  # no Quality Issues section heading


# ── typed API: the whole level domain, not only the presets ──────────────────

_LEVELS = tuple(SeverityLevel)
_ALL_CONFIGS = [
    SeverityConfig(*levels) for levels in itertools.product(_LEVELS, repeat=4)
]


class _Stub:
    def __init__(self, kind: str) -> None:
        self.kind = ChangeKind(kind)


@pytest.mark.parametrize("kind", sorted(EXPECTED_INFORMATIONAL))
def test_no_severity_config_gates_an_informational_kind(kind: str) -> None:
    change = _Stub(kind)
    assert classify_change(change.kind) is IssueCategory.INFORMATIONAL
    for config in [*_ALL_CONFIGS, *SEVERITY_PRESETS.values()]:
        assert compute_exit_code([change], config) == 0
        assert gate_contribution_for_change(change, config) == 0
        gate = compute_gate_decision([change], config)
        assert not gate.blocking
        assert gate.blocking_categories == ()


def test_policy_raising_an_informational_kind_is_still_honoured() -> None:
    """Policy decides acceptance (ADR-066): a promotion to BREAKING gates."""
    from abicheck.change_registry import (
        API_BREAK_KINDS,
        BREAKING_KINDS,
        COMPATIBLE_KINDS,
        RISK_KINDS,
    )

    kind = ChangeKind("public_surface_shrank")
    kind_sets = (
        BREAKING_KINDS | {kind},
        API_BREAK_KINDS,
        COMPATIBLE_KINDS - {kind},
        RISK_KINDS,
    )
    assert classify_change(kind, kind_sets=kind_sets) is IssueCategory.ABI_BREAKING
    assert (
        compute_exit_code(
            [_Stub(kind.value)], SEVERITY_PRESETS["default"], kind_sets=kind_sets
        )
        == 4
    )


@pytest.mark.parametrize(
    ("verdict", "is_addition", "overrides"),
    [
        (Verdict.BREAKING, False, {}),
        (Verdict.API_BREAK, False, {}),
        (Verdict.COMPATIBLE_WITH_RISK, False, {}),
        (Verdict.COMPATIBLE, True, {}),
        (Verdict.API_BREAK, False, {"sdk_vendor": Verdict.COMPATIBLE}),
    ],
)
def test_catalog_rejects_an_informational_flag_that_claims_a_change(
    verdict: Verdict, is_addition: bool, overrides: dict[str, Verdict]
) -> None:
    entry = ChangeKindMeta(
        "x_probe",
        verdict,
        impact="probe",
        is_addition=is_addition,
        policy_overrides=overrides,
        entity=REGISTRY.entries["public_surface_grew"].entity,
        operation=REGISTRY.entries["public_surface_grew"].operation,
        is_informational=True,
    )
    with pytest.raises(ValueError, match="is_informational"):
        ChangeKindRegistry([entry])


def test_pr_comment_never_blames_quality_for_an_informational_finding() -> None:
    from abicheck.pr_comment_base import _finding_category

    for kind in EXPECTED_INFORMATIONAL:
        assert _finding_category("compatible", kind) == "informational"
    assert _finding_category("compatible", "visibility_leak") == "quality_issues"


# ── release PR comment: member and release-global rows ───────────────────────


def _release_report(config: dict, *, member_info: int, global_kinds: list[str]) -> dict:
    return {
        "verdict": "COMPATIBLE",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            {
                "library": "lib.so",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "risk_changes": 0,
                "compatible_additions": 0,
                "quality_issues": 0,
                "informational": member_info,
            }
        ],
        "matrix_verdict": "COMPATIBLE",
        "matrix_findings": [
            {"kind": k, "symbol": f"s{i}", "description": "x"}
            for i, k in enumerate(global_kinds)
        ],
        "severity": {"config": config, "exit_code": 0},
        "unmatched_old": [],
        "unmatched_new": [],
    }


@pytest.mark.parametrize(
    "config",
    [
        {"quality_issues": "error"},
        dict.fromkeys(
            ("abi_breaking", "potential_breaking", "quality_issues", "addition"),
            "error",
        ),
    ],
)
def test_release_pr_comment_counts_informational_as_safe_never_blocking(
    config: dict,
) -> None:
    """A member's ``informational`` count and a release-global roll-up both
    land in Safe, and neither names ``quality_issues`` as a blocker."""
    from abicheck.pr_comment import build_model

    model = build_model(
        _release_report(config, member_info=2, global_kinds=["public_surface_grew"])
    )
    assert model.counts == (0, 0, 3)
    assert "quality_issues" not in model.breaking_categories


def test_release_pr_comment_global_quality_finding_still_blocks() -> None:
    """Control: a real quality finding beside the roll-up still gates."""
    from abicheck.pr_comment import build_model

    model = build_model(
        _release_report(
            {"quality_issues": "error"},
            member_info=0,
            global_kinds=["public_surface_grew", "soname_bump_unnecessary"],
        )
    )
    assert model.counts == (1, 0, 1)
    assert model.breaking_categories == frozenset({"quality_issues"})
