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

"""Synthetic ctor/dtor snapshot-key vocabulary shared across ``extract`` and
``compare``.

``castxml`` sometimes omits a constructor/destructor's real mangled name
(see ``extract/headers/castxml/functions.py``'s ``_function_mangled_name``
fallback); the parser then synthesizes a stable, non-mangled snapshot key
instead. Both the producer (the castxml parser, ``extract``) and several
consumers that must recognize the shape (symbol-diff public-surface
narrowing, namespace-move batch detection, template/finding-identity
matching -- all ``compare``) need the exact same prefix/predicate pair, so
it lives here in ``model`` -- the one layer both may import (ADR-061 D1) --
rather than in a castxml-specific module, the same way
``model/mangled_name.py`` holds the Itanium/MSVC scope-component decoders
both layers also share. Re-exported from
``abicheck.extract.headers.castxml.names``/``abicheck.dumper_castxml`` so
every existing import path keeps working.
"""

from __future__ import annotations

#: Marker for a snapshot key synthesized for a constructor overload whose
#: real mangled name castxml omitted. A class may have several overloaded
#: constructors, so the prefix alone is not unique -- the caller appends the
#: qualified scope and parameter signature after it (see
#: ``extract/headers/castxml/functions.py``'s ``_function_display_name``).
#: It is intentionally not a real ABI symbol, only a stable per-overload
#: identity -- ``diff_symbols._public_functions()`` reads this to exempt such
#: entries from its ELF-export-set narrowing, which they could never pass (the
#: key has no real exported symbol to match).
SYNTHETIC_CTOR_KEY_PREFIX = "__abicheck_ctor__"


def is_synthetic_ctor_key(key: str) -> bool:
    """Whether *key* is a castxml constructor-overload synthetic identity."""
    return key.startswith(SYNTHETIC_CTOR_KEY_PREFIX)


#: Marker for a snapshot key synthesized for a destructor whose real mangled
#: name castxml omitted (see ``_CastxmlParser._function_display_name`` and
#: ``_function_mangled_name``'s ``return name`` fallback). A class has at
#: most one destructor, so — unlike constructors — no per-overload prefix is
#: needed: the synthesized "~ClassName" display name is itself already a
#: stable, unique identity. It is intentionally not a real ABI symbol (a real
#: Itanium destructor mangling always starts with ``_Z``, never ``~``), only
#: a stable key — ``diff_symbols._public_functions()`` reads this the same
#: way it already does :data:`SYNTHETIC_CTOR_KEY_PREFIX`/
#: :func:`is_synthetic_ctor_key`, to exempt such entries from its
#: ELF-export-set narrowing, which they could never pass. Without this, a
#: real virtual destructor's PUBLIC visibility (``_ctor_or_dtor_visibility``)
#: was necessary but not sufficient: it would still be silently dropped
#: before reaching the diff whenever ELF metadata is present (Codex review,
#: PR #582 — found after the destructor-visibility fix, via the same Phase 2
#: parity gate).
_SYNTHETIC_DTOR_KEY_PREFIX = "~"


def is_synthetic_dtor_key(key: str) -> bool:
    """Whether *key* is a castxml destructor synthetic identity."""
    return key.startswith(_SYNTHETIC_DTOR_KEY_PREFIX)
