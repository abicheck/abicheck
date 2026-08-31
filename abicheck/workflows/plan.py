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
    "bazel_target_scoping_failure",
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


@dataclass(frozen=True)
class AnalysisPlan:
    """An immutable, pre-extraction record of one satisfiable request.

    Returned only once every registered check has passed — a caller that
    receives one never needs to re-check feasibility itself.
    """

    operation: str
    requested_depth: str | None
    sides: tuple[SidePlan, ...]


def _side_plan(label: str, side: InputSpec, depth: str | None) -> SidePlan:
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
    )


def _replace_lang_frontend(plan: SidePlan, lang: str, frontend: str) -> SidePlan:
    from dataclasses import replace

    return replace(plan, lang=lang, frontend=plan.frontend or frontend)


def bazel_target_scoping_failure(
    label: str, build_info: Path | None, build_targets: tuple[str, ...]
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
    """
    if not build_targets or build_info is None:
        return None
    if not build_info.is_file():
        return None
    from ..buildsource.inline import sniff_build_info_format

    fmt = sniff_build_info_format(build_info)
    if fmt not in ("bazel_aquery", "bazel_cquery"):
        return None
    return PlanningFailure(
        requested=f"build_targets={list(build_targets)!r} on the {label!r} side",
        why_unsupported=(
            f"the {label!r} side's --build-info is a pre-captured Bazel "
            f"{fmt.removeprefix('bazel_')} jsonproto ({build_info}); "
            "root-target scoping only applies to a *live* `bazel query` "
            "(pass --sources/a workspace with no --build-info, or pre-capture "
            "the jsonproto already scoped to the desired targets before "
            "passing it as --build-info)"
        ),
    )


def _check_bazel_target_scoping(side: SidePlan) -> PlanningFailure | None:
    return bazel_target_scoping_failure(side.label, side.build_info, side.build_targets)


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
                    _side_plan("input", request.input, request.depth),
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
