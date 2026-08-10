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

"""Strong vs. vague (weak/COMDAT) linkage — what a dropped export means.

A leaf module deliberately, rather than a helper inside ``diff_symbols``:
two detectors in two files observe the *same* event (an export present on the
old side and absent on the new) and must agree about it. ``diff_symbols``'s
``_check_removed_function`` reports it as a removal, and
``diff_platform``'s ELF-fallback detector reports it as a deletion. A rule
living in one of them and imported by the other is one refactor away from the
two disagreeing, which is exactly how the same symbol came to be reported
twice at BREAKING here in the first place.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .model import ElfBinding, Function, Visibility


def vague_linkage_export_dropped(f_old: Function, f_new: Function | None) -> bool:
    """Is this dropped export a COMDAT copy the consumer already carries?

    A ``WEAK`` ELF binding on a C++ entity is what the compiler emits for
    something the language requires *every* using translation unit to define
    for itself — an inline function, a template instantiation, an implicit
    special member — placed in a COMDAT group so the linker keeps one copy.
    So when such an export disappears while the new headers still define the
    entity inline, a consumer built against those headers has its own
    definition and keeps resolving. That is a materially different event from
    a strong definition disappearing, which the export table alone cannot
    distinguish: both detectors above previously called it BREAKING.

    What the demotion rests on is deliberately *not* "nobody uses it" — that
    is unprovable from two snapshots. It is "the header still defines it, so
    a user emits its own copy", and the new side's own declaration is direct
    evidence for that. Both halves are required:

    * ``elf_binding is WEAK`` on the old side. Tri-state: ``None`` means the
      binding was not captured (non-ELF platform, header-only declaration,
      pre-v21 snapshot) and must never be read as WEAK, so this stays inert
      wherever the evidence is absent rather than demoting a real removal on
      a guess.
    * The new side still declares it *and* that declaration is inline. A
      declaration alone is not enough: an entity demoted from inline to an
      out-of-line definition elsewhere is a genuine removal.

    The finding is demoted to a risk, not dropped, because the argument has
    a real edge: a consumer built against a header that only *declared* the
    entity, or one comparing its address across the library boundary
    expecting a single shared instance, can still be affected. The event is
    real either way — this decides its severity, not its existence.
    """
    if f_old.elf_binding is not ElfBinding.WEAK:
        return False
    return f_new is not None and f_new.is_inline


def check_removed_function(
    mangled: str,
    f_old: Function,
    new_all: dict[str, Function],
    elf_only_mode: bool,
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
    if vague_linkage_export_dropped(f_old, f_hidden):
        return make_change(
            ChangeKind.FUNC_EXPORT_DROPPED_INLINE_AVAILABLE,
            symbol=mangled,
            name=f_old.name,
            description=(
                f"Weak (COMDAT) export dropped, still defined inline in headers: {f_old.name}"
            ),
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
