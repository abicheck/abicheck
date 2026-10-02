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

"""Property tests for ``abicheck.model.name_decoration`` and
``abicheck.model.root_relative_path`` (design-hardening plan Phase 3, F3).

Every codec is held to the same three laws, with generated inputs and an
oracle that does not call the implementation's own ``encode``:

* **decode-of-oracle** -- a spelling built from its parts by this module's
  own f-string grammar decodes back to exactly those parts;
* **round trip** -- ``encode(decode(s)) == s`` for every decodable ``s``;
* **injectivity** -- two different parts never decode from one spelling,
  and the identity a join keys on (the decoded *name*) is equal exactly when
  the source names are equal. H3's PE ``__vectorcall`` bug class -- two
  distinct names (``_f@@8``, ``f@@8``) decoding to one identity -- is the
  named instance.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import assume, given, settings, strategies as st

from abicheck.model.name_decoration import elf_version, itanium_structors, macho, pe_x86
from abicheck.model.root_relative_path import (
    PROJECT_LAYOUT_MARKERS,
    RootRelativePath,
    is_absolute_spelling,
)

_IDENT_START = string.ascii_letters + "_"
_IDENT_REST = string.ascii_letters + string.digits + "_"
c_identifiers = st.builds(
    lambda h, t: h + t,
    st.sampled_from(_IDENT_START),
    st.text(alphabet=_IDENT_REST, max_size=12),
)
arg_bytes = st.integers(min_value=0, max_value=4096).map(lambda n: n * 4)

# -- PE ----------------------------------------------------------------------

_ORACLE = {
    pe_x86.CallingConvention.CDECL: lambda n, _b: f"_{n}",
    pe_x86.CallingConvention.STDCALL: lambda n, b: f"_{n}@{b}",
    pe_x86.CallingConvention.FASTCALL: lambda n, b: f"@{n}@{b}",
    pe_x86.CallingConvention.VECTORCALL: lambda n, b: f"{n}@@{b}",
}
conventions = st.sampled_from(list(pe_x86.CallingConvention))


@given(name=c_identifiers, conv=conventions, b=arg_bytes)
def test_pe_decodes_the_oracle_spelling(name, conv, b) -> None:
    spelled = _ORACLE[conv](name, b)
    decoded = pe_x86.decode(spelled, x86_32=True)
    want_bytes = None if conv is pe_x86.CallingConvention.CDECL else b
    assert decoded == pe_x86.PeDecoded(name, conv, want_bytes)
    assert pe_x86.encode(decoded) == spelled


@given(
    a=c_identifiers,
    b=c_identifiers,
    ca=conventions,
    cb=conventions,
    na=arg_bytes,
    nb=arg_bytes,
    x86=st.booleans(),
)
@settings(max_examples=300)
def test_pe_identity_is_injective(a, b, ca, cb, na, nb, x86) -> None:
    """Decoded names are equal exactly when the source names are."""
    da = pe_x86.decode(_ORACLE[ca](a, na), x86_32=x86)
    db = pe_x86.decode(_ORACLE[cb](b, nb), x86_32=x86)
    assume(da is not None and db is not None)
    assert (da.name == db.name) == (a == b)


@given(name=c_identifiers, b=arg_bytes, x86=st.booleans())
def test_pe_vectorcall_keeps_a_leading_underscore(name, b, x86) -> None:
    """H3: ``_f@@8`` is ``_f`` and ``f@@8`` is ``f`` on every machine."""
    plain = pe_x86.decode(f"{name}@@{b}", x86_32=x86)
    under = pe_x86.decode(f"_{name}@@{b}", x86_32=x86)
    assert plain is not None and under is not None
    assert plain.name == name
    assert under.name == "_" + name
    assert plain.name != under.name


@given(s=st.text(alphabet=_IDENT_REST + "@?$", max_size=20), x86=st.booleans())
def test_pe_round_trips_every_decodable_spelling(s, x86) -> None:
    decoded = pe_x86.decode(s, x86_32=x86)
    if decoded is not None:
        assert pe_x86.encode(decoded) == s


@given(name=c_identifiers, conv=conventions, b=arg_bytes)
def test_pe_off_x86_only_vectorcall_decodes(name, conv, b) -> None:
    decoded = pe_x86.decode(_ORACLE[conv](name, b), x86_32=False)
    if conv is pe_x86.CallingConvention.VECTORCALL:
        assert decoded is not None and decoded.name == name
    else:
        assert decoded is None


@given(name=c_identifiers, b=st.integers(min_value=0, max_value=4096))
def test_pe_rejects_an_argument_size_off_the_stack_slot(name, b) -> None:
    assume(b % 4)
    assert pe_x86.decode(f"_{name}@{b}", x86_32=True) is None


def test_pe_cxx_manglings_are_not_c_names() -> None:
    assert pe_x86.decode_c_name("__Z3fooi", x86_32=True) == ""
    assert pe_x86.decode_c_name("?f@@YAXXZ", x86_32=True) == ""
    assert pe_x86.decode_c_name("_f@8", x86_32=True) == "f"


# -- Mach-O ------------------------------------------------------------------


@given(body=st.text(alphabet=_IDENT_REST, max_size=20))
def test_macho_itanium_round_trip(body) -> None:
    pure = "_Z" + body
    assert macho.decode_itanium("_" + pure) == pure
    assert macho.encode(pure) == "_" + pure
    assert macho.decode_itanium(pure) == pure  # idempotent on decoded input


@given(a=c_identifiers, b=c_identifiers)
def test_macho_c_codec_is_injective(a, b) -> None:
    assert macho.decode_c("_" + a) == a
    assert (macho.decode_c(macho.encode(a)) == macho.decode_c(macho.encode(b))) == (
        a == b
    )


@given(s=c_identifiers)
def test_macho_shifted_spellings_are_one_step_away(s) -> None:
    for c in macho.shifted_spellings(s):
        assert c in (s[1:], "_" + s)
        assert c != s


# -- Itanium ctor/dtor variants ----------------------------------------------

# A nested name built by this module's own grammar: <len><id> components,
# optionally a template-arg block on the class, then the code, then 'E' and
# a parameter encoding.
_source_names = c_identifiers.map(lambda n: f"{len(n)}{n}")
_codes = st.sampled_from(
    itanium_structors.CTOR_VARIANTS + itanium_structors.DTOR_VARIANTS
)


@st.composite
def structors(draw):
    scopes = draw(st.lists(_source_names, min_size=1, max_size=3))
    template = draw(st.sampled_from(["", "IiE", "I3ErrE", "ILi5EE"]))
    code = draw(_codes)
    params = draw(st.sampled_from(["v", "i", "RKS_", "PKc"]))
    prefix = "_ZN" + "".join(scopes) + template
    return prefix, code, "E" + params


@given(parts=structors())
def test_structor_decodes_the_oracle_spelling(parts) -> None:
    prefix, code, suffix = parts
    decoded = itanium_structors.decode(prefix + code + suffix)
    assert decoded == itanium_structors.Structor(prefix, code, suffix)
    assert itanium_structors.encode(decoded) == prefix + code + suffix


@given(a=structors(), b=structors())
def test_structor_families_join_exactly_one_member(a, b) -> None:
    """Two variants share a family iff they differ only in the code, and
    the code kind (ctor vs dtor) is part of the family."""
    da = itanium_structors.decode("".join(a))
    db = itanium_structors.decode("".join(b))
    assert da is not None and db is not None
    same = a[0] == b[0] and a[2] == b[2] and a[1][0] == b[1][0]
    assert (da.family == db.family) == same


@given(parts=structors())
def test_structor_siblings_are_the_rest_of_the_family(parts) -> None:
    prefix, code, suffix = parts
    kind_codes = (
        itanium_structors.CTOR_VARIANTS
        if code[0] == "C"
        else itanium_structors.DTOR_VARIANTS
    )
    want = {prefix + c + suffix for c in kind_codes if c != code}
    assert set(itanium_structors.sibling_spellings(prefix + code + suffix)) == want


# Moved from tests/test_binary_fingerprint.py (rename detection's former
# private parser): fixed shapes the generated grammar above does not reach --
# substitutions, ABI tags, literals, malformed and adversarial input.
@pytest.mark.parametrize(
    ("symbol", "code"),
    [
        ("_Z6fooC1Ev", None),
        ("_Z6fooC2Ev", None),
        ("_ZN6WidgetC1Ev", "C1"),
        ("_ZN1A6fooC1EEv", None),
        ("_ZN1A6fooC2EEv", None),
        ("_ZN2ns6WidgetC1Ev", "C1"),
        ("_ZN3FooIiEC1Ev", "C1"),
        ("_ZN3FooIiEC2Ev", "C2"),
        ("_ZN3FooIiED1Ev", "D1"),
        ("_ZN3FooIN2ns1XEEC1Ev", "C1"),
        ("_ZN3FooILi5EEC1Ev", "C1"),
        ("_ZN3FooI3ErrEC1Ev", "C1"),
        ("_ZN3FooI3ErrEC2Ev", "C2"),
        ("_ZN3FooIS_EC1Ev", "C1"),
        ("_ZN3FooISsEC1Ev", "C1"),
        ("_ZN" + ("9" * 5000), None),
        ("_ZN9999999999FooC1Ev", None),
        ("_ZN99FooC1Ev", None),
        ("_ZN3FooIiC1Ev", None),
        ("_ZN3FooI", None),
        ("_ZN3FooILiC1Ev", None),
        ("_ZNK1A3fooEv", None),
        ("not_mangled", None),
        ("_ZNSt6vectorIiEC1Ev", "C1"),
        ("_ZNSt6vectorIiEC2Ev", "C2"),
        ("_ZNSsC1Ev", "C1"),
        ("_ZNSt6vectorIiE3fooEv", None),
        ("_ZN3FooB1xC1Ev", "C1"),
        ("_ZN3FooB1xC2Ev", "C2"),
    ],
)
def test_structor_code_table(symbol: str, code: str | None) -> None:
    decoded = itanium_structors.decode(symbol)
    assert (decoded.code if decoded is not None else None) == code


def test_structor_class_name_embedding_a_code_is_not_the_marker() -> None:
    decoded = itanium_structors.decode("_ZN6C1EvilIiEC1Ev")
    assert decoded is not None and decoded.prefix == "_ZN6C1EvilIiE"
    assert itanium_structors.decode("_ZN1A6fooC1EEv") is None
    assert itanium_structors.decode("_ZN3api7DerivedCI1NS_4BaseEEi") is None


# -- ELF versions --------------------------------------------------------------

versions = st.text(alphabet=_IDENT_REST + ".", min_size=1, max_size=10)


@given(name=c_identifiers, version=st.none() | versions, default=st.booleans())
def test_elf_decodes_the_oracle_spelling(name, version, default) -> None:
    if version is None:
        spelled, want = name, elf_version.ElfVersioned(name)
    else:
        spelled = f"{name}{'@@' if default else '@'}{version}"
        want = elf_version.ElfVersioned(name, version, default)
    assert elf_version.decode(spelled) == want
    assert elf_version.encode(want) == spelled
    assert elf_version.unversioned_name(spelled) == name


@given(s=st.text(alphabet=_IDENT_REST + "@.", max_size=16))
def test_elf_round_trips_every_decodable_spelling(s) -> None:
    decoded = elf_version.decode(s)
    if decoded is not None:
        assert elf_version.encode(decoded) == s


# -- RootRelativePath ----------------------------------------------------------

segments = st.text(alphabet=string.ascii_lowercase + " -_", min_size=1, max_size=6)
relative_paths = st.lists(
    segments.filter(lambda s: s.strip(". ") != ""), min_size=1, max_size=4
)
roots = st.lists(segments, min_size=0, max_size=4)


@given(root=roots, rel=relative_paths)
def test_relative_to_strips_any_root_spelling(root, rel) -> None:
    """Oracle: the segments themselves. Any spelling of the root (POSIX,
    redundant ``./``, a ``..`` detour, Windows separators and drive) yields
    the same root-relative path."""
    want = "/".join(rel)
    posix_root = "/" + "/".join(root)
    spellings = [
        (posix_root, posix_root + "/" + want),
        (posix_root, posix_root + "/./" + want),
        (posix_root, posix_root + "/x/../" + want),
        ("C:\\" + "\\".join(root), "C:\\" + "\\".join([*root, *rel])),
    ]
    for r, p in spellings:
        got = RootRelativePath.relative_to(p, r)
        assert got is not None and got.posix == want, (r, p)


@given(root=roots, rel=relative_paths, other=segments)
def test_relative_to_never_returns_an_absolute_or_foreign_path(
    root, rel, other
) -> None:
    assume(not root or other != root[0])
    posix_root = "/" + "/".join(root)
    got = RootRelativePath.relative_to("/" + other + "/" + "/".join(rel), posix_root)
    if root:
        assert got is None
    for path in ("/" + "/".join(rel), "C:\\x", "\\\\host\\share\\x"):
        assert is_absolute_spelling(path)
        assert RootRelativePath.from_recorded(path) is None


@given(rel=relative_paths)
def test_absolute_spelling_is_unconstructible(rel) -> None:
    for bad in ("/" + "/".join(rel), "C:/" + "/".join(rel), "../" + "/".join(rel)):
        with pytest.raises(ValueError):
            RootRelativePath.parse(bad)
        with pytest.raises(ValueError):
            RootRelativePath(bad)


@given(
    prefix=roots,
    marker=st.sampled_from(sorted(PROJECT_LAYOUT_MARKERS)),
    rel=relative_paths,
)
def test_project_layout_anchor_is_checkout_independent(prefix, marker, rel) -> None:
    assume(not any(s.lower() in PROJECT_LAYOUT_MARKERS for s in rel))
    want = "/".join([marker, *rel])
    for path in (
        "/" + "/".join([*prefix, marker, *rel]),
        "/elsewhere/deeper/" + "/".join([marker, *rel]),
        "D:\\" + "\\".join([*prefix, marker, *rel]),
        want,
    ):
        got = RootRelativePath.from_project_layout(path)
        assert got is not None and got.posix == want, path


@given(rel=relative_paths)
def test_project_layout_without_anchor_drops_an_absolute_path(rel) -> None:
    assume(not any(s.lower() in PROJECT_LAYOUT_MARKERS for s in rel))
    assert RootRelativePath.from_project_layout("/" + "/".join(rel)) is None
    kept = RootRelativePath.from_project_layout("/".join(rel))
    assert kept is not None and kept.posix == "/".join(rel)
