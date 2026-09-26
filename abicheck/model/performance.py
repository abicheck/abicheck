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

"""The memory/speed trade-off a run is executed under.

One user-facing setting -- ``performance.profile`` in ``.abicheck.yml``,
``compare --performance-profile``, ``CompareRequest.performance_profile`` --
names *what the operator wants to optimize*, and :func:`tuning_for` derives
the concrete execution knobs from it. The knobs are internal: a new one is
added to :class:`ExecutionTuning` and given a value per profile, so a
project config never needs to learn each mechanism by name.

A profile changes how a run executes, never what it observes: every profile
must produce the same snapshots, findings, verdict and exit code.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "DEFAULT_PERFORMANCE_PROFILE",
    "ExecutionTuning",
    "PerformanceProfile",
    "current_performance_profile",
    "parse_performance_profile",
    "performance_profile_scope",
    "tuning_for",
]


class PerformanceProfile(str, Enum):
    """What a run optimizes for."""

    #: Today's behavior: both sides of a comparison may be resolved at once,
    #: in this process. Fastest; peak memory holds both sides together.
    BALANCED = "balanced"
    #: Lowest peak memory: one side at a time, each in its own short-lived
    #: process where the platform supports it, so the memory a side's
    #: extraction used is returned to the system before the next one starts.
    LOW_MEMORY = "low-memory"


DEFAULT_PERFORMANCE_PROFILE = PerformanceProfile.BALANCED


@dataclass(frozen=True, slots=True)
class ExecutionTuning:
    """The concrete execution choices a profile implies."""

    #: Resolve the two sides one after the other, never concurrently.
    sequential_sides: bool
    #: Resolve each side in a child process (where ``fork`` is available).
    isolate_sides: bool
    #: Compare a release's libraries one at a time instead of sizing a
    #: worker pool to the host (directory/package ``compare``).
    sequential_members: bool


_TUNING: dict[PerformanceProfile, ExecutionTuning] = {
    PerformanceProfile.BALANCED: ExecutionTuning(
        sequential_sides=False, isolate_sides=False, sequential_members=False
    ),
    PerformanceProfile.LOW_MEMORY: ExecutionTuning(
        sequential_sides=True, isolate_sides=True, sequential_members=True
    ),
}


def tuning_for(profile: PerformanceProfile) -> ExecutionTuning:
    return _TUNING[profile]


def parse_performance_profile(value: str | PerformanceProfile) -> PerformanceProfile:
    """*value* as a profile; ``ValueError`` naming the valid spellings."""
    if isinstance(value, PerformanceProfile):
        return value
    try:
        return PerformanceProfile(str(value).strip().lower())
    except ValueError:
        valid = ", ".join(p.value for p in PerformanceProfile)
        raise ValueError(
            f"unknown performance profile {value!r} (expected one of: {valid})"
        ) from None


_CURRENT: contextvars.ContextVar[PerformanceProfile | None] = contextvars.ContextVar(
    "abicheck_performance_profile", default=None
)


@contextmanager
def performance_profile_scope(
    profile: PerformanceProfile | None,
) -> Iterator[None]:
    """Make *profile* the ambient profile for the enclosed run.

    ``None`` leaves the enclosing value (or the default) in effect, so a
    caller with nothing to say can enter the scope unconditionally.
    """
    if profile is None:
        yield
        return
    token = _CURRENT.set(profile)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_performance_profile() -> PerformanceProfile:
    return _CURRENT.get() or DEFAULT_PERFORMANCE_PROFILE
