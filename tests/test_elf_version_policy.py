"""Tests for ELF symbol-version policy checks (diff_versioning.py).

Covers:
  - Version node removal detection
  - Symbol migration between version nodes
  - SONAME bump recommendation (breaking changes, no SONAME change)
  - SONAME bump unnecessary (no breaking changes, SONAME changed)
  - Version script missing detection
  - Integration with checker.compare()
"""
from __future__ import annotations

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import (
    SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER,
    Change,
)
from abicheck.diff_versioning import (
    _build_version_node_map,
    check_soname_bump_policy,
    detect_version_node_changes,
    detect_version_script_missing,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sym(name: str, version: str = "", binding: SymbolBinding = SymbolBinding.GLOBAL) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        binding=binding,
        sym_type=SymbolType.FUNC,
        size=8,
        version=version,
        is_default=True,
    )


def _breaking_change(symbol: str = "foo") -> Change:
    """Create a dummy BREAKING change for testing."""
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=f"Function removed: {symbol}",
    )


def _compatible_change(symbol: str = "bar") -> Change:
    """Create a dummy COMPATIBLE change for testing."""
    return Change(
        kind=ChangeKind.FUNC_ADDED,
        symbol=symbol,
        description=f"Function added: {symbol}",
    )


# ===========================================================================
# Version node removal
# ===========================================================================

class TestVersionNodeRemoved:
    def test_removed_version_node(self):
        """A version node entirely removed -> SYMBOL_VERSION_NODE_REMOVED."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                _sym("foo", "LIBFOO_1.0"),
                _sym("bar", "LIBFOO_1.0"),
                _sym("baz", "LIBFOO_2.0"),
            ],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_2.0"],
            symbols=[
                _sym("baz", "LIBFOO_2.0"),
            ],
        )
        changes = detect_version_node_changes(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYMBOL_VERSION_NODE_REMOVED
        assert changes[0].symbol == "LIBFOO_1.0"
        assert "LIBFOO_1.0" in changes[0].description
        # Both symbols should appear in the description
        assert "bar" in changes[0].description
        assert "foo" in changes[0].description

    def test_no_removed_nodes(self):
        """No nodes removed -> no SYMBOL_VERSION_NODE_REMOVED changes."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[_sym("foo", "LIBFOO_1.0")],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                _sym("foo", "LIBFOO_1.0"),
                _sym("bar", "LIBFOO_2.0"),
            ],
        )
        changes = detect_version_node_changes(old, new)
        assert changes == []

    def test_empty_versions(self):
        """Both sides have no version definitions -> no changes."""
        old = ElfMetadata(symbols=[_sym("foo")])
        new = ElfMetadata(symbols=[_sym("foo")])
        changes = detect_version_node_changes(old, new)
        assert changes == []

    def test_multiple_nodes_removed(self):
        """Multiple version nodes removed simultaneously."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0", "LIBFOO_3.0"],
            symbols=[
                _sym("a", "LIBFOO_1.0"),
                _sym("b", "LIBFOO_2.0"),
                _sym("c", "LIBFOO_3.0"),
            ],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_3.0"],
            symbols=[_sym("c", "LIBFOO_3.0")],
        )
        changes = detect_version_node_changes(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.SYMBOL_VERSION_NODE_REMOVED]
        assert len(removed) == 2
        removed_nodes = {c.symbol for c in removed}
        assert removed_nodes == {"LIBFOO_1.0", "LIBFOO_2.0"}

    def test_truncation_more_than_five_symbols(self):
        """Removed node with >5 symbols truncates the description."""
        syms = [_sym(f"sym_{i}", "LIBFOO_1.0") for i in range(8)]
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=syms,
        )
        new = ElfMetadata(versions_defined=[], symbols=[])
        changes = detect_version_node_changes(old, new)
        assert len(changes) == 1
        desc = changes[0].description
        # Should show only 5 names and a "+3 more" suffix
        assert "(+3 more)" in desc
        # Count comma-separated names before the suffix
        sym_list_part = desc.split("Symbols previously under this node: ")[1].split(".")[0]
        # Remove the (+3 more) part to count names
        names_part = sym_list_part.split(" (+")[0]
        assert names_part.count(",") == 4  # 5 names = 4 commas

    def test_single_symbol_in_removed_node(self):
        """Removed node with exactly 1 symbol."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[_sym("only_sym", "LIBFOO_1.0")],
        )
        new = ElfMetadata(versions_defined=[], symbols=[])
        changes = detect_version_node_changes(old, new)
        assert len(changes) == 1
        assert "only_sym" in changes[0].description
        assert "(+" not in changes[0].description  # no truncation


# ===========================================================================
# Symbol migration between version nodes
# ===========================================================================

class TestSymbolMovedVersionNode:
    def test_symbol_moved_between_nodes(self):
        """Symbol moves from one version node to another -> SYMBOL_MOVED_VERSION_NODE."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                _sym("foo", "LIBFOO_1.0"),
                _sym("bar", "LIBFOO_2.0"),
            ],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                _sym("foo", "LIBFOO_2.0"),  # moved!
                _sym("bar", "LIBFOO_2.0"),
            ],
        )
        changes = detect_version_node_changes(old, new)
        moved = [c for c in changes if c.kind == ChangeKind.SYMBOL_MOVED_VERSION_NODE]
        assert len(moved) == 1
        assert moved[0].symbol == "foo"
        assert moved[0].old_value == "LIBFOO_1.0"
        assert moved[0].new_value == "LIBFOO_2.0"
        assert "LIBFOO_1.0" in moved[0].description
        assert "LIBFOO_2.0" in moved[0].description

    def test_symbol_stays_in_same_node(self):
        """Symbol remains in same version node -> no change."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[_sym("foo", "LIBFOO_1.0")],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[_sym("foo", "LIBFOO_1.0")],
        )
        changes = detect_version_node_changes(old, new)
        assert changes == []

    def test_new_symbol_not_reported_as_moved(self):
        """A symbol only in new is NOT reported as moved."""
        old = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[_sym("foo", "LIBFOO_1.0")],
        )
        new = ElfMetadata(
            versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"],
            symbols=[
                _sym("foo", "LIBFOO_1.0"),
                _sym("new_sym", "LIBFOO_2.0"),
            ],
        )
        changes = detect_version_node_changes(old, new)
        moved = [c for c in changes if c.kind == ChangeKind.SYMBOL_MOVED_VERSION_NODE]
        assert len(moved) == 0


# ===========================================================================
# Version node map internals
# ===========================================================================

class TestBuildVersionNodeMap:
    def test_symbol_version_not_in_defined_is_excluded(self):
        """A symbol with version tag NOT in versions_defined is excluded from node map."""
        elf = ElfMetadata(
            versions_defined=["LIBFOO_1.0"],
            symbols=[
                _sym("foo", "LIBFOO_1.0"),
                _sym("bar", "LIBFOO_ORPHAN"),  # version not in defined
            ],
        )
        node_map = _build_version_node_map(elf)
        assert "LIBFOO_ORPHAN" not in node_map
        assert "LIBFOO_1.0" in node_map
        assert node_map["LIBFOO_1.0"] == {"foo"}


# ===========================================================================
# SONAME bump recommendation
# ===========================================================================

class TestSonameBumpRecommended:
    def test_breaking_changes_no_soname_bump(self):
        """Breaking changes but SONAME not bumped -> SONAME_BUMP_RECOMMENDED."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.1")
        changes = [_breaking_change("func1"), _breaking_change("func2")]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_RECOMMENDED
        assert "2 binary-incompatible" in result[0].description
        assert "libfoo.so.1" in result[0].description

    def test_breaking_changes_with_soname_bump(self):
        """Breaking changes AND SONAME bumped -> no recommendation needed."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        changes = [_breaking_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert result == []

    def test_no_breaking_no_soname_change(self):
        """No breaking changes, no SONAME change -> nothing to report."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.1")
        changes = [_compatible_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert result == []

    def test_no_soname_at_all(self):
        """No SONAME on either side -> skip recommendation."""
        old_elf = ElfMetadata(soname="")
        new_elf = ElfMetadata(soname="")
        changes = [_breaking_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert result == []

    def test_soname_removed_with_breaking(self):
        """SONAME dropped (was set, now empty) with breaking changes."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="")
        changes = [_breaking_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_RECOMMENDED
        assert "dropped" in result[0].description
        assert result[0].new_value == ""

    def test_mixed_breaking_and_compatible(self):
        """Mixed breaking + compatible changes still triggers recommendation."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.1")
        changes = [_breaking_change("rm1"), _compatible_change("add1"), _breaking_change("rm2")]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_RECOMMENDED
        assert "2 binary-incompatible" in result[0].description

    def test_effective_breaking_change_requires_soname_bump(self):
        """Per-finding BREAKING verdicts count as binary-incompatible."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.1")
        change = Change(
            kind=ChangeKind.STDLIB_IMPLEMENTATION_CHANGED,
            symbol="__stdlib_implementation",
            description="stdlib implementation changed",
            effective_verdict=Verdict.BREAKING,
        )

        result = check_soname_bump_policy([change], old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_RECOMMENDED
        assert "1 binary-incompatible" in result[0].description


# ===========================================================================
# SONAME bump unnecessary
# ===========================================================================

class TestSonameBumpUnnecessary:
    def test_soname_bumped_no_breaking(self):
        """SONAME bumped but no breaking changes -> SONAME_BUMP_UNNECESSARY."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        changes = [_compatible_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_UNNECESSARY
        assert "libfoo.so.1" in result[0].description
        assert "libfoo.so.2" in result[0].description

    def test_soname_bumped_with_breaking(self):
        """SONAME bumped with breaking changes -> no unnecessary warning."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        changes = [_breaking_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert result == []

    def test_empty_changes_soname_bumped(self):
        """Empty change list but SONAME bumped -> SONAME_BUMP_UNNECESSARY."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        changes: list[Change] = []

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SONAME_BUMP_UNNECESSARY

    def test_soname_removed_not_considered_bump(self):
        """SONAME dropped (empty) is NOT considered a bump -> no unnecessary warning."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="")
        changes = [_compatible_change()]

        result = check_soname_bump_policy(changes, old_elf, new_elf)
        assert result == []


# ===========================================================================
# Version script missing
# ===========================================================================

class TestVersionScriptMissing:
    def test_both_missing_version_script_is_preexisting(self):
        """Both old and new lack version script -> no warning (pre-existing)."""
        old = ElfMetadata(
            symbols=[_sym("foo"), _sym("bar")],
            versions_defined=[],
        )
        new = ElfMetadata(
            symbols=[_sym("foo"), _sym("bar")],
            versions_defined=[],
        )
        changes = detect_version_script_missing(old, new)
        assert changes == []

    def test_version_script_dropped(self):
        """Old had version script, new does not -> VERSION_SCRIPT_MISSING."""
        old = ElfMetadata(
            symbols=[_sym("foo", "LIBFOO_1.0")],
            versions_defined=["LIBFOO_1.0"],
        )
        new = ElfMetadata(
            symbols=[_sym("foo")],
            versions_defined=[],
        )
        changes = detect_version_script_missing(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.VERSION_SCRIPT_MISSING

    def test_new_library_no_old_symbols(self):
        """Old has no symbols, new exports without version script -> warning."""
        old = ElfMetadata(symbols=[], versions_defined=[])
        new = ElfMetadata(
            symbols=[_sym("foo"), _sym("bar")],
            versions_defined=[],
        )
        changes = detect_version_script_missing(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.VERSION_SCRIPT_MISSING

    def test_library_with_version_script(self):
        """Library has version definitions -> no warning."""
        old = ElfMetadata(
            symbols=[_sym("foo", "LIBFOO_1.0")],
            versions_defined=["LIBFOO_1.0"],
        )
        new = ElfMetadata(
            symbols=[_sym("foo", "LIBFOO_1.0")],
            versions_defined=["LIBFOO_1.0"],
        )
        changes = detect_version_script_missing(old, new)
        assert changes == []

    def test_no_symbols_no_warning(self):
        """Library with no symbols -> no warning (nothing to version)."""
        old = ElfMetadata(symbols=[], versions_defined=[])
        new = ElfMetadata(symbols=[], versions_defined=[])
        changes = detect_version_script_missing(old, new)
        assert changes == []

    def test_old_missing_new_has_version_script(self):
        """Old side lacks version script but new has one -> no warning."""
        old = ElfMetadata(
            symbols=[_sym("foo")],
            versions_defined=[],
        )
        new = ElfMetadata(
            symbols=[_sym("foo", "LIBFOO_1.0")],
            versions_defined=["LIBFOO_1.0"],
        )
        changes = detect_version_script_missing(old, new)
        assert changes == []

    def test_symbols_with_versions_but_no_defined(self):
        """Symbols carry version tags but versions_defined is empty -> no warning.

        This can happen with imported version tags from .gnu.version_r.
        The has_any_version guard should suppress the warning.
        """
        old = ElfMetadata(symbols=[], versions_defined=[])
        new = ElfMetadata(
            symbols=[_sym("foo", "GLIBC_2.17")],
            versions_defined=[],
        )
        changes = detect_version_script_missing(old, new)
        assert changes == []


# ===========================================================================
# ChangeKind classification checks
# ===========================================================================

class TestChangeKindClassification:
    def test_symbol_version_node_removed_is_breaking(self):
        from abicheck.checker_policy import BREAKING_KINDS
        assert ChangeKind.SYMBOL_VERSION_NODE_REMOVED in BREAKING_KINDS

    def test_symbol_moved_version_node_is_risk(self):
        from abicheck.checker_policy import RISK_KINDS
        assert ChangeKind.SYMBOL_MOVED_VERSION_NODE in RISK_KINDS

    def test_soname_bump_recommended_is_compatible(self):
        from abicheck.checker_policy import COMPATIBLE_KINDS
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in COMPATIBLE_KINDS

    def test_soname_bump_unnecessary_is_compatible(self):
        from abicheck.checker_policy import COMPATIBLE_KINDS
        assert ChangeKind.SONAME_BUMP_UNNECESSARY in COMPATIBLE_KINDS

    def test_version_script_missing_is_compatible(self):
        from abicheck.checker_policy import COMPATIBLE_KINDS
        assert ChangeKind.VERSION_SCRIPT_MISSING in COMPATIBLE_KINDS


# ===========================================================================
# Cross-detector deduplication
# ===========================================================================

class TestDeduplication:
    def test_version_node_removed_deduplicates_defined_removed(self):
        """SYMBOL_VERSION_NODE_REMOVED and SYMBOL_VERSION_DEFINED_REMOVED
        for the same version string should be deduplicated."""
        from abicheck.diff_filtering import _deduplicate_cross_detector

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
                symbol="LIBFOO_1.0",
                description="Version node LIBFOO_1.0 removed",
                old_value="LIBFOO_1.0",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
                symbol="LIBFOO_1.0",
                description="Symbol version removed: LIBFOO_1.0",
                old_value="LIBFOO_1.0",
            ),
        ]
        result = _deduplicate_cross_detector(changes)
        # First one wins — should keep SYMBOL_VERSION_NODE_REMOVED
        assert len(result) == 1
        assert result[0].kind == ChangeKind.SYMBOL_VERSION_NODE_REMOVED

    def test_different_version_strings_not_deduplicated(self):
        """Different version strings should NOT be deduplicated."""
        from abicheck.diff_filtering import _deduplicate_cross_detector

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
                symbol="LIBFOO_1.0",
                description="node removed",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
                symbol="LIBFOO_2.0",
                description="def removed",
            ),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 2


# ===========================================================================
# Integration: checker.compare() picks up version policy changes
# ===========================================================================

class TestCheckerIntegration:
    def test_compare_detects_version_node_removal(self):
        """Full pipeline: version node removal is detected and deduplicated."""
        from abicheck.elf_metadata import ElfMetadata
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                versions_defined=["LIBFOO_1.0"],
                symbols=[_sym("foo", "LIBFOO_1.0")],
            ),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                versions_defined=[],
                symbols=[_sym("foo")],
            ),
        )
        from abicheck.checker import compare
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        # The more specific SYMBOL_VERSION_NODE_REMOVED should win dedup
        assert ChangeKind.SYMBOL_VERSION_NODE_REMOVED in kinds
        # The simpler SYMBOL_VERSION_DEFINED_REMOVED should be deduplicated away
        assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED not in kinds

    def test_compare_soname_bump_recommended(self):
        """Full pipeline: SONAME bump recommendation fires when breaking + no bump."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(name="removed_func", mangled="removed_func",
                         return_type="void", params=[],
                         visibility=Visibility.PUBLIC),
            ],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                symbols=[_sym("removed_func")],
            ),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                symbols=[],
            ),
        )
        from abicheck.checker import compare
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        # The function removal is breaking; soname unchanged -> recommendation
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in kinds

    def test_compare_soname_policy_uses_postprocessed_changes(self):
        """A breaking rename with unchanged SONAME should recommend a bump."""
        from abicheck.checker import compare
        from abicheck.checker_policy import Verdict
        from abicheck.model import AbiSnapshot, Function, Visibility

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="old_name",
                    mangled="old_name",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[_sym("old_name")]),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="1.1",
            functions=[
                Function(
                    name="new_name",
                    mangled="new_name",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[_sym("new_name")]),
        )
        result = compare(
            old,
            new,
            extra_changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="old_name",
                    description="Function removed: old_name",
                    old_value="old_name",
                ),
                Change(
                    kind=ChangeKind.FUNC_ADDED,
                    symbol="new_name",
                    description="Function added: new_name",
                    new_value="new_name",
                ),
                Change(
                    kind=ChangeKind.FUNC_LIKELY_RENAMED,
                    symbol="old_name",
                    description="Function likely renamed: old_name -> new_name",
                    old_value="old_name",
                    new_value="new_name",
                ),
            ],
        )
        kinds = {c.kind for c in result.changes}

        assert result.verdict == Verdict.BREAKING
        assert ChangeKind.FUNC_LIKELY_RENAMED in kinds
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in kinds

    def test_compare_soname_bump_not_unnecessary_for_breaking_rename(self):
        """SONAME bumped for a breaking rename must not read as unnecessary.

        Exercises the second branch of check_soname_bump_policy through the
        post-processed change set: the removed/added halves are rename-redundant
        and excluded from the verdict input, but the collapsed rename remains a
        binary-incompatible change.
        """
        from abicheck.checker import compare
        from abicheck.checker_policy import Verdict
        from abicheck.model import AbiSnapshot, Function, Visibility

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="old_name",
                    mangled="old_name",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[_sym("old_name")]),
        )
        new = AbiSnapshot(
            library="libfoo.so.2",
            version="2.0",
            functions=[
                Function(
                    name="new_name",
                    mangled="new_name",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            elf=ElfMetadata(soname="libfoo.so.2", symbols=[_sym("new_name")]),
        )
        result = compare(
            old,
            new,
            extra_changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="old_name",
                    description="Function removed: old_name",
                    old_value="old_name",
                ),
                Change(
                    kind=ChangeKind.FUNC_ADDED,
                    symbol="new_name",
                    description="Function added: new_name",
                    new_value="new_name",
                ),
                Change(
                    kind=ChangeKind.FUNC_LIKELY_RENAMED,
                    symbol="old_name",
                    description="Function likely renamed: old_name -> new_name",
                    old_value="old_name",
                    new_value="new_name",
                ),
            ],
        )
        kinds = {c.kind for c in result.changes}

        assert result.verdict == Verdict.BREAKING
        assert ChangeKind.FUNC_LIKELY_RENAMED in kinds
        assert ChangeKind.SONAME_BUMP_UNNECESSARY not in kinds
        assert ChangeKind.SONAME_BUMP_RECOMMENDED not in kinds

    def test_compare_soname_bump_recommendation_honors_suppression(self):
        """Late-generated SONAME advisories must still pass suppression."""
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.suppression import Suppression, SuppressionList

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="removed_func",
                    mangled="removed_func",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[_sym("removed_func")]),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[],
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[]),
        )
        suppression = SuppressionList([
            Suppression(
                symbol="DT_SONAME",
                change_kind=ChangeKind.SONAME_BUMP_RECOMMENDED.value,
                reason="tracked separately",
            )
        ])

        result = compare(old, new, suppression=suppression)
        kinds = {c.kind for c in result.changes}
        suppressed_kinds = {c.kind for c in result.suppressed_changes}

        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.SONAME_BUMP_RECOMMENDED not in kinds
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in suppressed_kinds
        assert result.suppressed_count == 1

    def test_compare_version_script_missing(self):
        """Full pipeline: version script dropped -> warning fires."""
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                symbols=[_sym("foo", "LIBFOO_1.0")],
                versions_defined=["LIBFOO_1.0"],
            ),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(
                soname="libfoo.so.1",
                symbols=[_sym("foo")],
                versions_defined=[],
            ),
        )
        from abicheck.checker import compare
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VERSION_SCRIPT_MISSING in kinds


class TestVersionNodeBumpDeduplication:
    """A version-node bump must not be double-counted per symbol."""

    def test_moved_node_and_alias_change_deduplicated(self) -> None:
        from abicheck.diff_filtering import _deduplicate_cross_detector

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="moved",
                old_value="LLVM_17",
                new_value="LLVM_18.1",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
                symbol="foo",
                # Not-retained case (old default gone): genuinely redundant with
                # the node move, so it carries the marker and is collapsed.
                description=(
                    "default version changed — "
                    + SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER
                ),
                old_value="LLVM_17",
                new_value="LLVM_18.1",
            ),
        ]
        kept = _deduplicate_cross_detector(changes)
        kinds = [c.kind for c in kept]
        # The alias-change duplicate is dropped; the node-level change is kept
        # (one finding for one version-node bump, not two).
        assert kinds == [ChangeKind.SYMBOL_MOVED_VERSION_NODE]

    def test_retained_alias_change_survives_matching_node_move(self) -> None:
        from abicheck.diff_filtering import _deduplicate_cross_detector

        # Old default is RETAINED as a non-default alias: the alias-change is
        # compatible and distinct from the node move's "will not find" wording,
        # so it must NOT be collapsed even though the transition matches.
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="moved",
                old_value="LIBA_1",
                new_value="LIBA_2",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
                symbol="foo",
                description="Default symbol version changed: foo (@@LIBA_1 → @@LIBA_2)",
                old_value="LIBA_1",
                new_value="LIBA_2",
            ),
        ]
        kept = _deduplicate_cross_detector(changes)
        kinds = {c.kind for c in kept}
        assert ChangeKind.SYMBOL_MOVED_VERSION_NODE in kinds
        assert ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED in kinds
        assert len(kept) == 2

    def test_moved_node_kept_without_matching_alias(self) -> None:
        from abicheck.diff_filtering import _deduplicate_cross_detector

        # No alias change for this transition → the node move stands on its own.
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="moved",
                old_value="LIBA_1",
                new_value="LIBA_2",
            ),
        ]
        kept = _deduplicate_cross_detector(changes)
        assert [c.kind for c in kept] == [ChangeKind.SYMBOL_MOVED_VERSION_NODE]

    def test_moved_node_kept_when_alias_transition_differs(self) -> None:
        from abicheck.diff_filtering import _deduplicate_cross_detector

        # Alias change exists for the symbol but a different transition → keep both.
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="moved",
                old_value="LIBA_1",
                new_value="LIBA_2",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
                symbol="foo",
                description="alias",
                old_value="LIBA_1",
                new_value="LIBA_3",
            ),
        ]
        kept = _deduplicate_cross_detector(changes)
        kinds = {c.kind for c in kept}
        # Transitions differ, so both findings are kept (no de-duplication).
        assert ChangeKind.SYMBOL_MOVED_VERSION_NODE in kinds
        assert ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED in kinds
        assert len(kept) == 2
