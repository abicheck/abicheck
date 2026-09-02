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

"""Direct unit tests for ``storage/bundle_facts_validation.py``'s own
functions -- most of this module's behavior is already exercised
end-to-end through ``tests/test_bundle_facts_archive*.py`` (real archives
through the real ``read_bundle_facts_archive``/``load_bundle_facts``
entry points), but ``load_bundle_facts_blob_json``'s own ``RecursionError``
translation is not reachable that way: the shared pre-scan
(``check_bundle_facts_json_budget``) always catches excessive nesting
*before* ``json.loads()`` runs, for every real caller. This file calls the
function directly to exercise that one defensive branch.
"""

from __future__ import annotations

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.bundle_facts_validation import load_bundle_facts_blob_json


class TestLoadBundleFactsBlobJsonRecursionError:
    def test_a_real_recursion_error_from_json_loads_translates_cleanly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The pre-scan's own nesting-depth check always fires first through
        # every real caller (read_bundle_facts_archive), so this defensive
        # `except RecursionError` branch is otherwise unreachable -- no-op
        # the pre-scan here to prove json.loads()'s own RecursionError still
        # translates to this module's SnapshotError vocabulary rather than
        # leaking raw, matching the sibling ValueError/UnicodeDecodeError
        # branches immediately above it.
        import abicheck.storage.bundle_facts_validation as validation_module

        monkeypatch.setattr(
            validation_module, "check_bundle_facts_json_budget", lambda *a, **kw: None
        )
        deeply_nested = ("[" * 100_000) + ("]" * 100_000)

        with pytest.raises(SnapshotError, match="too deeply nested"):
            load_bundle_facts_blob_json(
                deeply_nested.encode("utf-8"),
                max_json_object_nodes=1_000_000,
                path="unused",
                description="test blob",
            )
