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

"""``BuildConfig``'s (``.abicheck.yml``) strict-schema subkey-type tables.

Split out of ``inline.py`` (which sits at the AI-readiness 2000-line hard
cap) purely to keep it under that cap — this module has no other reason to
exist independently and is imported only by ``inline.py``'s
``BuildConfig._scalar_findings``/``_subkey_findings`` (ADR-043 CLI reset: no
separate ``config validate`` command — every real ingestion path enforces
this).

Block subkeys ``BuildConfig.from_dict()`` parses with
``_opt_bool``/``_opt_str``/``_str``/``_strs`` — a value of the wrong type
there (e.g. the YAML string "false" for a boolean, or a bare number for a
string/list field) must be rejected outright rather than silently
dropped/coerced. Keep these tables in sync with ``BuildConfig.from_dict``'s
helper calls when a new subkey is added — nothing enforces that
automatically.
"""

from __future__ import annotations

BOOL_SUBKEYS: dict[str, frozenset[str]] = {
    "scope": frozenset({"public", "collapse_versioned_symbols", "show_redundant"}),
    "suppression": frozenset({"strict", "require_justification"}),
    "compile": frozenset({"nostdinc"}),
    "debug": frozenset({"dwarf_only", "debuginfod"}),
}
STR_SUBKEYS: dict[str, frozenset[str]] = {
    "build": frozenset({"system", "query", "compile_db"}),
    "sources": frozenset({"graph"}),
    "severity": frozenset(
        {"preset", "abi_breaking", "potential_breaking", "quality_issues", "addition"}
    ),
    "source": frozenset({"method"}),
    "compile": frozenset({"frontend", "std", "sysroot"}),
    "debug": frozenset({"format", "debuginfod_url"}),
}
# `_strs()` accepts either a list of strings or a single bare string (folded
# to a 1-element list), so both shapes are valid here — anything else isn't.
LIST_SUBKEYS: dict[str, frozenset[str]] = {
    "build": frozenset({"targets"}),  # P0.2: root target(s) scoping L3 collection
    "sources": frozenset({"public_headers", "exclude"}),
    "scope": frozenset({"public_symbols"}),
    "compile": frozenset({"include_dirs", "defines"}),
}
# Recognized top-level keys that are scalars, not blocks (i.e. absent from
# BuildConfig._KNOWN_BLOCK_KEYS) — the same wrong-type gap as the block
# subkeys above, one level up. Empty since CLI cleanup phase two PR G2
# removed the only entry (`exit_code_scheme`) -- kept as a named, typed
# frozenset rather than deleted outright so a future scalar top-level key
# has an obvious home, and so every existing `TOP_LEVEL_STR_KEYS` reader
# keeps working unchanged against an empty set.
TOP_LEVEL_STR_KEYS: frozenset[str] = frozenset()
TOP_LEVEL_INT_KEYS: frozenset[str] = frozenset({"version"})
