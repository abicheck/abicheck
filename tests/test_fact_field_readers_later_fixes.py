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


class TestComprehensionTargetShadowsOnlyWithinTheComprehension:
    """A comprehension genuinely introduces its own new scope in Python 3
    -- unlike a plain `for`/`with`/`except`/`match`, none of which are
    block-scoped -- so its own target must shadow calls *inside* the
    comprehension without leaking into the rest of the enclosing function
    (Codex review, fresh evidence, a real regression in an earlier
    revision of the lexical-binding-forms fix): `[x for getattr in
    funcs]` followed by a genuine, unrelated `getattr(rec, "bases")` later
    in the same function was wrongly suppressed."""

    def test_does_not_leak_the_comprehension_target_into_the_rest_of_the_function(
        self,
    ) -> None:
        src = (
            "def f(rec, funcs):\n"
            "    _ = [x for getattr in funcs]\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_still_shadows_within_the_comprehension_elt(self) -> None:
        """Positive control: the target must still shadow calls genuinely
        inside the comprehension's own `elt`."""
        src = (
            "def f(rec, funcs):\n"
            '    return [getattr(rec, "bases") for getattr in funcs]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_still_shadows_within_a_generator_filter(self) -> None:
        """Positive control: an `if` filter clause is also inside the
        comprehension's own scope."""
        src = (
            "def f(rec, funcs):\n"
            '    return [x for getattr in funcs if getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_still_shadows_within_a_non_outermost_generators_iterable_by_an_earlier_target(
        self,
    ) -> None:
        """Positive control: a *non*-outermost generator's own iterable
        is still inside the comprehension's scope, shadowed by any
        *earlier* generator's own (already-bound-by-then) target -- unlike
        that same generator's own target, which is not yet bound at that
        point (Codex review, fresh evidence: an earlier revision of this
        test used a repro where the shadowing target was the *same*
        generator's own, self-referencing one -- see
        `TestComprehensionGeneratorBindingOrder` below for that corrected,
        narrower case)."""
        src = (
            "def f(rec, funcs):\n"
            "    return [x for getattr in funcs "
            'for x in getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_the_outermost_iterable_itself_is_not_shadowed_by_its_own_target(
        self,
    ) -> None:
        """The one real exception: the outermost generator's own iterable
        evaluates in the scope enclosing the comprehension, before the
        comprehension's own target exists -- so a target reusing the same
        name as a real callee in that one position must still be
        detected."""
        src = (
            'def f(rec, funcs):\n    return [x for getattr in getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"


class TestComprehensionGeneratorBindingOrder:
    """A *non*-outermost generator's own iterable evaluates before *that
    same* generator's own target is bound -- the identical binding-order
    rule the outermost generator's iterable already gets, just one level
    less special-cased (Codex review, fresh evidence: the previous fix
    blanket-checked every generator's target regardless of position,
    wrongly shadowing a later generator's own iterable by its own
    not-yet-bound target). A generator's own `if` filter, by contrast,
    runs *after* that generator's own target is bound, and the
    comprehension's final `elt`/`key`/`value` runs after every
    generator's target is bound."""

    def test_a_generators_own_iterable_is_not_shadowed_by_its_own_target(self) -> None:
        src = (
            "def f(rec, xs):\n"
            "    return [x for x in xs "
            'for getattr in getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_a_generators_own_filter_is_still_shadowed_by_its_own_target(self) -> None:
        src = (
            "def f(rec, xs):\n"
            '    return [x for getattr in xs if getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_the_element_expression_is_shadowed_by_every_generators_target(
        self,
    ) -> None:
        src = (
            "def f(rec, xs, ys):\n"
            '    return [getattr(rec, "bases") for x in xs for getattr in ys]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_a_third_generators_iterable_is_shadowed_by_the_first_two_targets(
        self,
    ) -> None:
        """Generalizes past two generators: the third generator's own
        iterable is shadowed by both earlier targets but not its own."""
        src = (
            "def f(rec, xs, ys):\n"
            "    return [x for getattr in xs for y in ys "
            'for x in getattr(rec, "bases")]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestLambdaDefaultsEvaluateBeforeParameterShadows:
    """A lambda's own default value expression evaluates at lambda-
    *creation* time, in the enclosing scope, before the lambda's own
    parameters exist at all -- the identical def-time-vs-body-time
    distinction `_enclosing_qualnames`'s own default/annotation handling
    already draws for a named `def` (Codex review, fresh evidence):
    `lambda getattr=getattr(rec, "bases"): getattr` was wrongly treated
    as shadowed by the lambda's own `getattr` parameter."""

    def test_detects_a_real_read_in_a_lambda_default_shadowed_by_its_own_param(
        self,
    ) -> None:
        src = (
            "def f(rec):\n"
            '    return (lambda getattr=getattr(rec, "bases"): getattr)()\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_still_ignores_a_real_shadow_inside_the_lambda_body(self) -> None:
        """Positive control: the lambda's own body genuinely is shadowed
        by its parameter."""
        src = (
            "def f(rec, some_getattr):\n"
            '    return (lambda getattr: getattr(rec, "bases"))(some_getattr)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestUnboundDictGetitemMappingReaders:
    """`dict.__getitem__(vars(rec), "bases")` -- the *unbound*-method
    spelling of the bound `vars(rec).__getitem__("bases")` form -- reads
    the identical normalized legacy value, the same relationship
    `object.__getattribute__(rec, "bases")` already has to
    `rec.__getattribute__("bases")` elsewhere in this module (Codex
    review, fresh evidence). `dict_names` is resolved via
    `_builtins_symbol_aliases()`'s already-generic mechanism (the same
    one `vars` itself already uses), so every alias shape that mechanism
    covers -- a bare/aliased `from builtins import dict [as D]`, a plain
    assignment chain, and a qualified `builtins.dict` -- is covered for
    free."""

    def test_detects_the_unbound_bare_form(self) -> None:
        src = 'def f(rec):\n    return dict.__getitem__(vars(rec), "bases")\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_an_aliased_import_form(self) -> None:
        src = (
            "from builtins import dict as D\n"
            'def f(rec):\n    return D.__getitem__(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_a_plain_assignment_alias_form(self) -> None:
        src = (
            'def f(rec):\n    D = dict\n    return D.__getitem__(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_ignores_a_dict_parameter_shadow(self) -> None:
        src = 'def f(rec, dict):\n    return dict.__getitem__(vars(rec), "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_unrelated_mapping_receiver_argument(self) -> None:
        """Negative control: the first argument must itself be a
        recognized mapping receiver -- an arbitrary local reusing a
        `dict`-suggestive name is not."""
        src = (
            "def f(rec, other_dict):\n"
            '    return dict.__getitem__(other_dict, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_still_detects_the_bound_form(self) -> None:
        """Regression guard: the fix must not disturb the existing bound
        `vars(rec).__getitem__("bases")` recognition."""
        src = 'def f(rec):\n    return vars(rec).__getitem__("bases")\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"


class TestUnboundDictGetMappingReaders:
    """`dict.get(vars(rec), "bases")` -- the *unbound*-method spelling of
    the bound `vars(rec).get("bases")` form, the identical relationship
    `TestUnboundDictGetitemMappingReaders` above already has to its own
    bound sibling (Codex review, fresh evidence)."""

    def test_detects_the_unbound_bare_form(self) -> None:
        src = 'def f(rec):\n    return dict.get(vars(rec), "bases")\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_an_optional_default_argument(self) -> None:
        """A third positional argument (the default) is accepted but not
        inspected, matching the bound `.get()` form's own treatment."""
        src = 'def f(rec):\n    return dict.get(vars(rec), "bases", None)\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_an_aliased_import_form(self) -> None:
        src = (
            "from builtins import dict as D\n"
            'def f(rec):\n    return D.get(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_ignores_a_dict_parameter_shadow(self) -> None:
        src = 'def f(dict, rec):\n    return dict.get(vars(rec), "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_unrelated_mapping_receiver_argument(self) -> None:
        src = 'def f(rec):\n    other = {"x": 1}\n    return dict.get(other, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_still_detects_the_bound_form(self) -> None:
        src = 'def f(rec):\n    return vars(rec).get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"


class TestAttrgetterDottedPaths:
    """`operator.attrgetter("bases.foo")` chains a second `getattr()` off
    whatever the first component reads. Unlike a *second* component,
    which would need type inference to resolve, the first component is
    read directly off the literal string argument -- so it is recognized,
    while a later component stays out of scope (Codex review, fresh
    evidence)."""

    def test_detects_the_first_dotted_component(self) -> None:
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.attrgetter("bases.foo")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_the_bare_spelling_dotted(self) -> None:
        src = (
            "from operator import attrgetter\n"
            'def f(rec):\n    return attrgetter("bases.foo")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_still_detects_the_non_dotted_form(self) -> None:
        """Regression guard: the fix must not disturb the existing
        non-dotted recognition."""
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_reports_each_argument_independently_when_one_is_dotted(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.attrgetter("size_bits", "bases.foo")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert [s[2] for s in sites] == ["bases"]

    def test_ignores_a_bridged_name_in_a_non_first_component(self) -> None:
        """A later dotted component would need type inference to
        resolve, so it stays out of scope even when its own spelling
        matches a bridged field name."""
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.attrgetter("foo.bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_non_bridged_first_component(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.attrgetter("not_a_field.bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestItemgetterMappingReaders:
    """`operator.itemgetter("bases")(vars(rec))` -- the `attrgetter`-
    shaped constructor spelling of a subscript read, for the *bare* or
    `operator`-qualified `itemgetter` (Codex review, fresh evidence).
    Unlike `attrgetter`'s own wider "match wherever constructed" stance,
    this form requires the constructed getter to be called immediately on
    a real mapping receiver -- the same `_is_mapping_receiver()` gate
    every other subscript-reading form in this module already applies."""

    def test_detects_the_qualified_form(self) -> None:
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.itemgetter("bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_the_bare_spelling(self) -> None:
        src = (
            "from operator import itemgetter\n"
            'def f(rec):\n    return itemgetter("bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_an_aliased_operator_module(self) -> None:
        src = (
            "import operator as op\n"
            'def f(rec):\n    return op.itemgetter("bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_detects_a_plain_assignment_alias_of_the_operator_module(self) -> None:
        src = (
            "import operator\n"
            "op2 = operator\n"
            'def f(rec):\n    return op2.itemgetter("bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_ignores_a_non_mapping_receiver(self) -> None:
        """Negative control: an ordinary sequence is not a mapping
        receiver, even though `itemgetter` would work on it at runtime
        too -- kept out of scope the same way `dict.get`/`operator.
        getitem` already require a real mapping receiver."""
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    other = ["a", "b"]\n'
            '    return operator.itemgetter("bases")(other)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_shadowed_operator_parameter(self) -> None:
        src = (
            "def f(operator, rec):\n"
            '    return operator.itemgetter("bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_non_bridged_field(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.itemgetter("not_a_field")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_getter_assigned_to_a_variable_before_being_called(
        self,
    ) -> None:
        """A getter stored in an intermediate variable before being
        called is ordinary, common Python -- `_itemgetter_alias_keys()`
        tracks which variables hold a constructed getter and what keys
        it was built with (Codex review, fresh evidence: this repro was
        previously, deliberately out of scope; see the module-docstring
        entry in `docs/contribute/plans/one-semantic-pipeline.md`
        recording that this narrower gap has since been closed)."""
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    getter = operator.itemgetter("bases")\n'
            "    return getter(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_does_not_chase_an_aliased_getter_through_a_second_name(self) -> None:
        """Deliberately narrower than the single-variable case above: a
        getter re-bound to a *second* name before being called is not
        chased through that further hop, the same "no type inference
        beyond one hop" limit `_itemgetter_alias_keys()`'s own docstring
        already accepts."""
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    getter = operator.itemgetter("bases")\n'
            "    getter2 = getter\n"
            "    return getter2(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_reassigned_getter_variable(self) -> None:
        """Negative control: a variable assigned more than once is
        ambiguous by the second assignment -- guessing which one a later
        call actually used would risk fabricating a false positive."""
        src = (
            "import operator\n"
            "def f(rec, cond, fallback):\n"
            '    getter = operator.itemgetter("bases")\n'
            "    if cond:\n"
            "        getter = fallback\n"
            "    return getter(vars(rec))\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_does_not_disturb_the_existing_attrgetter_recognition(self) -> None:
        """Regression guard: `attrgetter`'s own constructor-matching
        stays unaffected by the new `itemgetter`-specific gating."""
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_does_not_disturb_the_existing_operator_getitem_recognition(self) -> None:
        """Regression guard: the existing `operator.getitem(vars(rec),
        "bases")` call-form stays unaffected."""
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.getitem(vars(rec), "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_inspects_every_key_in_a_multi_key_getter(self) -> None:
        """`operator.itemgetter("foo", "bases")(vars(rec))` returns a
        getter reading *both* requested keys as a tuple -- real,
        documented `itemgetter` behavior -- so a bridged key must be
        recognized regardless of its position among several (Codex
        review, fresh evidence: the original fix required exactly one
        constructor argument, silently missing every multi-key form)."""
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.itemgetter("foo", "bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_reports_each_bridged_key_independently(self) -> None:
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.itemgetter("bases", "vtable")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert {s[2] for s in sites} == {"bases", "vtable"}

    def test_multi_key_bare_spelling_still_recognized(self) -> None:
        src = (
            "from operator import itemgetter\n"
            'def f(rec):\n    return itemgetter("foo", "bases")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert len(sites) == 1
        assert sites[0][2] == "bases"

    def test_ignores_a_multi_key_getter_with_no_bridged_keys(self) -> None:
        src = (
            "import operator\n"
            'def f(rec):\n    return operator.itemgetter("foo", "bar")(vars(rec))\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []
