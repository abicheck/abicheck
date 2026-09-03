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

"""``_itemgetter_alias_keys()``'s binding-form coverage
(``scripts/fact_field_readers_scope.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split into its own file rather than appended to
``test_fact_field_readers_later_fixes.py`` (whose own
``TestItemgetterMappingReaders`` class covers the plain-``ast.Assign``
form of the same mechanism): that file has only ~87 lines of headroom
under the architecture gate's 1200-line test-file cap, too little to
safely add a new binding-form matrix to.

Covers the follow-up finding that the original itemgetter-alias fix only
ever walked ``ast.Assign`` -- ``get: object = operator.itemgetter(...)``
(an annotated assignment) and ``(get := operator.itemgetter(...))``
(a named expression/walrus) construct and bind the identical getter, just
through a different Python binding statement, and both were silently
missed (Codex review, fresh evidence).
"""

from __future__ import annotations

import ast

from scripts.fact_field_readers import unmigrated_fact_reader_sites


class TestItemgetterConstructorAliasedThroughAnnAssign:
    """`get: object = operator.itemgetter("bases")` binds the getter via
    an annotated assignment rather than a plain `ast.Assign`."""

    def test_single_key_annassign_binding_is_recognized(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    get: object = operator.itemgetter("bases")\n'
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_multi_key_annassign_binding_reports_each_bridged_key(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    get: object = operator.itemgetter("bases", "vtable")\n'
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert {s[2] for s in sites} == {"bases", "vtable"}

    def test_bare_itemgetter_spelling_via_annassign_is_recognized(self) -> None:
        src = (
            "from operator import itemgetter\n"
            "def f(rec):\n"
            '    get: object = itemgetter("bases")\n'
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_annassign_binding_reassigned_afterward_is_ambiguous(self) -> None:
        """A name assigned more than once anywhere in the tree is dropped
        entirely -- the same ambiguity rule the plain-`ast.Assign` form
        already follows, now exercised through an `AnnAssign` binding."""
        src = (
            "import operator\n"
            "def f(rec, other):\n"
            '    get: object = operator.itemgetter("bases")\n'
            "    get = other\n"
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []

    def test_annassign_binding_on_non_mapping_receiver_is_ignored(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    get: object = operator.itemgetter("bases")\n'
            "    return get(rec)\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []


class TestItemgetterConstructorAliasedThroughNamedExpr:
    """`(get := operator.itemgetter("bases"))` binds the getter via a
    named expression/walrus rather than a plain `ast.Assign`."""

    def test_single_key_named_expr_binding_is_recognized(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    if (get := operator.itemgetter("bases")):\n'
            "        pass\n"
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_multi_key_named_expr_binding_reports_each_bridged_key(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    if (get := operator.itemgetter("bases", "vtable")):\n'
            "        pass\n"
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert {s[2] for s in sites} == {"bases", "vtable"}

    def test_bare_itemgetter_spelling_via_named_expr_is_recognized(self) -> None:
        src = (
            "from operator import itemgetter\n"
            "def f(rec):\n"
            '    if (get := itemgetter("bases")):\n'
            "        pass\n"
            "    return get(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_named_expr_binding_on_non_mapping_receiver_is_ignored(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    if (get := operator.itemgetter("bases")):\n'
            "        pass\n"
            "    return get(rec)\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []


class TestItemgetterConstructedAndCalledThroughAWalrusCallee:
    """`(get := operator.itemgetter("bases"))(vars(rec))` -- a walrus
    used directly as the call's own callee, immediately invoking the
    getter it just constructed, rather than binding `get` for a *later*
    call (the previous class's own subject) (Codex review, fresh
    evidence). Mirrors the identical `getattr` walrus-callee handling
    already recognized elsewhere in this module (`(read := getattr)(rec,
    "bases")`)."""

    def test_single_key_walrus_callee_is_recognized(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return (get := operator.itemgetter("bases"))(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_multi_key_walrus_callee_reports_each_bridged_key(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return (get := operator.itemgetter("bases", "vtable"))(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert {s[2] for s in sites} == {"bases", "vtable"}

    def test_bare_itemgetter_spelling_walrus_callee_is_recognized(self) -> None:
        src = (
            "from operator import itemgetter\n"
            "def f(rec):\n"
            '    return (get := itemgetter("bases"))(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_walrus_callee_with_no_bridged_keys_is_ignored(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return (get := operator.itemgetter("foo", "bar"))(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []

    def test_walrus_callee_on_a_non_mapping_receiver_is_ignored(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return (get := operator.itemgetter("bases"))(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []

    def test_walrus_callee_with_shadowed_operator_name_is_ignored(self) -> None:
        """Negative control: a parameter named `operator` shadows the
        real module inside the function body, the identical shadow rule
        `_shadowed()` already applies to the immediate-construction-and-
        call form."""
        src = (
            "def f(rec, operator):\n"
            '    return (get := operator.itemgetter("bases"))(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert sites == []
