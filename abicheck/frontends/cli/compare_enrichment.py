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

"""``compare``'s two Phase-2c/2d inputs, resolved at the CLI boundary
(``docs/contribute/plans/one-comparison-product.md`` §6 Phase 2c/2d).

Lives here rather than inside ``cli_compare_helpers.run_compare`` for the
usual reason this package exists -- that module is at its no-growth
baseline -- and because both halves are boundary translation, not analysis:
the *rules* live in :mod:`abicheck.workflows.changed_paths` and
:mod:`abicheck.workflows.abi3_audit`, which the typed API reaches without
any of this.

ADR-068 D5, applied per flag:

* ``--since``/``--changed-path`` are genuine **per-run** inputs (a PR's own
  diff changes every run), so they are CLI-only, exactly as on ``scan``.
* the ``--abi3`` **floor** is a stable project property -- which
  ``Py_LIMITED_API`` version a project promises is reviewed in a PR, not
  chosen per invocation -- so ``.abicheck.yml``'s ``python.abi3_floor``
  supplies it and the flag is the per-run override on top
  (``python_abi3_floor`` in :class:`~abicheck.buildsource.build_config.
  BuildConfig`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ...workflows.changed_paths import localized_collect_mode, resolve_changed_seed


@dataclass(frozen=True)
class CompareEnrichmentInputs:
    """The resolved ``--since``/``--changed-path``/``--abi3`` inputs."""

    #: The changed-path seed (empty when none was given or git could not
    #: resolve the ref). Scoping only -- it produces no finding.
    changed_paths: tuple[str, ...] = ()
    #: Human-readable provenance of the seed, for ``-v`` diagnostics.
    changed_source: str = "none (no diff seed; broad scope)"
    #: Whether a changed-path scope was actually attempted
    #: (``ChangedPathSeed.seeded``) -- distinct from ``bool(changed_paths)``:
    #: a successfully-resolved but *empty* ``--since``/``--changed-path``
    #: diff is seeded with zero paths, which must scan/replay nothing, not
    #: fall back to the unseeded broad scope (Codex review). Consumers that
    #: need "was a real, possibly-empty scope given" (the pattern pre-scan
    #: fold) must read this, not ``bool(self.changed_paths)``.
    seeded: bool = False
    #: The candidate-side stable-ABI audit floor, ``None`` when off.
    abi3_floor: tuple[int, int] | None = None

    def localize_collect_mode(self, collect_mode: str) -> str:
        """This run's collect mode, narrowed to the seed (ADR-043 D7).

        A thin bind of :func:`~abicheck.workflows.changed_paths.
        localized_collect_mode` to this seed, so a caller cannot narrow by
        one seed while resolving another.
        """
        return localized_collect_mode(collect_mode, self.changed_paths)


def resolve_compare_enrichment_inputs(
    *,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    abi3: str | None,
    project_cfg: Any | None,
    sources: Path | None,
) -> CompareEnrichmentInputs:
    """Resolve both inputs, warning through ``click.echo`` on a failed seed.

    *sources* is the tree the ``git diff`` runs in (the candidate side's
    ``--sources`` when there is one), matching ``scan``'s own behaviour.
    *project_cfg* is the loaded ``.abicheck.yml``, consulted only for the
    ``--abi3`` floor's D5 config default.
    """
    from ...cli_options import parse_abi3_floor

    seed = resolve_changed_seed(
        changed_paths_opt,
        since,
        sources,
        notify=lambda message: click.echo(message, err=True),
    )
    floor = parse_abi3_floor(abi3)
    if floor is None:
        # D5: the project's declared floor, when the run did not state one.
        floor = parse_abi3_floor(getattr(project_cfg, "python_abi3_floor", None))
    return CompareEnrichmentInputs(seed.paths, seed.source, seed.seeded, floor)


def fold_abi3_into_extra_changes(
    extra_changes: Any,
    candidate: Any,
    abi3_floor: tuple[int, int] | None,
    name: str,
) -> tuple[Any, str | None]:
    """Fold the candidate-side audit into this run's ``extra_changes``.

    A thin bind of :func:`~abicheck.workflows.abi3_audit.fold` (the
    shared rule that both front ends call) to the CLI's own
    candidate name. Returns ``(extra_changes, precondition_failure)``; the
    findings go in *before* ``compare_snapshots`` so policy, suppression,
    the disposition ledger and the verdict all score them.
    """
    from ...workflows import abi3_audit

    return abi3_audit.fold(extra_changes, candidate, abi3_floor, candidate_name=name)


def fold_lexical_prescan_into_result(
    result: Any,
    *,
    old_headers: list[Path],
    new_headers: list[Path],
    old_sources: Path | None,
    new_sources: Path | None,
    old_snapshot: Any,
    new_snapshot: Any,
    depth: str | None,
    changed_paths: tuple[str, ...],
    seeded: bool = False,
    old_compile_context: Any = None,
    new_compile_context: Any = None,
) -> None:
    """Thin bind of :func:`abicheck.workflows.lexical_prescan.fold_lexical_prescan`
    (the shared rule both front ends call) to the CLI's own already-resolved
    values -- plan §3 rows 6/8, §6 Phase 2b. Mirrors
    :func:`fold_abi3_into_extra_changes` above.

    *seeded* must be ``CompareEnrichmentInputs.seeded``, not
    ``bool(changed_paths)`` -- see that field's own docstring.
    """
    from ...workflows.lexical_prescan import fold_lexical_prescan

    fold_lexical_prescan(
        result,
        old_headers=old_headers,
        new_headers=new_headers,
        old_sources=old_sources,
        new_sources=new_sources,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
        depth=depth,
        changed_paths=changed_paths,
        seeded=seeded,
        old_compile_context=old_compile_context,
        new_compile_context=new_compile_context,
    )


def report_abi3_evidence_contract_error(result: Any, failure: str | None) -> None:
    """Stamp ADR-064's exit-7 axis for a failed ``--abi3`` precondition and
    say so on stderr (so the message survives ``--format json`` on stdout)."""
    from ...workflows.abi3_audit import record_abi3_evidence_contract_error

    record_abi3_evidence_contract_error(result, failure)
    if failure is not None:
        click.echo(f"error: {failure}", err=True)
