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

"""Structured error hierarchy for abicheck.

All public exceptions inherit from AbicheckError, which itself extends
the built-in Exception class for easy catch-all error handling.

SuppressionError inherits both AbicheckError and ValueError so that
existing code catching ValueError continues to work without changes.
"""

from __future__ import annotations


class AbicheckError(Exception):
    """Base exception for all abicheck-specific errors."""


class ValidationError(AbicheckError, ValueError):
    """Raised when input data fails validation (schema, format, length limits).

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError.
    """


class SnapshotError(AbicheckError, RuntimeError):
    """Raised when an ABI snapshot cannot be loaded or parsed.

    Inherits RuntimeError for backward compatibility with existing code that
    catches RuntimeError from snapshot extraction.
    """


class HeaderToolchainError(SnapshotError):
    """Raised when a header-scoped source-mode parse fails on a known,
    diagnosable host-toolchain mismatch (plan G16).

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged — but a dedicated class so a caller
    that wants to branch on "this failure carries an actionable, precise
    remediation" (e.g. a sized-float/``__assume__``/``--lang c`` signature
    :func:`abicheck.dumper._castxml_failure_hint` recognised) can do so,
    instead of treating every castxml failure as equally opaque. The
    remediation text is already folded into the exception message.
    """


class IncompatibleSnapshotSchemaError(SnapshotError):
    """Raised when a persisted snapshot's ``schema_version`` is both newer
    than this reader's ``serialization.SCHEMA_VERSION`` and at or above
    ``serialization._MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` (ADR-050
    D1).

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged, the same precedent
    :class:`HeaderToolchainError` already documents — rather than a bare
    sibling of :class:`AbicheckError` that would fall through
    ``cli_resolve.py``'s existing ``except ValidationError, SnapshotError``
    translation and surface as an unhandled internal failure instead of a
    clean usage error.

    Below that threshold, ``snapshot_from_dict`` keeps today's lenient
    warn-and-continue behavior for an ordinary additive schema bump — this
    error exists only for the specific class of field (starting with
    ``ExtractionContract``) where silently reading past an unrecognized,
    verdict-blocking field would let an old reader compare two
    possibly-incomparable snapshots and produce an ordinary, wrong verdict.
    """


class ProfileMismatchError(AbicheckError):
    """Raised by :func:`abicheck.comparability.check_contracts_comparable`
    when both sides of a compare carry a ``profile_fingerprint`` and it
    differs (ADR-050 D2) — the two snapshots' resolved compile context
    (compiler/macros/include-search inputs) is not comparable, so ``compare``
    must report ``not_comparable`` instead of producing any verdict.
    """


class ScopeMismatchError(AbicheckError):
    """Raised by :func:`abicheck.comparability.check_contracts_comparable`
    when both sides of a compare do not cover the same declared surface, so
    ``compare`` must report ``not_comparable`` instead of producing any
    verdict. Two independent causes:

    - Both sides carry a ``scope_fingerprint`` and it differs (ADR-050 D2)
      — a manifest/CLI-flag drift between two extraction runs.
    - Both sides carry an explicit, differing
      :attr:`abicheck.model.AbiSnapshot.dependency_scope` (schema v18) — one
      side excludes toolchain/system-header declarations
      (``dumper_scoping.py``, ``dump``'s default) and the other does not.
    """


class ManifestValidationError(AbicheckError, ValueError):
    """Raised by :func:`abicheck.dump_manifest.load_manifest` for any
    parse-time schema violation of a ``--dump-manifest`` YAML document
    (ADR-050 D3): an unknown field, a duplicate mapping key, a duplicate TU
    ``name``, the ``contributes_to_abi=True ⇒ required=True`` invariant, a
    manifest-declared ``frontend_context`` other than ``"host"`` (Phase D,
    not this phase, adds a real selector for ``"device"``), or a malformed
    field value.

    Inherits ValueError for the same backward-compatibility reason
    :class:`SuppressionError`/:class:`PolicyError` do — a generic ``except
    ValueError`` around manifest loading still catches it. Deliberately not
    a subclass of :class:`SnapshotError`: this fires before any extraction
    is even attempted, over the manifest document alone, the same
    parse-time-vs-extraction-time distinction :class:`ProfileMismatchError`/
    :class:`ScopeMismatchError` draw against :class:`SnapshotError`.
    """


class AmbiguousLibraryMatchError(AbicheckError, ValueError):
    """Raised by :func:`abicheck.binary_utils.build_match_map` when two or
    more candidate files for one canonical library key are indistinguishable
    except by storage encoding (e.g. a plain ``libfoo.abicheck.json`` and a
    stale ``.gz``/``.zst`` sibling left over from a previous release, ADR-059)
    -- there is no information left in the filename to break the tie
    correctly, so this fails closed instead of guessing (Codex review, PR
    #699, second round on the same fix).

    A plain :class:`AbicheckError`/:class:`ValueError`, not a CLI-specific
    exception: :func:`build_match_map` is a pure matching primitive with
    callers outside any Click command (e.g. ``bundle_side_input.py``).
    ``cli_helpers_compare._build_match_map`` -- the CLI-facing wrapper every
    ``compare``/``compare-release`` call site already used before this
    class existed -- catches this and re-raises ``click.ClickException``
    with the identical message, so no existing CLI-facing behavior changes.
    """


class TuMergeError(SnapshotError):
    """Raised by :func:`abicheck.tu_merge.merge_fragments` when two
    translation-unit fragments from one manifest-driven dump declare the
    same :func:`abicheck.tu_fragment.entity_key` in a way that cannot be
    trivially reconciled (ADR-050 D4, G32 Phase C).

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling around ``dump()``/``compare()`` already treats it as an
    ordinary extraction failure, the same precedent :class:`HeaderToolchainError`/
    :class:`IncompatibleSnapshotSchemaError` already document, matching the
    ADR's own "producing an extraction failure the same way a required TU's
    compile failure already does" (D3). ``code`` is one of:

    - ``"INCONSISTENT_DECLARATION"`` — two TUs' declarations for the same
      entity disagree in a way that isn't a forward-declaration/definition
      pair, a plain redeclaration, or a default-argument-only difference
      (e.g. differing return type, layout, or calling convention).
    - ``"HETEROGENEOUS_ABI_CONTEXT"`` — the manifest's *declared*
      compiler/target is uniform (``dump_manifest.py``'s parse-time rule
      already guarantees that, D3), but the TUs were extracted by
      different AST producers -- e.g. ``--ast-frontend auto``'s per-TU
      fallback landed one TU on castxml and another on clang within the
      same manifest.

    These are extraction-time conflict codes, not
    :class:`~abicheck.checker_policy.ChangeKind` members, despite reading
    like one — see :mod:`abicheck.tu_merge`'s module docstring for why.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        entity_key: tuple[str, str],
        tu_names: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.entity_key = entity_key
        self.tu_names = tu_names


class AstContextMissingError(SnapshotError):
    """Raised by :mod:`abicheck.sycl_context` when no decoded AST context
    matches the requested ``frontend_context`` ``kind`` (ADR-050 D5, G32
    Phase D) — either a DPC++-capable invocation's document stream contains
    zero contexts of the requested ``kind`` (e.g. only a ``device`` context
    when ``host`` was requested, or a broken/truncated toolchain invocation
    decoded to nothing), or the invocation was positively identified as a
    plain, non-SYCL frontend and something other than the default ``host``
    was requested — there is no device-context stream to select from at
    all in that case, the same underlying condition.

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling around ``dump()``/``compare()`` already treats it as an
    ordinary extraction failure, the same precedent :class:`HeaderToolchainError`
    documents. A dedicated class (rather than a bare :class:`SnapshotError`)
    so a caller building a remediation message can distinguish "nothing to
    select" from :class:`AstContextAmbiguousError`'s "too much to select
    from without a tiebreaker" — the two need different guidance.
    """


class AstContextAmbiguousError(SnapshotError):
    """Raised by :mod:`abicheck.sycl_context` when more than one decoded AST
    context shares the requested ``frontend_context`` ``kind`` (ADR-050 D5,
    G32 Phase D) — e.g. a multi-target DPC++ invocation producing two
    distinct ``device`` contexts for different offload targets. There is no
    implicit tiebreaker; the caller must narrow the request (e.g. to a
    specific target) rather than have one context silently chosen for them.

    A subclass of :class:`SnapshotError`, mirroring :class:`AstContextMissingError`'s
    reasoning for why existing ``except SnapshotError`` handling still
    catches it unchanged while remaining a dedicated, branchable class.
    """


class HeaderCompileContextAmbiguousError(SnapshotError):
    """Raised by :mod:`abicheck.buildsource.header_compile_context` (P0.3) when
    two or more L3 ``CompileUnit``s that reference a public header disagree on
    an ABI-relevant compile-context field (``-std=``, target triple, defines/
    undefines, include search order, sysroot, or an ABI-relevant flag such as
    ``-fPIC``/``-fno-omit-frame-pointer``).

    Mirrors :class:`AstContextAmbiguousError`'s reasoning: there is no sound
    "just pick one" tiebreaker for genuinely disagreeing evidence, so the
    single-context L3→L2 threading fails closed instead of silently deriving
    a header AST from one TU's context while another, differently-configured
    TU also compiles the same header. The caller must narrow the input (e.g.
    a ``compile_db_filter``, or an explicit ``--gcc-options``/``compile:``
    block override that pins the ambiguous field) rather than have one
    compile unit's context silently chosen for them.

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling around ``dump()``/``compare()`` (``cli_resolve.py``) already
    converts it to a clean ``click.ClickException`` instead of a raw
    traceback.
    """


class UnsupportedCastxmlVersionError(SnapshotError):
    """Raised when a CastXML build outside the supported version range would
    be used for an authoritative L2 scan, before any header is parsed.

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged. See :mod:`abicheck.castxml_policy`
    for the version range and the explicit ``allow_unsupported`` override
    that turns this hard failure into a degraded, clearly-flagged snapshot
    instead.
    """


class SuppressionError(AbicheckError, ValueError):
    """Raised for invalid suppression rules or patterns.

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError from SuppressionEngine.
    """


class PolicyError(AbicheckError, ValueError):
    """Invalid policy configuration.

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError from policy validation.
    """


class PackManifestError(AbicheckError, ValueError):
    """Invalid ADR-049 D8 compatibility/contract/gate pack manifest.

    A sibling of :class:`PolicyError`, not a subclass -- a pack manifest is a
    distinct configuration artifact from a ``--policy-file`` document (ADR-049
    D8: "Policy bases, rule packs, gate packs, contract/language packs ... are
    distinct"), even though both are hard load-time errors for the same
    reason (an unknown ``ChangeKind`` slug must never be silently skipped).
    Inherits ValueError for the same backward-compatible catch-all reason
    every other error in this module does.
    """


class PlanningError(AbicheckError, ValueError):
    """Raised by :func:`abicheck.workflows.plan.AnalysisPlanner.resolve` when a
    request cannot be satisfied by any resolved collector/backend combination
    (ADR-063 D4/Phase 4).

    An :class:`~abicheck.workflows.plan.AnalysisPlan` is built *before* any
    extraction runs — no collector or header-AST backend has been invoked yet
    — precisely so a request that names an evidence input a resolved side
    cannot use is rejected up front, with a named reason, instead of silently
    accepted and then dropped somewhere inside extraction with no diagnostic
    at all (the failure mode this decision exists to close; see
    ``docs/contribute/known-gaps.md``'s ``--build-target`` + pre-captured
    Bazel ``aquery``/``cquery`` entry for the motivating case).

    Carries every failed requirement found for the request, not only the
    first — :attr:`failures` is a non-empty tuple of
    :class:`~abicheck.workflows.plan.PlanningFailure`, each naming what was
    *requested* and *why* no resolved combination can honour it.

    Inherits ``ValueError`` for the same backward-compatibility reason every
    other error in this module does — a generic ``except ValueError`` around
    request resolution still catches it.
    """

    def __init__(self, failures: tuple[object, ...]) -> None:
        self.failures = failures
        message = "; ".join(str(f) for f in failures)
        super().__init__(f"request cannot be satisfied: {message}")


class UseCaseManifestError(AbicheckError, ValueError):
    """Raised by :func:`abicheck.impact.use_cases.load_use_case_manifest` for
    a structurally malformed ``impact-use-cases.yaml`` document (G29 Phase 4
    slice 2, ADR-057 amendment): not a YAML list at the top level, an entry
    that isn't a mapping, or an entry with a missing/blank ``use_case`` name.

    Inherits ValueError for the same backward-compatibility reason
    :class:`PolicyError`/:class:`ManifestValidationError` do. Deliberately a
    sibling of those, not a subclass: an ``impact-use-cases.yaml`` document is
    a distinct, unrelated configuration artifact (declared consumer use
    cases, not verdict policy or a dump manifest).

    A single manifest entry naming an ``entrypoints`` symbol the library
    graph cannot resolve is **not** this error — that degrades silently to
    "no node/edge for this entry", per the same "absence, never a wrong
    answer" discipline :mod:`abicheck.impact.consumer_graph` already follows
    (ADR-057). This error is only for the manifest document itself being
    unreadable as the schema it claims to be.
    """


class ReportError(AbicheckError):
    """Error during report generation."""


class ExtractionSecurityError(AbicheckError):
    """Raised when archive extraction encounters a security violation.

    Triggered by path traversal attempts, symlinks escaping the extraction
    root, or other unsafe archive member paths.
    """

    def __init__(self, member_path: str, reason: str) -> None:
        self.member_path = member_path
        self.reason = reason
        super().__init__(f"Unsafe archive member '{member_path}': {reason}")
