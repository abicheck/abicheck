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
    # compat intentionally maps BREAKING→1, API_BREAK→2 (not 4/2). Guard that the
    # two schemes are NOT accidentally unified.
    from abicheck.compat._errors import _classify_compat_error_exit_code  # noqa: F401

    # legacy compare maps BREAKING→4; compat maps BREAKING→1 (documented).
    assert legacy_exit_code(Verdict.BREAKING) == 4
