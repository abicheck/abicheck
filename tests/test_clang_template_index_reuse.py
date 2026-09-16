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

"""One template-parameter index build per raw clang AST, not per parser.

``_ClangAstParser.__init__`` rebuilt all three template-parameter indexes
(``_index_template_param_kinds``/``_index_template_param_defaults``/
``_index_template_param_names``) every time a parser was constructed, and a
single request routinely constructs several over the *same* tree: the legacy
export-bound parse and the neutral parse of each side, plus one per member of
a directory/package release fan-out sharing a header context. A measured
six-DSO shared-header comparison built 14 parsers over 2 distinct roots.

Three claims are tested here, and they are deliberately separate:

* **Reuse happens.** Each builder runs once per admissible key rather than
  once per parser -- across the neutral parse, across sequential members, and
  across pooled members sharing one ``contextvars`` context.
* **Reuse is scoped.** A different AST never reads another's answers, a later
  request never inherits an earlier one's, and outside an acquisition scope
  nothing is shared or retained at all.
* **Reuse changes nothing observable.** The bundled indexes equal what the
  three builders answer on their own, for every shape their registration
  semantics were painstakingly tuned for (``previousDecl`` redeclaration
  merging, renamed parameters, an added default, genuinely ambiguous sibling
  nested templates, reopened namespaces), and a real compiled old/new pair
  still produces the identical findings.

The property-style class at the end follows ``AGENTS.md``'s "primitive-level
property tests" guidance: ``build_template_param_indexes`` is a new reusable
primitive whose contract ("the same answers the three builders give, frozen,
and never dependent on how many consumers ask") is stated as invariants over
generated inputs rather than as one example per remembered bug.
"""

from __future__ import annotations

import contextvars
import copy
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck import dumper_cache
from abicheck.dumper_cache import ast_acquisition_scope
from abicheck.extract.headers.clang.template_param_indexes import (
    TemplateParamIndexes,
    build_template_param_indexes,
)
from abicheck.extract.headers.clang.templates import (
    _index_template_param_defaults,
    _index_template_param_kinds,
    _index_template_param_names,
)
from tests._dumper_clang_vtable_helpers import _record, _tu

# ── fixtures: hand-built `-ast-dump=json`-shaped nodes ────────────────────────


def _param(kind: str, name: str | None, default: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": kind}
    if name is not None:
        node["name"] = name
    if default is not None:
        node["defaultArg"] = {"type": {"qualType": default}}
    return node


def _class_template(
    name: str,
    *params: dict[str, Any],
    node_id: str | None = None,
    previous_decl: str | None = None,
    inner: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "kind": "ClassTemplateDecl",
        "name": name,
        "inner": [*params, *inner],
    }
    if node_id is not None:
        node["id"] = node_id
    if previous_decl is not None:
        node["previousDecl"] = previous_decl
    return node


def _namespace(name: str, *inner: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "NamespaceDecl", "name": name, "inner": list(inner)}


def _type_param(name: str | None, default: str | None = None) -> dict[str, Any]:
    return _param("TemplateTypeParmDecl", name, default)


#: A tree exercising every registration rule the three builders implement:
#: a plain template, a legal ``previousDecl`` redeclaration that both renames
#: its parameters and adds a default, a reopened namespace, and two sibling
#: nested templates that collide on one bare qualname with differing defaults
#: (the genuinely-ambiguous case, which must stay dropped).
def _rich_tree() -> dict[str, Any]:
    return _tu(
        _class_template("Plain", _type_param("T"), _type_param("U", "int")),
        # Redeclaration chain: declared with no default and one spelling of
        # the parameter names, redeclared with renamed parameters AND a newly
        # added dependent default.
        _class_template("Chain", _type_param("T"), _type_param("U"), node_id="0x1"),
        _class_template(
            "Chain",
            _type_param("X"),
            _type_param("Y", "X"),
            node_id="0x2",
            previous_decl="0x1",
        ),
        _namespace("ns", _class_template("Inner", _type_param("A", "double"))),
        # The same namespace, reopened later in the TU.
        _namespace("ns", _class_template("Other", _type_param("B"))),
        # Two same-named nested templates under two different explicit outer
        # specializations: one bare qualname, two different defaults.
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                _class_template("Nested", _type_param("V", "int")),
            ],
        },
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": "double"}},
                _class_template("Nested", _type_param("V", "double")),
            ],
        },
        _record("Holder"),
    )


def _independent(root: dict[str, Any]) -> dict[str, dict[str, list[str | None]]]:
    """What the three builders answer when each is called on its own.

    The oracle every parity assertion below compares against, and deliberately
    *not* ``build_template_param_indexes`` itself: an oracle that reused the
    function under test would make the whole comparison a tautology.
    """

    return {
        "kinds": _index_template_param_kinds(root),
        "defaults": _index_template_param_defaults(root),
        "names": _index_template_param_names(root),
    }


def _as_plain(indexes: TemplateParamIndexes) -> dict[str, dict[str, list[str | None]]]:
    return {
        field: {key: list(row) for key, row in getattr(indexes, field).items()}
        for field in ("kinds", "defaults", "names")
    }


# ── counting the builds ───────────────────────────────────────────────────────


class _BuildCounter:
    """Counts real index builds, at the one function the reuse gate wraps.

    Counted at ``build_template_param_indexes`` rather than at some global
    JSON/traversal statistic: the claim is about *this* builder under *this*
    key, and a counter attached to anything broader would move for unrelated
    reasons and prove nothing about either.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.roots: list[int] = []
        import abicheck.dumper_clang as dumper_clang

        original = dumper_clang.build_template_param_indexes

        def counted(root: dict[str, Any]) -> TemplateParamIndexes:
            self.roots.append(id(root))
            return original(root)

        monkeypatch.setattr(dumper_clang, "build_template_param_indexes", counted)

    @property
    def builds(self) -> int:
        return len(self.roots)

    @property
    def distinct_roots(self) -> int:
        return len(set(self.roots))


def _make_parser(root: dict[str, Any], exports: set[str] | None = None) -> Any:
    from abicheck.dumper_clang import _ClangAstParser

    return _ClangAstParser(root, exports or set(), set())


# ── reuse happens ─────────────────────────────────────────────────────────────


class TestReuseWithinOneRequest:
    def test_many_parsers_over_one_root_build_the_indexes_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Six members plus a neutral parse: one build, not seven.

        Six rather than two because the number is the point -- a fan-out's
        cost per member is exactly what this removes, so a two-parser test
        could pass against an implementation that merely memoized the
        immediately-preceding parser.
        """
        counter = _BuildCounter(monkeypatch)
        root = _rich_tree()
        with ast_acquisition_scope():
            parsers = [_make_parser(root) for _ in range(7)]
        assert len(parsers) == 7
        assert counter.builds == 1
        # Every parser sees the identical object, not merely an equal one.
        first = parsers[0]
        for other in parsers[1:]:
            assert other._template_param_kinds_by_qualname is (
                first._template_param_kinds_by_qualname
            )
            assert other._template_param_defaults_by_qualname is (
                first._template_param_defaults_by_qualname
            )
            assert other._template_param_names_by_qualname is (
                first._template_param_names_by_qualname
            )

    def test_pooled_members_sharing_one_context_build_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The release fan-out's real shape: workers on context copies.

        ``cli_compare_release_pairwise`` runs each library in a copy of the
        parent ``contextvars`` context, which is what carries the acquisition
        scope into the worker. A sequential-only test would not cover the path
        the six-DSO measurement actually took.
        """
        counter = _BuildCounter(monkeypatch)
        root = _rich_tree()
        with ast_acquisition_scope():
            parent = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = [
                    pool.submit(parent.copy().run, _make_parser, root) for _ in range(6)
                ]
                parsers = [f.result(timeout=30) for f in results]
        assert len(parsers) == 6
        assert counter.builds == 1

    def test_a_second_pass_over_a_retained_root_does_not_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A follow-up consumer of the same tree (the graph/warm-cache pass)."""
        counter = _BuildCounter(monkeypatch)
        root = _rich_tree()
        with ast_acquisition_scope():
            _make_parser(root)
            _make_parser(root)
            _make_parser(root)
        assert counter.builds == 1


# ── reuse is scoped ───────────────────────────────────────────────────────────


class TestReuseIsScoped:
    def test_two_distinct_asts_never_share_an_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counter = _BuildCounter(monkeypatch)
        first = _rich_tree()
        second = _tu(
            _class_template("Plain", _type_param("T"), _type_param("U", "long"))
        )
        with ast_acquisition_scope():
            a = _make_parser(first)
            b = _make_parser(second)
            # Both roots stay alive here, so neither address can be recycled.
            assert counter.builds == 2
            assert counter.distinct_roots == 2
        assert list(a._template_param_defaults_by_qualname["Plain"]) == [None, "int"]
        assert list(b._template_param_defaults_by_qualname["Plain"]) == [None, "long"]

    def test_an_edited_header_produces_a_new_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new version of the headers is a new tree, so a new answer.

        Stated over the values, not just the build count: the failure this
        guards against is a *stale* answer surviving an edit, which a count
        alone would not catch.
        """
        counter = _BuildCounter(monkeypatch)
        before = _tu(_class_template("Vec", _type_param("T"), _type_param("U", "int")))
        after = _tu(
            _class_template(
                "Vec",
                _type_param("T"),
                _type_param("U", "int"),
                _type_param("W", "char"),
            )
        )
        with ast_acquisition_scope():
            old_parser = _make_parser(before)
            new_parser = _make_parser(after)
        assert counter.builds == 2
        assert list(old_parser._template_param_names_by_qualname["Vec"]) == ["T", "U"]
        assert list(new_parser._template_param_names_by_qualname["Vec"]) == [
            "T",
            "U",
            "W",
        ]

    def test_a_new_request_does_not_inherit_the_previous_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each scope is a fresh table; nothing leaks between requests."""
        counter = _BuildCounter(monkeypatch)
        root = _rich_tree()
        with ast_acquisition_scope():
            _make_parser(root)
        assert counter.builds == 1
        with ast_acquisition_scope():
            _make_parser(root)
        assert counter.builds == 2

    def test_without_a_scope_nothing_is_shared_or_retained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The no-scope path is untouched: a local build, no retained root.

        The retention half matters as much as the sharing half -- a direct
        ``dumper.dump()`` caller must not start holding a multi-GB tree for the
        life of a table it never installed.
        """
        counter = _BuildCounter(monkeypatch)
        retained: list[Any] = []
        monkeypatch.setattr(
            dumper_cache, "retain_ast_context_object", lambda v: retained.append(v)
        )
        root = _rich_tree()
        assert not dumper_cache.ast_acquisition_active()
        _make_parser(root)
        _make_parser(root)
        assert counter.builds == 2
        assert retained == []


# ── member state never reaches the shared answer ──────────────────────────────


class TestMemberIndependence:
    #: Every constructor argument that distinguishes one member's parser from
    #: another's, swept together rather than one axis at a time -- these are
    #: exactly the inputs the shared key deliberately does *not* fold in, so
    #: the sweep is the audit. `dumper.py` builds the neutral parser from the
    #: same root with empty export sets and `no_binary_evidence=False`, which
    #: is the second row.
    _MEMBER_INPUTS = [
        dict(exported_dynamic=frozenset(), exported_static=frozenset()),
        dict(
            exported_dynamic=frozenset(),
            exported_static=frozenset(),
            no_binary_evidence=False,
        ),
        dict(
            exported_dynamic=frozenset({"_ZN2ns5InnerE"}), exported_static=frozenset()
        ),
        dict(exported_dynamic=frozenset(), exported_static=frozenset({"Chain"})),
        dict(
            exported_dynamic=frozenset({"a", "b", "c"}),
            exported_static=frozenset({"d"}),
            public_header_paths=[],
        ),
        dict(
            exported_dynamic=frozenset({"z"}),
            exported_static=frozenset(),
            public_header_paths=["/some/public.hpp"],
            public_dir_paths=["/some"],
        ),
        dict(
            exported_dynamic=frozenset(),
            exported_static=frozenset(),
            target_triple="aarch64-unknown-linux-gnu",
        ),
        dict(
            exported_dynamic=frozenset(),
            exported_static=frozenset(),
            is_cxx=False,
        ),
        dict(
            exported_dynamic=frozenset(),
            exported_static=frozenset(),
            no_binary_evidence=True,
        ),
    ]

    @pytest.mark.parametrize("member", _MEMBER_INPUTS)
    def test_no_member_input_changes_the_header_index_values(
        self, member: dict[str, Any]
    ) -> None:
        """Different members, one header answer.

        The audit that makes sharing legal at all, stated executably. The
        shared key is the AST's identity and nothing else, which is only sound
        because none of a parser's *other* constructor inputs -- export
        evidence, the public-header/-directory selection, the target triple,
        the C-vs-C++ language mode, the header-only evidence flag -- can move
        what these three builders answer. If any row here ever fails, the
        sharing is unsound and the build-count tests above are measuring a bug
        rather than a win.
        """
        from abicheck.dumper_clang import _ClangAstParser

        root = _rich_tree()
        baseline = _independent(root)
        kwargs = dict(member)
        parser = _ClangAstParser(
            root,
            set(kwargs.pop("exported_dynamic")),
            set(kwargs.pop("exported_static")),
            **kwargs,
        )
        assert (
            _as_plain(
                TemplateParamIndexes(
                    kinds=parser._template_param_kinds_by_qualname,
                    defaults=parser._template_param_defaults_by_qualname,
                    names=parser._template_param_names_by_qualname,
                )
            )
            == baseline
        )

    def test_member_order_does_not_change_the_shared_answer(self) -> None:
        """Reordering the members, and which one asks first, changes nothing.

        The first member through the scope is the one that actually builds
        the bundle, and every later member is served that build. So "which
        member went first" is a real input to the shared value even though it
        is not part of the key -- and it would matter if any builder read
        member state. Stated over several genuinely different orders and
        export sets rather than one swap, since a single reordering only
        forecloses the pair it names.
        """
        from abicheck.dumper_clang import _ClangAstParser

        root = _rich_tree()
        expected = _independent(root)
        members = [
            {"exported_dynamic": {"a"}, "no_binary_evidence": False},
            {"exported_dynamic": set(), "no_binary_evidence": True},
            {"exported_dynamic": {"b", "c"}, "public_header_paths": []},
            {"exported_dynamic": {"_ZN2ns5InnerE"}, "is_cxx": False},
            {"exported_dynamic": {"d"}, "target_triple": "aarch64-unknown-linux-gnu"},
            {"exported_dynamic": set(), "exported_static_extra": True},
        ]
        orders = [
            list(range(len(members))),
            list(reversed(range(len(members)))),
            [3, 0, 5, 1, 4, 2],
            [5, 4, 0, 2, 1, 3],
        ]
        for order in orders:
            with ast_acquisition_scope():
                for index in order:
                    spec = dict(members[index])
                    dynamic = set(spec.pop("exported_dynamic"))
                    static = (
                        {"s"} if spec.pop("exported_static_extra", False) else set()
                    )
                    parser = _ClangAstParser(root, dynamic, static, **spec)
                    actual = {
                        "kinds": {
                            k: list(v)
                            for k, v in parser._template_param_kinds_by_qualname.items()
                        },
                        "defaults": {
                            k: list(v)
                            for k, v in parser._template_param_defaults_by_qualname.items()
                        },
                        "names": {
                            k: list(v)
                            for k, v in parser._template_param_names_by_qualname.items()
                        },
                    }
                    assert actual == expected, (order, index)

    def test_member_local_state_stays_distinct_across_shared_parsers(self) -> None:
        """Sharing the indexes must not share anything else.

        The categorized declaration lists, the anonymous-ordinal state and the
        export-derived surface inputs are per member by construction; a bug
        that shared the whole parser would pass every count test above while
        collapsing six members' findings into one.
        """
        from abicheck.dumper_clang import _ClangAstParser

        root = _rich_tree()
        with ast_acquisition_scope():
            first = _ClangAstParser(root, {"a"}, set())
            second = _ClangAstParser(root, {"b"}, set())
        assert first._template_param_kinds_by_qualname is (
            second._template_param_kinds_by_qualname
        )
        assert first._exported_dynamic != second._exported_dynamic
        for attr in (
            "_functions",
            "_variables",
            "_records",
            "_enums",
            "_typedefs",
            "_anonymous_ordinal_state",
            "_record_vtable_index",
        ):
            assert getattr(first, attr) is not getattr(second, attr), attr


# ── the shared value is read-only to its depth ────────────────────────────────


class TestSharedIndexesAreReadOnly:
    def _indexes(self) -> TemplateParamIndexes:
        return build_template_param_indexes(_rich_tree())

    def test_the_mapping_rejects_assignment_and_deletion(self) -> None:
        indexes = self._indexes()
        for mapping in (indexes.kinds, indexes.defaults, indexes.names):
            assert mapping, "the fixture must actually populate every index"
            key = next(iter(mapping))
            with pytest.raises(TypeError):
                mapping[key] = ["poison"]  # type: ignore[index]
            with pytest.raises(TypeError):
                del mapping[key]  # type: ignore[attr-defined]
            with pytest.raises(AttributeError):
                mapping.clear()  # type: ignore[attr-defined]

    def test_a_row_cannot_be_written_through(self) -> None:
        """A `frozen=True` dataclass over mutable lists would fail this."""
        indexes = self._indexes()
        for mapping in (indexes.kinds, indexes.defaults, indexes.names):
            for key, row in mapping.items():
                assert isinstance(row, tuple), key
                with pytest.raises(TypeError):
                    row[0] = "poison"  # type: ignore[index]
                assert not hasattr(row, "append")
                # A row's own elements are immutable outright.
                for element in row:
                    assert element is None or isinstance(element, str)

    def test_the_bundle_itself_rebinds_nothing(self) -> None:
        indexes = self._indexes()
        with pytest.raises(Exception):
            indexes.kinds = {}  # type: ignore[misc]

    def test_a_consumer_cannot_corrupt_another_members_view(self) -> None:
        """The whole point of freezing, through the real consumer interface.

        Exercised via the parsers rather than the bundle directly: what must
        hold is that nothing reachable from a parser's supported surface lets
        one member write into what another reads.
        """
        root = _rich_tree()
        with ast_acquisition_scope():
            first = _make_parser(root)
            second = _make_parser(root)
        before = dict(second._template_param_defaults_by_qualname)
        with pytest.raises(TypeError):
            first._template_param_defaults_by_qualname["Plain"] = ["poison"]
        assert dict(second._template_param_defaults_by_qualname) == before


# ── the answers themselves are unchanged ──────────────────────────────────────


class TestSemanticParity:
    def test_the_bundle_equals_the_three_builders_on_the_rich_tree(self) -> None:
        root = _rich_tree()
        assert _as_plain(build_template_param_indexes(root)) == _independent(root)

    def test_redeclaration_merge_survives_renamed_parameters(self) -> None:
        """``_register_template_param_metadata``'s sixth/eighth-round rules.

        A legal ``previousDecl`` redeclaration that renames its parameters and
        adds a dependent default must still contribute that default, translated
        into the *tracked* parameter name. Asserted on the bundle so a fused or
        re-derived implementation could not quietly drop it.
        """
        indexes = build_template_param_indexes(_rich_tree())
        assert list(indexes.names["Chain"]) == ["T", "U"]
        assert list(indexes.defaults["Chain"]) == [None, "T"]

    def test_genuinely_ambiguous_sibling_nested_templates_stay_dropped(self) -> None:
        """Two unrelated nested templates on one bare qualname.

        Ambiguity is decided per index, not per qualname, and the fixture is
        built so that it *has* to be: the two nested ``Nested`` templates agree
        on parameter kinds and names and differ only in their defaults, so the
        defaults index must drop the key while the other two legitimately keep
        it. Asserting all three were dropped would have been the easier claim
        and the wrong one -- it would fail against correct behaviour.
        """
        indexes = build_template_param_indexes(_rich_tree())
        assert "Nested" not in indexes.defaults
        assert list(indexes.kinds["Nested"]) == [None]
        assert list(indexes.names["Nested"]) == ["V"]

    def test_reopened_namespaces_keep_their_qualified_keys(self) -> None:
        indexes = build_template_param_indexes(_rich_tree())
        assert list(indexes.defaults["ns::Inner"]) == ["double"]
        assert list(indexes.names["ns::Other"]) == ["B"]

    def test_reuse_does_not_change_what_a_parser_reads(self) -> None:
        """Scope on vs. scope off: identical index values."""
        root = _rich_tree()
        without = _make_parser(root)
        with ast_acquisition_scope():
            within = _make_parser(root)
        for attr in (
            "_template_param_kinds_by_qualname",
            "_template_param_defaults_by_qualname",
            "_template_param_names_by_qualname",
        ):
            assert {k: list(v) for k, v in getattr(without, attr).items()} == {
                k: list(v) for k, v in getattr(within, attr).items()
            }, attr


# ── primitive-level properties ────────────────────────────────────────────────

_NAMES = st.sampled_from(["T", "U", "V", "X", "Y", None])
_DEFAULTS = st.sampled_from([None, "int", "double", "T", "U", "char"])
_PARAM_KINDS = st.sampled_from(
    ["TemplateTypeParmDecl", "NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"]
)


@st.composite
def _template_nodes(draw: Any) -> dict[str, Any]:
    """A TU of class templates over a small, deliberately collision-prone
    alphabet of names/scopes -- so redeclaration chains, ambiguous repeats and
    reopened namespaces arise by generation rather than by being hand-listed.
    """
    count = draw(st.integers(min_value=1, max_value=6))
    decls: list[dict[str, Any]] = []
    for i in range(count):
        params = [
            _param(
                draw(_PARAM_KINDS),
                draw(_NAMES),
                draw(_DEFAULTS),
            )
            for _ in range(draw(st.integers(min_value=0, max_value=3)))
        ]
        node = _class_template(
            draw(st.sampled_from(["A", "B", "C"])),
            *params,
            node_id=draw(st.sampled_from([None, "0x1", "0x2"])),
            previous_decl=draw(st.sampled_from([None, "0x1", "0x2"])),
        )
        scope = draw(st.sampled_from([None, "ns", "ns2"]))
        decls.append(node if scope is None else _namespace(scope, node))
    return _tu(*decls)


class TestBuildTemplateParamIndexesProperties:
    """``build_template_param_indexes``'s contract, as invariants.

    ``AGENTS.md``'s primitive-level property guidance: a new reusable
    primitive gets a standalone property class stating what it promises,
    decoupled from any one caller, because a hand-written example only
    forecloses the input it names.
    """

    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    @given(root=_template_nodes())
    def test_it_answers_exactly_what_the_three_builders_answer(
        self, root: dict[str, Any]
    ) -> None:
        assert _as_plain(build_template_param_indexes(root)) == _independent(root)

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(root=_template_nodes())
    def test_it_is_deterministic_and_never_mutates_its_input(
        self, root: dict[str, Any]
    ) -> None:
        snapshot = copy.deepcopy(root)
        first = _as_plain(build_template_param_indexes(root))
        assert root == snapshot
        assert _as_plain(build_template_param_indexes(root)) == first

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(root=_template_nodes())
    def test_every_row_is_read_only_to_its_depth(self, root: dict[str, Any]) -> None:
        indexes = build_template_param_indexes(root)
        for mapping in (indexes.kinds, indexes.defaults, indexes.names):
            with pytest.raises(TypeError):
                mapping["poison"] = ()  # type: ignore[index]
            for row in mapping.values():
                assert isinstance(row, tuple)
                assert all(e is None or isinstance(e, str) for e in row)

    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    @given(root=_template_nodes())
    def test_how_many_consumers_ask_never_changes_the_answer(
        self, root: dict[str, Any]
    ) -> None:
        """The reuse invariant itself, over generated trees.

        One parser, six parsers, scope or no scope: the same values. This is
        what a fused traversal or a key that folded in a member-dependent
        input would break, and it is stated over the *values* rather than over
        a build count so it cannot be satisfied by caching a wrong answer.
        """
        expected = _independent(root)

        def read(parser: Any) -> dict[str, dict[str, list[str | None]]]:
            return {
                "kinds": {
                    k: list(v)
                    for k, v in parser._template_param_kinds_by_qualname.items()
                },
                "defaults": {
                    k: list(v)
                    for k, v in parser._template_param_defaults_by_qualname.items()
                },
                "names": {
                    k: list(v)
                    for k, v in parser._template_param_names_by_qualname.items()
                },
            }

        assert read(_make_parser(root)) == expected
        with ast_acquisition_scope():
            for _ in range(6):
                assert read(_make_parser(root)) == expected


# ── end to end, on real compiled artifacts ────────────────────────────────────

_CLANG_L2 = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or shutil.which("clang") is None
    or shutil.which("g++") is None
    or platform.machine().lower() not in {"x86_64", "amd64"},
    reason="clang L2 end-to-end parity needs clang+g++ on x86-64 Linux",
)

_TEMPLATE_HEADER = """
#pragma once
namespace fx {
template <class T, class U> struct Box;
template <class X, class Y = X> struct Box { X x; Y y; X get() const; };

template <class T, class U = int> struct Pair { T first; U second; T take() const; };
template <> struct Pair<float, int> { float first; int second; float take() const; };

template <class T> struct Outer { template <class V = T> struct Inner { V v; }; T value; };
template <> struct Outer<int> { template <class V = int> struct Inner { V v; }; int value; };

template <class T> struct Wrap { using type = Box<T, T>; };
template <class T> using WrapT = typename Wrap<T>::type;

struct Node : Box<double> {
  virtual ~Node();
  virtual double weight() const;
%(extra_virtual)s  WrapT<float> axis() const;
};

double reduce(const Node& n);
%(removed)s}
"""

_SOURCE = """
#include "api.hpp"
namespace fx {
template struct Box<int, int>;
template struct Box<double, double>;
float Pair<float, int>::take() const { return first; }
Node::~Node() {}
double Node::weight() const { return x + y; }
%(extra_virtual_def)s WrapT<float> Node::axis() const { return WrapT<float>(); }
double reduce(const Node& n) { return n.weight(); }
%(removed_def)s}
"""


def _build_side(root: Path, *, broken: bool) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    header = root / "api.hpp"
    header.write_text(
        _TEMPLATE_HEADER
        % {
            # The new side adds a virtual (a vtable change through a
            # defaulted-specialization base) and drops a public function.
            "extra_virtual": "  virtual double norm() const;\n" if broken else "",
            "removed": "" if broken else "double shape_count();\n",
        },
        encoding="utf-8",
    )
    source = root / "api.cpp"
    source.write_text(
        _SOURCE
        % {
            "extra_virtual_def": "double Node::norm() const { return x; }"
            if broken
            else "",
            "removed_def": "" if broken else "double shape_count() { return 1.0; }\n",
        },
        encoding="utf-8",
    )
    so = root / "libfx.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            "-std=c++17",
            f"-I{root}",
            str(source),
            "-o",
            str(so),
        ],
        check=True,
        capture_output=True,
    )
    return so, header


def _compare_sides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    old_so, old_h = _build_side(tmp_path / "old", broken=False)
    new_so, new_h = _build_side(tmp_path / "new", broken=True)
    old_snap = dump(old_so, [old_h], [old_h.parent])
    new_snap = dump(new_so, [new_h], [new_h.parent])
    return compare(old_snap, new_snap), old_snap, new_snap


@_CLANG_L2
def test_a_real_comparison_keeps_its_full_finding_set_under_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole real comparison under reuse off vs. on, not a verdict or a count.

    Both arms run **inside** an acquisition scope, and the only thing that
    varies is whether the index bundle is shared. That is load-bearing, and
    the first version of this test got it wrong: it compared a scoped run
    against an *unscoped* one, which also flips something this PR does not
    touch -- `header_ast_fields._parse_header_ast_fields` derives
    `semantic_ir` from the NEUTRAL parse inside a scope and from the LEGACY
    (export-bound) parse outside one. The two happen to agree on the
    fixture under one clang and not under another, so the test passed
    locally and failed on CI's 3.12 lane, asserting a difference that was
    never this change's. Holding the scope constant is what makes the
    comparison about the reuse.

    `AGENTS.md` asks a change to be proven through the public workflow and
    the rendered report, so this compares the *entire* serialized snapshot
    of each side plus the rendered JSON report -- not a verdict or a count,
    either of which would pass against an implementation that silently
    swapped one finding for another.
    """
    import json as _json

    from abicheck import dumper_clang
    from abicheck.reporter import to_json

    builds: dict[bool, int] = {}

    def _run(reuse: bool) -> tuple[Any, Any, Any]:
        """One comparison with reuse on or forced off, inside a scope."""
        count = 0
        real = dumper_clang.build_template_param_indexes

        def counted(root: dict[str, Any]) -> Any:
            nonlocal count
            count += 1
            return real(root)

        with monkeypatch.context() as patch:
            patch.setattr(dumper_clang, "build_template_param_indexes", counted)
            if not reuse:
                # The pre-change behaviour: rebuild per constructed parser.
                patch.setattr(dumper_clang, "_template_param_indexes_for", counted)
            with ast_acquisition_scope():
                result = _compare_sides(
                    tmp_path / ("arm_one" if reuse else "arm_two"), monkeypatch
                )
        builds[reuse] = count
        return result

    reuse_result, reuse_old, reuse_new = _run(True)
    plain_result, plain_old, plain_new = _run(False)

    # Prove both configurations really ran differently, by observing the
    # mechanism rather than its output -- otherwise an equality that held
    # because the second arm was served the first's answer would pass
    # (`AGENTS.md`: "a differential test must prove both of its
    # configurations actually ran").
    assert builds[True] == 2, builds
    assert builds[False] > builds[True], builds

    def _scrub_paths(value: Any) -> Any:
        """*value* with the two build roots' absolute paths normalized away.

        The arms build under two roots so neither can be served the other's
        AST cache entry; their paths therefore differ by construction and
        say nothing about the parse.
        """
        # Equal-length names on purpose: the compiled `.so` embeds its own
        # compile path, so roots of differing length would move `source_size`
        # for a reason that has nothing to do with the parse, and that field
        # would have to be excluded instead of checked.
        roots = (str(tmp_path / "arm_one"), str(tmp_path / "arm_two"))
        if isinstance(value, str):
            for root in roots:
                value = value.replace(root, "<root>")
            return value
        if isinstance(value, dict):
            return {k: _scrub_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_scrub_paths(v) for v in value]
        return value

    def _findings(result: Any) -> list[tuple[Any, ...]]:
        # `c.kind.value` (the slug), not `str(c.kind)` (which renders
        # "ChangeKind.X") -- the kind assertions below read as the vocabulary
        # the reports and docs use.
        return sorted(
            (c.kind.value, c.symbol or "", str(c.old_value), str(c.new_value))
            for c in result.changes
        )

    assert _findings(plain_result), "the fixture must produce real findings"
    assert _findings(reuse_result) == _findings(plain_result)
    assert reuse_result.verdict == plain_result.verdict

    # The expected breaks themselves, not only that the two arms agree: an
    # equality assertion alone would still hold if BOTH arms had silently
    # stopped detecting them.
    kinds = {kind for kind, *_ in _findings(reuse_result)}
    assert "type_vtable_changed" in kinds, kinds
    removed = {
        symbol
        for kind, symbol, *_ in _findings(reuse_result)
        if "removed" in kind.lower()
    }
    assert any("shape_count" in s for s in removed), (
        f"the export-loss break disappeared; kinds={kinds} removed={removed}"
    )

    def _surface(snap: Any) -> dict[str, Any]:
        """The whole serialized snapshot, minus what a rerun legitimately varies.

        Deliberately the full document rather than a hand-listed set of
        attributes -- the claim is that *nothing* the header AST decides
        moved, and a hand-listed projection only proves it for the fields
        whoever wrote the list thought of.
        """
        from abicheck.serialization import snapshot_to_dict

        document = snapshot_to_dict(snap)
        for volatile in (
            "library",
            "path",
            "timestamp",
            "created_at",
            "provenance",
            "source_mtime",
        ):
            document.pop(volatile, None)
        return _scrub_paths(document)

    assert _surface(reuse_old) == _surface(plain_old)
    assert _surface(reuse_new) == _surface(plain_new)

    # Vacuity guard on that equality: it is only evidence about template
    # handling if the parse actually reconstructed specialization spellings.
    rendered = _json.dumps(_surface(plain_old))
    assert "Outer<int>" in rendered, (
        "the fixture no longer exercises specialization-spelling "
        "reconstruction, so the snapshot equality proves nothing here"
    )

    def _stable(section: object) -> object:
        """*section* without `finding_id`, which folds in `source_location`.

        Two trees built under two roots legitimately disagree on it and on
        nothing else. `canonical_finding_id` -- the identity that is supposed
        to be stable across spellings and locations -- stays in the
        comparison, and matches.
        """
        if isinstance(section, list):
            return [_stable(item) for item in section]
        if isinstance(section, dict):
            return {k: _stable(v) for k, v in section.items() if k != "finding_id"}
        return section

    plain_report = _scrub_paths(_json.loads(to_json(plain_result)))
    reuse_report = _scrub_paths(_json.loads(to_json(reuse_result)))
    for section in ("changes", "summary", "analysis_assurance"):
        assert _stable(reuse_report.get(section)) == _stable(
            plain_report.get(section)
        ), section
    assert all(c.get("canonical_finding_id") for c in reuse_report["changes"])


@_CLANG_L2
def test_a_non_template_control_comparison_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The small control case: a header with no templates at all.

    A performance change that only ever fires on template-heavy input still
    has to leave the ordinary case bit-for-bit alone, and the regression it
    could plausibly cause there (an empty index shared where a populated one
    was expected) is invisible in the template fixture above.
    """
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    header_text = """
#pragma once
namespace plain {
struct Point { int x; int y; };
double distance(const Point& a, const Point& b);
struct Base { virtual ~Base(); virtual int kind() const; };
}
"""
    source_text = """
#include "plain.hpp"
namespace plain {
double distance(const Point& a, const Point& b) { return a.x - b.x; }
Base::~Base() {}
int Base::kind() const { return 1; }
}
"""
    from abicheck import dumper_clang

    results = []
    # Both arms inside a scope, varying only the reuse -- same reasoning as
    # the template test above: a scoped-vs-unscoped comparison would also
    # flip which parse `semantic_ir` is derived from, which this PR does not
    # touch.
    for reuse in (True, False):
        root = tmp_path / ("arm_one" if reuse else "arm_two")
        root.mkdir(parents=True, exist_ok=True)
        (root / "plain.hpp").write_text(header_text, encoding="utf-8")
        (root / "plain.cpp").write_text(source_text, encoding="utf-8")
        so = root / "libplain.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                "-std=c++17",
                f"-I{root}",
                str(root / "plain.cpp"),
                "-o",
                str(so),
            ],
            check=True,
            capture_output=True,
        )
        with monkeypatch.context() as patch:
            if not reuse:
                patch.setattr(
                    dumper_clang,
                    "_template_param_indexes_for",
                    dumper_clang.build_template_param_indexes,
                )
            with ast_acquisition_scope():
                snap = dump(so, [root / "plain.hpp"], [root])
                results.append((compare(snap, snap), snap))
    reuse_result, reuse_snap = results[0]
    plain_result, plain_snap = results[1]
    assert reuse_result.verdict == plain_result.verdict
    assert [str(c.kind) for c in reuse_result.changes] == [
        str(c.kind) for c in plain_result.changes
    ]
    # A self-comparison of an unchanged library must not be judged a break --
    # stated outright rather than inherited from the two arms agreeing, which
    # would also hold if both had started reporting the same spurious break.
    # Not "no findings at all": a self-compare with a header context legitimately
    # reports `header_binary_context_mismatch`, which is pre-existing and
    # nothing to do with this change, so the claim is about the verdict.
    assert str(reuse_result.verdict) == str(plain_result.verdict)
    assert not [
        c.kind.value
        for c in reuse_result.changes
        if c.kind.value not in {"header_binary_context_mismatch"}
    ], [c.kind.value for c in reuse_result.changes]
    # And the control must actually have parsed a real surface, or "clean"
    # is vacuous.
    assert any("distance" in (f.name or "") for f in reuse_snap.functions), [
        f.name for f in reuse_snap.functions
    ]


_CASTXML_CONTROL = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or shutil.which("castxml") is None
    or shutil.which("g++") is None,
    reason="the castxml control needs castxml + g++ on Linux",
)


@_CASTXML_CONTROL
@pytest.mark.integration
def test_the_castxml_backend_is_an_unchanged_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other L2 header backend must be untouched by this change.

    castxml has its own parser and never constructs a `_ClangAstParser`, so
    nothing here should reach it -- which is exactly why it is worth asserting
    rather than assuming. A shared-state change that accidentally leaked
    across backends (a key namespace collision in the one acquisition table,
    say) would show up here and nowhere else in this file.

    Two claims, because the weaker one alone would not catch a collision:
    the castxml comparison is identical with reuse on and off, **and** the
    clang-side builder never ran during it.
    """
    from abicheck import dumper_clang
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxml")
    builds: list[int] = []
    results = []
    for reuse in (True, False):
        count = 0
        real = dumper_clang.build_template_param_indexes

        def counted(root: dict[str, Any], _real: Any = real) -> Any:
            nonlocal count
            count += 1
            return _real(root)

        root = tmp_path / ("arm_one" if reuse else "arm_two")
        old_so, old_h = _build_side(root / "old", broken=False)
        new_so, new_h = _build_side(root / "new", broken=True)
        with monkeypatch.context() as patch:
            patch.setattr(dumper_clang, "build_template_param_indexes", counted)
            if not reuse:
                patch.setattr(dumper_clang, "_template_param_indexes_for", counted)
            with ast_acquisition_scope():
                old_snap = dump(old_so, [old_h], [old_h.parent])
                new_snap = dump(new_so, [new_h], [new_h.parent])
                results.append(compare(old_snap, new_snap))
        builds.append(count)
        # The control is only a control if castxml really parsed the headers.
        # Without this it would still "pass" after silently degrading to a
        # symbols-only or DWARF-only dump, which is the failure mode a
        # backend-scoped assertion exists to catch.
        assert getattr(old_snap, "ast_producer", None) == "castxml", getattr(
            old_snap, "ast_producer", None
        )
        spellings = {t.name for t in old_snap.types if "<" in (t.name or "")}
        assert spellings, sorted(t.name for t in old_snap.types)

    # Nothing in this change is reachable from the castxml path at all.
    assert builds == [0, 0], builds

    reuse_result, plain_result = results

    def _findings(result: Any) -> list[tuple[Any, ...]]:
        return sorted(
            (c.kind.value, c.symbol or "", str(c.old_value), str(c.new_value))
            for c in result.changes
        )

    assert _findings(reuse_result), "the castxml control must produce findings"
    assert _findings(reuse_result) == _findings(plain_result)
    assert str(reuse_result.verdict) == str(plain_result.verdict)
