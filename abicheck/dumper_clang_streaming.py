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

"""Early, streaming-style pruning of a ``clang -ast-dump=json`` tree.

Performance-only optimization layered in *front* of the existing
``dumper_clang.py``/``dumper_scoping.py`` pipeline -- it never replaces
:func:`abicheck.dumper_scoping.scope_snapshot_excluding_dependencies`, which
still runs afterward, unchanged, as the correctness backstop. See that
module's own docstring and ``AGENTS.md``'s module-map entry for
``dumper_scoping.py`` for the full keep/exclude contract this module must
never be *more* aggressive than.

Why this exists
----------------
A pathological header pulling in heavy STL/template machinery (``<vector>``,
``<map>``, ``<unordered_map>``, ...) produces a ``clang -ast-dump=json`` tree
whose bulk is overwhelmingly *template-instantiated member function*
declarations reachable only from dependency headers (``<vector>``'s own
``push_back``/iterator/allocator machinery instantiated once per element
type, and so on) -- these are never part of the library's own ABI surface.
Measured on a synthetic repro (7 types x 4 heavy STL headers): a 67-70MB AST
JSON, ~1GB peak RSS, and 25-58s wall time, almost entirely spent building and
walking Python objects for content that
:func:`~abicheck.dumper_scoping.scope_snapshot_excluding_dependencies`
unconditionally discards moments later anyway (for *functions and
variables* -- there is no "kept because directly referenced" exception for
those two categories, only for types/enums; see that function's own
docstring). ``dumper_scoping.py``'s filter cannot help with this cost at
all: it runs on the fully-parsed, fully-materialized :class:`~abicheck.model.
AbiSnapshot`, well after ``json.load()`` and after the entire
:class:`abicheck.dumper_clang._ClangAstParser` walk into model objects has
already paid for every excluded declaration (empirically verified, not
assumed, before this module was written).

What this module actually does
-------------------------------
:func:`load_pruned_clang_ast` is a drop-in replacement for a bare
``json.load(fh)`` of a clang ``-ast-dump=json`` stream. It uses the stdlib
``json`` module's ``object_pairs_hook`` -- called once per JSON object, in
document order, innermost-first -- to collapse a *function- or
variable-shaped* declaration node (``FunctionDecl``, ``CXXMethodDecl``,
``CXXConstructorDecl``, ``CXXDestructorDecl``, ``CXXConversionDecl``,
``VarDecl``) into a tiny placeholder the instant its own subtree is fully
built, whenever that subtree:

1. has an *explicit* (non-inherited) declaring file recorded on its own
   ``loc`` -- i.e. we only ever act on unambiguous, local evidence, never on
   clang's delta/"sticky" file encoding inherited from an ancestor context
   we cannot see from inside a bottom-up hook (see "What this deliberately
   does NOT attempt" below); and
2. that file is confidently a toolchain/dependency header per
   :func:`abicheck.provenance.is_dependency_header`, given the real
   ``-H``/``--header`` root set this dump was invoked with (so an installed
   library analyzed via its own system-prefixed install path is never
   misclassified -- the identical rule ``dumper_scoping.py`` itself relies
   on); and
3. a full re-scan of the already-built subtree finds *no* nested
   ``loc``/``range`` location anywhere pointing at a file that is NOT a
   dependency header -- i.e. the declaration's whole textual extent is
   confined to dependency headers, with no macro-expansion or
   template-instantiation edge reaching back into project code. This is the
   safety net: pruning must never be more aggressive than the post-hoc
   filter, and the post-hoc filter's ``source_header`` is whatever
   ``dumper_clang._node_file`` resolves for the declaration -- if any part
   of the node's own recorded extent names a kept file, the *conservative*
   choice is to keep the whole node rather than risk silently discarding it.

Types, records, enums, and typedefs are deliberately **never** pruned by
this module, at any nesting depth, in any circumstance -- unlike functions
and variables, a dependency type/enum *can* be retained by
``scope_snapshot_excluding_dependencies`` when it is directly referenced by
a kept public declaration (its "direct-reference retention" carve-out). That
decision can only be made once the *whole* snapshot's public surface is
known, which is not yet true at streaming-parse time -- pruning a
dependency-header record here would silently and permanently defeat that
carve-out for every field/type it might have been retained for. Restricting
this module to function/variable nodes -- the two categories the post-hoc
filter drops *unconditionally*, with no such carve-out -- is what keeps this
purely a performance optimization rather than a second, diverging exclusion
policy.

What this deliberately does NOT attempt
----------------------------------------
This is not a true top-down SAX/early-exit parser: the stdlib ``json``
module still tokenizes and builds every byte of the document (clang's own
subprocess-side cost, and the tokenization cost itself, are unavoidable with
this architecture -- see ``docs/contribute/plans/
libclang-selective-ast-traversal.md`` for the migration that could actually
avoid *that*). ``object_pairs_hook`` fires bottom-up (innermost objects
first, in document order), so a candidate node's full subtree is already
built in memory by the time this module can decide to discard it. The real
wins are: (a) once discarded, that memory is freed immediately rather than
held for the remainder of ``json.load()`` and the whole subsequent
:class:`abicheck.dumper_clang._ClangAstParser` walk (peak-RSS reduction,
bounded by the largest single retained-or-pruned subtree rather than the
sum of the whole tree); and (b) the model-construction walk never visits the
pruned content at all (Python-side wall-time reduction proportional to how
much gets pruned). Also deliberately out of scope: the DPC++/SYCL
multi-document decode path (``sycl_context.
decode_and_select_frontend_context_from_path``) already has its own
memory-conscious incremental reader for a different reason (selecting one of
several concatenated documents) and is not touched here.

Measured trade-off -- read before enabling
-------------------------------------------
This is honestly a **narrow** win, not a general fix, and it was measured
end to end (``dump()``, not just this module's own ``json.load()`` call)
rather than assumed:

* Single dependency header repro (7 types x 5 heavy STL headers,
  ~1,027,000 JSON nodes): pruning removed 113 of 8,167 functions
  (~1.4%) and ~16,000 of ~1,027,000 nodes (~1.6%) under the conservative
  "own explicit file only" criterion above -- but *increased* total
  ``dump()`` wall time (5.1s -> 7.1s), because ``object_pairs_hook``
  forces the CPython C-accelerated JSON scanner to materialize a Python
  ``(key, value)`` list and invoke a Python-level callback for **every**
  JSON object in the document, not just the pruned ones -- a fixed cost
  the no-hook path never pays, which dominated the modest pruning win at
  this scale.
* A larger, 20-header repro (~165,000 functions, ~10.2GB baseline peak
  RSS): pruning removed ~2.2% of functions and reduced peak RSS by
  ~1.2%, but wall time was again worse (114.2s -> 129.3s, ~13% slower).

Why the prune ratio is so low: clang's own delta/"sticky" location
encoding means most nodes deep inside one dependency header's content
never carry their own explicit ``loc.file`` at all (only the *first* node
after a file transition does) -- and a bottom-up ``json.load()`` hook has
no access to the top-down ambient context needed to resolve the rest
correctly (see point 1 above). A hand-rolled top-down parser could
resolve ambient inheritance and prune far more, but would have to
abandon CPython's C-accelerated scanner for the *entire* document
(including the >95% that must still be kept), which is likely to cost
more than it saves -- see ``docs/contribute/plans/
libclang-selective-ast-traversal.md`` for the fuller analysis and the
evidence-backed case for why a native (non-JSON) traversal API is the
more promising next step. Given this, this module is shipped **off by
default**, opt-in only via ``ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS=1``,
and should be understood as a real, correctness-reviewed building block
for a memory-constrained caller willing to trade a modest wall-time cost
for a modest reduction in retained functions/nodes -- not a recommended
default or a general performance fix.
"""

from __future__ import annotations

import json
from typing import Any

from .provenance import is_dependency_header

#: Declaration kinds this module ever prunes. Deliberately excludes every
#: type/record/enum/typedef kind (``CXXRecordDecl``,
#: ``ClassTemplateSpecializationDecl``, ``EnumDecl``, ``TypedefDecl``,
#: ``TypeAliasDecl``, ...) -- see the module docstring's "direct-reference
#: retention" reasoning for why those must never be touched here.
_PRUNABLE_DECL_KINDS: frozenset[str] = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
        "VarDecl",
    }
)

#: Marker ``kind`` for a collapsed placeholder. Distinct from every real
#: clang AST ``kind`` string so nothing downstream can mistake it for a real
#: declaration; ``dumper_clang.py``'s ``_categorize()``-style dispatch simply
#: never matches it and moves on, the same as any other unrecognized kind.
PRUNED_PLACEHOLDER_KIND = "AbicheckPrunedDependencyDecl"


def _own_explicit_file(loc: dict[str, Any]) -> str | None:
    """Mirror :func:`abicheck.dumper_clang._node_file`'s *own-evidence* tier
    only -- the explicit ``loc.file``, or the ``expansionLoc``/``spellingLoc``
    fallback -- never falling back to an ambient "current file" the way that
    function does for its caller's already-threaded top-down walk. A
    bottom-up ``object_pairs_hook`` has no such ambient context to fall back
    to, and per this module's own conservative contract, no usable local
    evidence means "keep" (return ``None``), not "guess"."""
    f = loc.get("file")
    if isinstance(f, str) and f:
        return f
    for sub in ("expansionLoc", "spellingLoc"):
        s = loc.get(sub)
        if isinstance(s, dict):
            sf = s.get("file")
            if isinstance(sf, str) and sf:
                return sf
    return None


def _loc_or_range_names_kept_file(node: dict[str, Any], is_dep: Any) -> bool:
    """True if *node* (a ``loc`` or ``range`` object) names, anywhere within
    it (including a ``begin``/``end``/``expansionLoc``/``spellingLoc``
    sub-object), a file that is NOT a dependency header per *is_dep*."""
    f = node.get("file")
    if isinstance(f, str) and f and not is_dep(f):
        return True
    for sub_key in ("begin", "end", "expansionLoc", "spellingLoc"):
        sub = node.get(sub_key)
        if isinstance(sub, dict) and _loc_or_range_names_kept_file(sub, is_dep):
            return True
    return False


def _subtree_reaches_a_kept_file(value: Any, is_dep: Any) -> bool:
    """Recursively scan an already-built (in-memory, not re-parsed) subtree
    for any ``loc``/``range`` mention of a file that is not a dependency
    header -- the safety net described in the module docstring. Cheap
    relative to the JSON parse itself: a plain walk of Python objects
    already resident in memory, no tokenization."""
    if isinstance(value, dict):
        for key in ("loc", "range"):
            sub = value.get(key)
            if isinstance(sub, dict) and _loc_or_range_names_kept_file(sub, is_dep):
                return True
        return any(_subtree_reaches_a_kept_file(v, is_dep) for v in value.values())
    if isinstance(value, list):
        return any(_subtree_reaches_a_kept_file(v, is_dep) for v in value)
    return False


def make_dependency_header_predicate(header_roots: tuple[str, ...]) -> Any:
    """Build a memoized ``is_dep(path) -> bool`` closure for *header_roots*.

    :func:`abicheck.provenance.is_dependency_header` re-resolves (and, for a
    relative root, calls ``Path.resolve()``/``is_dir()`` on) the *entire*
    header-root set on every single call -- fine for its normal per-snapshot
    call volume, but this module may ask it thousands of times per header
    dump (once per candidate function/variable node), so per-unique-path
    memoization here is what keeps pruning itself cheap. Mirrors
    ``provenance.apply_provenance``'s own ``origin_cache`` idiom.
    """
    cache: dict[str, bool] = {}

    def is_dep(source_header: str) -> bool:
        cached = cache.get(source_header)
        if cached is None:
            cached = is_dependency_header(source_header, header_roots)
            cache[source_header] = cached
        return cached

    return is_dep


class DependencyDeclPruningHook:
    """Stateful ``object_pairs_hook`` for :func:`json.load` that prunes
    dependency-header function/variable subtrees as they complete.

    One instance is used for exactly one :func:`json.load` call (its state --
    the per-path memo and the prune counter -- has no meaning across calls).
    """

    __slots__ = ("_is_dep", "pruned_count")

    def __init__(self, header_roots: tuple[str, ...]) -> None:
        self._is_dep = make_dependency_header_predicate(header_roots)
        #: Number of subtrees collapsed this call -- surfaced for logging/
        #: tests, never for a correctness decision.
        self.pruned_count = 0

    def __call__(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        node = dict(pairs)
        kind = node.get("kind")
        if kind not in _PRUNABLE_DECL_KINDS:
            return node
        loc = node.get("loc")
        if not isinstance(loc, dict):
            return node
        own_file = _own_explicit_file(loc)
        if own_file is None or not self._is_dep(own_file):
            return node
        if _subtree_reaches_a_kept_file(node, self._is_dep):
            return node
        self.pruned_count += 1
        # Keep just enough for a human/log to identify what was elided;
        # never consulted by ``_ClangAstParser`` (an unrecognized ``kind``
        # is simply skipped, the same as any other node it doesn't model).
        return {
            "kind": PRUNED_PLACEHOLDER_KIND,
            "name": node.get("name"),
            "loc": loc,
            "abicheck_pruned_original_kind": kind,
        }


def load_pruned_clang_ast(
    fh: Any, *, header_roots: tuple[str, ...]
) -> tuple[dict[str, Any], int]:
    """Like ``json.load(fh)``, but collapses dependency-header function/
    variable subtrees to placeholders as soon as each one completes.

    Returns ``(root, pruned_count)``. *fh* is a binary file object (matching
    the existing ``json.load(fh)`` call this replaces, which lets the
    stdlib ``json`` module detect the stream's encoding itself). Safe to
    call with an empty *header_roots* -- :func:`abicheck.provenance.
    is_dependency_header` then falls back to a bare toolchain-path
    heuristic, same as ``dumper_scoping.py`` does in that situation.
    """
    hook = DependencyDeclPruningHook(header_roots)
    root = json.load(fh, object_pairs_hook=hook)
    return root, hook.pruned_count
