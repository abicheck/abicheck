# Copyright 2026 Nikolay Petrov
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

"""End-to-end *workflow / topology* scenario tests.

The example catalog (``examples/caseNN_*``) is exhaustive about *change types*
but every case is consumed through the single-pair ``compare`` workflow. These
tests cover the *consumption topologies* that ``compare`` alone does not express
— a drop-in upgrade gate, an additive minor release, a host↔plugin load
contract, and a policy-scoped release decision — using synthetic snapshots so
they run in the fast (pure-Python) suite. They double as integration coverage
for the release recommender (``abicheck/semver.py``).

See docs/development/usecase-coverage-evaluation.md (gap G3 / G5).
"""

from __future__ import annotations

from abicheck.appcompat import check_plugin_host_contract
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import AbiSnapshot, EnumMember, EnumType, Function, Visibility
from abicheck.semver import SemverBump, SonameAction, recommend_release


def _fn(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def _lib(
    version: str, symbols: list[str], *, enums: list[EnumType] | None = None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libplugin.so",
        version=version,
        functions=[_fn(s) for s in symbols],
        enums=enums or [],
    )


def _removed_symbols(result_changes: list) -> set[str]:
    return {
        c.symbol
        for c in result_changes
        if c.kind in (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY)
    }


# ── Scenario A: drop-in upgrade gate ─────────────────────────────────────────


def test_drop_in_upgrade_with_removed_symbol_is_major_and_soname_bump() -> None:
    """A library that drops a public symbol must be a MAJOR + SONAME bump."""
    old = _lib("1.0", ["api_open", "api_close"])
    new = _lib("2.0", ["api_open"])  # api_close removed

    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING

    rec = recommend_release(result)
    assert rec.bump is SemverBump.MAJOR
    assert rec.soname is SonameAction.BUMP_REQUIRED


# ── Scenario B: additive minor release ───────────────────────────────────────


def test_additive_release_is_minor_no_soname_bump() -> None:
    old = _lib("1.0", ["api_open"])
    new = _lib("1.1", ["api_open", "api_open_ex"])  # purely additive

    result = compare(old, new)
    assert result.verdict is Verdict.COMPATIBLE

    rec = recommend_release(result)
    assert rec.bump is SemverBump.MINOR
    assert rec.soname is SonameAction.NO_BUMP_NEEDED


# ── Scenario C: host ↔ plugin load contract (the dlopen direction) ───────────
#
# A plugin host resolves a fixed set of entry-point symbols from each plugin it
# dlopen()s. Whether a plugin's symbol churn breaks the host depends on the
# *host's* required set — the same consumer-scoped insight `appcompat` applies,
# here in the plugin-load direction rather than the app-link direction.

HOST_REQUIRED_ENTRYPOINTS = {"plugin_init", "plugin_run"}


def test_plugin_drop_inside_host_contract_breaks_the_host() -> None:
    plugin_v1 = _lib("1.0", ["plugin_init", "plugin_run", "plugin_debug"])
    plugin_v2 = _lib("2.0", ["plugin_init", "plugin_debug"])  # drops plugin_run

    result = compare(plugin_v1, plugin_v2)
    removed = _removed_symbols(result.changes)

    # The host needs plugin_run; the plugin stopped providing it.
    assert "plugin_run" in removed
    assert removed & HOST_REQUIRED_ENTRYPOINTS == {"plugin_run"}
    assert result.verdict is Verdict.BREAKING
    assert recommend_release(result).bump is SemverBump.MAJOR


def test_plugin_drop_outside_host_contract_is_library_breaking_but_host_safe() -> None:
    """Library-level BREAKING does not imply *this* host breaks.

    The plugin drops an auxiliary symbol the host never resolves. The per-library
    verdict is still BREAKING (some consumer may use it), but the host's load
    contract is intact — motivating consumer-scoped (`appcompat`) analysis.
    """
    plugin_v1 = _lib("1.0", ["plugin_init", "plugin_run", "plugin_debug"])
    plugin_v2 = _lib("2.0", ["plugin_init", "plugin_run"])  # drops plugin_debug only

    result = compare(plugin_v1, plugin_v2)
    removed = _removed_symbols(result.changes)

    assert "plugin_debug" in removed
    assert removed & HOST_REQUIRED_ENTRYPOINTS == set()  # host unaffected
    # Library-wide verdict is still breaking for whoever *did* use plugin_debug.
    assert result.verdict is Verdict.BREAKING


# ── Scenario C2: first-class host-contract check (gap G5) ─────────────────────
#
# The same insight as Scenario C, but driven through the first-class
# `check_plugin_host_contract` API rather than re-deriving removed-symbol sets
# inline — the plugin-load mirror of `appcompat`.


def test_host_contract_check_breaks_when_required_entrypoint_dropped() -> None:
    plugin_v1 = _lib("1.0", ["plugin_init", "plugin_run", "plugin_debug"])
    plugin_v2 = _lib("2.0", ["plugin_init", "plugin_debug"])  # drops plugin_run

    result = check_plugin_host_contract(
        plugin_v1, plugin_v2, HOST_REQUIRED_ENTRYPOINTS,
    )

    assert result.verdict is Verdict.BREAKING
    assert result.missing_entrypoints == ["plugin_run"]
    assert result.coverage == 50.0
    # The drop shows up as a host-relevant change, not just a library-wide one.
    assert any(c.symbol == "plugin_run" for c in result.breaking_for_host)


def test_host_contract_check_safe_when_drop_outside_contract() -> None:
    """A library-BREAKING drop the host never resolves leaves the host intact."""
    plugin_v1 = _lib("1.0", ["plugin_init", "plugin_run", "plugin_debug"])
    plugin_v2 = _lib("2.0", ["plugin_init", "plugin_run"])  # drops plugin_debug only

    # The library-wide verdict is BREAKING …
    assert compare(plugin_v1, plugin_v2).verdict is Verdict.BREAKING
    # … but the host's load contract is fully satisfied.
    result = check_plugin_host_contract(
        plugin_v1, plugin_v2, HOST_REQUIRED_ENTRYPOINTS,
    )
    assert result.verdict is Verdict.COMPATIBLE
    assert result.missing_entrypoints == []
    assert result.coverage == 100.0


def test_host_contract_check_additive_plugin_is_compatible() -> None:
    plugin_v1 = _lib("1.0", ["plugin_init", "plugin_run"])
    plugin_v2 = _lib("1.1", ["plugin_init", "plugin_run", "plugin_extra"])

    result = check_plugin_host_contract(
        plugin_v1, plugin_v2, HOST_REQUIRED_ENTRYPOINTS,
    )
    assert result.verdict is Verdict.COMPATIBLE
    assert result.missing_entrypoints == []
    assert result.coverage == 100.0


def _cpp_lib(version: str) -> AbiSnapshot:
    """A plugin exporting a C++ entrypoint: source name != mangled linker name."""
    fn = Function(
        name="plugin_run(int)", mangled="_Z10plugin_runi",
        return_type="int", visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(library="libplugin.so", version=version, functions=[fn])


def test_host_contract_demangled_cpp_name_is_not_a_dlsym_export() -> None:
    """A C++ entrypoint is only resolvable by its mangled linker symbol; a
    contract listing the demangled source name must be reported as missing
    (dlsym cannot resolve `plugin_run(int)`)."""
    plugin = _cpp_lib("1.0")

    by_demangled = check_plugin_host_contract(plugin, plugin, {"plugin_run(int)"})
    assert by_demangled.verdict is Verdict.BREAKING
    assert by_demangled.missing_entrypoints == ["plugin_run(int)"]

    by_mangled = check_plugin_host_contract(plugin, plugin, {"_Z10plugin_runi"})
    assert by_mangled.verdict is Verdict.COMPATIBLE
    assert by_mangled.missing_entrypoints == []


def test_snapshot_export_names_covers_vars_and_unmangled() -> None:
    """The resolvable-export set includes exported variables and falls back to
    the plain name when no mangled symbol is recorded."""
    from abicheck.appcompat import _snapshot_export_names
    from abicheck.model import Variable

    snap = AbiSnapshot(
        library="libplugin.so",
        version="1.0",
        functions=[
            # extern "C": name == mangled → plain name resolvable
            Function(name="plugin_init", mangled="plugin_init",
                     return_type="int", visibility=Visibility.PUBLIC),
            # no mangled recorded → fall back to name
            Function(name="legacy_entry", mangled="",
                     return_type="int", visibility=Visibility.PUBLIC),
            # non-public → never a dlsym export
            Function(name="internal_helper", mangled="internal_helper",
                     return_type="int", visibility=Visibility.HIDDEN),
        ],
        variables=[
            Variable(name="plugin_table", mangled="plugin_table", type="void*",
                     visibility=Visibility.PUBLIC),
            Variable(name="internal_state", mangled="internal_state", type="int",
                     visibility=Visibility.HIDDEN),
        ],
    )
    names = _snapshot_export_names(snap)
    assert {"plugin_init", "legacy_entry", "plugin_table"} <= names
    assert "internal_helper" not in names
    assert "internal_state" not in names


def _elf_only_lib(version: str, symbols: list[str]) -> AbiSnapshot:
    """A symbols-only snapshot, as produced by dumping a stripped binary with
    no headers/DWARF: every export is Visibility.ELF_ONLY."""
    fns = [
        Function(name=s, mangled=s, return_type="int", visibility=Visibility.ELF_ONLY)
        for s in symbols
    ]
    return AbiSnapshot(
        library="libplugin.so", version=version, functions=fns, elf_only_mode=True,
    )


def test_host_contract_check_counts_elf_only_exports() -> None:
    """plugin-check on real stripped binaries (no headers) sees ELF_ONLY
    exports; those must count as satisfying the contract (regression: a
    compatible binary-only plugin previously came out BREAKING / 0% coverage)."""
    plugin_v1 = _elf_only_lib("1.0", ["plugin_init", "plugin_run"])
    plugin_v2 = _elf_only_lib("2.0", ["plugin_init", "plugin_run"])

    ok = check_plugin_host_contract(plugin_v1, plugin_v2, HOST_REQUIRED_ENTRYPOINTS)
    assert ok.verdict is Verdict.COMPATIBLE
    assert ok.missing_entrypoints == []
    assert ok.coverage == 100.0

    # Dropping a required entrypoint is still BREAKING in symbols-only mode.
    plugin_v3 = _elf_only_lib("3.0", ["plugin_init"])
    broke = check_plugin_host_contract(plugin_v1, plugin_v3, HOST_REQUIRED_ENTRYPOINTS)
    assert broke.verdict is Verdict.BREAKING
    assert broke.missing_entrypoints == ["plugin_run"]


# ── Scenario D: policy-scoped release decision ───────────────────────────────


def _enum(member_name: str) -> EnumType:
    return EnumType(
        name="Mode",
        members=[
            EnumMember(name="MODE_A", value=0),
            EnumMember(name=member_name, value=1),
        ],
        underlying_type="int",
    )


def test_enum_rename_recommendation_follows_policy() -> None:
    """The recommender is policy-aware: an enum-member rename is a MAJOR source
    break under strict_abi but a PATCH under sdk_vendor (which downgrades
    source-only renames)."""
    old = _lib("1.0", ["use_mode"], enums=[_enum("MODE_B")])
    new = _lib("2.0", ["use_mode"], enums=[_enum("MODE_RENAMED")])

    # Mode isn't referenced by a public function in this synthetic fixture, so
    # opt out of default public-header scoping (ADR-024 Phase 5) to exercise the
    # policy-aware recommender on the enum rename itself.
    strict = compare(old, new, policy="strict_abi", scope_to_public_surface=False)
    assert strict.verdict is Verdict.API_BREAK
    assert recommend_release(strict).bump is SemverBump.MAJOR

    vendor = compare(old, new, policy="sdk_vendor", scope_to_public_surface=False)
    assert vendor.verdict is Verdict.COMPATIBLE
    rec = recommend_release(vendor)
    assert rec.bump is not SemverBump.MAJOR
    assert rec.soname is SonameAction.NO_BUMP_NEEDED
