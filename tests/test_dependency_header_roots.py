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

"""``prepare_dependency_header_roots`` hoists filesystem work, not behaviour.

:func:`abicheck.provenance.is_dependency_header` re-resolved and re-``stat``ed
its entire root set on *every* call, and
``dumper_scoping.scope_snapshot_excluding_dependencies`` asks it once per
declaration -- twice over, since it re-scans the same lists to collect
dependency types. On a real 5,769-declaration header snapshot resolving to 15
distinct declaring headers that was 0.375s of repeated ``Path.resolve``/
``is_dir``/segmentation per scoping pass, for an answer that cannot change
between calls; prepared once and memoized by distinct header it is 0.0006s.

The whole correctness claim of that change is **equivalence**: a prepared
context must answer exactly what the per-call function answers, for every
input, including the awkward ones the per-call function's own comments were
written to handle (a relative root, a directory root, a file root flat in a
bare system prefix, a file root under a project subdirectory of one, and no
roots at all). So this file tests equivalence as a *property* over a
generated cross-product of root shapes and header paths rather than
re-asserting a handful of fixed classifications -- ``AGENTS.md``'s
"primitive-level property tests" guidance, and the reason it gives: a
hand-written case only forecloses the input it names, and the failure mode
here (one root shape whose preparation diverges) is exactly the kind a fixed
example misses.

:func:`is_dependency_header` itself is now a thin wrapper over the prepared
context, so ``tests/test_provenance.py``'s existing classification tests
remain the oracle for *what* the answers are; this file only pins that
preparing changes none of them.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from abicheck.extract.dependency_header_roots import (
    dependency_header_predicate,
    is_dependency_header,
    prepare_dependency_header_roots,
)
from abicheck.provenance import is_system_header


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    """A project tree plus a fake system prefix, covering every root shape
    ``is_dependency_header``'s own comments call out."""
    proj = tmp_path / "proj" / "include"
    proj.mkdir(parents=True)
    (proj / "api.h").write_text("")
    (proj / "internal.h").write_text("")
    nested = proj / "detail"
    nested.mkdir()
    (nested / "impl.h").write_text("")

    sysroot = tmp_path / "usr" / "include"
    sysroot.mkdir(parents=True)
    (sysroot / "zlib.h").write_text("")
    syslib = sysroot / "mylib"
    syslib.mkdir()
    (syslib / "api.h").write_text("")

    return {
        "proj_dir": proj,
        "proj_file": proj / "api.h",
        "proj_nested": nested / "impl.h",
        "proj_sibling": proj / "internal.h",
        "sys_dir": sysroot,
        "sys_flat_file": sysroot / "zlib.h",
        "sys_lib_dir": syslib,
        "sys_lib_file": syslib / "api.h",
    }


def _root_sets(tree: dict[str, Path]) -> list[list[str] | None]:
    """Every root shape worth distinguishing, including multi-root sets and
    the two "no roots" spellings that take the bare-``is_system_header``
    fallback."""
    single = [
        [str(tree["proj_file"])],
        [str(tree["proj_dir"])],
        [str(tree["sys_flat_file"])],
        [str(tree["sys_lib_file"])],
        [str(tree["sys_lib_dir"])],
        [str(tree["sys_dir"])],
    ]
    pairs = [
        [str(tree["proj_file"]), str(tree["sys_lib_file"])],
        [str(tree["proj_dir"]), str(tree["sys_flat_file"])],
    ]
    return [None, [], *single, *pairs]


def _header_paths(tree: dict[str, Path]) -> list[str | None]:
    return [
        None,
        "",
        str(tree["proj_file"]),
        str(tree["proj_sibling"]),
        str(tree["proj_nested"]),
        str(tree["sys_flat_file"]),
        str(tree["sys_lib_file"]),
        "/usr/include/c++/11/string",
        "/usr/include/stdio.h",
        "/nonexistent/elsewhere/other.h",
    ]


class TestPreparedContextMatchesPerCallClassification:
    def test_equivalence_over_every_root_shape_and_header(
        self, tree: dict[str, Path]
    ) -> None:
        """The core invariant, over the full cross-product rather than a
        sample -- batched so a failure names every disagreeing pair at once
        instead of stopping at the first."""
        disagreements = []
        for roots, header in itertools.product(_root_sets(tree), _header_paths(tree)):
            prepared = prepare_dependency_header_roots(roots).is_dependency(header)
            per_call = is_dependency_header(header, roots)
            if prepared != per_call:
                disagreements.append((roots, header, per_call, prepared))
        assert not disagreements, (
            f"{len(disagreements)} disagreements: {disagreements[:5]}"
        )

    def test_the_cross_product_is_not_vacuously_uniform(
        self, tree: dict[str, Path]
    ) -> None:
        """Vacuity guard on the oracle above: an implementation answering a
        constant would satisfy the equivalence test in full, so pin that the
        generated inputs really do produce both answers."""
        answers = {
            is_dependency_header(header, roots)
            for roots, header in itertools.product(
                _root_sets(tree), _header_paths(tree)
            )
        }
        assert answers == {True, False}

    def test_relative_root_resolves_against_the_working_directory(
        self, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative root is absolutized against the *current* working
        directory, which preparation must capture the same way the per-call
        function did -- the behaviour ``_absolutize_header_root`` exists for."""
        monkeypatch.chdir(tree["proj_dir"].parent.parent)
        roots = ["include/api.h"]
        for header in _header_paths(tree):
            assert prepare_dependency_header_roots(roots).is_dependency(
                header
            ) == is_dependency_header(header, roots)

    @pytest.mark.parametrize("roots", [None, []])
    def test_no_roots_takes_the_bare_system_header_fallback(
        self, roots: list[str] | None, tree: dict[str, Path]
    ) -> None:
        """With no roots the prepared context must reproduce
        ``is_system_header`` exactly, not a classification against an empty
        public set (which would answer ``False`` for everything)."""
        prepared = prepare_dependency_header_roots(roots)
        assert prepared.system_header_fallback is True
        for header in _header_paths(tree):
            assert prepared.is_dependency(header) == is_system_header(header)


class TestPreparationHappensOncePerRootSet:
    def test_classifying_many_headers_resolves_the_roots_once(
        self, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The work-count claim, asserted deterministically rather than by
        timing: preparing once and classifying N headers must ``is_dir()``
        the root set once, not once (twice, really) per header."""
        calls = {"n": 0}
        real_is_dir = Path.is_dir

        def counting_is_dir(self: Path) -> bool:
            calls["n"] += 1
            return real_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", counting_is_dir)

        roots = [str(tree["proj_file"]), str(tree["sys_lib_file"])]
        headers = _header_paths(tree)

        calls["n"] = 0
        prepared = prepare_dependency_header_roots(roots)
        for header in headers:
            prepared.is_dependency(header)
        prepared_calls = calls["n"]

        calls["n"] = 0
        for header in headers:
            is_dependency_header(header, roots)
        per_call_calls = calls["n"]

        assert prepared_calls == len(roots)
        assert per_call_calls > prepared_calls


class TestDependencyHeaderPredicate:
    """The memoized closure the batch callers share.

    It replaces a hand-rolled memo that had been written separately at more
    than one call site, so its contract is pinned here once rather than
    re-derived per caller.
    """

    def test_it_agrees_with_the_per_call_function(self, tree: dict[str, Path]) -> None:
        for roots in _root_sets(tree):
            is_dep = dependency_header_predicate(roots)
            for header in _header_paths(tree):
                assert is_dep(header) == is_dependency_header(header, roots)

    def test_repeated_questions_classify_each_header_once(
        self, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The memo's whole point, asserted by work count rather than timing:
        asking about one header N times must classify it once."""
        import abicheck.extract.dependency_header_roots as module

        calls: list[str | None] = []
        real = module.DependencyHeaderRoots.is_dependency

        def counting(self: Any, source_header: str | None) -> bool:
            calls.append(source_header)
            return real(self, source_header)

        monkeypatch.setattr(module.DependencyHeaderRoots, "is_dependency", counting)

        is_dep = dependency_header_predicate([str(tree["proj_file"])])
        headers = [str(tree["proj_file"]), str(tree["sys_flat_file"])]
        for _ in range(25):
            for header in headers:
                is_dep(header)

        assert sorted(calls) == sorted(headers)

    def test_the_root_set_is_prepared_once_for_the_whole_batch(
        self, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The *hoisting* half, which the memo alone would hide.

        A predicate that re-prepared the root set inside every cache miss
        still classifies each header once, so the work count above passes
        unchanged -- caught only by counting preparations separately. This is
        ``AGENTS.md``'s "an untested key is not an unnecessary key" lesson in
        its own shape: two mechanisms here (prepare-once and memoize-per-
        header) each need their own assertion, because either one alone makes
        the other's test pass.
        """
        import abicheck.extract.dependency_header_roots as module

        preparations: list[object] = []
        real = module.prepare_dependency_header_roots

        def counting(roots: object) -> object:
            preparations.append(roots)
            return real(roots)  # type: ignore[arg-type]

        monkeypatch.setattr(module, "prepare_dependency_header_roots", counting)

        is_dep = module.dependency_header_predicate([str(tree["proj_file"])])
        for header in _header_paths(tree) * 3:
            is_dep(header)

        assert len(preparations) == 1

    def test_a_false_answer_is_cached_too(self, tree: dict[str, Path]) -> None:
        """Regression guard on the ``cache.get(...) is None`` idiom this memo
        shares with its siblings: ``False`` is a legitimate cached answer and
        must not be mistaken for a miss. A memo that re-computes every
        ``False`` still returns correct answers, so only a work count catches
        it -- which is why this is asserted separately from correctness."""
        import abicheck.extract.dependency_header_roots as module

        calls: list[str | None] = []
        real = module.DependencyHeaderRoots.is_dependency

        def counting(self: Any, source_header: str | None) -> bool:
            calls.append(source_header)
            return real(self, source_header)

        original = module.DependencyHeaderRoots.is_dependency
        module.DependencyHeaderRoots.is_dependency = counting  # type: ignore[method-assign]
        try:
            is_dep = dependency_header_predicate([str(tree["proj_file"])])
            project_header = str(tree["proj_sibling"])
            assert is_dep(project_header) is False
            assert is_dep(project_header) is False
            assert is_dep(project_header) is False
        finally:
            module.DependencyHeaderRoots.is_dependency = original  # type: ignore[method-assign]

        assert calls == [project_header]


_COLLIDING_BASENAMES = ("allocator.h", "core.h", "string.h", "config.h", "memory")
_SYSTEM_LAYOUTS = (
    "/usr/include/c++/13/bits/{b}",
    "/usr/include/{b}",
    "/usr/include/fmt/{b}",
    "/usr/local/include/dep/{b}",
    "/opt/env/lib/gcc/x86_64-conda-linux-gnu/14.3.0/include/{b}",
    "/opt/env/share/castxml/clang/include/{b}",
)


class TestBasenameCollisionIsNotOwnership:
    """A system header sharing a root's *basename* is still a dependency.

    Regression from a real SVS scan: roots ``svs/core/allocator.h`` and
    ``svs/lib/saveload/core.h`` made ``classify_origin``'s basename-only
    fallback read libstdc++'s ``bits/allocator.h`` and fmt's ``core.h`` as
    the library's own, so 3,164 toolchain functions survived dependency
    scoping. Enumerated over basename x system layout x root shape; the
    oracle is the root-independent ``is_system_header`` heuristic, not the
    predicate under test.
    """

    @pytest.mark.parametrize("basename", _COLLIDING_BASENAMES)
    def test_colliding_system_header_stays_a_dependency(
        self, tmp_path: Path, basename: str
    ):
        pkg = tmp_path / "proj" / "include" / "pkg"
        (pkg / "sub").mkdir(parents=True)
        own = pkg / basename
        own.write_text("")
        sibling = pkg / "sub" / "other.h"
        sibling.write_text("")
        for roots in ([own], [pkg], [own, sibling]):
            is_dep = dependency_header_predicate([str(r) for r in roots])
            for layout in _SYSTEM_LAYOUTS:
                system_header = layout.format(b=basename)
                assert is_system_header(system_header), system_header  # vacuity
                assert is_dep(system_header), (roots, system_header)
                assert is_dependency_header(system_header, roots), system_header
            # The library's own headers are never dependencies.
            assert not is_dep(str(own))
            assert not is_dep(str(sibling))
