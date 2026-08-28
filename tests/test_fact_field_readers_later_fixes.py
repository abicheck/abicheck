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


class TestGetitemImportAliasesResolveTheBareCallableForm:
    """`_operator_attrgetter_aliases()` also resolves `operator.getitem`'s
    own bare-name import alias (`getitem_names`), the identical
    import-seeded/chained/qualified resolution `attrgetter_names` already
    gets -- not only the qualified `operator.getitem(...)` spelling."""

    def test_detects_an_aliased_bare_getitem_import(self) -> None:
        src = (
            "from operator import getitem as gi\n"
            "def f(rec):\n"
            '    return gi(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::gi(vars(rec), "bases")::gi(vars(rec), "bases")::1'
        ]

    def test_detects_an_unaliased_bare_getitem_import(self) -> None:
        src = (
            "from operator import getitem\n"
            "def f(rec):\n"
            '    return getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::getitem(vars(rec), "bases")::'
            'getitem(vars(rec), "bases")::1'
        ]

    def test_detects_a_chained_getitem_alias(self) -> None:
        src = (
            "from operator import getitem as gi\n"
            "def f(rec):\n"
            "    gi2 = gi\n"
            '    return gi2(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::gi2(vars(rec), "bases")::gi2(vars(rec), "bases")::1'
        ]

    def test_detects_a_qualified_getitem_assignment(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            "    gi = operator.getitem\n"
            '    return gi(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::gi(vars(rec), "bases")::gi(vars(rec), "bases")::1'
        ]

    def test_ignores_an_unrelated_local_getitem_with_no_import(self) -> None:
        """Negative control: an ordinary, locally defined function
        sharing the name `getitem`, with no `from operator import
        getitem` anywhere in the file, must not be recognized."""
        src = (
            "def getitem(a, b):\n"
            "    return a\n"
            "def f(rec):\n"
            '    return getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_getitem_import_shadowed_by_a_parameter(self) -> None:
        src = (
            "from operator import getitem\n"
            "def f(getitem, rec):\n"
            '    return getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestNamedExprAliasesAndInlineCallsAreRecognized:
    """A named expression (walrus) is a real assignment target too --
    `(read := getattr)(rec, "bases")` both introduces a real alias `read`
    (tracked the identical way a plain `read = getattr` already is) and,
    when the walrus is itself used as the call's own callee/mapping
    receiver, reads the field right there in the very expression that
    introduces the alias (Codex review, fresh evidence: the alias
    collectors only ever recognized `ast.Assign`/`ast.AnnAssign`, so
    neither shape produced a reader site)."""

    def test_detects_a_walrus_bound_getattr_called_inline(self) -> None:
        src = 'def f(rec):\n    return (read := getattr)(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_a_walrus_bound_vars_mapping_used_inline(self) -> None:
        src = 'def f(rec):\n    return (fields := vars(rec))["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_a_walrus_bound_getattr_alias_used_later(self) -> None:
        """The alias itself, not just the inline call, is tracked --
        `read` resolves as a real `getattr` alias for a later, ordinary
        call too."""
        src = (
            "def f(rec):\n"
            "    if (read := getattr) is not None:\n"
            '        return read(rec, "bases")\n'
            "    return None\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_ignores_a_walrus_bound_getattr_shadowed_by_a_parameter(self) -> None:
        """Negative control: a parameter named `getattr` shadows the real
        builtin, so the walrus's own RHS is not the real `getattr` either
        -- must not fire."""
        src = 'def f(rec, getattr):\n    return (read := getattr)(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_walrus_of_an_unrelated_builtin(self) -> None:
        """Negative control: `len` is not `getattr`/`vars` -- the walrus
        machinery must not treat every callable-valued walrus as a
        bridged-field read."""
        src = "def f(rec):\n    return (x := len)(rec)\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestLexicalBindingFormsAreTreatedAsShadows:
    """`_locally_bound_names()` now recognizes a `for` target, a
    `with ... as` target, an `except ... as` name, a comprehension's own
    `for` target, and a `match` capture as real local bindings -- not just
    a parameter, a `def`/`class` name, or an import (Codex review, fresh
    evidence): `for getattr in funcs: return getattr(rec, "bases")`
    reused the builtin-looking name for an ordinary, unrelated loop
    variable, but was still unconditionally treated as the real builtin
    and flagged, a false positive on genuinely valid code."""

    def test_ignores_a_for_target_shadow(self) -> None:
        src = (
            "def f(rec, funcs):\n"
            "    for getattr in funcs:\n"
            '        return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_for_target_shadow_through_tuple_unpacking(self) -> None:
        src = (
            "def f(rec, funcs):\n"
            "    for getattr, other in funcs:\n"
            '        return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_with_as_shadow(self) -> None:
        src = (
            "def f(rec, cm):\n"
            "    with cm() as getattr:\n"
            '        return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_except_as_shadow(self) -> None:
        src = (
            "def f(rec):\n"
            "    try:\n"
            "        pass\n"
            "    except Exception as getattr:\n"
            '        return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_comprehension_target_shadow(self) -> None:
        src = 'def f(rec, funcs):\n    return [getattr(rec, "bases") for getattr in funcs]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_bare_match_capture_shadow(self) -> None:
        src = (
            "def f(rec, val):\n"
            "    match val:\n"
            "        case getattr:\n"
            '            return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_match_as_pattern_capture_shadow(self) -> None:
        src = (
            "def f(rec, val):\n"
            "    match val:\n"
            "        case object() as getattr:\n"
            '            return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_match_capture_nested_in_a_class_pattern(self) -> None:
        src = (
            "def f(rec, val):\n"
            "    match val:\n"
            "        case SomeCls(getattr):\n"
            '            return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_still_detects_a_real_getattr_read_inside_an_unrelated_for_loop(
        self,
    ) -> None:
        """Positive control: a `for` loop binding an unrelated name must
        not suppress a real read elsewhere in the same function."""
        src = (
            "def f(rec, items):\n"
            "    for x in items:\n"
            '        return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"
