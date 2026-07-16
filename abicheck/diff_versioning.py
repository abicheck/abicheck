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

from .binary_utils import strip_vendor_hash
from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind, Verdict
from .checker_types import Change
from .diff_helpers import make_change
from .elf_metadata import ElfMetadata

# Tokens that mark an ELF symbol-version node as implementation-internal rather
# than public ABI. This is a widespread upstream convention: implementation-only
# exports are bound to a version node whose name carries one of these markers —
# glibc's ``GLIBC_PRIVATE``, nettle's ``NETTLE_INTERNAL_8_1`` /
# ``HOGWEED_INTERNAL_6_1``. Symbols on such a node are dynamically exported but
# are *not* part of the public ABI contract, so changes confined to them are a
# deployment risk (a consumer who illegally linked them rebuilds), not a break.
_INTERNAL_VERSION_NODE_TOKENS = ("PRIVATE", "INTERNAL")

_UNPARSEABLE_VERSION: tuple[int, ...] = (2**31,)
_MAX_VERSION_COMPONENT_DIGITS = 9
"""Sentinel returned by :func:`_parse_abi_version_tag` for non-numeric tags
like ``GLIBC_PRIVATE``.  Sorts *above* any real version so that a new
non-numeric requirement is always treated as potentially BREAKING — never
silently COMPAT."""


def _parse_dotted_numeric_version(text: str) -> tuple[int, ...] | None:
    """Parse a dotted numeric version safely, or return ``None``.

    Version tags and declared runtime floors can come from untrusted ELF
    metadata or snapshots.  Keep integer conversion bounded so pathological
    digit strings are treated like malformed versions rather than aborting the
    comparison via Python's integer-conversion guard (or burning CPU/memory on
    runtimes without one).
    """
    parts = text.split(".")
    if not parts:
        return None
    parsed: list[int] = []
    for part in parts:
        if (
            not part
            or not part.isascii()
            or not part.isdigit()
            or len(part) > _MAX_VERSION_COMPONENT_DIGITS
        ):
            return None
        parsed.append(int(part))
    return tuple(parsed) if parsed else None


def _parse_abi_version_tag(ver: str) -> tuple[int, ...]:
    """Parse a versioned symbol tag like ``GLIBC_2.34`` or ``GLIBCXX_3.4.19``
    into a comparable integer tuple.

    Only the numeric suffix after the last ``_`` is used:
    ``GLIBC_2.34`` → ``(2, 34)``, ``GLIBCXX_3.4.19`` → ``(3, 4, 19)``.

    Returns :data:`_UNPARSEABLE_VERSION` for non-numeric or malformed tags such
    as ``GLIBC_PRIVATE`` — a very large sentinel that always compares as newer
    than any real version, so such tags are conservatively treated as BREAKING.

    Canonical home: previously lived in ``diff_platform_elf_symbols``; moved
    here so both the per-node diff and the runtime-floor contract share it
    without an import cycle (that module imports this one).
    """
    parts = ver.rsplit("_", 1)
    numeric = parts[-1] if len(parts) > 1 else ver
    result = _parse_dotted_numeric_version(numeric)
    return result if result is not None else _UNPARSEABLE_VERSION

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


#: Change kinds the declared-runtime-floor contract (ADR-020b
#: ``runtime_floors``) can reclassify, and where each carries the offending
#: version tag: SYMBOL_VERSION_REQUIRED_ADDED in ``symbol`` (e.g.
#: ``GLIBC_2.34``), RUNTIME_FLOOR_RAISED in ``new_value``.
#: DT_RELR_INTRODUCED carries an *implied* requirement: a DT_RELR binary
#: needs glibc ≥ 2.36 to load, so a declared GLIBC floor decides it too.
_RUNTIME_FLOOR_KINDS = frozenset(
    {
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        ChangeKind.RUNTIME_FLOOR_RAISED,
        ChangeKind.DT_RELR_INTRODUCED,
    }
)

#: The glibc release that introduced DT_RELR loader support.
_DT_RELR_GLIBC_FLOOR_TAG = "GLIBC_2.36"


def _floor_required_tag(change: Change) -> str:
    """The version tag a floor-modulatable finding requires (or "")."""
    if change.kind is ChangeKind.RUNTIME_FLOOR_RAISED:
        return change.new_value or ""
    if change.kind is ChangeKind.DT_RELR_INTRODUCED:
        return _DT_RELR_GLIBC_FLOOR_TAG
    return change.symbol or ""


def apply_runtime_floor_contract(
    changes: list[Change], runtime_floors: dict[str, str]
) -> list[Change]:
    """Classify version-requirement findings against declared runtime floors.

    Without a declared deployment target, a new symbol-version requirement
    (``SYMBOL_VERSION_REQUIRED_ADDED`` / ``RUNTIME_FLOOR_RAISED``) can only be
    a *risk* — whether it breaks anyone depends on runtimes the tool cannot
    see. ``runtime_floors`` (EnvironmentMatrix, e.g. ``{"GLIBC": "2.28"}``)
    turns that into a checkable contract:

    * required version ≤ declared floor → ``COMPATIBLE`` (every declared
      target already ships it);
    * required version > declared floor → ``BREAKING`` (a declared target can
      no longer load the binary);
    * prefix not declared, or an unparseable tag → left at the kind's default
      (RISK).

    Applied via the per-finding ``effective_verdict`` hook (ADR-025), like
    :func:`demote_internal_version_node_findings`; findings already carrying a
    modulation are left untouched. Mutates and returns *changes*.
    """
    if not runtime_floors:
        return changes
    floors = {k.upper(): v for k, v in runtime_floors.items()}
    for change in changes:
        if change.kind not in _RUNTIME_FLOOR_KINDS:
            continue
        if change.effective_verdict is not None:
            continue
        tag = _floor_required_tag(change)
        if "_" not in tag:
            continue
        prefix = tag.rsplit("_", 1)[0].upper()
        floor = floors.get(prefix)
        if floor is None:
            continue
        required = _parse_abi_version_tag(tag)
        # Every dot component of the floor must be purely numeric. YAML
        # loading (EnvironmentMatrix.from_dict) already enforces this, but a
        # caller can hand a prebuilt dict straight to compare(); truncating a
        # "2.28-1" to (2,) here would silently flip verdicts, so a malformed
        # floor leaves the finding at its default instead (Codex review #510).
        floor_tuple = _parse_dotted_numeric_version(floor)
        if floor_tuple is None:
            continue
        if required == _UNPARSEABLE_VERSION:
            continue
        if required <= floor_tuple:
            change.effective_verdict = Verdict.COMPATIBLE
            change.modulation_reason = (
                f"within declared runtime floor ({prefix} ≥ {floor}): every "
                f"declared deployment target already provides {tag}"
            )
        else:
            change.effective_verdict = Verdict.BREAKING
            change.modulation_reason = (
                f"exceeds declared runtime floor ({prefix} {floor}): declared "
                f"deployment targets cannot load a binary requiring {tag}"
            )
        change.modulation_rule = "runtime_floor_contract"
    return changes


def check_platform_baseline_floor(
    elf: ElfMetadata, runtime_floors: dict[str, str] | None
) -> list[Change]:
    """Check a binary's own required GLIBC floor against a declared
    platform-baseline promise (e.g. a manylinux wheel tag) (G10).

    A manylinux tag (``manylinux_2_27``, …) is a promise about the *maximum*
    glibc symbol version a wheel's binaries may require. Unlike
    :func:`apply_runtime_floor_contract`, which only reclassifies a
    version-*requirement-change* finding between two snapshots, this fires on
    a single artifact's own requirement regardless of whether it moved
    relative to an old snapshot — the case a manylinux tag actually needs
    guarded against: a binary that has *always* required ``GLIBC_2.34`` while
    shipped under a ``manylinux_2_27`` tag is broken on day one, with no
    old→new delta for a diff to key on. This is the classic "works on my box,
    `GLIBC_2.x not found` on the user's older system" failure going
    undetected.

    *runtime_floors* is the same ``{prefix: "X.Y"}`` mapping consumed by
    :func:`apply_runtime_floor_contract` (ADR-020b ``EnvironmentMatrix`` /
    ``--env-matrix``) — keys are matched case-insensitively (normalized to
    upper), same as that function, since a direct API caller can construct
    ``EnvironmentMatrix(runtime_floors={"glibc": ...})`` bypassing
    ``from_dict``'s uppercasing. Only the ``GLIBC`` entry is read here; other
    prefixes have no platform-tag concept yet. Returns ``[]`` when no
    ``GLIBC`` floor is declared, the floor is malformed, or the binary's own
    requirement is at or below it.

    A binary built with packed relative relocations (``DT_RELR``) implicitly
    requires glibc >= 2.36 to load even when no ``GLIBC_ABI_DT_RELR``-tagged
    symbol version happens to appear in ``versions_required`` — the same
    implied floor :func:`apply_runtime_floor_contract` folds in for the delta
    case via ``_DT_RELR_GLIBC_FLOOR_TAG``, so it is folded in here too.
    """
    if not runtime_floors:
        return []
    floor_raw = {k.upper(): v for k, v in runtime_floors.items()}.get("GLIBC")
    if not floor_raw:
        return []
    floor_tuple = _parse_dotted_numeric_version(floor_raw)
    if floor_tuple is None:
        return []
    best: tuple[int, ...] = (0,)
    best_tag = ""
    providers: set[str] = set()
    for lib, tags in (getattr(elf, "versions_required", None) or {}).items():
        for tag in tags:
            if not tag.startswith("GLIBC_"):
                continue
            parsed = _parse_abi_version_tag(tag)
            if parsed == _UNPARSEABLE_VERSION:
                continue
            if parsed > best:
                best, best_tag = parsed, tag
            if parsed > floor_tuple:
                providers.add(lib)
    if getattr(elf, "has_dt_relr", False):
        relr_tuple = _parse_abi_version_tag(_DT_RELR_GLIBC_FLOOR_TAG)
        if relr_tuple > best:
            best, best_tag = relr_tuple, _DT_RELR_GLIBC_FLOOR_TAG
        if relr_tuple > floor_tuple:
            providers.add(getattr(elf, "soname", "") or "<binary>")
    if best == (0,) or best <= floor_tuple:
        return []
    return [
        make_change(
            ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED,
            symbol="<platform-baseline>",
            name=", ".join(sorted(providers)) or "(no provider evidence captured)",
            detail="GLIBC",
            old=f"GLIBC_{floor_raw}",
            new=best_tag,
        )
    ]


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
            make_change(
                ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
                symbol=node,
                name=node,
                detail=f"{sample}{suffix}",
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
                make_change(
                    ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                    symbol=sym_name,
                    name=sym_name,
                    old=old_node,
                    new=new_node,
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
        make_change(
            ChangeKind.VERSION_SCRIPT_MISSING,
            symbol="<version-script>",
            detail=str(len(new_elf.symbols)),
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

    # NOTE: a *collapsed* versioned-symbol scheme (opt-in preset) drops its
    # rename churn from `changes`, so `has_breaking` reads False even though the
    # SONAME bump is justified (the symbols were renamed). That case is handled
    # one layer up in `checker._apply_soname_policy`, which strips a
    # SONAME_BUMP_UNNECESSARY emitted here when
    # `PipelineContext.versioned_scheme_soname_relink_required` is set — a signal
    # that survives the advisory being suppressed. Keeping it there means this
    # pure policy needs no knowledge of the collapse preset.

    # A SONAME is considered "bumped" only when both old and new have a
    # non-empty SONAME and they differ.  If the new SONAME is empty the
    # library *dropped* its SONAME — that is not a bump. Compared on the
    # vendor-hash-stripped spelling so a hash-only wheel rebuild (auditwheel/
    # delocate rewrite the SONAME to match the content-hashed filename on
    # every build) is not mistaken for a deliberate SONAME bump — see
    # ``diff_platform_elf_dynamic._diff_elf_dynamic_section``.
    both_have_soname = bool(old_elf.soname) and bool(new_elf.soname)
    soname_bumped = both_have_soname and strip_vendor_hash(
        old_elf.soname
    ) != strip_vendor_hash(new_elf.soname)

    result: list[Change] = []

    if has_breaking and not soname_bumped and old_elf.soname:
        breaking_count = sum(1 for c in changes if _is_effectively_breaking(c))
        if new_elf.soname:
            detail = f"SONAME remains {old_elf.soname!r}"
        else:
            detail = f"SONAME was dropped (was {old_elf.soname!r})"
        result.append(
            make_change(
                ChangeKind.SONAME_BUMP_RECOMMENDED,
                symbol="DT_SONAME",
                name=str(breaking_count),
                detail=detail,
                old=repr(old_elf.soname),
                old_value=old_elf.soname,
                new_value=new_elf.soname,
            )
        )

    if not has_breaking and soname_bumped:
        result.append(
            make_change(
                ChangeKind.SONAME_BUMP_UNNECESSARY,
                symbol="DT_SONAME",
                old=repr(old_elf.soname),
                new=repr(new_elf.soname),
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
