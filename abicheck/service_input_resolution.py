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

"""One input side, resolved — the primitives ``compare`` and ``dump`` share.

G33 Phase 5 (ADR-055 D1's shape, applied to ``dump``). ``compare`` already had
exactly one resolution implementation for a *pair* of inputs
(:mod:`abicheck.service_compare_pipeline`); ``dump`` had none — the MCP
``abi_dump`` tool called :func:`abicheck.service.resolve_input` with a fixed
five-argument subset and could not express ``--depth``/``--sources``/
``--build-info``/``--dump-manifest``/a :class:`CompileContext` at all. Giving
``dump`` a typed request meant either a second copy of the per-side work
``compare`` does, or lifting that work out of the pair. This module is the
second option: everything here was ``service_compare_pipeline``'s, moved
verbatim and re-expressed for *one* side, so a change to how an input resolves
lands on both commands at once.

The pair-shaped decisions deliberately stayed behind in
``service_compare_pipeline``: the pair-wide C++20 dialect override exists
precisely because two sides must agree on a standard, and the concurrency rule
is about two extractions running at once. Neither means anything for a single
dump.

Same mechanical note as ``service_compare_pipeline``: everything this module
needs from ``service`` is looked up **through the module object at call time**
(``from . import service`` inside the function), never bound at import time, so
``monkeypatch.setattr(service, "resolve_input", ...)`` keeps working. The
function-local import also keeps this module out of ``service``'s import cycle
(AGENTS.md "What NOT to do").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .api_types import InputSpec
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_side_snapshot",
]


def is_raw_source_tree(path: Path | None) -> bool:
    """True for a source tree needing real extraction — not a prebuilt pack."""
    from .buildsource.inline import is_pack_dir
    from .cli_buildsource_helpers import _is_inputs_pack_dir

    return path is not None and not (is_pack_dir(path) or _is_inputs_pack_dir(path))


def reject_hybrid_source_frontend(
    depth: str | None,
    sides: Sequence[tuple[InputSpec, SideEvidence]],
    header_backend: str,
) -> None:
    """Reject ``depth='source'`` under the ``hybrid`` AST frontend.

    ``hybrid`` has no real ``embed_build_source`` extractor, so a raw source
    tree needing real extraction under it is a usage error rather than a
    silently weaker result. A prebuilt pack or a bare ``build_info`` never
    feeds L4, so neither is rejected. Mirrors ``cli.py``'s own ``--depth
    source`` + ``--ast-frontend hybrid`` ``UsageError``.
    """
    from . import service_compare_evidence as _sce

    if depth is None or depth.lower() != "source":
        return
    for side, evidence in sides:
        if (
            is_raw_source_tree(side.sources)
            and _sce.effective_frontend(evidence.compile, header_backend) == "hybrid"
        ):
            raise ValidationError(
                "depth='source' is incompatible with the 'hybrid' AST "
                "frontend: L4 source-ABI replay has no dual-backend hybrid "
                "extractor. Use 'castxml' or 'clang' for a depth='source' "
                "request."
            )


def resolve_side_snapshot(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str,
    header_backend: str,
    fmt: str | None,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    include_labels: dict[Path, str] | None = None,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Resolve one :class:`InputSpec` into an :class:`AbiSnapshot`.

    Runs :func:`abicheck.service.resolve_input` with this side's already-resolved
    :class:`~abicheck.service_compare_evidence.SideEvidence` (headers, compile
    context, dump manifest), then embeds the side's inline L3-L5 build/source
    evidence when it declares any.
    """
    from . import service

    snap = service.resolve_input(
        side.path,
        evidence.headers,
        list(side.includes),
        side.version,
        lang,
        is_elf=True if fmt == "elf" else None,
        pdb_path=side.pdb,
        debug_roots=list(side.debug_roots) or None,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        header_backend=header_backend,
        compile=evidence.compile,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        include_dependencies=side.include_dependencies,
        dump_manifest=evidence.dump_manifest,
        follow_linker_scripts=side.follow_linker_scripts,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        include_labels=include_labels,
        notify=notify,
    )
    if side.sources or side.build_info:
        embed_side_build_source(
            snap,
            side,
            evidence,
            header_backend,
            public_headers,
            public_header_dirs,
        )
    return snap


def embed_side_build_source(
    snap: AbiSnapshot,
    side: InputSpec,
    evidence: SideEvidence,
    header_backend: str,
    public_headers: list[Path],
    public_header_dirs: list[Path],
) -> None:
    """Embed one side's inline L3-L5 build/source evidence into *snap*.

    Same public roots as ``resolve_input``, plus a ``dump_manifest``'s
    *declared-public* roots only (a manifest's project-owned TU includes are
    private, hence ``dump_manifest_public_roots`` rather than
    ``dump_manifest_header_roots``).

    A malformed pack raises ``click.ClickException`` deep inside
    ``embed_build_source`` — no place in this Tier-2 API's
    ``ValidationError``/``SnapshotError`` contract, so it is translated here
    (Codex review).
    """
    import click

    from . import service_compare_evidence as _sce
    from .cli_buildsource import embed_build_source
    from .dumper_scoping import dump_manifest_public_roots

    try:
        embed_build_source(
            snap,
            build_info=side.build_info,
            sources=side.sources,
            collect_mode=evidence.collect_mode,
            extractor=_sce.effective_frontend(evidence.compile, header_backend),
            public_headers=tuple(str(p) for p in public_headers),
            public_header_dirs=tuple(str(p) for p in public_header_dirs)
            + tuple(str(p) for p in dump_manifest_public_roots(evidence.dump_manifest)),
            quiet=True,
        )
    except click.ClickException as exc:
        raise SnapshotError(str(exc)) from exc


def enforce_requested_depth(
    depth: str | None, sides: Sequence[tuple[str, AbiSnapshot]]
) -> None:
    """Fail when an explicit ``depth`` was requested but not actually reached.

    Mirrors ``dump``'s own ``check_requested_depth_satisfied`` hard-fail, but
    raises ``ValidationError`` (a Tier-2 API has no ``ClickException``
    concept). Without it, a raw input that could not reach the requested rung
    — no usable compile database, extractor, or linkable declarations —
    silently produced whatever weaker evidence ``embed_build_source`` managed.

    *sides* is ``(label, snapshot)`` pairs so the message names the side that
    fell short: ``compare`` passes both of its own, ``dump`` its single input.

    Known, accepted limitation (Codex review, not fixed here): this is a
    floor, not a ceiling. An input that is an already-serialized JSON snapshot
    with richer embedded evidence than ``depth`` requested still carries all of
    it — ``resolve_input``'s ``fmt == "json"`` branch returns
    ``load_snapshot(path)`` verbatim, matching the CLI's own long-documented
    default, which ``--depth`` has never projected down for a pre-built
    snapshot either.
    """
    if depth is None:
        return
    from .cli_dump_helpers import _DEPTH_RANK, _gated_source_label

    # validate() already restricts depth to USER_DEPTHS.
    requested_rank = _DEPTH_RANK.get(depth.lower(), 0)
    for side_label, snap in sides:
        effective = _gated_source_label(snap.build_source, snap)
        if _DEPTH_RANK.get(effective, 0) < requested_rank:
            raise ValidationError(
                f"depth={depth!r} was requested for the {side_label} "
                f"side but the resolved snapshot only reached {effective!r} "
                "evidence depth. Supply the evidence this rung needs (headers, "
                "a build/compile database, or --sources with linkable "
                "declarations) or lower depth to match what is actually "
                "available."
            )
