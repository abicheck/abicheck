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

"""Header-input expansion — directory-aware expansion of ``-H`` style inputs.

A leaf module (must not import :mod:`abicheck.service`). Holds the two
header-expansion primitives the ``dump``/``compare`` paths share;
:func:`expand_header_inputs` is re-exported by ``service`` for backward
compatibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
from ..errors import ValidationError
from ..header_utils import HEADER_SUFFIXES, iter_directory_headers

# Header file extensions recognised during directory expansion. Shared with the
# AST-cache include walk (dumper._cache_key) via the leaf header_utils module so
# expansion and cache-invalidation can never drift (Codex review).
_HEADER_EXTS = HEADER_SUFFIXES

# Directory segments never scanned for headers — VCS metadata plus abicheck's own
# in-tree cmake build dir; see build_query.PRUNED_HEADER_DIR_SEGMENTS (the shared
# single source of truth, also used by cli_resolve._expand_header_inputs).
_PRUNED_DIR_SEGMENTS = PRUNED_HEADER_DIR_SEGMENTS


def expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand header inputs where each item can be a file or a directory.

    Directories are scanned recursively for known header extensions.

    Raises:
        ValidationError: If a path does not exist or a header directory is empty.
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise ValidationError(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = iter_directory_headers(p, _PRUNED_DIR_SEGMENTS)
            if not found:
                raise ValidationError(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(found)
            continue
        raise ValidationError(f"Header path is neither file nor directory: {p}")

    # Deduplicate while preserving deterministic order
    seen: set[str] = set()
    deduped: list[Path] = []
    for h in out:
        k = str(h.resolve())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(h)
    return deduped


def expand_public_header_inputs(headers: Iterable[Path]) -> list[str]:
    """Best-effort :func:`expand_header_inputs`, as ``str`` paths.

    The public-header-root variant: a ``-H``/``--public-header-dir`` entry may
    name a directory, and two consumers need the individual header *files* out
    of it rather than the directory as one entry -- the S2 leak pass (so clang
    preprocesses each header instead of a directory as one bogus TU) and L4
    replay's install-tree-vs-build-tree mirror detection
    (``clang_public_roots._equivalent_public_roots_for_unit``, whose promotion
    rule needs two sampled matches for a directory root but only one for a file
    root, so an un-expanded directory silently loses a real mirror).

    Unlike :func:`expand_header_inputs` this never raises: an empty or missing
    directory degrades to the raw paths, because both consumers are advisory
    enrichment on top of a snapshot that has already been built.

    Lives here, in the engine layer, so
    ``service_input_resolution.embed_side_build_source`` can reach it without
    an engine-imports-CLI edge.
    """
    hdrs = list(headers)
    try:
        return [str(p) for p in expand_header_inputs(hdrs)]
    except Exception:  # noqa: BLE001 - expansion is best-effort for these tiers
        return [str(h) for h in hdrs]
