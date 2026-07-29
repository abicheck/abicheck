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
        before it ever reached the target. Resolution is now via the
        graph-reachability worklist (`_typedef_alias_reachability`), which
        has no fixed hop cap at all -- see that function's own docstring
        for how propagation converges."""
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

    def test_long_typedef_chain_reachability_stays_fast(self):
        """Codex review (eighteenth round, P2): the previous full-table
        relaxation rescanned every alias's entire edge set on each of up to
        `len(typedefs)` rounds, advancing a long outer-to-inner chain
        (`A0 -> A1 -> ... -> dep::Thing`) only one hop per round --
        confirmed empirically at ~4.9s for 4,000 chained aliases and
        ~20.4s for 8,000 (quadratic in chain length). A reverse-edge
        worklist must resolve a much longer chain well under a second."""
        import time

        n = 8000
        typedefs = {f"A{i}": f"A{i + 1}" for i in range(n)}
        typedefs[f"A{n}"] = "dep::Thing"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("A0",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs=typedefs,
        )
        start = time.monotonic()
        scoped = scope_snapshot_excluding_dependencies(snap)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"long typedef chain reachability took {elapsed:.2f}s"
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_self_referential_typedefs_do_not_blow_up(self):
        """Codex review (ninth round): `typedef struct Foo Foo;` -- a
        common, real C/C++ idiom -- creates a direct self-reference
        (`typedefs["Foo"] == "struct Foo"`). Naive pointer-doubling
        substitutes the embedded `Foo` token with its own current
        (already-grown) expansion every round, doubling the string length
        indefinitely rather than converging -- confirmed empirically at
        ~74MB of aggregate resolved text for 2,000 such entries. Must stay
        fast and must not grow the text at all."""
        import time

        typedefs = {f"Foo{i}": f"struct Foo{i}" for i in range(2000)}
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Foo0",))],
            types=[
                RecordType(
                    name="Foo0",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs=typedefs,
        )
        start = time.monotonic()
        scoped = scope_snapshot_excluding_dependencies(snap)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"self-referential typedefs took {elapsed:.2f}s"
        assert [t.name for t in scoped.types] == ["Foo0"]

    def test_branching_typedef_chain_does_not_blow_up(self):
        """Codex review (tenth round): `using A0 = Pair<A1, A1>; using A1 =
        Pair<A2, A2>; ...` -- a branching (not merely linear) alias chain.
        Materializing the full expansion doubles the string length at
        every level (exponential in nesting depth, confirmed empirically
        at ~38MB of resolved text for just 20 such levels). Resolution is
        now via set-based reachability (`_typedef_alias_reachability`),
        where a branching reference costs nothing extra since set union is
        idempotent -- must stay fast and still resolve correctly."""
        import time

        typedefs = {f"A{i}": f"Pair<A{i + 1}, A{i + 1}>" for i in range(20)}
        typedefs["A20"] = "dep::Thing"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("A0",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs=typedefs,
        )
        start = time.monotonic()
        scoped = scope_snapshot_excluding_dependencies(snap)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"branching typedef chain took {elapsed:.2f}s"
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_typedef_alias_pointing_at_kept_type_excludes_ambiguous_dependency(self):
        """Codex review (tenth round): an own typedef `Handle -> api::Own`
        means a kept signature spelling `Handle` is semantically naming
        the kept type -- but an unrelated dependency record `dep::Handle`
        derives the same bare suffix `Handle`. The dependency must not be
        retained through this collision; a real signature meaning the
        typedef alias must not be misattributed to it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Own",
                    kind="struct",
                    qualified_name="api::Own",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Handle",
                    kind="struct",
                    qualified_name="dep::Handle",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "api::Own"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["api::Own"]

    def test_compound_typedef_alias_retains_both_kept_and_dependency_components(self):
        """Codex review (eleventh round): `using Alias = Pair<api::Own,
        dep::Thing>;` reaches *both* a kept type and a genuinely distinct
        dependency candidate in the same compound target. Unlike the pure
        `Handle -> api::Own` case above, this is not ambiguous -- the
        alias names both simultaneously -- so treating the kept touch as
        blanket proof the alias belongs only to the kept surface would
        incorrectly erase `dep::Thing`'s retention too."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Alias",))],
            types=[
                RecordType(
                    name="Own",
                    kind="struct",
                    qualified_name="api::Own",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Alias": "Pair<api::Own, dep::Thing>"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert sorted(t.qualified_name for t in scoped.types) == [
            "api::Own",
            "dep::Thing",
        ]

    def test_compound_typedef_alias_retains_both_dependency_components(self):
        """Codex review (seventeenth round): `using Alias = Pair<dep::A,
        dep::B>;` reaches *two distinct dependency candidates* (not one
        kept + one dependency, as in the eleventh-round test above) in the
        same compound target. The single-owner ambiguity check previously
        applied uniformly to every spelling, including a purely
        alias-derived one -- so both identities becoming "owners" of the
        `Alias` spelling made it look like a coincidental collision between
        unrelated candidates and dropped it entirely, even though the
        alias unambiguously, structurally names both at once. Neither
        `dep::A` nor `dep::B` has its own bare name collide with anything
        else here -- the only reason both appear as owners is the shared
        alias -- so both must be retained."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Alias",))],
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    qualified_name="dep::A",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="B",
                    kind="struct",
                    qualified_name="dep::B",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Alias": "Pair<dep::A, dep::B>"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert sorted(t.qualified_name for t in scoped.types) == [
            "dep::A",
            "dep::B",
        ]

    def test_two_separate_aliases_colliding_on_bare_suffix_stay_ambiguous(self):
        """Codex review (nineteenth round): unlike the compound-alias test
        above -- one alias reaching two candidates in the same target,
        which is genuinely unambiguous -- this is *two separate,
        independently-qualified* aliases (`api::Handle -> dep::A`,
        `vendor::Handle -> dep::B`) that merely happen to reduce to the
        identical bare suffix `Handle` via ordinary namespace-dropping.
        A signature spelling bare `Handle` cannot tell which alias it
        means, so neither `dep::A` nor `dep::B` may be retained through
        it -- this is the same class of coincidental collision as two
        unrelated candidates sharing a bare suffix, just one level
        removed through the alias layer."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    qualified_name="dep::A",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="B",
                    kind="struct",
                    qualified_name="dep::B",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"api::Handle": "dep::A", "vendor::Handle": "dep::B"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_two_separate_aliases_still_resolve_via_their_own_qualified_spelling(
        self,
    ):
        """Companion to the test above: when a signature spells the fully
        qualified alias name directly (`api::Handle`, not the ambiguous
        bare-dropped `Handle`), that spelling is unique to one alias and
        must still resolve -- the fix for coincidental bare-suffix
        collision must not also break the unambiguous qualified case."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("api::Handle",))],
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    qualified_name="dep::A",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="B",
                    kind="struct",
                    qualified_name="dep::B",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"api::Handle": "dep::A", "vendor::Handle": "dep::B"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::A"]

    def test_kept_touched_alias_suffix_guards_coincidental_dependency_suffix(
        self,
    ):
        """Codex review (twenty-first round): `kept_touched_aliases` (the
        guard that keeps a candidate's own bare-suffix spelling from
        coincidentally matching an unrelated kept-pointing alias's name)
        previously excluded only the literal alias key (`api::Handle`),
        never its namespace-suffix expansion (`Handle`). A signature
        spelling the backend's bare-dropped `Handle` -- exactly the
        convention this module already accounts for everywhere else --
        slipped past the guard entirely, letting an unrelated
        `dep::Handle` retain through the coincidental collision even
        though the alias unambiguously pointed at the kept `api::Own`."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Own",
                    kind="struct",
                    qualified_name="api::Own",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Handle",
                    kind="struct",
                    qualified_name="dep::Handle",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"api::Handle": "api::Own"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["api::Own"]

    def test_primitive_typedef_alias_shadows_coincidental_dependency_suffix(
        self,
    ):
        """Codex review (twenty-second round): `typedefs["Handle"] =
        "int"` -- an alias to a primitive, not to any modeled record or
        enum -- reaches neither a kept spelling nor any dependency
        candidate's key, so it was never recorded by
        `kept_touched_aliases` (which only tracks an alias whose target
        resolves to something). An unrelated `dep::Handle` deriving the
        identical bare suffix `Handle` was therefore retained as if the
        signature's `Handle` genuinely named it, even though it
        unambiguously names the primitive alias instead. A typedef alias
        existing under a name at all is a collision claim on that name,
        regardless of what it resolves to."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Handle",
                    kind="struct",
                    qualified_name="dep::Handle",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "int"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_primitive_typedef_alias_shadows_exact_tag_identity_too(self):
        """Codex review (twenty-third round): unlike the previous test's
        `dep::Handle` (a *derived*, namespace-suffix match against
        `Handle`), this dependency candidate's own identity is *exactly*
        bare `Handle` -- a plain C-style struct tag with no namespace,
        never assigned a `qualified_name`. In C, tag names (`struct
        Handle`) and typedef names occupy separate namespaces, so a
        signature spelling the bare, unqualified `Handle` can only mean
        `typedefs["Handle"] = "int"`'s typedef -- the tag is never
        reachable that way, exact-identity match or not. The previous
        round's veto only covered a *derived* suffix owner; an *exact*
        identity owner bypassed it entirely and was retained regardless
        of the colliding primitive alias."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Handle",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "int"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_resolved_alias_takes_precedence_over_colliding_exact_tag(self):
        """Codex review (twenty-eighth round): `typedef struct Actual
        Handle;` alongside an unrelated `struct Handle` (a plain C-style
        tag, its own identity exactly `Handle`, not merely a derived
        suffix). The round-eighteen precedence fix only ever deferred a
        candidate's *derived* own-spelling claim to a resolved alias; an
        *exact*-identity claim still merged with the alias owner and
        required a single combined owner, so this collision dropped both
        `Actual` and `Handle` instead of retaining `Actual` -- the same
        separate-namespaces reasoning as the primitive-alias tests above,
        but now for an exact tag collision instead of a derived one."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Actual",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="Handle",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "struct Actual"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Actual"]

    def test_elaborated_tag_reference_survives_colliding_primitive_typedef(
        self,
    ):
        """Codex review (twenty-fourth round): unlike the previous two
        tests' *bare* `Handle` (correctly deferring to
        `typedefs["Handle"] = "int"`), this signature explicitly writes
        the elaborated-type-specifier form `struct Handle *`. In both C
        and C++, tag names and typedef names occupy separate namespaces,
        and the `struct` keyword is exactly what disambiguates a
        signature naming the tag directly, even when a same-named
        typedef alias also exists -- the unconditional bare-name veto
        must not also swallow this unambiguous elaborated reference."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("struct Handle *",))],
            types=[
                RecordType(
                    name="Handle",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "int"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Handle"]

    def test_elaborated_spelling_still_collision_guards_against_kept_type(
        self,
    ):
        """Codex review (thirty-first round, P1): a kept `api::Foo` and an
        unrelated dependency-header global `struct Foo` share the bare
        tag name `Foo`. A declaration inside `api::` can spell a
        reference to the kept type as bare `struct Foo *` (the same
        namespace-dropping convention this module already accounts for
        elsewhere) -- but `kept_spellings` only ever added the bare `Foo`
        suffix, never its elaborated form, so `struct Foo` slipped past
        the kept-type collision guard and retained the unrelated
        dependency `Foo` as if the signature's elaborated reference
        named it. Only `api::Foo` (the kept type) may be retained;
        the dependency `Foo` must not be."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("struct Foo *",))],
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

    def test_typedef_chain_through_a_shadowing_key_does_not_reach_dependency(
        self,
    ):
        """Codex review (twenty-fifth round): `typedef int Handle; typedef
        Handle B;` alongside an unrelated dependency `struct Handle` means
        the token `Handle` inside `B`'s own target is *simultaneously* a
        typedef key (resolving through to `int`, an uninteresting
        terminal) and the struct's own interesting key. `B` never
        actually names the struct -- only the *typedef* named `Handle`,
        which happens to resolve to a primitive -- so a kept signature
        using `B` must not retain `struct Handle` through this shadowing
        coincidence."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("B",))],
            types=[
                RecordType(
                    name="Handle",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "int", "B": "Handle"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_globally_qualified_signature_spelling_still_matches(self):
        """Codex review (twenty-sixth round): direct-clang preserves a
        signature's explicit global-scope qualifier in its printed
        `qualType` -- `void f(::dep::Thing *)` keeps the leading `::`.
        The boundary-aware matcher's negative lookbehind treats `:` as a
        non-boundary character (so it can't accidentally match a partial
        scope like bare `Thing` inside `ns::Thing`), which also means the
        plain `dep::Thing` spelling fails to match when immediately
        preceded by the extra `:` of a leading `::` -- silently dropping
        a directly-referenced dependency type whose only signature
        mention happens to be globally qualified."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("::dep::Thing *",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_globally_qualified_unnamespaced_signature_spelling_matches(self):
        """Codex review (twenty-seventh round): the previous fix only
        added the ``::``-prefixed spelling when the candidate's own
        identity already contained ``::`` -- but direct-clang preserves
        an explicit global-scope qualifier for an *unnamespaced* type
        just the same, e.g. `void f(::Foo *)` for a plain `struct Foo`
        with no namespace at all. That case was still excluded (the
        guard only ran when `"::" in identity`), so a directly-
        referenced unnamespaced dependency type whose only signature
        mention is globally qualified was silently dropped."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("::Foo *",))],
            types=[
                RecordType(
                    name="Foo",
                    kind="struct",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Foo"]

    def test_globally_qualified_typedef_alias_reference_matches(self):
        """Codex review (twenty-ninth round): direct-clang preserves an
        explicit global-scope qualifier on a typedef alias reference the
        same way it does on a direct type reference (rounds 26/27) --
        `using Handle = dep::Thing; void f(::Handle);` keeps the leading
        `::` on the alias, not just on a record/enum's own qualified
        name. The alias-spelling index only ever registered the bare
        alias name and its namespace suffixes, so a signature spelling
        `::Handle` failed to resolve through the alias to the directly-
        referenced `dep::Thing` at all."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("::Handle",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "dep::Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_alias_colliding_with_a_non_resolving_alias_stays_ambiguous(self):
        """Codex review (thirtieth round, P2): `api::Handle -> dep::A` and
        `vendor::Handle -> int` share the bare suffix `Handle`, but the
        second alias resolves to a primitive, not to any dep_candidate.
        An earlier revision only tracked spelling collisions among
        aliases *already known* to reach a dep_candidate -- so the
        non-resolving `vendor::Handle` was invisible to the ambiguity
        check, and `dep::A` was incorrectly retained as if `api::Handle`
        were the spelling's only possible source. `dep::A` must not be
        retained: the bare `Handle` genuinely could mean either alias."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    qualified_name="dep::A",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"api::Handle": "dep::A", "vendor::Handle": "int"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_partially_agreeing_aliases_retain_their_common_owner(self):
        """Codex review (thirtieth round, P1): `api::Handle -> Pair<dep::A,
        dep::B>` and `vendor::Handle -> dep::A` share the bare suffix
        `Handle` and *partially* agree -- both, unambiguously, could mean
        `dep::A` regardless of which alias the spelling denotes, even
        though only one of them also reaches `dep::B`. An earlier
        revision required every colliding alias's reach to be the
        *identical* set, which dropped this partial agreement entirely;
        only the genuinely disputed `dep::B` should be excluded, not the
        common `dep::A`."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    qualified_name="dep::A",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="B",
                    kind="struct",
                    qualified_name="dep::B",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={
                "api::Handle": "Pair<dep::A, dep::B>",
                "vendor::Handle": "dep::A",
            },
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::A"]

    def test_agreeing_colliding_aliases_retain_their_shared_target(self):
        """Codex review (twentieth round, P1): unlike the previous test's
        two *disagreeing* aliases (`api::Handle -> dep::A`, `vendor::Handle
        -> dep::B`), this is two aliases -- `Handle -> dep::Thing` and
        `api::Handle -> dep::Thing` -- that both reduce to the same bare
        `Handle` spelling AND both resolve to the identical dependency
        type. There is nothing to disambiguate here: every alias that
        could produce this spelling names the same referent, so requiring
        a single originating alias (the fix for the disagreeing case)
        must not also drop this spelling -- `dep::Thing` must still be
        retained."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "dep::Thing", "api::Handle": "dep::Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_alias_reaching_an_already_ambiguous_target_key_retains_neither(
        self,
    ):
        """Codex review (twentieth round, P2): an alias's target
        namespace-strips to a bare `Thing`, but that bare key is itself
        already ambiguous among dep_candidates (`dep1::Thing` and
        `dep2::Thing` both derive it). Unlike a genuine compound alias
        (`Pair<dep::A, dep::B>`, whose target contains two *distinct*,
        individually-unambiguous tokens), this alias's target contains
        exactly one *already-ambiguous* token -- there is no way to tell
        which of the two candidates it names, so the alias must not be
        treated as reaching either one. Neither `dep1::Thing` nor
        `dep2::Thing` may be retained through it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Alias",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep1::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep2::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Alias": "Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.types == []

    def test_resolved_typedef_owner_takes_precedence_over_coincidental_suffix(
        self,
    ):
        """Codex review (eighteenth round): a public signature spells
        `Handle`, and `typedefs["Handle"]` resolves to `dep::Actual` --
        but an unrelated dependency record `dep::Handle` also derives the
        bare suffix `Handle`. The resolved alias's own name is a literal,
        exact-name match (the same strength as a candidate's own exact
        identity), while `dep::Handle`'s claim on the same spelling is
        only a namespace-stripped guess, never its literal identity.
        Treating them as equally-strong competing owners (dropping both)
        hid a real layout change to the directly-referenced `dep::Actual`;
        the resolved alias must win and `dep::Actual` alone is retained."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Actual",
                    kind="struct",
                    qualified_name="dep::Actual",
                    source_header=_SYSTEM_HEADER,
                ),
                RecordType(
                    name="Handle",
                    kind="struct",
                    qualified_name="dep::Handle",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "dep::Actual"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::Actual"]

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

    def test_typedef_target_namespace_suffix_resolves_non_stdlib_dependency(self):
        """Codex review (seventh round): castxml's own
        `_underlying_type_name()` stores a namespaced typedef target
        *bare* (`Thing` for an underlying `dep::Thing`) -- a non-stdlib
        producer convention distinct from the stdlib-only stripped-form
        matching, requiring the same namespace-suffix spellings a kept
        signature's own spelling of a candidate already goes through."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Handle",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Handle": "Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["dep::Thing"]

    def test_ambiguous_typedef_target_kept_type_collision_not_trusted(self):
        """Codex review (eighth round): a resolved typedef target spelled
        with a bare suffix that's ambiguous between a kept type
        (`api::Thing`) and an unrelated dependency (`dep::Thing`) must not
        attribute the alias to the dependency -- the earlier guard only
        checked the alias's own spelling for a kept-type collision, never
        the ambiguous key that produced the attribution."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Alias",))],
            types=[
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="api::Thing",
                    source_header=_OWN_HEADER,
                ),
                RecordType(
                    name="Thing",
                    kind="struct",
                    qualified_name="dep::Thing",
                    source_header=_SYSTEM_HEADER,
                ),
            ],
            typedefs={"Alias": "Thing"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.qualified_name for t in scoped.types] == ["api::Thing"]

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
