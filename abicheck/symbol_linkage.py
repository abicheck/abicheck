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

"""What a dropped export means, given proof of how it was emitted.

A leaf module deliberately, rather than a helper inside ``diff_symbols``: two
detectors in two files observe the *same* event — an export present on the old
side and absent on the new. ``diff_symbols`` reports it as a removal and
``diff_platform``'s ELF fallback as a deletion. A rule living in one and
imported by the other is one refactor from the two disagreeing, which is
exactly how the same symbol came to be reported twice at BREAKING.

**The evidence, and why it is this evidence.** An earlier attempt demoted a
dropped weak export using ``Function.is_inline`` and was reverted: that field
records the ``inline`` *specifier*, not that a definition exists. Verified
against real clang, ``inline int f();`` yields ``inline=True, has_body=False``,
so a header declaring a function inline while defining it elsewhere would have
had a genuine load-time break demoted to a risk. Nor does ``WEAK`` in
``.dynsym`` help: an out-of-line ``__attribute__((weak))`` function is ``WEAK``
too and its consumers hold no copy of their own.

What does establish the invariant is the compiler's own record: a
vague-linkage definition is emitted into a COMDAT group *precisely because*
the language requires every using translation unit to define it
(``buildsource/comdat_groups.py``). That is not an inference from a
specifier — it is the fact itself, and it is only available from L3 object
files, since the linker discards section groups.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .model import AbiSnapshot, Function, Visibility


def vague_linkage_symbols(snapshot: AbiSnapshot) -> frozenset[str] | None:
    """Symbols this side's build proved to be vague-linkage, or ``None``.

    ``None`` — no L3 build evidence, no scan, or a scan that read nothing —
    means *not established*, and every consumer must treat it as "no demotion
    is licensed" rather than as an empty set. That distinction is the whole
    reason ``ComdatScan.resolvable`` exists separately from its symbol set.
    """
    pack = getattr(snapshot, "build_source", None)
    evidence = getattr(pack, "build_evidence", None) if pack is not None else None
    scan = getattr(evidence, "comdat", None) if evidence is not None else None
    if scan is None or not scan.resolvable:
        return None
    return frozenset(scan.symbols)


def vague_linkage_export_dropped(
    f_old: Function,
    f_new: Function | None,
    vague_symbols: frozenset[str] | None,
) -> bool:
    """Is this dropped export a COMDAT copy the consumer already carries?

    Both halves are required, and each answers a different population:

    * **The old side proved vague linkage.** The consumer was compiled against
      the old headers, so only the old build can establish that it emitted its
      own copy — and it establishes it by having put the definition in a
      COMDAT group. ``vague_symbols is None`` means the build evidence never
      said, which is never a licence to demote.
    * **The new side still declares the entity.** An entity gone from the
      headers entirely is a real removal for anything compiled against them,
      whatever already-linked binaries do.

    Deliberately *not* consulted: ``is_inline`` on either side. It is the
    specifier, and reading it as a definition is what made the reverted
    attempt wrong.

    The finding is demoted to a risk rather than dropped, because the proof
    covers already-linked consumers and not every consequence: code compiled
    against the new headers needs a definition there to emit, which no header
    backend can confirm (castxml, through 0.7.0, renders a declaration and a
    definition identically), and an entity whose address is compared across
    the library boundary can still be affected. The event is real either way —
    this decides its severity, not its existence.
    """
    if vague_symbols is None:
        return False
    if f_old.mangled not in vague_symbols:
        return False
    return f_new is not None


def check_removed_function(
    mangled: str,
    f_old: Function,
    new_all: dict[str, Function],
    elf_only_mode: bool,
    vague_symbols: frozenset[str] | None = None,
) -> Change:
    """Create a Change for a function that was removed or hidden."""
    f_hidden = new_all.get(mangled)
    if (
        f_hidden is not None
        and f_hidden.visibility == Visibility.HIDDEN
        and not (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
    ):
        return make_change(
            ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old_value=f_old.visibility.value,
            new_value=f_hidden.visibility.value,
        )
    if vague_linkage_export_dropped(f_old, f_hidden, vague_symbols):
        return make_change(
            ChangeKind.FUNC_VAGUE_EXPORT_DROPPED,
            symbol=mangled,
            name=f_old.name,
            old_value=f_old.name,
        )
    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
        else ChangeKind.FUNC_REMOVED
    )
    return make_change(
        removed_kind,
        symbol=mangled,
        description=f"{f_old.visibility.value.capitalize()} function removed: {f_old.name}",
        old_value=f_old.name,
    )
