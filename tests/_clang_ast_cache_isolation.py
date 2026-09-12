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

"""Cache-isolation helpers for the clang-AST backend's differential tests.

Split out of `test_clang_header_backend_integration.py` rather than trimmed
into it: `architecture/debt.yaml` carries a `no_growth` baseline for that
file, and `AGENTS.md` is explicit that the way to shrink such an entry is to
"move responsibility out to a properly-owned module, never to trim the file
to fit". These three helpers are one responsibility -- making two
differently-configured `dump()` calls genuinely independent, and proving the
second one really ran -- so they get their own owner and are reusable by any
other clang test that needs the same guarantee.

The responsibility is subtle enough to be worth its own module. A test whose
claim is "configuration A and configuration B agree" asserts nothing if B was
served A's cached result: it then compares A with itself and passes whatever B
would have done. Getting that right needs all three pieces together -- a
distinct on-disk cache root, a cleared in-process memo (a disk-cache miss
alone does not force a reparse), and an assertion that the path under
comparison actually executed.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _isolate_ast_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str = "xdg-cache"
) -> None:
    """Force a fresh on-disk AST cache dir so a prior (unpruned) run's cached
    raw JSON file can never be served back for a differently-configured call
    -- the streaming pruner only ever runs on a fresh parse, never on a
    disk-cache hit (see ``dumper_clang_streaming.py``'s module docstring).

    ``name`` is what makes two calls within *one* test genuinely independent.
    It exists because it did not: every call derived the same
    ``tmp_path / "xdg-cache"`` root, so a test that dumped twice off one
    ``tmp_path`` (pruning off, then on) pointed both runs at the same cache
    and the second was served the first run's cached raw JSON -- meaning the
    pruner never parsed anything, and an "off vs. on are equivalent"
    assertion compared the unpruned result with itself. Pass a distinct
    ``name`` per configuration; the in-process AST memo has to be cleared
    alongside it (see ``_reset_ast_memo``), since a disk-cache miss alone
    does not force a reparse.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(root))


def _reset_ast_memo() -> None:
    """Drop the in-process clang-AST memo slot.

    ``conftest.py``'s ``_isolate_ast_memo`` clears this between *tests*; a
    test that performs two differently-configured dumps needs it cleared
    between those two dumps too, for the same reason ``_isolate_ast_cache``
    needs a distinct name.
    """
    from abicheck import dumper_cache

    dumper_cache._ast_memo_slot.set(None)


class _PruneSpy:
    """Records whether the streaming pruner's loader actually ran, and what
    it reported, for the duration of one ``dump()`` call.

    An equivalence test's claim is that pruning-off and pruning-on agree;
    that claim is only meaningful if the pruning-on run really took the
    pruning path *in that same comparison*. A sibling test proving the
    pruner can engage on this repro does not establish it here -- a served
    cache hit would silently skip it.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.pruned_counts: list[int] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from abicheck import dumper_clang_errors

        real = dumper_clang_errors.load_pruned_clang_ast

        def _spy(*args: object, **kwargs: object):
            root, pruned_count = real(*args, **kwargs)
            self.calls += 1
            self.pruned_counts.append(pruned_count)
            return root, pruned_count

        monkeypatch.setattr(dumper_clang_errors, "load_pruned_clang_ast", _spy)


#: Every C++ method-shaped clang AST node kind. The streaming pruner must
#: never collapse one: a dependency base class's own methods feed
#: `dumper_clang_vtable.build_vtable`'s base-lookup recursion for a
#: project-owned derived class, so pruning one could silently corrupt a
#: *kept* class's reconstructed vtable rather than merely drop a declaration.
_METHOD_SHAPED_KINDS = frozenset(
    {"CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl", "CXXConversionDecl"}
)


def _count_kinds(node: object, kinds: frozenset[str]) -> int:
    """Count nodes of any of *kinds* anywhere in a raw clang AST tree.

    Lives here with the isolation helpers because it serves the same
    responsibility: proving what a pruning-on run actually did to the tree,
    which is the half a differential test cannot infer from its outputs.
    """
    if isinstance(node, dict):
        own = 1 if node.get("kind") in kinds else 0
        return own + sum(_count_kinds(v, kinds) for v in node.values())
    if isinstance(node, list):
        return sum(_count_kinds(v, kinds) for v in node)
    return 0
