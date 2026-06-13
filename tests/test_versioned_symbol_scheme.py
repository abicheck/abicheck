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

"""Tracked use-case for the *versioned-symbol naming scheme* pattern (field-eval P08).

Libraries like **ICU** embed the major version in *every* exported symbol name
(``u_strlen_75`` → ``u_strlen_78``). A routine, source-compatible upgrade then
reads as a wall of `func_removed` + `func_added` — 16 k changes for ICU 75→78 in
the field evaluation — even though almost nothing about the API actually changed.
OpenSSL/LLVM hit the same shape via GNU symbol-version nodes
(`symbol_moved_version_node`).

This test pins the **current** behaviour (no convention awareness: the whole
surface reads as removed+added → BREAKING). It is the executable spec for the
planned convention-aware mitigation (a "versioned symbol scheme" recogniser /
suppression preset): once that lands, the same input should collapse to a small,
review-able result and this test is updated to assert the reduced noise.
"""

from __future__ import annotations

import collections

from abicheck.checker import Verdict, compare
from abicheck.model import AbiSnapshot, Function, Param, Visibility

# A handful of distinct C entry points, each carrying the library major version
# as a name suffix — the ICU `u_<name>_<major>` convention.
_BASES: dict[str, list[str]] = {
    "strlen": ["char*"],
    "toupper": ["int"],
    "open": ["char*", "int"],
    "close": ["int", "int"],
    "setlocale": ["char*", "char*", "int"],
}


def _fn(name: str, ptypes: list[str]) -> Function:
    return Function(
        name=name, mangled=name, return_type="int",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(ptypes)],
        visibility=Visibility.PUBLIC,
    )


def _snap(version: str, suffix: str) -> AbiSnapshot:
    s = AbiSnapshot(library="libicuuc.so", version=version)
    s.functions = [_fn(f"u_{b}_{suffix}", pt) for b, pt in _BASES.items()]
    return s


def _kind_counts(result) -> dict[str, int]:
    return dict(collections.Counter(
        (c.kind.value if hasattr(c.kind, "value") else c.kind) for c in result.changes
    ))


def test_versioned_suffix_bump_reads_as_full_churn():
    """ICU-style `_75`→`_78` rename of the whole surface = removed+added wall."""
    old = _snap("75.1", "75")
    new = _snap("78.3", "78")
    result = compare(old, new)

    kinds = _kind_counts(result)
    n = len(_BASES)
    # Every symbol disappears and reappears under the new suffix.
    assert kinds.get("func_removed") == n, kinds
    assert kinds.get("func_added") == n, kinds
    # The recogniser emits exactly one advisory finding explaining the churn...
    assert kinds.get("versioned_symbol_scheme_detected") == 1, kinds
    # ...but it is *additive*: the artifact-proven removals still drive a BREAKING
    # verdict (authority rule). Collapsing to compatible stays an opt-in preset.
    assert result.verdict == Verdict.BREAKING


def test_identical_versioned_surface_is_no_change():
    """Guard: same suffix on both sides must NOT manufacture churn (no false break)."""
    result = compare(_snap("75.1", "75"), _snap("75.1", "75"))

    kinds = _kind_counts(result)
    assert "func_removed" not in kinds and "func_added" not in kinds, kinds
    assert result.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


# --- pure recogniser: thresholds + false-positive guards ------------------

def _ch(kind_value: str, symbol: str):
    from abicheck.checker_types import Change, ChangeKind
    return Change(kind=ChangeKind(kind_value), symbol=symbol, description="")


def test_recogniser_fires_on_majority_versioned_churn():
    from abicheck.versioned_symbol_scheme import detect_versioned_symbol_scheme
    changes = []
    for b in ("a", "b", "c", "d"):
        changes.append(_ch("func_removed", f"u_{b}_75"))
        changes.append(_ch("func_added", f"u_{b}_78"))
    out = detect_versioned_symbol_scheme(changes)
    assert out is not None
    assert out.kind.value == "versioned_symbol_scheme_detected"


def test_recogniser_silent_below_floor():
    # Only one versioned pair amid real removals → not a scheme (no false positive).
    from abicheck.versioned_symbol_scheme import detect_versioned_symbol_scheme
    changes = [
        _ch("func_removed", "u_a_75"), _ch("func_added", "u_a_78"),
        _ch("func_removed", "real_gone_1"), _ch("func_removed", "real_gone_2"),
        _ch("func_removed", "real_gone_3"),
    ]
    assert detect_versioned_symbol_scheme(changes) is None


def test_recogniser_ignores_digitless_renames():
    # Removals/additions without a numeric token are not a versioned scheme.
    from abicheck.versioned_symbol_scheme import detect_versioned_symbol_scheme
    changes = [
        _ch("func_removed", "alpha"), _ch("func_added", "beta"),
        _ch("func_removed", "gamma"), _ch("func_added", "delta"),
        _ch("func_removed", "epsilon"), _ch("func_added", "zeta"),
    ]
    assert detect_versioned_symbol_scheme(changes) is None


def test_recogniser_ignores_itanium_mangling_digits():
    # The digits in Itanium C++ ABI names are structural length/name data, not a
    # source-level versioning convention like ICU's `u_name_75` suffix.
    from abicheck.versioned_symbol_scheme import detect_versioned_symbol_scheme
    changes = [
        _ch("func_removed", "_Z4sym1"), _ch("func_added", "_Z4sym3"),
        _ch("func_removed", "_Z4sym2"), _ch("func_added", "_Z4sym4"),
        _ch("func_removed", "_Z4sym5"), _ch("func_added", "_Z4sym6"),
    ]
    assert detect_versioned_symbol_scheme(changes) is None
