# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for dump-time dependency scoping (``dump --include-dependencies``)."""

from __future__ import annotations

from abicheck.dumper_scoping import scope_snapshot_excluding_dependencies
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

_SYSTEM_HEADER = "/usr/include/c++/11/string"
_OWN_HEADER = "/src/myproject/include/api.h"
_OWN_PRIVATE_HEADER = "/src/myproject/src/internal.h"


def _fn(
    name: str,
    ret: str = "void",
    params: tuple[str, ...] = (),
    vis: Visibility = Visibility.PUBLIC,
    mangled: str | None = None,
    source_header: str | None = _OWN_HEADER,
) -> Function:
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        source_header=source_header,
    )


def _rec(
    name: str,
    fields: tuple[tuple[str, str], ...] = (),
    bases: tuple[str, ...] = (),
    source_header: str | None = _OWN_HEADER,
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
        source_header=source_header,
    )


class TestExcludesDependencies:
    def test_drops_function_from_system_header(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn(
                    "std_helper", mangled="_Z10std_helper", source_header=_SYSTEM_HEADER
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["run"]

    def test_drops_type_from_system_header(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[
                _rec("Own"),
                _rec("basic_string", source_header=_SYSTEM_HEADER),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]

    def test_keeps_private_declaration_from_own_header(self):
        """This is the point of the flag, distinct from the old
        public-surface-only design: a private (non-exported) declaration
        from the library's own headers must be kept, not just public ones."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn(
                    "internal_helper",
                    vis=Visibility.HIDDEN,
                    mangled="_Z9internal",
                    source_header=_OWN_PRIVATE_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["internal_helper"]

    def test_keeps_declaration_with_no_header_info(self):
        """A declaration with no source_header at all (e.g. export-only, no
        header matched it) is not confidently a dependency, so it is kept
        (conservative default: only drop what's confidently external)."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn(
                    "exported_only",
                    vis=Visibility.ELF_ONLY,
                    mangled="_Z13exported_only",
                    source_header=None,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["exported_only"]

    def test_keeps_variables_and_enums_from_own_headers_drops_system(self):
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            variables=[
                Variable(
                    name="own_var",
                    mangled="own_var",
                    type="int",
                    source_header=_OWN_HEADER,
                ),
                Variable(
                    name="sys_var",
                    mangled="sys_var",
                    type="int",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            enums=[
                EnumType(
                    name="OwnEnum",
                    members=[EnumMember(name="A", value=0)],
                    source_header=_OWN_HEADER,
                ),
                EnumType(
                    name="errc",
                    members=[EnumMember(name="B", value=0)],
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [v.name for v in scoped.variables] == ["own_var"]
        assert [e.name for e in scoped.enums] == ["OwnEnum"]

    def test_keeps_typedefs_unconditionally(self):
        """typedefs carry no per-entry header provenance, so they're kept
        wholesale rather than dropped for lack of evidence to classify them."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            typedefs={"size_type": "unsigned long", "Alias": "Own"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.typedefs == {"size_type": "unsigned long", "Alias": "Own"}

    def test_noop_without_header_derived_declarations(self):
        """A binary-only/DWARF-only dump (from_headers=False) has no header
        info to classify against -- this is default-on behavior, so it must
        no-op, not error, unlike the old opt-in flag's usage-error design."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            functions=[_fn("run", vis=Visibility.ELF_ONLY, source_header=None)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped is snap

    def test_does_not_mutate_input_snapshot(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("sys", mangled="_Z3sys", source_header=_SYSTEM_HEADER),
            ],
        )
        original_count = len(snap.functions)
        scope_snapshot_excluding_dependencies(snap)
        assert len(snap.functions) == original_count

    def test_lazy_lookup_indexes_rebuild_from_scoped_lists(self):
        sys_fn = _fn(
            "sys_helper", mangled="_Z10sys_helper", source_header=_SYSTEM_HEADER
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run"), sys_fn],
        )
        assert snap.func_by_mangled(sys_fn.mangled) is sys_fn
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.func_by_mangled(sys_fn.mangled) is None


class TestDwarfScoping:
    def test_dwarf_structs_filtered_to_kept_types(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            types=[_rec("Own")],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "Own": StructLayout(name="Own", byte_size=4),
                    "std::string": StructLayout(name="std::string", byte_size=32),
                },
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf is not None
        assert set(scoped.dwarf.structs) == {"Own"}


class TestDwarfAdvancedScoping:
    def test_type_and_symbol_keyed_collections_filtered(self):
        sys_fn = _fn("sys_fn", mangled="_Z6sys_fn", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run"), sys_fn],
            types=[_rec("Own")],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                packed_structs={"Own", "std::string"},
                all_struct_names={"Own", "std::string"},
                calling_conventions={"_Z3run": "normal", sys_fn.mangled: "ms_abi"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        adv = scoped.dwarf_advanced
        assert adv.packed_structs == {"Own"}
        assert adv.all_struct_names == {"Own"}
        assert set(adv.calling_conventions) == {"_Z3run"}


class TestCrossPlatformSystemHeaderPaths:
    """_SYSTEM_HEADER_DIRS covers more than /usr/include -- exercise each
    toolchain family so a Windows/macOS dump isn't silently unfiltered."""

    def test_windows_msvc_header_excluded(self):
        win_path = r"C:\Program Files\Microsoft Visual Studio\2022\VC\Tools\MSVC\14.38\include\string"
        snap = AbiSnapshot(
            library="libfoo.dll",
            version="1.0",
            from_headers=True,
            types=[_rec("Own"), _rec("basic_string", source_header=win_path)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]

    def test_windows_sdk_header_excluded(self):
        win_path = (
            r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22000.0\um\windows.h"
        )
        snap = AbiSnapshot(
            library="libfoo.dll",
            version="1.0",
            from_headers=True,
            types=[_rec("Own"), _rec("HWND__", source_header=win_path)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]

    def test_macos_sdk_header_excluded(self):
        mac_path = (
            "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/"
            "Developer/SDKs/MacOSX.sdk/usr/include/stdio.h"
        )
        snap = AbiSnapshot(
            library="libfoo.dylib",
            version="1.0",
            from_headers=True,
            types=[_rec("Own"), _rec("__sFILE", source_header=mac_path)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]

    def test_generated_header_is_not_excluded(self):
        """A machine-generated header (protobuf/moc/...) is part of the
        project's own deliverable, not a toolchain dependency -- only
        _SYSTEM_HEADER_DIRS excludes, generated/ trees don't match it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[_rec("Message", source_header="/src/myproject/generated/msg.pb.h")],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Message"]


class TestInstalledLibraryUnderSystemPrefix:
    """Regression coverage for a Codex-review P1 finding: an installed
    library analyzed via its real system-prefixed install path (e.g. a
    distro package's ``-H /usr/include/mylib/api.h``) must not have its own
    headers misclassified as toolchain headers just because they live under
    /usr/include -- that would silently empty the whole snapshot. The
    dump's actual -H root set must take precedence over the bare
    path-heuristic."""

    def test_own_header_under_usr_include_kept_when_it_is_the_root(self):
        root = "/usr/include/mylib/api.h"
        snap = AbiSnapshot(
            library="libmylib.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("mylib_run", source_header=root)],
            types=[_rec("MyLibStruct", source_header=root)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap, header_roots=[root])
        assert [f.name for f in scoped.functions] == ["mylib_run"]
        assert [t.name for t in scoped.types] == ["MyLibStruct"]

    def test_own_private_header_under_same_root_directory_kept(self):
        """A private header the root #include's (not itself passed as -H)
        but living in the same directory tree as the root must also be kept."""
        root = "/usr/include/mylib/api.h"
        private = "/usr/include/mylib/detail/internal.h"
        snap = AbiSnapshot(
            library="libmylib.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("mylib_run", source_header=root),
                _fn(
                    "mylib_internal",
                    vis=Visibility.HIDDEN,
                    mangled="_Z13mylib_internal",
                    source_header=private,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap, header_roots=[root])
        assert {f.name for f in scoped.functions} == {"mylib_run", "mylib_internal"}

    def test_real_dependency_still_excluded_alongside_installed_root(self):
        """The fix must not become "keep everything under /usr/include" --
        a genuine dependency header outside the root's own directory tree
        (e.g. libstdc++'s own tree) must still be excluded."""
        root = "/usr/include/mylib/api.h"
        snap = AbiSnapshot(
            library="libmylib.so",
            version="1.0",
            from_headers=True,
            types=[
                _rec("MyLibStruct", source_header=root),
                _rec("basic_string", source_header="/usr/include/c++/11/string"),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap, header_roots=[root])
        assert [t.name for t in scoped.types] == ["MyLibStruct"]

    def test_no_header_roots_falls_back_to_bare_heuristic(self):
        """Without a recorded root set at all, the old bare-path check still
        applies (a caller that doesn't have the -H list to hand)."""
        snap = AbiSnapshot(
            library="libmylib.so",
            version="1.0",
            from_headers=True,
            types=[_rec("MyLibStruct", source_header="/usr/include/mylib/api.h")],
        )
        scoped = scope_snapshot_excluding_dependencies(snap, header_roots=None)
        assert scoped.types == []


class TestQualifiedNameCollision:
    """Regression coverage for a Codex-review P2 finding: bare-tail matching
    let an excluded dependency type's qualified DWARF entry survive the
    filter when it shared a leaf name with a kept project type (a kept
    ``mine::Thing`` and an excluded ``std::Thing`` both reduce to the bare
    tail ``Thing``)."""

    def test_excluded_type_sharing_leaf_name_not_kept_via_dwarf(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    size_bits=32,
                    qualified_name="mine::Thing",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Thing",
                    kind="struct",
                    size_bits=64,
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "mine::Thing": StructLayout(name="mine::Thing", byte_size=4),
                    "std::Thing": StructLayout(name="std::Thing", byte_size=8),
                },
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf is not None
        assert set(scoped.dwarf.structs) == {"mine::Thing"}


class TestDirectlyReferencedDependencyRetention:
    """Status-review follow-up (P0 against PR #649): a dependency-header
    type directly named in a kept public signature must survive scoping,
    while its own purely-transitive internals stay excluded."""

    def test_stdlib_type_directly_referenced_by_public_param_is_kept(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("std::string",))],
            types=[
                RecordType(
                    name="string",
                    kind="struct",
                    qualified_name="std::string",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="_Alloc_hider",
                    kind="struct",
                    qualified_name="std::string::_Alloc_hider",
                    source_header=_SYSTEM_HEADER,
                    fields=[TypeField(name="ptr", type="char *")],
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        kept_names = {t.name for t in scoped.types}
        assert "string" in kept_names, (
            "std::string is directly named in run()'s own signature -- "
            "the library's ABI genuinely depends on its layout"
        )
        assert "_Alloc_hider" not in kept_names, (
            "only reachable transitively through std::string's own "
            "internals -- must still be excluded"
        )

    def test_non_public_libc_type_directly_referenced_is_kept(self):
        """The review's own example: `struct tm` from <time.h> used
        directly in a public function's signature."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("public_fn", params=("struct Internal *", "struct tm *"))],
            types=[
                _rec("Internal"),
                _rec("tm", source_header="/usr/include/time.h"),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert {t.name for t in scoped.types} == {"Internal", "tm"}

    def test_dependency_type_referenced_only_via_field_of_kept_type_is_kept(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[
                _rec("Wrapper", fields=(("value", "std::string"),)),
                RecordType(
                    name="string",
                    kind="struct",
                    qualified_name="std::string",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert {t.name for t in scoped.types} == {"Wrapper", "string"}

    def test_dependency_enum_directly_referenced_is_kept(self):
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", ret="errc")],
            enums=[
                EnumType(
                    name="errc",
                    members=[EnumMember(name="A", value=0)],
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [e.name for e in scoped.enums] == ["errc"]

    def test_ambiguous_bare_name_does_not_cross_admit_unrelated_type(self):
        """Codex review (P2): two dependency records sharing a bare `name`
        under different `qualified_name`s -- only the one actually named in
        a kept signature must be retained, not both via the shared bare
        spelling."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("std::Thing *",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="vendor::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["std::Thing"]

    def test_typedef_alias_resolves_dependency_target_record(self):
        """Codex review (P1): a signature spells a dependency type through a
        typedef alias (`std::string`) while the record's own identity is the
        underlying spelling (`std::__cxx11::basic_string<...>`) -- the link
        lives only in `snapshot.typedefs` and must still be followed."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("std::string",))],
            types=[
                RecordType(
                    name="basic_string",
                    kind="struct",
                    qualified_name="std::__cxx11::basic_string<char>",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"std::string": "std::__cxx11::basic_string<char>"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == [
            "std::__cxx11::basic_string<char>"
        ]

    def test_typedef_target_normalized_before_matching(self):
        """Codex review (P1, second round): a real DWARF typedef target is
        stored already namespace/ABI-tag-stripped (`basic_string<...>`),
        while the record's own identity is the full, qualified spelling
        (`std::__cxx11::basic_string<...>`) -- these must still resolve via
        `_stripped_signature_spelling`, not just exact string equality."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("std::string",))],
            types=[
                RecordType(
                    name="basic_string",
                    kind="struct",
                    qualified_name=(
                        "std::__cxx11::basic_string<char, std::char_traits<char>, "
                        "std::allocator<char> >"
                    ),
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={
                "std::string": (
                    "basic_string<char, std::char_traits<char>, std::allocator<char> >"
                )
            },
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["basic_string"]

    def test_chained_typedef_alias_resolves_dependency_target(self):
        """Codex review (P1, third round): `using Handle = Thing; using
        Thing = std::Thing;` -- a signature spelling the outermost alias
        must still resolve through the chain to the dependency record."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "Thing", "Thing": "std::Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["std::Thing"]

    def test_decorated_typedef_target_resolves_dependency_record(self):
        """Codex review (P1, third round): `using Handle = std::Thing *;` --
        the typedef target is a *decorated* form (pointer), not an exact
        match for the candidate's own identity, and must still resolve via
        a substring/token match rather than requiring exact equality."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "std::Thing *"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["std::Thing"]

    def test_chained_decorated_typedef_target_resolves_dependency_record(self):
        """Codex review (P1, fourth round): `using Ptr = Handle *; using
        Handle = std::Thing;` -- `"Handle *"` is not itself a typedef key
        (only the embedded `"Handle"` token is), so a whole-string chain
        follower stops there. The token must be expanded within the
        decorated intermediate target too."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Ptr",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Ptr": "Handle *", "Handle": "std::Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["std::Thing"]

    def test_long_typedef_chain_resolves_beyond_a_fixed_hop_count(self):
        """Codex review (P2, fifth/sixth rounds): a chain of fifty distinct
        alias hops must still resolve to the real dependency identity --
        two successive earlier versions capped expansion at a fixed round
        count (8, then 32), each truncating a legitimate longer chain
        before it ever reached the target. Resolution is now via
        pointer-doubling (`_resolve_typedef_chains`), which has no fixed
        cap at all -- convergence is O(log(chain depth))."""
        typedefs = {f"A{i}": f"A{i + 1}" for i in range(50)}
        typedefs["A50"] = "std::Thing"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("A0",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs=typedefs,
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["std::Thing"]

    def test_typedef_resolution_stays_fast_with_many_typedefs(self):
        """Self-review follow-up: an earlier version recompiled the
        typedef-key spelling pattern once per alias (O(typedef count^2)
        before any signature is even scanned) -- confirmed empirically at
        ~30s for 3,000 typedefs. Must stay well under a second now that the
        pattern is compiled once and reused."""
        import time

        typedefs = {f"Alias{i}": f"Alias{i + 1}" for i in range(2000)}
        typedefs["Alias2000"] = "int"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            types=[_rec("Own")],
            typedefs=typedefs,
        )
        start = time.monotonic()
        scope_snapshot_excluding_dependencies(snap)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"typedef resolution took {elapsed:.2f}s, expected < 5s"

    def test_typedef_matching_stays_fast_with_many_candidates_and_typedefs(self):
        """Codex review (sixth round): matching resolved typedef targets
        against dependency candidates one-by-one was
        O(dep_candidates x typedefs) -- confirmed empirically at ~5.6s for
        3,000 candidates x 3,000 typedefs. Must stay well under that now
        that resolved targets are scanned once via a shared reverse
        index."""
        import time

        typedefs = {f"Alias{i}": f"target{i}" for i in range(1500)}
        types = [_rec(f"Dep{i}", source_header=_SYSTEM_HEADER) for i in range(1500)]
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            types=types,
            typedefs=typedefs,
        )
        start = time.monotonic()
        scope_snapshot_excluding_dependencies(snap)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"typedef matching took {elapsed:.2f}s, expected < 5s"

    def test_typedef_alias_bare_suffix_spelling_resolves_dependency_record(self):
        """Self-review follow-up: a real backend can spell a typedef alias
        itself bare in a signature (`string` for a `typedefs["std::string"]`
        entry, DWARF's own convention) -- indexing only the literal alias
        key missed this, the same bare-vs-qualified split already handled
        for candidate identities."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("string",))],
            types=[
                RecordType(
                    name="basic_string",
                    kind="struct",
                    qualified_name="std::__cxx11::basic_string<char>",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"std::string": "std::__cxx11::basic_string<char>"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["basic_string"]

    def test_kept_enum_collision_guards_bare_dependency_spelling(self):
        """Codex review (P2, fourth round): a kept enum's bare spelling
        (`api::Status` spelled bare `Status`) must guard against an
        unrelated dependency record sharing that same bare identity
        (`vendor::Status`), the same way a kept *type*'s spelling already
        did -- an earlier version checked kept_types only."""
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", ret="Status")],
            enums=[
                EnumType(
                    name="Status",
                    qualified_name="api::Status",
                    members=[EnumMember(name="A", value=0)],
                    source_header=_OWN_HEADER,
                ),
            ],
            types=[
                RecordType(
                    name="Status",
                    kind="struct",
                    qualified_name="vendor::Status",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []
        assert [e.qualified_name for e in scoped.enums] == ["api::Status"]

    def test_bare_dependency_identity_guarded_against_kept_type_collision(self):
        """Codex review (P2, fourth round): a dependency candidate's own
        full identity is not automatically trusted -- when a kept type's
        own bare-suffix spelling (`api::Foo` spelled bare `Foo`) collides
        with an unrelated dependency candidate's bare identity (`Foo`, no
        namespace of its own), the dependency candidate must not be
        retained through that collision."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Foo",))],
            types=[
                RecordType(
                    name="Foo",
                    kind="struct",
                    qualified_name="api::Foo",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Foo",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["api::Foo"]

    def test_partially_qualified_nested_dependency_type_is_kept(self):
        """Codex review (P2, second round): a direct-clang-style backend
        spells a nested dependency type with the enclosing namespace
        elided but the class-nesting qualifier kept (`Outer::Inner` for
        `vendor::Outer::Inner`) -- a partial qualification distinct from
        both the full identity and the fully bare leaf."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Outer::Inner *",))],
            types=[
                RecordType(
                    name="Inner",
                    kind="struct",
                    qualified_name="vendor::Outer::Inner",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["vendor::Outer::Inner"]

    def test_typedef_derived_spelling_colliding_with_kept_type_not_trusted(self):
        """Codex review (P2, third round): a scope-losing typedef entry
        (`"Alias" -> "std::Thing"`) derives the spelling `"Alias"` for the
        dependency record -- but a kept type is *also* named `Alias`. A
        signature naming the kept `Alias` must not incorrectly retain the
        unrelated `std::Thing` dependency record through this collision."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Alias",))],
            types=[
                _rec("Alias"),
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="std::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Alias": "std::Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Alias"]

    def test_stripped_spelling_colliding_with_kept_type_not_trusted(self):
        """Codex review (P2, third round): a stdlib-stripped spelling
        (`"basic_string<...>"`, stripped from `"std::__cxx11::basic_string<...>"`)
        must not be trusted when a kept, unrelated type happens to be named
        that same bare spelling."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("basic_string<char>",))],
            types=[
                _rec("basic_string<char>"),
                RecordType(
                    name="basic_string",
                    kind="struct",
                    qualified_name="std::__cxx11::basic_string<char>",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["basic_string<char>"]

    def test_unreferenced_dependency_type_still_excluded(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            types=[
                _rec("Own"),
                RecordType(
                    name="unused_dep",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]


class TestEndToEndCompareAfterScoping:
    """Proves the actual point of this feature through the real compare()
    pipeline, not just the filter in isolation: a real ABI break in the
    library's own code must still be caught after default scoping, while a
    change confined to a purely-transitive (never directly referenced)
    dependency type must not surface at all (both sides dropped it
    identically). A dependency type that *is* directly referenced by a kept
    public signature (e.g. ``std::string`` taken by a public function) is
    retained precisely so a real layout drift on it is not silently lost --
    see ``_directly_referenced_dependency_names``."""

    def test_own_library_break_detected_dependency_noise_excluded(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker import compare

        def _snap(own_field_count, dep_size):
            own_fields = [("x", "int"), ("y", "int")][:own_field_count]
            return AbiSnapshot(
                library="libfoo.so",
                version="1.0",
                from_headers=True,
                # "helper *" is not std::string itself, so std::string here
                # is only transitively reachable (through Own's own
                # internals, which this test doesn't model) -- never
                # directly named by a kept signature -- and stays excluded.
                functions=[_fn("run", params=("Own *", "helper *"))],
                types=[
                    _rec("Own", fields=tuple(own_fields), source_header=_OWN_HEADER),
                    RecordType(
                        name="std::string",
                        kind="struct",
                        size_bits=dep_size,
                        source_header=_SYSTEM_HEADER,
                    ),
                ],
            )

        old_scoped = scope_snapshot_excluding_dependencies(_snap(1, 256))
        new_scoped = scope_snapshot_excluding_dependencies(_snap(2, 512))

        # The hazard condition: the dependency type never made it into either
        # scoped snapshot, so a real 256->512 size change on it cannot be
        # observed even though it genuinely happened.
        assert "std::string" not in {t.name for t in old_scoped.types}
        assert "std::string" not in {t.name for t in new_scoped.types}

        result = compare(old_scoped, new_scoped)
        symbols_mentioned = {c.symbol for c in result.changes if c.symbol}
        assert not any("string" in s for s in symbols_mentioned), (
            "no finding should mention the excluded dependency type"
        )
        # The real break (a field added to Own) must still be caught.
        assert result.verdict != Verdict.NO_CHANGE
        assert any(
            c.symbol == "Own" or c.caused_by_type == "Own" for c in result.changes
        )

    def test_directly_referenced_dependency_break_is_caught(self):
        """The status-review P0: a layout change on a dependency type that
        IS directly named in a kept public signature (std::string taken by
        value/pointer by a public function) must survive scoping and be
        detected, not silently dropped from both sides."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker import compare

        def _snap(dep_size):
            return AbiSnapshot(
                library="libfoo.so",
                version="1.0",
                from_headers=True,
                functions=[_fn("run", params=("std::string *",))],
                types=[
                    RecordType(
                        name="std::string",
                        kind="struct",
                        size_bits=dep_size,
                        source_header=_SYSTEM_HEADER,
                    ),
                ],
            )

        old_scoped = scope_snapshot_excluding_dependencies(_snap(256))
        new_scoped = scope_snapshot_excluding_dependencies(_snap(512))

        assert "std::string" in {t.name for t in old_scoped.types}
        assert "std::string" in {t.name for t in new_scoped.types}

        result = compare(old_scoped, new_scoped)
        assert result.verdict != Verdict.NO_CHANGE
        assert any(c.symbol == "std::string" for c in result.changes)
