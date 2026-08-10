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
    is unprovable from two snapshots. It is "the consumer already emitted its
    own copy", and it needs evidence on **both** sides:

    * ``elf_binding is WEAK`` on the old side. Tri-state: ``None`` means the
      binding was not captured (non-ELF platform, header-only declaration,
      pre-v21 snapshot) and must never be read as WEAK, so this stays inert
      wherever the evidence is absent rather than demoting a real removal on
      a guess.
    * ``is_inline`` on the **old** side. This is the load-bearing half and a
      first revision omitted it, which made the predicate wrong (Codex
      review): WEAK does not imply vague linkage. An ordinary out-of-line
      function marked ``__attribute__((weak))`` is also WEAK, and a consumer
      compiled against *that* declaration carries no definition of its own —
      only an undefined dynamic reference. If the new headers then make it
      inline and drop the export, that already-compiled consumer fails to
      load: a real ABI break the missing check demoted to a risk. The
      consumer was built against the **old** headers, so only the old side
      can establish that it emitted its own copy.
    * ``is_inline`` on the new side too, which protects the other population:
      a consumer compiled *against the new headers* needs a definition there
      to emit. An entity demoted from inline to an out-of-line definition
      elsewhere is a genuine removal.

    Requiring old-side inline narrows the demotion — a template instantiation
    or implicit special member whose producer did not set ``is_inline`` falls
    back to being reported as a removal. That is the safe direction, and the
    same absence-of-evidence discipline the binding check above uses.

    The finding is demoted to a risk, not dropped, because the argument still
    has an edge: a consumer comparing the entity's address across the library
    boundary expecting a single shared instance can be affected even when
    both sides are inline. The event is real either way — this decides its
    severity, not its existence.
    """
    if f_old.elf_binding is not ElfBinding.WEAK:
        return False
    if not f_old.is_inline:
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
