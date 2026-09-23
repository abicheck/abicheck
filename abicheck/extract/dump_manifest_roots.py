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

"""Which paths a ``--dump-manifest`` document declares as the project's own.

Moved out of ``dumper_scoping``: these read a
manifest and nothing else, while every consumer -- dependency scoping, the
parse-time exclusion scope, L4 replay's public set -- needs the same answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["dump_manifest_header_roots", "dump_manifest_public_roots"]


def dump_manifest_header_roots(dump_manifest: Any) -> tuple[Path, ...]:
    """Every path a ``--dump-manifest`` document declares as project-owned,
    for forwarding into :func:`scope_snapshot_excluding_dependencies`'s
    ``header_roots`` -- not just ``roots`` (Codex review).
    ``public_header_paths``/``public_header_dirs`` (the manifest's own
    ADR-015 provenance-input equivalent of the public-header set) and any
    per-translation-unit include directory
    explicitly marked ``project_owned: true`` are just as much "the dump's
    actual root set" as ``roots`` -- a declaration under one of them must
    not be misclassified as a dependency just because those paths happen to
    sit under a system prefix, the same reasoning ``roots`` itself already
    gets. Shared by both ``dump`` (``cli_dump_helpers.py``) and
    ``compare``'s implicit live-binary dumping (via
    ``dumper_scoping.apply_dependency_scope_to_run_dump_result``) so a manifest's roots are
    never dropped just because the dumping path used ``--dump-manifest``
    instead of ``-H`` (Codex review).
    """
    if dump_manifest is None:
        return ()
    roots = [
        *dump_manifest.roots,
        *dump_manifest.public_header_paths,
        *dump_manifest.public_header_dirs,
    ]
    for tu in dump_manifest.translation_units:
        # Codex review: forced_includes is "what this TU actually compiles"
        # (dump_manifest.py's own docstring: "a TU may force-include a
        # private support header alongside a public one") -- not required to
        # already be in roots/project_owned includes, so a private support
        # header force-included from a system-prefixed install path was
        # otherwise misclassified as a toolchain dependency and filtered out.
        roots.extend(tu.forced_includes)
        roots.extend(inc.path for inc in tu.includes if inc.project_owned)
    return tuple(roots)


def dump_manifest_public_roots(dump_manifest: Any) -> tuple[Path, ...]:
    """The manifest's *declared-public* roots only (``roots``/
    ``public_header_paths``/``public_header_dirs``) -- unlike
    :func:`dump_manifest_header_roots`, deliberately excludes each TU's
    ``project_owned`` include directories (Codex review). Those are
    sibling/private support roots used only to keep
    ``resolve_dependency_scope`` from misclassifying a project-owned
    directory as a toolchain dependency; they are not declared public API
    surface. Forwarding them into L4 source replay's own public-header set
    (as :func:`dump_manifest_header_roots` is for) would make the source
    extractors treat every declaration under a private support directory as
    API-relevant, false-flagging private-header churn as a source break."""
    if dump_manifest is None:
        return ()
    return tuple(
        (
            *dump_manifest.roots,
            *dump_manifest.public_header_paths,
            *dump_manifest.public_header_dirs,
        )
    )
