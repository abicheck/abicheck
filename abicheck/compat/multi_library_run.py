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

"""What a multi-library ``compat check`` does *around* the comparisons.

:mod:`abicheck.compat.multi_library` owns the two pure decisions -- how
libraries pair, and how per-library results merge. This module owns the run
that uses them: turning a descriptor into a pairing plan, refusing a run that
could pair nothing rather than comparing two unrelated libraries, recording
the unpaired entries as coverage, and reading the descriptor's own compile
flags.

Split out of ``compat/cli.py`` rather than added to it: that file carries a
``no_growth`` baseline in ``architecture/debt.yaml``, and the way to respect
one is to give new responsibility its own owner. None of this is Click
surface -- it takes descriptors and paths and returns plans and results -- so
the command body keeps only the call sites.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..errors import ScopeMismatchError
from ._helpers import _do_echo
from .descriptor import CompatDescriptor
from .multi_library import pair_libraries

if TYPE_CHECKING:
    from pathlib import Path

    from ..checker_types import DiffResult
    from ..model.snapshot import AbiSnapshot


def _descriptor_compile_options(desc: CompatDescriptor) -> str:
    """The descriptor's own ``<include_paths>``/``<defines>``/
    ``<gcc_options>`` as one compile-flag string.

    A ``<headers>`` directory very often only parses with the include roots
    and defines the descriptor itself declares -- MKL's does. Those elements
    were read by nothing before, so a descriptor that fully specified how to
    compile its own headers still failed to compile them, for a reason that
    named neither the descriptor nor the missing flag.

    Appended *after* any ``-gcc-options`` given on the command line, so an
    explicit CLI flag stays last-wins where the compiler treats it that way.
    """
    parts = [f"-I{p}" for p in desc.include_paths]
    parts += [d if d.startswith("-D") else f"-D{d}" for d in desc.defines]
    parts += list(desc.gcc_options)
    return " ".join(parts)


def _plan_library_pairs(
    old_d: CompatDescriptor | AbiSnapshot,
    new_d: CompatDescriptor | AbiSnapshot,
) -> tuple[list[tuple[Path, Path]], list[Path], list[Path]] | None:
    """Pair the two sides' ``<libs>`` entries, or ``None`` for no fan-out.

    ``None`` -- **not** an empty pair list -- is how "this is not a
    multi-library comparison" is reported: either side being an
    already-built snapshot (a JSON/Perl dump names no library list), or both
    naming one library or fewer. The single-library path then runs exactly
    as it did before, which keeps this change inert for every ordinary
    one-library descriptor.

    The distinction is load-bearing. Returning ``[]`` for both "no fan-out
    applies" and "a multi-library plan that paired nothing" let the caller's
    ``or [(None, None)]`` fallback turn the second case into "compare
    ``libs[0]`` against ``libs[0]``" -- two *unrelated* libraries, yielding a
    real verdict that the unpaired-library warnings appended afterwards
    could not undo (Codex review). A zero-pair multi-library plan is now
    returned as one, and the caller refuses to produce a verdict from it.
    """
    if not isinstance(old_d, CompatDescriptor) or not isinstance(
        new_d, CompatDescriptor
    ):
        return None
    if len(old_d.libs) <= 1 and len(new_d.libs) <= 1:
        return None
    return pair_libraries(old_d.libs, new_d.libs)


def _no_library_pair_error(
    unpaired_old: list[Path], unpaired_new: list[Path]
) -> ScopeMismatchError:
    """The error for a multi-library comparison in which nothing paired.

    ADR-065: "a run that completed zero comparisons never reads as a clean
    pass". With no pair there is no comparison to draw a verdict from, and
    the alternative -- comparing the first library of each side regardless
    of whether they are the same library -- produces a confident verdict
    about two unrelated artifacts.

    ``ScopeMismatchError`` so it classifies as compat exit 9 (not_comparable,
    ADR-050 D2), which is what actually happened: the two descriptors name
    library sets that cannot be put into correspondence.
    """
    return ScopeMismatchError(
        "No library in the old descriptor pairs with one in the new "
        "descriptor, so no comparison could be run. Old: "
        + (", ".join(p.name for p in unpaired_old) or "(none)")
        + "; new: "
        + (", ".join(p.name for p in unpaired_new) or "(none)")
        + ". Libraries are paired by filename, then by version-insensitive "
        "stem; rename or list matching libraries, or compare one pair "
        "directly."
    )


def _record_unpaired_libraries(
    result: DiffResult,
    unpaired_old: list[Path],
    unpaired_new: list[Path],
    quiet: bool,
) -> DiffResult:
    """Record libraries present on only one side as coverage warnings.

    Deliberately **not** findings. A descriptor's ``<libs>`` list is a
    selection, and an entry with no counterpart on the other side is "not
    supplied there", which is not the same fact as "removed from the
    release" (ADR-065: *absent is not removed* -- proving a removal needs
    inventory evidence a descriptor does not carry). Reporting them as
    removals would manufacture exactly the false break this whole change set
    exists to stop; dropping them silently would hide that the run covered
    less than the descriptor named. A coverage warning is the disposition
    that says both.
    """
    if not unpaired_old and not unpaired_new:
        return result
    warnings = [
        *(
            f"Library {p.name} is named by the old descriptor with no "
            f"counterpart in the new one; it was not compared (not evidence "
            f"of removal -- a descriptor names a selection, not an inventory)."
            for p in unpaired_old
        ),
        *(
            f"Library {p.name} is named by the new descriptor with no "
            f"counterpart in the old one; it was not compared."
            for p in unpaired_new
        ),
    ]
    for w in warnings:
        _do_echo(f"Warning: {w}", quiet)
    return dataclasses.replace(
        result, coverage_warnings=[*result.coverage_warnings, *warnings]
    )
