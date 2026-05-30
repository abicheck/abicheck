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

"""CLI — ``probe`` command group (build-configuration matrix harness).

Wraps the library API in :mod:`abicheck.probe_harness` and
:mod:`abicheck.diff_build_config` so the matrix-aware change kinds
(``API_DEPENDS_ON_CONSUMER_ENV``, ``CXX_STANDARD_FLOOR_RAISED``,
``BEHAVIOURAL_DEFAULT_CHANGED``) are reachable from the command line and
flow through the existing reporter / SARIF / JUnit paths.

Two subcommands:

* ``abicheck probe run SPEC --library L --version V --out matrix.json`` —
  compile every (configuration × probe) declared in the YAML manifest and
  emit a :class:`~abicheck.probe_harness.MatrixSnapshot` as JSON.
* ``abicheck probe compare OLD.json NEW.json`` — diff two matrix
  snapshots and render the findings (json / markdown / sarif / junit).

Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom of
:mod:`abicheck.cli` so the ``@main.group("probe")`` decorator runs.

The issue (#250) sketched ``abicheck compare --matrix old new``; this
lands the same capability under a dedicated ``probe`` group instead,
which keeps the large ``compare`` command untouched and groups the
run/compare halves of the harness together.
"""

from __future__ import annotations

from pathlib import Path

import click

from .checker_policy import compute_verdict
from .checker_types import DiffResult
from .cli import _write_or_echo, main
from .diff_build_config import diff_matrix
from .probe_harness import (
    load_matrix_snapshot,
    load_probe_spec,
    run_probe_matrix,
    write_matrix_snapshot,
)

# Verdict → process exit code, matching the legacy ``compare`` mapping
# documented in CLAUDE.md (0 = compatible, 2 = source break, 4 = ABI break).
_VERDICT_EXIT = {
    "BREAKING": 4,
    "API_BREAK": 2,
}

# Exit code for a comparison that could not be trusted because one of the
# input matrices contained failed probe results (e.g. a missing compiler).
# Distinct from the verdict codes so callers can tell "incomplete input"
# apart from "compatible".
_EXIT_INCOMPLETE_MATRIX = 3


@main.group("probe")
def probe_group() -> None:
    """Build-configuration matrix harness (compile probes, diff matrices)."""


@probe_group.command("run")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--library",
    "library_name",
    required=True,
    help="Library name to stamp into the matrix snapshot.",
)
@click.option(
    "--version", required=True, help="Version label to stamp into the matrix snapshot."
)
@click.option(
    "-o",
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the MatrixSnapshot JSON here (default: stdout).",
)
@click.option(
    "--work-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory for generated .cpp/.o files (default: a temp dir).",
)
@click.option(
    "--no-snapshot",
    is_flag=True,
    default=False,
    help="Compile probes but skip the dumper (routing check only).",
)
def probe_run(
    spec: Path,
    library_name: str,
    version: str,
    out: Path | None,
    work_dir: Path | None,
    no_snapshot: bool,
) -> None:
    """Compile every (configuration × probe) in SPEC into a MatrixSnapshot."""
    probe_spec = load_probe_spec(spec)
    matrix = run_probe_matrix(
        probe_spec,
        library_name=library_name,
        version=version,
        work_dir=work_dir,
        snapshot=not no_snapshot,
    )

    failures = [r for r in matrix.results if r.error]
    summary = (
        f"probe run: {len(probe_spec.configurations)} configuration(s) × "
        f"{len(probe_spec.probes)} probe(s) = {len(matrix.results)} result(s), "
        f"{len(failures)} failure(s)"
    )
    click.echo(summary, err=True)
    for r in failures:
        click.echo(f"  ! {r.configuration_id} / {r.probe_id}: {r.error}", err=True)

    if out is not None:
        write_matrix_snapshot(matrix, out)
        click.echo(f"wrote {out}", err=True)
    else:
        click.echo(matrix.to_json())


@probe_group.command("compare")
@click.argument(
    "old_matrix", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "new_matrix", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "-f",
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown", "sarif", "junit"]),
    default="json",
    show_default=True,
    help="Output format for the matrix-diff findings.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the report here (default: stdout).",
)
@click.option(
    "--policy",
    default="strict_abi",
    show_default=True,
    help="Built-in policy profile for verdict classification.",
)
@click.option(
    "--allow-failures",
    is_flag=True,
    default=False,
    help="Diff even when an input matrix contains failed probe results "
    "(by default the comparison is rejected as untrustworthy).",
)
def probe_compare(
    old_matrix: Path,
    new_matrix: Path,
    fmt: str,
    output: Path | None,
    policy: str,
    allow_failures: bool,
) -> None:
    """Diff two MatrixSnapshots and report build-configuration findings.

    Exit code follows the legacy ``compare`` mapping: 0 = compatible,
    2 = source break, 4 = ABI break. A third code (3) is returned when an
    input matrix contains failed probe results and ``--allow-failures``
    was not given — the env-dependence detector only sees successful
    snapshots, so diffing an incomplete matrix could silently report
    "compatible".
    """
    old = load_matrix_snapshot(old_matrix)
    new = load_matrix_snapshot(new_matrix)

    # Guard against a false "compatible" verdict over an incomplete
    # matrix. ``diff_matrix`` skips results with no snapshot, so a matrix
    # whose probes all failed to compile (e.g. a missing compiler) would
    # diff clean and exit 0 with high confidence. Refuse by default.
    old_failed = [r for r in old.results if r.error]
    new_failed = [r for r in new.results if r.error]
    if (old_failed or new_failed) and not allow_failures:
        for label, failed in (("old", old_failed), ("new", new_failed)):
            for r in failed:
                click.echo(
                    f"  ! {label} {r.configuration_id} / {r.probe_id}: {r.error}",
                    err=True,
                )
        click.echo(
            f"probe compare: {len(old_failed)} failed result(s) in old matrix, "
            f"{len(new_failed)} in new matrix. Refusing to diff an incomplete "
            f"matrix — a clean result would be misleading. Re-run `probe run` "
            f"with a working toolchain, or pass --allow-failures to diff the "
            f"successful subset.",
            err=True,
        )
        raise SystemExit(_EXIT_INCOMPLETE_MATRIX)

    findings = diff_matrix(old, new)

    result = DiffResult(
        old_version=old.version,
        new_version=new.version,
        library=new.library or old.library,
        changes=findings,
        verdict=compute_verdict(findings, policy=policy),
        policy=policy,
    )

    # When proceeding over a partial matrix (--allow-failures), the
    # env-dependence findings cover only the successful subset, so flag
    # the reduced trust rather than letting the report claim high
    # confidence.
    if old_failed or new_failed:
        from .checker_policy import Confidence

        result.confidence = Confidence.LOW
        result.coverage_warnings = [
            f"{len(old_failed) + len(new_failed)} probe result(s) failed; "
            f"env-dependence findings cover only the successful subset."
        ]

    if fmt == "json":
        from .reporter import to_json

        text = to_json(result)
    elif fmt == "markdown":
        from .reporter import to_markdown

        text = to_markdown(result)
    elif fmt == "sarif":
        from .sarif import to_sarif_str

        text = to_sarif_str(result)
    else:  # junit
        from .junit_report import to_junit_xml

        text = to_junit_xml(result)

    _write_or_echo(output, text)

    raise SystemExit(_VERDICT_EXIT.get(result.verdict.value, 0))
