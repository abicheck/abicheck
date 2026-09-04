# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""check_stripped_fp.py — false-positive guard for reduced-evidence artifact lanes.

A non-default artifact mode (stripped / release-without-debug / build-source)
changes the evidence available to the detector. It may legitimately *lose*
signal — a stripped or release binary drops the DWARF a layout/calling-convention
break needs — but it must never *manufacture* a real break. So the sound,
blockable invariant for any such full/partial run is: a case the debug ground
truth calls non-breaking (COMPATIBLE / NO_CHANGE / COMPATIBLE_WITH_RISK) must
never come out BREAKING in the reduced mode. Missed breaks (BREAKING→COMPATIBLE,
e.g. case129 stripped/release) are expected evidence loss and are reported, not
failed.

Usage:
    python tests/check_stripped_fp.py <results.json> [label]

Exit codes:
    0  no false positives in the reduced-evidence run
    1  one or more cases gained a spurious BREAKING
    2  input/usage error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).parent.parent
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"

# evidence_tiers.py is the single, exhaustive, KeyError-if-unmapped source of
# truth for which ChangeKind sits at which evidence tier -- reused below
# instead of re-deriving "is this kind DWARF-independent" from a second,
# necessarily-incomplete name/prefix list.
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import evidence_tiers  # noqa: E402

# Verdicts the ground truth may declare as "not a real ABI break".
# COMPATIBLE_WITH_RISK is included: the runtime-model-flip cases (case130–133)
# are risk-only, so a reduced-evidence run that reports BREAKING for one of them
# is still a spurious break the guard must catch.
_COMPATIBLE_EXPECTED = {"COMPATIBLE", "NO_CHANGE", "COMPATIBLE_WITH_RISK"}

# A reduced-evidence BREAKING -> clean result is only an expected downgrade
# when the receipt identifies the *specific* lost capability.  In particular,
# an overall partial result cannot waive an L0 export/SONAME finding: those
# checks do not depend on DWARF.  ``failed`` and ``not_comparable`` are failed
# validation, not evidence-loss downgrades.
_DWARF_DEPENDENT_MIN_EVIDENCE = "L1"
_L0_KIND_PREFIXES = ("symbol_", "soname_")
_L0_KIND_NAMES = {"func_removed", "func_removed_elf_only"}

# The L1 catalog tier is deliberately broader than one detector: basic debug
# metadata proves record/enum layout, while the advanced channel proves
# calling-convention/value-ABI/toolchain facts.  A general ``partial`` receipt
# is not a waiver.  Keep this map explicit and default unknown kinds to no
# waiver, so a new detector cannot be accidentally waived before declaring its
# required channel.
_DEBUG_CHANNEL_BY_KIND = {
    "calling_convention_changed": "advanced",
    "frame_register_changed": "advanced",
    "value_abi_trait_changed": "advanced",
    "struct_return_convention_changed": "advanced",
    "struct_packing_changed": "advanced",
    "toolchain_flag_drift": "advanced",
    # integer_model_changed (diff_integer_model._diff_integer_model) reads
    # AbiSnapshot.functions/typedefs -- header/L2 evidence, never DWARF-
    # advanced facts. Deliberately absent from this map (rather than mapped
    # to a channel) so an unrelated advanced-DWARF loss can never waive a
    # regression in this detector (P1 review).
    "wchar_model_changed": "advanced",
    "vector_abi_changed": "advanced",
    "type_size_changed": "basic",
    "struct_size_changed": "basic",
    "type_alignment_changed": "basic",
    "type_field_offset_changed": "basic",
    "type_field_type_changed": "basic",
    "type_field_added": "basic",
    "type_removed": "basic",
    "type_kind_changed": "basic",
    "enum_member_added": "basic",
    "enum_member_removed": "basic",
    "enum_member_renamed": "basic",
    "enum_member_value_changed": "basic",
    "enum_last_member_value_changed": "basic",
    "enum_underlying_size_changed": "basic",
    "union_field_added": "basic",
    "union_field_removed": "basic",
    "field_bitfield_changed": "basic",
    "flexible_array_member_changed": "basic",
    "base_class_offset_changed": "basic",
    "base_class_position_changed": "basic",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _toolchain_family(name: str | None) -> str:
    """Producer family ("gcc"|"clang"|"") from a compiler path/name string."""
    name = (name or "").lower()
    if "clang" in name:
        return "clang"
    if "gcc" in name or "g++" in name:
        return "gcc"
    return ""


def _case_is_cpp(case: str) -> bool:
    """Best-effort C vs C++ detection: does the case dir carry a .cpp/.hpp?

    Filesystem-only (no compiler invocation) — mirrors the same v1/v2/old-new/
    good-bad/libfoo naming conventions test_example_autodiscovery.py's layout
    detectors use, without needing to import that module here.
    """
    case_dir = REPO_DIR / "examples" / case
    if not case_dir.is_dir():
        return False
    return any(case_dir.rglob("*.cpp")) or any(case_dir.rglob("*.hpp"))


def _gap_applies(
    entry: dict[str, Any], case: str, platform: str, variant: str, data: dict[str, Any]
) -> bool:
    """Whether entry's known_gap is scoped to apply to this specific row.

    Mirrors tests/validate_examples.py::_gap_applies exactly (toolchain/
    platform/variant scoping) so a gap that's only documented for one
    producer/platform/variant doesn't blanket-exempt a real regression on a
    different one -- absent scoping fields apply everywhere (back-compat).
    """
    toolchains = entry.get("known_gap_toolchains")
    if toolchains:
        is_cpp = _case_is_cpp(case)
        compiler = data.get("compiler_cxx") if is_cpp else data.get("compiler_c")
        if _toolchain_family(compiler) not in toolchains:
            return False
    platforms = entry.get("known_gap_platforms")
    if platforms and platform not in platforms:
        return False
    variants = entry.get("known_gap_variants")
    if variants and variant not in variants:
        return False
    return True


def _is_l0_finding(entry: dict[str, Any]) -> bool:
    """Whether the canonical break includes direct binary-table evidence."""
    kinds = entry.get("expected_kinds") or ()
    return any(
        kind in _L0_KIND_NAMES or kind.startswith(_L0_KIND_PREFIXES)
        for kind in kinds
        if isinstance(kind, str)
    )


def _dwarf_evidence_loss_allows_downgrade(
    entry: dict[str, Any], assurance: object
) -> bool:
    """Allow only a receipt proving the required DWARF capability was lost.

    The catalog's ``min_evidence`` says which capability establishes the
    canonical finding; the receipt says which capability this run actually
    lost.  Both are required.  This deliberately excludes L0 kinds even when
    they co-occur with a higher-level detector in a case -- but only when
    that co-occurring L0 kind's own default verdict is itself BREAKING
    (``_is_l0_finding``'s hardcoded set): such a kind should keep firing
    without DWARF, so any downgrade for that case is suspicious, not
    expected evidence loss.
    """
    if not isinstance(assurance, dict):
        return False
    if assurance.get("status") != "partial":
        return False
    if entry.get("min_evidence") != _DWARF_DEPENDENT_MIN_EVIDENCE:
        return False
    if _is_l0_finding(entry):
        return False
    kinds = entry.get("expected_kinds")
    if not isinstance(kinds, list) or not kinds:
        return False
    # A kind whose own evidence tier is L0 (per evidence_tiers.py's
    # exhaustive registry) never depends on DWARF at all -- it is not part
    # of "the DWARF capability that was lost" and must not be required to
    # name a debug channel below. Without this filter, a case mixing an L0
    # kind (still detectable without DWARF, but not BREAKING-tier on its
    # own -- e.g. runtime_floor_raised, RISK-tier) with a DWARF-dependent
    # kind (the one actually lost) trips the "None in channels" guard and
    # wrongly rejects a genuine downgrade as an unproven regression
    # (case15_noexcept_change: found when frame_register_changed's first
    # real use in this catalog co-occurred with the pre-existing L0
    # runtime_floor_raised kind). An unmapped kind is conservatively *not*
    # treated as L0 here, preserving this module's existing "default
    # unknown kinds to no waiver" contract.
    dwarf_dependent_kinds = [
        kind
        for kind in kinds
        if isinstance(kind, str)
        and evidence_tiers.EVIDENCE_TIER_BY_KIND.get(kind) != "L0"
    ]
    if not dwarf_dependent_kinds:
        return False
    channels = {_DEBUG_CHANNEL_BY_KIND.get(kind) for kind in dwarf_dependent_kinds}
    if None in channels:
        return False

    # The receipt, not aggregate dwarf_context_status, proves loss for each
    # individual detector channel.  This rejects both pre-receipt artifacts
    # and the critical "basic parsed, advanced unavailable" case for layout
    # findings such as type_size_changed.
    evidence = assurance.get("debug_evidence")
    if not isinstance(evidence, dict):
        return False
    sides = [evidence.get(side) for side in ("old", "new")]
    if not all(isinstance(side, dict) for side in sides):
        return False
    # P2 review, fresh evidence: "not_supported" (a BTF/CTF-sourced side's
    # advanced channel -- neither format carries calling-convention/value-
    # ABI/frame-register facts at all, see analysis_assurance._debug_
    # evidence_receipt) is just as much a proven capability loss as the
    # other non-parsed states below. Omitting it rejected a legitimate
    # BTF/CTF-backed downgrade (e.g. calling_convention_changed) as an
    # unproven regression even though the receipt already proves the
    # required capability was unavailable on that side.
    non_parsed_states = {
        "not_available",
        "presence_only",
        "partial",
        "failed",
        "not_supported",
    }
    for channel in channels:
        if not any(side.get(channel) in non_parsed_states for side in sides):
            return False

    # The "basic" DWARF channel and header (L2) evidence both populate the
    # same model-level RecordType/EnumType facts diff_types.py compares --
    # struct/enum layout is derivable from EITHER source. So losing DWARF
    # alone does not prove the capability was lost: when header evidence is
    # present and clean (or merely drift-flagged -- still present) on this
    # exact run, the same finding should still have been caught from
    # headers, and a BREAKING->clean regression here is a real bug, not
    # expected evidence loss. Only "advanced"-channel kinds (calling
    # convention/value-ABI/frame-register/toolchain facts) have no header
    # equivalent, so this check applies only when "basic" evidence is
    # implicated (P1 review).
    if "basic" in channels:
        header_context_status = assurance.get("header_context_status")
        if header_context_status in ("clean", "drift_detected"):
            return False
    return True


def _known_gap_covers_row(
    entry: dict[str, Any],
    *,
    got: str,
    status: object,
    case: str,
    platform: str,
    variant: str,
    data: dict[str, Any],
) -> bool:
    """Whether a reviewed, exact known-gap observation covers this row.

    A prose-only ``known_gap`` is documentation, not a blanket waiver for a
    full CLI verdict.  The artifact must be an XFAIL and the catalog must pin
    the exact observed wrong verdict.
    """
    observed = entry.get("known_gap_observed")
    return bool(
        status == "XFAIL"
        and isinstance(observed, list)
        and got in observed
        and entry.get("known_gap")
        and _gap_applies(entry, case, platform, variant, data)
    )


def _classify_results(
    rows: list[dict[str, Any]], gt: dict[str, Any], label: str, data: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    """Split result rows into (false_positives, downgrades, errors) messages."""
    false_positives: list[str] = []
    downgrades: list[str] = []
    errors: list[str] = []
    for r in rows:
        case = str(r.get("case_id") or r.get("name") or "")
        got = (r.get("got") or "").upper()
        entry = gt.get(case, {})
        expected = (entry.get("expected") or "").upper()
        status = r.get("status")
        # SKIP is benign (tool/platform/feature unavailable). ERROR is NOT: the
        # validate run is invoked under `set +e`, so an ERROR row is the only
        # remaining signal that the reduced-evidence mode failed to produce a
        # verdict for a case. Ignoring it would let a crashed run pass the guard
        # green without ever checking the false-positive invariant — so treat
        # ERROR (and a missing verdict that is not a SKIP) as a guard failure.
        if status == "SKIP":
            continue
        if status == "ERROR" or not got:
            errors.append(f"{case}: status={status} ({r.get('message', '')[:120]})")
            continue
        # A scoped known_gap is an already-triaged mismatch in either verdict
        # direction, not evidence loss introduced by this reduced-evidence
        # mode. Only exempt it when the gap applies to this exact row
        # (toolchain/platform/variant), so a macOS/clang gap cannot mask a
        # genuine Linux/gcc regression on the same case.
        platform = r.get("platform") or data.get("platform", "")
        # _result_to_json (validate_examples.py) writes the artifact variant to
        # both "mode" and "variant" (mode is the JSON artifact schema's
        # canonical field name) -- prefer "mode" so a results file that only
        # carries one of the two still resolves correctly, only falling back
        # to the CLI label when a row has neither (Codex review).
        variant = r.get("mode") or r.get("variant") or label
        known_gap_applies = _known_gap_covers_row(
            entry,
            got=got,
            status=status,
            case=case,
            platform=platform,
            variant=variant,
            data=data,
        )
        assurance = r.get("analysis_assurance")
        assurance_status = (
            assurance.get("status") if isinstance(assurance, dict) else None
        )
        if known_gap_applies and (
            (expected in _COMPATIBLE_EXPECTED and got == "BREAKING")
            or (
                expected == "BREAKING"
                and got in _COMPATIBLE_EXPECTED
                and assurance_status not in {"failed", "not_comparable"}
            )
        ):
            downgrades.append(
                f"{case}: {expected}→{got} (known_gap, not evidence loss in {label} mode)"
            )
            continue
        if expected in _COMPATIBLE_EXPECTED and got == "BREAKING":
            false_positives.append(f"{case}: expected {expected} got {got}")
        elif expected == "BREAKING" and got in _COMPATIBLE_EXPECTED:
            dwarf_context_status = (
                assurance.get("dwarf_context_status")
                if isinstance(assurance, dict)
                else None
            )
            if _dwarf_evidence_loss_allows_downgrade(entry, assurance):
                downgrades.append(
                    f"{case}: {expected}→{got} (DWARF evidence lost in {label} "
                    f"mode; analysis_assurance={assurance_status})"
                )
            else:
                errors.append(
                    f"{case}: {expected}→{got} without a DWARF-dependent partial "
                    "analysis_assurance receipt "
                    f"(status={assurance_status!r}, "
                    f"dwarf_context_status={dwarf_context_status!r})"
                )
    return false_positives, downgrades, errors


def _report(
    label: str, false_positives: list[str], downgrades: list[str], errors: list[str]
) -> int:
    """Print the guard report and return the process exit code."""
    if downgrades:
        print(
            f"{label} downgrades (expected evidence loss, reported): {len(downgrades)}"
        )
        for d in downgrades:
            print(f"  - {d}")

    failed = False
    if errors:
        print(
            f"\nERROR: {label} run did not produce a verdict for {len(errors)} case(s) "
            "(crash/compare failure — the FP invariant was never checked):",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        failed = True
    if false_positives:
        print(
            f"\nERROR: {label} false positives: {len(false_positives)}", file=sys.stderr
        )
        for fp in false_positives:
            print(f"  - {fp}", file=sys.stderr)
        failed = True
    if failed:
        return 1
    print(f"\n{label} FP guard: no spurious breaks, no errored cases.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: check_stripped_fp.py <results.json> [label]", file=sys.stderr)
        return 2
    results_path = Path(argv[0])
    label = argv[1] if len(argv) > 1 else "stripped"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found", file=sys.stderr)
        return 2

    gt = _load(GROUND_TRUTH)["verdicts"]
    data = _load(results_path)
    false_positives, downgrades, errors = _classify_results(
        data.get("results", []), gt, label, data
    )
    return _report(label, false_positives, downgrades, errors)


if __name__ == "__main__":
    raise SystemExit(main())
