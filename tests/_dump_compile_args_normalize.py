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
(Codex review, four rounds -- each one found a real, different way a
convenient-but-wrong default silently accepted two non-equivalent argv as
"the same"; see below for what each round changed and why):

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

**Round 1** found the include-search case above. **Round 2** found the
``-D``/``-std`` last-wins case. **Round 3** found that the then-remaining
"everything else" bucket was still a permissive default (a catch-all set),
which silently mishandled ``--target=``/``-include`` the identical way
round 1 found for include-search flags -- so round 3 replaced the *default
direction* itself: an unrecognized token now defaults to strict,
order-preserving comparison, and only the small, explicit
`ORDER_INSENSITIVE_BOOLEAN_FLAGS` allowlist gets the permissive set
comparison.

**Round 4** found that "strict, order-preserving comparison" was still
split across *two independently-compared* buckets -- a
canonical-pair-form ``ordered_pairs`` sequence and a raw-token
``ordered_unknown`` sequence -- so an *attached*-spelling include flag
(``-I/a``, one argv token) fell into ``ordered_unknown`` while the
*split*-spelling form (``-I``, ``/a``, two argv tokens) fell into
``ordered_pairs``, and the two buckets being compared independently meant
their relative order to *each other* was invisible: ``("-I/a", "-I",
"/b")`` and ``("-I", "/b", "-I/a")`` normalized identically even though a
real compiler searches ``/a`` first in one and ``/b`` first in the other.
Fixed by (a) recognizing both spellings as the same canonical pair and (b)
collapsing every order-sensitive category into *one* unified
``ordered_stream``, rather than adding a fifth bucket for the next spelling
variant this module doesn't yet know about -- see the "one stream, not many
buckets" reasoning in `split_compile_args`'s own docstring.
"""

from __future__ import annotations

#: Flags that take an operand naming a search directory -- either as a
#: separate following argv token (``-I``, ``/a``) or attached
#: (``-I/a``). Order- and multiplicity-sensitive (first-match-wins
#: search); see the module docstring's "Round 1" and "Round 4" notes.
INCLUDE_SEARCH_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter")

#: Flags that take an operand naming a file to force-include, in either
#: spelling. Order-sensitive for a different reason than the search flags
#: above (an earlier one's macros are visible to a later one), but
#: normalized into the same canonical pair shape.
FORCED_INCLUDE_FLAGS = ("-include", "-imacros")

#: Every flag `split_compile_args` recognizes as taking a directory/file
#: operand, attached or separate -- checked in this combined order so a
#: longer, more specific prefix never loses to a shorter unrelated one
#: (no two entries here share a prefix in practice, but the check is
#: written to be safe regardless).
_PAIR_FLAGS = INCLUDE_SEARCH_FLAGS + FORCED_INCLUDE_FLAGS

#: Single-token flags with real "later occurrence wins" semantics, keyed by
#: their own literal prefix (there is only ever one effective value per
#: prefix, unlike -D/-U which are keyed per macro name below).
LAST_WINS_PREFIXES = ("-std=", "--target=")

#: The only flags whose repetition/order this module treats as genuinely
#: inert -- deliberately a narrow, explicitly-reviewed allowlist, not a
#: default (see the module docstring's "Round 3" note for why a catch-all
#: default was itself the bug).
ORDER_INSENSITIVE_BOOLEAN_FLAGS = frozenset(
    {"-fPIC", "-fpic", "-pthread", "-nostdinc", "-nostdinc++"}
)


def _match_pair_flag(
    tok: str, args: tuple[str, ...], i: int, n: int
) -> tuple[str, str, int] | None:
    """If *tok* (at position *i*) is one of `_PAIR_FLAGS`, return
    ``(flag, operand, tokens_consumed)`` -- handling both the attached
    spelling (``-I/a``, 1 token) and the separate-argument spelling
    (``-I``, ``/a``, 2 tokens). Returns ``None`` for anything else,
    *including* a recognized flag with no operand available (a malformed
    trailing flag) -- the caller then records the bare token itself rather
    than silently dropping it.
    """
    for flag in _PAIR_FLAGS:
        if tok == flag:
            return (flag, args[i + 1], 2) if i + 1 < n else None
        if tok.startswith(flag) and len(tok) > len(flag):
            return flag, tok[len(flag) :], 1
    return None


def split_compile_args(
    args: tuple[str, ...],
) -> tuple[dict[str, str], frozenset[str], tuple[str, ...]]:
    """Split *args* into ``(last_wins_state, presence_only_flags,
    ordered_stream)``.

    ``last_wins_state`` maps a normalized key -- ``"macro:<NAME>"`` for a
    ``-D``/``-U`` flag naming ``<NAME>``, or a flag's own literal prefix for
    one of `LAST_WINS_PREFIXES` -- to the *last* raw token seen for that
    key, walking *args* in order, mirroring a real compiler's last-wins
    semantics. ``presence_only_flags`` is every token in
    `ORDER_INSENSITIVE_BOOLEAN_FLAGS`, compared as a plain set.

    ``ordered_stream`` is **one** unified, order- and
    multiplicity-preserving sequence covering every token this module does
    not have positive evidence is safe to reorder or drop: a canonicalized
    ``"FLAG=OPERAND"`` entry for every include-search/forced-include pair
    (attached and separate spellings normalize to the identical string —
    see `_match_pair_flag`), and any unrecognized token, *interleaved in
    their original relative argv order*. This is deliberately **one**
    stream rather than one bucket per category (a canonical-pair bucket
    plus a separate unrecognized-token bucket, compared independently) --
    that split is exactly what let round 4 miss the relative order between
    an attached-form include flag and a plain unrecognized token. The
    general lesson this module's whole history states: every one of the
    four rounds found a *default* that was too permissive for some input,
    never a default that was too strict -- so the only default this
    function makes going forward is "compared exactly, in order, unless a
    flag is explicitly known to be safe otherwise."
    """
    last_wins: dict[str, str] = {}
    presence_only: list[str] = []
    ordered_stream: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        pair = _match_pair_flag(tok, args, i, n)
        if pair is not None:
            flag, operand, consumed = pair
            ordered_stream.append(f"{flag}={operand}")
            i += consumed
            continue
        if tok in ("-D", "-U") and i + 1 < n:
            # GCC/Clang accept -D/-U with the name as a *separate* argv
            # token too (`-D FOO=1`, not just `-DFOO=1`) -- canonicalize
            # to the attached spelling so both forms land in the same
            # last-wins key, for the identical reason `_match_pair_flag`
            # canonicalizes attached vs. separate include flags.
            combined = tok + args[i + 1]
            macro_name = combined[2:].split("=", 1)[0]
            last_wins[f"macro:{macro_name}"] = combined
            i += 2
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
            ordered_stream.append(tok)
        i += 1
    return last_wins, frozenset(presence_only), tuple(ordered_stream)
