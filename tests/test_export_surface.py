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


class TestOrdinalAndOwnerAndPlatformScoping:
    def test_ordinal_only_pe_export_is_not_dropped(self) -> None:
        # An unnamed ordinal export has an empty `PeExport.name`; dropping it
        # would hide a real entry point whose signature is unknown, letting a
        # named sibling make exclusion provable (Codex review). The
        # `ordinal:<n>` placeholder matches what `dumper._dump_pe` records.
        snap = AbiSnapshot(
            library="l.dll",
            version="1",
            functions=[_fn("named", "named")],
            pe=PeMetadata(
                exports=[PeExport(name="named"), PeExport(name="", ordinal=7)]
            ),
        )
        surf = compute_export_surface(snap)
        assert surf.unmatched_exports == frozenset({"ordinal:7"})
        assert not surf.exclusion_is_provable

    def test_a_headerless_ordinal_declaration_matches_the_placeholder(self) -> None:
        snap = AbiSnapshot(
            library="l.dll",
            version="1",
            functions=[_fn("ordinal:7", "ordinal:7", ret="?")],
            pe=PeMetadata(exports=[PeExport(name="", ordinal=7)]),
        )
        surf = compute_export_surface(snap)
        assert "ordinal:7" in surf.export_symbols
        assert not surf.unmatched_exports

    def test_a_namespace_is_not_seeded_as_a_method_owner(self) -> None:
        # `owner_class_of` cannot tell an enclosing class from an enclosing
        # namespace, so `api::run()` yields the bare fragment "api", which the
        # walk's alias-tolerant lookup would resolve to an unrelated
        # `other::api` and pull its field closure in (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api::run", "_ZN3api3runEv")],
            types=[_rec("other::api", fields=("Secret",)), _rec("Secret")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN3api3runEv")]),
        )
        assert not compute_export_surface(snap).export_types

    def test_a_real_method_owner_is_still_seeded(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("Widget::draw", "_ZN6Widget4drawEv")],
            types=[_rec("Widget", fields=("Pixel",)), _rec("Pixel")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN6Widget4drawEv")]),
        )
        assert {"Widget", "Pixel"} <= compute_export_surface(snap).export_types

    def test_a_qualified_owner_seeds_a_bare_recorded_record(self) -> None:
        # castxml/clang keep `name` bare and the qualified form in
        # `qualified_name`; the owner arrives qualified either way.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns::Widget::draw", "_ZN2ns6Widget4drawEv")],
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    size_bits=64,
                    qualified_name="ns::Widget",
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns6Widget4drawEv")]),
        )
        assert "Widget" in compute_export_surface(snap).export_types

    def test_macho_underscore_shift_does_not_reach_elf_names(self) -> None:
        # `export_names` used to be unioned before normalization, so a
        # snapshot carrying both tables let an ELF export `foo` make an
        # unrelated `_foo` a root (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("_foo", "_foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo")]),
            macho=MachoMetadata(exports=[MachoExport(name="unrelated")]),
        )
        assert not compute_export_surface(snap).export_symbols

    def test_a_declaration_with_no_identity_at_all_is_not_a_root(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("", "")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="real")]),
        )
        assert not compute_export_surface(snap).export_symbols

    def test_an_untyped_variable_root_marks_the_roots_incomplete(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            variables=[Variable(name="g", mangled="g", type="?")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="g")]),
        )
        surf = compute_export_surface(snap)
        assert surf.has_roots
        assert not surf.all_roots_typed


class TestMultiPlatformSpellings:
    def test_every_matched_platform_spelling_is_recorded(self) -> None:
        # One C declaration exported as `foo` in ELF and `_foo` in Mach-O:
        # recording only the first left the other in `unmatched_exports` and
        # wrongly blocked exclusion (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("foo", "foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo")]),
            macho=MachoMetadata(exports=[MachoExport(name="_foo")]),
        )
        surf = compute_export_surface(snap)
        assert surf.matched_exports == {"foo", "_foo"}
        assert not surf.unmatched_exports
        assert surf.exclusion_is_provable


class TestAmbiguityAndRuntimeOwnership:
    # The owner-seeding positive control lives in
    # `test_a_real_method_owner_is_still_seeded` above; the bare-name
    # collision cases at the tail of this class supersede an earlier
    # negative-only test of the same snapshot (CodeRabbit review).

    def test_macho_shift_does_not_steal_another_declarations_export(self) -> None:
        # A Mach-O library declaring both `foo` and `_foo` while exporting
        # only `_foo`: the shift must not root `foo` as well (Codex review).
        snap = AbiSnapshot(
            library="l.dylib",
            version="1",
            functions=[
                _fn("foo", "foo", ret="AOnly *"),
                _fn("_foo", "_foo", ret="BOnly *"),
            ],
            types=[_rec("AOnly"), _rec("BOnly")],
            macho=MachoMetadata(exports=[MachoExport(name="_foo")]),
        )
        surf = compute_export_surface(snap)
        assert surf.export_symbols == {"_foo"}
        assert "AOnly" not in surf.export_types

    def test_the_runtime_library_own_abi_is_not_filtered(self) -> None:
        # For libstdc++/libc++ itself, `_ZNSt...` exports are the inspected
        # ABI, not transitive runtime noise -- dropping them would let a
        # partial declaration set look fully accounted for (Codex review).
        snap = AbiSnapshot(
            library="libstdc++.so.6",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(
                soname="libstdc++.so.6",
                symbols=[
                    ElfSymbol(name="known"),
                    ElfSymbol(name="_ZNSt6vectorIiE4pushEi"),
                ],
            ),
        )
        surf = compute_export_surface(snap)
        assert surf.unmatched_exports == frozenset({"_ZNSt6vectorIiE4pushEi"})
        assert not surf.exclusion_is_provable

    def test_an_ordinary_library_still_filters_transitive_runtime(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(
                soname="libfoo.so",
                symbols=[
                    ElfSymbol(name="known"),
                    ElfSymbol(name="_ZNSt6vectorIiE4pushEi"),
                ],
            ),
        )
        surf = compute_export_surface(snap)
        assert not surf.unmatched_exports
        assert surf.exclusion_is_provable

    def test_exact_qualified_owner_survives_a_bare_name_collision(self) -> None:
        # castxml/clang record the bare leaf in `name` and the qualified form
        # in `qualified_name`, so `ns1::Foo` and `ns2::Foo` collapse onto one
        # ambiguous bare key. Seeding that key would walk both records; not
        # seeding at all leaves the exported method's own class outside the
        # closure and its layout findings provably-out (Codex review). The
        # qualified spelling is the exact handle that resolves neither
        # problem into the other.
        ns1 = RecordType(
            name="Foo",
            kind="struct",
            qualified_name="ns1::Foo",
            fields=[TypeField(name="a", type="OnlyNs1")],
        )
        ns2 = RecordType(
            name="Foo",
            kind="struct",
            qualified_name="ns2::Foo",
            fields=[TypeField(name="b", type="OnlyNs2")],
        )
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("ns1::Foo::bar", "_ZN3ns13Foo3barEv")],
            types=[ns1, ns2, _rec("OnlyNs1"), _rec("OnlyNs2")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN3ns13Foo3barEv")]),
        )
        surf = compute_export_surface(snap)
        # The owner is seeded (its canonical `name` is what the walk records)
        # and its own field follows...
        assert {"Foo", "OnlyNs1"} <= surf.export_types
        # ...while the unrelated same-named record's field stays out.
        assert "OnlyNs2" not in surf.export_types

    def test_an_ambiguous_bare_owner_alone_still_seeds_nothing(self) -> None:
        # Without a qualified spelling to disambiguate (a DWARF-style
        # producer would put the namespace in `name` itself), the bare owner
        # remains ambiguous and must not walk either record.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("Foo::bar", "_ZN3Foo3barEv")],
            types=[
                RecordType(
                    name="Foo",
                    kind="struct",
                    qualified_name="ns1::Foo",
                    fields=[TypeField(name="a", type="OnlyNs1")],
                ),
                RecordType(
                    name="Foo",
                    kind="struct",
                    qualified_name="ns2::Foo",
                    fields=[TypeField(name="b", type="OnlyNs2")],
                ),
                _rec("OnlyNs1"),
                _rec("OnlyNs2"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN3Foo3barEv")]),
        )
        surf = compute_export_surface(snap)
        assert "OnlyNs1" not in surf.export_types
        assert "OnlyNs2" not in surf.export_types

    def test_a_typedef_colliding_with_a_record_name_is_ambiguous(self) -> None:
        # `_index_surface_types` tallies collisions across the record and
        # enum indexes only, so a typedef alias sharing a name with a
        # record key went unflagged even though `_walk_type_closure`
        # resolves that one name through both (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Alias",))],
            types=[
                RecordType(
                    name="Alias",
                    kind="struct",
                    size_bits=64,
                    qualified_name="ns::Alias",
                ),
                _rec("Target"),
            ],
            typedefs={"Alias": "Target"},
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        assert "Alias" in compute_export_surface(snap).ambiguous_type_names

    def test_a_typedef_with_no_record_of_that_name_stays_unambiguous(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Alias",))],
            types=[_rec("Target")],
            typedefs={"Alias": "Target"},
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        surf = compute_export_surface(snap)
        assert "Alias" not in surf.ambiguous_type_names
        assert {"Alias", "Target"} <= surf.export_types

    def test_a_namespace_fragment_does_not_match_a_bare_recorded_record(self) -> None:
        # The exact-match owner rule closes the `api::run()` vs `other::api`
        # collision only if the record side is a full identity too. On the
        # castxml/clang path `name` is the bare leaf, so `other::api` is
        # stored as `name="api"` and the namespace fragment matched it
        # "exactly" (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api::run", "_ZN3api3runEv")],
            types=[
                RecordType(
                    name="api",
                    kind="struct",
                    size_bits=64,
                    qualified_name="other::api",
                    fields=[TypeField(name="s", type="Secret")],
                ),
                _rec("Secret"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN3api3runEv")]),
        )
        assert not compute_export_surface(snap).export_types

    def test_a_bare_recorded_record_with_no_namespace_still_seeds(self) -> None:
        # The guard keys on "the producer recorded a *differing* qualified
        # name", so a genuinely global class -- whose bare name is its whole
        # identity -- is unaffected.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("Widget::draw", "_ZN6Widget4drawEv")],
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    size_bits=64,
                    qualified_name="Widget",
                    fields=[TypeField(name="p", type="Pixel")],
                ),
                _rec("Pixel"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN6Widget4drawEv")]),
        )
        assert {"Widget", "Pixel"} <= compute_export_surface(snap).export_types


class TestUnresolvedTypeEdges:
    """A hole in the walked graph is not proof of unreachability.

    ``all_roots_typed`` only asks whether each root's signature *strings*
    are real; an edge whose spelling names no node this snapshot carries
    leaves the closure incomplete just the same (Codex review).
    """

    @staticmethod
    def _snap(param_type, types):
        return AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=(param_type,))],
            types=types,
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )

    def test_a_signature_naming_an_absent_type_blocks_exclusion(self) -> None:
        surf = compute_export_surface(self._snap("Alias *", [_rec("Internal")]))
        assert surf.unresolved_type_edges == frozenset({"Alias"})
        assert not surf.exclusion_is_provable

    def test_the_same_shape_resolves_once_the_type_is_declared(self) -> None:
        surf = compute_export_surface(
            self._snap("Alias *", [_rec("Internal"), _rec("Alias")])
        )
        assert not surf.unresolved_type_edges
        assert surf.exclusion_is_provable

    def test_a_reached_records_field_edge_is_scanned(self) -> None:
        surf = compute_export_surface(
            self._snap("Holder *", [_rec("Holder", fields=("Gone",))])
        )
        assert surf.unresolved_type_edges == frozenset({"Gone"})

    def test_an_unreached_records_field_edge_is_not(self) -> None:
        # A defect inside a type no export can reach says nothing about
        # what an export reaches.
        surf = compute_export_surface(
            self._snap("Holder *", [_rec("Holder"), _rec("Orphan", fields=("Gone",))])
        )
        assert not surf.unresolved_type_edges
        assert surf.exclusion_is_provable

    def test_a_partially_qualified_spelling_still_resolves(self) -> None:
        # direct-clang prints `api::Outer::Inner` as `"Outer::Inner"`, neither
        # the full identity nor the bare leaf.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Outer::Inner *",))],
            types=[
                RecordType(
                    name="Inner",
                    kind="struct",
                    size_bits=64,
                    qualified_name="api::Outer::Inner",
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        assert not compute_export_surface(snap).unresolved_type_edges

    def test_a_stdlib_typedef_key_resolves_its_bare_signature_spelling(self) -> None:
        # The DWARF backend spells a `std::string` parameter bare while the
        # typedef key is qualified -- without stripping, every C++ library
        # using a standard-library type could never prove an exclusion.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("string *",))],
            types=[],
            typedefs={"std::string": "basic_string<char>"},
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        surf = compute_export_surface(snap)
        assert not surf.unresolved_type_edges
        assert surf.exclusion_is_provable

    def test_toolchain_owned_internals_are_not_scanned(self) -> None:
        # Measured on a real g++ library: libstdc++'s own records spell
        # template *parameter* names (`_Tp`, `_Alloc`) in their field types.
        # Those are not declarations any snapshot carries, and treating them
        # as missing edges blocked every exclusion for every C++ library.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("std::vector<int> *",))],
            types=[
                RecordType(
                    name="vector",
                    kind="struct",
                    size_bits=64,
                    qualified_name="std::vector",
                    fields=[TypeField(name="_M_start", type="_Tp *")],
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        assert not compute_export_surface(snap).unresolved_type_edges

    def test_a_dependent_spelling_is_not_an_edge(self) -> None:
        # `typename`/`template` mark a spelling that names nothing until
        # instantiation, so no snapshot can carry a node for it.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", "api", params=("Holder *",))],
            types=[
                RecordType(
                    name="Holder",
                    kind="struct",
                    size_bits=64,
                    fields=[
                        TypeField(
                            name="f",
                            type="typename __alloc_traits<_Alloc>::template rebind<T>",
                        )
                    ],
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        assert not compute_export_surface(snap).unresolved_type_edges

    def test_an_export_matched_in_one_table_leaves_the_other_unexplained(self) -> None:
        # Only the Mach-O shift from `foo` matched; the ELF `_foo` is a real
        # entry point no declaration accounts for (Codex review).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("foo", "foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_foo")]),
            macho=MachoMetadata(exports=[MachoExport(name="_foo")]),
        )
        surf = compute_export_surface(snap)
        assert surf.unmatched_exports == frozenset({"_foo"})
        assert not surf.exclusion_is_provable
