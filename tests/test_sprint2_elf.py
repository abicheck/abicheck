"""Sprint 2 ELF-only detector tests.

All tests build AbiSnapshot + ElfMetadata directly — no castxml, no readelf required.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(elf: ElfMetadata | None = None, **kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    s = AbiSnapshot(**defaults)  # type: ignore[arg-type]
    s.elf = elf
    return s


def _elf(**kwargs: object) -> ElfMetadata:
    defaults: dict[str, object] = dict(soname="libfoo.so.1")
    defaults.update(kwargs)
    return ElfMetadata(**defaults)  # type: ignore[arg-type]


def _sym(name: str, **kwargs: object) -> ElfSymbol:
    defaults: dict[str, object] = dict(
        binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC, size=0
    )
    defaults.update(kwargs)
    return ElfSymbol(name=name, **defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dynamic section tests
# ---------------------------------------------------------------------------


def test_soname_changed() -> None:
    old = _snap(_elf(soname="libfoo.so.1"))
    new = _snap(_elf(soname="libfoo.so.2"))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SONAME_CHANGED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_soname_missing_reported_as_compatible_quality_issue() -> None:
    old = _snap(_elf(soname=""))
    new = _snap(_elf(soname="libfoo.so.1"))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SONAME_MISSING in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_needed_added() -> None:
    # NEEDED_ADDED is COMPATIBLE (warn only): the new dep may not exist on
    # older systems but existing consumers keep working if symbols are still present.
    old = _snap(_elf(needed=["libc.so.6"]))
    new = _snap(_elf(needed=["libc.so.6", "libssl.so.3"]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.NEEDED_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_needed_removed() -> None:
    old = _snap(_elf(needed=["libc.so.6", "libm.so.6"]))
    new = _snap(_elf(needed=["libc.so.6"]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.NEEDED_REMOVED in kinds
    assert result.verdict == Verdict.COMPATIBLE  # NEEDED_REMOVED is compatible


def test_rpath_changed() -> None:
    old = _snap(_elf(rpath="/opt/old/lib"))
    new = _snap(_elf(rpath="/opt/new/lib"))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.RPATH_CHANGED in kinds


def test_runpath_changed() -> None:
    old = _snap(_elf(runpath="/opt/old/lib"))
    new = _snap(_elf(runpath=""))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.RUNPATH_CHANGED in kinds


# ---------------------------------------------------------------------------
# Symbol versioning tests
# ---------------------------------------------------------------------------


def test_symbol_version_defined_removed() -> None:
    old = _snap(_elf(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"]))
    new = _snap(_elf(versions_defined=["LIBFOO_2.0"]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED in kinds
    assert result.verdict == Verdict.BREAKING


def test_unattached_private_symbol_version_marker_removed_is_ignored() -> None:
    old = _snap(_elf(versions_defined=["LIBFOO_1.0", "LIBFOO_PRIVATE"]))
    new = _snap(_elf(versions_defined=["LIBFOO_1.0"]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED not in kinds
    assert result.verdict == Verdict.NO_CHANGE


def test_private_symbol_version_with_exported_symbol_removed_is_breaking() -> None:
    old = _snap(
        _elf(
            versions_defined=["LIBFOO_1.0", "LIBFOO_PRIVATE"],
            symbols=[_sym("private_api", version="LIBFOO_PRIVATE")],
        )
    )
    new = _snap(_elf(versions_defined=["LIBFOO_1.0"], symbols=[]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_NODE_REMOVED in kinds
    assert result.verdict == Verdict.BREAKING


def test_symbol_version_defined_added_compatible() -> None:
    old = _snap(_elf(versions_defined=[]))
    new = _snap(_elf(versions_defined=["LIBFOO_1.0"]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_symbol_version_required_added() -> None:
    """New GLIBC_2.34 requirement = breaks on older runtimes."""
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5"]}))
    new = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_symbol_version_required_removed() -> None:
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.17"]}))
    new = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5"]}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED in kinds


# ---------------------------------------------------------------------------
# Per-symbol metadata tests
# ---------------------------------------------------------------------------


def test_symbol_binding_global_to_weak() -> None:
    old = _snap(_elf(symbols=[_sym("foo", binding=SymbolBinding.GLOBAL)]))
    new = _snap(_elf(symbols=[_sym("foo", binding=SymbolBinding.WEAK)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_BINDING_CHANGED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_symbol_type_func_to_object() -> None:
    old = _snap(_elf(symbols=[_sym("bar", sym_type=SymbolType.FUNC)]))
    new = _snap(_elf(symbols=[_sym("bar", sym_type=SymbolType.OBJECT)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_TYPE_CHANGED in kinds


def test_ifunc_introduced() -> None:
    old = _snap(_elf(symbols=[_sym("dispatch", sym_type=SymbolType.FUNC)]))
    new = _snap(_elf(symbols=[_sym("dispatch", sym_type=SymbolType.IFUNC)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.IFUNC_INTRODUCED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_ifunc_removed() -> None:
    old = _snap(_elf(symbols=[_sym("dispatch", sym_type=SymbolType.IFUNC)]))
    new = _snap(_elf(symbols=[_sym("dispatch", sym_type=SymbolType.FUNC)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.IFUNC_REMOVED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_symbol_size_changed() -> None:
    old = _snap(_elf(symbols=[_sym("g_state", sym_type=SymbolType.OBJECT, size=8)]))
    new = _snap(_elf(symbols=[_sym("g_state", sym_type=SymbolType.OBJECT, size=16)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_SIZE_CHANGED in kinds


def test_common_symbol_risk() -> None:
    old = _snap(_elf(symbols=[]))
    new = _snap(_elf(symbols=[_sym("g_counter", sym_type=SymbolType.COMMON)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.COMMON_SYMBOL_RISK in kinds


# ---------------------------------------------------------------------------
# No-change negative tests
# ---------------------------------------------------------------------------

_ELF_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.SONAME_CHANGED,
        ChangeKind.SONAME_MISSING,
        ChangeKind.NEEDED_ADDED,
        ChangeKind.NEEDED_REMOVED,
        ChangeKind.RPATH_CHANGED,
        ChangeKind.RUNPATH_CHANGED,
        ChangeKind.SYMBOL_BINDING_CHANGED,
        ChangeKind.SYMBOL_TYPE_CHANGED,
        ChangeKind.SYMBOL_SIZE_CHANGED,
        ChangeKind.IFUNC_INTRODUCED,
        ChangeKind.IFUNC_REMOVED,
        ChangeKind.COMMON_SYMBOL_RISK,
        ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
        ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
        ChangeKind.SYMBOL_BINDING_STRENGTHENED,
    }
)


def test_no_elf_changes() -> None:
    """Identical ELF metadata → no ELF-related changes."""
    elf = _elf(
        needed=["libc.so.6"],
        versions_defined=["LIBFOO_1.0"],
        versions_required={"libc.so.6": ["GLIBC_2.5"]},
        symbols=[
            _sym("foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC, size=32)
        ],
    )
    old = _snap(elf)
    new = _snap(elf)
    result = compare(old, new)
    elf_kinds = {c.kind for c in result.changes if c.kind in _ELF_CHANGE_KINDS}
    assert elf_kinds == set(), (
        f"Unexpected ELF changes on identical metadata: {elf_kinds}"
    )


def test_both_elf_none_produces_no_changes() -> None:
    """Both snapshots without ELF metadata → no ELF changes, no crash."""
    old = _snap(None)
    new = _snap(None)
    result = compare(old, new)
    elf_kinds = {c.kind for c in result.changes if c.kind in _ELF_CHANGE_KINDS}
    assert elf_kinds == set()


def test_symbol_size_changed_func_not_flagged() -> None:
    """STT_FUNC symbol size change must NOT produce SYMBOL_SIZE_CHANGED.

    Function size = machine-code bytes; changes with every compile/opt level.
    Flagging it would produce massive false positives. Only STT_OBJECT/TLS matter.
    """
    old = _snap(_elf(symbols=[_sym("foo", sym_type=SymbolType.FUNC, size=100)]))
    new = _snap(_elf(symbols=[_sym("foo", sym_type=SymbolType.FUNC, size=200)]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_SIZE_CHANGED not in kinds


def test_weak_to_global_binding_compatible() -> None:
    """WEAK→GLOBAL strengthens a symbol — backward-compatible for most consumers.

    Exception: consumers that override via weak interposition lose the override.
    Classified as COMPATIBLE; document the edge case.
    """
    old = _snap(_elf(symbols=[_sym("foo", binding=SymbolBinding.WEAK)]))
    new = _snap(_elf(symbols=[_sym("foo", binding=SymbolBinding.GLOBAL)]))
    result = compare(old, new)
    # SYMBOL_BINDING_CHANGED should still be reported (informational),
    # but the overall verdict must NOT be BREAKING.
    assert result.verdict != Verdict.BREAKING


def test_versions_required_entire_lib_removed() -> None:
    """When a lib disappears entirely from versions_required, all its versions are flagged removed."""
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.17"]}))
    new = _snap(_elf(versions_required={}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED in kinds
    removed = [
        c.symbol
        for c in result.changes
        if c.kind == ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED
    ]
    assert set(removed) == {"GLIBC_2.5", "GLIBC_2.17"}


# ---------------------------------------------------------------------------
# Verdict mapping checks
# ---------------------------------------------------------------------------


def test_elf_breaking_kinds_verdict() -> None:
    """BREAKING ELF kinds (defined version removed, type changed) produce BREAKING verdict."""
    # NOTE: SONAME_CHANGED is excluded here — it is now in COMPATIBLE_KINDS because
    # SONAME is a packaging/policy signal, not a binary ABI break. See test_soname_changed.
    breaking_cases = [
        _snap(_elf(versions_defined=["V1"])),  # SYMBOL_VERSION_DEFINED_REMOVED
        _snap(_elf(symbols=[_sym("f", sym_type=SymbolType.FUNC)])),  # TYPE_CHANGED
    ]
    new_cases = [
        _snap(_elf(versions_defined=[])),
        _snap(_elf(symbols=[_sym("f", sym_type=SymbolType.OBJECT)])),
    ]
    for old, new in zip(breaking_cases, new_cases):
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING, (
            f"Expected BREAKING, got {result.verdict}: {[c.kind for c in result.changes]}"
        )


def test_elf_risk_kinds_verdict() -> None:
    """RISK ELF kinds (new required version) produce COMPATIBLE_WITH_RISK verdict."""
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5"]}))
    new = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]}))
    result = compare(old, new)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK, (
        f"Expected COMPATIBLE_WITH_RISK, got {result.verdict}: {[c.kind for c in result.changes]}"
    )


# ---------------------------------------------------------------------------
# Visibility leak detector tests
# ---------------------------------------------------------------------------


def _elf_only_snap(symbols: list[str]) -> AbiSnapshot:
    """Build an ELF-only snapshot (elf_only_mode=True) with given symbol names."""
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(name=s, mangled=s, return_type="?", visibility=Visibility.ELF_ONLY)
            for s in symbols
        ],
        elf_only_mode=True,
    )
    return snap


def test_visibility_leak_detected() -> None:
    """Internal-looking ELF_ONLY symbols in old lib → VISIBILITY_LEAK plus removal breaks."""
    old = _elf_only_snap(["public_api", "internal_helper", "another_impl"])
    new = _elf_only_snap(["public_api"])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.VISIBILITY_LEAK in kinds
    assert ChangeKind.FUNC_REMOVED_ELF_ONLY in kinds
    assert result.verdict == Verdict.BREAKING


def test_visibility_leak_not_fired_without_elf_only_mode() -> None:
    """VISIBILITY_LEAK only fires when elf_only_mode=True (no-header dump)."""
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(
                name="internal_helper",
                mangled="internal_helper",
                return_type="void",
                visibility=Visibility.ELF_ONLY,
            )
        ],
        elf_only_mode=False,  # headers were provided
    )
    new = AbiSnapshot(library="libfoo.so", version="2.0")
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.VISIBILITY_LEAK not in kinds


def test_visibility_leak_not_fired_for_clean_public_symbols() -> None:
    """No VISIBILITY_LEAK if all ELF_ONLY symbols have clean public-looking names."""
    old = _elf_only_snap(["foo", "bar", "compute", "get_version"])
    new = _elf_only_snap(["foo", "bar", "compute", "get_version"])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.VISIBILITY_LEAK not in kinds


def test_visibility_leak_ignores_linker_artifact_symbols() -> None:
    """Linker boundary symbols should not be counted as visibility leaks."""
    old = _elf_only_snap(["public_api", "__bss_start", "_edata", "_end"])
    new = _elf_only_snap(["public_api"])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.VISIBILITY_LEAK not in kinds
    assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in kinds
    assert result.verdict == Verdict.NO_CHANGE


def test_symbol_version_required_added_older_is_compat() -> None:
    """Adding an OLDER version requirement (e.g. GLIBC_2.2.5 when max was GLIBC_2.34)
    is NOT breaking — callers already satisfied the higher requirement.

    Regression: TBB 2021.11→2021.13 false-positive BREAKING (issue tbb-fp-01).
    """
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]}))
    new = _snap(
        _elf(
            versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34", "GLIBC_2.2.5"]}
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    # Should be COMPAT kind, not BREAKING kind
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED not in kinds, (
        "Adding GLIBC_2.2.5 when max was GLIBC_2.34 must not be BREAKING"
    )
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_symbol_version_required_added_newer_is_compatible_with_risk() -> None:
    """Adding a NEWER GLIBC version requirement produces COMPATIBLE_WITH_RISK.

    Existing compiled consumers are unaffected (already linked at build time).
    Deployment risk: the library will not load on systems with an older glibc.
    This is NOT BREAKING — it is a deployment environment constraint.
    """
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5"]}))
    new = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_symbol_version_required_added_new_dep_is_compat() -> None:
    """Adding version requirements for a brand-new DT_NEEDED lib is COMPATIBLE.

    The lib addition itself is captured by needed_added; its version requirements
    don't add extra constraints on callers who already link the old binary.

    Regression: TBB 2021.13 adds libdl.so.2 / GLIBC_2.2.5 false-positive.
    """
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5"]}))
    # libdl.so.2 is entirely new — wasn't in old
    new = _snap(
        _elf(
            versions_required={
                "libc.so.6": ["GLIBC_2.5"],
                "libdl.so.2": ["GLIBC_2.2.5"],
            }
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED not in kinds, (
        "Version requirements for a newly-added lib must not be BREAKING"
    )
    # Positive assertions: the compat kind must appear and verdict must be COMPATIBLE
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in kinds, (
        "Version requirements for a newly-added lib must produce COMPAT kind"
    )
    assert result.verdict == Verdict.COMPATIBLE


def test_symbol_version_required_tbb_like_upgrade() -> None:
    """Full TBB-like scenario: upgrade removes newer requirements + adds older ones.

    TBB 2021.11 required GLIBC_2.34 / GLIBCXX_3.4.32.
    TBB 2021.13 dropped those and only requires GLIBC_2.2.5 / GLIBCXX_3.4.19.
    Net effect: minimum system requirements lowered — this is COMPATIBLE.
    """
    old = _snap(
        _elf(
            versions_required={
                "libc.so.6": [
                    "GLIBC_2.4",
                    "GLIBC_2.14",
                    "GLIBC_2.32",
                    "GLIBC_2.34",
                    "GLIBC_2.2.5",
                ],
                "libstdc++.so.6": [
                    "GLIBCXX_3.4",
                    "GLIBCXX_3.4.11",
                    "GLIBCXX_3.4.32",
                    "CXXABI_1.3.13",
                ],
            }
        )
    )
    new = _snap(
        _elf(
            versions_required={
                "libc.so.6": [
                    "GLIBC_2.4",
                    "GLIBC_2.14",
                    "GLIBC_2.2.5",
                ],  # removed 2.32, 2.34
                "libstdc++.so.6": [
                    "GLIBCXX_3.4",
                    "GLIBCXX_3.4.11",
                    "GLIBCXX_3.4.19",
                ],  # dropped 3.4.32, CXXABI_1.3.13
                "libdl.so.2": ["GLIBC_2.2.5"],  # new dep, old glibc
                "libpthread.so.0": ["GLIBC_2.2.5"],  # new dep, old glibc
            }
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    # No genuinely BREAKING version requirement should appear
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED not in kinds, (
        f"TBB-like upgrade must not produce BREAKING version requirements, got: "
        f"{[c for c in result.changes if c.kind == ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED]}"
    )
    # Compat variants may appear (informational)
    assert result.verdict in (Verdict.COMPATIBLE, Verdict.NO_CHANGE), (
        f"Expected COMPATIBLE, got {result.verdict}"
    )


def test_symbol_version_required_genuine_upgrade_is_breaking() -> None:
    """Upgrading to a library that requires a NEWER glibc is BREAKING.

    If a user is on an old system (only has GLIBC_2.17), they cannot run
    a binary that now requires GLIBC_2.38.
    """
    old = _snap(
        _elf(
            versions_required={
                "libc.so.6": ["GLIBC_2.4", "GLIBC_2.17"],
            }
        )
    )
    new = _snap(
        _elf(
            versions_required={
                "libc.so.6": [
                    "GLIBC_2.4",
                    "GLIBC_2.17",
                    "GLIBC_2.38",
                ],  # genuinely raised the bar
            }
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds, (
        "Raising minimum GLIBC requirement must be COMPATIBLE_WITH_RISK (deployment risk)"
    )
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_symbol_version_required_private_tag_is_breaking() -> None:
    """Adding GLIBC_PRIVATE (non-numeric tag) must be BREAKING, not COMPAT.

    GLIBC_PRIVATE symbols are internal ABI surface — requiring them is a
    genuine runtime constraint that not all systems satisfy.
    Regression guard for _parse_abi_version_tag sentinel logic.
    """
    old = _snap(_elf(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]}))
    new = _snap(
        _elf(
            versions_required={
                "libc.so.6": ["GLIBC_2.5", "GLIBC_2.34", "GLIBC_PRIVATE"]
            }
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds, (
        "Adding GLIBC_PRIVATE must be treated as BREAKING"
    )
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT not in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_symbol_version_required_cross_namespace_no_bleed() -> None:
    """Adding newer CXXABI_1.3.14 must be BREAKING even if GLIBCXX_3.4.32 dominates old_max.

    Without per-prefix grouping, GLIBCXX_3.4.32 (as global old_max=3.4.32) would
    cause CXXABI_1.3.14 (1.3.14 <= 3.4.32) to be misclassified as COMPAT.
    Fix: old_max is computed per version-tag prefix, not globally.
    """
    old = _snap(
        _elf(
            versions_required={
                "libstdc++.so.6": ["GLIBCXX_3.4.32", "CXXABI_1.3.13"],
            }
        )
    )
    new = _snap(
        _elf(
            versions_required={
                "libstdc++.so.6": ["GLIBCXX_3.4.32", "CXXABI_1.3.13", "CXXABI_1.3.14"],
            }
        )
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds, (
        "Adding newer CXXABI_1.3.14 must be BREAKING regardless of GLIBCXX version"
    )
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


# ---------------------------------------------------------------------------
# ELF visibility change tests
# ---------------------------------------------------------------------------


def test_elf_visibility_changed_default_to_protected() -> None:
    """DEFAULT → PROTECTED visibility change is detected and COMPATIBLE."""
    old = _snap(_elf(symbols=[_sym("foo", visibility="default")]))
    new = _snap(_elf(symbols=[_sym("foo", visibility="protected")]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_elf_visibility_unchanged_no_change() -> None:
    """Same visibility → no visibility change."""
    old = _snap(_elf(symbols=[_sym("foo", visibility="default")]))
    new = _snap(_elf(symbols=[_sym("foo", visibility="default")]))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED not in kinds


# ---------------------------------------------------------------------------
# Executable stack tests
# ---------------------------------------------------------------------------


def test_executable_stack_detected() -> None:
    """has_executable_stack False → True is detected and COMPATIBLE."""
    old = _snap(_elf(has_executable_stack=False))
    new = _snap(_elf(has_executable_stack=True))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.EXECUTABLE_STACK in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_executable_stack_no_change() -> None:
    """Same has_executable_stack value → no EXECUTABLE_STACK."""
    old = _snap(_elf(has_executable_stack=False))
    new = _snap(_elf(has_executable_stack=False))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.EXECUTABLE_STACK not in kinds
