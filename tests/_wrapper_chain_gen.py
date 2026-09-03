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

"""Shared AST wrapper-chain generator for the ``extraction.ast_wrapper_chain_
traversal`` bug class (``tests/regressions/manifest.py``).

Not a test module itself -- a leaf helper, mirroring this directory's own
convention for shared, non-``test_``-prefixed support code (see
``tests/CLAUDE.md``'s "Helpers" section: ``_strict_process.py``,
``_canonical_lane.py``, ``_workflow_exec.py``). Nothing here is collected by
pytest; every test file that needs a generated wrapper-chain node imports
these builders instead of hand-rolling its own, so the generator's own shape
(and any future fix to it) is shared across every "unwrap until X" helper
this bug class covers, not maintained as several independently-drifting
copies.

The bug this generator targets (Phase 2 of
``docs/contribute/plans/bug-class-regression-testing.md``, generalizing
#839): clang's JSON AST folds a semantic fact -- an evaluated constant, a
literal value, an initializer's identity -- onto exactly ONE node along a
chain of semantics-preserving single-child "wrapper" expressions
(``ImplicitCastExpr``, ``ConstantExpr``, ``ParenExpr``, ...), and *which*
node varies by what the underlying expression actually is. A correct
extractor must give the same answer regardless of which wrapper nodes -- how
many, which kinds, in what order -- sit between the declaration and the fact
it's after; a naive implementation that only checks the outermost node and
the fully-unwrapped leaf silently drops a fact folded onto an intermediate
node (exactly what shipped, and was fixed, in #839).

Every builder here returns node objects by IDENTITY (the same ``dict``
instances threaded through the chain), so a caller can assert e.g. ``unwrap
(chain) is leaf`` rather than a value-equality check that could pass by
coincidence.
"""

from __future__ import annotations

from typing import Any

#: A non-wrapper leaf kind carrying no value of its own -- the "pure alias"
#: shape (e.g. a ``DeclRefExpr`` naming another enumerator/constant with no
#: independently-folded value): every real chain terminates in something
#: like this when nothing further down carries a fact.
NON_LITERAL_LEAF_KIND = "DeclRefExpr"


def build_wrapper_chain(
    kinds: list[str],
    *,
    value_at: int | None = None,
    value: Any = None,
    leaf_kind: str = NON_LITERAL_LEAF_KIND,
    leaf_extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a single-child wrapper chain (outermost first) over a leaf.

    Returns ``(root, leaf)`` -- the outermost node to feed a function under
    test, and the leaf node object itself (by identity) so a caller can
    assert an unwrap reached exactly it.

    *value_at* (``0`` == outermost, ``len(kinds)`` == the leaf) optionally
    folds ``value`` onto the node at that position, mirroring where clang
    itself is observed to fold a fact -- never assumed to be the outermost
    or innermost node, since that assumption is exactly what #839 got wrong.
    """
    node: dict[str, Any] = {"kind": leaf_kind, **(leaf_extra or {})}
    if value_at == len(kinds):
        node["value"] = value
    leaf = node
    for i in range(len(kinds) - 1, -1, -1):
        wrapper: dict[str, Any] = {"kind": kinds[i], "inner": [node]}
        if value_at == i:
            wrapper["value"] = value
        node = wrapper
    return node, leaf


def chain_with_ambiguous_branch(
    kinds: list[str], branch_at: int, leaf_kind: str = NON_LITERAL_LEAF_KIND
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A chain identical to :func:`build_wrapper_chain` except the wrapper
    at *branch_at* has TWO children instead of one -- a real, if unusual,
    shape (clang emits more than one ``inner`` child for some wrapper kinds
    in edge cases) that an unwrap must stop at rather than guess through.

    Returns ``(root, stopping_node)`` -- *stopping_node* is the mutated
    wrapper itself (by identity): an unwrap that stops on ambiguity must
    return exactly this node, never descend into either branch."""
    root, _ = build_wrapper_chain(kinds, leaf_kind=leaf_kind)
    cur = root
    for _ in range(branch_at):
        cur = cur["inner"][0]
    # Give the branching node a sibling child so len(inner) != 1.
    cur["inner"] = [cur["inner"][0], {"kind": leaf_kind}]
    return root, cur


def chain_with_missing_inner(
    kinds: list[str], missing_at: int, leaf_kind: str = NON_LITERAL_LEAF_KIND
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A chain where the wrapper at *missing_at* carries no ``inner`` key at
    all -- a malformed/truncated AST fragment an unwrap must stop at
    cleanly, not crash on. Returns ``(root, stopping_node)``, as above."""
    root, _ = build_wrapper_chain(kinds, leaf_kind=leaf_kind)
    cur = root
    for _ in range(missing_at):
        cur = cur["inner"][0]
    del cur["inner"]
    return root, cur


def chain_with_non_dict_child(
    kinds: list[str], bad_at: int, leaf_kind: str = NON_LITERAL_LEAF_KIND
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A chain where the wrapper at *bad_at*'s sole ``inner`` entry is not a
    dict (e.g. a bare string) -- every real production filter drops
    non-dict children before counting, so this must degrade the same way a
    missing/empty ``inner`` does, never raise ``AttributeError``. Returns
    ``(root, stopping_node)``, as above."""
    root, _ = build_wrapper_chain(kinds, leaf_kind=leaf_kind)
    cur = root
    for _ in range(bad_at):
        cur = cur["inner"][0]
    cur["inner"] = ["not-a-node"]
    return root, cur


def chain_with_non_list_inner(
    kinds: list[str],
    bad_at: int,
    bad_inner: Any,
    leaf_kind: str = NON_LITERAL_LEAF_KIND,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A chain where the wrapper at *bad_at*'s ``inner`` field is itself not
    a list at all (e.g. a bare int or dict, rather than the expected list of
    children) -- a shape distinct from a missing ``inner`` key (that one is
    ``None``/absent, this one is present but the wrong TYPE). A naive
    ``for c in cur.get("inner", [])`` degrades cleanly on a missing key
    (falls back to ``[]``) but raises ``TypeError`` on a present,
    non-iterable-as-expected value -- confirmed against all three
    production copies before this builder was added. Returns ``(root,
    stopping_node)``, as above."""
    root, _ = build_wrapper_chain(kinds, leaf_kind=leaf_kind)
    cur = root
    for _ in range(bad_at):
        cur = cur["inner"][0]
    cur["inner"] = bad_inner
    return root, cur


def add_irrelevant_metadata(node: dict[str, Any], metadata: dict[str, Any]) -> None:
    """Recursively stamp *metadata* onto *node* and every node reachable
    through its ``inner`` chain, in place -- a real clang AST node always
    carries volatile bookkeeping fields (``id``, ``loc``, ``range``, ...)
    alongside the structural ones this generator otherwise builds, and no
    traversal/value-extraction primitive is meant to depend on them. Chosen
    metadata keys (``id``/``loc``/``range``) never collide with a key this
    generator's own chain/leaf builders set, so this only ever ADDS noise,
    never masks the structural fields a test actually checks."""
    if not isinstance(node, dict):
        return
    node.update(metadata)
    inner = node.get("inner")
    if isinstance(inner, list):
        for child in inner:
            add_irrelevant_metadata(child, metadata)
