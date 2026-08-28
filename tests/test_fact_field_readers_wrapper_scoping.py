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

Covers eleven independent Codex-review findings across several review
rounds:

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

5. **A real regression in finding 4's own fix**: a first version tracked
   a ``nearest_func`` parameter, skipping class layers the same way
   ``_lexical_function_parents`` deliberately does for *closure* purposes
   -- but a method's own name binds into its *class*, not its enclosing
   function/module, so ``class C: def getattr(self, name): ...`` made an
   unrelated function elsewhere in the module read as if it had a local
   ``getattr`` binding.

6. **``_enclosing_qualnames()`` attributed a parameter default value/
   annotation to the function's own body scope**, even though it
   evaluates at *def-time*, in whatever scope directly contains the
   ``def`` statement -- so ``def f(getattr, x=getattr(rec, "bases")):
   ...`` wrongly excluded a real builtin read, since the default sits on
   the function's own signature line.

7. **No branch recognized a mapping-based field read at all** --
   ``vars(rec)["bases"]``/``rec.__dict__["bases"]`` both read the exact
   same normalized legacy value ``rec.bases`` does, through the
   instance's own ``__dict__`` mapping rather than attribute-lookup
   machinery, invisible to every existing branch (``ast.Attribute``,
   ``getattr()``, ``attrgetter()``, ``__getattribute__()``). Added as a
   new ``ast.Subscript`` branch with a literal string key, gated on
   ``_shadowed()`` for the ``vars`` spelling (an ordinary bare-name call)
   and matched unconditionally for ``.__dict__`` (an attribute access,
   nothing for a local binding to shadow).

8. **``_locally_bound_names()`` never visited ``ast.Import``/``ast.
   ImportFrom`` at all** -- ``from helper import getattr`` then
   ``getattr(rec, "bases")`` was still treated as the real builtin, since
   an import statement's own binding was invisible the same way a bare
   parameter/``def``/``class`` name once was (findings 4-5 above). Fixed
   by recording each imported name against its containing scope, carved
   out for the specific imports this module already recognizes as a
   genuine alias *source* (``from builtins import getattr``/``object``/
   ``type``, ``from operator import attrgetter``, ``import builtins``/
   ``operator``) -- recording one of those as a "shadow" of itself would
   have broken the very recognition it exists to enable.

9. **The mapping-subscript branch (finding 7) missed the ``dict.get()``
   spelling of the identical read** -- ``vars(rec).get("bases")``/
   ``rec.__dict__.get("bases")`` read the exact same normalized legacy
   value, but neither is an ``ast.Subscript``, so both were invisible to
   that branch. The shared "is this a mapping over the instance's own
   ``__dict__``" check (``vars(...)``/``.__dict__``) was factored out
   into ``_is_mapping_receiver()``, reused by both the subscript and the
   new ``.get()`` branch, so the two forms can't independently drift on
   what counts as a recognized mapping receiver.

10. **``_is_mapping_receiver()`` only ever matched the bare literal
    spelling ``vars``** -- ``import builtins; builtins.vars(rec)
    ["bases"]`` (a qualified call through a real ``builtins`` alias) and
    ``read_map = vars; read_map(rec).get("bases")`` (a real ``vars``
    alias) were both invisible, the identical gap ``_builtins_getattr_
    aliases()`` already closed for ``getattr`` specifically. Fixed with a
    new, generalized ``_builtins_symbol_aliases()`` -- the *symbol*-
    specific half of that same alias-resolution mechanism, taking the
    caller's already-resolved ``builtins_names`` as a parameter rather
    than re-deriving it -- applied to ``vars``.

11. **``_operator_attrgetter_aliases()``'s own assignment-chain
    resolution never recognized a *qualified* RHS** -- ``import operator
    as op; ag = op.attrgetter; ag("bases")(rec)`` was invisible, since
    ``_add_candidate()`` only ever matched a plain ``ast.Name`` value,
    the identical gap ``_builtins_getattr_aliases()``'s own
    ``qualified_candidates`` mechanism already closes for ``read_attr =
    builtins.getattr``. Fixed the same way: a qualified ``X.attrgetter``
    assignment is collected during the walk and resolved once the walk
    (and therefore ``operator_names``) is complete.
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

    def test_ignores_a_real_getattr_call_shadowed_by_a_method_of_the_same_name(
        self,
    ) -> None:
        """A real regression in the first version of this fix: `class C:
        def getattr(self, name): ...` -- a *method's* own name does not
        bind into the enclosing function/module namespace at all (it
        becomes a class attribute, `C.getattr`), so an unrelated
        function's own genuine `getattr()` call must still be detected,
        not wrongly excluded (Codex review, fresh evidence)."""
        src = (
            "class C:\n"
            "    def getattr(self, name):\n"
            "        return None\n"
            "def g(rec):\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::g::bases::getattr(rec, "bases")::getattr(rec, "bases")::1'
        ]

    def test_detects_a_real_attrgetter_call_despite_a_nested_class_sibling(
        self,
    ) -> None:
        """The identical class-body-transparency rule for a nested
        `class` (not just a nested `def`), and for the `attrgetter`
        spelling, not just `getattr`: `class attrgetter: ...` nested
        inside an unrelated sibling function `outer` must not leak into
        `f`'s own genuine `attrgetter()` call."""
        src = (
            "from operator import attrgetter\n"
            "def outer():\n"
            "    class attrgetter:\n"
            "        pass\n"
            "def f(rec):\n"
            '    return attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::attrgetter("bases")(rec)::attrgetter("bases")::1'
        ]


class TestDefaultValuesEvaluateInTheEnclosingScope:
    """A parameter default value/annotation evaluates at *def-time*, in
    whatever scope directly, syntactically contains the `def` statement --
    not the function's own body scope, even though it's textually part of
    the function's own signature line (Codex review, fresh evidence)."""

    def test_detects_a_getattr_call_in_a_default_shadowed_by_its_own_parameter(
        self,
    ) -> None:
        """`def f(getattr, x=getattr(rec, "bases")): ...` -- the default
        evaluates before `f`'s own parameter `getattr` exists, so this
        call genuinely reads the real builtin despite `f` declaring a
        same-named parameter."""
        src = 'def f(getattr, x=getattr(rec, "bases")):\n    return x\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::<module>::bases::getattr(rec, "bases")::getattr(rec, "bases")::1'
        ]

    def test_ignores_a_getattr_call_in_the_function_body_shadowed_by_its_own_parameter(
        self,
    ) -> None:
        """Negative control: an ordinary call in the function *body* (not
        a default) is still correctly excluded by its own same-named
        parameter -- this fix must not widen resolution past the
        def-time subtree it's actually about."""
        src = 'def f(getattr, rec):\n    return getattr(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_default_shadowed_by_a_real_outer_parameter(self) -> None:
        """Negative control: a default value nested inside a function
        that genuinely declares the shadowing parameter itself is still
        correctly excluded -- this fix only corrects the *function's own*
        signature line being wrongly attributed to itself, not shadowing
        in general."""
        src = (
            "def outer(getattr):\n"
            '    def f(x=getattr(rec, "bases")):\n'
            "        return x\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestMappingBasedFieldReads:
    """``vars(rec)["bases"]``/``rec.__dict__["bases"]`` -- both read the
    identical normalized legacy value ``rec.bases`` does, through the
    instance's own mapping rather than attribute-lookup machinery."""

    def test_detects_a_vars_call_subscripted_by_a_literal_field_name(
        self,
    ) -> None:
        src = 'def f(rec):\n    return vars(rec)["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::vars(rec)["bases"]::vars(rec)["bases"]::1']

    def test_detects_a_dunder_dict_subscripted_by_a_literal_field_name(
        self,
    ) -> None:
        src = 'def f(rec):\n    return rec.__dict__["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::rec.__dict__["bases"]::rec.__dict__["bases"]::1'
        ]

    def test_ignores_a_vars_call_shadowed_by_its_own_parameter(self) -> None:
        """Negative control: an ordinary parameter named ``vars`` shadows
        the builtin the same way a ``getattr``-named parameter already
        does for the other dynamic-read forms."""
        src = 'def f(vars, rec):\n    return vars(rec)["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_non_literal_subscript_key(self) -> None:
        """Negative control: a computed key can't be resolved statically
        -- the same "no type inference" limit every other dynamic-read
        form here already accepts."""
        src = "def f(rec, name):\n    return vars(rec)[name]\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_dict_subscript_naming_an_unrelated_key(self) -> None:
        """Negative control: an ordinary ``__dict__`` lookup for a key
        outside the five bridged fields must not be flagged."""
        src = 'def f(rec):\n    return rec.__dict__["unrelated"]\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestImportedNamesShadowBuiltinRecognition:
    """An import statement binds its target name too -- ``from helper
    import getattr`` then ``getattr(rec, "bases")`` must not be treated
    as the real builtin, the same way a shadowing parameter already
    isn't. The carve-out for this module's own recognized alias sources
    (``from builtins import getattr``, ``from operator import
    attrgetter``, etc.) is exercised separately below, since incorrectly
    treating one of those as a shadow of itself would be a regression in
    the opposite direction."""

    def test_ignores_a_getattr_call_shadowed_by_an_unrelated_import(self) -> None:
        src = (
            "from helper import getattr\n"
            "def f(rec):\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_getattr_call_shadowed_by_an_aliased_unrelated_import(
        self,
    ) -> None:
        src = (
            "from helper import read_attr as getattr\n"
            "def f(rec):\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_getattr_call_shadowed_by_a_function_scoped_import(
        self,
    ) -> None:
        """The identical shadow, established inside the function itself
        rather than at module scope."""
        src = (
            "def f(rec):\n"
            "    from helper import getattr\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_real_getattr_call_despite_an_unrelated_sibling_import(
        self,
    ) -> None:
        """Negative control: an unrelated import shadowing `getattr` in
        one function must not suppress detection of a genuine builtin
        read in an unrelated sibling function. `g`'s own read goes
        through `builtins.getattr` -- itself a recognized reader form --
        rather than a bare, unrecognized call, so this test actually
        exercises whether `f`'s own shadowing import incorrectly bleeds
        into `g`'s scope, instead of trivially passing regardless of
        scoping because neither function's call is recognized at all
        (Codex review, fresh evidence)."""
        src = (
            "from helper import getattr\n"
            "def f(rec):\n"
            '    return getattr(rec, "bases")\n'
            "def g(rec):\n"
            "    import builtins\n"
            '    return builtins.getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::g::bases::builtins.getattr(rec, "bases")::builtins.getattr(rec, "bases")::1'
        ]

    def test_a_bare_builtins_getattr_import_still_recognized(self) -> None:
        """The carve-out: `from builtins import getattr` is a genuine
        alias *source* this module already recognizes elsewhere
        (`_builtins_getattr_aliases`) -- recording it as a local shadow
        of itself would wrongly suppress the very recognition it exists
        to enable."""
        src = (
            "from builtins import getattr\n"
            "def f(rec):\n"
            '    return getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::getattr(rec, "bases")::getattr(rec, "bases")::1'
        ]

    def test_an_aliased_builtins_getattr_import_still_recognized(self) -> None:
        """The identical carve-out for the aliased spelling -- the alias
        name is the one that must stay unshadowed, not the bare
        ``getattr`` the import statement itself never binds here."""
        src = (
            "from builtins import getattr as g\n"
            "def f(rec):\n"
            '    return g(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::g(rec, "bases")::g(rec, "bases")::1']

    def test_a_bare_operator_attrgetter_import_still_recognized(self) -> None:
        """The identical carve-out for `from operator import
        attrgetter` -- the import that this module's own docstring
        requires before recognizing `attrgetter` at all must not also be
        read as shadowing the name it introduces."""
        src = (
            "from operator import attrgetter\n"
            "def f(rec):\n"
            '    return attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::attrgetter("bases")(rec)::attrgetter("bases")::1'
        ]

    def test_a_bare_operator_module_import_still_recognized(self) -> None:
        """The identical carve-out for a bare `import operator` (as
        opposed to `from operator import attrgetter`)."""
        src = (
            "import operator\n"
            "def f(rec):\n"
            '    return operator.attrgetter("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::operator.attrgetter("bases")(rec)::operator.attrgetter("bases")::1'
        ]


class TestMappingGetFieldReads:
    """``vars(rec).get("bases")``/``rec.__dict__.get("bases")`` -- the
    `dict.get()` spelling of the identical mapping read
    `TestMappingBasedFieldReads` already covers for the subscript form."""

    def test_detects_a_vars_get_call_with_a_literal_field_name(self) -> None:
        src = 'def f(rec):\n    return vars(rec).get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::vars(rec).get("bases")::vars(rec).get("bases")::1'
        ]

    def test_detects_a_dunder_dict_get_call_with_a_literal_field_name(self) -> None:
        src = 'def f(rec):\n    return rec.__dict__.get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::rec.__dict__.get("bases")::rec.__dict__.get("bases")::1'
        ]

    def test_detects_a_get_call_with_an_explicit_default(self) -> None:
        """A `.get()` call's optional second argument (the default) is
        accepted but not inspected -- matching how `getattr()`'s own
        third argument is treated elsewhere in this module."""
        src = 'def f(rec):\n    return vars(rec).get("bases", [])\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::vars(rec).get("bases", [])::vars(rec).get("bases", [])::1'
        ]

    def test_ignores_a_get_call_shadowed_by_its_own_vars_parameter(self) -> None:
        """Negative control: an ordinary parameter named `vars` shadows
        the builtin, the identical guard the subscript form already
        gets."""
        src = 'def f(vars, rec):\n    return vars(rec).get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_get_call_naming_an_unrelated_key(self) -> None:
        """Negative control: an ordinary `.get()` lookup for a key
        outside the five bridged fields must not be flagged."""
        src = 'def f(rec):\n    return rec.__dict__.get("unrelated")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_unrelated_dict_methods_get_call(self) -> None:
        """Negative control: an ordinary `.get()` call on some unrelated
        object (not `vars(...)`/`.__dict__`) must not be flagged, even
        when it happens to pass a bridged field name as its argument."""
        src = 'def f(rec, mapping):\n    return mapping.get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestVarsAliasesInMappingReads:
    """`_is_mapping_receiver()` resolves a real `vars` alias too, not
    just the bare literal spelling -- both a qualified `builtins.vars(...)`
    call through a real `builtins` alias and a plain assignment alias of
    `vars` itself."""

    def test_detects_a_qualified_builtins_vars_subscript(self) -> None:
        src = 'import builtins\ndef f(rec):\n    return builtins.vars(rec)["bases"]\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::builtins.vars(rec)["bases"]::builtins.vars(rec)["bases"]::1'
        ]

    def test_detects_an_assigned_vars_alias_get_call(self) -> None:
        src = 'read_map = vars\ndef f(rec):\n    return read_map(rec).get("bases")\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_map(rec).get("bases")::read_map(rec).get("bases")::1'
        ]

    def test_detects_an_imported_vars_alias(self) -> None:
        src = (
            'from builtins import vars as V\ndef f(rec):\n    return V(rec)["bases"]\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::V(rec)["bases"]::V(rec)["bases"]::1']

    def test_ignores_a_qualified_call_shadowed_by_a_builtins_parameter(self) -> None:
        """Negative control: a parameter named `builtins` shadows the
        real module, the identical guard the bare-`vars` form already
        gets."""
        src = (
            "import builtins\n"
            "def f(builtins, rec):\n"
            '    return builtins.vars(rec)["bases"]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_imported_vars_alias_shadowed_by_its_own_parameter(
        self,
    ) -> None:
        """Negative control: the aliased spelling is shadowed the
        identical way the bare spelling already is."""
        src = (
            "from builtins import vars as V\n"
            "def f(V, rec):\n"
            '    return V(rec)["bases"]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_an_unrelated_objects_vars_named_method(self) -> None:
        """Negative control: an unrelated object's own `.vars()` method
        (not `builtins.vars`) must not be flagged, even when it happens
        to pass a bridged field name as its argument."""
        src = (
            "class Fake:\n"
            "    def vars(self, rec):\n"
            "        return {}\n"
            "def f(rec):\n"
            "    fake = Fake()\n"
            '    return fake.vars(rec).get("bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestQualifiedAttrgetterAssignmentAliases:
    """``_operator_attrgetter_aliases()`` resolves a qualified assignment
    (``ag = op.attrgetter``, given a real ``operator`` alias ``op``) too,
    not only a plain-name assignment chain."""

    def test_detects_a_qualified_attrgetter_assignment(self) -> None:
        src = (
            "import operator as op\n"
            "ag = op.attrgetter\n"
            "def f(rec):\n"
            '    return ag("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::ag("bases")(rec)::ag("bases")::1']

    def test_detects_a_further_chained_qualified_assignment(self) -> None:
        """The qualified-assignment resolution feeds back into the
        existing plain-name chain, so a second alias of the qualified
        alias resolves too."""
        src = (
            "import operator as op\n"
            "ag = op.attrgetter\n"
            "ag2 = ag\n"
            "def f(rec):\n"
            '    return ag2("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::ag2("bases")(rec)::ag2("bases")::1']

    def test_ignores_a_qualified_attribute_on_an_unrelated_object(self) -> None:
        """Negative control: an unrelated object's own `.attrgetter`
        attribute (not a real `operator` alias) must not be recognized."""
        src = (
            "class Fake:\n"
            "    attrgetter = None\n"
            "def f(rec):\n"
            "    fake = Fake()\n"
            "    ag = fake.attrgetter\n"
            '    return ag("bases")(rec)\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


class TestUnboundGetattributeMethodAliases:
    """``_unbound_getattribute_method_aliases()`` resolves a plain-name
    alias of the unbound method itself (``read_attr = object.
    __getattribute__``), not only a call made directly off `object`/
    `type`/an alias of either receiver."""

    def test_detects_a_call_through_an_aliased_method(self) -> None:
        src = (
            "def f(rec):\n"
            "    read_attr = object.__getattribute__\n"
            '    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_detects_a_chained_alias_of_the_method(self) -> None:
        """The method alias resolves to a fixed point, so a second alias
        of the first also resolves."""
        src = (
            "def f(rec):\n"
            "    a = object.__getattribute__\n"
            "    b = a\n"
            '    return b(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::b(rec, "bases")::b(rec, "bases")::1']

    def test_detects_a_method_alias_through_an_aliased_receiver(self) -> None:
        """The method-alias resolution composes with an already-aliased
        receiver (`from builtins import object as O`), not just the bare
        `object`/`type` spellings."""
        src = (
            "from builtins import object as O\n"
            "def f(rec):\n"
            "    read_attr = O.__getattribute__\n"
            '    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_detects_a_method_alias_via_the_type_receiver(self) -> None:
        """The identical alias resolution for the `type.__getattribute__`
        spelling."""
        src = (
            "def f(rec):\n"
            "    read_attr = type.__getattribute__\n"
            '    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_ignores_a_method_alias_shadowed_by_a_parameter(self) -> None:
        """Negative control: an unrelated parameter reusing the alias name
        must not be treated as the resolved unbound-method alias."""
        src = 'def f(read_attr, rec):\n    return read_attr(rec, "bases")\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_ignores_a_call_with_a_non_matching_argument(self) -> None:
        """Negative control: the attribute name argument must still name a
        bridged attribute."""
        src = (
            "def f(rec):\n"
            "    read_attr = object.__getattribute__\n"
            '    return read_attr(rec, "unrelated_field")\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []
