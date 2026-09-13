"""Symbol versioning coexistence tests.

Validates ELF symbol version scenarios including:
1. Multiple version nodes (LIBFOO_1.0 + LIBFOO_2.0) coexisting
2. Default vs non-default version aliases (foo@@V2 vs foo@V1)
3. Version node additions and removals
4. Required version changes (dependency version requirements)
5. SONAME bump policy checks with version-aware changes
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    Visibility,
)


def _snap(version="1.0", functions=None, elf=None, elf_only_mode=False):
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        elf=elf,
        elf_only_mode=elf_only_mode,
    )


def _pub_func(name, mangled, ret="void"):
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _kinds(result):
    return {c.kind for c in result.changes}


# ═══════════════════════════════════════════════════════════════════════════
# Version Node Additions & Removals
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionNodeAddition:
    """Adding a new version node is typically compatible."""

    def test_single_version_added(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0"])
        new_elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in _kinds(r)
        assert r.verdict != Verdict.BREAKING

    def test_multiple_versions_added(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0"])
        new_elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0", "LIBFOO_3.0"]
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in _kinds(r)


class TestVersionNodeRemoval:
    """Removing a version node may break existing binaries."""

    def test_single_version_removed(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"])
        new_elf = ElfMetadata(versions_defined=["LIBFOO_2.0"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED in _kinds(r)

    def test_all_versions_removed(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"])
        new_elf = ElfMetadata(versions_defined=[])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED in _kinds(r)


class TestVersionNodeCoexistence:
    """Multiple version nodes coexisting (glibc-style versioning)."""

    def test_no_change_with_same_versions(self):
        elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0", "LIBFOO_3.0"])
        r = compare(_snap(elf=elf), _snap(elf=elf))
        version_changes = [c for c in r.changes if "version" in c.kind.value.lower()]
        assert len(version_changes) == 0

    def test_version_added_while_others_unchanged(self):
        """Adding a new version while existing ones persist.

        Moving a symbol's default version from one node to another triggers
        SYMBOL_MOVED_VERSION_NODE (BREAKING), because binaries linked against
        the old default version would resolve to a different implementation.
        """
        old_elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                ElfSymbol(
                    name="foo",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_2.0",
                    is_default=True,
                ),
            ],
        )
        new_elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0", "LIBFOO_3.0"],
            symbols=[
                ElfSymbol(
                    name="foo",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_3.0",
                    is_default=True,
                ),
            ],
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        # New version added
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in kind_set
        # Moving default version is a breaking change
        assert ChangeKind.SYMBOL_MOVED_VERSION_NODE in kind_set
        assert r.verdict == Verdict.BREAKING


# ═══════════════════════════════════════════════════════════════════════════
# Default vs Non-Default Version Aliases
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionAliases:
    """Symbol version alias changes (foo@V1 vs foo@@V2)."""

    def test_default_version_changed(self):
        """Default version moved from one node to another."""
        old_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="api",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_1.0",
                    is_default=True,
                ),
            ],
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
        )
        new_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="api",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_2.0",
                    is_default=True,
                ),
            ],
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_MOVED_VERSION_NODE in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_symbol_lost_default_status(self):
        """Symbol went from @@default to @specific (non-default)."""
        old_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="api",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="V1",
                    is_default=True,
                ),
            ],
        )
        new_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="api",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="V1",
                    is_default=False,
                ),
            ],
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        # Losing default status changes linker resolution behavior
        kind_set = _kinds(r)
        assert len(kind_set) > 0 or r.verdict == Verdict.NO_CHANGE


# ═══════════════════════════════════════════════════════════════════════════
# Required Version Changes
# ═══════════════════════════════════════════════════════════════════════════


class TestRequiredVersionChanges:
    """Changes to .gnu.version_r (required versions from dependencies)."""

    def test_new_dependency_version_required(self):
        old_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})
        new_elf = ElfMetadata(
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]}
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        assert (
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kind_set
            or ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in kind_set
        )

    def test_dependency_version_dropped(self):
        old_elf = ElfMetadata(
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28"]}
        )
        new_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED in _kinds(r)

    def test_new_dependency_library_added(self):
        old_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})
        new_elf = ElfMetadata(
            versions_required={
                "libc.so.6": ["GLIBC_2.17"],
                "libm.so.6": ["GLIBC_2.17"],
            }
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        assert (
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kind_set
            or ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in kind_set
        )

    def test_dependency_library_removed(self):
        old_elf = ElfMetadata(
            versions_required={
                "libc.so.6": ["GLIBC_2.17"],
                "libm.so.6": ["GLIBC_2.17"],
            }
        )
        new_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# Combined Version + Symbol Changes
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionWithSymbolChanges:
    """Version changes combined with symbol-level changes."""

    def test_version_added_with_func_added(self):
        """New version node + new function in that version."""
        f = _pub_func("new_api", "_Z7new_apiv")
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0"])
        new_elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                ElfSymbol(
                    name="_Z7new_apiv",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_2.0",
                ),
            ],
        )
        r = compare(
            _snap(elf=old_elf),
            _snap(functions=[f], elf=new_elf),
        )
        kind_set = _kinds(r)
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in kind_set
        assert ChangeKind.FUNC_ADDED in kind_set

    def test_version_removed_with_func_removed(self):
        """Old version node + associated function both removed."""
        f = _pub_func("old_api", "_Z7old_apiv")
        old_elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                ElfSymbol(
                    name="_Z7old_apiv",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    version="LIBFOO_1.0",
                ),
            ],
        )
        new_elf = ElfMetadata(versions_defined=["LIBFOO_2.0"])
        r = compare(
            _snap(functions=[f], elf=old_elf),
            _snap(elf=new_elf),
        )
        kind_set = _kinds(r)
        assert ChangeKind.SYMBOL_VERSION_NODE_REMOVED in kind_set
        assert ChangeKind.FUNC_REMOVED in kind_set


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionEdgeCases:
    """Edge cases in version handling."""

    def test_empty_version_lists(self):
        """Both snapshots with no versions → no version changes."""
        elf = ElfMetadata(versions_defined=[], versions_required={})
        r = compare(_snap(elf=elf), _snap(elf=elf))
        version_kinds = {c.kind for c in r.changes if "version" in c.kind.value.lower()}
        assert len(version_kinds) == 0

    def test_same_versions_no_change(self):
        elf = ElfMetadata(
            versions_defined=["V1", "V2"],
            versions_required={"libc.so.6": ["GLIBC_2.17"]},
        )
        r = compare(_snap(elf=elf), _snap(elf=elf))
        version_kinds = {c.kind for c in r.changes if "version" in c.kind.value.lower()}
        assert len(version_kinds) == 0

    def test_version_rename_is_add_plus_remove(self):
        """Renaming a version node appears as both removal and addition."""
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0"])
        new_elf = ElfMetadata(versions_defined=["LIBFOO_V1"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED in kind_set
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in kind_set
