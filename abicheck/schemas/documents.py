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

"""Where each published JSON Schema lives, and how to load it.

Lifted out of this package's ``__init__`` -- which owns the *version
constants* and their long per-bump changelog comments, and is a recorded
no-growth file -- so that adding a schema is adding a path and a loader here
rather than growing that registry. Every name stays importable from
``abicheck.schemas``: the package re-exports them, and callers were never
meant to know which module defines them.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

__all__ = [
    "AGGREGATE_REPORT_SCHEMA_PATH",
    "AUDIT_REPORT_SCHEMA_PATH",
    "COMPARE_REPORT_SCHEMA_PATH",
    "load_aggregate_report_schema",
    "load_audit_report_schema",
    "load_compare_report_schema",
]

_SCHEMA_DIR = Path(__file__).resolve().parent
COMPARE_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "compare_report.schema.json"
AGGREGATE_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "aggregate_report.schema.json"
#: ADR-068 D2's single-build audit (``compare --no-baseline``). A schema of
#: its own rather than a branch of the compare report's: an audit reports no
#: compatibility verdict, so its ``verdict: null`` means "no comparison was
#: performed" -- not compare_report's ADR-050 D2 "the comparability gate
#: rejected this pair", which is why that schema requires a ``reason``
#: beside a null verdict and this one must not.
AUDIT_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "audit_report.schema.json"


@cache
def load_compare_report_schema() -> dict[str, Any]:
    """Return the parsed compare-report JSON Schema as a dict."""
    with COMPARE_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@cache
def load_audit_report_schema() -> dict[str, Any]:
    """Return the parsed single-build-audit JSON Schema as a dict."""
    with AUDIT_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@cache
def load_aggregate_report_schema() -> dict[str, Any]:
    """Return the parsed aggregate-report JSON Schema as a dict."""
    with AGGREGATE_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data
