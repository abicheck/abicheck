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

"""A dump's recorded dependency scope matches what its parse was allowed
to skip, however dumps nest.

Bug class: a full-surface dump (``include_dependencies=True``) running inside
an enclosing scoped dump inherited the enclosing parse-time dependency skip.
The CLI's ``hybrid`` path is exactly that shape -- it recurses into
``run_dump(include_dependencies=True)`` once per backend under the outer
``compare`` scope -- so its clang leg skipped dependency declarations the
castxml leg kept, the merge reconciled two disagreeing legs, and each leg was
stamped ``dependency_scope="full"`` over a filtered surface.

The oracle is the request itself, not the implementation: for every nesting
of an outer scope (none, scoped, full) around an inner request (scoped,
full), the inner extraction must see exactly what the *inner* request
allows.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from unittest.mock import patch

import pytest
from _dump_format_fakes import fake_format_adapter

from abicheck.extract.dependency_exclusion import active_dependency_predicate
from abicheck.extract.headers.clang.streaming import streaming_prune_suppressed
from abicheck.model import AbiSnapshot
from abicheck.service import run_dump
from abicheck.workflows.dump.formats import NativeExtractRequest
from abicheck.workflows.run_dump_scope import wrap_run_dump_with_dependency_scope

_PROJECT_ROOT = "/proj/include"
_PROJECT_HEADER = "/proj/include/api.h"
_SYSTEM_HEADER = "/usr/include/stdio.h"


def _observed() -> tuple[bool, bool | None, bool | None]:
    """(pruner off, project header skippable, system header skippable)."""
    pred = active_dependency_predicate()
    if pred is None:
        return (streaming_prune_suppressed(), None, None)
    return (streaming_prune_suppressed(), pred(_PROJECT_HEADER), pred(_SYSTEM_HEADER))


def _make_dump(seen: list[tuple[bool, bool | None, bool | None]]):
    def _uncached(headers: list[Path] | None = None) -> AbiSnapshot:
        seen.append(_observed())
        return AbiSnapshot(library="lib", version="1")

    return wrap_run_dump_with_dependency_scope(_uncached)


#: What an extraction may skip, as a pure function of the *inner* request.
#: ``True`` keeps the full surface: no pruner, no skip predicate at all.
#: ``False`` may skip system headers but never the project's own headers.
_EXPECTED: dict[bool, tuple[bool | None, bool | None, bool | None]] = {
    True: (True, None, None),
    False: (None, False, True),  # pruner state unconstrained when scoping
}


@pytest.mark.parametrize(
    "outer,inner",
    list(itertools.product((None, False, True), (False, True))),
    ids=lambda v: {None: "no-outer", False: "scoped", True: "full"}.get(v, str(v)),
)
def test_inner_extraction_sees_only_its_own_scope(
    outer: bool | None, inner: bool
) -> None:
    seen: list[tuple[bool, bool | None, bool | None]] = []
    dump = _make_dump(seen)

    def _inner() -> AbiSnapshot:
        return dump(headers=[Path(_PROJECT_HEADER)], include_dependencies=inner)

    if outer is None:
        snap = _inner()
    else:
        outer_dump = wrap_run_dump_with_dependency_scope(lambda headers=None: _inner())
        snap = outer_dump(headers=[Path(_PROJECT_ROOT)], include_dependencies=outer)
        seen = seen[:1]

    prune_off, project_skip, system_skip = seen[0]
    want_prune, want_project, want_system = _EXPECTED[inner]
    if want_prune is not None:
        assert prune_off is want_prune
    assert (project_skip, system_skip) == (want_project, want_system)
    assert isinstance(snap, AbiSnapshot)


class TestCliHybridLegsKeepTheFullSurface:
    """The CLI ``hybrid`` path (``service.run_dump``) must parse both legs
    with no dependency skip, whatever scope the caller asked for -- the same
    guarantee ``dumper.dump``'s hybrid path already gives
    (``test_dependency_exclusion_scope.test_hybrid_dump_runs_both_legs_without_the_skip``).
    """

    @pytest.mark.parametrize("include_dependencies", [False, True])
    def test_both_legs_parse_unscoped(
        self, tmp_path: Path, include_dependencies: bool
    ) -> None:
        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "include" / "api.h"
        header.parent.mkdir()
        header.write_text("int f(void);\n", encoding="utf-8")
        legs: list[tuple[str, tuple[bool, bool | None, bool | None]]] = []

        def _fake_dump_elf(request: NativeExtractRequest) -> AbiSnapshot:
            assert request.compile is not None
            frontend = request.compile.frontend
            legs.append((frontend, _observed()))
            return AbiSnapshot(
                library="lib", version="1", from_headers=True, ast_producer=frontend
            )

        with (
            fake_format_adapter("elf", side_effect=_fake_dump_elf),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=lambda snap, *_a, **_k: snap,
            ),
        ):
            result = run_dump(
                so,
                "elf",
                headers=[header],
                header_backend="hybrid",
                include_dependencies=include_dependencies,
            )

        assert sorted(frontend for frontend, _ in legs) == ["castxml", "clang"]
        for frontend, (prune_off, project_skip, system_skip) in legs:
            assert prune_off is True, frontend
            assert (project_skip, system_skip) == (None, None), frontend
        assert result.ast_producer == "hybrid"
        assert result.dependency_scope == (
            "full" if include_dependencies else "filtered"
        )
