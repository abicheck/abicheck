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

"""``_memoize_per_tree``'s contract, stated as invariants.

``scripts/fact_detector_misuse_scope.py`` caches four pure whole-tree
helpers on the tree object, because `fact_equality_misuse_sites` and the
`_fact_aliases` it calls were each recomputing them -- two to four full
`ast.walk`s per file, on 787 files.

A cache on a gate's detection path is only safe while the functions under
it stay pure and their results stay unmutated, and neither property is
visible at the call site. So this module tests the *property* -- memoized
and unmemoized results agree -- over many independently-shaped inputs
rather than one fixture, and it obtains the unmemoized answer from the
undecorated function itself (`__wrapped__`, which `functools.wraps` sets)
rather than from a second copy of the logic that could drift.

The adversarial inputs matter more than the count: the sources below are
chosen to exercise the *specific* shapes these helpers disagree about --
nested scopes, a shadowed constructor name, an aliased import, a chained
alias, an annotated parameter, a comprehension scope, a lambda default --
because a cache keyed too coarsely (on the tree alone, ignoring a second
argument) would return one scope's answer for another and only a
multi-scope input would notice.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.fact_detector_misuse import fact_equality_misuse_sites  # noqa: E402
from scripts.fact_detector_misuse_scope import (  # noqa: E402
    _def_containing_qualnames,
    _enclosing_qualnames,
    _lexical_function_parents,
    _locally_bound_constructor_shadow_names,
    _memoize_per_tree,
)

# Each source targets a shape the memoized helpers must keep distinct.
_SOURCES: dict[str, str] = {
    "empty": "",
    "module_level_compare": "x = a.bases_fact\ny = b.bases_fact\nz = x == y\n",
    "single_function": (
        "def f(rec, other):\n    fact = rec.bases_fact\n    return fact == other\n"
    ),
    "two_sibling_functions": (
        "def f(rec, other):\n"
        "    fact = rec.bases_fact\n"
        "    return fact == other\n"
        "\n"
        "def g(rec, other):\n"
        "    plain = rec.bases\n"
        "    return plain == other\n"
    ),
    "nested_function_inherits_alias": (
        "def outer(rec):\n"
        "    fact = rec.bases_fact\n"
        "    def inner(other):\n"
        "        return fact == other\n"
        "    return inner\n"
    ),
    "chained_alias": (
        "def f(rec, other):\n"
        "    first = rec.vtable_fact\n"
        "    second = first\n"
        "    return second == other\n"
    ),
    "annotated_parameter": (
        "from abicheck.model.fact import Fact\n"
        "def f(value: Fact[int], other):\n    return value == other\n"
    ),
    "annotated_assignment": (
        "from abicheck.model.fact import Fact\n"
        "def f(rec, other):\n"
        "    v: Fact[list[str]] = rec.bases_fact\n"
        "    return v == other\n"
    ),
    "shadowed_constructor_name": (
        "from abicheck.model.fact import Fact\n"
        "def f(Fact, other):\n    return Fact(1) == other\n"
    ),
    "aliased_import": (
        "from abicheck.model.fact import Fact as F\n"
        "def f(other):\n    return F.present(1) == other\n"
    ),
    "class_method_scope": (
        "class C:\n"
        "    def m(self, rec, other):\n"
        "        fact = rec.vptr_offset_bits_fact\n"
        "        return fact == other\n"
    ),
    "comprehension_scope": (
        "def f(recs, other):\n"
        "    return [r.bases_fact == other for r in recs]\n"
    ),
    "lambda_default_scope": (
        "def outer(rec):\n"
        "    fact = rec.bases_fact\n"
        "    return lambda other=fact: other == fact\n"
    ),
    "deeply_nested_scopes": (
        "def a(rec):\n"
        "    def b():\n"
        "        def c():\n"
        "            def d():\n"
        "                return rec.bases_fact == rec.vtable_fact\n"
        "            return d\n"
        "        return c\n"
        "    return b\n"
    ),
    "global_declared_name": (
        "fact = None\n"
        "def f(rec, other):\n"
        "    global fact\n"
        "    fact = rec.bases_fact\n"
        "    return fact == other\n"
    ),
    "no_compare_at_all": "def f(rec):\n    return rec.bases_fact\n",
}


def _tree(source: str) -> ast.Module:
    return ast.parse(source, filename="<memo-test>")


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_memoized_result_equals_the_undecorated_computation(name: str) -> None:
    """The invariant the cache must never break, for each single-argument
    helper: the decorated call and the raw function agree.

    `__wrapped__` is the real, undecorated function, so this compares the
    cache against the very logic it wraps -- not against a restatement of
    it that could drift and quietly agree with a wrong answer.
    """
    source = _SOURCES[name]
    for helper in (
        _enclosing_qualnames,
        _def_containing_qualnames,
        _lexical_function_parents,
    ):
        raw = helper.__wrapped__  # type: ignore[attr-defined]
        # Separate trees: a fresh one for the raw call, so no cache the
        # decorated call populated can possibly serve it.
        assert helper(_tree(source)) == raw(_tree(source)), f"{helper.__name__}/{name}"


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_two_argument_helper_also_matches_undecorated(name: str) -> None:
    """`_locally_bound_constructor_shadow_names` takes the tree *and* the
    qualname spans, so it is the one helper a too-coarse cache key would
    break -- checked over the same multi-scope inputs."""
    source = _SOURCES[name]
    raw = _locally_bound_constructor_shadow_names.__wrapped__  # type: ignore[attr-defined]

    cached_tree = _tree(source)
    cached = _locally_bound_constructor_shadow_names(
        cached_tree, _enclosing_qualnames(cached_tree)
    )
    plain_tree = _tree(source)
    expected = raw(plain_tree, _enclosing_qualnames.__wrapped__(plain_tree))  # type: ignore[attr-defined]
    assert cached == expected, name


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_whole_scan_is_unchanged_by_memoization(name: str) -> None:
    """The end-to-end property that actually matters: the reported misuse
    sites are identical. Repeated on one tree as well, since the second
    call is the first one that can be served from the cache."""
    source = _SOURCES[name]
    tree = _tree(source)
    first = fact_equality_misuse_sites(tree, "<memo-test>")
    second = fact_equality_misuse_sites(tree, "<memo-test>")
    assert first == second, f"repeat call differs for {name}"
    # A fresh tree must agree with the (now warm) one.
    assert fact_equality_misuse_sites(_tree(source), "<memo-test>") == first, name


def test_cache_key_includes_the_second_argument() -> None:
    """The cache must key on *all* arguments, not just the tree.

    This test exists because the first version of this module did not have
    it, and a deliberate mutation -- `key = ()`, ignoring the extra
    arguments entirely -- passed all 77 tests then present. It survived
    because every real caller happens to pass the one memoized
    `_enclosing_qualnames(tree)` object for a given tree, so no existing
    input distinguished a per-tree key from a per-(tree, args) key. That
    made the keying untested, not unnecessary: the second argument is
    plainly observable (real spans scope the shadowed names per function;
    empty spans collapse them all onto `<module>`), so a coarse key would
    serve one scoping for another the moment any caller passed a different
    one.

    Asserted by calling the decorated helper twice on the *same* tree with
    two genuinely different span arguments and requiring each to get its
    own answer.
    """
    source = (
        "from abicheck.model.fact import Fact\n"
        "def f(Fact, other):\n    return Fact(1) == other\n"
        "def g(other):\n    return Fact(2) == other\n"
    )
    tree = _tree(source)
    real_spans = _enclosing_qualnames(tree)

    via_real = _locally_bound_constructor_shadow_names(tree, real_spans)
    via_empty = _locally_bound_constructor_shadow_names(tree, ())

    raw = _locally_bound_constructor_shadow_names.__wrapped__  # type: ignore[attr-defined]
    fresh = _tree(source)
    assert via_real == raw(fresh, _enclosing_qualnames.__wrapped__(fresh))  # type: ignore[attr-defined]
    assert via_empty == raw(_tree(source), ())
    # The load-bearing half: the two must not be the same object's answer.
    assert via_real != via_empty


def test_cache_actually_caches() -> None:
    """The optimization exists at all: a second call must not recompute.

    Without this, every equivalence test above would still pass against a
    decorator that silently forwarded every call -- a cache that caches
    nothing is indistinguishable from a correct one by output alone.
    """
    calls = []

    @_memoize_per_tree
    def _counted(tree: ast.Module) -> int:
        calls.append(1)
        return len(list(ast.walk(tree)))

    tree = _tree("def f():\n    return 1\n")
    assert _counted(tree) == _counted(tree) == _counted(tree)
    assert len(calls) == 1, "decorated function recomputed for the same tree"

    # A different tree is a different cache -- the entry is on the node.
    _counted(_tree("x = 1\n"))
    assert len(calls) == 2


def test_cache_is_scoped_to_its_own_tree() -> None:
    """Two distinct trees never share an entry, even with equal source --
    the failure a module-level `id(tree)`-keyed dict would eventually
    produce after an address is reused."""

    @_memoize_per_tree
    def _identity(tree: ast.Module) -> int:
        return id(tree)

    a, b = _tree("x = 1\n"), _tree("x = 1\n")
    assert _identity(a) == id(a)
    assert _identity(b) == id(b)
    assert _identity(a) != _identity(b)


def test_cache_does_not_leak_into_unrelated_attributes() -> None:
    """The cache is stored under a namespaced slot, so it cannot collide
    with a real `ast` field name on the node."""

    @_memoize_per_tree
    def _noop(tree: ast.Module) -> None:
        return None

    tree = _tree("x = 1\n")
    before = set(ast.Module._fields)
    _noop(tree)
    slots = [k for k in tree.__dict__ if k.startswith("_abicheck_memo__")]
    assert slots == ["_abicheck_memo__" + "_noop"]
    assert not (set(slots) & before)
