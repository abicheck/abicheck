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

"""Versioned JSON Schemas for abicheck machine-readable output.

The schemas in this package describe the stable JSON contract that
automated consumers (CI gates, dashboards, other tooling) can rely on.

Stability policy
----------------
The compare-report schema is versioned with a SemVer-style
``MAJOR.MINOR`` string exposed as :data:`REPORT_SCHEMA_VERSION` and emitted
in every JSON report as ``report_schema_version``:

- **Additive** changes — new optional keys, new enum members, relaxing a
  constraint — bump the **MINOR** component. Existing consumers keep working.
- **Breaking** changes — removing/renaming a key, tightening a type,
  removing an enum member — bump the **MAJOR** component.

Consumers should accept any report whose ``report_schema_version`` shares
their expected MAJOR component and ignore unknown keys.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

#: SemVer-style (MAJOR.MINOR) version of the compare-report JSON schema.
#: 1.1 — added the optional ``release_recommendation`` object (additive).
REPORT_SCHEMA_VERSION = "1.1"

_SCHEMA_DIR = Path(__file__).resolve().parent
COMPARE_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "compare_report.schema.json"


@cache
def load_compare_report_schema() -> dict[str, Any]:
    """Return the parsed compare-report JSON Schema as a dict."""
    with COMPARE_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "COMPARE_REPORT_SCHEMA_PATH",
    "load_compare_report_schema",
]
