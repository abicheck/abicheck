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

"""ADR-049 ``contract=exports``: tests for the export-rooted evidence provider.

``export_surface.py`` answers a different question than ``surface.py``: roots
are the binary's *observed export table*, not header-derived
``Visibility.PUBLIC``, and no header origin ever demotes anything. These tests
pin exactly that difference (a private-header type reached from a real export
is inside this domain; an unexported public-header declaration is not), the
three platforms' export tables, and the deliberately conservative
"unresolvable rather than empty" rule for a snapshot with no export table at
all.
"""

from __future__ import annotations

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface, observed_export_names
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.pe_metadata import PeExport, PeMetadata


def _fn(name, mangled, ret="void", params=(), vis=Visibility.PUBLIC, origin=None):
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=ScopeOrigin.UNKNOWN if origin is None else origin,
    )


def _rec(name, fields=(), bases=(), origin=None):
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=f"f{i}", type=t) for i, t in enumerate(fields)],
        bases=list(bases),
        origin=ScopeOrigin.UNKNOWN if origin is None else origin,
    )


class TestObservedExportNames:
    def test_no_binary_metadata_is_none_not_empty(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("a", "_Z1av")])
        assert observed_export_names(snap) is None

    def test_empty_export_table_is_also_none(self) -> None:
        # An export-table-less parse and a genuinely empty export table are
        # indistinguishable from the recorded data; claiming "exports
        # nothing" would let a parse failure prove every entity out of
        # contract.
        snap = AbiSnapshot(library="l", version="1", elf=ElfMetadata(symbols=[]))
        assert observed_export_names(snap) is None

    def test_elf_pe_and_macho_tables_are_unioned(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            elf=ElfMetadata(symbols=[ElfSymbol(name="from_elf")]),
            pe=PeMetadata(exports=[PeExport(name="from_pe")]),
            macho=MachoMetadata(exports=[MachoExport(name="from_macho")]),
        )
        assert observed_export_names(snap) == {"from_elf", "from_pe", "from_macho"}


class TestRoots:
    def test_only_export_table_members_are_roots(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("kept", "_Z4keptv"), _fn("gone", "_Z4gonev")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z4keptv")]),
        )
        surf = compute_export_surface(snap)
        assert surf.resolvable
        # Every symbol key of the root, so a finding naming either encoding
        # resolves (mirrors surface._symbol_keys).
        assert {"kept", "_Z4keptv"} <= surf.export_symbols
        assert "_Z4gonev" not in surf.export_symbols
        # ... but the non-root is still *known*, which is what lets a caller
        # tell "proven not exported" from "never heard of it".
        assert {"gone", "_Z4gonev"} <= surf.all_symbols

    def test_declaration_visibility_does_not_make_a_root(self) -> None:
        # The header-derived domain's own root rule (Visibility.PUBLIC) has no
        # authority here: an unexported public declaration is not an export.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("inline_only", "_Z11inline_onlyv", vis=Visibility.PUBLIC)],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z5otherv")]),
        )
        surf = compute_export_surface(snap)
        assert surf.resolvable
        assert not surf.export_symbols

    def test_hidden_visibility_declaration_that_is_exported_is_a_root(self) -> None:
        # The mirror image of the test above: this domain believes the export
        # table, not the declaration's recorded visibility.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("h", "_Z1hv", vis=Visibility.HIDDEN)],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z1hv")]),
        )
        assert "_Z1hv" in compute_export_surface(snap).export_symbols

    def test_exported_variable_is_a_root(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            variables=[Variable(name="g_cfg", mangled="g_cfg", type="Cfg")],
            types=[_rec("Cfg")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="g_cfg")]),
        )
        surf = compute_export_surface(snap)
        assert "g_cfg" in surf.export_symbols
        assert "Cfg" in surf.export_types

    def test_untyped_roots_are_flagged(self) -> None:
        # An export-table-only dump records return_type "?" and no params, so
        # the closure has no usable seeds (ADR-024 D5.2's rule for this
        # domain) -- the caller must not read an empty closure as proof.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("opaque", "opaque", ret="?")],
            types=[_rec("Whatever")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="opaque")]),
        )
        surf = compute_export_surface(snap)
        assert surf.resolvable
        assert surf.export_symbols
        assert not surf.has_typed_roots

    def test_method_root_seeds_its_owner_class(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("Widget::draw", "_ZN6Widget4drawEv")],
            types=[_rec("Widget")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN6Widget4drawEv")]),
        )
        assert "Widget" in compute_export_surface(snap).export_types


class TestClosure:
    def test_closure_follows_fields_bases_and_typedefs(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "_Z3api5Alias", params=("Alias",))],
            types=[
                _rec("Root", fields=("Field",), bases=("Base",)),
                _rec("Field"),
                _rec("Base"),
                _rec("Unreached"),
            ],
            typedefs={"Alias": "Root"},
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3api5Alias")]),
        )
        surf = compute_export_surface(snap)
        assert {"Root", "Field", "Base"} <= surf.export_types
        assert "Unreached" not in surf.export_types
        assert "Unreached" in surf.all_types

    def test_header_origin_never_demotes_a_reached_type(self) -> None:
        # The core difference from the `public` domain: a private-header type
        # a real export takes by value is genuinely part of the export
        # contract, so nothing about its origin removes it here.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "_Z3api7Private", params=("Private",))],
            types=[_rec("Private", origin=ScopeOrigin.PRIVATE_HEADER)],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3api7Private")]),
        )
        assert "Private" in compute_export_surface(snap).export_types

    def test_ambiguous_type_names_are_recorded(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "_Z3apiv")],
            types=[_rec("one::Point"), _rec("two::Point")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        assert "Point" in compute_export_surface(snap).ambiguous_type_names


class TestUnresolvable:
    def test_no_export_table_leaves_the_surface_unresolvable(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "_Z3api3Foo", params=("Foo",))],
            types=[_rec("Foo")],
        )
        surf = compute_export_surface(snap)
        assert not surf.resolvable
        assert not surf.export_symbols
        assert not surf.export_types
        # The universes are still populated, so a caller can still tell a
        # known entity from an unknown one while refusing to decide
        # membership.
        assert {"api", "_Z3api3Foo"} <= surf.all_symbols
        assert "Foo" in surf.all_types


class TestMachoUnderscoreQuirk:
    def test_double_underscore_mangled_name_matches_a_stripped_trie_export(
        self,
    ) -> None:
        # clang records `__ZN3lib3addEii` on macOS while the Mach-O export
        # trie's own names have the platform underscore stripped. A missed
        # root here would make every C++ declaration in a Mach-O snapshot
        # falsely PROVEN_OUT_OF_CONTRACT.
        snap = AbiSnapshot(
            library="libfoo.dylib",
            version="1",
            functions=[_fn("lib::add", "__ZN3lib3addEii")],
            macho=MachoMetadata(exports=[MachoExport(name="_ZN3lib3addEii")]),
        )
        surf = compute_export_surface(snap)
        assert "__ZN3lib3addEii" in surf.export_symbols

    def test_de_prefixing_does_not_invent_a_root(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.dylib",
            version="1",
            functions=[_fn("lib::other", "__ZN3lib5otherEv")],
            macho=MachoMetadata(exports=[MachoExport(name="_ZN3lib3addEii")]),
        )
        assert not compute_export_surface(snap).export_symbols


class TestRootMatchingIsLinkerIdentityOnly:
    """Regressions for the review findings on how rootness is decided."""

    def test_bare_tail_alias_does_not_make_an_unrelated_decl_a_root(self) -> None:
        # A binary exporting the C symbol `foo` while the headers also declare
        # an unexported `ns::foo`: `_symbol_keys` gives the latter the trailing
        # alias "foo", which is a *lookup* alias for findings, never a linker
        # identity. Matching on it pulled the unrelated C++ declaration -- and
        # its whole type closure -- into the export contract.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns::foo", "_ZN2ns3fooEv", ret="Secret *")],
            types=[_rec("Secret")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo")]),
        )
        surf = compute_export_surface(snap)
        assert not surf.export_symbols
        assert not surf.export_types
        assert not surf.has_roots

    def test_a_genuine_root_still_resolves_under_every_lookup_alias(self) -> None:
        # Narrowing the rootness *decision* must not narrow the lookup keys:
        # a finding naming any encoding of a real root still resolves.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns::foo", "_ZN2ns3fooEv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns3fooEv")]),
        )
        assert {"ns::foo", "_ZN2ns3fooEv", "foo"} <= (
            compute_export_surface(snap).export_symbols
        )

    def test_plain_name_is_the_identity_only_when_no_mangled_name_exists(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("c_api", "")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="c_api")]),
        )
        assert "c_api" in compute_export_surface(snap).export_symbols

    def test_underscore_alias_does_not_apply_to_elf(self) -> None:
        # On ELF/PE the underscore is meaningful: `foo` and `_foo` can be
        # distinct declarations, so an export table listing only `foo` must
        # not invent a root for `_foo`.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("_foo", "_foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo")]),
        )
        surf = compute_export_surface(snap)
        assert not surf.export_symbols
        assert not surf.has_roots


class TestRootCompleteness:
    def test_all_roots_typed_is_false_when_any_root_is_untyped(self) -> None:
        # `has_typed_roots` (some root is typed) is not enough to prove a type
        # unreachable: the untyped root's own closure is unknown and could
        # contain the very type being judged.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("typed", "typed", ret="Known *"),
                _fn("opaque", "opaque", ret="?"),
            ],
            types=[_rec("Known"), _rec("Maybe")],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="typed"), ElfSymbol(name="opaque")]
            ),
        )
        surf = compute_export_surface(snap)
        assert surf.has_roots
        assert surf.has_typed_roots
        assert not surf.all_roots_typed

    def test_all_roots_typed_when_every_root_carries_signature_types(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("a", "a", ret="Known *"), _fn("b", "b", params=("Known",))],
            types=[_rec("Known")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="a"), ElfSymbol(name="b")]),
        )
        surf = compute_export_surface(snap)
        assert surf.has_roots
        assert surf.all_roots_typed

    def test_observed_table_with_no_matching_declaration_has_no_roots(self) -> None:
        # A real export table whose names none of the declarations match (a
        # mangling-scheme gap) is resolvable but rootless -- the exports are
        # real, so nothing may be proven out of a contract whose roots were
        # never resolved.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("local", "_Z5localv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="?unmatched@@YAXXZ")]),
        )
        surf = compute_export_surface(snap)
        assert surf.resolvable
        assert not surf.has_roots


class TestUnresolvableUniverseParity:
    def test_all_symbols_matches_the_resolvable_path(self) -> None:
        # The no-export-table path routes through the same seeding helper, so
        # the `all_*` universe cannot be derived differently between the two.
        kw = {
            "functions": [_fn("api", "_Z3apiv")],
            "variables": [Variable(name="g", mangled="g", type="int")],
        }
        without_table = compute_export_surface(
            AbiSnapshot(library="l", version="1", **kw)
        )
        with_table = compute_export_surface(
            AbiSnapshot(
                library="l",
                version="1",
                elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
                **kw,
            )
        )
        assert without_table.all_symbols == with_table.all_symbols


class TestUnmatchedExports:
    def test_an_export_no_declaration_matched_is_recorded(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="known"), ElfSymbol(name="mystery")]
            ),
        )
        surf = compute_export_surface(snap)
        assert surf.has_roots
        assert surf.unmatched_exports == frozenset({"mystery"})
        assert not surf.exclusion_is_provable

    def test_linker_artifacts_are_not_unexplained(self) -> None:
        # Every real ELF binary exports these; counting them would make
        # exclusion permanently unprovable. The judgment is delegated to
        # `elf_symbol_filter.is_abi_relevant_elf_symbol`, the repo's existing
        # owner of it.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(
                symbols=[
                    ElfSymbol(name="known"),
                    ElfSymbol(name="_init"),
                    ElfSymbol(name="_fini"),
                    ElfSymbol(name="_ZThn8_N3Foo3barEv"),
                ]
            ),
        )
        surf = compute_export_surface(snap)
        assert not surf.unmatched_exports
        assert surf.exclusion_is_provable

    def test_exclusion_needs_a_resolvable_surface(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api", "_Z3apiv")])
        assert not compute_export_surface(snap).exclusion_is_provable


class TestMachoUnderscoreDirections:
    def test_headerless_dump_strips_a_second_underscore(self) -> None:
        # `dumper._dump_macho`'s `_normalize_macho_sym` strips one underscore
        # from an export name `macho_metadata` already stripped once, so the
        # declaration ends up one underscore *shorter* than the table entry --
        # the opposite direction from clang's `mangledName` (Codex review).
        snap = AbiSnapshot(
            library="l.dylib",
            version="1",
            functions=[_fn("ZN3lib3addEii", "ZN3lib3addEii", ret="?")],
            macho=MachoMetadata(exports=[MachoExport(name="_ZN3lib3addEii")]),
        )
        assert "ZN3lib3addEii" in compute_export_surface(snap).export_symbols

    def test_clang_direction_still_matches(self) -> None:
        snap = AbiSnapshot(
            library="l.dylib",
            version="1",
            functions=[_fn("lib::add", "__ZN3lib3addEii")],
            macho=MachoMetadata(exports=[MachoExport(name="_ZN3lib3addEii")]),
        )
        assert "__ZN3lib3addEii" in compute_export_surface(snap).export_symbols

    def test_neither_direction_applies_on_elf(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("_foo", "_foo"), _fn("bar", "bar")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo"), ElfSymbol(name="_bar")]),
        )
        assert not compute_export_surface(snap).export_symbols


class TestPlatformScopedRelevanceFilter:
    def test_pe_export_is_not_filtered_by_elf_conventions(self) -> None:
        # `is_abi_relevant_elf_symbol` encodes ELF/Itanium rules -- among them
        # "a `__` infix means a private C symbol" -- which `dumper.py` applies
        # to ELF and Mach-O exports but never to PE. A legitimate PE export
        # like `api__v2` must stay counted as unexplained, or an unmatched
        # export silently stops blocking exclusion (Codex review).
        snap = AbiSnapshot(
            library="l.dll",
            version="1",
            functions=[_fn("ok", "ok")],
            pe=PeMetadata(exports=[PeExport(name="ok"), PeExport(name="api__v2")]),
        )
        surf = compute_export_surface(snap)
        assert surf.unmatched_exports == frozenset({"api__v2"})
        assert not surf.exclusion_is_provable

    def test_the_same_spelling_is_still_filtered_on_elf(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ok", "ok")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="ok"), ElfSymbol(name="api__v2")]),
        )
        surf = compute_export_surface(snap)
        assert not surf.unmatched_exports
        assert surf.exclusion_is_provable


class TestRootTypeCompleteness:
    def test_one_unknown_parameter_type_makes_the_root_incomplete(self) -> None:
        # `dwarf_snapshot._process_param` writes the "?" sentinel for a
        # missing DW_AT_type. Such a root's parameter closure is unknown even
        # though the root as a whole looks typed (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Known *", "?"))],
            types=[_rec("Known")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        surf = compute_export_surface(snap)
        assert surf.has_typed_roots
        assert not surf.all_roots_typed

    def test_all_real_parameter_types_keep_the_root_complete(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Known *", "int"))],
            types=[_rec("Known")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        assert compute_export_surface(snap).all_roots_typed


class TestAmbiguousLookupAliases:
    def test_an_alias_shared_with_a_non_root_is_dropped(self) -> None:
        # The inverse of the linker-identity rootness fix: with `ns::foo`
        # exported and an unrelated unexported C `foo` also declared,
        # `_symbol_keys` puts the bare tail "foo" in `export_symbols`, so a
        # finding about the C `foo` matched the C++ root's alias (Codex
        # review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns::foo", "_ZN2ns3fooEv"), _fn("foo", "foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns3fooEv")]),
        )
        surf = compute_export_surface(snap)
        assert "foo" not in surf.export_symbols
        assert {"_ZN2ns3fooEv", "ns::foo"} <= surf.export_symbols

    def test_an_unshared_alias_survives(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns::foo", "_ZN2ns3fooEv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns3fooEv")]),
        )
        assert "foo" in compute_export_surface(snap).export_symbols

    def test_a_matched_export_name_is_never_dropped(self) -> None:
        # An export table name is unambiguous by construction, so it stays
        # even if some non-root declaration happens to share the spelling.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "shared"), _fn("other", "other")],
            variables=[Variable(name="shared", mangled="unexported_var", type="int")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="shared")]),
        )
        assert "shared" in compute_export_surface(snap).export_symbols
