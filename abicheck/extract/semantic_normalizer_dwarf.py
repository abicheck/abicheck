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

"""DWARF-specific ``cv_qualification`` derivation for
``extract.semantic_normalizer`` (ADR-063 Phase 6, fifth slice).

Split out of ``semantic_normalizer.py`` (its only caller) purely to keep
that file under the AI-readiness gate's 800-line production maximum for a
new file -- this module's own contents are otherwise that module's, not a
separate design decision, the identical reason ``semantic_normalizer_
artifacts.py`` was split out one slice earlier (that module's own
docstring).

**DWARF, the first non-header-AST producer.** ADR-063 Phase 2's
"fourteenth slice" already gave ``dwarf_snapshot.py`` a real, typed
``ScopePath`` (built at each ``DW_TAG_namespace``/record DIE, the identical
"widen the walker's own scope representation" discipline the two
header-AST backends already use) and a populated ``entity_id`` on every
``RecordType``/``EnumType``/``Function``/``Variable``/typedef it produces —
``normalize_header_ast``'s own "reads identity, never resolves it" contract
means DWARF needed no *new* identity work to become a caller, only a new
caller: ``dwarf_snapshot.build_snapshot_from_dwarf`` passes its builder's
``types``/``enums``/``typedefs`` (already namespace-qualified-keyed,
matching ``typedefs_qualified``'s own convention)/``typedef_entity_ids``/
``functions``/``variables`` straight through, with ``producer="dwarf"`` and
``constants={}``/``constant_entity_ids={}`` (DWARF carries no constexpr
initializer evidence at all — the same documented, permanent gap
``AbiSnapshot.constant_entity_ids`` already states for a DWARF-only
snapshot).

Records/enums/typedefs need no DWARF-specific handling: their
``canonical_spelling`` is already the resolved qualified name, identical in
shape to the header-AST case. Functions and variables each need one
producer-specific carve-out, both driven by what DWARF's own DIE walk does
and does not extract structurally, verified against real compiled fixtures
rather than assumed:

- **A function's ``cv_qualification`` is ``Fact.not_collected()``, never
  ``Fact.present(...)``.** ``dwarf_snapshot._build_function`` never reads a
  method's own const/volatile qualifier at all (documented in the
  fourteenth slice's own writeup as "inert in practice" for identity, since
  only the mangled name matters there) — so ``Function.is_const``/
  ``is_volatile`` are structurally always their dataclass default
  (``False``) for every DWARF-sourced function, real const method included,
  never a confirmed reading. Reusing the castxml/clang branch's
  unconditional ``Fact.present(canonical_cv_qualification(...))`` here would
  silently claim "confirmed not const/volatile" for a function this
  producer never even looked at — exactly the "reported unreliably rather
  than left unset" mistake ``semantic_normalizer.py``'s own ``restrict`` and
  typedef-behind-an-alias sections already refuse to make elsewhere.
- **A variable's ``cv_qualification`` is read from the already-extracted
  structural ``Variable.is_const`` field, NOT from
  ``semantic_normalizer._variable_top_level_cv_qualification``'s text
  scan.** This deliberately inverts that function's own stated reason for
  existing — its docstring explains it does not read ``Variable.is_const``
  because *both header-AST backends* compute that field with a bare
  whole-string word search that conflates a mutable pointer to const data
  with a genuinely const pointer. DWARF's own ``is_const`` is not computed
  that way at all: ``dwarf_snapshot._process_variable`` sets it from
  whether the variable's OWN outermost type DIE is ``DW_TAG_const_type`` —
  the correct structural question, answered directly from DWARF's
  type-chain nesting rather than guessed from text. Verified against a real
  compiled fixture covering all four cases: ``const int g`` and
  ``int* const g`` both read ``is_const=True``; ``const int* g``
  (pointee-const, pointer itself mutable) reads ``is_const=False``;
  ``const int* const g`` reads ``is_const=True`` — the exact discrimination
  this IR's ``cv_qualification`` needs, and the text scan cannot make on
  DWARF's own output regardless: ``int* const g`` and ``const int* g``
  render as the IDENTICAL text (``"const int *"``) by
  ``dwarf_snapshot._compute_type_name``'s own const/pointer composition
  order, so no text-based scan could ever tell them apart here even in
  principle. DWARF extracts no structural volatile-qualifier fact for a
  variable at all (no ``Variable.is_volatile`` field exists for any
  backend), so a DWARF variable's ``cv_qualification`` can only ever contain
  ``"const"``, never ``"volatile"`` — a documented, accepted gap, not a
  claimed absence: a genuinely volatile DWARF variable reports the same
  ``()`` a non-volatile one would if it is also non-const, the identical
  "left unset rather than reported unreliably" choice already made for
  ``restrict``.

Leaf module: imports only ``model.fact``/``model.semantic_ir`` -- per
ADR-061 D10's leaf-module contract for a module split out purely for size,
this deliberately does NOT import back from ``semantic_normalizer.py``
(that would recreate the cycle the split exists to avoid); the caller
passes down only the plain values (``Function.is_const``, a bare ``bool``)
each function below actually needs.
"""

from __future__ import annotations

from ..model.fact import Fact
from ..model.semantic_ir import canonical_cv_qualification

#: Producer name for this slice's DWARF caller (``dwarf_snapshot.
#: build_snapshot_from_dwarf``, via ``dumper_elf_fallback._dwarf_semantic_ir``).
DWARF_PRODUCER = "dwarf"


def function_cv_qualification() -> Fact[tuple[str, ...]]:
    """A DWARF-sourced function's ``cv_qualification`` -- see this module's
    own docstring for why it is unconditionally ``Fact.not_collected()``,
    never a confirmed ``Fact.present(())``."""
    return Fact.not_collected()


def variable_cv_qualification(is_const: bool) -> Fact[tuple[str, ...]]:
    """A DWARF-sourced variable's ``cv_qualification``, built from its
    already-extracted, structurally-sound ``Variable.is_const`` -- see this
    module's own docstring for why this differs from the castxml/clang text
    scan (and why it can only ever report ``const``, never ``volatile``).

    Always ``Fact.partial(...)``, never ``Fact.present(...)`` -- including
    when *is_const* is ``False`` (Codex review, fresh evidence): DWARF's own
    DIE walk never extracts a volatile-qualifier fact for a variable at all
    (no backend has an ``is_volatile`` field on ``Variable``), so even a
    confirmed-non-const result is only ever confirmed for the "const" half
    of this tuple's vocabulary -- volatile stays genuinely uncollected
    regardless of what *is_const* says. ``Fact.present(())`` would
    misrepresent that gap as "confirmed: neither qualifier applies", the
    identical "PRESENT denotes a complete, confirmed value" mistake this
    module's own function cv_qualification carve-out already avoids for a
    different reason.
    """
    return Fact.partial(
        canonical_cv_qualification(("const",) if is_const else ()),
        "DWARF does not extract a volatile-qualifier fact for a variable",
    )
