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
from concurrent.futures import ThreadPoolExecutor
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
