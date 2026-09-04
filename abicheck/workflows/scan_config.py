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

"""Scan configuration the engine resolves for itself.

ADR-061 Phase 4. These three were the last things ``service_scan`` had to
reach *back into the CLI* for -- the final entries in
``ENGINE_CLI_BOUNDARY_ALLOWLIST``. None of them is CLI work: loading a
``risk_rules:`` YAML profile, deciding whether a set of header inputs
establishes a public directory boundary, and resolving one
``CompatibilityEvaluationConfig`` for a scan are all things a typed-API caller
needs done identically. Living in ``cli_scan_baseline``/``cli_scan_receipt``
meant the engine either imported upward or kept a second copy, which is the
inversion this phase exists to remove.

The CLI modules keep the old private spellings as delegating aliases, so no
call site moved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..buildsource.risk import RiskRules, RiskScore, score_changed_paths
from ..errors import SnapshotError

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from pathlib import Path

__all__ = [
    "SCAN_CONFIG_PARAMS",
    "RiskRules",
    "RiskScore",
    "SCAN_REQUEST_SPELLINGS",
    "load_risk_rules",
    "public_provenance_set",
    "resolve_scan_config",
    "score_changed_paths",
]

#: ``scan_cmd`` gives them. A caller passing a partial mapping is rejected
#: rather than letting a dropped key resolve as "not stated" -- the same
#: guard ``cli_compare_receipt.COMPARE_CONFIG_PARAMS`` carries, for the same
#: reason: a renamed option should fail loudly, not silently change a
#: receipt's meaning.
SCAN_CONFIG_PARAMS: tuple[str, ...] = (
    "policy",
    "policy_file_path",
    "suppress",
    "scope_public_headers",
    "contract_mode",
    "pack_paths",
    # CLI cleanup phase two, "PR B": needed so this resolver's D7 precedence
    # can tell an explicit --severity-preset apart from "nothing stated" --
    # without it a selected gate pack looked unopposed here regardless of
    # what the CLI actually gave, which let
    # `pack_application.apply_to_compare_config` override an explicit value
    # (see this module's own docstring for the reproduced repro and why
    # this is safe for `scan` specifically). No `exit_code_scheme` entry any
    # more (CLI cleanup phase two PR G2 deleted the manual selector
    # everywhere) -- the algorithm is purely derived from whether a
    # severity setting is in effect.
    "severity_preset",
)

#: How a :class:`~abicheck.service_scan.ScanRequest` spells the inputs whose
#: default API name comes from :class:`~abicheck.api_types.CompareRequest`.
#: "The API" is not one namespace: resolving a ``ScanRequest`` at
#: ``FrontEnd.API`` alone still recorded ``scope_public``/``policy_file_path``/
#: ``suppress``, none of which that entity has, so a replay consumer could not
#: identify the input that produced the value (Codex review).
#:
#: Only the three that actually differ are listed -- ``policy``,
#: ``force_public_symbols``, and ``contract_mode`` are spelled identically on
#: both requests, and an unmapped field keeps its default spelling.
#: ``tests/test_scan_compare_parity.py`` pins every entry against
#: ``ScanRequest``'s real fields, so a renamed field fails there rather than
#: silently reintroducing a name nobody can replay.
SCAN_REQUEST_SPELLINGS: Mapping[str, str] = {
    "scope_public": "scope_to_public_surface",
    "policy_file_path": "policy_file",
    "suppress": "suppression",
}



def public_provenance_set(
    headers: list[Path], public_header_dirs: list[Path]
) -> tuple[list[Path], list[Path]]:
    """Build the ``(public_headers, public_header_dirs)`` provenance set for scan.

    A directory boundary is what lets ``apply_provenance`` classify origins as
    PUBLIC/INTERNAL (and so unlocks the leakage / RTTI / exported-vs-public
    cross-checks, ADR-024). Directories come from ``--public-header-dir`` and from
    any ``-H`` argument that is itself a directory; ``-H`` *file* arguments ride
    along as explicit public headers.

    A lone ``-H`` umbrella *file* with no directory does **not** activate
    provenance: a single header cannot establish a public directory boundary
    (the abicheck A1 finding), so we return empty sets and every origin stays
    ``UNKNOWN`` — preserving the prior default-scan behaviour.
    """
    dirs = list(public_header_dirs)
    files: list[Path] = []
    for h in headers:
        if h.is_dir():
            dirs.append(h)
        else:
            files.append(h)
    if not dirs:
        return [], []
    return files, dirs


def load_risk_rules(path: Path | None) -> RiskRules:
    """Load a ``risk_rules:`` profile from a YAML file, or the shipped default."""
    if path is None:
        return RiskRules.default()
    import yaml  # hard dep (pyyaml); import out of the try so the except can name it

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # yaml.YAMLError (e.g. ParserError) is not a ValueError, so catch it
        # explicitly — else malformed --risk-rules YAML escapes as a traceback
        # through the installed console script (Codex review).
        # Operational, not a usage error: the flag was well-formed and the
        # file was not. The CLI adapter renders this as a plain
        # ClickException (exit 1), which is what it did before this moved.
        raise SnapshotError(f"cannot read --risk-rules {path}: {exc}") from exc
    block = raw.get("risk_rules") if isinstance(raw, dict) else None
    return RiskRules.from_dict(block if isinstance(block, dict) else raw)


def resolve_scan_config(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any = None,
    project_path: Path | None = None,
    project_sha256: str | None = None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    symbols_list: Any = None,
    front_end: Any = None,
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this scan.

    *params* is ``scan_cmd``'s own parameter mapping; *typed* names the
    parameters Click reports the user actually typed, which matters for the
    ones whose click default is indistinguishable from a stated value.

    *front_end* defaults to the ``scan`` CLI. ``service_scan.run_scan``
    passes :attr:`FrontEnd.API`, because a receipt may only name inputs its
    caller really has: a ``ScanRequest`` sets ``policy`` and
    ``scope_to_public_surface`` as typed fields, and recording those as
    ``--policy``/``--scope-public-headers`` describes a command line nobody
    ran, so the receipt could not identify the input that selected the
    value (Codex review). Only the selector *spelling* and layer differ --
    the normalization below is shared deliberately, see *params*.

    The normalization itself is :func:`compare_cli_inputs`, **reused rather
    than re-implemented**: ``scan``'s shared config surface deliberately uses
    the same option destinations as ``compare``'s (``policy``,
    ``policy_file_path``, ``suppress``, ``scope_public_headers``,
    ``public_symbols``, ``public_symbols_list``, ``contract_mode``), because
    §6.4's Gate is that the two commands agree -- and two front ends that
    normalize the same flags through two functions is exactly how they would
    stop agreeing. A second normalizer would also have to re-derive
    :data:`DEFAULTED_COMPARE_PARAMETERS`' typed-vs-defaulted rule, the
    load-once affordances for the policy file/suppression/symbols list, and
    the union rule for ``--public-symbol``/``--public-symbols-list``.

    *policy_file*/*suppression*/*symbols_list* are the same "pass what you
    already loaded" affordance ``compare`` uses, and for the same reason: the
    scan loaded all three long before configuration is resolved, so
    re-reading here could pair a digest with content that did not score the
    run, and a file deleted mid-scan would fail an otherwise-finished
    comparison.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict,
    a D8 pack conflict); mapping those to a ``click.UsageError`` is the
    caller's job, exactly as it is for ``compare``.
    """
    from ..compatibility_evaluation_frontend import (
        FrontEnd,
        ProjectCompatibilityInputs,
        SuppressionSource,
        compare_cli_inputs,
        resolve_compatibility_evaluation_config,
    )

    missing = [name for name in SCAN_CONFIG_PARAMS if name not in params]
    if missing:
        raise KeyError(
            "scan config params missing from the caller's mapping: "
            f"{', '.join(sorted(missing))}. Every entry of SCAN_CONFIG_PARAMS "
            "must be present, so a renamed option fails here rather than "
            "silently resolving as 'not stated'."
        )
    # From the single load the comparison itself used, so the digest always
    # describes the rules that scored the run -- and falling back to a
    # content digest of the rules when the list carries none, which an
    # in-memory `SuppressionList` always does (Codex review; the inline
    # version here took `""` and failed the whole scan).
    suppression_source = SuppressionSource.from_loaded(suppression, suppress_path)
    project = None
    if project_cfg is not None:
        # No longer blanked (CLI cleanup phase two, "PR B" -- see this
        # function's own former `_without_gate_settings` note, kept below as
        # a comment rather than a dead function): `ProjectCompatibilityInputs.
        # from_build_config` and `cli_helpers_compare.resolve_compare_config`
        # -- the function that actually scores a scan's gate -- both read the
        # identical six fields off the identical `project_cfg`/`cfg` object
        # with the identical `explicit CLI > project config > built-in
        # default` precedence, and `scan` has no `--profile` option (unlike
        # `compare`) to introduce a tier the other resolver doesn't know
        # about. So, for `scan` specifically, these two resolutions cannot
        # disagree on a project-config-sourced severity/exit-code-scheme the
        # way blanking here was written to guard against -- and leaving them
        # blanked here left a real precedence bug in the *pack* fold: a
        # selected gate pack folded onto the real `resolved_cfg`
        # (`pack_application.apply_to_compare_config`, called from
        # `cli_scan._resolve_scan_evaluation_config`) saw a project-config
        # value as merely "unstated" and silently overrode it (the same class
        # of bug Codex review found for the explicit-CLI tier on #801,
        # reproduced here for the project-config tier by the identical
        # mechanism).
        project = ProjectCompatibilityInputs.from_build_config(
            project_cfg,
            path=str(project_path) if project_path is not None else None,
            sha256=project_sha256,
        )
    resolved_front_end = front_end if front_end is not None else FrontEnd.CLI
    return resolve_compatibility_evaluation_config(
        front_end=resolved_front_end,
        api_spellings=(
            SCAN_REQUEST_SPELLINGS if resolved_front_end is FrontEnd.API else None
        ),
        explicit=compare_cli_inputs(
            params,
            explicit_parameters=typed,
            policy_file=policy_file,
            suppression=suppression_source,
            public_symbols_list=symbols_list,
        ),
        project=project,
    )
