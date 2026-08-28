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

"""Unit-test mirror of the ``fact-detector-misuse`` AI-readiness check
(``scripts/fact_detector_misuse.py``, registered by
``scripts/check_ai_readiness.py``) — ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

The check ERRORs on any ``==``/``!=`` comparison, anywhere under
``abicheck/``, where at least one side is recognizably ``Fact[T]``-typed —
a `<attr>_fact` field access (``bases_fact``/``virtual_bases_fact``/
``vtable_fact``/``vptr_offset_bits_fact``/``is_va_list_fact``) or a
``Fact(...)``/``Fact.<classmethod>(...)`` constructor call. Unlike the
sibling ``fact-field-readers`` check, this one ships with **no baseline**:
zero such comparisons exist under ``abicheck/`` today (verified by running
the real scan), so any hit is an unconditional error, not an allowlisted
one. This file pins that the real repository is clean and that the
detection logic itself actually catches the misuse pattern
``abicheck/model/fact.py``'s own docstring describes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.fact_detector_misuse import (  # noqa: E402
    FACT_FIELD_NAMES,
    check_fact_detector_misuse,
    fact_equality_misuse_sites,
)


def test_no_violation_in_real_repo() -> None:
    """The real repository has zero `Fact[T]` equality-misuse sites under
    `abicheck/` — this check has no baseline, so any hit at all is an
    error; this pins that the check is clean against the actual tree."""
    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert errors == [], "Fact[T] equality misuse:\n" + "\n".join(errors)


class TestFactEqualityMisuseSites:
    """Direct tests on the AST-walking primitive, independent of the real
    repository tree — pins the detection logic's own contract."""

    @pytest.mark.parametrize("attr", sorted(FACT_FIELD_NAMES))
    def test_detects_a_comparison_between_two_fact_attrs(self, attr: str) -> None:
        src = f"def f(a, b):\n    return a.{attr} == b.{attr}\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_not_equal_too(self) -> None:
        src = "def f(a, b):\n    return a.vtable_fact != b.vtable_fact\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_comparison_against_a_fact_constructor_call(self) -> None:
        src = "def f(rec):\n    return rec.bases_fact == Fact.present([])\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_comparison_against_a_bare_fact_call(self) -> None:
        src = "def f(rec, status):\n    return rec.bases_fact == Fact(status)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_each_pair_in_a_chained_comparison(self) -> None:
        """`a == b == c` is two adjacent comparisons, not one — both should
        be caught if the relevant operand is Fact-typed."""
        src = "def f(a, b, c):\n    return a.vtable_fact == b.vtable_fact == c\n"
        tree = ast.parse(src, filename="x.py")
        # Both (a.vtable_fact == b.vtable_fact) and (b.vtable_fact == c)
        # involve a Fact-typed operand, so both pairs are reported — same
        # ast.Compare node, so both sites share the node's own location.
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11), (2, 11)]

    def test_ignores_identity_comparison(self) -> None:
        """`is`/`is not` (e.g. checking whether a Fact sibling was ever
        supplied, `model/fact.py`'s own bridge pattern) is not the misuse
        this check exists to catch — only `==`/`!=`."""
        src = "def f(rec):\n    return rec.bases_fact is None\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_ordinary_comparison(self) -> None:
        src = "def f(rec):\n    return rec.size_bits == 64\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_call_to_an_unrelated_function_named_like_a_classmethod(
        self,
    ) -> None:
        """`SomethingElse.present(x) == y` must not match merely because
        the *method* name happens to collide — only a call on the bare
        name `Fact` itself counts."""
        src = "def f(x, y):\n    return SomethingElse.present(x) == y\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_local_alias(self) -> None:
        """`old_fact = old.bases_fact` then `old_fact == new_fact` -- both
        operands are bare `ast.Name`s, invisible to attribute/call matching
        alone (Codex review: an ordinary local-variable refactor must not
        launder this misuse past the gate)."""
        src = (
            "def f(old, new_fact):\n"
            "    old_fact = old.bases_fact\n"
            "    return old_fact == new_fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert sites == [(3, 11)]

    def test_detects_a_comparison_through_a_chained_alias(self) -> None:
        """`first = rec.bases_fact; second = first; second == other` --
        `second`'s own RHS is a bare `ast.Name` (`first`), not directly
        Fact-typed, so a single pass over assignments alone would stop at
        `first` (Codex review: a second ordinary local-variable refactor
        must not launder past the alias-tracking fix either)."""
        src = (
            "def f(rec, other):\n"
            "    first = rec.bases_fact\n"
            "    second = first\n"
            "    return second == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

    def test_detects_a_comparison_through_an_annotated_local_assignment(
        self,
    ) -> None:
        """`old_fact: Fact[list[str]] = old.bases_fact` is an `ast.AnnAssign`,
        a distinct node type the original `ast.Assign`-only candidate
        collection never matched at all (Codex review: the ordinary
        annotated-assignment spelling must not bypass the gate)."""
        src = (
            "def f(old, other):\n"
            "    old_fact: Fact[list[str]] = old.bases_fact\n"
            "    return old_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_bare_annotated_local_with_no_value(self) -> None:
        """The annotation alone is an unconditional signal, mirroring the
        function-parameter case -- `old_fact: Fact[list[str]]` with no RHS
        at all is still Fact-typed."""
        src = "def f(other):\n    old_fact: Fact[list[str]]\n    return old_fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_pep_604_optional_fact_annotated_parameter(self) -> None:
        """`def f(value: Fact[int] | None, other): return value == other`
        -- a detector helper commonly declares an optional Fact-typed
        parameter this way, and only the outermost bare/subscript shape
        was ever recognized (Codex review, fresh evidence)."""
        src = "def f(value: Fact[int] | None, other):\n    return value == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_an_optional_subscript_fact_annotated_parameter(self) -> None:
        """The identical wrapping via `Optional[Fact[int]]`."""
        src = "def f(value: Optional[Fact[int]], other):\n    return value == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_union_subscript_fact_annotated_parameter(self) -> None:
        """The identical wrapping via `Union[Fact[int], None]`."""
        src = (
            "def f(value: Union[Fact[int], None], other):\n    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_ignores_an_ordinary_optional_non_fact_annotated_parameter(
        self,
    ) -> None:
        """Negative control: an ordinary `int | None` parameter must not
        be treated as Fact-typed merely because it's wrapped in a union."""
        src = "def f(value: int | None, other):\n    return value == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_closure_over_an_outer_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in an outer function, then `def
        inner(): return fact == other` -- `inner`'s own qualname has no
        assignment of its own establishing `fact`, but it's a real,
        visible closure variable there (Codex review: a nested function
        must inherit its enclosing scope's aliases)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        return fact == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_inherited_alias_does_not_leak_into_an_unrelated_sibling(
        self,
    ) -> None:
        """The closure-inheritance fix must not widen back into the
        already-fixed sibling-leakage case: a name aliased in `f` still
        must not make an unrelated same-named parameter in an unrelated,
        non-nested sibling function `g` read as Fact-typed."""
        src = (
            "def f(rec):\n"
            "    x = rec.bases_fact\n"
            "    return x\n"
            "def g(x, y):\n"
            "    return x == y\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_closure_over_a_class_nested_method(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in an outer function, then `class C:
        def method(self): return fact == other` -- Python still closes
        `method` over `fact` right through the intervening class body
        (Codex review: a class scope between the alias and its use must
        not break the closure-inheritance fix)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    class C:\n"
            "        def method(self):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 19)]


def test_check_reports_a_new_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real Fact-equality comparison in a throwaway
    `abicheck/`-shaped tree fails the gate."""
    import scripts.fact_detector_misuse as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_misuse.py").write_text(
        "def f(rec, other):\n    return rec.bases_fact == other.bases_fact\n"
    )

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert len(errors) == 1
    assert "a_new_misuse.py:2" in errors[0]


def test_check_is_silent_for_clean_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: ordinary `.status`-based unwrapping raises no
    finding — this check must not fire on the *correct* usage pattern."""
    import scripts.fact_detector_misuse as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "clean.py").write_text(
        "def f(rec):\n"
        "    if rec.bases_fact.is_present:\n"
        "        return rec.bases_fact.value\n"
        "    return None\n"
    )

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert errors == []
