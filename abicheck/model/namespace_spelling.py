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

"""``_namespace_suffix_spellings`` -- every suffix spelling of a qualified
identity obtainable by dropping some prefix of its namespace/class-scope
chain (ADR-063 Track 2, 5B closure).

Split out of ``type_reachability_spelling.py`` (its original home, which
still re-exports this name by value for back-compat) for the identical
reason ``model/mangled_name.py``'s own docstring already gives for the
Itanium/MSVC scope-component parsers it holds: this is pure string
matching over an already-spelled identity, with no dependency on
``model``'s own entity types and no I/O -- a genuine leaf, needed by a
module *below* ``type_reachability_spelling.py`` in the dependency graph.

Concretely: ``compare/vtable_evidence.py``'s shared vtable-evidence
predicate (moved out of ``diff_types_vtable.py``, see that module's own
docstring) takes a namespace-suffix matcher as an injected callable rather
than importing one, specifically so it stays a leaf depending on ``model``
only. Its two callers -- ``diff_types_vtable.py`` (already importing
``type_reachability_spelling`` at module scope; unaffected) and
``diff_cxx_rules.py`` -- need to supply that callable themselves.
``diff_cxx_rules.py`` cannot import ``type_reachability_spelling`` at all,
even function-locally: that module already imports ``diff_cxx_rules`` at
its own top level (``itanium_qualified_name``/``msvc_qualified_name``), so
the reverse import -- at any scope, the AI-readiness ``import-cycle-growth``
gate's static AST scan does not distinguish module-level from
function-local imports -- would recreate exactly the cycle this move
avoids. Moving the one function actually needed down into ``model/``,
below both modules, is the same fix already applied to the Itanium/MSVC
scope parsers for the identical reason.
"""

from __future__ import annotations


def _namespace_suffix_spellings(identity: str) -> list[str]:
    """Every suffix spelling of *identity* obtainable by dropping some
    prefix of its namespace/class-scope chain, at each ``"::"`` boundary
    that occurs at template-argument bracket depth zero — from the full
    identity itself (dropping nothing) down to the fully bare leaf.

    A real backend does not always spell a nested type as either the
    fully-qualified identity or the fully-bare leaf (Codex review, fresh
    evidence, confirmed empirically via ``clang -ast-dump`` on
    ``namespace api { struct Outer { struct Inner {}; }; Outer::Inner
    g(); }``): direct-clang prints that function's return type as exactly
    ``"Outer::Inner"`` — dropping the *enclosing namespace* (``api::``,
    implied by lookup context inside that namespace) while keeping the
    *class-nesting* qualifier (``Outer::``, a distinct scope that is never
    elided) — a partial qualification distinct from both the full identity
    ``"api::Outer::Inner"`` and the fully-bare leaf ``"Inner"``. Generating
    every such suffix (not just the two extremes) is what lets a signature
    spelled this way still resolve to the right record.

    A plain ``identity.rsplit("::", 1)`` would additionally split *inside*
    a template argument's own qualified name: for
    ``"api::Wrapper<dep::Tag>"``, the lexically last ``"::"`` belongs to
    the template argument ``dep::Tag``, not an outer namespace boundary.
    Tracking ``<``/``>`` nesting depth and only considering a ``"::"`` at
    depth zero as a namespace separator avoids that.

    Returns ``[identity]`` (a single-element list) when *identity* carries
    no depth-zero ``"::"`` at all (already bare, or only qualified inside
    template arguments).
    """
    depth = 0
    splits = [0]
    i = 0
    n = len(identity)
    while i < n:
        ch = identity[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == ":" and depth == 0 and i + 1 < n and identity[i + 1] == ":":
            splits.append(i + 2)
            i += 1
        i += 1
    return [identity[s:] for s in splits]
