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

"""``compare``'s raw ``--old/new-sources``/``--build-info`` sides, as request data.

A raw source checkout or build directory (not a prebuilt pack) is collected
inline, by extracting its side's binary at the run's collect mode -- what
``abicheck dump --sources S --build-info B`` does. ``compare`` used to do it
by running a nested ``dump`` command and comparing the temporary snapshot it
wrote. Now the shared resolution (``workflows.compare.
resolve_compare_request``) collects the side itself, and this module states
the front-end decisions that ``dump`` command used to make for it, on the
side's ``InputSpec``:

* **which project config applies, and whether it is trusted** -- ``dump``'s
  own rule (``--config``, else the ``--sources`` tree root, else the nearest
  one above the working directory); only an explicit ``--config`` may run
  ``build.query`` (``DumpExecutionOptions.build_config_explicit``);
* **that config's passive settings** -- ``scope.exclude_headers`` and
  ``build.compile_db_filter`` when the run states none;
* **the tree's ``compile:`` block**, merged over the run's compile context
  with the CLI winning (``workflows.compile_config.merge_compile_config``);
* **``dump``'s execution plan** -- the ``-p`` compile-database match, the L2
  seed run at the collect mode, the L4 replay on the folded compiler, and
  the changed-path seed;
* **``--depth source`` under a ``hybrid`` frontend** is rejected on the
  side's merged frontend (``reject_hybrid_source_frontend``, the rule
  resolution applies to a request that states ``depth``).

Which inputs the collection consumes is ``inline_dump_evidence_routing``'s
rule; what it does not consume (a pack beside no raw tree) is returned for the
out-of-band pack path. A side whose raw evidence the run cannot collect from
(collect mode ``off``, or a stored-snapshot operand) is left to resolution's
own ``without_unusable_raw_evidence``, which ignores it with a note.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...dry_run_estimate import CompileContext
    from ..dump.project_config import DumpDebugConfig
    from ..request_inputs import InputSpec
    from ..service_requests import CompareRequest

__all__ = ["InlineEvidence", "InlineEvidenceRun", "OutOfBandPacks"]


@dataclass(frozen=True)
class InlineEvidenceRun:
    """The run-wide values each raw side's collection is resolved against."""

    #: Registers a restoration to run when the invocation closes (the CLI
    #: passes ``ctx.call_on_close``): the tree config's compile env toggles.
    on_close: Callable[[Callable[[], None]], object]
    #: A user-facing note (the session host's ``notify``): the tree config's
    #: compile warnings go here rather than to the process stderr.
    notify: Callable[[str], None]
    #: Whether ``nostdinc`` was stated on the command line.
    nostdinc_explicit: bool
    #: The run's compile context *with* its frontend (the side's own
    #: ``--ast-frontend old=/new=`` override replaces it per side).
    compile_context: CompileContext
    old_header_backend: str | None
    new_header_backend: str | None
    collect_mode: str
    #: ``compare``'s raw ``--depth``. The resolution request states only the
    #: collect mode, so the ``--depth source`` + ``hybrid`` rule is applied
    #: here, per side, on the side's merged frontend.
    depth: str | None
    changed_paths: tuple[str, ...]
    #: ``compare``'s raw ``--config`` -- ``None`` unless the operator named
    #: one; never the auto-discovered path, which must not become trusted.
    config: Path | None
    lang: str
    debug: DumpDebugConfig
    old_debug_roots: tuple[Path, ...]
    new_debug_roots: tuple[Path, ...]


@dataclass(frozen=True)
class InlineEvidence:
    """``compare``'s per-side ``--sources``/``--build-info`` inputs."""

    old_sources: Path | None
    old_build_info: Path | None
    new_sources: Path | None
    new_build_info: Path | None

    def apply(
        self,
        request: CompareRequest,
        run: InlineEvidenceRun,
    ) -> CompareRequest:
        """*request* with the run's collect mode and each collectable raw side
        stated as ``dump`` would collect it.

        Raw evidence the run cannot collect from is decided first
        (``without_unusable_raw_evidence``), before any of its config is
        read: such a side carries its raw inputs unchanged, for resolution
        to ignore with a note and for classification to still count as given
        (the ``--depth`` floor's liveness), and a pack beside it goes back to
        the out-of-band path (:meth:`out_of_band`), as it did when the tree
        was never collected.
        """
        from ...service_compare_evidence import without_unusable_raw_evidence

        staged = dataclasses.replace(
            request,
            resolved_collect_mode=run.collect_mode,
            old=dataclasses.replace(
                request.old,
                sources=_raw(self.old_sources),
                build_info=_raw(self.old_build_info),
            ),
            new=dataclasses.replace(
                request.new,
                sources=_raw(self.new_sources),
                build_info=_raw(self.new_build_info),
            ),
        )
        collectable = without_unusable_raw_evidence(staged, lambda _note: None)
        old = _side(
            request.old,
            self.old_sources,
            self.old_build_info,
            collectable.old,
            run,
            run.old_header_backend,
            run.old_debug_roots,
        )
        new = _side(
            request.new,
            self.new_sources,
            self.new_build_info,
            collectable.new,
            run,
            run.new_header_backend,
            run.new_debug_roots,
        )
        return dataclasses.replace(
            request, resolved_collect_mode=run.collect_mode, old=old, new=new
        )

    def out_of_band(self, request: CompareRequest) -> OutOfBandPacks:
        """The packs among these inputs that *request* (as :meth:`apply`
        stated it) does not consume, for the out-of-band pack path.

        A request never carries a pack as ``sources``; it carries a pack
        ``build_info`` only beside a raw tree it collects (the pack seeds
        that tree's L4 replay). A raw input is never a pack, whether it was
        collected or ignored.
        """

        def pack(path: Path | None, consumed: Path | None) -> Path | None:
            if path is None or _raw(path) is not None or path == consumed:
                return None
            return path

        return OutOfBandPacks(
            old_build_info=pack(self.old_build_info, request.old.build_info),
            old_sources=pack(self.old_sources, None),
            new_build_info=pack(self.new_build_info, request.new.build_info),
            new_sources=pack(self.new_sources, None),
        )


@dataclass(frozen=True)
class OutOfBandPacks:
    """Each side's prebuilt packs no inline collection consumed."""

    old_build_info: Path | None
    old_sources: Path | None
    new_build_info: Path | None
    new_sources: Path | None


def _raw(path: Path | None) -> Path | None:
    from ..extraction import is_raw_evidence_input

    return path if is_raw_evidence_input(path) else None


def _side(
    side: InputSpec,
    sources: Path | None,
    build_info: Path | None,
    collectable: InputSpec,
    run: InlineEvidenceRun,
    header_backend_override: str | None,
    debug_roots: tuple[Path, ...],
) -> InputSpec:
    """*side* stated for inline collection. *collectable* is the side after
    raw evidence the run cannot collect from was dropped."""
    from .inline_evidence_routing import inline_dump_evidence_routing

    sources_raw = collectable.sources is not None
    build_info_raw = collectable.build_info is not None
    # A raw input the run ignores is neither collected nor kept as a pack.
    dump_sources, dump_build_info, _kept_sources, _kept_build_info = (
        inline_dump_evidence_routing(
            sources if sources_raw or _raw(sources) is None else None,
            build_info if build_info_raw or _raw(build_info) is None else None,
            sources_raw,
            build_info_raw,
        )
    )
    if dump_sources is None and dump_build_info is None:
        # Nothing collectable: any raw input stays for resolution to ignore.
        return dataclasses.replace(
            side, sources=_raw(sources), build_info=_raw(build_info)
        )
    from ...service_compare_evidence import SideEvidence, dump_cli_compile_db_path
    from ..artifact.dump_execution_options import DumpExecutionOptions
    from ..artifact.resolve import reject_hybrid_source_frontend
    from ..compile_config import merge_compile_config
    from ..dump.project_config import resolve_dump_project_config

    # `run.lang` is always resolved already, so `lang_default` is never read.
    project = resolve_dump_project_config(
        run.on_close,
        build_config=run.config,
        sources=dump_sources,
        lang=run.lang,
        lang_default=run.lang,
        exclude_headers=side.exclude_headers,
        resolved_debug=run.debug,
        debug_roots=debug_roots,
    )
    # The tree's own `compile:` block, merged over the run's context with the
    # CLI winning -- explicitness as compare's command line stated it.
    backend = header_backend_override or run.compile_context.frontend
    nostdinc_explicit = run.nostdinc_explicit
    compile_context, includes = merge_compile_config(
        dataclasses.replace(run.compile_context, frontend=backend),
        tuple(side.includes),
        None,
        sources=dump_sources,
        frontend_explicit=header_backend_override is not None,
        nostdinc_explicit=nostdinc_explicit or run.compile_context.nostdinc,
        notify=run.notify,
    )
    collected = dataclasses.replace(side, sources=dump_sources)
    reject_hybrid_source_frontend(
        run.depth,
        (
            (
                collected,
                SideEvidence(
                    headers=[],
                    compile=compile_context,
                    collect_mode=run.collect_mode,
                    dump_manifest=None,
                ),
            ),
        ),
        backend,
    )
    return dataclasses.replace(
        collected,
        build_info=dump_build_info,
        compile=compile_context,
        includes=includes,
        exclude_headers=project.exclude_headers,
        compile_db_filter=project.compile_db_filter,
        execution=DumpExecutionOptions(
            build_config=project.path,
            build_config_explicit=project.explicit,
            allow_build_query=True,
            compile_db=dump_cli_compile_db_path(dump_build_info, tuple(side.headers)),
            compile_db_filter=project.compile_db_filter,
            seed_collect_mode=run.collect_mode,
            source_frontend_from_folded_context=True,
            changed_paths=run.changed_paths,
        ),
    )
