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

"""The release-level public-surface stage: acquire once, reconcile once.

The orchestration half of the release product model (ADR-061: coordinating
release behavior is ``workflows``' own responsibility): acquire each side's
public surface **once**, index the bundle's exports, and reconcile the one
product contract against the many providers. Every decision lives in the
owning layer it belongs to -- the acquisition identity and the surface in
``model.release_surface``, the acquisition itself in
``workflows.release_surface_acquisition``, the export index in
``compare.bundle_export_index``, the reconciliation in
``policy.release_contract_reconciliation`` -- and this module only assembles
them from what a release front end has already resolved.

:func:`reconcile_release_public_surface` must run **before** the per-member
``_diff_result``/bundle-evidence keys are stripped from the release's
library entries, because the export index is built from them. The report
section is assembled afterwards, from the returned stage, by
``report.release_public_surface.assemble_release_public_surface``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..compare.bundle_export_index import (
    build_bundle_export_index,
    member_export_names,
)
from ..model.release_surface import (
    ReleasePublicSurface,
    SurfaceAcquisitionIdentity,
    unresolved_surface,
)
from ..policy.release_contract_reconciliation import (
    ReleaseReconciliation,
    reconcile_release,
    undocumented_exports_by_member,
)
from .release_surface_acquisition import (
    SurfaceAcquisitionLedger,
    acquire_release_surface,
)

if TYPE_CHECKING:
    from ..compile_context import CompileContext

#: Member entry verdicts that mean the member never produced comparable
#: evidence (``member_error_entry``'s own vocabulary plus the degraded
#: marker). Such a member is an *unread provider*, which is why it lands in
#: ``failed_members`` rather than simply being absent: an absent member
#: silently narrows the union and turns the symbols it provides into
#: missing exports.
_FAILED_MEMBER_VERDICTS = frozenset(
    {"ERROR", "error", "failed", "not_comparable", "unsupported"}
)


@dataclass(frozen=True)
class ReleaseSurfaceStage:
    """What the release-level stage produced, before reporting."""

    reconciliation: ReleaseReconciliation | None
    ledger: SurfaceAcquisitionLedger
    old_surface: ReleasePublicSurface | None
    new_surface: ReleasePublicSurface | None

    @property
    def findings(self) -> tuple[Any, ...]:
        """Release-level findings (empty when the stage did not run)."""
        return () if self.reconciliation is None else self.reconciliation.findings

    @property
    def warnings(self) -> tuple[str, ...]:
        return (
            () if self.reconciliation is None else self.reconciliation.coverage_warnings
        )


def _compile_options(
    compile_context: CompileContext | None,
) -> tuple[tuple[str, str], ...]:
    """The AST-affecting values of *compile_context*, as sorted pairs.

    Read off the resolved context rather than re-derived from CLI flags:
    the context *is* the run's decision, and re-deriving one here would be
    a second opinion about it (the same "read, don't re-derive" rule the
    gate-pack application follows).
    """
    if compile_context is None:
        return ()
    fields = (
        "compiler",
        "gcc_path",
        "gcc_prefix",
        "gcc_options",
        "sysroot",
        "nostdinc",
        "frontend",
        "frontend_context",
    )
    pairs: list[tuple[str, str]] = []
    for name in fields:
        value = getattr(compile_context, name, None)
        if value is not None:
            pairs.append((name, str(value)))
    tokens = getattr(compile_context, "gcc_option_tokens", ())
    if tokens:
        pairs.append(("gcc_option_tokens", "\x00".join(str(t) for t in tokens)))
    return tuple(sorted(pairs))


def resolve_surface_backend(compile_context: CompileContext | None) -> str:
    """The header-AST backend the run's resolved compile context selects.

    One function, used for *both* the acquisition identity and the parse, so
    the two cannot disagree: a key that folded in a backend the parse did not
    actually use would let two genuinely different acquisitions share one
    surface. ``"auto"`` with no context, which is what every pre-existing
    caller already got.
    """
    return str(getattr(compile_context, "frontend", None) or "auto")


def build_side_identity(
    headers: Sequence[Path],
    includes: Sequence[Path],
    *,
    lang: str,
    exclude_headers: Sequence[str],
    public_header_dirs: Sequence[Path] | None,
    compile_context: CompileContext | None,
    depth: str | None,
    include_dependencies: bool,
) -> SurfaceAcquisitionIdentity:
    """The acquisition identity for one release side's header request.

    Every AST-affecting input the release fan-out has is folded in --
    header files and roots, exclusions, include paths, language, the
    resolved compile context (compiler/prefix/options/sysroot/nostdinc/
    frontend/frontend-context), depth, and the dependency-scope mode. Two
    sides with identical inputs therefore key identically, which is exactly
    what an ordinary directory comparison against one shared header tree
    wants: one acquisition, reused. Two sides (or two future per-member
    contexts) whose request genuinely differs key apart and each acquire
    their own surface.
    """
    files = tuple(sorted(str(Path(h).resolve()) for h in headers if Path(h).is_file()))
    dirs = tuple(sorted(str(Path(h).resolve()) for h in headers if Path(h).is_dir()))
    return SurfaceAcquisitionIdentity(
        header_files=files,
        header_dirs=dirs,
        exclude_headers=tuple(exclude_headers),
        includes=tuple(sorted(str(Path(i).resolve()) for i in includes)),
        public_header_dirs=tuple(
            sorted(str(Path(p).resolve()) for p in (public_header_dirs or ()))
        ),
        lang=lang or "",
        lang_explicit=bool(lang),
        backend=resolve_surface_backend(compile_context),
        frontend_context=str(
            getattr(compile_context, "frontend_context", None) or "host"
        ),
        compile_options=_compile_options(compile_context),
        depth=depth,
        include_dependencies=include_dependencies,
    )


def _member_evidence(
    library_results: Sequence[Mapping[str, Any]],
    side: str,
    expected_members: Collection[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Per-member export evidence and the *unread* members, for one side.

    Reads whichever of the two shapes the fan-out stashed -- the full
    ``AbiSnapshot`` (JUnit / ``--bundle-facts-out``) or the compact
    ``BundleSignatureEvidence`` it keeps otherwise -- so building the index
    never forces the release to retain every member's full snapshot.

    *expected_members* is what makes "unread" answerable, and it is not
    optional: ``library_results`` is not a list of members. The release
    appends pseudo-entries to it that are not libraries at all (a
    support-promise finding's entry is keyed by the retired/introduced
    *promise*, not by a DSO), and treating one as a member with no export
    evidence would mark the whole reconciliation incomplete -- which
    suppresses every real missing-export finding. So an entry counts here
    only when its ``library`` is a member this release actually expected;
    an expected member with no evidence is the real unread-provider case.
    """
    expected = set(expected_members)
    evidence: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for entry in library_results:
        library = str(entry.get("library", ""))
        if library not in expected:
            continue
        verdict = str(entry.get("verdict", ""))
        member = entry.get(f"_{side}_snapshot") or entry.get(f"_{side}_bundle_evidence")
        if verdict in _FAILED_MEMBER_VERDICTS or member is None:
            failed[library] = str(
                entry.get("error") or entry.get("reason") or f"member verdict {verdict}"
            )
            continue
        evidence[library] = member
    return evidence, failed


def reconcile_release_public_surface(
    library_results: Sequence[Mapping[str, Any]],
    *,
    expected_members: Collection[str],
    old_headers: Sequence[Path],
    new_headers: Sequence[Path],
    old_includes: Sequence[Path],
    new_includes: Sequence[Path],
    lang: str,
    exclude_headers: Sequence[str] = (),
    public_header_dirs: Sequence[Path] | None = None,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    include_dependencies: bool = False,
    has_baseline: bool = True,
) -> ReleaseSurfaceStage:
    """Acquire both sides' surfaces once and reconcile the product contract.

    *expected_members* names the DSOs this release expected to compare (see
    :func:`_member_evidence` for why it is required rather than inferred
    from *library_results*).

    Returns an inert stage object when the release has fewer than two
    expected members or no header inputs at all: with one member there is
    no union to take (the per-member check already answered it, unchanged),
    and with no headers there is no contract to reconcile.
    """
    ledger = SurfaceAcquisitionLedger()
    new_evidence, new_failed = _member_evidence(
        library_results, "new", expected_members
    )
    old_evidence, old_failed = _member_evidence(
        library_results, "old", expected_members
    )
    if len(set(expected_members)) < 2 or not new_headers:
        return ReleaseSurfaceStage(None, ledger, None, None)

    from ..extract.header_exclusions import apply_header_exclusions_to_inputs

    def _acquire(
        side: str, headers: Sequence[Path], includes: Sequence[Path]
    ) -> tuple[SurfaceAcquisitionIdentity, ReleasePublicSurface]:
        identity = build_side_identity(
            headers,
            includes,
            lang=lang,
            exclude_headers=exclude_headers,
            public_header_dirs=public_header_dirs,
            compile_context=compile_context,
            depth=depth,
            include_dependencies=include_dependencies,
        )
        # The same expansion + exclusion pair a member dump applies, in the
        # same order: a `-H` directory is expanded to its header files, then
        # `--exclude-header` narrows the list. Provenance still gets the
        # *unexpanded* inputs, so a `-H` directory keeps its directory-shaped
        # public scope (`header_utils.split_public_header_inputs`).
        from ..dry_run_estimate import expand_header_inputs

        try:
            expanded = apply_header_exclusions_to_inputs(
                expand_header_inputs([Path(h) for h in headers]),
                tuple(exclude_headers),
            )
        except Exception as exc:  # noqa: BLE001 -- an input fault is a fact
            # A header input that vanished, or a `-H` directory holding no
            # supported header, is an *unresolved surface* with its reason --
            # never an exception out of a reporting stage. Nothing was
            # acquired, so this deliberately does not go through the ledger:
            # its acquisition count must stay the number of parses that
            # really happened.
            return identity, unresolved_surface(
                acquisition_key=identity.key(),
                side=side,
                reason=f"public-header inputs could not be expanded: {exc}",
            )
        surface = acquire_release_surface(
            identity,
            side,
            ledger=ledger,
            headers=expanded,
            includes=[Path(i) for i in includes],
            # The resolved compile context reaches the *parse*, not only the
            # key. Hashing it while parsing without it is the worst of both:
            # two differently-configured runs key apart, and every one of
            # them acquires a surface that ignores the configuration the
            # member dumps honor -- so a declaration behind `#ifdef FEATURE`
            # with `compile.defines: [FEATURE]` set silently leaves the
            # product's contract, and its missing export stops being
            # reported at all (Codex security review, PR #1328). The backend
            # comes from the same resolved context, through the one function
            # the identity above also uses.
            compile_context=compile_context,
            backend=resolve_surface_backend(compile_context),
            public_headers=[Path(h) for h in headers if Path(h).is_file()],
            public_header_dirs=[
                *(Path(h) for h in headers if Path(h).is_dir()),
                *(Path(p) for p in (public_header_dirs or ())),
            ],
        )
        return identity, surface

    _, new_surface = _acquire("new", new_headers, new_includes)
    old_surface: ReleasePublicSurface | None = None
    if has_baseline and old_headers:
        _, old_surface = _acquire("old", old_headers, old_includes)

    new_index = build_bundle_export_index(
        "new", new_evidence, failed_members=new_failed
    )
    old_index = (
        build_bundle_export_index("old", old_evidence, failed_members=old_failed)
        if old_surface is not None
        else None
    )
    per_member_exports = {
        name: names
        for name, names in (
            (name, member_export_names(member)) for name, member in new_evidence.items()
        )
        if names is not None
    }
    reconciliation = reconcile_release(
        new_surface,
        new_index,
        old_surface=old_surface,
        old_index=old_index,
        undocumented_exports_by_member=undocumented_exports_by_member(
            new_surface, new_index, per_member_exports
        ),
    )
    return ReleaseSurfaceStage(reconciliation, ledger, old_surface, new_surface)


def release_surface_verdict(
    stage: ReleaseSurfaceStage, *, policy: str = "strict_abi"
) -> str:
    """The verdict the release-level findings alone imply.

    Applies the *same* two exclusions ``checker._compute_verdict_for``
    applies to a member comparison's own cross-source findings -- a
    ``RESOLVED`` finding and a ``PERSISTENT`` risk-category one do not drive
    a pairwise verdict (``policy.classification.
    excluded_from_verdict_as_persistent_hygiene`` carries the reasoning,
    including the Intel MKL case that forced it) -- and then the same
    ``compute_verdict`` the engine uses. Sharing both rules is what keeps a
    release-level finding gating exactly as the per-member finding it
    replaces did: moving a check's *owner* must not change what it gates.
    """
    from ..policy.classification import (
        compute_verdict,
        excluded_from_verdict_as_persistent_hygiene,
        policy_kind_sets,
    )
    from ..policy.evidence_status import is_cross_source_resolved

    findings = stage.findings
    if not findings:
        return "NO_CHANGE"
    sets = policy_kind_sets(policy)
    scored = [
        change
        for change in findings
        if not is_cross_source_resolved(change)
        and not excluded_from_verdict_as_persistent_hygiene(change, *sets)
    ]
    return compute_verdict(scored, policy=policy).value


def release_surface_severity_exit(
    stage: ReleaseSurfaceStage,
    severity: Any,
    *,
    policy: str = "strict_abi",
    policy_file: Any = None,
) -> int:
    """The severity-aware exit code the release-level findings alone imply.

    The severity-aware exit is aggregated from the *per-library* entries,
    which by construction cannot see a release-level finding -- the same
    blind spot ``_fold_release_global_severity`` already closes for bundle
    and probe-matrix findings, and for the same reason: without this, a
    release whose members are clean but whose public contract is missing an
    export would exit 0 under a severity preset while the verdict says
    otherwise. ``0`` when no severity setting is in effect, matching that
    function's own no-op contract.
    """
    if severity is None or not stage.findings:
        return 0
    from ..policy.severity import compute_exit_code

    return int(
        compute_exit_code(
            list(stage.findings),
            severity,
            policy=policy,
            policy_file=policy_file,
        )
    )
