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

"""ADR-049 Phase 5: the ``compare`` CLI's own resolved configuration.

``checker.compare`` builds a persisted contract context from the arguments it
was handed, so it can honestly claim no more than ``API_REQUEST`` for any of
them (see :mod:`abicheck.contract_context`). This module is where the
``compare`` command hands over what it -- and only it -- knows: which flags
the user really typed, which ``.abicheck.yml`` supplied a value, which
``--profile`` filled one in, and the exit-code scheme and severity levels the
run was actually scored with.

Those inputs go through Phase 1's canonical resolver
(:func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`),
and the resulting object *replaces* the core verb's narrower reconstruction.
That is the whole point of the phase: one resolver decides D7 precedence for
every front end, instead of each one patching the fields it happens to know
about after the fact.

**Values and provenance come from different places for the gate, on purpose.**
The scheme and the four severity levels are resolved by
``cli_helpers_compare.resolve_compare_config`` *before* this runs, and that
resolution is what the run's verdict and exit code were computed from -- so
those are the values a receipt must report, and they are written through
:func:`~abicheck.contract_context.with_resolved_gate` unchanged. The
canonical resolver re-derives the same two from the same inputs and is used
for their *provenance*. The two agreeing is a real, checkable claim rather
than an assumption: ``tests/test_cli_compare_config_receipt.py`` asserts it
across the input matrix, so a divergence surfaces as a failing parity test
instead of a receipt that quietly describes a different run.

Split out of :mod:`abicheck.cli_compare_helpers` when that file reached the
2000-line hard cap -- a cohesive unit (one concern, one caller) rather than
an arbitrary cut. Deliberately a **leaf**: it imports nothing from its
caller, so no cycle forms. "Which parameters did the user actually type" is
the caller's question to answer (it holds the Click context), so the answers
arrive here as data (*typed*) rather than this module reaching back for them,
and ``resolved_cfg``/``project_cfg`` stay ``Any`` for the same reason --
typing them would import the very module this one is split out of.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

#: The ``compare`` kwargs that feed the compatibility configuration. Named
#: here so the caller forwards exactly the mapping
#: ``compare_cli_inputs`` reads, and an option renamed on one side fails
#: loudly on the other rather than resolving to "not stated".
COMPARE_CONFIG_PARAMS: tuple[str, ...] = (
    "contract_mode",
    "scope_public_headers",
    "policy",
    "policy_file_path",
    "public_symbols",
    "public_symbols_list",
    "suppress",
    "require_justification",
    "exit_code_scheme",
    "severity_preset",
    "severity_abi_breaking",
    "severity_potential_breaking",
    "severity_quality_issues",
    "severity_addition",
    "pack_paths",
)

#: The four :class:`~abicheck.severity.SeverityConfig` categories, in the
#: spelling ``compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS``
#: keys its ``gate.severity.<category>`` provenance entries by.
_SEVERITY_CATEGORIES = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)


def typed_parameter_names() -> tuple[str, ...]:
    """The parameters the caller must answer "was this typed?" for.

    Read off the resolver's own
    :data:`~abicheck.compatibility_evaluation_frontend.DEFAULTED_COMPARE_PARAMETERS`
    rather than restated here: those are exactly the options whose click
    default (``--policy strict_abi``, ``--scope-public-headers``) is
    indistinguishable from a value the user chose, and a second copy of that
    list is a second thing to keep in sync. Every other option already
    spells "not given" as ``None``/``()``.
    """
    from .compatibility_evaluation_frontend import DEFAULTED_COMPARE_PARAMETERS

    return tuple(sorted(DEFAULTED_COMPARE_PARAMETERS))


def _suppression_source(suppression: Any, path: Any) -> Any:
    """The already-loaded ``--suppress`` list, as a resolver input.

    Built from the list the run really used rather than re-reading *path*:
    a second read could pair one content's digest with another's rules, the
    trap :meth:`SuppressionSource.from_file` documents for its own single
    read. A list with no ``source_sha256`` (``merge()`` drops it, and the
    ABICC front end constructs several without one) still selected a source,
    so it is reported as one -- the same absent-vs-empty rule
    ``contract_context.suppression_config_for`` follows.
    """
    if suppression is None:
        return None
    from .compatibility_evaluation_frontend import SuppressionSource

    return SuppressionSource(
        path=str(path) if path is not None else None,
        sha256=getattr(suppression, "source_sha256", None) or "",
        rules=tuple(suppression.rule_identities()),
    )


def _profile_inputs(run_profile: Mapping[str, Any] | None) -> Any:
    """A ``--profile``'s injected values, as a resolver input.

    *run_profile* is what ``cli_options.apply_compare_profile`` recorded:
    ``{"name": ..., "injected": {dest: value}}``. Only the keys the
    configuration actually has a field for are read; the rest of a profile
    (depth, format, ``--recommend``, ``--stat``) is execution/report surface
    with nothing to resolve here.
    """
    from .compatibility_evaluation_frontend import RunProfileInputs

    if not run_profile:
        return None
    injected = run_profile.get("injected") or {}
    scheme = injected.get("exit_code_scheme")
    return RunProfileInputs(
        name=run_profile.get("name"),
        exit_code_scheme=str(scheme) if scheme is not None else None,
    )


def resolve_cli_config(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any,
    project_path: Path | None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    run_profile: Mapping[str, Any] | None = None,
    policy_option: str | None = None,
    policy_path: Path | None = None,
    policy_sha256: str | None = None,
    project_sha256: str | None = None,
    symbols_list: Any = None,
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this invocation.

    *policy_option* names the flag that selected ``policy`` when it was not
    ``--policy`` -- ``--required-symbol``/``--required-symbols``, whose
    contract switches an untouched ``--policy`` to ``plugin_abi``, with
    *policy_path*/*policy_sha256* identifying the list file when that is the
    form used. *project_sha256* is the digest of the ``.abicheck.yml`` bytes
    *project_cfg* was parsed from, so a project-supplied value names a
    revision rather than only a path.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict, a
    D8 pack conflict, a malformed pack manifest); mapping those onto an exit
    code is the caller's job, as that module documents.
    """
    from .compatibility_evaluation_frontend import (
        FrontEnd,
        ProjectCompatibilityInputs,
        compare_cli_inputs,
        resolve_compatibility_evaluation_config,
    )

    # The declared contract, enforced rather than only documented: every one
    # of these keys is read below, and a caller that renamed or dropped one
    # would have it resolve silently to "not stated" -- indistinguishable
    # from a user who did not pass the flag (CodeRabbit review).
    missing = [name for name in COMPARE_CONFIG_PARAMS if name not in params]
    if missing:
        raise KeyError(
            f"compare config params missing {missing}; every name in "
            "COMPARE_CONFIG_PARAMS must be forwarded, since an absent one "
            "resolves as unstated rather than failing"
        )

    # A profile injects its values *into the command's kwargs*, so by the time
    # they reach here they are indistinguishable from typed ones -- and read
    # as `EXPLICIT_CLI`, the one layer a profile must never claim. Blanked
    # here and re-contributed at the `run_profile` tier below, which is both
    # the honest source and the precedence D7 gives it.
    injected = (run_profile or {}).get("injected") or {}
    params = {
        name: (None if name in injected else value) for name, value in params.items()
    }
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI,
        explicit=compare_cli_inputs(
            params,
            explicit_parameters=typed,
            policy_file=policy_file,
            suppression=_suppression_source(suppression, suppress_path),
            policy_base_option=policy_option,
            policy_base_path=str(policy_path) if policy_path is not None else None,
            policy_base_sha256=policy_sha256,
            public_symbols_list=symbols_list,
        ),
        project=ProjectCompatibilityInputs.from_build_config(
            project_cfg, path=project_path, sha256=project_sha256
        ),
        profile=_profile_inputs(run_profile),
    )


def record_resolved_config(
    result: Any,
    resolved_cfg: Any,
    project_cfg: Any,
    *,
    params: Mapping[str, Any],
    typed: Collection[str],
    project_path: Path | None = None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    run_profile: Mapping[str, Any] | None = None,
    policy_option: str | None = None,
    policy_path: Path | None = None,
    policy_sha256: str | None = None,
    project_sha256: str | None = None,
    symbols_list: Any = None,
) -> None:
    """Install this front end's resolved configuration onto the context.

    A no-op unless ``--contract-evaluation`` produced a context. Runs before
    any report is rendered, so every output path sees one configuration
    resolved by the canonical resolver rather than the core verb's
    argument-shaped reconstruction, and sees the gate the run was actually
    scored with rather than :class:`GateConfig`'s built-in defaults.
    """
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .compatibility_evaluation_frontend import (
        EXIT_CODE_SCHEME_FIELD,
        SEVERITY_CATEGORY_FIELDS,
    )
    from .contract_context import with_resolved_config, with_resolved_gate

    config = resolve_cli_config(
        params,
        typed=typed,
        project_cfg=project_cfg,
        project_path=project_path,
        policy_file=policy_file,
        suppression=suppression,
        suppress_path=suppress_path,
        run_profile=run_profile,
        policy_option=policy_option,
        policy_path=policy_path,
        policy_sha256=policy_sha256,
        project_sha256=project_sha256,
        symbols_list=symbols_list,
    )
    ctx = with_resolved_config(ctx, config)
    # The values the run was scored with, with the canonical resolver's
    # provenance -- see this module's docstring for why the two halves come
    # from different resolutions, and which test holds them to agreeing.
    result.contract_context = with_resolved_gate(
        ctx,
        exit_code_scheme=resolved_cfg.exit_code_scheme,
        severity=resolved_cfg.severity,
        scheme_provenance=config.provenance[EXIT_CODE_SCHEME_FIELD],
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in _SEVERITY_CATEGORIES
        },
    )
