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

"""Shared helper: split a real ``AbiSnapshot.ast_compile_args`` sequence into
its three semantically distinct pieces for equivalence comparison.

Non-test-prefixed by convention (mirrors ``_strict_process.py``/
``_workflow_exec.py`` -- see ``tests/CLAUDE.md``'s "Helpers" section), so it
can be imported both by the real-compiler integration parity test
(``test_dump_cli_typed_api_parity.py``) and by a fast, compiler-free unit
test that pins its normalization rules directly
(``test_dump_compile_args_normalize.py``).

Why a plain ``set(args) == set(args)`` comparison is wrong for this data
(Codex review, two rounds): a real compiler's flag semantics are not
uniformly order-insensitive.

* **Macro-define/-undef flags** (``-D``/``-U``) and the dialect flag
  (``-std=``) are *last-wins per name* -- ``-DFOO=1 -DFOO=2`` and
  ``-DFOO=2 -DFOO=1`` are sets of identical tokens but different effective
  compiler states (the compiler sees ``FOO`` as ``2`` in the first case,
  ``1`` in the second). A plain set comparison cannot distinguish these,
  so it can pass two paths that would parse a real header under two
  different macro/dialect states.
* **Include-search flags** (``-I``/``-isystem``/``-iquote``/``-idirafter``)
  are order- *and* multiplicity-sensitive for a different reason: a real
  compiler resolves a colliding header basename from the *first* matching
  directory in argv order within each of these search buckets, so
  reordering (or dropping-then-reintroducing) one relative to another can
  change which file is actually parsed even though the same set of
  directories is present overall.
* Every other flag (``-fPIC``, and similar boolean-style, non-value-taking
  flags) genuinely has no meaningful order or repeat-count -- comparing
  those as a plain set is the correct, not merely convenient, choice.
"""

from __future__ import annotations

#: Flags that take a following, space-separated operand naming a search
#: directory. Kept in this one place so both consumers agree on the
#: vocabulary.
INCLUDE_SEARCH_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter")


def split_compile_args(
    args: tuple[str, ...],
) -> tuple[dict[str, str], frozenset[str], tuple[tuple[str, str], ...]]:
    """Split *args* into ``(last_wins_state, presence_only_flags, include_pairs)``.

    ``last_wins_state`` maps a normalized key (``"macro:<NAME>"`` for a
    ``-D``/``-U`` flag naming ``<NAME>``, or the literal ``"dialect"`` for
    ``-std=``) to the *last* raw token seen for that key, walking *args* in
    order -- exactly mirroring a real compiler's last-flag-wins semantics
    for these two flag families. ``presence_only_flags`` is every other
    non-include-search token, compared as a plain set (order/repetition is
    genuinely inert for these). ``include_pairs`` is the ordered,
    duplicate-preserving sequence of ``(flag, directory)`` pairs for every
    include-search flag -- see the module docstring for why this one alone
    keeps both order and multiplicity.
    """
    last_wins: dict[str, str] = {}
    presence_only: list[str] = []
    include_pairs: list[tuple[str, str]] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok in INCLUDE_SEARCH_FLAGS and i + 1 < n:
            include_pairs.append((tok, args[i + 1]))
            i += 2
            continue
        if tok.startswith(("-D", "-U")) and len(tok) > 2:
            macro_name = tok[2:].split("=", 1)[0]
            last_wins[f"macro:{macro_name}"] = tok
        elif tok.startswith("-std="):
            last_wins["dialect"] = tok
        else:
            presence_only.append(tok)
        i += 1
    return last_wins, frozenset(presence_only), tuple(include_pairs)
