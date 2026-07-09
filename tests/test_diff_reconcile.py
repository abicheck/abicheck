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

"""Unit tests for build-context reconciliation (ADR-039, diff_reconcile).

Covers the diff-layer pass that clears context-free header-parse false positives,
and its soundness guards (Codex review #498): only field-*presence* findings are
reconcilable (never size/offset), guards and declarations are evaluated *per
side*, a pruned field whose *declaration* changed is kept, and a real break
(unconditional or flag-flipped) is never cleared.
"""

from __future__ import annotations

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_reconcile import RECONCILE_REASON, reconcile_build_context
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

GUARD = "CONFIG_KEEP_LEGACY"


def _fn() -> Function:
    return Function(
        name="mk",
        mangled="mk",
        return_type="S *",
        params=[],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _tf(name: str, type_: str = "int") -> TypeField:
    return TypeField(name=name, type=type_)


def _guarded(
    type_: str = "int", guard: str = GUARD, access: str = "public", is_last: bool = True
) -> dict[str, object]:
    """A conditional-field registry entry (full declaration).

    ``is_last`` defaults True: the canonical FP is a *trailing* guarded field, and
    the reconciler only clears a presence delta when the field is terminal (Codex
    review #498, P1). Tests that model a mid-record field pass ``is_last=False``.
    """
    return {
        "guard": guard,
        "type": type_,
        "is_bitfield": False,
        "bitfield_bits": None,
        "access": access,
        "is_last": is_last,
    }


def _reg(field: str = "legacy", *, type_: str = "int", guard: str = GUARD):
    return {"S": {field: _guarded(type_, guard)}}


def _snap(
    version: str,
    fields: list[TypeField],
    *,
    size_bits: int = 64,
    defines: set[str] | None = frozenset({GUARD}),
    conditional: dict[str, dict[str, dict[str, object]]] | None = None,
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="lib",
        version=version,
        from_headers=True,
        types=[
            RecordType(
                name="S",
                kind="struct",
                size_bits=size_bits,
                fields=fields,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        functions=[_fn()],
    )
    snap.build_context_defines = set(defines) if defines else set()
    snap.conditional_fields = conditional or {}
    return snap


def _fp_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """The canonical false positive: v1 declares ``legacy`` unconditionally; v2's
    context-free parse prunes it (guarded on ``CONFIG_KEEP_LEGACY``, its declaration
    kept in the registry). Both records carry the artifact-accurate size (64), so
    only a ``type_field_removed`` phantom arises; both builds define the macro, so
    the real ABI is unchanged."""
    old = _snap("1", [_tf("version"), _tf("legacy")])
    new = _snap("2", [_tf("version")], conditional=_reg())
    return old, new


# ── the headline behaviour ────────────────────────────────────────────────────


def test_false_positive_present_without_reconciliation():
    old, new = _fp_pair()
    result = compare(old, new, scope_to_public_surface=True)
    assert result.verdict == Verdict.BREAKING
    assert {c.kind for c in result.changes} == {ChangeKind.TYPE_FIELD_REMOVED}
    assert result.reconciled_count == 0


def test_reconciliation_clears_the_false_positive():
    old, new = _fp_pair()
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.NO_CHANGE
    assert result.changes == []
    assert result.reconciled_count == 1
    only = result.reconciled_changes[0]
    assert only.kind == ChangeKind.TYPE_FIELD_REMOVED
    assert only.surface_exclusion_reason == RECONCILE_REASON
    assert only.evidence_category == "build_context"


# ── soundness: presence only, never size/offset (Codex P1-b) ─────────────────


def test_size_change_is_never_reconciled():
    """A ``type_size_changed`` is not provable-false from field presence, so it is
    never cleared — even for a same-field-set alignment/packing change."""
    old = _snap("1", [_tf("a"), _tf("b")], size_bits=64, conditional=_reg("b"))
    new = _snap("2", [_tf("a"), _tf("b")], size_bits=128, conditional=_reg("b"))
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_colocated_size_drift_survives_even_when_field_presence_reconciled():
    """A pruned guarded field co-located with a real size change: the field-removal
    is reconciled, but the ``type_size_changed`` survives → verdict stays BREAKING
    (the real change is never hidden)."""
    old = _snap("1", [_tf("version"), _tf("legacy")], size_bits=64)
    new = _snap("2", [_tf("version")], size_bits=128, conditional=_reg())
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert ChangeKind.TYPE_SIZE_CHANGED in {c.kind for c in result.changes}
    assert {c.kind for c in result.reconciled_changes} == {
        ChangeKind.TYPE_FIELD_REMOVED
    }


# ── soundness: pruned-field declaration change (Codex P1, decl) ──────────────


def test_pruned_field_declaration_change_is_kept():
    """A guarded field pruned from the context-free side whose *type* changed
    (``int`` → ``unsigned int``), both builds defining the guard, is a real ABI
    change: comparing declarations (not just names) keeps the finding."""
    old = _snap("1", [_tf("version"), _tf("mode", "int")])
    new = _snap("2", [_tf("version")], conditional=_reg("mode", type_="unsigned int"))
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_pruned_field_access_change_is_kept():
    """A guarded field pruned from the context-free side whose C++ *access*
    changed (public → private) is a real API change: carrying access in the
    reconciled declaration keeps the finding (Codex review #498, P2)."""
    old = _snap("1", [_tf("version"), _tf("mode")])  # observed → public
    new = _snap(
        "2",
        [_tf("version")],
        conditional={"S": {"mode": _guarded(access="private")}},
    )
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_reconcile_runs_before_soname_policy():
    """Reconciliation must precede the SONAME bump policy: a phantom breaking
    finding must not leave a stale ``soname_bump_recommended`` advisory once it is
    cleared (Codex review #498)."""
    from abicheck.elf_metadata import ElfMetadata

    old, new = _fp_pair()
    old.elf = ElfMetadata(soname="libx.so.1")
    new.elf = ElfMetadata(soname="libx.so.1")
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.NO_CHANGE
    assert not any("soname" in c.kind.value for c in result.changes)


# ── soundness: per-side guards (Codex P1-a) ──────────────────────────────────


def test_guard_not_applied_across_sides():
    """A field unguarded in the old build but guarded (and undefined) in the new
    one is a *real* removal: the new side's guard must not mark the old field
    absent. Both builds define an unrelated macro, not the guard."""
    old = _snap("1", [_tf("version"), _tf("legacy")], defines={"OTHER"})
    new = _snap("2", [_tf("version")], defines={"OTHER"}, conditional=_reg())
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_reorder_hidden_by_pruned_field_is_kept():
    """A real field reorder that surfaces only as ``type_field_removed`` (offsets
    unavailable) must not be reconciled: old ``[version, legacy, tail]`` vs new
    ``[version, tail, #ifdef KEEP legacy]``. Adding ``legacy`` back makes the
    orderless maps equal, but ``legacy`` is not terminal in old, so the ordering
    gate keeps the finding (Codex review #498, P1)."""
    old = _snap("1", [_tf("version"), _tf("legacy"), _tf("tail")])
    # new prunes legacy (registry says it is the trailing member of new's source),
    # but in old legacy sits *before* tail — a genuine reorder.
    new = _snap(
        "2",
        [_tf("version"), _tf("tail")],
        conditional={"S": {"legacy": _guarded(is_last=True)}},
    )
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_non_terminal_pruned_field_is_kept():
    """Even a same-field-set case is kept when the pruned field is not terminal on
    the pruned side (``is_last`` False): its position among siblings is unproven,
    so re-adding it could reorder them (Codex review #498, P1)."""
    old = _snap("1", [_tf("version"), _tf("legacy")])
    new = _snap(
        "2", [_tf("version")], conditional={"S": {"legacy": _guarded(is_last=False)}}
    )
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_ambiguous_guard_field_is_not_reconciled():
    """A guarded field flagged ``ambiguous`` (its macro is ``#undef``/``#define``d
    inside a branch the scanner could not evaluate) has build-context-dependent
    presence. The reconciler keeps the finding instead of clearing it — otherwise a
    build that activates the branch (pruning the field) would have a real add/remove
    hidden as NO_CHANGE (Codex review #498, P1). Identical inputs *without* the flag
    reconcile (see ``test_reconciliation_clears_the_false_positive``)."""
    reg = {"S": {"legacy": {**_guarded(), "ambiguous": True}}}
    old = _snap("1", [_tf("version"), _tf("legacy")], conditional=reg)
    new = _snap("2", [_tf("version")], conditional=reg)
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


# ── authority rule: never delete a real break ────────────────────────────────


def test_unconditional_break_is_not_reconciled():
    """A field removal with no conditional-field evidence must survive — the
    authority rule (ADR-028 D3)."""
    old = _snap("1", [_tf("version"), _tf("legacy")])
    new = _snap("2", [_tf("version")])  # no registry → genuinely removed
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_guard_flag_flipped_between_builds_is_a_real_removal():
    """The guard is defined in the old build but not the new one (both builds are
    build-aware — the new one just defines an unrelated macro), so ``legacy`` is
    genuinely gone from the new ABI → kept, not reconciled."""
    old = _snap("1", [_tf("version"), _tf("legacy")], defines={GUARD})
    new = _snap("2", [_tf("version")], defines={"OTHER"}, conditional=_reg())
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def _neg_guarded(type_: str = "int", guard: str = GUARD) -> dict[str, object]:
    """A registry entry for a simple ``#ifndef GUARD`` (negative) field."""
    return {**_guarded(type_, guard), "negative": True}


def test_negatively_guarded_observed_field_is_pruned_and_kept():
    """An old field observed context-free from ``#ifndef KEEP`` but pruned by the
    KEEP-defining build must not be treated as present: the guard-polarity flip
    (old ``#ifndef KEEP``, new ``#ifdef KEEP``) is a real change, not reconciled
    (Codex review #498)."""
    # old: `legacy` observed context-free (under #ifndef), registry marks it
    # negative-guarded by GUARD; the old build defines GUARD → really pruned.
    old = _snap(
        "1",
        [_tf("version"), _tf("legacy")],
        conditional={"S": {"legacy": _neg_guarded()}},
    )
    # new: `legacy` under #ifdef GUARD, pruned context-free, positive registry;
    # the new build defines GUARD → really present.
    new = _snap("2", [_tf("version")], conditional=_reg())
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_effective_decls_drops_defined_negative_guard():
    from abicheck.diff_reconcile import _effective_decls
    from abicheck.model import RecordType

    rec = RecordType(name="S", kind="struct", fields=[_tf("version"), _tf("legacy")])
    reg = {"legacy": _neg_guarded()}
    # GUARD defined → #ifndef false → legacy pruned.
    assert _effective_decls(rec, reg, {GUARD}) == {
        "version": ("int", False, None, "public", False, False, False)
    }
    # GUARD undefined → #ifndef true → legacy stays.
    assert set(_effective_decls(rec, reg, set())) == {"version", "legacy"}


def test_mixed_evidence_pair_is_not_reconciled():
    """When only one side carries build defines, the other is a context-free parse
    whose observed fields are not build-authoritative, so no reconciliation
    happens even though a registry is present (Codex review #498)."""
    old = _snap("1", [_tf("version"), _tf("legacy")], defines=set())  # context-free
    new = _snap("2", [_tf("version")], defines={GUARD}, conditional=_reg())
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_pruned_field_qualifier_change_is_kept():
    """A guarded field pruned from the context-free side whose cv-qualifier
    changed (``const int`` → ``int``) is a real ABI change: carrying the cv/mutable
    bits in the reconciled declaration keeps the finding rather than collapsing it
    to NO_CHANGE (Codex review #498, P2)."""
    old = _snap("1", [_tf("version"), TypeField(name="mode", type="int", is_const=True)])
    new = _snap("2", [_tf("version")], conditional=_reg("mode"))  # registry: non-const
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


# ── no-op safety ──────────────────────────────────────────────────────────────


def test_no_build_evidence_is_a_noop():
    """Without both defines and a registry there is no build evidence, so every
    finding is left untouched (the context-free FP survives)."""
    old = _snap("1", [_tf("version"), _tf("legacy")], defines=set())
    new = _snap("2", [_tf("version")], defines=set(), conditional=_reg())
    change_list = compare(old, new, scope_to_public_surface=True).changes
    kept, reconciled = reconcile_build_context(list(change_list), old, new)
    assert reconciled == []
    assert len(kept) == len(change_list)


def test_reconcile_disabled_by_default():
    """compare() must not reconcile unless explicitly asked — default off."""
    old, new = _fp_pair()
    result = compare(old, new, scope_to_public_surface=True)  # no flag
    assert result.reconciled_count == 0
    assert result.verdict == Verdict.BREAKING


def test_non_presence_findings_are_untouched():
    """A non-reconcilable kind (e.g. a size change) passes straight through even
    with build evidence."""
    from abicheck.checker_types import Change

    old, new = _fp_pair()
    size = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="S", description="x")
    kept, reconciled = reconcile_build_context([size], old, new)
    assert kept == [size] and reconciled == []


def test_finding_on_type_absent_from_snapshot_is_kept():
    """A presence finding whose type is not present in both snapshots is left
    untouched (defensive branch)."""
    from abicheck.checker_types import Change

    old, new = _fp_pair()
    ghost = Change(kind=ChangeKind.TYPE_FIELD_REMOVED, symbol="Ghost", description="x")
    kept, reconciled = reconcile_build_context([ghost], old, new)
    assert kept == [ghost] and reconciled == []


def test_finding_on_type_without_guards_is_kept():
    """Build evidence exists for one type but the finding is on another type with
    no conditional fields → kept."""
    from abicheck.checker_types import Change

    old = _snap("1", [_tf("a"), _tf("b")], conditional={"Other": {"x": _guarded()}})
    new = _snap("2", [_tf("a")], conditional={"Other": {"x": _guarded()}})
    change = Change(kind=ChangeKind.TYPE_FIELD_REMOVED, symbol="S", description="x")
    kept, reconciled = reconcile_build_context([change], old, new)
    assert kept == [change] and reconciled == []


def test_registry_lookup_is_exact_not_tail_matched():
    """A registry keyed by a *different* qualified name (``api::S``) must NOT clear
    a finding on the bare global ``S`` — exact match only, no unqualified-tail
    fallback that could borrow an unrelated namespace's evidence (Codex #498)."""
    old = _snap("1", [_tf("version"), _tf("legacy")])
    new = _snap("2", [_tf("version")], conditional={"api::S": {"legacy": _guarded()}})
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    # The finding is on the global ``S``; the only registry entry is ``api::S``,
    # a distinct type → the real removal survives.
    assert result.verdict == Verdict.BREAKING
    assert result.reconciled_count == 0


def test_effective_decls_resolves_registry_guards_per_side():
    """_effective_decls adds a pruned registry field (with its declaration) only
    when its guard is defined on that side."""
    from abicheck.diff_reconcile import _effective_decls
    from abicheck.model import RecordType

    rec = RecordType(name="S", kind="struct", fields=[_tf("version")])
    reg = {"legacy": _guarded()}
    assert _effective_decls(rec, reg, set()) == {
        "version": ("int", False, None, "public", False, False, False)
    }
    assert _effective_decls(rec, reg, {GUARD}) == {
        "version": ("int", False, None, "public", False, False, False),
        "legacy": ("int", False, None, "public", False, False, False),
    }


def test_effective_decls_treats_observed_field_as_authoritative():
    """A field present in ``fields`` is authoritative on that side: it stays
    present with the record's declaration regardless of the registry guard/defines
    (Codex review #498). Only a registry-*only* (pruned) field is gated by defines."""
    from abicheck.diff_reconcile import _effective_decls
    from abicheck.model import RecordType

    rec = RecordType(name="S", kind="struct", fields=[_tf("x")])
    reg = {"x": _guarded()}
    # Guard active or not, the observed field is present (parse saw it).
    expected = {"x": ("int", False, None, "public", False, False, False)}
    assert _effective_decls(rec, reg, {GUARD}) == expected
    assert _effective_decls(rec, reg, set()) == expected


# ── disclosure (Codex P2) ────────────────────────────────────────────────────


def test_reconciled_findings_disclosed_in_json_sarif_and_cli(capsys):
    """Cleared findings are disclosed (JSON ``build_context_reconciled``, SARIF
    ``buildContextReconciled``, CLI ``--show-filtered``), never silent."""
    import json as _json

    from abicheck.cli_audit import echo_reconciled
    from abicheck.reporter import to_json
    from abicheck.sarif import to_sarif

    old, new = _fp_pair()
    result = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    j = _json.loads(to_json(result))
    assert j["build_context_reconciled"]["count"] == 1
    assert {c["kind"] for c in j["build_context_reconciled"]["changes"]} == {
        ChangeKind.TYPE_FIELD_REMOVED.value
    }
    assert "buildContextReconciled" in _json.dumps(to_sarif(result))

    echo_reconciled(result)
    err = capsys.readouterr().err
    assert "Reconciled as context-free header-parse artifacts" in err
    assert RECONCILE_REASON in err


def test_json_omits_reconciled_block_when_nothing_cleared():
    import json as _json

    from abicheck.reporter import to_json

    old, new = _fp_pair()
    result = compare(old, new, scope_to_public_surface=True)  # no reconciliation
    assert "build_context_reconciled" not in _json.loads(to_json(result))


# ── serialization + example fixtures + service/CLI passthrough ───────────────


def test_registry_and_defines_survive_serialization():
    old, new = _fp_pair()
    rt = snapshot_from_dict(snapshot_to_dict(new))
    assert rt.build_context_defines == {GUARD}
    assert rt.conditional_fields == {"S": {"legacy": _guarded()}}
    result = compare(
        snapshot_from_dict(snapshot_to_dict(old)),
        rt,
        scope_to_public_surface=True,
        reconcile_build_context=True,
    )
    assert result.verdict == Verdict.NO_CHANGE
    assert result.reconciled_count == 1


def _case164_dir():
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "case164_preproc_conditional_field"
    )


def test_case164_fixtures_reconcile():
    """The committed example-catalog fixtures (case163) reproduce the false
    positive and its build-context remedy end-to-end in the fast lane."""
    import json

    case = _case164_dir()
    old = snapshot_from_dict(json.loads((case / "v1.abi.json").read_text()))
    new = snapshot_from_dict(json.loads((case / "v2.abi.json").read_text()))
    assert new.build_context_defines == {"CONFIG_KEEP_LEGACY"}
    assert new.conditional_fields["Config"]["legacy"]["guard"] == "CONFIG_KEEP_LEGACY"

    fp = compare(old, new, scope_to_public_surface=True)
    assert fp.verdict == Verdict.BREAKING
    ok = compare(old, new, scope_to_public_surface=True, reconcile_build_context=True)
    assert ok.verdict == Verdict.NO_CHANGE
    assert ok.reconciled_count == 1


def test_service_compare_snapshots_threads_the_flag():
    """The Tier-2 service verb honours reconcile_build_context (front-ends route
    through here, never the core directly)."""
    from abicheck.service import compare_snapshots

    old, new = _fp_pair()
    assert (
        compare_snapshots(old, new, reconcile_build_context=True).verdict
        == Verdict.NO_CHANGE
    )
    assert compare_snapshots(old, new).verdict == Verdict.BREAKING


def test_cli_compare_reconcile_flag():
    """`abicheck compare … --reconcile-build-context` clears the FP end-to-end and
    discloses the reconciled findings under --show-filtered."""
    from click.testing import CliRunner

    from abicheck.cli import main

    case = _case164_dir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "compare",
            str(case / "v1.abi.json"),
            str(case / "v2.abi.json"),
            "--scope-public-headers",
            "--reconcile-build-context",
            "--show-filtered",
        ],
    )
    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "Reconciled as context-free header-parse artifacts" in combined


def test_cli_reconcile_rejected_for_directory_inputs(tmp_path):
    """The flag is not threaded through the per-library release fan-out, so it is
    rejected (not silently ignored) for directory/package inputs (Codex #498)."""
    from click.testing import CliRunner

    from abicheck.cli import main

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main, ["compare", str(old_dir), str(new_dir), "--reconcile-build-context"]
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "--reconcile-build-context is not supported" in combined
