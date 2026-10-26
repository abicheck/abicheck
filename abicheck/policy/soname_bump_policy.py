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

"""SONAME-bump advisory: recommend a bump when breaking changes ship without
one, and flag a bump that no breaking change justifies.

This is a gating decision over already-detected findings, so it lives in
``policy/`` (ADR-061). It was moved out of the legacy ``diff_versioning``
monolith; ``checker`` is its only production caller.
"""

from __future__ import annotations

from collections.abc import Callable

from ..diff_helpers import make_change
from ..model.binary_naming import strip_vendor_hash
from ..model.change import Change
from ..model.change_catalog.assembled import BREAKING_KINDS
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.elf_facts import ElfMetadata

__all__ = ["check_soname_bump_policy"]

#: BREAKING kinds a SONAME bump cannot remedy — deployment/wheel-packaging
#: failures rather than an ABI-incompatible symbol-table change. Bumping
#: DT_SONAME tells consumers "relink against the new major version"; it does
#: nothing for "your wheel claims x86_64 but the binary is aarch64"
#: (wheel_tag_architecture_mismatch), "this musllinux-tagged binary actually
#: needs glibc" (musllinux_glibc_dependency_detected), or "a vendored
#: dependency has no RPATH to ever be found" (wheel_closure_dependency_violation)
#: — recommending a bump for these is actively misleading remediation advice
#: (Codex review #583). The same reasoning applies to
#: :data:`_BASELINE_VIOLATION_ONLY_KINDS` once
#: :func:`promote_baseline_violation_findings` gives one of them
#: ``effective_verdict=BREAKING``: "the binary requires a newer GLIBC/macOS
#: SDK than the declared deployment floor promises" is fixed by rebuilding
#: against the older sysroot, never by a SONAME bump.
#: ``WHEEL_RPATH_NOT_PORTABLE`` is deliberately *not* included here: it is
#: no longer promoted to BREAKING at all (see
#: :data:`_BASELINE_VIOLATION_ONLY_KINDS`'s own docstring), and at its
#: catalog-default RISK severity it can never satisfy
#: :func:`check_soname_bump_policy`'s own breaking-kind check anyway, so
#: including it here would be inert.
_SONAME_BUMP_CANNOT_FIX_KINDS = frozenset(
    {
        ChangeKind.MUSLLINUX_GLIBC_DEPENDENCY_DETECTED,
        ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH,
        ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION,
        ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED,
        ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED,
    }
)


def _count_distinct_breaking_symbols(
    changes: list[Change], is_breaking: Callable[[Change], bool]
) -> int:
    """How many distinct symbols the breaking *changes* touch.

    A single root cause can reach the change list more than once -- a
    visibility finding and an ELF-only removal for one export, or a parameter
    and a return change on one function -- and counting findings reports it as
    several incompatibilities. Counting symbols makes the advisory count the
    affected interface, not the number of detectors that noticed it. A change
    with no symbol counts on its own, so the count never drops below the
    number of such changes.
    """
    keys = {
        c.symbol if c.symbol else ("<no-symbol>", id(c))
        for c in changes
        if is_breaking(c)
    }
    return len(keys)


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
        # Deployment/wheel-packaging BREAKING kinds a SONAME bump cannot fix
        # never count toward this policy's recommendation, regardless of
        # effective_verdict (Codex review #583).
        if c.kind in _SONAME_BUMP_CANNOT_FIX_KINDS:
            return False
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
        breaking_count = _count_distinct_breaking_symbols(
            changes, _is_effectively_breaking
        )
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
