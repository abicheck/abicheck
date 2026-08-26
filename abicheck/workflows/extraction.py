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

"""Extraction operations a frontend asks the engine to perform on an input.

ADR-061 Phase 4 item 2 -- "make workflows the sole operation owners". A
frontend may not import the ``extract`` ring directly, and that rule is doing
real work here rather than shuffling names: every function below *does*
something to an input (expands a header directory, seeds an include search,
folds L3 build evidence into an L2 compile context, embeds a build-source
pack, resolves a source extractor's clang binary). Those are operations, and
an operation the CLI performs by reaching past the engine is an operation the
typed API has no way to perform identically.

Re-export only, deliberately: the point is that there is one owner per
operation and the frontend reaches it through the workflow layer, not that a
new implementation appears here. Each name's own module remains the one to
read and to change.

One consequence worth stating, because it is not obvious and it bit the test
suite: ``from ..x import y`` **binds** ``y`` here at import time. Patching
``abicheck.x.y`` afterwards does not change what a caller reaching it through
this module sees. That is ordinary Python import semantics rather than anything
this module invents, but the indirection makes it easy to miss -- so a test
that needs to substitute one of these must patch it *here*, where the call
actually resolves.
"""

from __future__ import annotations

from .._compiler_options import has_explicit_std
from ..binary_utils import (
    _canonical_library_key,
    detect_binary_format,
    normalize_binary_input,
    resolve_linker_script,
    strip_vendor_hash,
)
from ..buildsource.embed import embed_build_source
from ..buildsource.inline import (
    BuildConfig,
    _autodiscover_compile_db,
    _compile_db_at,
    build_inline_coverage,
    discover_build_config,
    is_pack_dir,
    load_build_config,
    sniff_build_info_format,
)
from ..buildsource.inputs_pack import (
    _load_build_evidence,
    ingest_inputs_pack,
    is_inputs_pack,
    is_inputs_pack_dir,
    load_inputs_manifest,
)
from ..buildsource.l2_seed import seed_includes_and_fold_compile_context
from ..debug_resolver import DebugArtifact, resolve_debug_info
from ..dump_manifest import DumpManifest, load_manifest
from ..dumper_clang import resolve_source_frontend_clang_bin
from ..dumper_scoping import dump_manifest_header_roots, resolve_dependency_scope
from ..header_conditionals import attach_build_context_for_parsed_headers
from ..header_utils import (
    dedup_paths_preserve_order,
    deferred_token_dirs,
    include_operand_dirs,
    iter_directory_headers,
    resolve_inferred_header_roots,
    split_public_header_inputs,
)

__all__ = [
    "BuildConfig",
    "DebugArtifact",
    "DumpManifest",
    "_autodiscover_compile_db",
    "_canonical_library_key",
    "_compile_db_at",
    "_load_build_evidence",
    "attach_build_context_for_parsed_headers",
    "build_inline_coverage",
    "dedup_paths_preserve_order",
    "deferred_token_dirs",
    "detect_binary_format",
    "discover_build_config",
    "dump_manifest_header_roots",
    "embed_build_source",
    "has_explicit_std",
    "include_operand_dirs",
    "ingest_inputs_pack",
    "is_inputs_pack",
    "is_inputs_pack_dir",
    "is_pack_dir",
    "iter_directory_headers",
    "load_build_config",
    "load_inputs_manifest",
    "load_manifest",
    "normalize_binary_input",
    "resolve_debug_info",
    "resolve_dependency_scope",
    "resolve_inferred_header_roots",
    "resolve_linker_script",
    "resolve_source_frontend_clang_bin",
    "seed_includes_and_fold_compile_context",
    "sniff_build_info_format",
    "split_public_header_inputs",
    "strip_vendor_hash",
]
