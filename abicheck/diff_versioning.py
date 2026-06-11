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

"""ELF symbol-version policy checks.

Extends the existing L0 detector pattern (ADR-011) with version-node graph
diffing, SONAME bump recommendations, and version-script-missing detection.
"""

from __future__ import annotations

from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind, Verdict
from .checker_types import Change
from .elf_metadata import ElfMetadata

# Tokens that mark an ELF symbol-version node as implementation-internal rather
# than public ABI. This is a widespread upstream convention: implementation-only
# exports are bound to a version node whose name carries one of these markers —
# glibc's ``GLIBC_PRIVATE``, nettle's ``NETTLE_INTERNAL_8_1`` /
# ``HOGWEED_INTERNAL_6_1``. Symbols on such a node are dynamically exported but
# are *not* part of the public ABI contract, so changes confined to them are a
# deployment risk (a consumer who illegally linked them rebuilds), not a break.
_INTERNAL_VERSION_NODE_TOKENS = ("PRIVATE", "INTERNAL")

# Change kinds whose ``symbol`` field is itself a version-node name (not a
# symbol name) — for these, the node-name marker test applies directly.
_VERSION_NODE_NAME_KINDS = frozenset(
    {
        ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
        ChangeKind.SYMBOL_MOVED_VERSION_NODE,
        ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
        ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
    }
)


def is_internal_version_node(version: str) -> bool:
    """True if an ELF version-node name marks it implementation-internal/private.

    Matches the ``GLIBC_PRIVATE`` / ``*_INTERNAL_*`` convention (see
    :data:`_INTERNAL_VERSION_NODE_TOKENS`). The check is on the *version-node*
    name only — never an arbitrary symbol name — so a public function that merely
    has ``internal`` in its identifier is unaffected.
    """
    upper = (version or "").upper()
    return any(token in upper for token in _INTERNAL_VERSION_NODE_TOKENS)


def internal_versioned_symbols(elf: ElfMetadata) -> set[str]:
    """Names whose **every** exported binding is on an internal/private node.

    A name is returned only when it has at least one internal/private version
    binding and **no** public binding — neither a public version node nor an
    unversioned (default) export. If the same name is also exported on a public
    node (``foo@LIBFOO_1.0`` alongside ``foo@LIBFOO_PRIVATE``), it stays public so
    a real break to the public alias is never demoted (Codex review #354).
    """
    public: set[str] = set()
    internal: set[str] = set()
    for sym in getattr(elf, "symbols", []) or []:
        name = getattr(sym, "name", "")
        if not name:
            continue
        ver = getattr(sym, "version", "") or ""
        if ver and is_internal_version_node(ver):
            internal.add(name)
        else:
            # An unversioned (default) export or a public version node means the
            # name is part of the public surface.
            public.add(name)
    return internal - public


def demote_internal_version_node_findings(
    changes: list[Change], old_elf: ElfMetadata, new_elf: ElfMetadata
) -> list[Change]:
    """Demote breaking findings confined to internal/private version-node symbols.

    A symbol the library author bound to a ``*_INTERNAL_*`` / ``*PRIVATE*`` ELF
    version node is exported but is not public ABI (the
    ``abi-compliance-checker`` header-scoped tracker correctly ignores it; see
    ``validation/realworld-tracker-parity-2026-06.md`` class A — nettle 3.6→3.7).
    abicheck's binary-strict default would otherwise score a real change to such a
    symbol (removal, signature change, internal data-table resize, or the rename
    of the internal node itself) as ``BREAKING``.

    This reclassifies each such finding to ``COMPATIBLE_WITH_RISK`` via the
    per-finding ``effective_verdict`` modulation hook (ADR-025) — binary-compatible
    for conforming consumers, a deployment risk only for anyone who illegally
    linked an internal symbol, exactly the ``GLIBC_PRIVATE`` semantics. It is
    deliberately conservative:

    * only findings whose *kind* is already BREAKING/API_BREAK are touched (it
      never escalates a compatible finding);
    * the per-symbol set is derived from the **old** side's actual ELF version
      bindings — that is the surface old consumers linked against. A symbol that
      was *public* in the old SONAME but rebound to an internal node in the new
      binary (``foo@LIBFOO_1.0`` → ``foo@LIBFOO_PRIVATE``) is **not** demoted: old
      consumers still require ``foo@LIBFOO_1.0`` and a real change to it breaks
      them (Codex review #354). A public function whose name merely contains
      ``internal`` is likewise never matched (the test is on the version node);
    * findings already carrying a ``frozen_namespace_violation`` or a prior
      ``effective_verdict`` override are left untouched.

    ``new_elf`` is accepted for symmetry/future use but intentionally does not
    widen the internal set — see the old-side rationale above.

    Mutates and returns ``changes``.
    """
    del new_elf  # old-side bindings define public-ness for old consumers
    internal = internal_versioned_symbols(old_elf)
    for change in changes:
        if change.frozen_namespace_violation is not None:
            continue
        if change.effective_verdict is not None:
            continue
        if change.kind not in BREAKING_KINDS and change.kind not in API_BREAK_KINDS:
            continue
        symbol = change.symbol or ""
        on_internal_node = symbol in internal or (
            change.kind in _VERSION_NODE_NAME_KINDS and is_internal_version_node(symbol)
        )
        if not on_internal_node:
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_reason = (
            "symbol bound to an internal/private ELF version node (not public ABI)"
        )
        change.modulation_rule = "internal_version_node_scope"
    return changes


def _is_unattached_private_version_node(elf: ElfMetadata, version: str) -> bool:
    """Return True for private version-script marker nodes with no exports.

    A version definition whose name contains ``PRIVATE`` and which no exported
    symbol is bound to is a linker bookkeeping marker, not a real ABI version
    node. Such markers are ignored as removals and must not count toward
    "the old library had a version script".
    """
    if "PRIVATE" not in version.upper():
        return False
    return not any(
        getattr(sym, "version", "") == version for sym in getattr(elf, "symbols", [])
    )


def detect_version_node_changes(
    old_elf: ElfMetadata,
    new_elf: ElfMetadata,
) -> list[Change]:
    """Compare ELF symbol version definition graphs.

    Builds a version_node → set[symbol_name] mapping for both old and new,
    then detects:
      - Removed version nodes (all symbols in that node gone)
      - Symbols migrated between version nodes
      - New version nodes added (informational, no change emitted here —
        already covered by SYMBOL_VERSION_DEFINED_ADDED)
    """
    old_node_syms = _build_version_node_map(old_elf)
    new_node_syms = _build_version_node_map(new_elf)

    changes: list[Change] = []

    # Detect removed version nodes (node existed in old, gone in new)
    for node in sorted(set(old_node_syms) - set(new_node_syms)):
        sym_names = sorted(old_node_syms[node])
        sample = ", ".join(sym_names[:5])
        suffix = f" (+{len(sym_names) - 5} more)" if len(sym_names) > 5 else ""
        changes.append(
            Change(
                kind=ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
                symbol=node,
                description=(
                    f"Version node {node} was entirely removed from the version script. "
                    f"Symbols previously under this node: {sample}{suffix}. "
                    f"Applications linked against {node} will get unresolved symbol errors."
                ),
                old_value=node,
            )
        )

    # Detect symbols that moved between version nodes
    old_sym_to_node = _build_sym_to_node_map(old_node_syms)
    new_sym_to_node = _build_sym_to_node_map(new_node_syms)

    for sym_name in sorted(set(old_sym_to_node) & set(new_sym_to_node)):
        old_node = old_sym_to_node[sym_name]
        new_node = new_sym_to_node[sym_name]
        if old_node != new_node:
            changes.append(
                Change(
                    kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                    symbol=sym_name,
                    description=(
                        f"Symbol {sym_name} moved from version node {old_node} to "
                        f"{new_node}. Applications linked against {old_node} will not "
                        f"find this symbol at the expected version. This is typically "
                        f"intentional during a major release."
                    ),
                    old_value=old_node,
                    new_value=new_node,
                )
            )

    return changes


def detect_version_script_missing(
    old_elf: ElfMetadata,
    new_elf: ElfMetadata,
) -> list[Change]:
    """Check whether the new library exports symbols without a version script.

    Only the new library is checked — warning about the old library is not
    actionable in a diff tool.  Emits VERSION_SCRIPT_MISSING when:
      - The new library has exported symbols
      - None of them carry a version tag
      - No version definitions exist
      - The old library *did* have a version script (i.e., the version script
        was dropped or the library is new).  If neither old nor new has a
        version script, this is a pre-existing condition, not a new change —
        suppressing it avoids false verdict escalation on NO_CHANGE cases.
    """
    if not new_elf.symbols:
        return []
    if new_elf.versions_defined:
        return []
    if any(s.version for s in new_elf.symbols):
        return []
    # If the old library also lacks a version script, this is a pre-existing
    # condition — not a new change.  Only warn when a version script was
    # dropped or when comparing a brand-new library (old has no symbols).
    #
    # Unattached private version-script markers (e.g. ``FOO_PRIVATE`` with no
    # old exported symbol bound to that node) do not constitute a real version
    # script: they are deliberately ignored as version-node removals elsewhere,
    # so they must not count as "old had a version script" here either —
    # otherwise dropping a marker-only script re-introduces VERSION_SCRIPT_MISSING.
    old_real_versions_defined = [
        ver
        for ver in old_elf.versions_defined
        if not _is_unattached_private_version_node(old_elf, ver)
    ]
    old_had_version_script = bool(old_real_versions_defined) or any(
        s.version for s in old_elf.symbols
    )
    if not old_had_version_script and old_elf.symbols:
        return []
    return [
        Change(
            kind=ChangeKind.VERSION_SCRIPT_MISSING,
            symbol="<version-script>",
            description=(
                f"Library exports {len(new_elf.symbols)} symbol(s) without "
                f"a version script. This is a common oversight that prevents "
                f"fine-grained symbol versioning and makes future ABI evolution "
                f"harder to manage. Consider adding a version script "
                f"(--version-script=libfoo.map)."
            ),
        )
    ]


def check_soname_bump_policy(
    changes: list[Change],
    old_elf: ElfMetadata,
    new_elf: ElfMetadata,
) -> list[Change]:
    """Check whether SONAME bump is appropriate given detected changes.

    This is a post-detection check that runs after all detectors, since it
    needs the full change list to make its recommendation.

    Rules:
      - Breaking changes detected but SONAME not bumped → SONAME_BUMP_RECOMMENDED
      - No breaking changes but SONAME bumped → SONAME_BUMP_UNNECESSARY
    """
    breaking_kinds = BREAKING_KINDS

    def _is_effectively_breaking(c: Change) -> bool:
        # Honor a per-finding ``effective_verdict`` override (ADR-025): a change
        # demoted to COMPATIBLE_WITH_RISK — e.g. one confined to an internal/
        # private version-node symbol — must not count as a break here, or it
        # would trigger the very SONAME-bump advisory this policy aims to avoid.
        if c.effective_verdict is not None:
            return c.effective_verdict == Verdict.BREAKING
        return c.kind in breaking_kinds

    has_breaking = any(_is_effectively_breaking(c) for c in changes)

    # A SONAME is considered "bumped" only when both old and new have a
    # non-empty SONAME and they differ.  If the new SONAME is empty the
    # library *dropped* its SONAME — that is not a bump.
    both_have_soname = bool(old_elf.soname) and bool(new_elf.soname)
    soname_bumped = both_have_soname and old_elf.soname != new_elf.soname

    result: list[Change] = []

    if has_breaking and not soname_bumped and old_elf.soname:
        breaking_count = sum(1 for c in changes if _is_effectively_breaking(c))
        if new_elf.soname:
            detail = f"SONAME remains {old_elf.soname!r}"
        else:
            detail = f"SONAME was dropped (was {old_elf.soname!r})"
        result.append(
            Change(
                kind=ChangeKind.SONAME_BUMP_RECOMMENDED,
                symbol="DT_SONAME",
                description=(
                    f"{breaking_count} binary-incompatible change(s) detected but "
                    f"{detail}. Consumers linked against "
                    f"{old_elf.soname!r} will encounter runtime failures. "
                    f"Recommended: bump SONAME to signal the ABI break."
                ),
                old_value=old_elf.soname,
                new_value=new_elf.soname,
            )
        )

    if not has_breaking and soname_bumped:
        result.append(
            Change(
                kind=ChangeKind.SONAME_BUMP_UNNECESSARY,
                symbol="DT_SONAME",
                description=(
                    f"SONAME changed from {old_elf.soname!r} to {new_elf.soname!r} "
                    f"but no binary-incompatible changes were detected. This forces "
                    f"all consumers to relink unnecessarily. Consider whether the "
                    f"bump was intentional."
                ),
                old_value=old_elf.soname,
                new_value=new_elf.soname,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_version_node_map(elf: ElfMetadata) -> dict[str, set[str]]:
    """Build a mapping from version node name → set of symbol names."""
    node_map: dict[str, set[str]] = {}
    for sym in elf.symbols:
        if sym.version and sym.version in elf.versions_defined:
            node_map.setdefault(sym.version, set()).add(sym.name)
    return node_map


def _build_sym_to_node_map(node_map: dict[str, set[str]]) -> dict[str, str]:
    """Invert node_map: symbol_name → version_node (first wins)."""
    result: dict[str, str] = {}
    for node, syms in node_map.items():
        for sym in syms:
            if sym not in result:
                result[sym] = node
    return result
