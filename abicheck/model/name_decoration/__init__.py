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

"""Platform name-decoration codecs (design-hardening plan Phase 3, F3;
ADR-063 Phase 2B).

One module per decoration a linker or ABI adds on top of a source-level
name. Each exposes ``decode`` (decorated spelling -> structured record, or
``None`` when the spelling is not provably that decoration) and ``encode``
(the exact inverse), so ``encode(decode(s)) == s`` for every decodable
``s`` and two distinct spellings never decode to one record:

* :mod:`.macho` -- Darwin's leading ``_`` on every global symbol;
* :mod:`.pe_x86` -- PE ``__cdecl``/``__stdcall``/``__fastcall``/
  ``__vectorcall`` export decoration;
* :mod:`.itanium_structors` -- Itanium ctor/dtor variants
  ``C1``/``C2``/``C3``/``D0``/``D1``/``D2``;
* :mod:`.elf_version` -- GNU symbol-version suffixes ``@VER``/``@@VER``.

Extraction applies these once; matching and joining code consume the
decoded names (or the alias tables :mod:`..export_index` derives through
these codecs) and never strip a decoration themselves.
"""
