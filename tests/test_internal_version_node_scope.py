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

"""Internal/private ELF version-node symbols are not public ABI.

Symbols a library binds to a ``*_INTERNAL_*`` / ``*PRIVATE*`` version node
(glibc ``GLIBC_PRIVATE``, nettle ``HOGWEED_INTERNAL_6_1``) are exported but not
public ABI. A real change to one is a deployment risk, not a break — exactly the
real-world divergence root-caused as parity class A (nettle 3.6→3.7) in
``validation/realworld-tracker-parity-2026-06.md``.

These tests cover the pure classifier helpers in :mod:`abicheck.diff_versioning`
and the end-to-end demotion through :func:`abicheck.checker.compare`.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.diff_versioning import (
    demote_internal_version_node_findings,
    internal_versioned_symbols,
    is_internal_version_node,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_is_internal_version_node_recognises_internal_and_private_markers() -> None:
    assert is_internal_version_node("HOGWEED_INTERNAL_6_1")
    assert is_internal_version_node("NETTLE_INTERNAL_8_1")
    assert is_internal_version_node("GLIBC_PRIVATE")
    # public version nodes are not internal
    assert not is_internal_version_node("HOGWEED_6")
    assert not is_internal_version_node("GLIBC_2.34")
    assert not is_internal_version_node("")


def test_internal_versioned_symbols_collects_only_internal_bindings() -> None:
    elf = ElfMetadata(
        symbols=[
            ElfSymbol(name="_nettle_ecc_mod", version="HOGWEED_INTERNAL_6_1"),
            ElfSymbol(name="nettle_sha256_init", version="NETTLE_8"),
            ElfSymbol(name="_priv_blob", version="FOO_PRIVATE"),
            ElfSymbol(name="public_api", version=""),
            # nameless symbol on an internal node must be skipped, not added as ""
            ElfSymbol(name="", version="FOO_INTERNAL_1"),
            # mixed alias: same name on BOTH a public and an internal node must
            # stay public — a real break to foo@LIBFOO_1.0 cannot be demoted.
            ElfSymbol(name="foo", version="LIBFOO_1.0"),
            ElfSymbol(name="foo", version="LIBFOO_PRIVATE"),
            # internal node + unversioned default export -> public surface present
            ElfSymbol(name="bar", version="BAR_INTERNAL_1"),
            ElfSymbol(name="bar", version=""),
        ]
    )
    assert internal_versioned_symbols(elf) == {"_nettle_ecc_mod", "_priv_blob"}


# --------------------------------------------------------------------------- #
# demote_internal_version_node_findings (unit)
# --------------------------------------------------------------------------- #
def _change(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description=symbol)


def test_demote_reclassifies_breaking_findings_on_internal_symbols() -> None:
    old_elf = ElfMetadata(
        symbols=[ElfSymbol(name="_nettle_ecc_mod", version="HOGWEED_INTERNAL_6_0")]
    )
    new_elf = ElfMetadata(symbols=[])
    changes = [
        _change(ChangeKind.FUNC_PARAMS_CHANGED, "_nettle_ecc_mod"),
        _change(ChangeKind.SYMBOL_VERSION_NODE_REMOVED, "HOGWEED_INTERNAL_6_0"),
    ]
    demote_internal_version_node_findings(changes, old_elf, new_elf)
    for c in changes:
        assert c.effective_verdict == Verdict.COMPATIBLE_WITH_RISK
        assert c.modulation_rule == "internal_version_node_scope"


def test_demote_leaves_public_symbol_findings_untouched() -> None:
    old_elf = ElfMetadata(symbols=[ElfSymbol(name="public_fn", version="LIBFOO_1.0")])
    new_elf = ElfMetadata(symbols=[])
    change = _change(ChangeKind.FUNC_REMOVED, "public_fn")
    demote_internal_version_node_findings([change], old_elf, new_elf)
    assert change.effective_verdict is None


def test_demote_requires_old_side_internal_binding() -> None:
    # A symbol that was PUBLIC in the old SONAME but is rebound to an internal
    # node in the new binary must NOT be demoted: old consumers still linked
    # foo@LIBFOO_1.0 and a real change to it breaks them (Codex review #354).
    old_elf = ElfMetadata(symbols=[ElfSymbol(name="foo", version="LIBFOO_1.0")])
    new_elf = ElfMetadata(symbols=[ElfSymbol(name="foo", version="LIBFOO_PRIVATE")])
    change = _change(ChangeKind.FUNC_PARAMS_CHANGED, "foo")
    demote_internal_version_node_findings([change], old_elf, new_elf)
    assert change.effective_verdict is None


def test_demote_never_escalates_a_compatible_finding() -> None:
    # A compatible-kind finding on an internal symbol must stay untouched (the
    # demotion only ever downgrades a break, never escalates).
    old_elf = ElfMetadata(symbols=[ElfSymbol(name="_priv", version="FOO_INTERNAL_1")])
    change = _change(ChangeKind.FUNC_ADDED, "_priv")
    demote_internal_version_node_findings([change], old_elf, ElfMetadata())
    assert change.effective_verdict is None


def test_demote_respects_frozen_namespace_violation() -> None:
    old_elf = ElfMetadata(symbols=[ElfSymbol(name="_priv", version="FOO_INTERNAL_1")])
    change = _change(ChangeKind.FUNC_REMOVED, "_priv")
    change.frozen_namespace_violation = "**::detail"
    demote_internal_version_node_findings([change], old_elf, ElfMetadata())
    assert change.effective_verdict is None


def test_demote_leaves_a_prior_effective_verdict_override_untouched() -> None:
    # A finding already carrying an effective_verdict (e.g. from an earlier
    # modulation pass) must not be overwritten by the internal-node demotion.
    old_elf = ElfMetadata(symbols=[ElfSymbol(name="_priv", version="FOO_INTERNAL_1")])
    change = _change(ChangeKind.FUNC_REMOVED, "_priv")
    change.effective_verdict = Verdict.BREAKING
    change.modulation_rule = "some_other_rule"
    demote_internal_version_node_findings([change], old_elf, ElfMetadata())
    assert change.effective_verdict == Verdict.BREAKING
    assert change.modulation_rule == "some_other_rule"


# --------------------------------------------------------------------------- #
# End-to-end through compare()
# --------------------------------------------------------------------------- #
def _snapshot(name: str, funcs: list[str], elf_syms: list[ElfSymbol]) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version=name,
        functions=[
            Function(name=f, mangled=f, return_type="?", visibility=Visibility.ELF_ONLY)
            for f in funcs
        ],
        elf_only_mode=True,
        platform="elf",
        language_profile="cpp",
    )
    snap.elf = ElfMetadata(symbols=elf_syms)
    return snap


def test_compare_demotes_internal_versioned_symbol_removal() -> None:
    # An internal-version-node function removed: real change, but not public ABI.
    old = _snapshot(
        "old",
        ["_nettle_cnd_swap"],
        [ElfSymbol(name="_nettle_cnd_swap", version="HOGWEED_INTERNAL_6_0")],
    )
    new = _snapshot("new", [], [])
    result = compare(old, new)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK, (
        f"internal-version-node removal must demote to risk, got {result.verdict}"
    )
    removed = [c for c in result.changes if c.symbol == "_nettle_cnd_swap"]
    assert removed and all(
        c.effective_verdict == Verdict.COMPATIBLE_WITH_RISK for c in removed
    )


def test_compare_public_versioned_symbol_removal_stays_breaking() -> None:
    # Same shape, but the symbol is on a *public* version node -> real break.
    old = _snapshot(
        "old",
        ["nettle_pubfn"],
        [ElfSymbol(name="nettle_pubfn", version="NETTLE_8")],
    )
    new = _snapshot("new", [], [])
    result = compare(old, new)
    assert result.verdict == Verdict.BREAKING, (
        f"a public symbol removal must stay breaking, got {result.verdict}"
    )


def test_internal_symbol_removal_does_not_recommend_soname_bump() -> None:
    # A demoted internal-version-node break must NOT trigger SONAME_BUMP_RECOMMENDED:
    # the SONAME-bump policy has to honor the effective (demoted) verdict, not the
    # raw kind, or it claims a binary-incompatible change the verdict denies.
    old = _snapshot(
        "old",
        ["_nettle_cnd_swap"],
        [ElfSymbol(name="_nettle_cnd_swap", version="HOGWEED_INTERNAL_6_0")],
    )
    old.elf.soname = "libfoo.so.1"
    new = _snapshot("new", [], [])
    new.elf.soname = "libfoo.so.1"
    result = compare(old, new)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
    assert not any(
        c.kind == ChangeKind.SONAME_BUMP_RECOMMENDED for c in result.changes
    ), "demoted internal-only change must not recommend a SONAME bump"


def test_public_symbol_removal_still_recommends_soname_bump() -> None:
    # Guard: a genuine public break under an unchanged SONAME still recommends the bump.
    old = _snapshot(
        "old",
        ["nettle_pubfn"],
        [ElfSymbol(name="nettle_pubfn", version="NETTLE_8")],
    )
    old.elf.soname = "libfoo.so.1"
    new = _snapshot("new", [], [])
    new.elf.soname = "libfoo.so.1"
    result = compare(old, new)
    assert result.verdict == Verdict.BREAKING
    assert any(c.kind == ChangeKind.SONAME_BUMP_RECOMMENDED for c in result.changes)
