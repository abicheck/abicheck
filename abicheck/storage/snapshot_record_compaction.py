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

"""The encoder's per-record omissions: keys a decoder restores on its own
(an uncaptured slot fact, a default ELF version binding, unobserved ODR
conflicts, an unrecorded advanced-extraction status) are dropped from the
encoded document, so documents from producers that never record them stay
byte-identical to older schema versions.

Moved out of :mod:`abicheck.storage.snapshot_encode` (file-size cap) when
that module gained the per-section encode (storage plan A2.2). Every pass
here reads and rewrites only the keys it names, record by record, so it
gives the same result on one section's keys as on the whole document.
"""

from __future__ import annotations

from typing import Any

from ..model import FactStatus
from ..model.dwarf_facts import AdvancedExtractionStatus
from .dwarf_layout_facts import drop_uncaptured_dwarf_layout_facts

__all__ = ["compact_records"]


#: ``DwarfMetadata``'s ODR-conflict fields (schema v51). Written only when
#: the producer looked for conflicts (or found one), so a snapshot whose
#: debug info was never walked for them -- BTF/CTF/PDB, a symbols-only dump
#: -- encodes byte-identically to v50.
_DWARF_ODR_KEYS = ("struct_odr_conflicts", "enum_odr_conflicts")


def _compact_elf_symbols(d: dict[str, Any]) -> None:
    """Drop the per-symbol keys a decoder restores on its own.

    v56: ``ElfSymbol.code_hash`` is written only when computed, so a
    snapshot without hashes encodes exactly as v55. v58:
    ``version_binding`` is written only where ``version`` cannot say it --
    ``unknown`` is the decoder's default and ``named`` is implied by a
    non-empty ``version`` (``snapshot_platform_blocks.elf_symbol_binding``).
    """
    elf = d.get("elf")
    if not isinstance(elf, dict):
        return
    for sym in elf.get("symbols") or ():
        if not isinstance(sym, dict):
            continue
        if not sym.get("code_hash"):
            sym.pop("code_hash", None)
        binding = sym.get("version_binding")
        if binding == "unknown" or (binding == "named" and sym.get("version")):
            sym.pop("version_binding", None)


def _drop_unobserved_odr_conflicts(d: dict[str, Any]) -> None:
    dwarf = d.get("dwarf")
    if not isinstance(dwarf, dict):
        return
    for key in _DWARF_ODR_KEYS:
        if not dwarf.get(key):
            dwarf.pop(key, None)
    if not dwarf.get("odr_conflicts_observed"):
        dwarf.pop("odr_conflicts_observed", None)


#: Schema v54's per-slot resolved type identities, written only when a
#: producer captured them: ``None`` ("not captured") is the default for every
#: producer but castxml, so omitting it keeps a snapshot from any other
#: producer -- and every digest over one -- byte-identical to v53. The decoder
#: reads the missing key as ``NOT_COLLECTED`` (``fact_codec.
#: validated_identities``).
_TYPE_IDENTITY_KEY = "type_identities_fact"
_RETURN_TYPE_IDENTITY_KEY = "return_type_identities_fact"
_SPECIALIZATION_KEY = "specialization_declared_fact"
_TEMPLATE_PARAM_DEFAULTS_KEY = "template_param_defaults_fact"
_EXPLICIT_INSTANTIATION_KEY = "explicit_instantiation_fact"
#: Schema v69: a fundamental-typed slot's ABI fact, written only where a producer
#: determined it (model/abi_fundamental.py).
_TYPE_ABI_KEY = "type_abi_fact"
_RETURN_TYPE_ABI_KEY = "return_type_abi_fact"


def _encode_advanced_extraction_status(d: dict[str, Any]) -> None:
    """``dwarf_advanced.extraction`` (schema v68, ADR-080) is written only when
    the extraction recorded an outcome. ``unrecorded`` -- a hand-built object,
    or a BTF/CTF/PDB producer -- writes no key, so those documents encode
    exactly as v67 did and a missing key decodes back to ``unrecorded``.
    """
    dwarf_advanced = d.get("dwarf_advanced")
    if not isinstance(dwarf_advanced, dict):
        return
    if dwarf_advanced.get("extraction") in (None, AdvancedExtractionStatus.UNRECORDED):
        dwarf_advanced.pop("extraction", None)


def _drop_uncaptured(entry: dict[str, Any], key: str) -> None:
    """Omit an uncaptured slot fact: ``None``, or a bare ``NOT_COLLECTED``
    (no value, diagnostics or producer) -- which is exactly what the decoder
    reads an absent key back as, so omitting it keeps a load/save round trip
    byte-stable instead of materialising the decoder's answer on re-save."""
    if key not in entry:
        return
    fact = entry[key]
    if fact is None or (
        fact.get("status") in (FactStatus.NOT_COLLECTED, FactStatus.NOT_COLLECTED.value)
        and fact.get("value") is None
        and not fact.get("diagnostics")
        and fact.get("producer") is None
    ):
        del entry[key]


def _drop_uncaptured_type_identities(d: dict[str, Any]) -> None:
    for fn in d.get("functions", ()):
        _drop_uncaptured(fn, _RETURN_TYPE_IDENTITY_KEY)
        # Schema v64: written only when a producer recorded the defaults.
        _drop_uncaptured(fn, _TEMPLATE_PARAM_DEFAULTS_KEY)
        # Schema v65: written only when the clang text pass recorded the kind.
        _drop_uncaptured(fn, _EXPLICIT_INSTANTIATION_KEY)
        _drop_uncaptured(fn, _RETURN_TYPE_ABI_KEY)
        for param in fn.get("params", ()):
            _drop_uncaptured(param, _TYPE_IDENTITY_KEY)
            _drop_uncaptured(param, _TYPE_ABI_KEY)
    for var in d.get("variables", ()):
        _drop_uncaptured(var, _TYPE_IDENTITY_KEY)
        _drop_uncaptured(var, _EXPLICIT_INSTANTIATION_KEY)
        _drop_uncaptured(var, _TYPE_ABI_KEY)
    for rec in d.get("types", ()):
        for fld in rec.get("fields", ()):
            _drop_uncaptured(fld, _TYPE_IDENTITY_KEY)
            _drop_uncaptured(fld, _TYPE_ABI_KEY)
        # Schema v61: written only when captured, so a record from any other
        # producer (or no specialization) encodes exactly as v60.
        _drop_uncaptured(rec, _SPECIALIZATION_KEY)
        # Schema v62: written only when a producer recorded the defaults.
        _drop_uncaptured(rec, _TEMPLATE_PARAM_DEFAULTS_KEY)


def compact_records(d: dict[str, Any]) -> None:
    """Every omission above, in the encoder's order (in place)."""
    _drop_unobserved_odr_conflicts(d)
    _encode_advanced_extraction_status(d)
    _compact_elf_symbols(d)
    _drop_uncaptured_type_identities(d)
    drop_uncaptured_dwarf_layout_facts(d)
