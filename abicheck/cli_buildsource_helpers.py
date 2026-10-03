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

"""Plain helper functions extracted from ``cli_buildsource``.

These cover the ``compare``/``dump`` build-source
integration (embedded-evidence diffing, layer-coverage reporting, capability
reporting) and the source-graph load/localize helpers. They were extracted from
``cli_buildsource.py`` to keep that module under the 2000-line hard cap. They
must NOT import from ``abicheck.cli_buildsource`` or ``abicheck.cli`` (that would
create an import cycle rejected by the CI gate) — this is a leaf module.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .buildsource.pack import BuildSourcePack
from .cli_buildsource_merge import (
    _exported_symbols_from_snapshot as _exported_symbols_from_snapshot,
    _ingest_inputs_pack_snapshot as _ingest_inputs_pack_snapshot,
)
from .errors import SnapshotError
from .workflows.extraction import (
    purge_external_outputs as purge_external_outputs,
)

if TYPE_CHECKING:
    from .checker_types import Change, DiffResult
    from .model import AbiSnapshot
    from .workflows.policy_file import PolicyFile


def _echo(message: str) -> None:
    """The CLI's sink for `buildsource.evidence_report`'s report lines.

    stderr, deliberately: the D7 coverage/capability report must cover every
    output format without polluting a ``-o json=...`` stdout that a consumer
    pipes into a parser.
    """
    click.echo(message, err=True)


def _resolve_side_pack(
    build_info: Path | None,
    sources: Path | None,
    snap: AbiSnapshot | None,
) -> BuildSourcePack | None:
    """CLI adapter over ``buildsource.evidence_report.resolve_side_pack``.

    Translates the engine's ``SnapshotError`` into a plain ``ClickException``
    (**exit 1** -- operational, not a usage error: the command line was
    well-formed and the pack was not), the same translation
    :func:`_load_pack_or_raise` makes for the single-pack case. Message
    unchanged. Pinned by ``tests/test_evidence_report_contract.py``.

    Also supplies the stderr sink for a Flow-2 pack's non-fatal validation
    findings, which the engine returns through a callback rather than
    printing itself.
    """
    from .buildsource.evidence_report import resolve_side_pack

    try:
        return resolve_side_pack(build_info, sources, snap, on_warning=_echo)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def diff_embedded_build_source(
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    collect_mode: str,
    new_snapshot: AbiSnapshot,
    old_snapshot: AbiSnapshot | None = None,
    policy_file: PolicyFile | None = None,
) -> tuple[list[Change], list[dict[str, object]], dict[str, object]]:
    """CLI adapter over ``buildsource.evidence_report.diff_embedded_build_source``.

    Supplies the stderr sink the engine deliberately does not own; a caller
    that wants no output calls the engine directly with no sink (as
    ``service_compare_pipeline`` does). See that function for the behaviour.
    """
    from .buildsource.evidence_report import diff_embedded_build_source as _diff

    try:
        return _diff(
            old_build_info,
            new_build_info,
            old_sources,
            new_sources,
            collect_mode,
            new_snapshot,
            old_snapshot,
            policy_file,
            on_output=_echo,
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def prepare_embedded_build_source(
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    collect_mode: str,
    extra_changes: list[Change] | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    policy_file: PolicyFile | None = None,
) -> tuple[
    list[Change] | None, list[dict[str, object]], dict[str, object], list[Change]
]:
    """CLI adapter over ``buildsource.evidence_report.prepare_embedded_build_source``.

    Same stderr sink and same ``SnapshotError`` -> ``ClickException`` (exit 1)
    translation as :func:`diff_embedded_build_source` above.
    """
    from .buildsource.evidence_report import prepare_embedded_build_source as _prepare

    try:
        return _prepare(
            old_snapshot,
            new_snapshot,
            collect_mode,
            extra_changes,
            old_build_info,
            new_build_info,
            old_sources,
            new_sources,
            policy_file,
            on_output=_echo,
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def attach_evidence_metrics(
    result: DiffResult,
    metrics: dict[str, object],
    injected_changes: list[Change],
) -> None:
    """CLI adapter over ``buildsource.evidence_report.attach_evidence_metrics``."""
    from .buildsource.evidence_report import attach_evidence_metrics as _attach

    _attach(result, metrics, injected_changes, on_output=_echo)


def _load_pack_or_raise(evidence_dir: Path) -> BuildSourcePack:
    """CLI adapter over ``buildsource.pack_load.load_pack_or_raise``.

    Translates the engine's ``SnapshotError`` into a plain ``ClickException``
    (**exit 1** -- operational, not a usage error: the command line was
    well-formed and the pack was not). Message unchanged.
    """
    from .workflows.extraction import load_pack_or_raise

    try:
        return load_pack_or_raise(evidence_dir)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _is_inputs_pack_dir(path: Path | None) -> bool:
    """Alias for ``buildsource.inputs_pack.is_inputs_pack_dir`` (ADR-035 D5),
    which has owned it since ADR-061 Phase 3."""
    from .workflows.extraction import is_inputs_pack_dir

    return is_inputs_pack_dir(path)


def _load_inputs_pack_or_raise(
    path: Path, *, exported_symbols: Iterable[str] = ()
) -> BuildSourcePack:
    """CLI adapter over ``buildsource.pack_load.load_inputs_pack_or_raise``.

    Same exit-1 translation as :func:`_load_pack_or_raise`, plus the stderr
    sink for the loader's non-fatal findings -- the engine returns those
    through a callback rather than owning a stream.
    """
    from .workflows.extraction import load_inputs_pack_or_raise

    try:
        return load_inputs_pack_or_raise(
            path,
            exported_symbols=exported_symbols,
            on_warning=lambda message: click.echo(message, err=True),
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_side_pack_input(
    path: Path | None, *, exported_symbols: Iterable[str] = ()
) -> BuildSourcePack | None:
    """Load a compare-side out-of-band pack, auto-detecting its pack kind."""
    if path is None:
        return None
    if _is_inputs_pack_dir(path):
        return _load_inputs_pack_or_raise(path, exported_symbols=exported_symbols)
    return _load_pack_or_raise(path)
