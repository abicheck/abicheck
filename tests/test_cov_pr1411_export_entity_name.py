"""Behaviour of the Itanium entity-name walker (``model/export_entity_name``)."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from abicheck.model.export_entity_name import (
    PublicTemplateScopes,
    entity_name_components,
    public_template_for_symbol,
    public_template_scopes,
    split_qualified,
    strip_template_args,
)
from abicheck.model.vocabulary import ScopeOrigin


def _mangle_nested(parts: list[str], template_at: int | None = None) -> str:
    """Independent tiny encoder: ``_ZN<len><name>...E`` + a ``v`` signature."""
    out = []
    for idx, p in enumerate(parts):
        out.append(f"{len(p)}{p}")
        if idx == template_at:
            out.append("IiE")
    return "_ZN" + "".join(out) + "Ev"


def test_roundtrip_generated_nested_names() -> None:
    rng = random.Random(1411)
    alphabet = "abcdefghijklmnopqrstuvwxyz_"
    for _ in range(300):
        parts = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
            for _ in range(rng.randint(2, 5))
        ]
        tpl = rng.choice([None, *range(len(parts))])
        parsed = entity_name_components(_mangle_nested(parts, tpl))
        assert parsed is not None
        assert parsed.components == tuple(parts)
        assert parsed.nested is True
        assert parsed.template_positions == (frozenset() if tpl is None else {tpl})
        assert parsed.template_arg_spans == (() if tpl is None else ("IiE",))


@pytest.mark.parametrize(
    ("symbol", "components"),
    [
        ("_ZThn16_N3fmt6detail4impl3runEv", ("fmt", "detail", "impl", "run")),
        ("_ZTVN3fmt3BoxIiEE", ("fmt", "Box")),
        ("_ZN3fmt3fooB5cxx11Ev", ("fmt", "foo")),
        ("_ZNSt6vectorIiSaIiEE9push_backEOi", ("std", "vector", "push_back")),
        ("_ZN3fmt3BoxC2Ev", ("fmt", "Box", "{ctor}")),
        ("_ZN3fmt3BoxD1Ev", ("fmt", "Box", "{dtor}")),
        ("_ZN3fmt1aCI1BEv", ("fmt", "a", "{ctor}")),
        ("_ZN3fmt3BoxplERKS0_", ("fmt", "Box", "{op:pl}")),
        ("_ZN3fmt3BoxcviEv", ("fmt", "Box", "{op:cv}")),
        ("_ZNK3fmt3Box3getEv", ("fmt", "Box", "get")),
        ("_ZNS_3fooEv", ("{subst}", "foo")),
        ("_ZL3foov", ("foo",)),
    ],
)
def test_supported_forms(symbol: str, components: tuple[str, ...]) -> None:
    parsed = entity_name_components(symbol)
    assert parsed is not None
    assert parsed.components == components


@pytest.mark.parametrize(
    "symbol",
    [
        "foo",  # not mangled
        "_ZN99999999999999999999999abcE",  # absurd length, no int() blowup
        "_ZN3fooB99xEv",  # ABI tag longer than the name
        "_ZC2Ev",  # ctor without an owning class
        "_ZN3fmt0E",  # zero-length source name
        "_ZN3fmtXE",  # unmodelled component
        "_ZN3fmt3BoxIXE",  # unbalanced template args
        "_ZN",  # nothing after the intro
    ],
)
def test_unmodelled_forms_make_no_claim(symbol: str) -> None:
    assert entity_name_components(symbol) is None


def test_strip_and_split_are_bracket_aware() -> None:
    assert strip_template_args("ns::Box<int, ns::T<2>>::get") == "ns::Box::get"
    assert strip_template_args("a>b") == "ab"  # stray '>' never goes negative
    assert split_qualified("ns::Box<int>::get(int, char)") == ("ns", "Box", "get")
    assert split_qualified("") == ()


def _decl(name: str, mangled: str | None = None, *, public: bool = True, **kw):
    origin = ScopeOrigin.PUBLIC_HEADER if public else ScopeOrigin.SYSTEM_HEADER
    return SimpleNamespace(name=name, mangled=mangled, origin=origin, **kw)


def test_public_template_scopes_collects_types_templates_and_mangled() -> None:
    snap = SimpleNamespace(
        functions=[
            _decl("ns::plain", "_ZN2ns5plainEv"),  # not a template: skipped
            _decl("ns::Box<int>::get", "_ZN2ns3BoxIiE3getEv"),
            _decl("ns::pattern", None, is_template_pattern=True),
            _decl("sys::Hidden<int>", public=False),
        ],
        variables=[],
        types=[_decl("ccl::v1::Widget")],
    )
    snap.declarations = snap
    scopes = public_template_scopes(snap)
    assert ("ns", "Box") in scopes.scopes
    assert ("ns", "Box", "get") in scopes.scopes
    assert ("ns", "pattern") in scopes.scopes
    assert ("ccl", "v1", "Widget") in scopes.scopes
    assert ("ns", "plain") not in scopes.scopes
    assert not any(s[0] == "sys" for s in scopes.scopes)
    assert ("ccl", "Widget") in scopes.heads
    merged = scopes.union(PublicTemplateScopes(frozenset({("x",)}), frozenset()))
    assert ("x",) in merged.scopes and scopes.scopes <= merged.scopes


def test_public_template_for_symbol_matching() -> None:
    public = PublicTemplateScopes(
        frozenset({("ns", "Box")}), frozenset({("ccl", "Widget")})
    )
    # member of a public class-template specialization
    assert public_template_for_symbol("_ZN2ns3BoxIiE3getEv", public) == "ns::Box"
    # templated ctor names its class
    assert public_template_for_symbol("_ZN2ns3BoxIiEC2Ev", public) == "ns::Box"
    # versioned inline namespace met via heads
    assert (
        public_template_for_symbol("_ZN3ccl2v16WidgetIiE3getEv", public)
        == "ccl::v1::Widget"
    )
    # member template of a public class
    assert public_template_for_symbol("_ZN2ns3Box3mapIiEEvv", public) == "ns::Box"
    # no template args -> no claim; unknown template -> no claim
    assert public_template_for_symbol("_ZN2ns3Box3getEv", public) is None
    assert public_template_for_symbol("_ZN2zz3BoxIiE3getEv", public) is None
