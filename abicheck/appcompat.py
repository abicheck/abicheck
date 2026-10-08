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


"""Public Python API path for application compatibility checking (ADR-005).

``docs/use/python-api.md`` names ``abicheck.appcompat.scope_diff_to_app`` as
the consumer-scoping entry point, so this module re-exports, unchanged, the
consumer-scoping workflow (:mod:`abicheck.workflows.consumer_scope`,
:mod:`abicheck.workflows.consumer_scope_standalone`), its fact types
(:mod:`abicheck.model.consumer_requirements`) and the pure evaluation helper
:func:`uncovered_missing_symbols` (:mod:`abicheck.policy.consumer_requirements`).
It holds no logic.

Internal code never imports it: every ``abicheck`` module imports the owner.
"""

from __future__ import annotations

from .model.consumer_requirements import (
    AppRequirements as AppRequirements,
    ConsumerImportFacts as ConsumerImportFacts,
    LibraryExportFacts as LibraryExportFacts,
)
from .policy.consumer_requirements import (
    uncovered_missing_symbols as uncovered_missing_symbols,
)
from .workflows.consumer_scope import (
    AppCompatResult as AppCompatResult,
    PluginHostContractResult as PluginHostContractResult,
    check_against as check_against,
    parse_app_requirements as parse_app_requirements,
    scope_diff_to_app as scope_diff_to_app,
    scope_diff_to_required_symbols as scope_diff_to_required_symbols,
)
from .workflows.consumer_scope_standalone import (
    check_appcompat as check_appcompat,
    check_plugin_host_contract as check_plugin_host_contract,
)

__all__ = [
    "AppCompatResult",
    "AppRequirements",
    "ConsumerImportFacts",
    "LibraryExportFacts",
    "PluginHostContractResult",
    "check_against",
    "check_appcompat",
    "check_plugin_host_contract",
    "parse_app_requirements",
    "scope_diff_to_app",
    "scope_diff_to_required_symbols",
    "uncovered_missing_symbols",
]
