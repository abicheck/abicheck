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

"""CLI — ``stable-abi`` command (CPython Limited-API / ``abi3`` audit, G14).

Audits a single CPython extension module (Cython, pybind11, nanobind, or a
hand-written C extension) against the stable-ABI contract: it enumerates the
CPython C-API symbols the module **imports** from ``libpython`` and flags any
that are outside the Limited API for a target ``Py_LIMITED_API`` floor. This is
the compatibility surface the export table cannot see — an ``abi3`` module
exports essentially just ``PyInit_<mod>``, but importing a private ``_Py*``
symbol (or one newer than its declared floor) makes it fail to import on an
older interpreter with an ``undefined symbol`` error.

    abicheck stable-abi ext.abi3.so --abi3 3.9

Split out of :mod:`abicheck.cli` per the "Adding a new top-level command"
convention; imported for side-effect at the bottom of :mod:`abicheck.cli` so the
``@main.command("stable-abi")`` decorator runs.

Exit codes: ``0`` = clean (all imports within the target stable ABI), ``1`` =
one or more non-stable imports found, ``2`` = the input is not a recognisable
CPython extension module, ``3`` = incomplete audit — an ``abi3`` module was
given without a resolvable target floor (pass ``--abi3`` so the stable-symbol
floor check can run and the module can be certified).
"""

from __future__ import annotations

from pathlib import Path

import click

from . import stable_abi
from .checker_policy import ChangeKind, compute_verdict
from .checker_types import Change, DiffResult
from .cli import _write_or_echo, main
from .diff_helpers import make_change
from .stable_abi import StableAbiStatus

_EXIT_VIOLATIONS = 1
_EXIT_NOT_EXTENSION = 2
#: abi3 module audited without a resolvable target floor — the floor check could
#: not run, so the audit is incomplete and cannot certify the module.
_EXIT_NO_FLOOR = 3


def _audit_imports(
    module_name: str,
    cpython_imports: list[str],
    abi3_floor: tuple[int, int] | None,
) -> tuple[list[Change], list[str]]:
    """Classify a module's CPython imports → (findings, unknown symbols).

    Emits aggregated :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` findings —
    private/unstable imports, imports newer than the target floor, and public
    ``Py*`` symbols absent from the authoritative Stable-ABI set — so a
    SARIF/JUnit consumer sees distinct, actionable rows without one line per
    symbol. The unknown-public symbols are also returned so the CLI can note that
    they are treated as violations but *could* be newer than the vendored CPython
    data (refresh to confirm) — the one benign case.
    """
    private: list[str] = []
    above_floor: list[str] = []
    unknown: list[str] = []
    for name in cpython_imports:
        status, added = stable_abi.classify(name, abi3_floor)
        if status is StableAbiStatus.PRIVATE:
            private.append(name)
        elif status is StableAbiStatus.ABOVE_FLOOR:
            above_floor.append(
                f"{name} (added {stable_abi.format_version(added)})"
                if added is not None
                else name
            )
        elif status is StableAbiStatus.UNKNOWN:
            unknown.append(name)

    findings: list[Change] = []
    for group in (private, above_floor, unknown):
        if group:
            findings.append(
                make_change(
                    ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                    symbol=f"python:{module_name}",
                    name=module_name,
                    detail=", ".join(sorted(group)),
                    new_value=sorted(group),
                )
            )
    return findings, sorted(unknown)


@main.command("stable-abi")
@click.argument("ext", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--abi3",
    "abi3",
    default=None,
    help="Target Py_LIMITED_API floor, e.g. `3.9`. Imports newer than this are "
    "flagged even when they are stable-ABI symbols. Defaults to the module's "
    "own declared floor (from its SOABI tag) when omitted.",
)
@click.option(
    "-f",
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown", "sarif", "junit"]),
    default="markdown",
    show_default=True,
    help="Output format for the audit findings.",
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
def stable_abi_cmd(
    ext: Path,
    abi3: str | None,
    fmt: str,
    output: Path | None,
    policy: str,
) -> None:
    """Audit a CPython extension's imported C-API against the stable ABI (abi3).

    EXT is a compiled extension module (``.so`` / ``.pyd`` / ``.dylib``) or a
    saved abicheck snapshot of one.
    """
    from .cli_resolve import _resolve_input

    snap = _resolve_input(ext, [], [], version="", lang="c++")

    python_ext = snap.python_ext
    if python_ext is None or not python_ext.is_extension:
        click.echo(
            f"{ext}: not a recognisable CPython extension module "
            f"(no PyInit_* export and no CPython C-API imports).",
            err=True,
        )
        raise SystemExit(_EXIT_NOT_EXTENSION)

    # Target floor: explicit --abi3 wins, else the module's own declared floor —
    # but ONLY when the module is actually an abi3 build (its floor came from a
    # `cpXY-abi3` tag). For a version-specific `foo.cpython-311.so`,
    # `declared_abi3` is the *interpreter* minor, not a Limited-API floor, so it
    # must NOT be treated as an abi3 target; the floor stays unresolved and the
    # audit is reported incomplete unless the user passes --abi3 (Codex review).
    abi3_floor: tuple[int, int] | None = None
    if abi3 is not None:
        abi3_floor = stable_abi.parse_abi3_version(abi3)
        if abi3_floor is None:
            raise click.BadParameter(f"invalid --abi3 version: {abi3!r}")
    elif python_ext.limited_api:
        abi3_floor = python_ext.declared_abi3

    module_name = python_ext.module_name or python_ext.init_symbol or ext.name
    findings, unknown = _audit_imports(
        module_name, python_ext.cpython_imports, abi3_floor
    )

    # Without a resolvable floor the stable-symbol (above-floor) check cannot
    # run — only private imports are caught. This happens for ANY tagless
    # artifact (a bare `foo.pyd` or a snapshot with no SOABI tag), not just ones
    # whose name already says `abi3`: we cannot rule out that it is a stable-ABI
    # build whose floor lives in the wheel tag. Treat every unresolved floor as
    # an incomplete audit and require --abi3, so a cp39-abi3 module importing a
    # 3.11 symbol is not silently accepted (Codex review).
    floor_check_skipped = abi3_floor is None

    floor_txt = stable_abi.format_version(abi3_floor) if abi3_floor else "unset"
    click.echo(
        f"stable-abi: {module_name} — {len(python_ext.cpython_imports)} CPython "
        f"import(s), target abi3 floor {floor_txt}, "
        f"{len(findings)} finding(s), {len(unknown)} unknown symbol(s).",
        err=True,
    )
    if floor_check_skipped:
        click.echo(
            "  ERROR: no target floor — the module declares no Limited-API floor "
            "(a bare `.abi3.so`, a tagless `.pyd`, or a version-specific build), "
            "so the stable-symbol floor check could NOT run (only private/unstable "
            "imports were checked). The audit is INCOMPLETE and cannot certify the "
            "module (exit 3). Pass --abi3 <version> (e.g. the wheel's cpXY-abi3 "
            "tag) to verify imported symbols against that floor.",
            err=True,
        )
    if unknown:
        click.echo(
            "  note: the following public Py* imports are NOT in the vendored "
            "Stable-ABI set, so they are counted as violations — they are outside "
            "the Limited API (e.g. PyUnicode_AsUTF8). The one benign case is a "
            "symbol newer than the vendored CPython data; refresh it to confirm: "
            + ", ".join(unknown[:20])
            + (" …" if len(unknown) > 20 else ""),
            err=True,
        )

    # The report must reflect an incomplete audit too: an abi3/tagless module run
    # without a resolvable floor exits 3, but if it has no concrete import
    # findings the DiffResult would otherwise carry zero changes — a JUnit/JSON/
    # SARIF consumer would then read the audit as passed (Codex review). Add a
    # synthetic finding describing the incomplete state so machine outputs show
    # it. Kept OUT of `findings` so the exit code stays 3 (incomplete), not 1.
    report_changes = list(findings)
    if floor_check_skipped and not findings:
        report_changes.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=f"python:{module_name}",
                name=module_name,
                description=(
                    f"stable-abi audit INCOMPLETE for '{module_name}': no "
                    "Py_LIMITED_API floor could be resolved, so imported symbols "
                    "were not verified against a stable-ABI floor. Pass --abi3 "
                    "<version> to certify the module."
                ),
            )
        )

    result = DiffResult(
        old_version=snap.version or "",
        new_version=snap.version or "",
        library=snap.library or str(ext),
        changes=report_changes,
        verdict=compute_verdict(report_changes, policy=policy),
        policy=policy,
    )

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
        from .severity import PRESET_STRICT

        # Every audit finding is a hard violation (the command exits 1 on any),
        # so they must render as JUnit <failure> elements. Without escalation the
        # shared writer emits <failure> only for breaking/error kinds, and
        # PYTHON_STABLE_ABI_VIOLATION is RISK — a JUnit-only CI dashboard would
        # then show failures="0" for a failing audit (Codex review). The strict
        # config classifies RISK (potential_breaking) as error.
        text = to_junit_xml(result, severity_config=PRESET_STRICT)

    _write_or_echo(output, text)

    # Exit precedence: a concrete violation (1) outranks an incomplete audit.
    # When the module is abi3 but no target floor could be resolved, the
    # stable-symbol floor check did not run — the audit is INCOMPLETE and cannot
    # certify the module, so it must NOT exit 0 (a CI job would otherwise accept
    # a cp39-abi3 artifact importing a 3.11 symbol). Fail with a distinct code.
    if findings:
        raise SystemExit(_EXIT_VIOLATIONS)
    if floor_check_skipped:
        raise SystemExit(_EXIT_NO_FLOOR)
    raise SystemExit(0)
