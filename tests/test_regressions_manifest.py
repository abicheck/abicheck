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

"""Integrity checks for ``tests/regressions/manifest.py`` (Phase 1 of
``docs/contribute/plans/bug-class-regression-testing.md``).

Mirrors `test_canonical_finding_id_completeness.py`'s discipline for
`canonical_identity_contract.py`: a registry is only as trustworthy as the
mechanism that checks it stays honest. Every property here is something a
hand-added `BugClass` entry could get wrong silently (a typo'd path, a
duplicate id, an empty ``seed_tests``) without any of these checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.regressions.manifest import BUG_CLASSES, BugClass, all_ids, get

REPO_ROOT = Path(__file__).resolve().parent.parent


def _decorator_call_args(source: str, marker_name: str) -> list[str | None]:
    """Every `@pytest.mark.<marker_name>(...)` call's raw argument text in
    *source* — `None` in the list for a bare, unparenthesized occurrence.

    Depth-counted rather than a `.*?` regex, since a non-greedy regex over
    a call's arguments stops at the *first* `)` and would misread a nested
    call (e.g. `reason=some_helper(x)`) as the marker's own closing paren.
    A literal-substring search rather than `\\bmarker\\b`-style regex, with
    an explicit next-character guard, so searching for `skip` does not
    match the unrelated `skipif` marker (`skip` is a prefix of `skipif`).
    """
    marker = f"pytest.mark.{marker_name}"
    results: list[str | None] = []
    idx = 0
    while True:
        pos = source.find(marker, idx)
        if pos == -1:
            break
        after = pos + len(marker)
        if after < len(source) and (source[after].isalnum() or source[after] == "_"):
            # A longer identifier (e.g. `skipif` while searching for `skip`)
            # — not a real match of this marker.
            idx = after
            continue
        j = after
        while j < len(source) and source[j] in " \t":
            j += 1
        if j < len(source) and source[j] == "(":
            depth = 0
            k = j
            while k < len(source):
                if source[k] == "(":
                    depth += 1
                elif source[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            results.append(source[j + 1 : k])
            idx = k + 1
        else:
            results.append(None)
            idx = after
    return results


def _canary_strictness_violation(source: str) -> str | None:
    """`None` if *source* honors the "fails loudly" canary contract
    (`KnownGap.canary_test`'s own docstring); otherwise a one-sentence
    reason it doesn't (Codex review, PR #885 — this repository has no
    `xfail_strict` ini option, so a bare `@pytest.mark.xfail` stays green
    on an unexpected pass, and a `@pytest.mark.skip`'d test never executes
    at all — neither can detect the tracked residual closing or widening).

    Only rejects the two decorator patterns that are *unconditionally*
    wrong regardless of the test body: a non-strict/bare `@pytest.mark.
    xfail` and any `@pytest.mark.skip`. It does **not** accept — nor
    specifically endorse — a conditional runtime `pytest.xfail(...)` call
    as an equivalent to `strict=True`: a second review round (fresh
    evidence) found that once the guarding condition stops being met, such
    a call is simply never reached, and whatever follows just runs to an
    ordinary PASS with no XPASS-style signal at all. Static source
    scanning cannot tell a canary built that way apart from one that
    correctly asserts the residual's own bound (the legitimate pattern for
    "no xfail/skip decorator at all") — that distinction is semantic, not
    syntactic, so it's on the author, not this check, the same way this
    function already can't verify a bare assertion genuinely encodes the
    gap rather than something unrelated.
    """
    if _decorator_call_args(source, "skip"):
        return "uses @pytest.mark.skip, which never executes and so cannot fail loudly"
    for args in _decorator_call_args(source, "xfail"):
        if args is None or "strict=True" not in args.replace(" ", "").replace("\n", ""):
            return (
                "uses a non-strict @pytest.mark.xfail — an unexpected pass "
                "(XPASS) stays green with no `xfail_strict` ini option set; "
                "use `strict=True` instead"
            )
    return None


class TestRegistryShape:
    def test_registry_is_non_empty(self) -> None:
        assert len(BUG_CLASSES) > 0

    def test_every_entry_is_a_bug_class(self) -> None:
        assert all(isinstance(bc, BugClass) for bc in BUG_CLASSES)

    def test_ids_are_unique(self) -> None:
        ids = [bc.id for bc in BUG_CLASSES]
        assert len(ids) == len(set(ids)), f"duplicate BugClass id(s) in {ids}"

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_id_is_dotted_and_non_empty(self, bug_class: BugClass) -> None:
        assert bug_class.id, "BugClass.id must not be empty"
        assert "." in bug_class.id, (
            f"{bug_class.id!r} should be a dotted id (category.name), "
            "matching every other entry's convention"
        )

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_invariant_is_stated(self, bug_class: BugClass) -> None:
        assert bug_class.invariant.strip(), (
            f"{bug_class.id}: invariant must be a real sentence, not empty"
        )

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_traces_to_at_least_one_fix(self, bug_class: BugClass) -> None:
        # A class with no fixed_by is a hypothesis, not a registered
        # escape-history entry — every class here traces back to a real
        # merged fix (see the class's own `fixed_by`).
        assert bug_class.fixed_by, f"{bug_class.id}: fixed_by must be non-empty"
        assert all(isinstance(n, int) and n > 0 for n in bug_class.fixed_by)

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_has_at_least_one_seed_test(self, bug_class: BugClass) -> None:
        # This is the one property that keeps this registry from
        # degrading into exactly what it exists to prevent: a class
        # description with no executable test backing it.
        assert bug_class.seed_tests, (
            f"{bug_class.id}: a BugClass with no seed_tests is prose, not "
            "a registry entry — either name a real test or leave this "
            "class as an AGENTS.md 'Known gaps' paragraph instead"
        )


def _is_collected_test_path(rel_path: str) -> bool:
    """Whether `rel_path` matches the shape pytest's default `testpaths =
    ["tests"]` / `test_*.py` collection actually picks up: a real `.py`
    file, named `test_*.py`, located under `tests/` (at any depth).

    Deliberately a naming/location check, not a live `pytest --collect-only`
    invocation — the registry only ever names files that already exist in
    this repo's own test tree, so this is enough to catch a stale path or a
    path pointing at a support/data module (e.g.
    `tests/canonical_identity_contract.py`, which real tests *import* but
    which pytest itself never collects) without paying a subprocess per
    registry entry (Codex review, PR #885 — the previous version of this
    predicate, `path.name.startswith("test_") or path.parent.name !=
    "tests"`, was true for almost any path and accepted exactly the support
    modules this check exists to reject).

    Resolves both sides before checking containment (Codex review,
    fresh evidence, same PR): `Path.relative_to()` is a purely lexical
    check that never collapses `..` or follows a symlink, so an
    unresolved `rel_path` like `tests/../agent-evals/tasks/.../
    hidden_tests/test_foo.py` satisfied the old `relative_to(tests/)`
    check while naming a file the root `testpaths = ["tests"]` suite
    never collects.
    """
    path = REPO_ROOT / rel_path
    if not path.is_file() or path.suffix != ".py" or not path.name.startswith("test_"):
        return False
    try:
        path.resolve().relative_to((REPO_ROOT / "tests").resolve())
    except ValueError:
        return False
    return True


class TestIsCollectedTestPathBoundary:
    """Direct tests of `_is_collected_test_path`'s own containment check —
    per this repo's own bug-class discipline (the point of this whole PR),
    the regression here is the *class* (a `tests/../...` traversal escaping
    the `tests/` boundary via a purely lexical `relative_to()`), not only
    the one reported example (Codex review, PR #885)."""

    def test_a_real_hidden_eval_fixture_outside_tests_is_rejected(self) -> None:
        # A real file (not a hypothetical): a test_*.py hidden eval fixture
        # that lexically satisfies `tests/../...`.relative_to("tests") but
        # resolves outside the tests/ tree the root suite actually collects.
        traversal = (
            "tests/../agent-evals/tasks/add-change-kind-small/"
            "hidden_tests/test_type_nodiscard_detection.py"
        )
        assert (REPO_ROOT / traversal).is_file(), "fixture for this test moved/renamed"
        assert not _is_collected_test_path(traversal)

    def test_an_ordinary_tests_path_is_accepted(self) -> None:
        assert _is_collected_test_path("tests/test_bugfix_test_contract.py")

    def test_a_nested_tests_path_is_accepted(self) -> None:
        # tests/regressions/manifest.py's own sibling — real, nested.
        assert _is_collected_test_path("tests/test_regressions_manifest.py")


class TestRegisteredTestPathsExist:
    """Every path this registry names must resolve to a real, collectible
    test file — a stale or typo'd path here is worse than no entry at all,
    since it reads as verified coverage that doesn't exist."""

    @pytest.mark.parametrize(
        "bug_class",
        BUG_CLASSES,
        ids=lambda bc: bc.id,
    )
    def test_seed_tests_exist_and_are_collectible(self, bug_class: BugClass) -> None:
        for rel_path in bug_class.seed_tests:
            assert _is_collected_test_path(rel_path), (
                f"{bug_class.id}: {rel_path} does not resolve to a real, "
                "pytest-collected tests/**/test_*.py file — point at the "
                "test that exercises this class, not a support/data module"
            )

    @pytest.mark.parametrize(
        "bug_class",
        BUG_CLASSES,
        ids=lambda bc: bc.id,
    )
    def test_known_gap_canaries_exist(self, bug_class: BugClass) -> None:
        for gap in bug_class.known_gaps:
            assert gap.description.strip(), (
                f"{bug_class.id}: known_gaps entry must describe the residual gap"
            )
            assert gap.reference.strip(), (
                f"{bug_class.id}: known_gaps entry must name a reference "
                f"(issue/PR/plan section): {gap.description}"
            )
            if gap.canary_test is None:
                # An untracked-by-canary residual is honest (see
                # `KnownGap.canary_test`'s own docstring) — nothing further
                # to check.
                continue
            assert _is_collected_test_path(gap.canary_test), (
                f"{bug_class.id}: known_gaps canary does not resolve to a "
                f"real, pytest-collected test: {gap.canary_test} "
                f"({gap.description})"
            )
            source = (REPO_ROOT / gap.canary_test).read_text(encoding="utf-8")
            violation = _canary_strictness_violation(source)
            assert violation is None, (
                f"{bug_class.id}: known_gaps canary {gap.canary_test} {violation} "
                f"({gap.description}) — see KnownGap.canary_test's own docstring"
            )


class TestCanaryStrictnessViolation:
    """Direct tests of `_canary_strictness_violation` — per this repo's own
    bug-class discipline, the property being enforced ("fails loudly on an
    unexpected pass or on the residual widening") is what's tested, not one
    hand-picked example (Codex review, PR #885: no `BugClass` entry
    currently sets a real `canary_test`, so without these direct tests the
    strictness contract would be enforced by code nothing exercises)."""

    def test_a_strict_xfail_is_accepted(self) -> None:
        source = (
            "@pytest.mark.xfail(reason='tracked gap', strict=True)\ndef test_x(): ...\n"
        )
        assert _canary_strictness_violation(source) is None

    def test_a_bare_xfail_is_rejected(self) -> None:
        source = "@pytest.mark.xfail\ndef test_x(): ...\n"
        assert _canary_strictness_violation(source) is not None

    def test_a_non_strict_xfail_is_rejected(self) -> None:
        source = "@pytest.mark.xfail(reason='tracked gap')\ndef test_x(): ...\n"
        assert _canary_strictness_violation(source) is not None

    def test_strict_false_is_rejected(self) -> None:
        source = "@pytest.mark.xfail(reason='tracked gap', strict=False)\ndef test_x(): ...\n"
        assert _canary_strictness_violation(source) is not None

    def test_a_skip_is_rejected_even_with_a_reason(self) -> None:
        source = "@pytest.mark.skip(reason='not implemented yet')\ndef test_x(): ...\n"
        assert _canary_strictness_violation(source) is not None

    def test_skipif_is_not_confused_with_skip(self) -> None:
        """`skip` is a literal prefix of `skipif` — a naive substring search
        would misfire here."""
        source = (
            "@pytest.mark.skipif(sys.platform == 'win32', reason='posix only')\n"
            "def test_x(): ...\n"
        )
        assert _canary_strictness_violation(source) is None

    def test_a_plain_assertion_test_is_accepted(self) -> None:
        """No xfail/skip decorator at all — a canary that currently passes,
        asserting the residual's *bound* rather than its absence, is a
        legitimate "equivalent executable assertion" per the docstring."""
        source = "def test_x():\n    assert some_bound_still_holds()\n"
        assert _canary_strictness_violation(source) is None

    def test_a_conditional_runtime_xfail_is_not_flagged_but_is_not_endorsed(
        self,
    ) -> None:
        """This shape is NOT a reliable strict canary — once `fixed_yet()`
        starts returning `True`, the `pytest.xfail(...)` call is never
        reached and `assert real_behavior()` passing is an ordinary PASS,
        not an XPASS, so nothing alerts (Codex review, PR #885, second
        round). `_canary_strictness_violation` still doesn't flag it: the
        call is neither `@pytest.mark.skip` nor `@pytest.mark.xfail`, and
        static scanning can't distinguish this shape from a genuine bound
        assertion — this test pins that known limitation rather than
        endorsing the pattern (see the function's own docstring)."""
        source = (
            "def test_x():\n"
            "    if not fixed_yet():\n"
            "        pytest.xfail('tracked gap')\n"
            "    assert real_behavior()\n"
        )
        assert _canary_strictness_violation(source) is None

    def test_nested_call_in_xfail_reason_does_not_confuse_paren_matching(self) -> None:
        """A `.*?` regex over the call's arguments would stop at the first
        `)` — the nested `helper(x)` call's own — and misread the marker as
        already closed, missing the real `strict=True` that follows."""
        source = (
            "@pytest.mark.xfail(reason=helper(x), strict=True)\ndef test_x(): ...\n"
        )
        assert _canary_strictness_violation(source) is None

    def test_multiple_xfail_markers_all_require_strict(self) -> None:
        source = (
            "@pytest.mark.xfail(strict=True)\n"
            "def test_a(): ...\n"
            "\n"
            "@pytest.mark.xfail(reason='no strict here')\n"
            "def test_b(): ...\n"
        )
        assert _canary_strictness_violation(source) is not None


class TestLookupHelpers:
    def test_get_returns_the_registered_entry(self) -> None:
        for bug_class in BUG_CLASSES:
            assert get(bug_class.id) is bug_class

    def test_get_raises_key_error_on_unknown_id(self) -> None:
        with pytest.raises(KeyError):
            get("not.a.registered.class")

    def test_all_ids_matches_the_registry(self) -> None:
        assert all_ids() == tuple(bc.id for bc in BUG_CLASSES)
