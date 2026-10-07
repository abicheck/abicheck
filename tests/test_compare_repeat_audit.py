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

"""Whole-package same-argument repeat-call ratchet.

``tests/test_compare_cost_budgets.py`` pins repeats of a short, reviewed
list of expensive functions. This gate covers **every** first-party function
(except dunders and the memo machinery): one cold ``compare()`` per synthetic
workload runs under ``scripts/audit_repeated_calls.py``'s fingerprinting
hook, and each function's same-argument repeat count -- plus their total --
may not rise above ``perf_repeat_baseline.json``. A function absent from the
baseline counts as recorded at 0, so a newly-introduced repeat in any helper
fails even when the total fell elsewhere. A total *below* the baseline fails
too, so an improvement is locked in. Re-record with
``python scripts/audit_repeated_calls.py --write-repeat-baseline``.

The measurement runs in a child interpreter: process-wide caches warmed by
other tests in this worker would otherwise hide repeats a cold run makes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_repeated_calls import (  # noqa: E402
    REPEAT_AUDIT_N,
    REPEAT_BASELINE_FILE,
    _repeat_audit_includes,
    audit_repeated_calls,
    compare_repeat_audit,
    measure_repeat_audit_subprocess,
    repeat_site_key,
)

_HINT = "re-record with `python scripts/audit_repeated_calls.py --write-repeat-baseline` and say why in the PR"


def test_whole_package_repeat_calls_do_not_exceed_baseline() -> None:
    baseline = json.loads(REPEAT_BASELINE_FILE.read_text(encoding="utf-8"))
    assert baseline["n"] == REPEAT_AUDIT_N
    assert baseline["total"] == sum(baseline["functions"].values())
    measured = measure_repeat_audit_subprocess()
    assert measured, "the audit observed no first-party calls at all"
    errors, improvements = compare_repeat_audit(measured, baseline)
    assert not errors, (
        "same-argument repeats grew:\n  "
        + "\n  ".join(errors[:40])
        + f"\nfix the repeat (a memo with a sound lifetime), or {_HINT}"
    )
    # The baseline is recorded on Linux (the canonical CI platform). Other
    # OSes skip platform-specific paths and can legitimately count fewer
    # repeats (windows-latest measured 13 fewer, main run 37645089922), so
    # the lock-in-an-improvement direction is only enforced on Linux;
    # growth is enforced everywhere.
    if sys.platform.startswith("linux"):
        assert not improvements, (
            f"{improvements[0]} -- an improvement; lock it in: {_HINT}"
        )


def test_comparison_flags_a_new_function_and_a_grown_total() -> None:
    baseline = {"total": 10, "functions": {"a.py(f)": 10}}
    errors, _ = compare_repeat_audit({"a.py(f)": 4, "a.py(g)": 1}, baseline)
    assert errors == ["a.py(g): 1 same-argument repeat call(s), baseline 0"]
    errors, _ = compare_repeat_audit({"a.py(f)": 11}, baseline)
    assert errors[0].startswith("total: 11")
    assert any(e.startswith("a.py(f): 11") for e in errors)
    errors, improvements = compare_repeat_audit({"a.py(f)": 3}, baseline)
    assert not errors and improvements == [
        "total: 3 same-argument repeat calls, baseline 10"
    ]


def test_site_key_drops_line_numbers() -> None:
    assert repeat_site_key("abicheck/x.py:12(C.m)") == "abicheck/x.py(C.m)"
    assert repeat_site_key("abicheck/x.py:9(<lambda>)") == "abicheck/x.py(<lambda>)"


def test_dunders_and_memo_machinery_are_excluded() -> None:
    assert not _repeat_audit_includes("abicheck/x.py:1(C.__init__)")
    assert not _repeat_audit_includes("abicheck/model/execution_cache.py:3(memoized)")
    assert _repeat_audit_includes("abicheck/x.py:1(C.method)")
    assert _repeat_audit_includes("abicheck/x.py:1(_private)")


def test_fingerprinting_sees_repeats_by_value_and_identity_not_equality() -> None:
    """Non-vacuity of the oracle the gate rests on: a first-party function
    called on the same plain value or the same object is a repeat; on an
    equal-but-distinct object it is not."""
    from abicheck.model.int_spelling import canonical_int_spelling

    a, b = ["x"], ["x"]

    def run() -> None:
        for _ in range(3):
            canonical_int_spelling("unsigned int")
        canonical_int_spelling("long")
        for obj in (a, a, b):
            repr(obj)

    rows = {repeat_site_key(r.function): r.calls for r in audit_repeated_calls(run)}
    assert rows == {"abicheck/model/int_spelling.py(canonical_int_spelling)": 3}
