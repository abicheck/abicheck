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

"""Old-consumer/new-library runtime execution probe — ADR-044 P2 item 2.

**Disabled by design (P0.1, security hardening).** This module used to
execute a real consumer binary (``subprocess.run``) with an analyzed
OLD/NEW shared library staged onto ``LD_LIBRARY_PATH``, so that the dynamic
linker's own eager-binding failure could corroborate the static
``--used-by`` scanner. That is a hard violation of this project's central
trust boundary: an *analyzed* artifact — the very ``.so``/consumer binary
being compared — must be treated as data, never executed. Loading it lets
its ELF constructors and other load-time initializers run with this
process's privileges, environment, and credentials (CI secrets, tokens,
workspace contents included).

``run_runtime_probe`` is therefore now a **non-executing no-op**: it never
spawns a process and never stages/loads an analyzed library into this
process. It always returns ``attempted=False`` with an explicit
``skipped_reason``, preserving the public dataclass/function shape so the
``--verify-runtime`` CLI flag and :mod:`abicheck.cli_helpers_compare` keep
working unmodified, and existing callers/tests that only assert on
``attempted``/``skipped_reason`` degrade cleanly. ``--verify-runtime``
itself is retained only as an explicitly deprecated, non-executing
compatibility shim — real corroboration must come from the static
``--used-by`` scanner (:mod:`abicheck.appcompat`), which never executes
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The one, unconditional reason every call returns. Kept as a shared
#: constant so tests and callers can match it without restating the prose.
_DISABLED_REASON = (
    "the --verify-runtime execution probe has been disabled: it used to "
    "execute the analyzed OLD/NEW shared library (and the consumer "
    "binary) as a security hardening measure, abicheck never executes an "
    "analyzed artifact -- use the static --used-by scanner instead, which "
    "answers the same undefined-symbol question without running anything"
)

#: Default wall-clock budget kept for API compatibility with existing
#: callers that still pass ``timeout=`` explicitly — unused now that no
#: process is ever spawned.
DEFAULT_TIMEOUT = 10.0


@dataclass
class RuntimeProbeOutcome:
    """One side's (old or new library) execution attempt."""

    ok: bool
    missing_symbol: str | None = None
    stderr_tail: str = ""
    timed_out: bool = False


@dataclass
class RuntimeProbeResult:
    """Result of probing one consumer binary against the old and new library."""

    app_path: str
    attempted: bool
    skipped_reason: str | None = None
    old: RuntimeProbeOutcome | None = None
    new: RuntimeProbeOutcome | None = None

    @property
    def regressed_symbol(self) -> str | None:
        """The specific symbol whose resolution regressed old→new, if any.

        Only set when the app ran cleanly against the old library
        (``old.ok``) but the dynamic linker itself named a missing symbol
        against the new one — the one shape this probe treats as
        attributable to the library change, not an unrelated environment
        factor.
        """
        if self.old is not None and self.old.ok and self.new is not None:
            return self.new.missing_symbol
        return None


def run_runtime_probe(
    app_path: Path,
    old_lib: Path,
    new_lib: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> RuntimeProbeResult:
    """Disabled no-op — never executes *app_path*, *old_lib*, or *new_lib*.

    Kept as a real, importable function (rather than deleted outright) so
    :mod:`abicheck.cli_helpers_compare`'s existing ``--verify-runtime``
    wiring and any external caller keep working unmodified. Always returns
    ``attempted=False`` with :data:`_DISABLED_REASON` — it never inspects
    *app_path*/*old_lib*/*new_lib* beyond stringifying the former, never
    spawns a subprocess, and never stages or loads a library into this
    process. *timeout* is accepted and ignored.
    """
    return RuntimeProbeResult(
        app_path=str(app_path),
        attempted=False,
        skipped_reason=_DISABLED_REASON,
    )
