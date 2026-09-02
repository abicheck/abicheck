# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for dump-time dependency scoping (``dump --include-system-declarations``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from abicheck.dumper_scoping import (
    resolve_dependency_scope,
    scope_snapshot_excluding_dependencies,
    wrap_run_dump_with_dependency_scope,
)
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
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
from abicheck.model.fact import Fact
from abicheck.model.identity import entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR

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
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN,
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
        source_header=source_header,
        origin=origin,
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

    def test_semantic_ir_occurrence_is_dropped_alongside_its_flat_type(self):
        """ADR-063 Phase 6 (second slice, Codex review, PR #1001):
        dataclasses.replace() used to carry ``snap.semantic_ir`` over
        unfiltered, so an excluded dependency type's occurrence stayed
        reachable through the "filtered" snapshot's own canonical IR even
        though ``types`` correctly dropped it -- a SemanticIR-aware
        consumer could see more than a flat-field one does, defeating this
        function's whole size/surface contract.
        """
        own = _rec("Own")
        dep = _rec("basic_string", source_header=_SYSTEM_HEADER)
        own.entity_id = entity_id_for_type((), "Own")
        dep.entity_id = entity_id_for_type((), "basic_string")
        semantic_ir = SemanticIR(
            occurrences={
                OccurrenceId(own.entity_id): CanonicalEntity(
                    canonical_spelling=Fact.present("Own")
                ),
                OccurrenceId(dep.entity_id): CanonicalEntity(
                    canonical_spelling=Fact.present("basic_string")
                ),
            }
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[own, dep],
            semantic_ir=semantic_ir,
        )
        scoped = scope_snapshot_excluding_dependencies(snap)

        assert [t.name for t in scoped.types] == ["Own"]
        assert scoped.semantic_ir is not None
        assert list(scoped.semantic_ir.occurrences) == [OccurrenceId(own.entity_id)]

    def test_semantic_ir_is_untouched_when_nothing_is_excluded(self):
        """No excluded type/enum -> the same SemanticIR object, not a
        rebuilt-but-equal copy (matches this module's own dataclasses.
        replace() convention of only touching what actually changed)."""
        own = _rec("Own")
        own.entity_id = entity_id_for_type((), "Own")
        semantic_ir = SemanticIR(
            occurrences={
                OccurrenceId(own.entity_id): CanonicalEntity(
                    canonical_spelling=Fact.present("Own")
                ),
            }
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[own],
            semantic_ir=semantic_ir,
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.semantic_ir is semantic_ir

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


class TestDependencyScopeTagging:
    def test_scoped_snapshot_tagged_filtered(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dependency_scope == "filtered"

    def test_noop_path_does_not_fabricate_a_tag(self):
        """The from_headers=False no-op path returns the input unchanged --
        it must not claim "filtered" for a snapshot nothing was actually
        filtered from."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            functions=[_fn("run", vis=Visibility.ELF_ONLY, source_header=None)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dependency_scope is None


class TestResolveDependencyScope:
    """`resolve_dependency_scope` is the single choke point `dump`'s own
    ``cli._write_snapshot_output`` calls -- it determines the serialized
    ``dependency_scope`` for every dump command invocation."""

    def test_default_mode_filters_and_tags_filtered(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("sys", mangled="_Z3sys", source_header=_SYSTEM_HEADER),
            ],
        )
        resolved = resolve_dependency_scope(snap, include_dependencies=False)
        assert resolved.dependency_scope == "filtered"
        assert [f.name for f in resolved.functions] == ["run"]

    def test_include_dependencies_tags_full_without_filtering(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("sys", mangled="_Z3sys", source_header=_SYSTEM_HEADER),
            ],
        )
        resolved = resolve_dependency_scope(snap, include_dependencies=True)
        assert resolved.dependency_scope == "full"
        assert {f.name for f in resolved.functions} == {"run", "sys"}

    def test_include_dependencies_on_non_header_snapshot_stays_untagged(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            functions=[_fn("run", vis=Visibility.ELF_ONLY, source_header=None)],
        )
        resolved = resolve_dependency_scope(snap, include_dependencies=True)
        assert resolved.dependency_scope is None


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

    def test_kept_function_with_unmangled_bare_name_keeps_dwarf_advanced_entry(self):
        """A header-AST backend can't always produce a real mangled name
        (e.g. a header auto-detected as C, or an uninstantiated C++
        template -- ``tu_merge.py``'s own documented limitation) --
        ``Function.mangled`` then falls back to the bare ``name``. A
        non-dependency function in this shape must not lose its real
        DWARF-derived ``value_abi_traits`` entry (keyed by the *true*
        linker-mangled symbol) just because the header-AST spelling
        couldn't be confidently matched against it (regression: this
        previously required an exact match against the unreliable bare
        name, silently dropping the finding)."""
        own_fn = _fn("distance", mangled="distance", source_header=_OWN_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[own_fn],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                value_abi_traits={"_Z8distance5PointS_": "p0:nontrivial"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        assert set(scoped.dwarf_advanced.value_abi_traits) == {"_Z8distance5PointS_"}

    def test_dependency_function_with_genuine_mangled_name_still_dropped(self):
        """The flip side: a dependency-header function whose mangled name
        *is* confidently a real symbol (Itanium ``_Z`` prefix) is still
        excluded, same as before."""
        sys_fn = _fn("sys_fn", mangled="_Z6sys_fn", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[sys_fn],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                value_abi_traits={"_Z6sys_fn": "p0:nontrivial"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        assert set(scoped.dwarf_advanced.value_abi_traits) == set()

    def test_dependency_function_with_bare_unmangled_name_still_dropped(self):
        """Codex review: a genuine C/``extern "C"`` dependency function's
        header-AST ``mangled`` field also falls back to its bare ``name`` --
        but for a *genuinely* unmangled symbol, that bare spelling is also
        its real linker-level name, so it must still be excluded (unlike the
        kept-function case in
        ``test_kept_function_with_unmangled_bare_name_keeps_dwarf_advanced_entry``,
        where the bare spelling is merely an unreliable guess at a *real*
        mangled name). Regression: an earlier fix over-corrected by
        requiring a confident mangling marker for exclusion too, which left
        this class of dependency noise unfiltered."""
        dep_fn = _fn("dep", mangled="dep", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[dep_fn],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                value_abi_traits={"dep": "p0:nontrivial"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        assert set(scoped.dwarf_advanced.value_abi_traits) == set()

    def test_ambiguous_bare_spelling_shared_with_a_kept_function_is_not_excluded(self):
        """Codex review, fresh evidence: a kept `extern "C" foo` genuinely has
        mangled == name == "foo", and an unrelated excluded C++ dependency
        function can independently fail to recover its own (different) real
        mangled name, falling back to a bare spelling that happens to equal
        that same "foo" -- no ODR conflict (they're distinct real symbols),
        but trusting the excluded function's bare spelling to exclude "foo"
        would wrongly drop the *kept* function's own real DWARF-advanced
        entry. Regression for the fix in
        test_dependency_function_with_bare_unmangled_name_still_dropped,
        which (correctly, for a non-colliding bare name) started trusting an
        excluded function's bare spelling again."""
        kept_fn = _fn("foo", mangled="foo", source_header=_OWN_HEADER)
        excluded_fn = _fn("foo", mangled="foo", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[kept_fn, excluded_fn],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                value_abi_traits={"foo": "p0:nontrivial"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        assert set(scoped.dwarf_advanced.value_abi_traits) == {"foo"}

    def test_kept_functions_bare_name_does_not_shadow_a_different_excluded_symbol(self):
        """Codex review, fresh evidence, second round: the collision guard
        above must key only on kept functions' own *mangled* field, not
        their bare *name* too -- a kept C++ function named "dep" with a real,
        different mangled key (``_ZN4mine3depEv``) must not itself shadow an
        unrelated excluded C function genuinely keyed bare "dep": the two
        real DWARF keys don't collide, so excluding the C function's entry
        is still correct and must not be blocked just because a kept
        function happens to share its bare *name* (not its real mangled
        key) with that spelling."""
        kept_fn = _fn("dep", mangled="_ZN4mine3depEv", source_header=_OWN_HEADER)
        excluded_fn = _fn("dep", mangled="dep", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[kept_fn, excluded_fn],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                value_abi_traits={
                    "_ZN4mine3depEv": "p0:nontrivial",
                    "dep": "p0:nontrivial",
                },
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        assert set(scoped.dwarf_advanced.value_abi_traits) == {"_ZN4mine3depEv"}


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


class TestWrapRunDumpWithDependencyScope:
    """``service.run_dump`` is built from ``_run_dump_uncached`` via
    :func:`wrap_run_dump_with_dependency_scope` -- the wrapper that lets
    ``compare``'s live-binary dumping filter consistently with a ``dump``
    baseline instead of always producing the unfiltered surface."""

    def _uncached(self, tagged_snap):
        def _fn(
            path,
            binary_fmt,
            headers=None,
            includes=None,
            version="",
            lang="c++",
            *,
            dump_manifest=None,
            public_headers=None,
            public_header_dirs=None,
            **_kw,
        ):
            return tagged_snap

        return _fn

    def test_default_include_dependencies_true_tags_full(self):
        snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))
        result = run_dump(Path("/lib.so"), "elf")
        assert result.dependency_scope == "full"

    def test_include_dependencies_false_filters(self):
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("sys", mangled="_Z3sys", source_header=_SYSTEM_HEADER),
            ],
        )
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))
        result = run_dump(Path("/lib.so"), "elf", include_dependencies=False)
        assert result.dependency_scope == "filtered"
        assert [f.name for f in result.functions] == ["run"]

    def test_non_header_snapshot_stays_untagged(self):
        snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=False)
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))
        result = run_dump(Path("/lib.so"), "elf")
        assert result.dependency_scope is None

    def test_default_include_dependencies_true_suppresses_streaming_prune(self):
        """A full/unscoped request (``include_dependencies=True``, the
        default) must suppress the opt-in streaming pruner
        (dumper_clang_streaming.py) for the inner call's dynamic extent --
        otherwise the pruner could silently drop dependency-header
        functions/variables even though this wrapper was about to keep them
        (Codex review, PR #840)."""
        from abicheck.dumper_clang_streaming import streaming_prune_suppressed

        observed: list[bool] = []

        def _fn(
            path,
            binary_fmt,
            headers=None,
            includes=None,
            version="",
            lang="c++",
            *,
            dump_manifest=None,
            public_headers=None,
            public_header_dirs=None,
            **_kw,
        ):
            observed.append(streaming_prune_suppressed())
            return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

        run_dump = wrap_run_dump_with_dependency_scope(_fn)
        assert not streaming_prune_suppressed()  # not leaked before the call
        run_dump(Path("/lib.so"), "elf")  # include_dependencies defaults True
        assert observed == [True]
        assert not streaming_prune_suppressed()  # not leaked after the call

    def test_include_dependencies_false_does_not_suppress_streaming_prune(self):
        """A filtered request needs no suppression: the pruner can never be
        more aggressive than the post-hoc filter this wrapper is about to
        apply anyway."""
        from abicheck.dumper_clang_streaming import streaming_prune_suppressed

        observed: list[bool] = []

        def _fn(
            path,
            binary_fmt,
            headers=None,
            includes=None,
            version="",
            lang="c++",
            *,
            dump_manifest=None,
            public_headers=None,
            public_header_dirs=None,
            **_kw,
        ):
            observed.append(streaming_prune_suppressed())
            return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

        run_dump = wrap_run_dump_with_dependency_scope(_fn)
        run_dump(Path("/lib.so"), "elf", include_dependencies=False)
        assert observed == [False]

    def test_headers_recovered_regardless_of_positional_or_keyword(self):
        """`headers` (the -H root set) must reach resolve_dependency_scope's
        header_roots the same way whether the underlying dump function was
        invoked positionally or by keyword."""
        root = "/usr/include/mylib/api.h"
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("mylib_run", source_header=root)],
        )
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))

        by_positional = run_dump(
            Path("/lib.so"), "elf", [root], [], include_dependencies=False
        )
        by_keyword = run_dump(
            Path("/lib.so"), "elf", headers=[root], include_dependencies=False
        )
        assert [f.name for f in by_positional.functions] == ["mylib_run"]
        assert [f.name for f in by_keyword.functions] == ["mylib_run"]

    def test_dump_manifest_roots_recovered_when_no_headers_given(self):
        """Codex review: ``--dump-manifest`` is mutually exclusive with
        ``-H``, so ``headers`` alone is empty for a manifest-driven dump.
        Without also recovering the manifest's own project-owned roots
        (``roots``/``public_header_paths``/``public_header_dirs``/
        project-owned TU includes), a project header installed under a
        system-like prefix (e.g. ``/usr/include/mylib/``) would be
        misclassified as a dependency and filtered out."""
        root = "/usr/include/mylib/api.h"
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("mylib_run", source_header=root)],
        )
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))
        manifest = SimpleNamespace(
            roots=[root],
            public_header_paths=[],
            public_header_dirs=[],
            translation_units=[],
        )

        result = run_dump(
            Path("/lib.so"), "elf", include_dependencies=False, dump_manifest=manifest
        )
        assert [f.name for f in result.functions] == ["mylib_run"]

    def test_public_header_dirs_recovered_as_roots(self, tmp_path):
        """Codex review (ADR-055 D1): a declared-public directory
        (InputSpec.public_header_dirs / --public-header-dir) must be treated
        as a project root the same way `-H`/`--header` roots are, even
        though it wasn't also passed via `headers` -- previously it never
        reached resolve_dependency_scope's header_roots at all, so a header
        under it (e.g. an installed library's own system-prefixed path) could
        be misclassified as a dependency and dropped."""
        # A real, existing directory whose segments actually match the
        # system-header heuristic (`_SYSTEM_HEADER_DIRS`'s ("usr", "include")
        # subsequence) -- a directory root is filesystem-checked via
        # `Path.is_dir()`, so a merely tmp_path-rooted directory that doesn't
        # itself look like a system prefix wouldn't exercise this path at all
        # (it would survive filtering either way).
        root = tmp_path / "usr" / "include" / "mylib"
        root.mkdir(parents=True)
        header = str(root / "api.h")
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("mylib_run", source_header=header)],
        )
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))

        result = run_dump(
            Path("/lib.so"),
            "elf",
            include_dependencies=False,
            public_header_dirs=[root],
        )
        assert [f.name for f in result.functions] == ["mylib_run"]

    def test_public_headers_recovered_as_roots(self):
        """Codex review, second pass: the first fix only folded in
        public_header_dirs, missing the file-level public_headers set -- an
        explicitly-declared public *file* (reached transitively rather than
        listed in `headers`, e.g. an installed library's own
        `/usr/include/mylib/api.h`) must be protected the same way."""
        header = "/usr/include/mylib/api.h"
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("mylib_run", source_header=header)],
        )
        run_dump = wrap_run_dump_with_dependency_scope(self._uncached(snap))

        result = run_dump(
            Path("/lib.so"),
            "elf",
            include_dependencies=False,
            public_headers=[header],
        )
        assert [f.name for f in result.functions] == ["mylib_run"]
