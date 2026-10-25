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

**ADR-061 gap B: retained, not reclassified** -- ``current()`` reaches
`report`/`workflows`, which also import this package; same escape hatch
as ``checker_policy.py``.
The schemas in this package describe the stable JSON contract that
automated consumers (CI gates, dashboards, other tooling) can rely on.

Stability policy: each artifact is versioned with a SemVer-style
``MAJOR.MINOR`` string (e.g. :data:`REPORT_SCHEMA_VERSION`, emitted as
``report_schema_version``). **Additive** changes (new optional keys, new
enum members, relaxing a constraint) bump **MINOR** -- existing consumers
keep working. **Breaking** changes (removing/renaming a key, tightening a
type, removing an enum member) bump **MAJOR**. Consumers should accept any
report sharing their expected MAJOR component and ignore unknown keys.
"""

from __future__ import annotations

from .documents import (  # noqa: F401 -- re-exported: `abicheck.schemas` is the import site every caller uses
    AGGREGATE_REPORT_SCHEMA_PATH as AGGREGATE_REPORT_SCHEMA_PATH,
    AUDIT_REPORT_SCHEMA_PATH as AUDIT_REPORT_SCHEMA_PATH,
    AUDIT_SET_REPORT_SCHEMA_PATH as AUDIT_SET_REPORT_SCHEMA_PATH,
    COMPARE_REPORT_SCHEMA_PATH as COMPARE_REPORT_SCHEMA_PATH,
    load_aggregate_report_schema as load_aggregate_report_schema,
    load_audit_report_schema as load_audit_report_schema,
    load_audit_set_report_schema as load_audit_set_report_schema,
    load_compare_report_schema as load_compare_report_schema,
)
from .release_schema import RELEASE_SCHEMA_VERSION

#: Artifact names :func:`current` accepts, each mapped to the module-level
#: constant that already owns its version (ADR-055 D3). Read-only lookup
#: facade -- adding an entry here never changes that constant's own value or
#: bump policy. Built once at import time; it is :func:`current`'s
#: per-artifact lookups that use function-local imports (not this set),
#: since some of those modules transitively import this package themselves
#: and a module-level import here would turn that into a real import cycle.


#: SemVer-style (MAJOR.MINOR) version of the compare-report JSON schema.
#: The per-version history, with the migration note for each bump, lives in
#: docs/contribute/report-schema-history.md; add the new entry there in the same PR.
REPORT_SCHEMA_VERSION = "6.7"

from .artifact_versions import current_artifact_version  # noqa: E402

# The directory/package release envelope's own version and version history
# live in `release_schema.py` (see that module's docstring for why); the
# re-export at the top of this file keeps `schemas.RELEASE_SCHEMA_VERSION`
# and `schemas.current("release")` unchanged.


def current(name: str) -> str | int:
    """The current version abicheck emits for persisted artifact *name*.

    See :func:`abicheck.schemas.artifact_versions.current_artifact_version`.
    """
    return current_artifact_version(
        name,
        report_version=REPORT_SCHEMA_VERSION,
        release_version=RELEASE_SCHEMA_VERSION,
    )


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "RELEASE_SCHEMA_VERSION",
    "COMPARE_REPORT_SCHEMA_PATH",
    "AGGREGATE_REPORT_SCHEMA_PATH",
    "load_compare_report_schema",
    "load_aggregate_report_schema",
    "current",
]
