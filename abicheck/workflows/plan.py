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

"""ADR-063 Phase 4: ``AnalysisPlan`` — pre-flight resolution, not mid-run
discovery.

An unsatisfiable request (an evidence requirement no resolved
collector/backend combination can produce) is rejected before extraction, with
a named reason, instead of discovered as a silent no-op mid-run or not
discovered at all. :func:`AnalysisPlanner.resolve` runs first inside
:func:`abicheck.service_compare_pipeline.resolve_compare_request` and
:func:`abicheck.service_dump_pipeline.resolve_dump_request` — before either
function touches a header-AST backend, a build-info adapter, or the
filesystem beyond the small, already-in-hand request paths a check needs to
classify (e.g. sniffing a ``--build-info`` file's own format) — so a request
that names an input a resolved side cannot use is rejected with a
:class:`~abicheck.errors.PlanningError` up front, not silently accepted and
then dropped somewhere inside extraction with no diagnostic at all.

**Scope, deliberately narrow (ADR-063 D4).** ``AnalysisPlan`` answers exactly
one question — is this request's requested evidence/toolchain input
satisfiable by some resolved collector/backend combination — and nothing
else:

* It carries the *requested* toolchain/compile-context inputs (an explicit
  ``gcc_path``/``--ast-frontend``/language, whatever ``build_info``/``sources``
  path was given), never the *resolved* P0.3 L3→L2 compile-context fold's
  output. That fold (``buildsource.l2_seed.seed_includes_and_fold_compile_context``)
  cannot be determined without invoking it, and it can raise
  ``HeaderCompileContextAmbiguousError`` on genuinely ambiguous build
  evidence — exactly why
  :class:`abicheck.service_dump_pipeline.ResolvedDumpRequest` already
  excludes the fold's result and runs it only inside
  :func:`~abicheck.service_dump_pipeline.execute_dump_request`, never during
  a side-effect-free resolve step. Running it here, before an
  ``AnalysisPlan`` is returned, would be the identical contract change to
  ``--dry-run``'s "never raises but a usage error" guarantee that decision
  already ruled out for that object; ``AnalysisPlan`` is bound by the same
  constraint.
* It carries no resolved policy, pack, or public-surface-contract state.
  Those answer a later question — how an already-extracted comparison is
  classified and scored — and for the native ``compare``/``scan`` CLIs that
  question is not even answerable at the point a plan is built:
  ``cli_compare_receipt.resolve_and_apply()`` (ADR-049 Phase 5) is a
  separate, Click-dependent step that runs strictly *after* snapshot
  resolution, over CLI-specific inputs (``--policy``/``--pack``/a discovered
  ``.abicheck.yml``) this module's plain ``CompareRequest``/``DumpRequest``
  input has no seam for. A plan field populated at the wrong point would be
  stale or incomplete for exactly the front end that most needs it — worse
  than not recording it.

**One planner, two request shapes.** :func:`AnalysisPlanner.resolve` accepts
either a :class:`~abicheck.api_types.CompareRequest` (two sides) or a
:class:`~abicheck.api_types.DumpRequest` (one side) and normalizes both into
the same per-side :class:`SidePlan` shape before running every registered
check — so a check that only cares about one side's own requested inputs
(every check today is exactly this shape) never needs to know which request
type it came from.

**Two named silent-failure scenarios motivate this phase (ADR-063 D4); one
check is implemented here.** ``docs/contribute/known-gaps.md``'s
``--build-target`` + pre-captured Bazel ``aquery``/``cquery`` entry is a real,
isolated, currently-unfixed silent no-op — :func:`_check_bazel_target_scoping`
below closes it. D4's second illustrative scenario, "a ``-H`` flag accepted
by a collect mode that cannot use it," does not correspond to any isolated,
currently-open known-gap entry once checked against the real code: the one
combination that literally matches that description —
``--depth binary`` combined with an explicit ``-H``/``headers`` value, which
resolves to collect mode ``"off"`` and silently clears the header list
(:func:`abicheck.service_compare_evidence._headers`,
:func:`abicheck.cli_dump_depth.resolve_dump_collect_context`) — is
intentional, already-shipped, reviewed behavior with its own dedicated
regression tests (``tests/test_cli_scan.py::test_depth_binary_clears_headers_in_scan``,
``tests/test_service_unit.py::test_depth_binary_clears_headers``,
``tests/test_typed_dump_request.py``, ``tests/test_depth_vocabulary.py``),
not an acknowledged gap. Turning it into a hard :class:`~abicheck.errors.PlanningError`
would be a real, unreviewed behavior change to already-tested surface, not a
same-phase fix for a documented silent failure — named explicitly here as
out of scope for this phase, rather than forced to fit, so a future reader
does not rediscover the same tension. A genuinely new, currently-silent
"input accepted by a collect mode that cannot use it" case would be a good
second candidate for :data:`_CHECKS` when one is found.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import PlanningError

if TYPE_CHECKING:
    from ..api_types import CompareRequest, DumpRequest, InputSpec

__all__ = [
    "AnalysisPlan",
    "AnalysisPlanner",
    "PlanningFailure",
    "SidePlan",
    "artifact_set_bazel_scoping_failure",
    "bazel_target_scoping_failure",
    "scan_bazel_scoping_failure",
]


@dataclass(frozen=True)
class PlanningFailure:
    """One evidence/extraction requirement no resolved combination can satisfy.

    ``requested`` names what the caller asked for (the flag/value); ``why_unsupported``
    states, in one sentence, which resolved collector/backend combination
    cannot honour it and why — the same two-field shape
    :class:`~abicheck.errors.PlanningError`'s own docstring describes.
    """

    requested: str
    why_unsupported: str

    def __str__(self) -> str:
        return f"{self.requested} ({self.why_unsupported})"


@dataclass(frozen=True)
class SidePlan:
    """The *requested* facts an :class:`AnalysisPlan` records for one side.

    Every field here is read straight off the request/``InputSpec`` — nothing
    is resolved, inferred, or fetched from the filesystem beyond the small,
    local classification a check needs to run (e.g. sniffing a
    ``build_info`` file's own on-disk format, which is a content sniff of an
    already-named path, not extraction). See this module's own docstring for
    why the resolved P0.3 compile-context fold and resolved policy are
    deliberately excluded.
    """

    label: str
    requested_depth: str | None
    lang: str
    frontend: str
    sources: Path | None
    build_info: Path | None
    build_targets: tuple[str, ...]
    gcc_path: str | None
    #: ``DumpRequest.resolved_collect_mode`` (``None`` for every ``compare``
    #: side, which has no such field) -- when set, it is what actually runs,
    #: overriding whatever ``requested_depth`` alone would resolve to.
    resolved_collect_mode: str | None = None
    #: The raw, requested ``InputSpec.headers`` -- *not* the effective
    #: (post depth=binary-clearing) list. Needed because a collect mode of
    #: ``"off"`` (whether resolved from ``requested_depth`` alone or from an
    #: explicit ``resolved_collect_mode`` override) does not by itself mean
    #: ``build_info`` is never consulted: the L2 seed's own independent
    #: header-seeding pass (``_seeded_includes_and_compile_context`` /
    #: ``collect_inline_pack``) still runs whenever real headers are present,
    #: regardless of collect mode.
    headers: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AnalysisPlan:
    """An immutable, pre-extraction record of one satisfiable request.

    Returned only once every registered check has passed — a caller that
    receives one never needs to re-check feasibility itself.
    """

    operation: str
    requested_depth: str | None
    sides: tuple[SidePlan, ...]


def _side_plan(
    label: str,
    side: InputSpec,
    depth: str | None,
    resolved_collect_mode: str | None = None,
) -> SidePlan:
    gcc_path = side.compile.gcc_path if side.compile is not None else None
    frontend = (
        side.compile.frontend
        if side.compile is not None and side.compile.frontend.lower() != "auto"
        else None
    )
    return SidePlan(
        label=label,
        requested_depth=depth,
        lang="",  # filled in by the caller, which knows the request-level lang
        frontend=frontend or "",
        sources=side.sources,
        build_info=side.build_info,
        build_targets=side.build_targets,
        gcc_path=gcc_path,
        resolved_collect_mode=resolved_collect_mode,
        headers=side.headers,
    )


def _replace_lang_frontend(plan: SidePlan, lang: str, frontend: str) -> SidePlan:
    from dataclasses import replace

    return replace(plan, lang=lang, frontend=plan.frontend or frontend)


def _discovered_config_build_targets(
    sources: Path | None,
    build_config: Path | None = None,
    *,
    headers_present: bool = False,
) -> tuple[str, ...]:
    """Auto-discovered (or explicit) ``.abicheck.yml``'s ``build.targets``.

    Mirrors ``embed_build_source``'s own ``cfg_path = build_config or
    discover_build_config(raw_sources)`` precedence exactly: an explicit
    *build_config* path (``scan``'s own ``ScanRequest.build_config`` /
    ``dump``/``compare``'s ``--config``) wins outright; otherwise falls back
    to auto-discovering one at *sources*. ``dump``/``compare`` have no
    request-level seam for the explicit half today -- no ``build_config``
    field exists on :class:`~abicheck.api_types.InputSpec`/
    :class:`~abicheck.api_types.DumpRequest`/
    :class:`~abicheck.api_types.CompareRequest` (see this module's own
    "Also not landed" status entry) -- so their own call sites always pass
    *build_config* as ``None`` and get the auto-discovery half only.
    Discovering and parsing a ``.abicheck.yml`` is a pure, deterministic,
    non-executing read (no subprocess, no ambiguity to raise on), so it fits
    the same side-effect-free constraint every other :data:`_CHECKS` entry
    already honors -- unlike the P0.3 L3->L2 compile-context fold, which this
    module's own docstring explains cannot run here.

    A malformed ``.abicheck.yml`` is deliberately swallowed to "no config
    found" here rather than raised as a second, independently-worded
    :class:`PlanningError` -- ``embed_build_source`` already raises a
    correctly-typed ``ValidationError`` for it at real-execution time
    (exit 64 either way), and duplicating that diagnosis pre-flight would be
    exactly the second-copy drift this phase exists to avoid, for a case
    that already fails loudly downstream.

    *headers_present* gates whether either pack shape (classic
    ``BuildSourcePack`` or Flow-2 ``abicheck_inputs``,
    :func:`~abicheck.buildsource.inputs_pack.is_any_pack_dir`) is skipped
    (Codex review, fresh evidence beyond the original pack-shape fix):
    ``embed_build_source``'s own *main* L3/L4/L5 collection never discovers a
    config at a pack dir (``raw_sources`` is ``None`` for either shape), but
    ``buildsource.l2_seed._l2_seed_config`` -- reached whenever real headers
    are present, independent of collect_mode
    (``seed_includes_and_fold_compile_context``'s own ``... or not headers:
    return ...`` gate) -- calls ``discover_build_config`` on the *original*
    ``sources`` path before pack recognition nulls it for that seed's own
    ``collect_inline_pack`` call, so a pack's bundled ``.abicheck.yml`` *is*
    genuinely consulted in that case, just by the L2 seed rather than the
    main collection. Skipping pack recognition unconditionally (the first
    version of this fix) therefore missed a real, reachable path -- both
    shapes are skipped only when *headers_present* is ``False``, matching
    exactly the condition that keeps the L2 seed from ever running at all.
    An *explicit* *build_config* is honored regardless of pack shape or
    headers, since it names the config file directly rather than searching
    *sources* for one -- the identical precedence order (`build_config or
    discover_build_config(sources)`) whichever consumer resolves it.
    """
    # Imported from the already-`extract`-classified `buildsource.inline`
    # (which re-exports both from `buildsource.build_config`), not from
    # `config_paths`/`buildsource.build_config` directly -- neither is
    # classified into a layer `workflows` may import from
    # (`scripts/check_architecture.py`'s `unclassified-import` gate).
    from ..buildsource.inline import discover_build_config, load_build_config

    if build_config is not None:
        cfg_path: Path | None = build_config
    else:
        if sources is None:
            return ()
        if not headers_present:
            from ..buildsource.inputs_pack import is_any_pack_dir

            if is_any_pack_dir(sources):
                return ()
        cfg_path = discover_build_config(sources)
    if cfg_path is None:
        return ()
    try:
        cfg = load_build_config(cfg_path)
    except ValueError:
        return ()
    return tuple(cfg.targets)


def bazel_target_scoping_failure(
    label: str,
    build_info: Path | None,
    build_targets: tuple[str, ...],
    sources: Path | None = None,
    build_config: Path | None = None,
    *,
    headers_present: bool = False,
) -> PlanningFailure | None:
    """Reject ``build_targets`` combined with a pre-captured Bazel jsonproto.

    ``docs/contribute/known-gaps.md``'s named gap: a pre-captured Bazel
    ``aquery``/``cquery`` ``--build-info`` jsonproto is parsed by
    ``buildsource.inline._maybe_collect_bazel_build_info``, which routes it to
    :class:`~abicheck.buildsource.adapters.bazel.BazelAdapter` with no
    ``targets`` parameter at all — every action/target in the captured graph
    is collected unconditionally, regardless of any requested root-target
    scope. A live ``bazel query`` (no pre-captured file) *does* scope
    correctly (``BazelAdapter.collect()``'s own live-query path), so this
    check only fires for the pre-captured combination, not for
    ``build_targets`` in general.

    Fixed here as "option 2" from that known-gap entry: reject the
    combination with a named reason, rather than "option 1" (teaching the
    adapter to filter an already-parsed action/target graph down to the
    requested roots' transitive closure) — the known-gap entry's own
    analysis found option 1 needs two structurally different algorithms for
    ``aquery`` versus ``cquery`` data and is a separate, larger feature, not
    a same-phase fix.

    A free function, not a method on :class:`SidePlan`/:class:`AnalysisPlan`
    — the known-gap entry names this same silent no-op on ``scan --against``
    too, and ``scan_engine.py``'s own candidate resolution (``_build_new_snapshot``)
    builds a raw ``InputSpec`` directly rather than a :class:`~abicheck.api_types.CompareRequest`/
    :class:`~abicheck.api_types.DumpRequest`, so it has no :class:`AnalysisPlan`
    of its own to resolve through. Exposing the check itself, over plain
    ``(build_info, build_targets)`` values, lets that call site reuse the
    identical logic instead of a second, independently-maintained copy.

    *sources*/*build_config*, when given, close the dry-run/execution parity
    gap this module's own docstring names for the config-sourced (no
    explicit ``--build-target``) case: an empty *build_targets* falls back
    to whatever root targets an auto-discovered (or explicitly named)
    ``.abicheck.yml``'s ``build.targets:`` declares (see
    :func:`_discovered_config_build_targets`), mirroring
    ``embed_build_source``'s own ``targets=list(build_targets) if
    build_targets else cfg.targets`` precedence exactly. *headers_present*
    forwards unchanged to that same function -- it governs only whether a
    *pack* directory at *sources* is skipped (see its own docstring); it
    does not otherwise change this function's behavior. Every existing
    caller keeps passing none of the three (all default to ``None``/``None``/
    ``False``), which reproduces the prior, request-level-flag-only
    behavior unchanged.
    """
    effective_targets = build_targets or _discovered_config_build_targets(
        sources, build_config, headers_present=headers_present
    )
    if not effective_targets or build_info is None:
        return None
    if not build_info.is_file():
        return None
    from ..buildsource.inline import sniff_build_info_format

    fmt = sniff_build_info_format(build_info)
    if fmt not in ("bazel_aquery", "bazel_cquery"):
        return None
    source_note = "" if build_targets else " (from an auto-discovered .abicheck.yml)"
    return PlanningFailure(
        requested=(
            f"build_targets={list(effective_targets)!r}{source_note} on the "
            f"{label!r} side"
        ),
        why_unsupported=(
            f"the {label!r} side's --build-info is a pre-captured Bazel "
            f"{fmt.removeprefix('bazel_')} jsonproto ({build_info}); "
            "root-target scoping only applies to a *live* `bazel query` "
            "(pass --sources/a workspace with no --build-info, or pre-capture "
            "the jsonproto already scoped to the desired targets before "
            "passing it as --build-info)"
        ),
    )


def scan_bazel_scoping_failure(
    headers: object,
    eff_depth: object,
    collect_mode: str,
    build_info: Path | None,
    build_targets: tuple[str, ...],
    sources: Path | None = None,
    build_config: Path | None = None,
) -> PlanningFailure | None:
    """The shared ``scan`` pre-flight guard for :func:`bazel_target_scoping_failure`.

    Used by both ``scan_engine.run_scan_core`` (per-member) and
    ``service_scan.run_scan_set`` (once, before discovery) so the two share
    one exemption rule, and the depth=binary header-clearing it depends on,
    rather than two independently-maintained copies. Exempt only when
    *neither* consumer can reach ``build_info`` at all: empty collection
    layers (``embed_build_source`` no-ops) AND no headers (the L2 seed's own
    independent ``collect_inline_pack`` call no-ops too -- ``--depth headers``
    keeps real headers, so it stays unexempted despite its own ``collect_mode``
    being ``"off"``, same as ``--depth binary``).

    *sources*/*build_config* forward to :func:`bazel_target_scoping_failure`
    unchanged -- see that function's own docstring for the config-sourced
    (no explicit ``--build-target``) fallback they enable, and for what
    *headers_present* (derived here, not accepted as a parameter) governs.
    Both default ``None``, reproducing the prior, request-level-flag-only
    behavior for any caller that doesn't pass them.
    """
    from ..buildsource.scan_levels import EvidenceDepth
    from ..buildsource.source_replay import collection_for_ci_mode

    effective_headers = [] if eff_depth is EvidenceDepth.BINARY else headers
    if not effective_headers and not collection_for_ci_mode(collect_mode)[1]:
        return None
    return bazel_target_scoping_failure(
        "candidate",
        build_info,
        build_targets,
        sources=sources,
        build_config=build_config,
        headers_present=bool(effective_headers),
    )


def _depth_implied_collect_mode(depth: str) -> str:
    """The collect mode an *explicit* ``depth`` value resolves to on its own.

    Mirrors ``service_compare_evidence._resolve_depth_collect_mode``'s
    explicit-depth branch (duplicated, not imported, for the same
    leaf-module reason that function's own docstring states) -- with an
    explicit depth, both ``dump``'s and ``compare``'s own resolvers ignore
    their differing "omitted depth" defaults and compute identically, so
    there is exactly one mapping to mirror. ``"binary"`` and ``"headers"``
    both resolve to ``"off"``; only ``"binary"`` additionally clears headers
    (see :func:`_check_bazel_target_scoping`'s own comment).
    """
    from ..buildsource.scan_levels import (
        EvidenceDepth,
        SourceScope,
        depth_to_method,
        level_to_collect_mode,
    )

    evidence_depth = EvidenceDepth(depth.lower())
    method = depth_to_method(evidence_depth)
    if method is None:
        return "off"
    return level_to_collect_mode(
        method, evidence_depth, source_scope=SourceScope.TARGET
    )


def _check_bazel_target_scoping(side: SidePlan) -> PlanningFailure | None:
    # ADR-063 Phase 4 (Codex review, fresh evidence): `depth="binary"`
    # resolves to collect_mode "off", and `embed_build_source` itself
    # no-ops before ever calling `collect_inline_pack` at that mode -- so
    # `build_info`/`build_targets` are never actually consulted regardless
    # of what they name, and rejecting them here would be a false positive
    # (`cli_scan.py`'s own `_normalize_depth_inputs` already prunes
    # `build_info` to `None` for this same depth on the `scan` side, which
    # is why this same false positive can't reach `scan_engine.py`'s call
    # to the free `bazel_target_scoping_failure` function below).
    #
    # A later Codex round found that raw depth alone is not the whole
    # story for `dump`: `DumpRequest.resolved_collect_mode`, when set,
    # overrides what `depth` alone would resolve to (Codex review, PR 3A
    # blocker 5 -- see that field's own docstring), and
    # `resolve_dump_request_evidence` honors the override. A request with
    # `depth="binary"` but `resolved_collect_mode="build"` therefore still
    # runs `collect_inline_pack` for real -- the override, not the raw
    # depth, decides whether `build_info` is ever consulted. `compare`
    # sides have no such field (`resolved_collect_mode` stays `None`),
    # so this only changes behavior for `dump`.
    #
    # A further Codex round found that even a genuine "off" collect mode
    # (raw depth=binary, or an explicit resolved_collect_mode="off"
    # override) is not sufficient on its own: the L2 seed's own independent
    # header-seeding pass (`_seeded_includes_and_compile_context` /
    # `collect_inline_pack`) runs whenever real headers are present,
    # regardless of collect mode -- mirroring `scan_bazel_scoping_failure`'s
    # own `headers or collection_for_ci_mode(...)[1]` rule above.
    # `depth="binary"` clears headers to empty before execution
    # (`service_compare_evidence._headers`) independent of any
    # `resolved_collect_mode` override, so that clearing is folded into
    # `effective_headers` here rather than re-derived from collect mode.
    #
    # A yet further Codex round found this still only equated "off" with
    # `depth="binary"` in the no-override branch -- but `depth="headers"`
    # resolves to collect mode "off" too (see `_depth_implied_collect_mode`).
    # A headerless `depth="headers"` request was therefore wrongly rejected:
    # neither `embed_build_source` (collect_mode "off") nor the L2 seed (no
    # headers to seed) would ever have consulted `build_info`. Only
    # `depth="binary"` additionally clears headers to empty; `depth="headers"`
    # keeps real headers, so `effective_headers` must still reflect them.
    is_binary = (
        side.requested_depth is not None and side.requested_depth.lower() == "binary"
    )
    effective_headers = () if is_binary else side.headers
    if side.resolved_collect_mode is not None:
        off = side.resolved_collect_mode == "off"
    elif side.requested_depth is not None:
        off = _depth_implied_collect_mode(side.requested_depth) == "off"
    else:
        off = False
    if off and not effective_headers:
        return None
    return bazel_target_scoping_failure(
        side.label,
        side.build_info,
        side.build_targets,
        sources=side.sources,
        headers_present=bool(effective_headers),
    )


def artifact_set_bazel_scoping_failure(
    depth: str | None,
    headers_present: bool,
    build_info: Path | None,
    build_targets: tuple[str, ...],
    sources: Path | None = None,
    build_config: Path | None = None,
) -> PlanningFailure | None:
    """The ``scan --artifact-set`` pre-flight guard (Codex review).

    ``cli_scan._run_artifact_set`` has no per-member resolved ``collect_mode``
    to check against at this point (each discovered member resolves its own
    tier/level independently, later, inside ``run_scan_set``) -- only the raw
    ``depth`` CLI flag and whether *any* member's header pair was given. Mirrors
    :func:`_check_bazel_target_scoping`'s own no-``SidePlan`` reasoning exactly
    (``depth="binary"``/``"headers"`` both resolve to collect_mode ``"off"``
    via :func:`_depth_implied_collect_mode`; only ``"binary"`` additionally
    clears headers) rather than ``scan_bazel_scoping_failure``'s, which needs a
    real, resolved ``EvidenceDepth``/``collect_mode`` pair this call site
    doesn't have yet.

    *depth* being ``None`` (omitted) cannot be resolved to "off"/"not off"
    here at all (Codex review, fresh evidence): an unset ``--depth``
    resolves per-member, later, via real risk scoring over each member's own
    change seed (``_resolve_auto_source_method``) -- exactly the kind of
    resolved value :class:`AnalysisPlan`'s own design (this module's
    docstring) excludes from a pre-flight check. Guessing "off" here risks
    the opposite, worse failure mode (rejecting a request ``run_scan_set``'s
    own later, correctly-resolved check would have accepted); guessing
    "not off" is what every caller before this fix already did, and is kept
    for an *explicit* ``build_targets`` -- a scope the caller stated
    outright, and the shape every existing test/caller since this phase's
    first slice already exercises. What changes for an unset *depth* is
    narrower: the *config-sourced* (``.abicheck.yml``-only, no explicit
    ``build_targets``) fallback is withheld by not forwarding
    *sources*/*build_config* at all -- trusting an auto-discovered scope
    this check cannot rule "reachable" or not for is the part that's newly
    added and newly risky; an explicit scope the caller named is not.
    """
    if depth is None:
        return bazel_target_scoping_failure("candidate", build_info, build_targets)
    is_binary = depth.lower() == "binary"
    effective_headers_present = False if is_binary else headers_present
    off = _depth_implied_collect_mode(depth) == "off"
    if off and not effective_headers_present:
        return None
    return bazel_target_scoping_failure(
        "candidate",
        build_info,
        build_targets,
        sources=sources,
        build_config=build_config,
        headers_present=effective_headers_present,
    )


#: Every registered pre-flight check, run against each side of a request in
#: order. A check returns ``None`` when it has nothing to say about *side*,
#: or a single :class:`PlanningFailure` when it does — one check, one
#: possible failure per side, matching every check registered here today.
_CHECKS: tuple[Callable[[SidePlan], PlanningFailure | None], ...] = (
    _check_bazel_target_scoping,
)


class AnalysisPlanner:
    """Resolves a request into an :class:`AnalysisPlan`, or raises
    :class:`~abicheck.errors.PlanningError`."""

    @staticmethod
    def resolve(request: CompareRequest | DumpRequest) -> AnalysisPlan:
        """Build the plan for *request*, running every registered check first.

        Raises:
            PlanningError: If any side fails any registered check. Every
                failure found is reported together (:attr:`PlanningError.failures`),
                not only the first.
        """
        from ..api_types import DumpRequest as _DumpRequest

        sides: tuple[SidePlan, ...]
        if isinstance(request, _DumpRequest):
            operation = "dump"
            sides = (
                _replace_lang_frontend(
                    _side_plan(
                        "input",
                        request.input,
                        request.depth,
                        request.resolved_collect_mode,
                    ),
                    request.lang,
                    request.frontend,
                ),
            )
        else:
            operation = "compare"
            sides = (
                _replace_lang_frontend(
                    _side_plan("old", request.old, request.depth),
                    request.lang,
                    request.frontend,
                ),
                _replace_lang_frontend(
                    _side_plan("new", request.new, request.depth),
                    request.lang,
                    request.frontend,
                ),
            )

        failures = [
            failure
            for side in sides
            for check in _CHECKS
            if (failure := check(side)) is not None
        ]
        if failures:
            raise PlanningError(tuple(failures))
        return AnalysisPlan(
            operation=operation, requested_depth=request.depth, sides=sides
        )
