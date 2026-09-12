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

"""One CLI input per evidence *role*, split back into the per-transport,
per-side destinations the command bodies already consume.

One-comparison-product plan Phase 7n. ``--debug-root`` merged into
``--debug-info``, ``--devel-pkg`` into ``-H/--header``, and
``--probe-matrix`` into ``--build-info`` -- so each role has one flag, while
every transport keeps the destination, side ownership, schema and identity
validation it had as a flag of its own. This module is the translation, and
it is *only* the translation: nothing downstream of
``cli_options.normalize_sided_options`` learns that the flags merged.

Which transport each value is comes from `abicheck.workflows.
evidence_transport`, from the operand's content -- never its name (bug class
``cli_surface.name_independent_dispatch_undone_downstream``). Kept out of
``cli_options`` because that module is a ``frontends`` file under a
no-growth debt baseline and this is a new responsibility, not more of its
existing one.

Deliberately **not** extended to ``--sources``: a checkout and the context
it was built under are independent inputs routinely supplied together, and
folding them would trade a flag for a type vocabulary the user must learn
(plan Phase 7n's own stated boundary).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ....workflows.evidence_transport import (
    BuildInfoTransport,
    DebugTransport,
    HeaderTransport,
    classify_build_info_transport,
    classify_debug_transport,
    classify_header_transport,
)

#: A ``(side, path)`` pair as a sided Click parameter type yields it, where
#: *side* is one of ``"old"``/``"new"``/``"both"`` (ADR-040 Lever 1).
SidedPaths = Sequence[tuple[str, Path]]


def _partition(
    pairs: SidedPaths, predicate: Callable[[Path], bool]
) -> tuple[list[tuple[str, Path]], list[tuple[str, Path]]]:
    """*pairs* split into (matching, rest), each value classified exactly once.

    One pass, and positional rather than value-keyed: two identical
    ``old=``-scoped values are two values, and a membership test would drop
    both from the complement.
    """
    matching: list[tuple[str, Path]] = []
    rest: list[tuple[str, Path]] = []
    for side, path in pairs:
        (matching if predicate(path) else rest).append((side, path))
    return matching, rest


def _three_buckets(
    pairs: SidedPaths,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    """``split_sided_paths``'s repeatable both/old-only/new-only buckets.

    Restated here rather than imported from ``cli_options`` (which imports
    *this* module) and applied to an already-filtered subset of one role's
    values, so a bare value still means "both sides" exactly as before.
    """
    both: list[Path] = []
    old: list[Path] = []
    new: list[Path] = []
    for side, path in pairs:
        if side == "old":
            old.append(path)
        elif side == "new":
            new.append(path)
        else:
            both.append(path)
    return tuple(both), tuple(old), tuple(new)


def _single_per_side(pairs: SidedPaths) -> tuple[Path | None, Path | None]:
    """``_split_sided_single``'s semantics: bare/``both=`` sets both sides,
    ``old=``/``new=`` sets one, last value wins per side."""
    old: Path | None = None
    new: Path | None = None
    for side, path in pairs:
        if side in ("both", "old"):
            old = path
        if side in ("both", "new"):
            new = path
    return old, new


def split_debug_evidence(pairs: SidedPaths) -> dict[str, object]:
    """``--debug-info``'s three transports, each to its own destination.

    A package goes to ``debug_info1``/``debug_info2`` (the release
    extraction path, one per side, last wins -- unchanged from
    ``--debug-info``); a directory *or* a detached debug file goes to
    ``debug_roots``/``debug_roots_old``/``debug_roots_new`` (the ADR-021a
    resolver chain, repeatable -- unchanged from ``--debug-root``, which the
    chain now also accepts a file in, see
    `abicheck.extract.detached_debug`).
    """
    packages, searchable = _partition(
        pairs, lambda path: classify_debug_transport(path) is DebugTransport.PACKAGE
    )
    both, old, new = _three_buckets(searchable)
    old_pkg, new_pkg = _single_per_side(packages)
    return {
        "debug_roots": both,
        "debug_roots_old": old,
        "debug_roots_new": new,
        "debug_info1": old_pkg,
        "debug_info2": new_pkg,
    }


def split_header_evidence(pairs: SidedPaths) -> dict[str, object]:
    """``-H/--header``'s two transports, each to its own destination.

    A development package goes to ``devel_pkg1``/``devel_pkg2`` (one per
    side, last wins -- unchanged from ``--devel-pkg``); everything else goes
    to ``headers``/``old_headers_only``/``new_headers_only``, whose
    file-vs-directory provenance asymmetry (``provenance.apply_provenance``)
    is untouched.
    """
    packages, plain = _partition(
        pairs, lambda path: classify_header_transport(path) is HeaderTransport.PACKAGE
    )
    both, old, new = _three_buckets(plain)
    old_pkg, new_pkg = _single_per_side(packages)
    return {
        "headers": both,
        "old_headers_only": old,
        "new_headers_only": new,
        "devel_pkg1": old_pkg,
        "devel_pkg2": new_pkg,
    }


def split_build_evidence(pairs: SidedPaths) -> dict[str, object]:
    """``--build-info``'s two evidence kinds, each to its own destination.

    A probe-matrix snapshot goes to ``probe_matrix_old``/
    ``probe_matrix_new`` (unchanged from ``--probe-matrix``, including its
    both-sides-or-neither rule, which ``runtime._load_probe_matrix_changes``
    still owns); a build directory / compile database / capture pack goes to
    ``old_build_info``/``new_build_info``. Both may be given for one side --
    the two roles stay distinct internally, and the report tells them apart
    exactly as it did when they were two flags.
    """
    matrices, contexts = _partition(
        pairs,
        lambda path: classify_build_info_transport(path)
        is BuildInfoTransport.PROBE_MATRIX,
    )
    old_ctx, new_ctx = _single_per_side(contexts)
    old_matrix, new_matrix = _single_per_side(matrices)
    return {
        "old_build_info": old_ctx,
        "new_build_info": new_ctx,
        "probe_matrix_old": old_matrix,
        "probe_matrix_new": new_matrix,
    }


def unsided_debug_packages(paths: Sequence[Path]) -> tuple[Path, ...]:
    """Those *paths* that are package archives -- ``dump``'s rejection set.

    ``dump``'s operand is one binary, with no release fan-out and so no
    package-extraction stage to hand a debug package to -- which is why a
    package is a usage error there rather than a fourth silent no-op. Its
    directories and detached files both go straight to the resolver chain,
    as ``--debug-root``'s values always did.
    """
    return tuple(
        p for p in paths if classify_debug_transport(p) is DebugTransport.PACKAGE
    )


__all__ = [
    "split_build_evidence",
    "split_debug_evidence",
    "split_header_evidence",
    "unsided_debug_packages",
]
