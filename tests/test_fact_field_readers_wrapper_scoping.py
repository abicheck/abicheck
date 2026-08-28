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

"""Narrower, more structural fixes for the ``fact-field-readers``
AI-readiness check (``scripts/fact_field_readers.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split out as its own file (rather than appended to
``test_fact_field_readers.py``, already at the architecture gate's
1200-line test-file cap) for the same reason
``tests/test_fact_detector_misuse_def_time_scope.py`` was split out of
``test_fact_detector_misuse.py`` -- see that file's own docstring.

Covers four independent Codex-review findings across two review rounds:

1. **``_operator_attrgetter_aliases()`` seeded ``"attrgetter"``/``"operator"``
   unconditionally**, the same way ``_builtins_getattr_aliases()`` correctly
   seeds the real builtin ``"getattr"`` -- but `attrgetter`/`operator` are
   not builtins; they mean nothing until a real ``import operator``/``from
   operator import attrgetter`` actually happens. An unrelated local
   function or variable sharing either name, with no such import anywhere
   in the file, was wrongly recognized as the real
   ``operator.attrgetter`` constructor.

2. **``_outermost_containing_expr()`` stopped climbing one level too
   early** at a keyword-argument value or a comprehension's own
   ``for ... in ...`` clause, since neither ``ast.keyword`` nor
   ``ast.comprehension`` is itself an ``ast.expr``. A read's own key is
   fingerprinted by its outermost containing expression precisely so two
   textually-identical reads in different surrounding contexts don't
   collide -- climbing through these transparent wrapper node types keeps
   that fingerprint accurate for a read sitting inside a keyword argument
   or a comprehension clause, the same way it already was for a read
   sitting inside any ordinary nested expression.

3. **The attrgetter branch required the constructor call to be
   *immediately* invoked** (``attrgetter("bases")(rec)``), missing the
   equally common callback spelling (``sorted(records,
   key=attrgetter("bases"))``, ``map(attrgetter("bases"), records)``) --
   the constructor is now matched wherever it's *constructed*, regardless
   of how (or whether, at the same expression) its result is called. This
   also closes, as a side effect, the previously-documented
   two-step-local-variable-indirection gap
   (``getter = attrgetter("bases"); getter(rec)``).

4. **``_locally_bound_names()`` only ever tracked a *parameter* as a
   locally-bound name, never a nested ``def``/``class`` statement's own
   *name*** -- so ``def getattr(obj, name): ...`` followed by an ordinary
   call to it was still treated as the real builtin, since only the
   builtin's own unconditional seed and the parameter-only shadow map were
   ever consulted.
"""

from __future__ import annotations

import ast

from scripts.fact_field_readers import unmigrated_fact_reader_sites


class TestOperatorAttrgetterRequiresARealImport:
    """`_operator_attrgetter_aliases()` must not recognize a bare
    `attrgetter`/`operator` name with no corresponding import anywhere in
    the file."""

    def test_ignores_an_unrelated_local_attrgetter_function_with_no_import(
        self,
    ) -> None:
        """`def attrgetter(rec, name): ...` -- an ordinary, locally
        defined function that happens to share the builtin-*looking* name
        `attrgetter`, with no `import operator`/`from operator import
        attrgetter` anywhere in the file. This call must not be
        recognized as the `operator.attrgetter` constructor."""
        src = (
            "def attrgetter(rec, name):\n"
            "    return rec\n"
            "def f(rec):\n"
            '    return attrgetter(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_unrelated_operator_variable_with_no_import(
        self,
    ) -> None:
        """The dotted-access sibling of the same false positive: a plain
        local variable named `operator` (e.g. an arithmetic-operator
        object, nothing to do with the stdlib module) with an
        `attrgetter(...)` attribute of its own, and no real `import
        operator` anywhere in the file."""
        src = (
            "class Operator:\n"
            "    def attrgetter(self, name):\n"
            "        return name\n"
            "def f(rec):\n"
            "    operator = Operator()\n"
            '    return operator.attrgetter("bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestOutermostContainingExprClimbsThroughTransparentWrappers:
    """`_outermost_containing_expr()` must climb through an `ast.keyword`
    or `ast.comprehension` wrapper the same way it already climbs through
    any ordinary `ast.expr` parent."""

    def test_climbs_through_a_keyword_argument_to_the_enclosing_call(
        self,
    ) -> None:
        """`make_change(old_value=t_old.bases, description="x")` -- the
        read's outermost containing expression must be the whole
        `make_change(...)` call, not just the keyword's own value
        expression `t_old.bases` (a real example from this repository's
        own reviewed baseline is
        `make_change(..., old_value=str(t_old.bases), ...)`)."""
        src = 'def f(t_old):\n    make_change(old_value=t_old.bases, description="x")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::make_change(old_value=t_old.bases, description="x")::'
            "t_old.bases::1"
        ]

    def test_climbs_through_a_comprehension_to_the_enclosing_display(
        self,
    ) -> None:
        """`{b for b in header.bases}` -- the read of `header.bases` sits
        inside the comprehension's own `for ... in ...` clause
        (`ast.comprehension`, also not an `ast.expr`), so the outermost
        containing expression must be the whole set-display
        `{b for b in header.bases}`, not just `header.bases` in isolation
        -- a real example from this repository's own reviewed baseline is
        `{_topmost_scope_suffix(b) for b in header.bases + header.virtual_bases}`."""
        src = "def f(header):\n    return {b for b in header.bases}\n"
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ["x.py::f::bases::{b for b in header.bases}::header.bases::1"]

    def test_keys_two_keyword_argument_reads_by_their_own_call(self) -> None:
        """Two reads of the identical attribute, both wrapped in a keyword
        argument, but of two *different* calls -- each must climb to its
        own enclosing call rather than collapsing onto a shared inner
        boundary, so the two keys stay distinct by more than just the
        occurrence counter."""
        src = (
            "def f(t_old, t_new):\n"
            "    old_call(value=t_old.bases)\n"
            "    new_call(value=t_new.bases)\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::bases::old_call(value=t_old.bases)::t_old.bases::1",
            "x.py::f::bases::new_call(value=t_new.bases)::t_new.bases::1",
        ]


class TestAttrgetterMatchedAtConstruction:
    """`operator.attrgetter(...)`/`attrgetter(...)` is matched at the point
    of *construction*, not only when the constructed getter is called
    immediately -- the field will be read on whatever the getter is
    eventually called with, regardless of how that call happens (Codex
    review, fresh evidence: matching only an immediate outer call missed
    the equally common callback spelling)."""

    def test_detects_an_attrgetter_used_as_a_sort_key_callback(self) -> None:
        """`sorted(records, key=operator.attrgetter("bases"))` -- the
        getter is handed to `sorted` as a callback, never itself
        immediately called at the same expression."""
        src = (
            "import operator\n"
            "def f(records):\n"
            '    return sorted(records, key=operator.attrgetter("bases"))\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::sorted(records, key=operator.attrgetter("bases"))::'
            'operator.attrgetter("bases")::1'
        ]

    def test_detects_an_attrgetter_used_as_a_map_callback(self) -> None:
        """The bare `attrgetter(...)` spelling as a positional callback
        argument: `map(attrgetter("bases"), records)`."""
        src = (
            "from operator import attrgetter\n"
            "def f(records):\n"
            '    return map(attrgetter("bases"), records)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::map(attrgetter("bases"), records)::attrgetter("bases")::1'
        ]

    def test_detects_an_attrgetter_indirected_through_a_local_variable(
        self,
    ) -> None:
        """`getter = operator.attrgetter("bases"); getter(rec)` splits the
        constructor call and the application call across two statements --
        the construction alone is now sufficient, so this two-step
        indirection is caught as a side effect without needing dedicated
        local-alias resolution for `attrgetter` itself (unlike `getattr`,
        which still needs one, since a bare `getattr` reference alone
        reads nothing until it's actually called)."""
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    getter = operator.attrgetter("bases")\n'
            "    return getter(rec)\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::operator.attrgetter("bases")::'
            'operator.attrgetter("bases")::1'
        ]


class TestLocallyBoundNamesCoversNestedDefAndClassNames:
    """`_locally_bound_names()` must recognize a `def`/`class` statement's
    own *name* as a locally-bound name, not just a parameter -- an
    ordinary, unrelated function definition sharing a builtin-looking name
    must not be treated as the real builtin (Codex review, fresh
    evidence)."""

    def test_ignores_a_module_level_function_shadowing_getattr(self) -> None:
        """`def getattr(obj, name): ...` at module scope, followed by an
        ordinary call to it, must not be treated as the real `getattr`
        builtin."""
        src = (
            "def getattr(obj, name):\n"
            "    return None\n"
            "def f(rec):\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_nested_function_shadowing_getattr(self) -> None:
        """The identical shadowing for a `def getattr(...):` nested
        *inside* the calling function itself, not just at module scope."""
        src = (
            "def f(rec):\n"
            "    def getattr(obj, name):\n"
            "        return None\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_class_shadowing_attrgetter(self) -> None:
        """The identical shadowing rule applies to a `class attrgetter:
        ...` definition, not just a `def`."""
        src = (
            "class attrgetter:\n"
            "    def __call__(self, name):\n"
            "        return name\n"
            "def f(rec):\n"
            '    return attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_real_getattr_call_despite_a_shadowing_def_in_a_sibling(
        self,
    ) -> None:
        """Negative control: a shadowing `def getattr(...):` at module
        scope must not leak into an unrelated sibling function's own
        genuine `getattr()` call."""
        src = (
            "def getattr(obj, name):\n"
            "    return None\n"
            "def g(rec):\n"
            "    import builtins\n"
            '    return builtins.getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::g::bases::builtins.getattr(rec, "bases")::'
            'builtins.getattr(rec, "bases")::1'
        ]
