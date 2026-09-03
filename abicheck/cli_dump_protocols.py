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

"""The `Protocol` types describing the callables `cli.py`'s `dump_cmd` passes
into `perform_elf_dump` (`cli_dump_helpers.py`) and `handle_non_elf_dump`
(`cli_dump_non_elf.py`), extracted into their own genuine leaf module (Codex
review, fresh evidence).

Both functions receive `dump_native_binary`/`stamp_provenance`/
`write_snapshot_output` (and `perform_elf_dump` additionally
`expand_header_inputs`/`populate_dependency_info`) as parameters from `cli.py`
rather than importing them -- the AST-based import-cycle gate counts *any*
import (including a lazy function-body one), so importing them directly would
close a `cli -> ... -> cli` cycle. These Protocols existed in
`cli_dump_helpers.py` before `handle_non_elf_dump` was split out into
`cli_dump_non_elf.py`; keeping them there and having the new sibling import
them back would have joined `cli_dump_non_elf.py` to the pre-existing
CLI-registration import-cycle SCC for no structural reason -- this module has
no other edge into that cluster at all (it imports only leaves:
`.workflows.artifact`, `.buildsource.l2_seed`, `.dumper_clang`,
`.dumper_clang_streaming`, `.errors`, `.header_utils`). Moving the shared
Protocol definitions to this standalone leaf instead means neither
`cli_dump_helpers.py` nor `cli_dump_non_elf.py` needs to import the other for
this purpose, so `cli_dump_non_elf.py` never joins the cycle in the first
place -- no `IMPORT_CYCLE_ALLOWLIST` entry needed for it (unlike
`service_header_graph_attach.py`'s own split, where the equivalent avoidance
isn't available -- see that module's own docstring for why).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from .model import AbiSnapshot


class ExpandHeaderInputs(Protocol):
    def __call__(self, inputs: list[Path]) -> list[Path]: ...


class PopulateDependencyInfo(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        so_path: Path,
        search_paths: list[Path],
        sysroot: Path | None,
        ld_library_path: str,
    ) -> None: ...


class StampProvenance(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        *,
        git_tag: str | None,
        build_id: str | None,
        no_git: bool,
    ) -> None: ...


class WriteSnapshotOutput(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        output: Path | None,
        build_info: Path | None,
        sources: Path | None,
        build_config: Path | None,
        allow_build_query: bool,
        collect_mode: str,
        build_query: str | None = ...,
        build_compile_db: str | None = ...,
        build_targets: tuple[str, ...] = ...,
        extractor: str = ...,
        inputs_pack: Path | None = ...,
        depth: str | None = ...,
        include_dependencies: bool = ...,
        header_roots: tuple[Path, ...] = ...,
        clang_bin: str = ...,
        snapshot_compression: str = ...,
        public_headers: tuple[Path, ...] = ...,
        public_header_dirs: tuple[Path, ...] = ...,
    ) -> None: ...
