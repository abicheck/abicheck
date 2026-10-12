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

"""Pure helpers for the ``compare-release`` command.

The CLI-only checks that guard a release run's outputs
(``--bundle-facts-out`` collisions) and the Click translation of package
extraction. The bundle stage and the ``--bundle-facts-out`` capture moved to
:mod:`abicheck.workflows.release_bundle` with the release compare owner.

Leaf module: it must not import from :mod:`abicheck.cli` or
:mod:`abicheck.cli_compare_release`. The release report itself is no longer
formatted here: :mod:`abicheck.report.release_document` builds the one
release report and :mod:`abicheck.report.render_release_report` renders
every format from it, reached through
:func:`abicheck.frontends.cli.release_report.render_release_summary`.

``GateOptions``/``resolve_release_gate_options``/``apply_release_gate_pack``/
``_resolve_release_severity_config`` (ADR-064's release-fan-out gate
resolution) live in :mod:`abicheck.policy.release_gate_options` instead --
this package's own home for deciding gate/severity effect
(``abicheck/policy/AGENTS.md``), and also outside the file-size no-growth
budget this module is at. This (a ``frontends``-classified module) reaches
them through :mod:`abicheck.workflows.gate`'s facade rather than importing
``policy`` directly (``frontends -> policy`` is forbidden), and re-exports
them here so every pre-existing import of them from this module keeps
working.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .frontends.cli.release_exit import _exit_compare_release as _exit_compare_release
from .workflows.contract_conflicts import (
    # E-S3 case 3: does a package's declared contract match its own
    # contained binary. Needs `elf_metadata` (`extract`, forbidden directly
    # from `frontends`), so the real logic lives in `workflows`.
    debian_symbols_release_conflict_lines as debian_symbols_release_conflict_lines,  # re-exported, E-S3
)
from .workflows.gate import (
    GateOptions as GateOptions,  # re-exported, ADR-064
    _resolve_release_severity_config as _resolve_release_severity_config,  # re-exported, ADR-064
    apply_release_gate_pack as apply_release_gate_pack,  # re-exported, ADR-064
    resolve_release_gate_options as resolve_release_gate_options,  # re-exported, ADR-064
)

if TYPE_CHECKING:
    from .workflows.extraction import PackageExtractor


#: Four pure helpers that raise nothing moved to ``workflows.
#: release_inputs`` alongside the rest of release input resolution (ADR-061
#: gap D) and are re-exported here under their original private names --
#: there is no Click translation to do for a function that cannot fail.
from .workflows import release_inputs as _release_inputs  # noqa: E402

# Plain assignments, not `from ... import x as _x`: under mypy's
# `no_implicit_reexport` a renaming import is not an explicit re-export, and
# `cli_compare_release.py` imports these names from here.
_debian_symbols_warning = _release_inputs.debian_symbols_warning
_discover_include_roots = _release_inputs.discover_include_roots
_match_release_keys = _release_inputs.match_release_keys
_resolve_release_headers = _release_inputs.resolve_release_headers


def _extract_if_package(
    input_path: Path,
    debug_pkg: Path | None,
    devel_pkg: Path | None,
    make_temp_dir: Callable[[str], Path],
    is_package: Callable[[Path], bool],
    detect_extractor: Callable[[Path], PackageExtractor | None],
) -> tuple[Path, Path | None, Path | None, Path | None, bool]:
    """The Click-translating wrapper over :func:`abicheck.workflows.
    release_inputs.extract_if_package` -- see that module's docstring for
    why the extraction itself is engine-side now. Same messages, same exit
    code."""
    from .errors import ReleaseOperandContentError
    from .workflows.release_inputs import extract_if_package

    try:
        return extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            make_temp_dir,
            is_package,
            detect_extractor,
        )
    except ReleaseOperandContentError as exc:
        raise click.ClickException(str(exc)) from exc


def reject_bundle_facts_out_collision(
    bundle_facts_out: Path | None,
    output: Path | None,
    *secondary_outputs: Path | None,
) -> None:
    """Reject ``--bundle-facts-out`` naming the same file as ``--output``/
    ``-o`` (G38 Phase 2).

    A command-specific extra check, deliberately not
    ``reject_incoherent_secondary_output()``'s job (see that leaf module's
    own docstring) -- without it, ``--bundle-facts-out result.json --output
    result.json`` silently overwrites the requested baseline with the
    report while still reporting success (Codex review).

    *secondary_outputs* is variadic because ``-o`` is repeatable: every
    requested artifact's PATH is checked, not just the first. Checking one
    let ``-o json=a.json -o markdown=b.md --bundle-facts-out
    b.md`` through.
    """
    if bundle_facts_out is None:
        return
    for label, other in (
        ("--output/-o", output),
        *(("--write", out) for out in secondary_outputs),
    ):
        if other is not None and bundle_facts_out.resolve() == other.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from {label}: writing "
                "both to the same file would silently overwrite the "
                "requested bundle-facts baseline with the report."
            )


def reject_bundle_facts_out_dir_collision(
    bundle_facts_out: Path | None,
    output_dir: Path | None,
    old_map: dict[str, Path],
) -> None:
    """Reject ``--bundle-facts-out`` naming a path a per-component export will
    also write (G38 Phase 2, Codex review, fresh evidence).

    ``reject_bundle_facts_out_collision()`` above only knows about
    the export set -- it can't see a per-component export's own
    ``summary.json`` or per-library ``<stem>.json`` files, since those
    paths depend on *output_dir* and (for the per-library case) the
    resolved OLD-side library map, neither known at that earlier
    validation point. Called once ``old_map`` is resolved, before
    ``output_dir`` is created or anything is written into it.
    """
    if bundle_facts_out is None or output_dir is None:
        return
    resolved = bundle_facts_out.resolve()
    summary_path = output_dir / "summary.json"
    if resolved == summary_path.resolve():
        raise click.UsageError(
            "--bundle-facts-out's PATH must differ from the per-component "
            "export's own summary.json: writing both to the same file would silently "
            "overwrite the requested bundle-facts baseline with the "
            "per-library summary report."
        )
    for name, old_path in old_map.items():
        lib_path = output_dir / f"{old_path.stem}.json"
        if resolved == lib_path.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from the per-component "
                f"export's own report for {name!r} ({lib_path}): writing "
                "both to the same file would silently overwrite whichever "
                "was written second."
            )


def _cleanup_temp_dirs(temp_dir_paths: list[str]) -> None:
    """Remove temporary directories created during package extraction.

    Always cleans up now -- ``--keep-extracted`` is gone (Phase 7d,
    ADR-068 D5), no replacement."""
    import shutil as _shutil

    for td_path in temp_dir_paths:
        _shutil.rmtree(td_path, ignore_errors=True)
