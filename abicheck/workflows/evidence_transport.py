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

"""Which *transport* an evidence operand is, decided from its content.

One-comparison-product plan Phase 7n gives each evidence **role** one CLI
input, so a transport difference stops being a flag difference:

===================  =========================================================
Role                 Transports the one input accepts
===================  =========================================================
``--debug-info``     a debug package · a directory of debug files · a
                     detached debug file (was ``--debug-info`` +
                     ``--debug-root``)
``-H/--header``      a header file or directory · a development package
                     (was ``-H`` + ``--devel-pkg``)
``--build-info``     a build directory / compile database / capture pack ·
                     a probe-matrix snapshot (was ``--build-info`` +
                     ``--probe-matrix``); both may be supplied for one side
===================  =========================================================

Every answer here comes from the operand's own content -- a package
extractor's own format contract, a debug file's ELF sections or PDB magic, a
probe matrix's own document discriminator. **Never** from a filename or
suffix: that is bug class
``cli_surface.name_independent_dispatch_undone_downstream``
(`tests/regressions/manifest_tool_surface.py`), whose corollary is that the
classifier being name-independent proves nothing about the pipeline behind
it -- which is why the transports this module names are exactly the ones
`abicheck.package`'s extractors and
`abicheck.extract.detached_debug` resolve content-first too, and why the
role tests drive the whole public invocation under non-conventional names.

A ``workflows`` module because it reads ``extract``-layer contracts
(`abicheck.package`, `abicheck.extract.detached_debug`) that ADR-061 does
not let a ``frontends`` module import; the frontend half (splitting each
role's sided values into the per-side destinations the command bodies
consume) is `abicheck.frontends.cli.options.evidence_roles`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

#: The ``schema`` discriminator a probe-matrix snapshot carries from Phase 7n
#: on. Older, tagless snapshots stay readable -- see
#: :func:`is_probe_matrix_document`.
PROBE_MATRIX_SCHEMA = "abicheck.probe-matrix/v1"

#: The keys every probe-matrix snapshot has always carried
#: (``workflows.findings.matrix_snapshot_from_dict`` reads each without a
#: default, so a document missing one was never loadable as a matrix).
_PROBE_MATRIX_REQUIRED_KEYS = frozenset({"library", "version", "spec_name"})

#: Bounded read for the document sniff below: a probe matrix embeds a whole
#: ``AbiSnapshot`` per probe result and can be tens of megabytes, and this is
#: a *classification* step, not the load.
_DOCUMENT_SNIFF_BYTES = 64 * 1024


class DebugTransport(Enum):
    """How a ``--debug-info`` operand carries its debug evidence."""

    #: An archive a package extractor unpacks (RPM/Deb/tar/conda/wheel).
    PACKAGE = "package"
    #: A directory to search: a build-id tree, a path mirror, dSYM bundles.
    ROOT = "root"
    #: The artifact itself: a separate debug ELF, a ``.dwp``, or a PDB.
    DETACHED_FILE = "detached_file"


class HeaderTransport(Enum):
    """How a ``-H/--header`` operand carries its header evidence."""

    #: A header file, or a directory of them.
    HEADERS = "headers"
    #: A development package whose extracted tree supplies the headers.
    PACKAGE = "package"


class BuildInfoTransport(Enum):
    """Which evidence a ``--build-info`` operand carries."""

    #: A build directory, a compile database, or a pre-captured pack.
    COMPILE_CONTEXT = "compile_context"
    #: A probe-matrix snapshot (build-configuration observations).
    PROBE_MATRIX = "probe_matrix"


def is_evidence_package(path: Path) -> bool:
    """Whether *path*'s content is a package archive some extractor unpacks.

    Delegates to :func:`abicheck.package.is_package`, which asks the
    extractors themselves -- so this answers "the pipeline can actually
    unpack this", not "the name looks like a package", and the two cannot
    drift apart.
    """
    from ..package import is_package

    return is_package(path)


def _document_prefix(path: Path) -> str | None:
    """*path*'s leading text, or ``None`` when it is not a readable text file."""
    try:
        with open(path, "rb") as f:
            raw = f.read(_DOCUMENT_SNIFF_BYTES)
    except OSError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # A truncated multi-byte character at the sniff boundary is not a
        # decoding failure of the document -- only of this window.
        return raw.decode("utf-8", errors="ignore")


def is_probe_matrix_document(path: Path) -> bool:
    """Whether *path* is a probe-matrix snapshot, read from the document.

    Two accepted discriminators, in this order:

    1. an explicit ``"schema": "abicheck.probe-matrix/v1"`` member, which
       every snapshot written from Phase 7n on carries;
    2. the required-key contract ``matrix_snapshot_from_dict`` has always
       enforced (``library``/``version``/``spec_name`` on a top-level JSON
       object), which is what keeps a snapshot captured before that tag
       existed classifiable.

    Neither is a filename, and neither collides with ``--build-info``'s own
    operands: a build directory is a directory, a compile database is a
    top-level JSON *array*, and a capture pack is a directory holding a
    ``manifest.json``.

    Parsed from a bounded prefix rather than the whole file, so a matrix
    carrying a snapshot per probe result is not fully loaded twice. A
    document too large for the window falls back to the key sniff below,
    which does not need valid JSON to answer.
    """
    if path.is_dir():
        return False
    prefix = _document_prefix(path)
    if prefix is None or not prefix.lstrip().startswith("{"):
        return False
    try:
        data = json.loads(prefix)
    except ValueError:
        # Truncated by the sniff window (or genuinely malformed): fall back
        # to the top-level key names, which appear in the object's own head.
        # Deliberately requires *every* discriminator key, so a compile
        # database or any other JSON document mentioning one of them in
        # passing is not misread as a matrix.
        if f'"{PROBE_MATRIX_SCHEMA}"' in prefix:
            return True
        return all(f'"{key}"' in prefix for key in _PROBE_MATRIX_REQUIRED_KEYS)
    if not isinstance(data, dict):
        return False
    if data.get("schema") == PROBE_MATRIX_SCHEMA:
        return True
    return _PROBE_MATRIX_REQUIRED_KEYS.issubset(data.keys())


def classify_debug_transport(path: Path) -> DebugTransport:
    """Which transport a ``--debug-info`` operand uses.

    A directory is a debug root; a package archive is a package; any other
    existing file is the detached artifact itself. A path that does not
    exist is a root, which is what ``--debug-root`` always did with one --
    the resolver chain simply finds nothing there, and turning that into a
    hard error would reject an invocation that works today.
    """
    if path.is_dir():
        return DebugTransport.ROOT
    if not path.exists():
        return DebugTransport.ROOT
    if is_evidence_package(path):
        return DebugTransport.PACKAGE
    return DebugTransport.DETACHED_FILE


def classify_header_transport(path: Path) -> HeaderTransport:
    """Which transport a ``-H/--header`` operand uses.

    A development package is a carrier of header evidence; everything else
    -- a header file, a directory of headers, or a path that does not exist
    yet (``-H`` never required existence, since a header may be absent for a
    symbols-only fallback) -- is header evidence directly.
    """
    if path.is_dir() or not path.exists():
        return HeaderTransport.HEADERS
    if is_evidence_package(path):
        return HeaderTransport.PACKAGE
    return HeaderTransport.HEADERS


def classify_build_info_transport(path: Path) -> BuildInfoTransport:
    """Which evidence a ``--build-info`` operand carries.

    Probe observations and compile context remain two distinct kinds of
    evidence internally and may be supplied together for one side; only the
    *flag* merges.
    """
    if is_probe_matrix_document(path):
        return BuildInfoTransport.PROBE_MATRIX
    return BuildInfoTransport.COMPILE_CONTEXT


def all_probe_matrices(paths: Iterable[Path]) -> bool:
    """Whether *paths* is non-empty and every member is a probe matrix.

    The question a caller asks when only *one* of ``--build-info``'s two
    evidence kinds is admissible: the directory/package release fan-out
    collects no inline build/source evidence, but has always run its own
    release-global build-configuration comparison, so a matrix must stay
    accepted there while a compile context stays rejected
    (`cli_resolve._reject_evidence_flags_for_set_inputs`). Lives here, with
    the classification it is made of, so that guard and the splitter cannot
    form two opinions about what a given operand is.
    """
    paths = list(paths)
    return bool(paths) and all(
        classify_build_info_transport(p) is BuildInfoTransport.PROBE_MATRIX
        for p in paths
    )


__all__ = [
    "PROBE_MATRIX_SCHEMA",
    "BuildInfoTransport",
    "all_probe_matrices",
    "DebugTransport",
    "HeaderTransport",
    "classify_build_info_transport",
    "classify_debug_transport",
    "classify_header_transport",
    "is_evidence_package",
    "is_probe_matrix_document",
]
