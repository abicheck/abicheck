from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.policies import builtin_policy_names
from abicheck.policy_file import PolicyFile, builtin_policy_path

# Profiles shipped under abicheck/policies/. Adding a new *.yaml there must keep
# this list in sync (the parametrized tests below load every shipped file, so a
# malformed addition fails regardless, but this anchors the intended catalog).
EXPECTED_PROFILES = {
    "security",
    "qt_kde_cpp",
    "glibc_symbol_versioned",
    "msvc_pe",
    "mach_o_dylib",
    "rust_c_ffi",
    "gnome_parallel_install",
}


def _change(kind: ChangeKind):
    c = MagicMock()
    c.kind = kind
    # Real string (not a truthy mock) so frozen-namespace logic stays inert.
    c.frozen_namespace_violation = ""
    return c


def test_expected_profiles_are_shipped() -> None:
    shipped = set(builtin_policy_names())
    missing = EXPECTED_PROFILES - shipped
    assert not missing, f"expected built-in profiles not shipped: {missing}"


@pytest.mark.parametrize("name", sorted(EXPECTED_PROFILES))
def test_builtin_profile_loads_and_validates(name: str) -> None:
    path = builtin_policy_path(name)
    assert path is not None and path.is_file(), f"{name} did not resolve to a file"
    pf = PolicyFile.load(path)
    # A built-in profile must build cleanly on one of the real base policies and
    # reference only valid kinds (PolicyFile.load drops unknown slugs with a
    # warning, so a populated overrides map means every key parsed).
    assert pf.base_policy in {"strict_abi", "sdk_vendor", "plugin_abi"}
    assert pf.overrides, f"{name} has no overrides — it would be a no-op profile"
    for kind, verdict in pf.overrides.items():
        assert isinstance(kind, ChangeKind)
        assert isinstance(verdict, Verdict)


@pytest.mark.parametrize("name", sorted(EXPECTED_PROFILES))
def test_builtin_profile_has_no_high_risk_downgrades(name: str) -> None:
    """No shipped profile may silently downgrade a crash-class break to ignore."""
    pf = PolicyFile.load(builtin_policy_path(name))
    warnings = pf.validate_overrides()
    high_risk = [w for w in warnings if w.startswith("HIGH RISK")]
    assert not high_risk, f"{name} contains high-risk downgrades: {high_risk}"


def test_qt_kde_promotes_noexcept_removal_to_break() -> None:
    pf = PolicyFile.load(builtin_policy_path("qt_kde_cpp"))
    verdict = pf.compute_verdict([_change(ChangeKind.FUNC_NOEXCEPT_REMOVED)])
    assert verdict == Verdict.BREAKING


def test_gnome_promotes_soname_bump_to_break() -> None:
    pf = PolicyFile.load(builtin_policy_path("gnome_parallel_install"))
    verdict = pf.compute_verdict([_change(ChangeKind.SONAME_BUMP_RECOMMENDED)])
    assert verdict == Verdict.BREAKING


def test_gnome_surfaces_unnecessary_bump_as_risk() -> None:
    """The non-redundant half of the profile: a needless major bump (COMPATIBLE
    base verdict) is surfaced as a deployment risk, not silently accepted."""
    pf = PolicyFile.load(builtin_policy_path("gnome_parallel_install"))
    verdict = pf.compute_verdict([_change(ChangeKind.SONAME_BUMP_UNNECESSARY)])
    assert verdict == Verdict.COMPATIBLE_WITH_RISK


def test_rust_c_ffi_demotes_vtable_change_to_risk() -> None:
    pf = PolicyFile.load(builtin_policy_path("rust_c_ffi"))
    verdict = pf.compute_verdict([_change(ChangeKind.FUNC_VIRTUAL_ADDED)])
    assert verdict == Verdict.COMPATIBLE_WITH_RISK


def test_rust_c_ffi_keeps_c_layout_break_strict() -> None:
    """The C-relevant surface stays a hard break — only C++ kinds are demoted."""
    pf = PolicyFile.load(builtin_policy_path("rust_c_ffi"))
    verdict = pf.compute_verdict([_change(ChangeKind.TYPE_SIZE_CHANGED)])
    assert verdict == Verdict.BREAKING
