"""A castxml constructor/destructor placeholder resolves to its linker
identity through the export table (evidence-entity-model plan, Phase 1 gap:
castxml ctor/dtor placeholders are ``unresolved``).

castxml 0.7.0 records no ``mangled`` attribute on a ``Constructor``/
``Destructor`` element, so the castxml parser keys one on a placeholder
(``__abicheck_ctor__ns::W(int)``, ``~ns::W``). The identity table pairs such a
placeholder with an exported Itanium variant family (``C1``/``C2``/``C3``,
``D0``/``D1``/``D2``) one-to-one, and the node id becomes the complete-object
spelling clang itself reports.

The oracle is independent of the implementation: the expected symbols are
built here by a small Itanium mangler for the test's own type domain
(builtins and namespace-scoped class names, no substitutions needed), and the
expected resolution follows the rule stated in prose -- "resolved iff the
owner exports at least one variant of exactly this overload and no other
declared overload of that owner has the same unqualified signature" -- never
by calling the module's own helpers.
"""

from __future__ import annotations

import random
import shutil

import pytest

from abicheck.demangle import demangle
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.graph_join import JoinState

_HAS_DEMANGLER = demangle("_ZN2ns1WC1Ei") == "ns::W::W(int)"
needs_demangler = pytest.mark.skipif(
    not _HAS_DEMANGLER and shutil.which("c++filt") is None,
    reason="constructor parameter matching needs cxxfilt or c++filt",
)

# (castxml spellings, Itanium code). Several castxml spellings per builtin:
# castxml/clang print `unsigned long`, a typedef target may read
# `long unsigned int` -- both name one type.
_BUILTINS = {
    "int": (("int",), "i"),
    "long": (("long", "long int"), "l"),
    "ulong": (("unsigned long", "long unsigned int"), "m"),
    "double": (("double",), "d"),
    "char": (("char",), "c"),
}


def _source_name(parts):
    return "".join(f"{len(p)}{p}" for p in parts)


def _ctor(scope, codes, variant="C1"):
    return f"_ZN{_source_name(scope)}{variant}E{''.join(codes) or 'v'}"


def _dtor(scope, variant="D1"):
    return f"_ZN{_source_name(scope)}{variant}Ev"


def _ctor_placeholder(scope, spellings):
    return f"__abicheck_ctor__{'::'.join(scope)}({','.join(spellings)})"


def _fn(key, name="W", *params):
    return Function(
        name=name,
        mangled=key,
        return_type="void",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions, exports, typedefs_qualified=None):
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=list(functions),
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exports]),
        typedefs_qualified=dict(typedefs_qualified or {}),
    )


def _node(ids, i):
    return ids.functions[i]


def _identities(snap):
    from abicheck.model.snapshot_identity_table import identities_for_snapshot

    return identities_for_snapshot(snap)


# ---------------------------------------------------------------------------
# Generated domain: the resolution rule as an invariant.
# ---------------------------------------------------------------------------


def _scenario(rng):
    scopes = [("ns", "W"), ("ns", "V"), ("other", "W"), ("W",), ("a", "b", "W")]
    functions, exports, expected = [], set(), {}
    for scope in rng.sample(scopes, rng.randint(1, 3)):
        overloads = set()
        for _ in range(rng.randint(1, 3)):
            overloads.add(
                tuple(rng.choice(sorted(_BUILTINS)) for _ in range(rng.randint(0, 2)))
            )
        for params in sorted(overloads):
            spellings = [rng.choice(_BUILTINS[p][0]) for p in params]
            codes = [_BUILTINS[p][1] for p in params]
            idx = len(functions)
            functions.append(_fn(_ctor_placeholder(scope, spellings), scope[-1]))
            variants = rng.sample(["C1", "C2", "C3"], rng.randint(0, 2))
            names = {_ctor(scope, codes, v) for v in variants}
            exports |= names
            if names:
                expected[idx] = (
                    f"decl://{_ctor(scope, codes)}",
                    {f"decl://{n}" for n in names} - {f"decl://{_ctor(scope, codes)}"},
                )
        idx = len(functions)
        functions.append(_fn(f"~{'::'.join(scope)}", f"~{scope[-1]}"))
        variants = rng.sample(["D0", "D1", "D2"], rng.randint(0, 3))
        names = {_dtor(scope, v) for v in variants}
        exports |= names
        if names:
            expected[idx] = (
                f"decl://{_dtor(scope)}",
                {f"decl://{n}" for n in names} - {f"decl://{_dtor(scope)}"},
            )
    return functions, exports, expected


@needs_demangler
def test_placeholder_resolves_to_its_exported_variant_family():
    """60 generated scenarios in one test, so a failure names every
    disagreeing seed at once and the oracle cannot be vacuous."""
    failures = {}
    resolved_total = 0
    for seed in range(60):
        rng = random.Random(seed)
        functions, exports, expected = _scenario(rng)
        resolved_total += len(expected)
        ids = _identities(_snap(functions, exports))
        got = {
            i: (ident.node_id, set(ident.aliases))
            for i, ident in enumerate(ids.functions)
            if ident.resolved
        }
        if got != expected:
            failures[seed] = (got, expected)
        for i, ident in enumerate(ids.functions):
            if i not in expected and not ident.node_id.startswith("unresolved://"):
                failures.setdefault(seed, ("resolved without evidence", i))
    assert resolved_total > 60  # vacuity guard on the generator itself
    assert failures == {}


@needs_demangler
@pytest.mark.parametrize("seed", range(10))
def test_resolution_does_not_depend_on_input_order(seed):
    rng = random.Random(seed)
    functions, exports, _ = _scenario(rng)
    base = _identities(_snap(functions, exports))
    by_key = {f.mangled: i for i, f in zip(base.functions, functions)}
    shuffled = functions[:]
    rng.shuffle(shuffled)
    again = _identities(_snap(shuffled, sorted(exports, reverse=True)))
    assert {f.mangled: i for i, f in zip(again.functions, shuffled)} == by_key


# ---------------------------------------------------------------------------
# Named sibling cases.
# ---------------------------------------------------------------------------


class TestSpecialMemberCases:
    @needs_demangler
    def test_complete_and_base_object_ctor_variants(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "W"), ["int"]))],
                ["_ZN2ns1WC1Ei", "_ZN2ns1WC2Ei"],
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ei"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ei",)

    @needs_demangler
    def test_only_base_object_variant_exported_still_keys_on_complete(self):
        ids = _identities(
            _snap([_fn(_ctor_placeholder(("ns", "W"), []))], ["_ZN2ns1WC2Ev"])
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ev"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ev",)

    def test_deleting_complete_and_base_dtor_variants(self):
        ids = _identities(
            _snap(
                [_fn("~ns::W", "~W")], ["_ZN2ns1WD0Ev", "_ZN2ns1WD1Ev", "_ZN2ns1WD2Ev"]
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WD1Ev"
        assert set(ids.functions[0].aliases) == {
            "decl://_ZN2ns1WD0Ev",
            "decl://_ZN2ns1WD2Ev",
        }

    def test_inline_ctor_with_no_export_stays_unresolved(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "I"), [])), _fn("~ns::I", "~I")],
                ["_Z3foov"],
            )
        )
        assert all(i.node_id.startswith("unresolved://") for i in ids.functions)

    @needs_demangler
    def test_implicit_copy_ctor_matches_its_substituted_mangling(self):
        # castxml spells the implicit copy ctor's parameter relative to its
        # scope; the mangling uses a substitution (S0_) the demangler expands.
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "Imp"), ["const Imp&"]))],
                ["_ZN2ns3ImpC1ERKS0_", "_ZN2ns3ImpC2ERKS0_"],
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns3ImpC1ERKS0_"

    def test_template_owner_stays_unresolved(self):
        ids = _identities(_snap([_fn("~ns::B<int>", "~B")], ["_ZN2ns1BIiED1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")

    def test_same_leaf_class_in_another_namespace_does_not_join(self):
        ids = _identities(_snap([_fn("~other::W", "~W")], ["_ZN2ns1WD1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")

    @needs_demangler
    def test_overloads_indistinguishable_after_leaf_reduction_resolve_neither(self):
        fns = [
            _fn(_ctor_placeholder(("A", "W"), ["p::X"])),
            _fn(_ctor_placeholder(("A", "W"), ["q::X"])),
        ]
        ids = _identities(_snap(fns, ["_ZN1A1WC1EN1p1XE", "_ZN1A1WC1EN1q1XE"]))
        assert all(i.node_id.startswith("unresolved://") for i in ids.functions)

    @needs_demangler
    def test_typedef_parameter_found_by_scope_lookup(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "W"), ["size_type"]))],
                ["_ZN2ns1WC1Em"],
                {"ns::W::size_type": "long unsigned int"},
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Em"

    def test_spelling_owned_by_another_declaration_is_refused(self):
        fns = [_fn("~ns::W", "~W"), _fn("_ZN2ns1WD1Ev", "~W")]
        ids = _identities(_snap(fns, ["_ZN2ns1WD1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")
        assert ids.functions[1].node_id == "decl://_ZN2ns1WD1Ev"

    def test_real_ctor_linker_name_gains_observed_variant_aliases(self):
        ids = _identities(
            _snap([_fn("_ZN2ns1WC1Ei")], ["_ZN2ns1WC1Ei", "_ZN2ns1WC2Ei"])
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ei"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ei",)

    def test_class_named_like_a_variant_code_is_not_rewritten(self):
        # `C1Evil` embeds "C1E"; only the structural marker is a variant code.
        ids = _identities(
            _snap([_fn("~C1Evil", "~C1Evil")], ["_ZN6C1EvilD1Ev", "_ZN6C2EvilD2Ev"])
        )
        assert ids.functions[0].node_id == "decl://_ZN6C1EvilD1Ev"
        assert ids.functions[0].aliases == ()


class TestExportJoinOfSpecialMembers:
    @needs_demangler
    def test_placeholder_joins_every_variant_export(self):
        from abicheck.compare.export_join import join_exports

        snap = _snap(
            [_fn(_ctor_placeholder(("ns", "W"), ["int"])), _fn("~ns::W", "~W")],
            [
                "_ZN2ns1WC1Ei",
                "_ZN2ns1WC2Ei",
                "_ZN2ns1WD0Ev",
                "_ZN2ns1WD1Ev",
                "_ZN2ns1WD2Ev",
            ],
        )
        j = join_exports(snap)
        ctor = j.declaration("decl://_ZN2ns1WC1Ei")
        assert ctor.state is JoinState.MATCHED
        assert ctor.candidates == (
            "binary_symbol://elf/_ZN2ns1WC1Ei",
            "binary_symbol://elf/_ZN2ns1WC2Ei",
        )
        assert j.declaration("decl://_ZN2ns1WD1Ev").state is JoinState.MATCHED
        assert {r.state for r in j.join.right.values()} == {JoinState.MATCHED}
