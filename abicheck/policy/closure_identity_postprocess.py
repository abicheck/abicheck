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

"""Post-process lambda-closure function-level findings, in the order the
checker needs them.

Two steps, both over the change list ``compare()`` has assembled:

1. :func:`~abicheck.compare.closure_respelling.drop_closure_respellings`
   removes a signature finding that only re-spells a closure's per-header
   ordinal on a symbol exported under the same real key on both sides.
2. :func:`~abicheck.diff_templates.demote_lambda_closure_unexported_findings`
   demotes a lambda-closure finding whose symbol neither binary exports.

Step 1 runs first so a dropped finding can neither drive a verdict nor a
SONAME advisory. The list is mutated in place, which is what the checker's
own ``kept``/``verdict_redundant`` lists require.
"""

from __future__ import annotations

from ..compare.closure_respelling import drop_closure_respellings
from ..diff_templates import demote_lambda_closure_unexported_findings
from ..model.change import Change
from ..model.snapshot import AbiSnapshot


def apply_closure_identity_postprocess(
    kept: list[Change],
    verdict_redundant: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> None:
    kept[:] = drop_closure_respellings(kept, old, new)
    verdict_redundant[:] = drop_closure_respellings(verdict_redundant, old, new)
    demote_lambda_closure_unexported_findings(kept + verdict_redundant, old, new)
