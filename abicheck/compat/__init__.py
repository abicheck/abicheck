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

"""abicheck.compat — ABICC compatibility layer.

Submodules:
- descriptor: ABICC XML descriptor parsing (CompatDescriptor, parse_descriptor)
- xml_report: ABICC-format XML report generation
- abicc_dump_import: ABICC Perl dump importer
- cli: compat group CLI subcommands (``compat check``, ``compat dump``) and helpers
"""

from .descriptor import CompatDescriptor, parse_descriptor

__all__ = ["CompatDescriptor", "parse_descriptor"]
