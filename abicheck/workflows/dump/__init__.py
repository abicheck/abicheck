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

"""The dump workflow (ADR-061 ``workflows``): one native-binary dump behind
a binary-format seam.

* :mod:`.formats` -- :class:`~.formats.BinaryFormatAdapter`, the protocol the
  ELF/PE/Mach-O adapters implement, and the registry
  ``service_dump_native._run_dump_uncached`` dispatches through.
* :mod:`.pe`, :mod:`.macho` -- PE and Mach-O primary extraction. ELF's
  extractor is still ``service_dump_native.extract_elf`` (see
  :class:`.formats.ElfAdapter`).

No names are re-exported here: import from the owning module.
"""
