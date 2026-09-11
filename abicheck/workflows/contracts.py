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

"""Typed request/response structs for the Tier-2 service layer (ADR-037 D2;
ADR-061 gap B).

"Options are data, not signatures": the service verbs take frozen request
dataclasses instead of an ever-growing list of keyword arguments. A new feature
becomes a new field with a default, never a signature break — and the same
struct is assembled identically from CLI flags, the typed Python API, and
direct Python callers, so a default can no longer silently diverge between
front-ends (the ``scope_public`` True-vs-False drift ADR-037 §Context #1
documents).

The real owner behind the flat ``abicheck.api_types`` compatibility
facade's pair/result half: :class:`OutputSpec`, :class:`CompareRequest`
(with :meth:`CompareRequest.validate`), :class:`DumpRequest`, and
:class:`CompareResult`. :class:`~abicheck.workflows.request_inputs.
InputSpec` -- the per-side operand these build on -- lives in the sibling
module :mod:`abicheck.workflows.request_inputs`, split out purely to keep
each module under the 800-line new-file ceiling.

ADR-055 D1 (``InputSpec.sources``/``build_info``/``dump_manifest``/
``compile``/``public_header_dirs`` and ``CompareRequest.depth``/
``frontend_context``) closes the gap that ADR's own Gap 1 documents:
``CompareRequest`` previously had no way to express ``compare``'s
``--depth``/``--sources``/``--build-info``/``--dump-manifest``/per-side
``CompileContext`` feature set at all, so a Python caller wanting that had
to fall back to loose keyword arguments on lower-level functions.
``service.run_compare_request`` reads these new fields directly (see its own
docstring for exactly how). A second D1 slice then closed the rest of the
gap against the CLI's own, then separately-maintained
``cli_resolve._resolve_compare_snapshots`` — ``dwarf_only``,
``debug_format``, ``include_labels``, and ``--follow-deps``. A third slice
then removed that second implementation outright: ``run_compare_request``
was split into its two phases (``service_compare_pipeline``), which gave the
CLI's Click-dependent ADR-049 ``resolve_and_apply`` step a seam to run in —
the thing that had made a shared resolution look impossible — and
``_resolve_compare_snapshots`` now builds one of these requests and
delegates. So this really is the one resolution every front end uses.
ADR-055's "Two-resolution-path finding", first answered as option (b), is
recorded as settled the other way in D1's "Structural half" note.

ADR-055 D2 adds :class:`CompareResult`, the result side of the same pair, and
D4 adds ``InputSpec.follow_linker_scripts`` — see each one's own docstring.
"""

from __future__ import annotations

import math
from dataclasses import KW_ONLY, dataclass, field, replace
from pathlib import Path
from typing import Any

from ..change_registry_types import Verdict
from ..checker_types import DiffResult
from ..environment_matrix import EnvironmentMatrix
from ..errors import ValidationError
from ..model import AbiSnapshot
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import VALID_BASE_POLICIES
from ..policy.exit_decision import ExitDecision
from ..policy.severity import SeverityConfig
from ..suppression import SuppressionList
from .request_inputs import (
    _ANDROID_NEEDS_SOURCES,
    FRONTEND_CONTEXTS as FRONTEND_CONTEXTS,
    HEADER_AST_FRONTENDS as HEADER_AST_FRONTENDS,
    SUPPORTED_DEBUG_FORMATS as SUPPORTED_DEBUG_FORMATS,
    SUPPORTED_FRONTENDS as SUPPORTED_FRONTENDS,
    SUPPORTED_LANGS as SUPPORTED_LANGS,
    InputSpec,
    _debug_format_errors,
    _depth_errors,
    _lang_errors,
    _path_required_errors,
    _resolved_collect_mode_errors,
    _side_errors,
    _source_only_binary_depth_errors,
    frontend_context_errors,
    frontend_value_errors,
    required_path as required_path,
)


@dataclass(frozen=True)
class OutputSpec:
    """Where/how a result is rendered — the invocation-level output choice.

    ``path is None`` means "write to stdout". Kept deliberately small for
    Phase 1; the rendering verbs still take an explicit format today, but the
    struct gives later phases a single place to grow output options.
    """

    fmt: str = "text"
    path: Path | None = None


@dataclass(frozen=True)
class CompareRequest:
    """A fully-specified comparison request — the single input to ``run_compare``.

    Every front-end (CLI, MCP, ``compare-release`` fan-out, ``appcompat``)
    assembles one of these and hands it to :func:`abicheck.service.run_compare`,
    so there is exactly one classification path and one set of defaults.
    """

    old: InputSpec
    new: InputSpec
    lang: str = "c++"
    frontend: str = "auto"
    has_sources: bool = False
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    suppress: Path | None = None
    scope_public: bool = True
    force_public_symbols: frozenset[str] | None = None
    # `compare --post-manifest`: the committed `pp_*`/ufunc-loop surface of a POST
    # manifest. When set, the comparison is scoped to this set — export findings
    # outside it (e.g. private `__pp_*` kernel churn) are demoted. None = not
    # manifest-scoped.
    public_surface_allowlist: frozenset[str] | None = None
    pattern_verdicts: bool = False
    enable_debuginfod: bool = False
    # Override debuginfod server URL (only meaningful with enable_debuginfod);
    # None uses the resolver's default server list / DEBUGINFOD_URLS env var.
    debuginfod_url: str | None = None
    # No ``reconcile_build_context`` field: one-comparison-product.md §4.1's
    # AUTO row made ADR-039's reconciliation unconditional, forced on in the
    # Tier-2 ``compare_snapshots`` chokepoint, so neither this request nor
    # the CLI carries a switch for it (still evidence-gated: a no-op without
    # ``build_context_defines`` + per-field ``guard`` annotations).
    #
    # Codex review, PR #1180 ("Prevent positional CompareRequest arguments
    # from shifting"): removing a field from the *middle* of a positional
    # dataclass silently rebinds every field after it for a positional
    # caller instead of failing loudly — the exact PR #582 lesson the
    # ``lang_explicit``/``pack_policy_overrides`` fields below already
    # guard against for a *new* field, but nothing protected a *removed*
    # one until now. A ``KW_ONLY`` sentinel right here, at the boundary
    # ``reconcile_build_context`` itself used to sit on, makes every field
    # from this point on keyword-only: a positional caller who used to
    # reach as far as ``reconcile_build_context`` now gets an immediate
    # ``TypeError`` at construction instead of a silently shifted value,
    # while every caller within the documented ``CompareRequest(old, new,
    # "c++", "clang", ...)`` shape (this file's own example, well short of
    # this boundary) is unaffected.
    _: KW_ONLY
    # ADR-020b: declared deployment constraints (EnvironmentMatrix YAML).
    # When its ``runtime_floors`` are set, new symbol-version requirements
    # classify against the declared floors (≤ floor → COMPATIBLE, > floor →
    # BREAKING) instead of the default deployment-RISK verdict. Demoted off
    # the CLI (this PR, ADR-068 D5): the former ``--env-matrix FILE`` is now
    # ``.abicheck.yml``'s ``deployment:`` config key, resolved once by
    # ``resolve_compare_config`` into a real ``EnvironmentMatrix`` -- so this
    # field now carries the already-*resolved* value, not a path to load,
    # with no surviving CLI flag of its own. Kept as a genuine
    # ``CompareRequest`` field (rather than dropped like
    # ``bundle_system_providers``/``fail_on_removed_library``, which never
    # reach ``classify_compare_pair`` at all) because it is a per-comparison
    # classification input, not a release-level gating knob -- the same
    # reasoning that keeps ``collapse_versioned_symbols``/
    # ``public_header_dirs`` as real fields here: both need a genuine
    # channel into the directory/package release fan-out's per-library
    # ``compare_snapshots`` call (``service_compare_pipeline.run_compare`` /
    # ``cli_compare_release_pairwise._run_compare_pair``), which a config-
    # only value with no request field cannot reach.
    env_matrix: EnvironmentMatrix | None = None
    # ADR-050 D2: force a tentative diff through a genuine comparability-
    # contract mismatch (scope/profile fingerprint drift) instead of the
    # default hard ``ProfileMismatchError``/``ScopeMismatchError``. Opt-in;
    # the resulting ``DiffResult.assurance`` is stamped ``"none"`` so a
    # caller can still see a result but knows not to trust it. Forwarded
    # to ``checker.compare``'s own ``diagnostic_comparison`` parameter.
    diagnostic_comparison: bool = False
    # ADR-049 Phase 3: stamp each finding's shadow, non-authoritative
    # contract-relevance decision (``Change.contract_relevance``/
    # ``contract_reason_code``/``contract_assurance``). Opt-in; changes no
    # verdict, severity, or exit code. Forwarded to ``checker.compare``'s
    # own ``contract_evaluation`` parameter -- previously reachable only by
    # calling the Tier-1 core directly, which no front-end may do
    # (``cli-contract`` AI-readiness gate, ADR-037 D10.1), so this field is
    # what makes the shadow evaluator reachable through the real Tier-2
    # chokepoint at all (Codex review, fresh evidence).
    contract_evaluation: bool = False
    # ADR-049 Phase 6: which evidence domain `contract_evaluation` judges
    # against -- "public" (header-derived declared surface), "exports" (the
    # binary's own export table plus the raw type closure from it), or "all"
    # (no root/closure evidence required). `None` keeps the legacy derivation
    # from `scope_public`; an explicit value outranks it per ADR-049 D7
    # (`explicit_cli` > `legacy_alias`). Selects the domain only -- like
    # `contract_evaluation` itself, non-authoritative for verdict/exit code.
    contract_mode: str | None = None
    # ADR-055 D1: the friendly evidence-depth dial (`--depth`, same vocabulary
    # as `dump`/`scan`: binary/headers/build/source). `None` (the default)
    # infers the collect mode from whether either side sets `sources`/
    # `build_info` instead of defaulting to "off" -- matching the CLI's own
    # `--depth`-omitted inference (P1 fix in `cli_compare_helpers.py`), not
    # a fixed default. `"binary"` also clears both sides' `headers` before
    # resolving (matching the CLI's identical `depth == "binary"` handling),
    # since a binary-only depth request that still carries headers would
    # otherwise silently keep running L2.
    depth: str | None = None
    # ADR-068 §3 #19, absorbed from `ScanRequest.budget`: a wall-clock guard
    # (seconds; `None` = unbounded); `run_compare_request` raises
    # `deadline.DeadlineExceeded` on overflow. `kw_only` (CodeRabbit review)
    # since it's inserted before positional fields below (`dwarf_only` etc).
    budget_s: float | None = field(default=None, kw_only=True)
    # ADR-055 D1, second slice: the last four concepts `compare`'s own
    # resolution (`cli_resolve._resolve_compare_snapshots`) could express and
    # this request could not, so a Python/MCP caller had to drop to loose
    # kwargs on `resolve_input` to reach them. All both-sides, mirroring the
    # CLI flags they come from, which are single-valued too.
    #
    # `--dwarf-only` / `--debug-format`: restrict a side's debug-info parse to
    # DWARF, or pin which debug format is read, instead of auto-detecting.
    dwarf_only: bool = False
    # Validated against SUPPORTED_DEBUG_FORMATS and lowercased before use, so a
    # typo fails through this module's ValidationError contract rather than a
    # raw ValueError deep in extraction, and "DWARF" works here exactly as it
    # does for the CLI's case-insensitive choice.
    debug_format: str | None = None
    # ADR-050 D1's resolved `path -> label` map for a labeled include set.
    # A tuple of pairs rather than a `dict` so the request stays hashable, the
    # property `InputSpec`'s own docstring calls out; `run_compare_request`
    # converts it back for `resolve_input`.
    include_labels: tuple[tuple[Path, str], ...] = ()
    # `--follow-deps` / `--search-path` / `--ld-library-path`: after both
    # sides resolve, populate each ELF side's transitive `DependencyInfo`.
    # Off by default, matching the CLI flag: it costs a full dependency-graph
    # resolution per side, so it stays opt-in rather than becoming a silent
    # cost for every typed caller.
    follow_dependencies: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    # ADR-055 D1 / ADR-050: request-level default for `CompileContext.
    # frontend_context` (`--frontend-context`, host|device), applied to a
    # side whose own `InputSpec.compile.frontend_context` reads as the class
    # default ("host") -- `CompileContext.frontend_context` has no "unset"
    # representation, so this can't distinguish a side that explicitly wants
    # "host" from one that just never touched the field; see
    # `service_compare_evidence._compile_context`'s own docstring for the
    # accepted limitation and how to work around it.
    frontend_context: str = "host"
    # G31 Phase C follow-up: `lang` alone cannot say whether the caller
    # genuinely wants this language forced or is just leaving the field at
    # its dataclass default (the CLI has the identical problem with Click's
    # `--lang` default — see `cli.dump_cmd`'s `lang_explicit` and AGENTS.md's
    # "dump --lang c++ is silently discarded ..." known gap). `False` (the
    # default) preserves the pre-existing behavior exactly: `resolve_input`
    # auto-detects unless `lang == "c"`. Set `True` when `lang` reflects a
    # real, deliberate request (e.g. forwarded from a genuine CLI `--lang`)
    # so the header-AST pass honors it even on a language-ambiguous header.
    # `kw_only=True` (not a positional field, and appended at the true end
    # rather than inserted mid-list) so an existing positional caller of this
    # documented public request type — `CompareRequest(old, new, "c++",
    # "clang", ...)` — keeps binding every field to what it always did,
    # instead of silently shifting onto this new one (Codex review; the
    # identical PR #582 lesson AGENTS.md's `Change`-dataclass entry already
    # documents for exactly this mistake).
    lang_explicit: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B slice 1: a caller that has already resolved
    # a ``--pack``'s ``policy.overrides``/``surface.internal_namespaces``
    # contributions (ADR-049 D8's ``pack_application.PackApplication``) can
    # hand them over here instead of dropping to Tier-1 directly. Before this
    # field existed, ``policy_file_path`` was the *only* channel this typed
    # request had for policy configuration, and it names a file on disk —
    # there was no way to say "apply these already-resolved overrides on top
    # of it" without either writing a synthetic policy file to disk or
    # calling ``checker.compare``/``compare_snapshots`` directly, which the
    # ``cli-contract`` AI-readiness gate forbids for any ``cli*.py`` module.
    # That gap is exactly why the directory/package release fan-out rejected
    # ``--pack`` outright (see ``cli_compare_options._reject_set_input_flags``)
    # while the single-pair ``compare`` CLI could already apply one: the
    # single-pair path never went through this typed request for that step,
    # calling ``compare_snapshots`` directly with an already pack-folded
    # ``PolicyFile`` object. Folded into the loaded ``PolicyFile`` by
    # ``service_compare_pipeline.classify_compare_pair`` via
    # ``pack_application.policy_file_with_packs`` — the exact same function
    # the single-pair CLI path already uses — so every caller that reaches
    # ``classify_compare_pair`` (which is every ``CompareRequest`` consumer,
    # ``run_compare_request``'s own two phases included) applies a pack's
    # policy/contract-surface contributions identically, rather than each
    # front end re-deriving its own application. ``None``/empty is a no-op:
    # every pre-existing caller that never sets this field is unaffected.
    # Deliberately narrower than the full ``PackApplication`` (no
    # ``exit_code_scheme``/``severity_levels``): those gate-shaped fields
    # need their own resolved gate-options wiring the release fan-out does
    # not have yet — see the plan's "PR B" section.
    # A tuple of pairs, not a `dict` -- the same reason `include_labels`
    # above is a tuple of pairs rather than a `dict`: this dataclass is
    # frozen and Python derives `__hash__` from its fields, which a `dict`
    # field would silently break the moment one was actually populated
    # (verified: `CompareRequest(...)` hashes fine with every pre-existing
    # field, and stops the moment this one carries a real `dict`).
    pack_policy_overrides: tuple[tuple[ChangeKind, Verdict], ...] | None = field(
        default=None, kw_only=True
    )
    #: A resolved ``.abicheck.yml`` ``policy.overrides`` contribution
    #: (ADR-068 §3 #23), weaker than an explicit ``--policy``/pack entry --
    #: see ``policy.policy_file_project_overrides``. ``None``/empty: no-op.
    project_policy_overrides: tuple[tuple[ChangeKind, Verdict], ...] | None = field(
        default=None, kw_only=True
    )
    #: ``surface.internal_namespaces`` when a pack supplied it — see
    #: ``pack_policy_overrides`` above for why this field exists and how it
    #: is applied. ``None`` means "no pack stated this"; distinct from an
    #: empty tuple, which is a pack's explicit "this project has none".
    pack_internal_namespaces: tuple[str, ...] | None = field(default=None, kw_only=True)
    #: The one automatic gate algorithm (ADR-064/CLI cleanup phase two PR
    #: G2) is fully determined by whether a severity setting is in effect
    #: -- there is no ``exit_code_scheme`` field here to force one direction
    #: or the other (removed in PR G2 along with the CLI flag, the
    #: ``.abicheck.yml`` key, and the pack field it used to mirror).
    severity_preset: str | None = field(default=None, kw_only=True)  # ADR-064/PR G2
    #: ADR-068 / plan §3 #12 (Phase 2c): the resolved changed-path seed
    #: (``compare --changed-path``, or ``--since`` after its ``git diff``).
    #: Scoping input only -- it produces no finding of its own; it narrows
    #: the L4/L5 points of interest a ``--depth source`` run examines, per
    #: ADR-043 D7 (changed-path scope when a seed exists, else the current
    #: library target -- never a zero-TU no-op). Empty = no seed.
    changed_paths: tuple[str, ...] = field(default=(), kw_only=True)
    #: ADR-068 D3 / plan §3 #15 (Phase 2d): the target ``Py_LIMITED_API``
    #: floor for the candidate-side stable-ABI audit (``compare --abi3``),
    #: as ``(major, minor)``. ``None`` (the default) leaves the audit off,
    #: so every pre-existing request is unchanged.
    abi3_floor: tuple[int, int] | None = field(default=None, kw_only=True)
    #: One-comparison-product Phase 4 commit 2 (ADR-068, ADR-055 amendment):
    #: absorbed from ``ScanRequest.collapse_versioned_symbols`` -- a genuine
    #: gap, not a mirrored duplicate. ``checker.compare()``/
    #: ``compare_snapshots()`` have accepted this parameter since G15, and
    #: ``ScanRequest``/``scan --against`` could already reach it
    #: (``scope.collapse_versioned_symbols`` in a project config, or the
    #: typed field directly), but ``CompareRequest`` had no field for it at
    #: all -- a typed ``compare`` caller had no way to opt into version-
    #: rename-pair collapsing (G15) short of calling ``compare_snapshots``
    #: directly, which the ``cli-contract`` gate forbids for a front-end
    #: module. ``False`` (the default) reproduces every pre-existing
    #: request's behavior unchanged.
    collapse_versioned_symbols: bool = field(default=False, kw_only=True)
    #: One-comparison-product Phase 4 (ADR-068, ADR-055 amendment): absorbed from ``ScanRequest.allow_build_query`` -- the one field that amendment's field-by-field audit listed as a real, open ``CompareRequest`` gap and that survived ADR-068's second 2026-09-09 ruling table (``risk_rules_path``/``build_targets``/``bundle_system_providers``/``bundle_manifest``/``enabled_checks``/``severities`` are all ruled (b), dropped rather than absorbed). ``resolve_side_snapshot`` has accepted the pass-through since PR 3A; only the request had no way to state it, so a typed caller with a trusted ``.abicheck.yml`` ``build.query`` could not authorize running it. ``False`` (the default) is the Tier-2 "never execute a build system as a side effect of resolving an input" rule, so every pre-existing request is unchanged -- and under it ``_gated_build_query_inputs`` nulls the whole per-side ``InputSpec.build_config``, *including* passive keys such as ``build.compile_db``, exactly as it does for ``dump``'s own typed pipeline. That is not a narrowing: nothing on the ``compare`` path read ``build_config`` at all before this field existed. ``scan`` is the one caller that keeps a config's passive half readable without consent (it passes ``build_config_locally_trusted``, because its CLI-side gate authorizes only the executable field); giving ``compare`` the same split is a separate change with its own behavioural blast radius, not part of absorbing this field (Codex review, PR #1186). ``True`` asserts the same operator consent ``dump --allow-build-query`` expresses, and makes the whole config -- executable ``build.query`` included -- readable.
    allow_build_query: bool = field(default=False, kw_only=True)

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        Lives here (Tier 2) so the CLI and MCP front-ends surface *identical*
        error text for the same bad request (ADR-037 D9 / goal AC 8): value
        validation (language / AST frontend enums) and the cross-flag
        feasibility rules (an ``android`` frontend has no header-AST path, so it
        needs source inputs).
        """
        errors: list[str] = []
        errors += _lang_errors(self.lang)
        frontend = self.frontend.lower()
        frontend_errors = frontend_value_errors(self.frontend)
        errors += frontend_errors
        if (
            not frontend_errors
            and frontend == "android"
            and not (
                self.has_sources
                or self.old.sources
                or self.new.sources
                or self.old.build_info
                or self.new.build_info
            )
        ):
            # D8/D9: 'android' reuses a pre-captured header-abi dump; it has no
            # header-AST path, so a header-only run can't use it. ADR-055 D1
            # (Codex review): either side's own `InputSpec.sources` also
            # satisfies this -- not just the legacy `has_sources` flag, which
            # a typed caller using the new field alone would otherwise have to
            # redundantly set too. A second review round: `InputSpec.
            # build_info` alone must count too -- `embed_build_source`
            # auto-detects a pack directory in *either* `sources` or
            # `build_info` (`cli_buildsource.py`'s own `bi_is_pack`/
            # `src_is_pack`), so a prebuilt evidence pack passed via
            # `build_info` is exactly the same "already have a pre-captured
            # header-abi dump" case this rule exists to allow.
            errors.append(_ANDROID_NEEDS_SOURCES)
        # ADR-055 D1 (Codex review, two rounds): whether `InputSpec.sources`
        # is compatible with `frontend == "android"` depends on whether it's
        # a genuine raw source tree (run_compare_request's inline
        # embed_build_source has no real Android extractor -- rejected) or a
        # prebuilt evidence pack (loaded as pre-captured facts, no extractor
        # ever runs -- valid). That distinction needs filesystem access plus
        # helpers from the CLI/service import-cycle-allowlisted cluster this
        # leaf module deliberately stays out of, so it's checked at runtime
        # in service.run_compare_request instead of here.
        errors += _debug_format_errors(self.debug_format)
        if not self.policy:
            errors.append("policy profile name must not be empty")
        elif self.policy_file_path is None and self.policy not in VALID_BASE_POLICIES:
            # Only checked with no policy_file_path (Codex review, Round 11):
            # a file overrides the base name -- stated_policy_base's own
            # logic, applied here too -- so an unknown name paired with a
            # valid file is a legitimate request the file already resolves
            # for; only a name with nothing to override it is a real error,
            # and needs to fail here before any extraction runs.
            errors.append(
                f"unknown policy {self.policy!r}: choose from "
                f"{sorted(VALID_BASE_POLICIES)}"
            )
        # D9 pre-flight: a --policy-file path that doesn't exist is a hard error
        # here (Tier 2), so CLI and MCP surface the same message before any work.
        if (
            self.policy_file_path is not None
            and not Path(self.policy_file_path).exists()
        ):
            errors.append(f"policy file not found: {self.policy_file_path}")
        # ADR-049 Phase 6 (Codex review): the same two rules the CLI applies
        # to --contract, so a typed caller fails fast and with identical text
        # instead of having the mode silently ignored (contract_evaluation
        # off) or raising a raw ValueError deep in the pipeline after input
        # resolution (bad value).
        if self.contract_mode is not None:
            from ..contract_relevance_types import ContractMode

            allowed_modes = {mode.value for mode in ContractMode}
            if self.contract_mode not in allowed_modes:
                errors.append(
                    f"unsupported contract mode {self.contract_mode!r}: "
                    f"choose from {', '.join(sorted(allowed_modes))}"
                )
            if not self.contract_evaluation:
                errors.append(
                    "contract_mode requires contract_evaluation: it selects "
                    "which evidence domain the shadow contract evaluator "
                    "judges against, and without that flag no contract "
                    "decision is computed at all"
                )
        errors += _depth_errors(self.depth)
        errors += frontend_context_errors(self.frontend_context)
        # Fail fast on a misspelled severity_preset (Codex review, fresh
        # evidence): `resolve_release_gate_options` already rejects an
        # unknown preset, but `classify_compare_pair` only calls it *after*
        # `resolve_compare_request` has already run extraction — a project-
        # controlled build/source step that can be slow or side-effecting.
        # Checking here means a bad value is a Tier-2 ValidationError before
        # any of that runs, for every front end that calls
        # `validate()`/`validation_errors()` (native `compare` CLI
        # included, via `cli_compare_receipt.py`). `SEVERITY_PRESETS` is
        # `resolve_severity_config`'s own lookup table, checked here without
        # calling it (this method must stay side-effect-free; resolving
        # would also require a real `SeverityConfig` this validation has no
        # use for).
        if self.severity_preset is not None:
            from ..policy.severity import SEVERITY_PRESETS

            if self.severity_preset not in SEVERITY_PRESETS:
                errors.append(
                    f"invalid severity_preset {self.severity_preset!r}; "
                    f"must be one of {sorted(SEVERITY_PRESETS)} or None"
                )
        # Codex review (fresh evidence, PR #1178): the CLI's `--budget` parser
        # (`cli_compare_helpers._parse_budget`) already rejects non-finite
        # (`nan`/`inf`/`-inf`) and negative values via `math.isfinite` before
        # ever calling `deadline.deadline_scope`, because `deadline.check()`/
        # `bounded_timeout()` both test `left <= 0` -- a value that is never
        # `<= 0` (an infinite deadline) or never compares meaningfully at all
        # (`nan`) makes the promised wall-clock guard silently inert. A typed
        # caller reaches the identical `deadline_scope(request.budget_s)` call
        # in `run_compare_request` with no CLI in between, so the same floor
        # belongs here too -- otherwise this documented public field could
        # disable its own guard.
        if self.budget_s is not None and (
            not math.isfinite(self.budget_s) or self.budget_s < 0
        ):
            errors.append(
                f"budget_s must be a finite, non-negative number of seconds "
                f"or None; got {self.budget_s!r}"
            )
        # Round 7: moved from classify_compare_pair (ran after extraction).
        for field_name, overrides in (
            ("pack_policy_overrides", self.pack_policy_overrides),
            ("project_policy_overrides", self.project_policy_overrides),
        ):
            for kind, verdict in overrides or ():
                if verdict is Verdict.NO_CHANGE:
                    errors.append(
                        f"{field_name} kind {kind.value!r} may not target Verdict.NO_CHANGE"
                    )
        for label, side in (("old", self.old), ("new", self.new)):
            errors += _path_required_errors(label, side, source_only_allowed=False)
            errors += _side_errors(label, side)
        return errors

    def validate(self) -> CompareRequest:
        """Validate fail-fast; raise :class:`ValidationError` on the first batch.

        Returns ``self`` so callers can write ``request.validate()`` inline.
        """
        errors = self.validation_errors()
        if errors:
            raise ValidationError("; ".join(errors))
        return self

    def replace(self, **changes: Any) -> CompareRequest:
        """Return a copy with *changes* applied (frozen-dataclass ``replace``).

        ``**changes: Any`` is deliberate, not an oversight (a fresh review's
        own question): a per-field ``TypedDict``/overload set would need to be
        kept in sync with every field this dataclass gains, which is exactly
        the maintenance burden this module's own docstring says a request
        dataclass exists to avoid ("a new feature becomes a new field with a
        default, never a signature break"). A typo'd kwarg still surfaces —
        as ``dataclasses.replace``'s own ``TypeError`` at the call site,
        rather than a mypy error.
        """
        return replace(self, **changes)


@dataclass(frozen=True)
class DumpRequest:
    """A fully-specified snapshot-extraction request — the input to ``run_dump_request``.

    ``compare``'s counterpart to this (:class:`CompareRequest`) has existed
    since ADR-037 D2; ``dump`` had no typed request at all, so the only way to
    ask for a snapshot through the service layer was
    :func:`abicheck.service.resolve_input`'s twenty-odd loose keyword arguments
    — and a front end that wanted ``--depth``/``--sources``/``--build-info``
    had to add the inline build-source embedding, the depth gate and the
    dependency walk itself. That is what kept the MCP ``abi_dump`` tool at a
    fixed five-argument subset of what ``abicheck dump`` accepts (G33 Phase 5).

    One side, so it reuses :class:`InputSpec` verbatim: everything about *what*
    to extract (path, headers, includes, ``sources``/``build_info``,
    ``dump_manifest``, per-input ``compile`` context, public-header dirs,
    dependency scoping) already lives there. The fields here are the ones
    :class:`CompareRequest` also keeps at request level because they describe
    *how* the extraction runs rather than which input it runs on.

    Deliberately **not** carried over from :class:`CompareRequest`: anything
    about classification (policy, suppression, scope, severity, contract
    evaluation). A dump produces evidence; it renders no verdict.
    """

    input: InputSpec
    lang: str = "c++"
    frontend: str = "auto"
    # Mirrors `CompareRequest.has_sources`: the legacy "this run has source
    # evidence" flag, which alone satisfies the `android` frontend's rule even
    # when the evidence is not `InputSpec.sources`/`build_info`.
    has_sources: bool = False
    # The friendly evidence-depth dial (`--depth`: binary/headers/build/source).
    # `None` infers the collect mode from whether the input sets `sources`/
    # `build_info`, exactly as `CompareRequest.depth` does. An explicit value is
    # a floor `run_dump_request` enforces: a depth that was requested but not
    # reached raises rather than silently returning a weaker snapshot (the same
    # contract `dump --depth` has via `check_requested_depth_satisfied`).
    depth: str | None = None
    dwarf_only: bool = False
    debug_format: str | None = None
    enable_debuginfod: bool = False
    debuginfod_url: str | None = None
    include_labels: tuple[tuple[Path, str], ...] = ()
    # `--follow-deps` / `--search-path` / `--ld-library-path`: populate the
    # snapshot's transitive `DependencyInfo`. Off by default, matching the CLI
    # flag and `CompareRequest.follow_dependencies` — it costs a full
    # dependency-graph resolution, so it stays opt-in.
    follow_dependencies: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    # Request-level default for `CompileContext.frontend_context`
    # (`--frontend-context`, host|device), applied when the input's own
    # `InputSpec.compile.frontend_context` reads as the class default — see
    # `service_compare_evidence._compile_context` for the accepted limitation
    # that shared merge rule carries.
    frontend_context: str = "host"
    # See `CompareRequest.lang_explicit` — the identical default-vs-explicit
    # ambiguity and the same conservative default (`False`: auto-detect
    # unless `lang == "c"`, unchanged from before this field existed).
    # `kw_only=True`, appended at the true end (not inserted mid-list), for
    # the same positional-caller-safety reason as `CompareRequest`'s own
    # field (Codex review).
    lang_explicit: bool = field(default=False, kw_only=True)
    # An already-resolved collect mode, overriding what
    # `service_compare_evidence.dump_collect_mode_for` would otherwise derive
    # from `depth` alone (Codex review, PR 3A blocker 5). `compare`'s own
    # implicit-dump path (`cli_compare_helpers._embed_inline_source_side`)
    # resolves collect mode from the *pair* (`collect_mode_for`, a materially
    # different rule from `dump`'s own default — see that function's own
    # docstring) and forwards it into `dump_cmd`'s private
    # `_resolved_collect_mode` hook so the real run doesn't re-derive a
    # possibly-different mode from `depth` in isolation. Without this field,
    # a `DumpRequest` built for that invocation (e.g. by `--dry-run`) could
    # only record `depth`, and `resolve_dump_request()` would silently
    # recompute a different collect mode than the one the real run actually
    # uses — the exact "preview describes a run that never happened" hazard
    # this whole request object exists to foreclose. `None` (the default)
    # means "derive from `depth`", unchanged for every other caller.
    resolved_collect_mode: str | None = field(default=None, kw_only=True)

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        The same value and cross-flag rules :meth:`CompareRequest.validation_errors`
        applies, through the same module-level helpers, so ``dump`` and
        ``compare`` reject an identical mistake with identical text (ADR-037 D9
        front-end parity, extended across the two commands).
        """
        errors: list[str] = []
        errors += _lang_errors(self.lang)
        frontend_errors = frontend_value_errors(self.frontend)
        errors += frontend_errors
        if (
            not frontend_errors
            and self.frontend.lower() == "android"
            and not (self.has_sources or self.input.sources or self.input.build_info)
        ):
            errors.append(_ANDROID_NEEDS_SOURCES)
        errors += _debug_format_errors(self.debug_format)
        errors += _depth_errors(self.depth)
        errors += _resolved_collect_mode_errors(self.resolved_collect_mode)
        errors += frontend_context_errors(self.frontend_context)
        errors += _path_required_errors("input", self.input, source_only_allowed=True)
        errors += _source_only_binary_depth_errors(self.input, self.depth)
        errors += _side_errors("input", self.input)
        return errors

    def validate(self) -> DumpRequest:
        """Validate fail-fast; raise :class:`ValidationError` on the first batch.

        Returns ``self`` so callers can write ``request.validate()`` inline.
        """
        errors = self.validation_errors()
        if errors:
            raise ValidationError("; ".join(errors))
        return self

    def replace(self, **changes: Any) -> DumpRequest:
        """Return a copy with *changes* applied (frozen-dataclass ``replace``).

        See :meth:`CompareRequest.replace` for why ``**changes: Any`` is
        deliberate rather than an oversight.
        """
        return replace(self, **changes)


@dataclass(frozen=True)
class CompareResult:
    """What one :class:`CompareRequest` produced — the typed result (ADR-055 D2).

    Returned by :func:`abicheck.service.run_compare_request` and by the
    ``run_compare`` kwargs shim. Both returned a bare
    ``tuple[DiffResult, AbiSnapshot, AbiSnapshot]`` before 0.6, which left
    every new thing a comparison resolves nowhere to land but a fourth tuple
    slot — a break for every positional caller. As a struct, a future field
    (a resolved-depth record, ADR-049's evaluation receipt, a coverage
    summary) is an additive attribute instead. The same reasoning ADR-035
    applied to ``ScanRequest``/``ScanResult`` (both since retired -- ADR-068
    Phase 4's typed-API slice), generalized to ``compare``.
    :meth:`as_tuple` reproduces the pre-0.6 shape in one line.

    ``suppression`` is the one field beyond that rename, and it is not
    speculative: it is what ADR-055 D4 needed to exist. ``run_compare_request``
    resolves the suppression list internally from
    ``CompareRequest.suppress``, but a front end applying a *post*-
    classification concern still needs the resolved object — ``appcompat``'s
    ``scope_diff_to_app(..., suppression=...)`` is the concrete case. Without
    it here, the MCP server would have had to keep its own
    ``SuppressionList.load`` call purely to re-derive a value the service had
    already loaded, which is precisely the duplication D4 removes. The
    resolved policy file needs no equivalent field — ``DiffResult.policy_file``
    already carries it.

    Placed here rather than in ``service.py`` (where ADR-055's own file sketch
    put it) so the request and its result live in one leaf module: this one is
    already "typed request/response structs", imports nothing at runtime from
    the service layer, and keeps a caller able to type-annotate a result
    without importing ``service``'s much heavier graph.
    """

    diff: DiffResult
    old_snapshot: AbiSnapshot
    new_snapshot: AbiSnapshot
    suppression: SuppressionList | None = None
    # ADR-064/PR G2, see classify_compare_pair.
    exit_decision: ExitDecision | None = None
    # ADR-064/PR G2 rendering parity (Codex review): the same resolved
    # `GateOptions.severity` `exit_decision` above was scored under, so a
    # caller can pass `severity_config=result.severity_config` into
    # `reporter.render_output`/`to_json` and get a rendered `severity` block
    # and exit code that agree with `exit_decision`, instead of silently
    # recomputing a `None`-severity legacy exit that contradicts it. `None`
    # under the legacy scheme, where there is nothing to disagree with.
    severity_config: SeverityConfig | None = None

    def as_tuple(self) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
        """Return ``(diff, old_snapshot, new_snapshot)`` — the pre-0.6 shape.

        A one-line migration for a caller that unpacked the tuple
        ``run_compare``/``run_compare_request`` used to return::

            result, old, new = run_compare(...).as_tuple()

        Nothing in abicheck itself returns that shape any more; this exists
        only so a caller need not restructure to adopt the typed result.
        """
        return self.diff, self.old_snapshot, self.new_snapshot


__all__ = [
    "FRONTEND_CONTEXTS",
    "HEADER_AST_FRONTENDS",
    "SUPPORTED_DEBUG_FORMATS",
    "SUPPORTED_FRONTENDS",
    "SUPPORTED_LANGS",
    "CompareRequest",
    "CompareResult",
    "DumpRequest",
    "InputSpec",
    "OutputSpec",
    "frontend_context_errors",
    "frontend_value_errors",
    "required_path",
]
