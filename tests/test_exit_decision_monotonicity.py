# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Exhaustive monotonicity of every ``compare`` exit-code resolver.

``test_exit_precedence_matrix.py`` pins individual cells and
``test_exit_rank_table.py`` the dominant-axis table. This module states the
*shape* of the exit code as properties, enumerated over the whole finite input
lattice rather than sampled:

* **Monotone.** Making one input worse (a worse verdict, a new finding, an
  operational failure, a coverage or completeness gap, a stricter severity
  level) never lowers the exit code. The one documented exception -- the legacy
  scheme's exit ``8`` yielding to a verdict/operational ``2``/``4``
  (``docs/reference/exit-codes.md``, "``compare`` with directories/packages") --
  is asserted as an exception, in exactly the cells it covers.
* **Closed codomain.** Each resolver returns only the codes its table
  documents; ``64`` (usage error) is never produced by any of them.
* **Oracle agreement.** Every cell equals a re-statement of the documented
  precedence written out below from ``docs/reference/exit-codes.md`` -- the
  category exit table, the legacy verdict table and the release ``8``/``16``
  rules -- with no call into the helpers under test.

The audit that motivated this module ran ad hoc at commit 509cb0d; these are
its durable form. Default marker: the whole enumeration is a few thousand
pure-function calls.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from abicheck.checker import Change, DiffResult
from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    Verdict,
    compute_verdict,
)
from abicheck.cli import main
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.exit_decision import resolve_exit_decision
from abicheck.policy.exit_decision_precedence import (
    GATE_MODES,
    resolve_compare_exit_decision_with_abort_axes,
    resolve_gate_mode_exit_code,
    resolve_scoped_exit_code,
)
from abicheck.policy.release_exit_decision import (
    resolve_release_exit_decision_for_report,
)
from abicheck.policy.severity import (
    SeverityConfig,
    SeverityLevel,
    compute_exit_code,
)

USAGE_ERROR = 64

# ---------------------------------------------------------------------------
# The oracle: docs/reference/exit-codes.md, restated literally.
# ---------------------------------------------------------------------------

#: "Severity-aware exit codes": the exit code each category contributes when
#: it is configured ``error``.
_CATEGORY_EXIT = {
    "abi_breaking": 4,
    "potential_breaking": 2,
    "quality_issues": 1,
    "addition": 1,
}

#: "Legacy exit codes": verdict -> exit.
_LEGACY_EXIT = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}

#: One representative kind per policy class, with the category the docs place
#: that class in (BREAKING -> abi_breaking; API_BREAK and RISK ->
#: potential_breaking; a compatible addition -> addition; any other compatible
#: change -> quality_issues). ``test_representatives_are_in_their_class``
#: guards the choice against a reclassification.
_REPRESENTATIVES: dict[ChangeKind, tuple[frozenset[ChangeKind], str]] = {
    ChangeKind.FUNC_REMOVED: (BREAKING_KINDS, "abi_breaking"),
    ChangeKind.CONSTANT_REMOVED: (API_BREAK_KINDS, "potential_breaking"),
    ChangeKind.PARAM_RENAMED: (RISK_KINDS, "potential_breaking"),
    ChangeKind.FUNC_ADDED: (COMPATIBLE_KINDS, "addition"),
    ChangeKind.VAR_DEPRECATED_ADDED: (COMPATIBLE_KINDS, "quality_issues"),
}
_KINDS = tuple(_REPRESENTATIVES)

_LEVEL_RANK = {SeverityLevel.INFO: 0, SeverityLevel.WARNING: 1, SeverityLevel.ERROR: 2}
_CATEGORIES = ("abi_breaking", "potential_breaking", "quality_issues", "addition")

#: All 81 severity configurations.
_CONFIGS = tuple(
    SeverityConfig(**dict(zip(_CATEGORIES, levels, strict=True)))
    for levels in itertools.product(list(SeverityLevel), repeat=4)
)

#: Every multiset of at most two representative findings (1 + 5 + 15 = 21).
_MULTISETS: tuple[tuple[ChangeKind, ...], ...] = tuple(
    combo
    for size in range(3)
    for combo in itertools.combinations_with_replacement(_KINDS, size)
)


def _oracle_severity_exit(config: SeverityConfig, kinds: tuple[ChangeKind, ...]) -> int:
    return max(
        (
            _CATEGORY_EXIT[_REPRESENTATIVES[k][1]]
            for k in kinds
            if getattr(config, _REPRESENTATIVES[k][1]) is SeverityLevel.ERROR
        ),
        default=0,
    )


def _changes(kinds: tuple[ChangeKind, ...]) -> list[Change]:
    return [
        Change(kind=k, symbol=f"sym_{i}", description=f"{k.value} #{i}")
        for i, k in enumerate(kinds)
    ]


def _stricter(config: SeverityConfig) -> Iterator[SeverityConfig]:
    """Every config one category-level step stricter than *config*."""
    ladder = (SeverityLevel.INFO, SeverityLevel.WARNING, SeverityLevel.ERROR)
    for cat in _CATEGORIES:
        rank = _LEVEL_RANK[getattr(config, cat)]
        if rank < 2:
            yield dataclasses.replace(config, **{cat: ladder[rank + 1]})


def test_representatives_are_in_their_class() -> None:
    for kind, (kind_set, _category) in _REPRESENTATIVES.items():
        assert kind in kind_set, kind


# ---------------------------------------------------------------------------
# 1. compute_exit_code: 81 configs x 21 multisets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config", _CONFIGS, ids=lambda c: c.describe().replace("\n", ";")
)
def test_compute_exit_code_matches_category_table_and_is_monotone(
    config: SeverityConfig,
) -> None:
    codes = {kinds: compute_exit_code(_changes(kinds), config) for kinds in _MULTISETS}
    for kinds, code in codes.items():
        assert code == _oracle_severity_exit(config, kinds), kinds
        assert code in {0, 1, 2, 4}
    # Adding a finding never lowers the code.
    for kinds in _MULTISETS:
        if len(kinds) == 2:
            continue
        for extra in _KINDS:
            grown = tuple(sorted((*kinds, extra), key=_KINDS.index))
            assert codes[grown] >= codes[kinds], (kinds, extra)
    # Raising any category one level never lowers the code.
    for stricter in _stricter(config):
        for kinds in _MULTISETS:
            assert compute_exit_code(_changes(kinds), stricter) >= codes[kinds], (
                stricter,
                kinds,
            )


# ---------------------------------------------------------------------------
# 2. The scalar compare ladder: scheme x findings x evidence x budget
# ---------------------------------------------------------------------------

#: ``None`` is the legacy scheme; the rest are severity maps from the lattice's
#: corners and its default.
_SCHEMES: tuple[SeverityConfig | None, ...] = (
    None,
    SeverityConfig(),
    _CONFIGS[0],
    _CONFIGS[-1],
    SeverityConfig(
        abi_breaking=SeverityLevel.WARNING,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.INFO,
        addition=SeverityLevel.ERROR,
    ),
)


def _scalar_oracle(
    scheme: SeverityConfig | None,
    kinds: tuple[ChangeKind, ...],
    *,
    evidence: bool,
    budget: bool,
) -> int:
    if evidence:  # "Evidence-contract error" -- above budget overflow.
        return 7
    if budget:
        return 5
    if scheme is None:
        return _LEGACY_EXIT[compute_verdict(_changes(kinds))]
    return _oracle_severity_exit(scheme, kinds)


def _scalar_code(
    scheme: SeverityConfig | None,
    kinds: tuple[ChangeKind, ...],
    *,
    evidence: bool,
    budget: bool,
) -> int:
    changes = _changes(kinds)
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes,
        verdict=compute_verdict(changes),
        evidence_contract_error=evidence,
        budget_overflow=budget,
    )
    gate = EffectiveGate.from_severity(scheme)
    return resolve_compare_exit_decision_with_abort_axes(result, gate).code


@pytest.mark.parametrize(
    "scheme",
    _SCHEMES,
    ids=lambda s: "legacy" if s is None else s.describe().replace("\n", ";"),
)
def test_scalar_ladder_matches_oracle_and_is_monotone(
    scheme: SeverityConfig | None,
) -> None:
    flags = list(itertools.product((False, True), repeat=2))
    codes = {
        (kinds, ev, bud): _scalar_code(scheme, kinds, evidence=ev, budget=bud)
        for kinds in _MULTISETS
        for ev, bud in flags
    }
    for (kinds, ev, bud), code in codes.items():
        assert code == _scalar_oracle(scheme, kinds, evidence=ev, budget=bud)
        assert code in {0, 1, 2, 4, 5, 7}
    for (kinds, ev, bud), code in codes.items():
        if not ev:
            assert codes[(kinds, True, bud)] >= code
        if not bud:
            assert codes[(kinds, ev, True)] >= code
        if len(kinds) < 2:
            for extra in _KINDS:
                grown = tuple(sorted((*kinds, extra), key=_KINDS.index))
                assert codes[(grown, ev, bud)] >= code, (kinds, extra, ev, bud)


# ---------------------------------------------------------------------------
# 3. The ordinary fold (policy/exit_decision.py): every 0/1 floor x compat
# ---------------------------------------------------------------------------

_FLOOR_AXES = (
    "contract_coverage_contribution",
    "analysis_assurance_contribution",
    "incomplete_scope_contribution",
    "no_comparison_completed_contribution",
    "additions_review_contribution",
    "unused_rules_contribution",
    "unconfirmed_contribution",
)


def test_ordinary_fold_is_max_and_monotone() -> None:
    """A floor raises a clean ``0`` to ``1`` and never lowers a ``2``/``4``."""
    cells = {}
    for compat, op in itertools.product((0, 1, 2, 4), (0, 4)):
        for floors in itertools.product((0, 1), repeat=len(_FLOOR_AXES)):
            decision = resolve_exit_decision(
                compatibility_contribution=compat,
                operational_error_contribution=op,
                **dict(zip(_FLOOR_AXES, floors, strict=True)),
            )
            assert decision.code == max(compat, op, *floors)
            cells[(compat, op, floors)] = decision.code
    for (compat, op, floors), code in cells.items():
        for i, value in enumerate(floors):
            if value == 0:
                raised = floors[:i] + (1,) + floors[i + 1 :]
                assert cells[(compat, op, raised)] >= code
        if op == 0:
            assert cells[(compat, 4, floors)] >= code


# ---------------------------------------------------------------------------
# 4. The release ladder (policy/release_exit_decision.py)
# ---------------------------------------------------------------------------

_VERDICTS = ("NO_CHANGE", "COMPATIBLE", "API_BREAK", "BREAKING")
#: ``None`` = legacy scheme; an int = the aggregated severity-aware code.
_RELEASE_SCHEMES: tuple[int | None, ...] = (None, 0, 1, 2, 4)
#: none / a member that reported ERROR / a failed member from the scope record.
_OPERATIONAL = ("none", "error_member", "failed_member")
#: gate.fail_on_removed_library off / on with nothing proven removed / on with
#: a proven removal.
_REMOVAL = ("off", "on_none_removed", "on_removed")


@dataclasses.dataclass(frozen=True)
class _ReleaseCell:
    scheme: int | None
    verdict: str
    operational: str
    not_comparable: bool
    removal: str
    coverage: int
    incomplete_scope: int
    no_comparison: int
    evidence: int


_RELEASE_AXES: dict[str, tuple[object, ...]] = {
    "verdict": _VERDICTS,
    "operational": _OPERATIONAL,
    "not_comparable": (False, True),
    "removal": _REMOVAL,
    "coverage": (0, 1),
    "incomplete_scope": (0, 1),
    "no_comparison": (0, 1),
    "evidence": (0, 7),
}


def _release_cells() -> Iterator[_ReleaseCell]:
    for scheme in _RELEASE_SCHEMES:
        for values in itertools.product(*_RELEASE_AXES.values()):
            yield _ReleaseCell(scheme, *values)  # type: ignore[arg-type]


def _release_oracle(cell: _ReleaseCell) -> int:
    """``docs/reference/exit-codes.md``'s release table, in its own order."""
    if cell.not_comparable:  # 16 "takes precedence over every other outcome"
        return 16
    base = _LEGACY_EXIT[Verdict[cell.verdict]] if cell.scheme is None else cell.scheme
    operational = 0 if cell.operational == "none" else 4
    if cell.removal == "on_removed":
        # Severity: 8 "takes precedence over 0/1/2/4". Legacy: "emitted only
        # when no API/ABI verdict exit 2/4 and no operational ERROR exit 4
        # already applies".
        if cell.scheme is not None or (base == 0 and operational == 0):
            return 8
    if cell.evidence:
        return 7
    return max(
        base, operational, cell.coverage, cell.incomplete_scope, cell.no_comparison
    )


def _release_code(cell: _ReleaseCell) -> int:
    members: list[dict[str, object]] = [
        {
            "library": "libok.so",
            "verdict": cell.verdict,
            "evidence_contract_error_contribution": cell.evidence,
        }
    ]
    if cell.operational == "error_member":
        members.append({"library": "libbroken.so", "verdict": "ERROR"})
    if cell.not_comparable:
        members.append({"library": "libnc.so", "verdict": "not_comparable"})
    # The release rollup ranks not_comparable > ERROR > every real verdict.
    if cell.not_comparable:
        worst = "not_comparable"
    elif cell.operational == "error_member":
        worst = "ERROR"
    else:
        worst = cell.verdict
    return resolve_release_exit_decision_for_report(
        worst,
        cell.removal != "off",
        ["libgone.so"] if cell.removal == "on_removed" else [],
        cell.scheme,
        cell.coverage,
        members,
        incomplete_scope_contribution=cell.incomplete_scope,
        no_comparison_completed_contribution=cell.no_comparison,
        failed_member_contribution=4 if cell.operational == "failed_member" else 0,
    ).code


@pytest.fixture(scope="module")
def release_codes() -> dict[_ReleaseCell, int]:
    return {cell: _release_code(cell) for cell in _release_cells()}


def test_release_ladder_matches_documented_precedence(
    release_codes: dict[_ReleaseCell, int],
) -> None:
    mismatches = [
        (cell, code, _release_oracle(cell))
        for cell, code in release_codes.items()
        if code != _release_oracle(cell)
    ]
    assert not mismatches, mismatches[:5]
    assert set(release_codes.values()) <= {0, 1, 2, 4, 7, 8, 16}
    assert USAGE_ERROR not in set(release_codes.values())


def _worse_neighbours(cell: _ReleaseCell) -> Iterator[_ReleaseCell]:
    """Each cell one step worse on exactly one axis (scheme held fixed, except
    the severity code itself, which is an ordered axis inside its scheme)."""
    for axis, ladder in _RELEASE_AXES.items():
        index = ladder.index(getattr(cell, axis))
        for worse in ladder[index + 1 :]:
            yield dataclasses.replace(cell, **{axis: worse})
    if cell.scheme is not None:
        for worse_code in (c for c in (1, 2, 4) if c > cell.scheme):
            yield dataclasses.replace(cell, scheme=worse_code)


def _is_documented_legacy_removal_yield(
    before: _ReleaseCell, after: _ReleaseCell, codes: dict[_ReleaseCell, int]
) -> bool:
    """Legacy scheme only: a proven removal's 8 yields once a verdict 2/4 or an
    operational ERROR 4 applies -- the one place the release code drops."""
    return (
        before.scheme is None
        and after.scheme is None
        and codes[before] == 8
        and after.removal == "on_removed"
        and (_LEGACY_EXIT[Verdict[after.verdict]] > 0 or after.operational != "none")
    )


def test_release_ladder_is_monotone(release_codes: dict[_ReleaseCell, int]) -> None:
    exceptions = 0
    for cell, code in release_codes.items():
        for worse in _worse_neighbours(cell):
            if release_codes[worse] >= code:
                continue
            assert _is_documented_legacy_removal_yield(cell, worse, release_codes), (
                cell,
                code,
                worse,
                release_codes[worse],
            )
            assert release_codes[worse] in {2, 4, 7}
            exceptions += 1
    # The exception is real (the documented asymmetry exists), not vacuous.
    assert exceptions > 0


def test_release_exit_8_dominance(release_codes: dict[_ReleaseCell, int]) -> None:
    for cell, code in release_codes.items():
        if cell.not_comparable:
            assert code == 16
            continue
        proven = cell.removal == "on_removed"
        if not proven:
            # Neither a disabled gate nor an empty proven set ever reaches 8.
            assert code != 8
        elif cell.scheme is not None:
            assert code == 8
        else:
            blocked = (
                _LEGACY_EXIT[Verdict[cell.verdict]] > 0 or cell.operational != "none"
            )
            assert (code == 8) is not blocked


# ---------------------------------------------------------------------------
# 5. Consumer-scoped code and check-target's gate-mode composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    _SCHEMES,
    ids=lambda s: "legacy" if s is None else s.describe().replace("\n", ";"),
)
def test_scoped_exit_code_matches_oracle_and_is_monotone(
    scheme: SeverityConfig | None,
) -> None:
    host = DiffResult(old_version="1", new_version="2", library="l", changes=[])
    codes = {}
    for kinds in _MULTISETS:
        changes = _changes(kinds)
        verdict = compute_verdict(changes)
        for missing in (False, True):
            code = resolve_scoped_exit_code(
                verdict,
                changes,
                host,
                scheme,
                "strict_abi",
                None,
                has_missing_contract=missing,
            )
            if scheme is None:
                expected = _LEGACY_EXIT[verdict]
            else:
                expected = _oracle_severity_exit(scheme, kinds)
                if missing and scheme.abi_breaking is SeverityLevel.ERROR:
                    expected = max(expected, _CATEGORY_EXIT["abi_breaking"])
            assert code == expected, (kinds, missing)
            codes[(kinds, missing)] = code
    for (kinds, missing), code in codes.items():
        if not missing:
            assert codes[(kinds, True)] >= code
        if len(kinds) < 2:
            for extra in _KINDS:
                grown = tuple(sorted((*kinds, extra), key=_KINDS.index))
                assert codes[(grown, missing)] >= code


@pytest.mark.parametrize("gate_mode", GATE_MODES)
def test_gate_mode_pass_fail_is_monotone(gate_mode: str) -> None:
    """ADR-047 §7: an operational error always fails (exit ``1``); ``local``
    is the analysis exit unchanged; ``deferred``/``advisory`` never fail on a
    verdict. So *fails* is monotone in both inputs, and ``64`` never appears."""
    for real in (0, 1, 2, 4, 7, 8, 16):
        for operational in (False, True):
            code = resolve_gate_mode_exit_code(
                gate_mode, real_exit_code=real, operational_error=operational
            )
            if operational:
                expected = 1
            elif gate_mode == "local":
                expected = real
            else:
                expected = 0
            assert code == expected
            assert code != USAGE_ERROR
            fails_without = resolve_gate_mode_exit_code(
                gate_mode, real_exit_code=real, operational_error=False
            )
            assert bool(code) >= bool(fails_without)


# ---------------------------------------------------------------------------
# 6. 64 is the usage-error code and nothing else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["compare"],
        ["compare", "--no-such-flag", "a.json", "b.json"],
        ["compare", "/nonexistent/old.so", "/nonexistent/new.so"],
        ["no-such-command"],
    ],
)
def test_invalid_invocation_exits_usage_error(argv: list[str]) -> None:
    assert CliRunner().invoke(main, argv).exit_code == USAGE_ERROR
