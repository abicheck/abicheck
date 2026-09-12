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

"""``Change.demangled_symbol`` resolution for an ``ELF_ONLY``-visibility
declaration, and the batch-demangling prewarm that keeps it cheap.

Moved out of the no-growth ``diff_symbols.py`` legacy monolith (Codex
review, fresh evidence -- the identical migration-rule bypass
``compare/rename_evidence.py``'s own docstring documents for
``diff_symbols_renames.py``): this is genuinely new comparison-adjacent
behavior (deciding what evidence backs a raw removal's display name), so
per ADR-061 D1 / ``compare/AGENTS.md`` it belongs here, not in a
``no_growth``-tracked legacy module whose baseline gets raised to make
room for it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from ..demangle import demangle, demangle_batch
from ..model import Function, Variable, Visibility
from ..model.surface_facts import declaration_confirmed_absent


def elf_only_demangled_name(mangled: str, visibility: Visibility) -> str | None:
    """:data:`Change.demangled_symbol` for an export-table-only declaration.

    ``itanium_export_function``/``itanium_export_variable``
    (``extract/export_symbol_identity.py``) set ``Function.name``/
    ``Variable.name`` to the raw mangled spelling for a ``Visibility.
    ELF_ONLY`` entity -- there is no header AST to source a pretty name
    from, unlike every other visibility. So ``description``/``old_value``
    on a finding about one of these embeds the raw mangled name, not a
    human-readable one (Codex review, item 8). Returns ``None`` for every
    other visibility (already demangled at parse time) and whenever
    :func:`demangle.demangle` itself returns ``None``/the unchanged input
    (not a valid mangled name, or no demangler available).
    """
    if visibility != Visibility.ELF_ONLY:
        return None
    demangled = demangle(mangled)
    return demangled if demangled and demangled != mangled else None


def prewarm_elf_only_demangling(
    old_map: dict[str, Function] | dict[str, Variable],
    new_keys: Mapping[str, object] | Collection[str],
) -> None:
    """Batch-demangle the ``ELF_ONLY``-visibility OLD-side mangled names that
    can actually end up ``FUNC_REMOVED_ELF_ONLY``/``VAR_REMOVED`` in one
    ``c++filt``/``cxxfilt`` call, before the per-symbol removal path in
    ``diff_symbols.py`` runs :func:`elf_only_demangled_name` one entity at a
    time.

    Without this, a library with many distinct ELF-only C++ removals forked
    one ``c++filt`` subprocess *per removed symbol* whenever ``cxxfilt`` isn't
    installed -- seconds to minutes of pure process-launch overhead, entirely
    wasted whenever the selected report format never reads
    ``demangled_symbol`` at all (Codex review, fresh evidence). ``demangle()``
    already checks :func:`demangle.demangle_batch`'s own module-level cache
    before doing any work of its own (see its docstring's "field-eval P11"
    note; :func:`diff_symbols_renames`'s rename gate relies on the identical
    warm-then-reuse pattern), so this call turns every later
    ``demangle(mangled)`` in this pass into a cache hit rather than a fresh
    subprocess, without changing what any single lookup returns.

    *new_keys* is the NEW side's own mangled-key index (``SymbolIdentityIndex``
    or a plain ``dict``) -- an OLD-side mangled key present there can never
    reach ``_check_removed_function``/``_var_removed``
    (``_match_old_function``'s own exact-key check returns a
    signature-diff/no-change result first; :func:`diff_by_key`'s ``on_removed``
    callback is likewise only invoked for a key absent from the new index), so
    excluding those names keeps an *unchanged* large ELF-only C++ library from
    demangling its entire export table on every comparison for a field that
    ends up unused everywhere (Codex review, fresh evidence -- the first cut
    of this prewarm batched every OLD-side ELF-only name regardless of
    whether the new side still exported it). A name that survives this filter
    but is later matched anyway (e.g. the extern-C alias fallback) is merely
    warmed and unused, not incorrect -- this is a cheap superset, not an exact
    "will be removed" prediction.
    """
    demangle_batch(
        [
            m
            for m, decl in old_map.items()
            if declaration_confirmed_absent(decl) and m not in new_keys
        ]
    )
