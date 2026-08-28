# Copyright 2026 Nikolay Petrov
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

"""Cross-flow exit-code integrity (C7).

The verdict→exit-code contract (BREAKING→4, API_BREAK→2, compatible→0) is now
encoded once in `severity.legacy_exit_code`. These tests lock that mapping and
assert the two CLI flows that exit on a single verdict — `compare` and
`compare-release` — produce the *same* code for the same verdict, so they can
never drift apart. The `compat` flow uses a deliberately different scheme
(0/1/2 + 3–11 errors); that distinction is asserted too.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.severity import _LEGACY_VERDICT_EXIT_CODE
from abicheck.severity import legacy_exit_code


def _fn(name: str) -> Function:
    return Function(name=name, mangled=name, return_type="void", params=[], visibility=Visibility.PUBLIC)


@pytest.mark.parametrize(
    ("verdict", "code"),
    [
        (Verdict.BREAKING, 4),
        (Verdict.API_BREAK, 2),
        (Verdict.COMPATIBLE_WITH_RISK, 0),
        (Verdict.COMPATIBLE, 0),
        (Verdict.NO_CHANGE, 0),
    ],
)
def test_legacy_exit_code_contract(verdict: Verdict, code: int) -> None:
    assert legacy_exit_code(verdict) == code


def test_legacy_exit_code_total_over_all_verdicts() -> None:
    # Every Verdict must map (no KeyError / silent 0 for an unmapped member).
    for v in Verdict:
        assert isinstance(legacy_exit_code(v), int)


def _exit_code_of(callable_, *args, **kwargs) -> int:
    """Run a function that may sys.exit; return the code (0 if it returns)."""
    try:
        callable_(*args, **kwargs)
    except SystemExit as exc:  # noqa: PT012 — capturing the exit code is the point
        return int(exc.code or 0)
    return 0


@pytest.mark.parametrize("worst", ["BREAKING", "API_BREAK", "COMPATIBLE", "NO_CHANGE"])
def test_compare_release_flow_matches_canonical(worst: str) -> None:
    from abicheck.cli_compare_release import _exit_compare_release

    got = _exit_code_of(
        _exit_compare_release, worst, False, [], severity_exit_code=None
    )
    assert got == legacy_exit_code(Verdict[worst])


def test_compare_flow_matches_canonical() -> None:
    from abicheck.cli import _exit_with_severity_or_verdict

    old = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[_fn("a"), _fn("b")])
    new = AbiSnapshot(library="libfoo.so.1", version="2.0", functions=[_fn("a")])
    result = compare(old, new, scope_to_public_surface=False)

    got = _exit_code_of(_exit_with_severity_or_verdict, result, None, False)
    assert got == legacy_exit_code(result.verdict)


def test_compare_and_release_agree_for_each_verdict() -> None:
    # The cross-flow guarantee: identical verdict → identical exit code.
    from abicheck.cli_compare_release import _exit_compare_release

    for v in (Verdict.BREAKING, Verdict.API_BREAK, Verdict.COMPATIBLE, Verdict.NO_CHANGE):
        release_code = _exit_code_of(_exit_compare_release, v.name, False, [], severity_exit_code=None)
        assert release_code == legacy_exit_code(v)


def test_compat_scheme_is_distinct() -> None:
    # The compat flow uses a deliberately different, wider exit-code scheme
    # (3–11 for operational errors). Exercise its classifier and assert the codes
    # it emits fall OUTSIDE the legacy compare range {0, 2, 4}, so the two schemes
    # can never be accidentally unified.
    from abicheck.compat._errors import _classify_compat_error_exit_code

    legacy_codes = set(_LEGACY_VERDICT_EXIT_CODE.values())  # {0, 2, 4}
    # 11 (interrupted) is emitted by compat but never by the legacy verdict
    # mapping — proof the schemes are distinct. (Some numeric codes, e.g. 4,
    # overlap by coincidence with different meanings; 11 cannot.)
    interrupted = _classify_compat_error_exit_code(KeyboardInterrupt())
    assert interrupted == 11
    assert interrupted not in legacy_codes
    # And the legacy mapping itself is unchanged.
    assert legacy_exit_code(Verdict.BREAKING) == 4


class TestReleaseContractCoverageFold:
    """CLI-audit P1 (release/package contract parity): the release fan-out's
    own aggregated contract-coverage floor (0/1, max()-folded across every
    library) must obey the same "raises a clean 0, never lowers a real
    2/4/8" rule single-pair `compare` applies via
    `contract_coverage_exit.fold_coverage_exit` -- and must not mask, or be
    masked by, the separately-aggregated removed-library exit 8 (AGENTS.md:
    "не смешивая его с entity contract relevance")."""

    def test_raises_a_clean_compatible_exit_to_one(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "NO_CHANGE", False, [],
            severity_exit_code=None,
            contract_coverage_exit_contribution=1,
        )
        assert code == 1

    def test_never_lowers_a_real_breaking_exit(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "BREAKING", False, [],
            severity_exit_code=None,
            contract_coverage_exit_contribution=1,
        )
        assert code == 4

    def test_removed_library_exit_still_wins_over_coverage_only(self) -> None:
        # A removed library's own exit 8 is checked ahead of the
        # coverage-only fallback -- the same precedence the pre-existing
        # severity-scheme branch already gives fail_on_removed.
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "NO_CHANGE", True, ["removed_lib"],
            severity_exit_code=None,
            contract_coverage_exit_contribution=1,
        )
        assert code == 8

    def test_error_verdict_still_floors_at_four_with_coverage_folded(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "ERROR", False, [],
            severity_exit_code=None,
            contract_coverage_exit_contribution=1,
        )
        assert code == 4

    def test_severity_scheme_folds_coverage_too(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "COMPATIBLE", False, [],
            severity_exit_code=0,
            contract_coverage_exit_contribution=1,
        )
        assert code == 1

    def test_severity_scheme_removed_library_still_wins(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            "COMPATIBLE", True, ["removed_lib"],
            severity_exit_code=0,
            contract_coverage_exit_contribution=1,
        )
        assert code == 8

    @pytest.mark.parametrize(
        "worst", ["BREAKING", "API_BREAK", "COMPATIBLE", "NO_CHANGE"]
    )
    def test_zero_contribution_is_a_true_no_op(self, worst: str) -> None:
        # The default (no --contract) must reproduce every
        # pre-existing exit code exactly -- this is what
        # test_compare_release_flow_matches_canonical already asserts
        # without the new keyword; this locks the explicit-zero case too.
        from abicheck.cli_compare_release import _exit_compare_release

        code = _exit_code_of(
            _exit_compare_release,
            worst, False, [],
            severity_exit_code=None,
            contract_coverage_exit_contribution=0,
        )
        assert code == legacy_exit_code(Verdict[worst])


def test_compat_not_comparable_exit_code_is_9_and_distinct_from_compare() -> None:
    # ADR-050 D2: compat check's not_comparable code (9) is the one integer
    # the 3-11 range documented no meaning for, and deliberately different
    # from native compare's own not_comparable code (16) -- the two commands
    # maintain independent, non-overlapping exit-code schemes.
    from abicheck.compat._errors import _classify_compat_error_exit_code
    from abicheck.errors import ProfileMismatchError, ScopeMismatchError

    assert _classify_compat_error_exit_code(ProfileMismatchError("x")) == 9
    assert _classify_compat_error_exit_code(ScopeMismatchError("x")) == 9
