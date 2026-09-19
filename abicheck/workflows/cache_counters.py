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

"""Run-wide cache attribution for a release comparison (ADR-061 ``workflows``).

The two counter sets that decide where a multi-member release comparison's
time actually goes, and that a sampling profile cannot answer:

* **The spelling caches** -- hits, misses, bypasses *by reason*, evictions,
  compilations, and retained bytes **by owner**. A profile shows that
  matching is expensive; it does not show that matching is expensive
  *because results are being recomputed*. On a real oneDAL comparison those
  are different diagnoses with different fixes, and the counters separate
  them: 98.99% hits with 20 s of matching is a working cache, 63.6% hits
  with 411,232 bypasses and 59 s is a cache that is being refused.
* **The include-tree walk** -- calls against *distinct* directories. A
  profile shows the walk is hot; it does not distinguish one expensive walk
  from a thousand repeats of a cheap one, and those have opposite fixes
  (make the walk cheaper vs. stop repeating it). Measured directly, one
  walk of a 4,188-header tree costs ~36 ms plus ~43 ms of caller ``stat``
  work, which only reconciles with the profile's 3.67% share at a call
  count far above what one walk per cache key would produce.

This lives in ``workflows`` rather than beside the fan-out that calls it
because the caches it reads belong to ``compare`` and ``extract``, and a
``frontends``-classified ``cli_*`` module may not import either
(ADR-061 dependency direction; the architecture gate rejects it).
"""

from __future__ import annotations

from . import memory_trace

__all__ = ["record_shared_cache_counters"]


def record_shared_cache_counters() -> None:
    """Emit the run-wide cache counters once the member fan-out is done.

    Emitted after the *whole* fan-out rather than per member: these caches
    are process-wide and members share them, so a per-member reading would
    attribute one member's reuse of another's work to whichever ran second.

    Free when tracing is off, which is the default -- one boolean test, and
    the imports below never run. They are deliberately function-local: this
    is instrumentation, and enabling it must not change the module-import
    graph of a run that has it disabled.
    """
    if not memory_trace.memory_trace_enabled():
        return
    from ..compare.spelling_match_cache import cache_statistics
    from ..extract.cache_header_scan import header_scan_statistics

    memory_trace.counts("release.spelling_cache", **cache_statistics())
    memory_trace.counts("release.header_scan", **header_scan_statistics())
