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

"""Tell a header parse, ahead of time, that dependency scoping will run.

``dumper_scoping.scope_snapshot_excluding_dependencies`` drops every function
and variable declared in a dependency header, after the snapshot is built.
On a real header-AST surface that is most of the work: on Intel SVS (76
header roots) the clang parser built 93,582 functions and scoping kept
12,341. Building the rest only to discard them cost 21% of the dump's wall
time.

The parser cannot decide this on its own: whether scoping runs, and with
which roots, is chosen by the caller (``dump --include-system-declarations``
keeps everything; several ``run_dump`` callers never scope at all). So this
is opt-in. A caller that *will* scope with roots R wraps the extraction in
:func:`dependency_exclusion_scope` with the same R, and only then may a parser
consult :func:`active_dependency_predicate` to skip a declaration early. No
scope means no skipping, which is today's behaviour exactly.

Skipping is only ever a subset of what scoping drops: this predicate is
``dependency_header_predicate(R)``, and scoping's is that same predicate
widened by ``--exclude-header`` patterns
(``header_exclusions.scoping_header_predicate``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .dependency_header_roots import dependency_header_predicate

__all__ = [
    "active_dependency_predicate",
    "dependency_exclusion_scope",
    "suppress_dependency_exclusion",
]

_PREDICATE: ContextVar[Callable[[str | None], bool] | None] = ContextVar(
    "abicheck_dependency_exclusion_predicate", default=None
)


@contextmanager
def dependency_exclusion_scope(
    header_roots: Sequence[Path | str] | None,
) -> Iterator[None]:
    """Declare that the snapshot built inside this block will be scoped with
    ``resolve_dependency_scope(..., include_dependencies=False, header_roots)``.

    *header_roots* must be the exact root set that call receives.
    """
    token = _PREDICATE.set(dependency_header_predicate(header_roots))
    try:
        yield
    finally:
        _PREDICATE.reset(token)


def active_dependency_predicate() -> Callable[[str | None], bool] | None:
    """The in-scope ``is_dependency(source_header)`` predicate, or ``None``
    when no caller has declared that dependency scoping will run."""
    return _PREDICATE.get()


@contextmanager
def suppress_dependency_exclusion() -> Iterator[None]:
    """Parse with no early skip inside this block, even under a scope.

    For a hybrid dump: only the clang leg can skip, so skipping there would
    hand ``dumper_hybrid.merge_snapshots`` two legs that disagree about the
    dependency declarations it reconciles and backfills from.
    """
    token = _PREDICATE.set(None)
    try:
        yield
    finally:
        _PREDICATE.reset(token)
