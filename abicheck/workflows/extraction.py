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
    resolve_linker_script_chain,
    strip_vendor_hash,
)
from ..buildsource.build_config_io import load_build_config_with_digest
from ..buildsource.build_query import (
    PRUNED_HEADER_DIR_SEGMENTS,
    drain_build_dir_cleanups,
)
from ..buildsource.compiler_record import extract_compiler_record
from ..buildsource.embed import embed_build_source
from ..buildsource.extractor import CollectionAction, CollectionContext, CollectionMode
from ..buildsource.extractor_manifest import (
    ManifestError,
    load_extractor_manifest,
    run_external_extractor,
)
from ..buildsource.graph_backends import (
    ingest_codeql_call_results,
    ingest_codeql_extends_results,
    ingest_kythe_entries,
)
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
from ..buildsource.inline_graph_fold import (
    fold_archive_graph,
    fold_call_graph,
    fold_callback_graph,
    fold_include_graph,
    fold_macro_graph,
    fold_override_graph,
    fold_template_graph,
    fold_type_graph,
    fold_virtual_dispatch_graph,
)
from ..buildsource.inputs_pack import (
    _load_build_evidence,
    ingest_inputs_pack,
    is_inputs_pack,
    is_inputs_pack_dir,
    load_inputs_manifest,
)
from ..buildsource.inputs_validate import validate_inputs_pack
from ..buildsource.l2_seed import seed_includes_and_fold_compile_context
from ..buildsource.pack_load import load_inputs_pack_or_raise, load_pack_or_raise
from ..buildsource.pattern_scan import scan_files
from ..buildsource.poi import build_points_of_interest, resolve_symbol_tus
from ..buildsource.preprocessor_scan import run_preprocessor_scan
from ..buildsource.redaction import DEFAULT_REDACTION
from ..buildsource.snapshot_exports import exported_symbols_from_snapshot
from ..buildsource.source_link import relink_surface_exports
from ..buildsource.source_replay import collection_for_ci_mode
from ..buildsource.toolchain_bindings import (
    BindingsFile,
    BindingsFileError,
    check_profile_bindings_resolve,
    load_bindings_file,
)
from ..buildsource.toolchain_probe import check_profile_toolchain_identity
from ..debug_resolver import DebugArtifact, resolve_debug_info
from ..dump_manifest import DumpManifest, load_manifest
from ..dumper_clang import resolve_source_frontend_clang_bin
from ..dumper_scoping import dump_manifest_header_roots, resolve_dependency_scope
from ..elf_metadata import parse_elf_metadata
from ..header_conditionals import attach_build_context_for_parsed_headers
from ..header_utils import (
    dedup_paths_preserve_order,
    deferred_token_dirs,
    include_operand_dirs,
    iter_directory_headers,
    resolve_inferred_header_roots,
    split_public_header_inputs,
)
from ..numpy_capi import extract_numpy_capi_surface
from ..python_api import detect_python_api
from ..python_ext import detect_python_extension
from ..symvers_metadata import looks_like_symvers

__all__ = [
    "BindingsFile",
    "BindingsFileError",
    "BuildConfig",
    "CollectionAction",
    "CollectionContext",
    "CollectionMode",
    "DEFAULT_REDACTION",
    "DebugArtifact",
    "DumpManifest",
    "ManifestError",
    "PRUNED_HEADER_DIR_SEGMENTS",
    "_autodiscover_compile_db",
    "_canonical_library_key",
    "_compile_db_at",
    "_load_build_evidence",
    "attach_build_context_for_parsed_headers",
    "build_inline_coverage",
    "build_points_of_interest",
    "check_profile_bindings_resolve",
    "check_profile_toolchain_identity",
    "collection_for_ci_mode",
    "dedup_paths_preserve_order",
    "deferred_token_dirs",
    "detect_binary_format",
    "detect_python_api",
    "detect_python_extension",
    "discover_build_config",
    "drain_build_dir_cleanups",
    "dump_manifest_header_roots",
    "embed_build_source",
    "exported_symbols_from_snapshot",
    "extract_compiler_record",
    "extract_numpy_capi_surface",
    "fold_archive_graph",
    "fold_call_graph",
    "fold_callback_graph",
    "fold_include_graph",
    "fold_macro_graph",
    "fold_override_graph",
    "fold_template_graph",
    "fold_type_graph",
    "fold_virtual_dispatch_graph",
    "has_explicit_std",
    "include_operand_dirs",
    "ingest_codeql_call_results",
    "ingest_codeql_extends_results",
    "ingest_inputs_pack",
    "ingest_kythe_entries",
    "is_inputs_pack",
    "is_inputs_pack_dir",
    "is_pack_dir",
    "iter_directory_headers",
    "load_bindings_file",
    "load_build_config",
    "load_build_config_with_digest",
    "load_extractor_manifest",
    "load_inputs_manifest",
    "load_inputs_pack_or_raise",
    "load_manifest",
    "load_pack_or_raise",
    "looks_like_symvers",
    "normalize_binary_input",
    "parse_elf_metadata",
    "relink_surface_exports",
    "resolve_debug_info",
    "resolve_dependency_scope",
    "resolve_inferred_header_roots",
    "resolve_linker_script",
    "resolve_linker_script_chain",
    "resolve_source_frontend_clang_bin",
    "resolve_symbol_tus",
    "run_external_extractor",
    "run_preprocessor_scan",
    "scan_files",
    "seed_includes_and_fold_compile_context",
    "sniff_build_info_format",
    "split_public_header_inputs",
    "strip_vendor_hash",
    "validate_inputs_pack",
]
