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

"""The policy-disposition ledger operations a frontend legitimately performs.

Same rule and same shape as ``workflows/policy_file.py`` and
``workflows/suppression.py`` (ADR-061 Phase 4 item 2): ``policy`` is not in
``frontends.may_import``, so a CLI module that has to close the run's
disposition ledger reaches it through the workflow layer rather than
importing ``policy/disposition_ledger.py`` directly.

Exactly one frontend needs this, for exactly one reason: the scoped-gate
orchestrators (``cli_helpers_compare._apply_used_by_scoping`` and
``_apply_required_symbol_scoping``) are the only code that knows the *union*
of relevant findings across every ``--used-by`` consumer, and
``close_consumer_scope`` has to be called once with that union rather than
once per consumer (``apply_scope`` only demotes, so per-consumer calls would
intersect the consumers' sets). See that function's own docstring.

Re-export only, deliberately: ``policy/disposition_ledger.py`` remains the
one module to read and to change.
"""

from __future__ import annotations

from ..policy.disposition_close import (
    close_consumer_scope as close_consumer_scope,
    ledger_for as ledger_for,
)

__all__ = ["close_consumer_scope", "ledger_for"]
