# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Loading an out-of-band pack, with the engine's own error vocabulary.

ADR-061 Phase 3. These loaders used to live in ``cli_buildsource_helpers``
and raise ``click.ClickException``, which meant the engine
(``service_input_resolution``) caught a *CLI* exception type and translated
it -- the inversion in its purest form, and the last thing blocking
``embed_build_source`` from moving off the CLI layer.

The exit codes that ride on this are not incidental, so they are stated here
rather than left to the call sites to remember:

* An invalid pack is an **operational** failure, not a usage error. The
  command line was well-formed; the data was not. It raises
  :class:`~abicheck.errors.SnapshotError`, which the CLI renders as a plain
  ``click.ClickException`` -- **exit 1**. It must never become a
  ``UsageError`` (exit 64), which would tell a CI consumer the invocation was
  wrong when it was the pack.
* Warnings are returned through *on_warning* rather than printed. An engine
  module has no business owning a stream; the CLI passes a callback that
  writes to stderr, and a Tier-2 caller passes nothing.

``tests/test_build_source_embed_errors.py`` pins both, at the CLI and at the
function boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from ..errors import SnapshotError
from . import pack_io
from .pack import BuildSourcePack


def load_pack_or_raise(evidence_dir: Path) -> BuildSourcePack:
    """Load a classic :class:`BuildSourcePack`, or raise ``SnapshotError``."""
    try:
        return pack_io.load(evidence_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SnapshotError(f"Invalid evidence pack at {evidence_dir}: {exc}") from exc


def load_inputs_pack_or_raise(
    path: Path,
    *,
    exported_symbols: Iterable[str] = (),
    on_warning: Callable[[str], None] | None = None,
) -> BuildSourcePack:
    """Validate and ingest an ``abicheck_inputs/`` directory into a BuildSourcePack.

    Validation happens automatically whenever the pack is consumed -- there is
    no separate ``inputs validate`` command to run first (ADR-043 D1). A
    structurally invalid pack is a hard error; non-fatal findings go to
    *on_warning*.

    ``exported_symbols`` — the analyzed binary's L0 exports — seed the L4
    decl→symbol linking so ``source_decl_to_binary_symbol`` resolves against the
    DSO instead of leaving ``matched_symbols=0`` (AC-003). When empty (e.g. a
    source-only pack with no artifact side yet), the surface is relinked against
    the artifact exports later during ``merge``.
    """
    from .inputs_pack import ingest_inputs_pack
    from .inputs_validate import validate_inputs_pack

    report = validate_inputs_pack(path)
    if report.errors:
        raise SnapshotError(
            f"Invalid abicheck_inputs/ pack at {path}: " + "; ".join(report.errors)
        )
    if on_warning is not None:
        for warning in report.warnings:
            on_warning(f"warning: {path}: {warning}")
    return ingest_inputs_pack(path, exported_symbols=exported_symbols).pack
