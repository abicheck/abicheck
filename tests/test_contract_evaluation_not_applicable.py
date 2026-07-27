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

"""ADR-049 Phase 3 shadow evaluator: ``_NOT_APPLICABLE_KIND_SLUGS`` curation.

Split out of ``test_contract_evaluation.py`` (AI-readiness 2000-line hard
cap): a ``NOT_APPLICABLE`` kind short-circuits before mode or surface
evidence is even consulted, regardless of mode or surface resolvability --
this file owns exactly that curation, one class, independent of the rest of
the shadow evaluator's behavior covered by the sibling file.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import (
    ContractEvaluationDecision,
    evaluate_change_contract_relevance,
)
from abicheck.contract_relevance_types import (
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
from abicheck.surface import PublicSurface, compute_public_surface


def _fn(name, vis=Visibility.PUBLIC, origin=ScopeOrigin.UNKNOWN, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type="void",
        visibility=vis,
        origin=origin,
    )


_UNRESOLVABLE = PublicSurface()  # resolvable defaults to False


class TestNotApplicableKinds:
    """A NOT_APPLICABLE kind short-circuits before mode or surface evidence
    is even consulted -- true regardless of mode or surface resolvability."""

    def test_soname_changed_is_not_applicable_under_public_mode(self) -> None:
        c = Change(kind=ChangeKind.SONAME_CHANGED, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_not_applicable_kind_wins_even_with_resolvable_surfaces(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.RELRO_WEAKENED, symbol="", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.NOT_APPLICABLE
        assert decision.reason_code == "non_entity_finding"

    def test_ordinary_entity_kind_is_not_not_applicable(self) -> None:
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision.relevance is not ContractRelevance.NOT_APPLICABLE

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.NEEDED_ADDED,
            ChangeKind.NEEDED_REMOVED,
            ChangeKind.NEEDED_ORDER_CHANGED,
        ],
    )
    def test_needed_dependency_changes_are_not_applicable(
        self, kind: ChangeKind
    ) -> None:
        # Regression (Codex review, fresh evidence): DT_NEEDED loader
        # dependency changes describe which *other* shared libraries this
        # one requires, never a function/variable/type an importing
        # consumer's code references -- without this, they fell through to
        # ordinary header-surface classification and came back
        # UNKNOWN_UNRESOLVED (PUBLIC mode, no headers) or IN_CONTRACT (ALL
        # mode), neither of which is the right ADR-049 "non-entity" verdict.
        # needed_order_changed (a pure DT_NEEDED reorder, dependency set
        # unchanged) is the same loader-level state as needed_added/removed,
        # not a different kind of entity.
        c = Change(kind=kind, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.ELF_MACHINE_CHANGED,
            ChangeKind.ELF_ABI_FLAGS_CHANGED,
            ChangeKind.MACHO_CPU_TYPE_CHANGED,
        ],
    )
    def test_binary_architecture_identity_changes_are_not_applicable(
        self, kind: ChangeKind
    ) -> None:
        # Regression (Codex review, fresh evidence): elf_machine_changed
        # (e_machine drift), elf_abi_flags_changed (decoded float-ABI/EABI
        # drift), and macho_cpu_type_changed (the Mach-O analogue of
        # pe_machine_changed/elf_class_changed) are all binary-wide CPU
        # architecture/calling-convention identity, not a function/variable/
        # type -- but were missing from this set, so each fell through to
        # UNKNOWN_UNRESOLVED (PUBLIC mode) / IN_CONTRACT (ALL mode) instead
        # of correctly short-circuiting here like their PE sibling.
        c = Change(kind=kind, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.INTERPRETER_CHANGED,
            ChangeKind.BIND_NOW_DISABLED,
            ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED,
            ChangeKind.ELF_INIT_FINI_CHANGED,
            ChangeKind.SYMBOLIC_BINDING_MODE_CHANGED,
            ChangeKind.COMPAT_VERSION_CHANGED,
        ],
    )
    def test_loader_control_changes_are_not_applicable(self, kind: ChangeKind) -> None:
        # Regression (Codex review, fresh evidence): PT_INTERP/DT_* loader-
        # control state (program interpreter path, eager/lazy symbol
        # binding, dlopen/dlclose flags, init/fini array presence,
        # DT_SYMBOLIC/DF_SYMBOLIC) and Mach-O's LC_ID_DYLIB compat_version
        # (its own synthetic symbol="compat_version" subject,
        # diff_platform._diff_macho_compat_version) are all binary-wide
        # loader-contract identity, the same synthetic-subject shape as the
        # DT_NEEDED findings above -- but were missing from this set.
        c = Change(kind=kind, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
            ChangeKind.TEXT_RELOCATION_REMOVED,
            ChangeKind.CET_PROTECTION_WEAKENED,
            ChangeKind.BRANCH_PROTECTION_WEAKENED,
            ChangeKind.CET_PROTECTION_IMPROVED,
            ChangeKind.BRANCH_PROTECTION_IMPROVED,
        ],
    )
    def test_remaining_security_hardening_changes_are_not_applicable(
        self, kind: ChangeKind
    ) -> None:
        # Regression (Codex review, fresh evidence): a writable+executable
        # segment (W^X violation) and IBT/SHSTK (x86 CET) / BTI/PAC
        # (AArch64 branch protection) gained or dropped are the same
        # binary-wide build-flag hardening posture as the security kinds
        # already covered (RELRO/PIE/canary/FORTIFY/...), but were omitted
        # from the original curation.
        c = Change(kind=kind, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.RUNTIME_FLOOR_RAISED,
            ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED,
            ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED,
            ChangeKind.X86_ISA_BASELINE_RAISED,
            ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED,
        ],
    )
    def test_deployment_floor_changes_are_not_applicable(
        self, kind: ChangeKind
    ) -> None:
        # Regression (Codex review, fresh evidence): each of these is a
        # synthesized headline finding over a synthetic subject (e.g.
        # "libc.so.6:GLIBC_2" for RUNTIME_FLOOR_RAISED) -- the minimum
        # runtime/OS/CPU-ISA a binary now requires, never a specific
        # function/variable/type a consumer's code references. Only the
        # neighboring wheel-deployment checks were covered before this.
        c = Change(kind=kind, symbol="libfoo.so.1:GLIBC_2", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_pe_import_load_mode_change_is_not_applicable(self) -> None:
        # Regression (Codex review, fresh evidence): pe_import_load_mode_changed
        # is a link-time property of a PE import table entry (eager IAT vs.
        # delay-loaded), not a change to the imported function itself.
        c = Change(
            kind=ChangeKind.PE_IMPORT_LOAD_MODE_CHANGED,
            symbol="kernel32.dll!Sleep",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_hash_style_removed_is_not_applicable(self) -> None:
        # Regression (Codex review, fresh evidence): hash_style_removed is a
        # synthetic ".hash"/".gnu.hash" subject (ELF symbol-hash style),
        # never a real function/variable/type.
        c = Change(
            kind=ChangeKind.HASH_STYLE_REMOVED, symbol=".gnu.hash", description=""
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_musllinux_glibc_dependency_detected_is_not_applicable(self) -> None:
        # Regression (Codex review, fresh evidence): a synthetic
        # symbol="<platform-baseline>" sentinel finding -- a musllinux-tagged
        # wheel actually depending on a glibc-versioned symbol, a packaging
        # fact, never a real function/variable/type.
        c = Change(
            kind=ChangeKind.MUSLLINUX_GLIBC_DEPENDENCY_DETECTED,
            symbol="<platform-baseline>",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )
