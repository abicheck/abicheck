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
its four semantically distinct pieces for equivalence comparison.

Non-test-prefixed by convention (mirrors ``_strict_process.py``/
``_workflow_exec.py`` -- see ``tests/CLAUDE.md``'s "Helpers" section), so it
can be imported both by the real-compiler integration parity test
(``test_dump_cli_typed_api_parity.py``) and by a fast, compiler-free unit
test that pins its normalization rules directly
(``test_dump_compile_args_normalize.py``).

Why a plain ``set(args) == set(args)`` comparison is wrong for this data
(Codex review, three rounds -- each one found a *different* flag family this
module's previous "everything else is a set" default silently mishandled,
which is the reason the default direction changed on the third round; see
below):

* **Include-search flags** (``-I``/``-isystem``/``-iquote``/``-idirafter``)
  are order- *and* multiplicity-sensitive: a real compiler resolves a
  colliding header basename from the *first* matching directory in argv
  order within each of these search buckets, so reordering (or
  dropping-then-reintroducing) one relative to another can change which
  file is actually parsed even though the same set of directories is
  present overall.
* **Macro-define/-undef flags** (``-D``/``-U``) and single-token,
  last-wins-style flags (the dialect flag ``-std=``, the target triple
  ``--target=``) are *last-wins* -- ``-DFOO=1 -DFOO=2`` and
  ``-DFOO=2 -DFOO=1`` are sets of identical tokens but different effective
  compiler states (the compiler sees ``FOO`` as ``2`` in the first case,
  ``1`` in the second). A plain set comparison cannot distinguish these.
* **Forced-include flags** (``-include``/``-imacros``) take a following
  operand and are order-sensitive the same way include-search flags are:
  each application can affect what a *later* one sees (an earlier
  ``-include``'s macros are visible to a later one), so two paths naming
  the same files in a different order are not equivalent.
* A small, genuinely order- and repeat-count-insensitive family of
  boolean-style flags (``-fPIC`` and the like) is the *only* case where a
  plain set comparison is correct, not merely convenient.

**The default direction matters more than any one flag's classification.**
The first two review rounds each found one more flag family this module's
"everything unrecognized is presence-only" default had silently
mis-classified -- first include-search flags, then ``-D``/``-std``. A third
round found the same shape again for ``--target=``/``-include``, which is
what changed the *default* itself rather than adding a fourth special case:
an unrecognized token is now compared as an exact, order- and
multiplicity-preserving sequence entry by default (``ordered_unknown``), and
only the small, explicitly-reviewed `ORDER_INSENSITIVE_BOOLEAN_FLAGS`
allowlist gets the more permissive set comparison. A token this module does
not recognize is far more likely to be order-sensitive (most compiler flags
that take an operand are) than not, so "unknown -> compared strictly" is the
safe default; "unknown -> compared loosely" is what let three consecutive
review rounds find a real, silent gap.
"""

from __future__ import annotations

#: Flags that take a following, space-separated operand naming a search
#: directory. Order- and multiplicity-sensitive (first-match-wins search).
INCLUDE_SEARCH_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter")

#: Flags that take a following, space-separated operand naming a file to
#: force-include. Order-sensitive for a different reason than the search
#: flags above (an earlier one's macros are visible to a later one), but
#: grouped as the identical "ordered (flag, operand) pair" shape.
FORCED_INCLUDE_FLAGS = ("-include", "-imacros")

#: Single-token flags with real "later occurrence wins" semantics, keyed by
#: their own literal prefix (there is only ever one effective value per
#: prefix, unlike -D/-U which are keyed per macro name below).
LAST_WINS_PREFIXES = ("-std=", "--target=")

#: The only flags whose repetition/order this module treats as genuinely
#: inert -- deliberately a narrow, explicitly-reviewed allowlist, not a
#: default (see the module docstring's "default direction" paragraph for
#: why a catch-all default was the actual bug across three review rounds).
ORDER_INSENSITIVE_BOOLEAN_FLAGS = frozenset(
    {"-fPIC", "-fpic", "-pthread", "-nostdinc", "-nostdinc++"}
)


def split_compile_args(
    args: tuple[str, ...],
) -> tuple[
    dict[str, str], frozenset[str], tuple[tuple[str, str], ...], tuple[str, ...]
]:
    """Split *args* into ``(last_wins_state, presence_only_flags,
    ordered_pairs, ordered_unknown)``.

    ``last_wins_state`` maps a normalized key -- ``"macro:<NAME>"`` for a
    ``-D``/``-U`` flag naming ``<NAME>``, or a flag's own literal prefix for
    one of `LAST_WINS_PREFIXES` -- to the *last* raw token seen for that
    key, walking *args* in order, mirroring a real compiler's last-wins
    semantics. ``presence_only_flags`` is every token in
    `ORDER_INSENSITIVE_BOOLEAN_FLAGS`, compared as a plain set.
    ``ordered_pairs`` is the ordered, duplicate-preserving sequence of
    ``(flag, operand)`` pairs for every include-search or forced-include
    flag. ``ordered_unknown`` is every remaining, unrecognized token, kept
    as an exact, order- and multiplicity-preserving sequence -- the safe
    default for a flag this module has no specific rule for (see the
    module docstring).
    """
    last_wins: dict[str, str] = {}
    presence_only: list[str] = []
    ordered_pairs: list[tuple[str, str]] = []
    ordered_unknown: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok in INCLUDE_SEARCH_FLAGS or tok in FORCED_INCLUDE_FLAGS:
            if i + 1 < n:
                ordered_pairs.append((tok, args[i + 1]))
                i += 2
                continue
            # A trailing flag with no operand is malformed input for this
            # flag family -- fall through and record it verbatim rather
            # than silently dropping it.
            ordered_unknown.append(tok)
            i += 1
            continue
        if tok.startswith(("-D", "-U")) and len(tok) > 2:
            macro_name = tok[2:].split("=", 1)[0]
            last_wins[f"macro:{macro_name}"] = tok
        elif tok.startswith(LAST_WINS_PREFIXES):
            prefix = next(p for p in LAST_WINS_PREFIXES if tok.startswith(p))
            last_wins[prefix] = tok
        elif tok in ORDER_INSENSITIVE_BOOLEAN_FLAGS:
            presence_only.append(tok)
        else:
            ordered_unknown.append(tok)
        i += 1
    return (
        last_wins,
        frozenset(presence_only),
        tuple(ordered_pairs),
        tuple(ordered_unknown),
    )
