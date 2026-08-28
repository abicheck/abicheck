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

"""Later-round structural fixes for the ``fact-field-readers`` AI-
readiness check (``scripts/fact_field_readers.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split out of ``test_fact_field_readers_wrapper_scoping.py`` once that
file crossed the architecture gate's own 1200-line test-file cap --
mechanical extraction, not a redesign: every test class here is moved
unchanged, as a contiguous block, from that file's own tail -- the same
reason that file was itself split out of ``test_fact_field_readers.py``;
see either file's own docstring for the fuller history.

Covers a later, coherent slice of dynamic-reader-recognition findings:
augmented assignment through a mapping receiver, a lambda parameter
shadow, a mapping-receiver alias resolved through a local name, explicit
`__getitem__`/`operator.getitem` mapping-item reads, and a dynamic-
reader alias resolved per lexical scope rather than by a whole-tree,
scope-blind name collection.
"""

from __future__ import annotations

import ast

from scripts.fact_field_readers import unmigrated_fact_reader_sites


class TestAugmentedAssignmentThroughMappingReceivers:
    """Python marks an `AugAssign` target `ast.Store` regardless of its
    shape, even though the operation reads the target's existing value
    first -- the identical implicit-read gap the dedicated `ast.Attribute`-
    target `AugAssign` branch already covers for `rec.bases += inherited`,
    applied here to the mapping-subscript forms (`rec.__dict__["bases"]`/
    `vars(rec)["bases"]`) instead."""

    def test_detects_an_augmented_assignment_through_dunder_dict(self) -> None:
        src = 'def f(rec, values):\n    rec.__dict__["bases"] += values\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::rec.__dict__["bases"]::rec.__dict__["bases"]::1'
        ]

    def test_detects_an_augmented_assignment_through_vars(self) -> None:
        src = 'def f(rec, values):\n    vars(rec)["bases"] += values\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::vars(rec)["bases"]::vars(rec)["bases"]::1']

    def test_ignores_an_augmented_assignment_with_a_non_matching_key(self) -> None:
        """Negative control: the subscript key must still name a bridged
        attribute."""
        src = 'def f(rec, values):\n    rec.__dict__["unrelated"] += values\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_plain_overwrite_through_dunder_dict(self) -> None:
        """Negative control: an ordinary (non-augmented) subscript
        overwrite genuinely never reads the existing value, matching the
        established rule for the plain-attribute case -- and its `Store`
        context correctly disqualifies it from the ordinary, `Load`-only
        Subscript branch too."""
        src = 'def f(rec, values):\n    rec.__dict__["bases"] = values\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_augmented_assignment_on_a_non_mapping_receiver(self) -> None:
        """Negative control: an ordinary dict (not `vars(rec)`/
        `rec.__dict__`) must not be treated as an instance's own mapping."""
        src = 'def f(d, values):\n    d["bases"] += values\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestLambdaParametersShadowDynamicReaders:
    """`_shadowed()` checks a call's real AST ancestry for an enclosing
    `ast.Lambda` whose own parameters include the matched name, since
    `_enclosing_qualnames()`/`_locally_bound_names()` deliberately don't
    model a lambda as its own scope at all."""

    def test_ignores_a_getattr_call_shadowed_by_a_lambda_parameter(self) -> None:
        src = 'def f(rec):\n    g = lambda getattr, rec: getattr(rec, "bases")\n    return g\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_genuine_builtin_call_inside_an_unrelated_lambda(self) -> None:
        """Positive control: a lambda with no shadowing parameter of its
        own must not suppress a real builtin call inside its body."""
        src = 'def f(rec):\n    g = lambda x: getattr(x, "bases")\n    return g\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::lambda x: getattr(x, "bases")::getattr(x, "bases")::1'
        ]

    def test_detects_an_unrelated_call_outside_the_lambda_in_the_same_function(
        self,
    ) -> None:
        """Negative-of-the-negative: a genuine `getattr` call textually
        outside the lambda, in the same enclosing function, must still be
        caught -- the lambda-parameter shadow must not leak past the
        lambda's own body."""
        src = (
            "def f(rec, other):\n"
            '    lam = lambda getattr: getattr(rec, "bases")\n'
            '    return getattr(other, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::getattr(other, "bases")::getattr(other, "bases")::1'
        ]

    def test_ignores_a_closure_shadow_reaching_through_a_lambda(self) -> None:
        """A lambda with no parameter of its own still inherits a real
        shadow from its enclosing function's own parameter, via the
        pre-existing qualname-based closure walk -- unaffected by the new
        lambda-ancestor check running first."""
        src = 'def outer(getattr):\n    return (lambda rec: getattr(rec, "bases"))(None)\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_shadow_in_a_nested_lambda(self) -> None:
        src = (
            "def f(rec):\n"
            '    return (lambda getattr: (lambda: getattr(rec, "bases"))())(None)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestMappingReceiverAliasesResolveThroughLocalNames:
    """`_mapping_receiver_aliases()` resolves a name assigned from a
    mapping-receiver-shaped RHS (`vars(rec)`/`X.__dict__`), so
    `_is_mapping_receiver()` recognizes it later even when the mapping
    itself was stored in an intermediate variable first."""

    def test_detects_a_subscript_read_through_a_vars_alias(self) -> None:
        src = 'def f(rec):\n    fields = vars(rec)\n    return fields["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::fields["bases"]::fields["bases"]::1']

    def test_detects_a_get_call_through_a_dunder_dict_alias(self) -> None:
        src = 'def f(rec):\n    fields = rec.__dict__\n    return fields.get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::fields.get("bases")::fields.get("bases")::1']

    def test_detects_a_chained_mapping_alias(self) -> None:
        src = 'def f(rec):\n    a = vars(rec)\n    b = a\n    return b.get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::b.get("bases")::b.get("bases")::1']

    def test_ignores_an_unrelated_dict_alias(self) -> None:
        """Negative control: an ordinary parameter assigned to another
        name is not a mapping receiver."""
        src = 'def f(d):\n    fields = d\n    return fields["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_mapping_alias_shadowed_by_a_parameter(self) -> None:
        """Negative control: a parameter reusing the alias name must not
        be treated as the resolved mapping-receiver alias."""
        src = 'def f(fields, rec):\n    return fields["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_mapping_alias_read_with_a_non_bridged_key(self) -> None:
        src = 'def f(rec):\n    fields = vars(rec)\n    return fields["unrelated"]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestExplicitMappingItemReaders:
    """`vars(rec).__getitem__("bases")` / `operator.getitem(vars(rec),
    "bases")` read the identical normalized legacy value the subscript/
    `.get()` mapping-read forms already catch."""

    def test_detects_a_dunder_getitem_call(self) -> None:
        src = 'def f(rec):\n    return vars(rec).__getitem__("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::vars(rec).__getitem__("bases")::'
            'vars(rec).__getitem__("bases")::1'
        ]

    def test_detects_operator_getitem(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::operator.getitem(vars(rec), "bases")::'
            'operator.getitem(vars(rec), "bases")::1'
        ]

    def test_detects_operator_getitem_through_an_aliased_import(self) -> None:
        src = (
            "import operator as op\n"
            "def f(rec):\n"
            '    return op.getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::op.getitem(vars(rec), "bases")::'
            'op.getitem(vars(rec), "bases")::1'
        ]

    def test_ignores_dunder_getitem_on_a_non_mapping_receiver(self) -> None:
        src = 'def f(d):\n    return d.__getitem__("bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_operator_getitem_shadowed_by_a_parameter(self) -> None:
        src = 'def f(operator, rec):\n    return operator.getitem(vars(rec), "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestDynamicReaderAliasesResolvePerLexicalScope:
    """A recognized alias-source import stops `_shadowed()`'s outward
    closure walk at the scope it was recognized in, rather than letting
    the walk continue and potentially find a completely unrelated
    same-named binding in an enclosing scope."""

    def test_detects_a_scoped_alias_despite_an_unrelated_outer_import(self) -> None:
        src = (
            "from helper import ag\n"
            "def f(rec):\n"
            "    from operator import attrgetter as ag\n"
            '    return ag("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::ag("bases")(rec)::ag("bases")::1']

    def test_ignores_an_unrelated_outer_import_with_no_inner_recognition(
        self,
    ) -> None:
        """Negative control: without the inner recognized re-import, the
        outer, unrelated `ag` must not be treated as a genuine alias --
        this fix must not widen recognition, only stop the walk early
        once a real alias is found."""
        src = 'from helper import ag\ndef f(rec):\n    return ag(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_genuine_parameter_shadow_unaffected_by_the_fix(self) -> None:
        src = 'def f(getattr, rec):\n    return getattr(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_closure_through_a_real_recognized_outer_alias(self) -> None:
        """Positive control: an inner function with no import of its own
        still correctly resolves a recognized alias from an *enclosing*
        scope -- the fix only changes what happens once a recognized
        alias is actually found, not the closure walk itself."""
        src = (
            "from builtins import getattr\n"
            "def outer():\n"
            "    def inner(rec):\n"
            '        return getattr(rec, "bases")\n'
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::outer.inner::bases::getattr(rec, "bases")::getattr(rec, "bases")::1'
        ]
