"""Contract evaluation is total over the ChangeKind catalog (ADR-049 amendment).

Bug class ``classification.non_entity_finding_feeds_contract_coverage``: a kind
no contract domain claimed fell through to the header domain, read
``UNKNOWN_UNRESOLVED`` under a stated contract, and became an unsuppressible
``finding_unresolved`` coverage failure (exit 1). The escapes were a
hand-curated exemption list grown one field report at a time (SONAME, then
``DT_NEEDED``, then version nodes, then -- the SVS report -- imports), and a
probe of the whole catalog still found 59 non-declaration kinds unresolved
(``toolchain_flag_drift``, ``exceptions_mode_changed``, every ``bundle_*`` and
``sycl_*`` kind, ...).

The invariant, stated over the whole catalog rather than the reported kinds:
for every ``ChangeKind`` x contract mode {adaptive default, public, exports,
all} x subject shape {declared public, undeclared own export, runtime ``std::``
name, an import, a sentinel}, only a kind whose subject is a *declaration* can
end ``UNKNOWN_UNRESOLVED``; every other kind is ``NOT_APPLICABLE`` and stays
scored by policy. The oracle is the catalog's declared ``contract_subject``
cross-checked against an independent hand-written table below -- never the
evaluator's own exemption set.
"""

from __future__ import annotations

import dataclasses
import functools

import pytest

from abicheck.build_mode import BuildMode, StdlibFamily
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.compatibility_evaluation_config import ValueProvenance
from abicheck.contract_pipeline import build_contract_stage
from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractRelevance,
    SelectorLayer,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.dimensions import (
    ChangeEntity,
    ChangeOperation,
    ContractSubject,
)
from abicheck.model.change_catalog.registry import (
    ChangeKindMeta,
    ChangeKindRegistry,
    Verdict,
)
from abicheck.policy.finding_disposition import PipelineContext

_ADAPTIVE = ValueProvenance(
    layer=SelectorLayer.BUILT_IN_DEFAULT,
    source_kind="evidence_adaptive",
    reference="public_header_evidence",
)
_STATED = ValueProvenance(
    layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag", reference="--contract"
)

#: (contract mode, how it was chosen). The adaptive default is ``public``
#: picked from header evidence; the other three are the user's stated domain.
_MODES = {
    "adaptive": ("public", _ADAPTIVE),
    "public": ("public", _STATED),
    "exports": ("exports", _STATED),
    "all": ("all", _STATED),
}

#: Subject shapes a finding can carry. The kind decides relevance for a
#: non-declaration subject, never the spelling of its symbol.
_SUBJECTS = {
    "declared_public": "pub_a",
    "undeclared_own_export": "mylib_internal_helper",
    "runtime_std_name": "_ZNSt6vectorIiSaIiEE9push_backERKi",
    "import": "memcpy",
    "sentinel": "<toolchain>",
    "empty": "",
}

_DECL = ContractSubject.DECLARATION
_DEP = ContractSubject.DEPENDENCY_EDGE
_BIN = ContractSubject.BINARY_WIDE
_BUILD = ContractSubject.BUILD_FACT
_ANA = ContractSubject.ANALYSIS

#: Hand-written from each detector's subject construction (the ``symbol=`` it
#: builds), independent of the catalog: every kind a field report or the
#: catalog probe found unresolved, plus the declaration-subject controls that
#: must keep gating when the contract cannot place them.
_EXPECTED_SUBJECT: dict[str, ContractSubject] = {
    # Edges to another library (an import, a required version, a provider).
    "imported_symbol_added": _DEP,
    "imported_symbol_removed": _DEP,
    "needed_added": _DEP,
    "needed_removed": _DEP,
    "symbol_version_required_added": _DEP,
    "symbol_version_required_removed": _DEP,
    "runtime_symbol_provider_changed": _DEP,
    "runtime_weak_resolution_changed": _DEP,
    "target_dependency_added": _DEP,
    "bundle_intra_dep_removed": _DEP,
    "bundle_provider_changed": _DEP,
    "bundle_unresolved_intra_dependency": _DEP,
    "numpy_capi_consumption_added": _DEP,
    # Build and toolchain configuration (``build-option:<key>``, ``<toolchain>``).
    "toolchain_flag_drift": _BUILD,
    "toolchain_version_changed": _BUILD,
    "abi_relevant_build_flag_changed": _BUILD,
    "build_context_changed": _BUILD,
    "exceptions_mode_changed": _BUILD,
    "rtti_mode_changed": _BUILD,
    "tls_model_changed": _BUILD,
    "lto_mode_changed": _BUILD,
    "char_signedness_changed": _BUILD,
    "float_abi_changed": _BUILD,
    "sanitizer_mode_changed": _BUILD,
    "vector_abi_changed": _BUILD,
    "wchar_model_changed": _BUILD,
    "bundle_library_added": _BUILD,
    "bundle_manifest_entry_unsatisfied": _BUILD,
    # The whole artifact (``<dwarf>``, ``__stdlib_implementation``, ``sycl::pi``).
    "dwarf_info_missing": _BIN,
    "stdlib_implementation_changed": _BIN,
    "glibcxx_dual_abi_flip_detected": _BIN,
    "time64_abi_changed": _BIN,
    "integer_model_changed": _BIN,
    "sycl_plugin_removed": _BIN,
    "sycl_pi_entrypoint_removed": _BIN,
    "sycl_runtime_version_changed": _BIN,
    "soname_changed": _BIN,
    "relro_weakened": _BIN,
    # Statements about the evidence, not the library.
    "layer_coverage_asymmetric": _ANA,
    "source_binary_provenance_mismatch": _ANA,
    "source_surface_dso_mismatch": _ANA,
    # Declarations: an own export (or a roll-up of them) the contract must
    # place -- an undeclared one keeps gating.
    "func_removed": _DECL,
    "type_size_changed": _DECL,
    "symbol_binding_changed": _DECL,
    "symbol_type_changed": _DECL,
    "ifunc_introduced": _DECL,
    "kabi_symbol_removed": _DECL,
    "long_double_abi_changed": _DECL,
    "symbol_renamed_batch": _DECL,
    "inline_namespace_moved": _DECL,
    "symbol_leaked_from_dependency_changed": _DECL,
}


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(version="1.0", functions=[_fn("pub_a"), _fn("pub_b")], **common),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a")], **common),
    )


@functools.cache
def _relevances(mode_key: str, subject: str) -> dict[str, ContractRelevance]:
    """Every kind's final relevance through the real stage (domain decision,
    observed-export and adaptive fallbacks, runtime-origin exclusion)."""
    mode, provenance = _MODES[mode_key]
    old, new = _pair()
    stage = build_contract_stage(
        old,
        new,
        scope_to_public_surface=True,
        force_public_symbols=None,
        pp_ctx=PipelineContext(old=old, new=new),
        contract_mode=mode,
    )
    stage = dataclasses.replace(stage, mode_provenance=provenance, changes=[])
    changes = [Change(kind, subject, kind.value) for kind in ChangeKind]
    stage.classify(changes)
    return {c.kind.value: c.contract_relevance for c in changes}


def _subject_of(kind: str) -> ContractSubject | None:
    entry = REGISTRY.get(kind)
    return entry.contract_subject if entry is not None else None


@pytest.mark.parametrize("subject", list(_SUBJECTS.values()), ids=list(_SUBJECTS))
@pytest.mark.parametrize("mode_key", list(_MODES))
def test_only_declaration_subjects_can_stay_unresolved(
    mode_key: str, subject: str
) -> None:
    relevances = _relevances(mode_key, subject)
    unresolved_non_declarations = sorted(
        kind
        for kind, rel in relevances.items()
        if rel is ContractRelevance.UNKNOWN_UNRESOLVED
        and _subject_of(kind) is not _DECL
    )
    assert unresolved_non_declarations == []


@pytest.mark.parametrize("subject", list(_SUBJECTS.values()), ids=list(_SUBJECTS))
@pytest.mark.parametrize("mode_key", list(_MODES))
def test_every_non_declaration_kind_is_not_applicable_and_scored(
    mode_key: str, subject: str
) -> None:
    # NOT_APPLICABLE, not out of contract: policy keeps scoring the finding,
    # so a strict preset still gates it, whatever the mode or the subject's
    # spelling (even a runtime name the toolchain-origin rule would claim).
    relevances = _relevances(mode_key, subject)
    wrong = {
        kind: rel
        for kind, rel in relevances.items()
        if _subject_of(kind) is not _DECL
        and rel is not ContractRelevance.NOT_APPLICABLE
    }
    assert wrong == {}


def test_the_sweep_is_not_vacuous() -> None:
    """Negative control: under a stated ``public`` contract an undeclared own
    export of a declaration kind still reads ``UNKNOWN_UNRESOLVED`` and gates
    -- the totality rule narrows nothing a declaration owes the contract."""
    relevances = _relevances("public", _SUBJECTS["undeclared_own_export"])
    for kind in ("func_removed", "symbol_binding_changed", "kabi_symbol_removed"):
        assert relevances[kind] is ContractRelevance.UNKNOWN_UNRESOLVED, kind
    assert (
        _relevances("public", _SUBJECTS["declared_public"])["func_removed"]
        is ContractRelevance.IN_CONTRACT
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    sorted(_EXPECTED_SUBJECT.items()),
    ids=sorted(_EXPECTED_SUBJECT),
)
def test_catalog_subject_matches_the_independent_table(
    kind: str, expected: ContractSubject
) -> None:
    assert _subject_of(kind) is expected


def test_every_catalog_kind_declares_a_subject() -> None:
    missing = sorted(k.value for k in ChangeKind if _subject_of(k.value) is None)
    assert missing == []
    # Every analysis-entity kind is about the analysis, never a declaration.
    assert all(
        _subject_of(k.value) is _ANA
        for k in ChangeKind
        if REGISTRY.entity_for(k.value) is ChangeEntity.ANALYSIS
    )


def _meta(**overrides: object) -> ChangeKindMeta:
    fields: dict[str, object] = {
        "entity": ChangeEntity.BINARY,
        "operation": ChangeOperation.MODIFIED,
        "contract_subject": ContractSubject.BINARY_WIDE,
    }
    fields.update(overrides)
    return ChangeKindMeta("new_kind", Verdict.COMPATIBLE, impact="x", **fields)  # type: ignore[arg-type]


def test_a_new_kind_without_a_contract_subject_fails_registration() -> None:
    assert ChangeKindRegistry([_meta()]).get("new_kind") is not None
    with pytest.raises(ValueError, match="contract_subject must be declared"):
        ChangeKindRegistry([_meta(contract_subject=None)])


def test_an_analysis_entity_cannot_claim_a_declaration_subject() -> None:
    with pytest.raises(ValueError, match="requires contract_subject 'analysis'"):
        ChangeKindRegistry(
            [_meta(entity=ChangeEntity.ANALYSIS, contract_subject=_DECL)]
        )


@pytest.mark.parametrize("contract_mode", ["public", "exports", "all"])
def test_a_binary_wide_finding_is_scored_under_a_stated_contract(
    contract_mode: str,
) -> None:
    """End to end through ``compare``: a stated contract used to leave a
    stdlib-implementation switch unresolved (unscored, exit-1 coverage
    failure). It is now scored exactly as without a contract."""

    def snap(version: str, stdlib: StdlibFamily) -> AbiSnapshot:
        return AbiSnapshot(
            library="libwidget.so.1",
            version=version,
            from_headers=True,
            functions=[_fn("pub_a")],
            build_mode=BuildMode(stdlib=stdlib),
        )

    old = snap("1", StdlibFamily.LIBSTDCXX)
    new = snap("2", StdlibFamily.LIBCXX)
    baseline = compare(old, new)
    result = compare(old, new, contract_evaluation=True, contract_mode=contract_mode)
    (finding,) = [
        c for c in result.changes if c.kind is ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
    ]
    assert finding.contract_relevance is ContractRelevance.NOT_APPLICABLE
    assert (
        finding.compatibility_evaluation_status
        is CompatibilityEvaluationStatus.EVALUATED
    )
    assert result.not_evaluated == []
    assert result.verdict is baseline.verdict is Verdict.COMPATIBLE_WITH_RISK
