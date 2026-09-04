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

"""``check_contracts_comparable``'s profile-fingerprint axis -- split out of
``comparability.py`` (over the file-size soft limit) as its own module.

Checks whether a genuine platform/toolchain-identity difference (word size,
endianness, target triple, ...) corroborates a ``profile_fingerprint``
mismatch between two snapshots, mirroring the same file's
dependency-scope and scope-fingerprint checks for the third of
``check_contracts_comparable``'s three comparability axes. Not a documented
public Python API path, so ``comparability.py`` reaches
:func:`_check_profile_fingerprint_comparable` via a dynamic
``importlib.import_module`` call rather than a static import: this module
needs :class:`~abicheck.comparability.ComparabilityMismatch` and several
private fingerprint-diagnostic helpers back from ``comparability.py``, and a
static two-way import would be the exact
``comparability <-> comparability_profile`` cycle
``scripts/check_ai_readiness.py``'s ``import-cycle-growth`` check rejects --
the same shape ``type_reachability.py``'s own
``type_reachability_stdlib_spellings.py`` split resolved identically.
"""

from __future__ import annotations

from .comparability import (
    _BUILD_CONTEXT_FIELDS,
    _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS,
    _PLATFORM_IDENTITY_FIELDS,
    _PROFILE_FINGERPRINT_KEY_SETS,
    ComparabilityMismatch,
    _binary_platform_components,
    _build_context_corroborated,
    _differing_keys,
    _fingerprint_is_authentic,
    _scope_growth_corroborated,
    _unknown_differing_keys,
)
from .comparability_language_mode import (
    language_standard_content_divergence_corroborated,
    language_standard_probe_upgrade_corroborated,
)
from .comparability_sequences import (
    _HEADER_SEQUENCE_FIELDS,
    _INCLUDE_SEQUENCE_FIELDS,
    _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth,
    _scope_newly_added_headers,
)
from .model import AbiSnapshot, ExtractionContract


def _platform_identity_confirmed(
    old: AbiSnapshot, new: AbiSnapshot, platform_candidate: set[str]
) -> bool:
    """Whether the binaries themselves confirm every candidate platform-identity
    field as a genuine cross-architecture difference.

    Every candidate field must itself map to a binary-derived component present
    on BOTH sides AND genuinely differing on that same field (Codex review, PR
    #624) -- not just "some" component of the platform identity differs
    somewhere. A field with no corresponding binary component on one side (e.g.
    pointer_width/endianness for a PE/Mach-O snapshot, which has no distinct
    word-size/endianness field) can never be confirmed this way, so the
    carve-out correctly declines to waive it.

    ``target_triple`` is the one exception, verified against the FULL axis
    rather than its own single "machine" component (Codex review, PR #624):
    some ELF families share ``e_machine`` across word sizes (e.g. EM_RISCV for
    both RV32 and RV64), so a target_triple change that's really just an
    expression of a genuine word-size change (riscv32-... vs. riscv64-...)
    would otherwise fail verification on its own narrow "machine" component
    even though ``elf_class`` already confirms the architecture genuinely
    differs. target_triple is a coarse, composite descriptor -- unlike
    pointer_width/endianness, which map to one specific, independently-
    meaningful field, it can be corroborated by any genuine difference on this
    axis.
    """
    old_components = _binary_platform_components(old)
    new_components = _binary_platform_components(new)
    if old_components is None or new_components is None:
        return False

    common_keys = old_components.keys() & new_components.keys()
    any_component_differs = any(
        old_components[k] != new_components[k] for k in common_keys
    )

    def _field_verified(field: str) -> bool:
        if field not in old_components or field not in new_components:
            return False
        if field == "target_triple":
            return any_component_differs
        return old_components[field] != new_components[field]

    return all(_field_verified(field) for field in platform_candidate)


def _unexplained_profile_fields(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_contract: ExtractionContract,
    new_contract: ExtractionContract,
    differing: set[str],
) -> set[str]:
    """Narrow ``differing`` down to the fields no carve-out can account for.

    Each carve-out claims and verifies only the subset of ``differing`` it
    actually understands, removing exactly those fields -- carve-outs COMPOSE
    (Codex review, PR #641 follow-up, fourth round): a release combining two
    independently-sanctioned deltas (e.g. a header addition AND a corroborated
    C++-standard raise) must not raise just because neither carve-out's static
    field-set covers ``differing`` in full on its own. Four of the six
    carve-outs' field-sets (:data:`_PLATFORM_IDENTITY_FIELDS`/
    :data:`_BUILD_CONTEXT_FIELDS`/:data:`_HEADER_SEQUENCE_FIELDS`/
    :data:`_INCLUDE_SEQUENCE_FIELDS`) are mutually disjoint, so their relative
    order never matters -- each only ever narrows the working set, never
    re-adds to it. The remaining two,
    :func:`language_standard_probe_upgrade_corroborated` and
    :func:`language_standard_content_divergence_corroborated`, are narrower,
    single-field carve-outs over ``language_standard`` -- a field the
    build-context carve-out's own set already covers -- so both are checked
    *after* the build-context one specifically (its broader waiver, when it
    applies, already subsumes either; when it doesn't, each still gets its
    own independent chance, checked in that order since the upgrade carve-out
    is the more specific of the two).
    """
    old_fields = old_contract.profile_fields
    new_fields = new_contract.profile_fields
    unexplained = set(differing)

    platform_candidate = unexplained & _PLATFORM_IDENTITY_FIELDS
    if platform_candidate and _platform_identity_confirmed(
        old, new, platform_candidate
    ):
        # genuine cross-architecture compare; diff_platform.py handles it
        unexplained -= platform_candidate

    build_candidate = unexplained & _BUILD_CONTEXT_FIELDS
    if build_candidate and _build_context_corroborated(old, new):
        # Build-context carve-out (Codex review, PR #624 follow-up --
        # examples/case98_cxx_standard_floor_raised's real CI failure):
        # a raised C++-standard floor or a build-derived macro delta
        # between two snapshots BOTH actually reconciled against real
        # build-system evidence is exactly the fact
        # CXX_STANDARD_FLOOR_RAISED/ABI_RELEVANT_BUILD_FLAG_CHANGED
        # (diff_build_config.py) exist to surface as a RISK finding --
        # gating it into a generic not_comparable first would only
        # discard that finding instead of letting the more specific
        # detector classify it correctly.
        unexplained -= build_candidate

    # abicheck-internal-bugs finding 2 follow-up (Codex review): waive a
    # language_standard-only mismatch that is fully explained by this PR's
    # own probe having been added by an upgrade -- see
    # language_standard_probe_upgrade_corroborated's own docstring. Checked
    # after the build-context carve-out (whose broader waiver already
    # subsumes this one when both sides carry real build evidence) and
    # narrowly scoped to this single field so it can never mask a genuine
    # compiler_family/compiler_version difference riding alongside it.
    if (
        "language_standard" in unexplained
        and language_standard_probe_upgrade_corroborated(
            old, new, old_fields, new_fields
        )
    ):
        unexplained.discard("language_standard")

    # Real CI failure (Codex review, fresh evidence:
    # examples/case66_language_linkage_changed,
    # examples/case69_trivial_to_nontrivial): a purely content-driven
    # language_standard divergence under an identical, corroborated
    # toolchain -- see language_standard_content_divergence_corroborated's
    # own docstring for why this must never be treated as a blocking
    # extraction-environment mismatch, unlike the narrower upgrade-only
    # carve-out just above. Also discards "compiler_version" when present
    # (real CI failure, second round): under castxml specifically, the
    # mode switch this carve-out corroborates changes which host-compiler
    # binary gets resolved ("gcc" vs "g++"), which by itself makes the
    # *raw* compiler_version profile field differ even though
    # language_standard_content_divergence_corroborated's own,
    # driver-name-normalized comparison already confirmed it's the
    # identical toolchain -- so once that corroboration succeeds,
    # compiler_version's raw-field divergence is explained by the exact
    # same fact language_standard's was, not a second, independent one.
    if (
        "language_standard" in unexplained
        and language_standard_content_divergence_corroborated(
            old, new, old_fields, new_fields
        )
    ):
        unexplained.discard("language_standard")
        unexplained.discard("compiler_version")

    # Both sequence carve-outs below additionally require
    # _scope_growth_corroborated (Codex review, PR #641 follow-up, P1):
    # an additive-shaped header_sequence/include_sequence on its own is
    # not sufficient evidence -- a header already declared identically
    # on both sides as public headers, but fed to the L2 frontend via
    # -H only on the new side, produces the identical additive-growth
    # SHAPE with scope_fingerprint completely UNCHANGED, even though the
    # old snapshot never actually parsed that header's content at all
    # (see _scope_growth_corroborated's own docstring for why that's
    # unsafe to wave through). Requiring a genuinely differing,
    # independently-verified scope-level growth corroborates that the
    # sequence growth reflects real new declared content, not just a
    # same-declared-surface extraction-mechanism difference.
    scope_growth_corroborated = _scope_growth_corroborated(old_contract, new_contract)
    # The specific set of header identities the sequence carve-outs
    # below are allowed to treat an appended/newly-owned entry as
    # corresponding to (Codex review, PR #641 follow-up, ninth P1) --
    # see _scope_newly_added_headers's own docstring for why
    # scope_growth_corroborated alone (proving the scope grew by SOME
    # header) isn't enough; the carve-outs must additionally verify
    # they're waiving growth in the SAME header(s).
    scope_new_headers = _scope_newly_added_headers(
        old_contract.scope_fields.get("headers"),
        new_contract.scope_fields.get("headers"),
    )

    header_seq_candidate = unexplained & _HEADER_SEQUENCE_FIELDS
    if (
        header_seq_candidate
        and scope_growth_corroborated
        and _header_sequence_is_additive_reorder_free(
            old_fields.get("header_sequence"),
            new_fields.get("header_sequence"),
            scope_new_headers,
        )
    ):
        # Header-sequence-growth carve-out (PR #641 follow-up, third
        # round) -- see check_contracts_comparable's own docstring.
        unexplained -= header_seq_candidate

    include_seq_candidate = unexplained & _INCLUDE_SEQUENCE_FIELDS
    if (
        include_seq_candidate
        and scope_growth_corroborated
        and _include_sequence_is_additive_owned_growth(
            old_fields.get("include_sequence"),
            new_fields.get("include_sequence"),
            scope_new_headers,
        )
    ):
        # Include-sequence-owned-growth carve-out (PR #641 follow-up,
        # fourth round) -- see check_contracts_comparable's own
        # docstring.
        unexplained -= include_seq_candidate

    return unexplained


def _profile_mismatch_reason(
    unknown_differing: set[str], differing: set[str], unexplained: set[str]
) -> str | None:
    """Why an authenticated ``profile_fingerprint`` mismatch is not comparable,
    or ``None`` when every differing field was explained by a carve-out."""
    if unknown_differing:
        return (
            "old and new snapshots were extracted under different "
            "compile contexts (profile_fingerprint mismatch), and "
            f"differ on field(s) this version does not recognize: "
            f"{', '.join(sorted(unknown_differing))} — the comparison "
            "cannot be verified safe."
        )
    if not differing:
        # Codex review, PR #641 follow-up (P1): profile_fingerprint
        # differs but NONE of the known PROFILE_FIELD_KEYS explain it --
        # profile_fields was entirely absent/malformed on
        # deserialization (_extraction_contract_from_dict substitutes
        # {}, so every old_fields.get(k, "")/new_fields.get(k, "")
        # compares "" == "" for every k). An empty `differing` must NOT
        # be treated as "nothing to explain, therefore comparable" --
        # that would silently bypass this fail-closed gate exactly when
        # the granular field data needed to verify safety is missing or
        # incomplete, which is the opposite of the gate's purpose.
        return (
            "old and new snapshots were extracted under different "
            "compile contexts (profile_fingerprint mismatch), but no "
            "recognized profile field explains the difference — "
            "profile_fields may be absent/incomplete — so the "
            "comparison cannot be verified safe."
        )
    if unexplained:
        return (
            "old and new snapshots were extracted under different compile "
            f"contexts (profile_fingerprint mismatch; differing fields: "
            f"{', '.join(sorted(unexplained))}) — the comparison "
            "is not comparable."
        )
    return None


def _check_profile_fingerprint_comparable(
    old: AbiSnapshot, new: AbiSnapshot
) -> ComparabilityMismatch | None:
    """The ``profile_fingerprint`` half of :func:`check_contracts_comparable`.

    Gated independently of the scope half — a side that never ran an L2
    frontend carries no ``profile_fingerprint`` and is not hard-failed on this
    axis for that ordinary depth difference alone. Returns the mismatch that
    would raise :class:`ProfileMismatchError`, or ``None`` when this axis is
    comparable.
    """
    old_contract = old.contract
    new_contract = new.contract
    if (
        old_contract is None
        or new_contract is None
        or old_contract.profile_fingerprint is None
        or new_contract.profile_fingerprint is None
        or old_contract.profile_fingerprint == new_contract.profile_fingerprint
    ):
        return None

    old_fields = old_contract.profile_fields
    new_fields = new_contract.profile_fields
    if not (
        _fingerprint_is_authentic(
            old_contract.profile_fingerprint, old_fields, _PROFILE_FINGERPRINT_KEY_SETS
        )
        and _fingerprint_is_authentic(
            new_contract.profile_fingerprint, new_fields, _PROFILE_FINGERPRINT_KEY_SETS
        )
    ):
        # No carve-out below may be trusted: at least one side's
        # profile_fields don't actually produce that side's own
        # profile_fingerprint (Codex review, PR #641 follow-up, sixth
        # P1) -- see _fingerprint_matches_fields's own docstring, and
        # the scope-side equivalent check above.
        return ComparabilityMismatch(
            kind="profile",
            reason=(
                "old and new snapshots were extracted under different "
                "compile contexts (profile_fingerprint mismatch), and at "
                "least one side's profile_fields do not reproduce its own "
                "profile_fingerprint — the comparison cannot be verified "
                "safe."
            ),
        )

    differing = _differing_keys(
        old_fields, new_fields, _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
    )
    # `_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`, not `PROFILE_FIELD_KEYS`
    # (CodeRabbit review): `frontend_context_kind` is a field this build knows
    # about -- `differing` directly above iterates it -- so reporting it as one
    # "this version does not recognize" was simply wrong. The outcome for a
    # differing `frontend_context_kind` is unchanged, only its reason: no
    # carve-out's field-set contains it, so it stays in `unexplained` and the
    # pair is still not comparable.
    unknown_differing = _unknown_differing_keys(
        old_fields, new_fields, _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
    )
    unexplained = _unexplained_profile_fields(
        old, new, old_contract, new_contract, differing
    )
    reason = _profile_mismatch_reason(unknown_differing, differing, unexplained)
    if reason is None:
        return None
    return ComparabilityMismatch(kind="profile", reason=reason)
